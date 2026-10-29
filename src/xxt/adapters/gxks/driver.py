"""高校考试网播放驱动（T3-b）：进入节 / 保活 / 重建恢复 / 回读达标 / 切下一节。

分层（PLAN-GXKS §11.2、§11.14）：

- **上半部分是纯判定函数**：吃 dict / 数字，返回动作与理由，可离线单测；
- **下半部分 `GxksDriver` 是薄壳**：只做 Playwright 调用，一个方法只做一件事，
  所有平台常量来自 `selectors.py`。

三条实测约束（改这里之前先看 `docs/gxks/`）：

1. 页面计时是**墙钟**（`m_time` 每秒 +1，不读 `currentTime`）⇒ 页面秒表**不能**当进度；
2. 完成判定只认**列表回读**：状态文案「已完成」为准，分钟数只作辅助（实测出现过 42/42 仍「学习中」）；
3. **媒体重建后服务端会停止记账**（Q13：重建后 5 分钟上报照发、列表 0 增长）⇒
   恢复手段是**重载播放页开新会话**，而不是继续挂着等它自己好。
"""

from __future__ import annotations

import math
import re
import time
from typing import Any

from . import listparse, selectors

# ---------------------------------------------------------------- 判定阈值

REBUILD_BACKSTEP_SECONDS = 5      # t 比上一次回退超过 5 秒 → 视为媒体重建
STALL_MIN_ELAPSED = 30            # 至少观察 30 秒才判“卡顿”，避免抖动误判
STALL_LAG_SECONDS = 10            # 播放位置落后实时超过 10 秒 → 判卡顿（实测卡顿时 30 秒只走 15 秒）
RELOAD_COOLDOWN_SECONDS = 180     # 两次重载之间至少隔 3 分钟，防重载风暴
MAX_RECOVERIES_PER_SECTION = 6    # 单节重载上限，超了转人工
MAX_PAUSE_RECOVERIES = 3          # 同一节反复被判 paused 时最多拉回几次（C2：不能无限 play）
NO_REPORT_AFTER_SECONDS = 75      # 上报是 60 秒一拍；超过 75 秒没新增上报 = 网络/上报断了
OVERRUN_MARGIN_MINUTES = 2        # 达标不能“刚刚好”：学时 ≥ 要求 + 本余量 才停（C11）

# 浏览器侧采样脚本：只读 DOM，不做任何操作
_PROBE_JS = """() => {
  const v = document.querySelector('video');
  const t = (v && isFinite(v.currentTime)) ? v.currentTime : null;
  const d = (v && isFinite(v.duration)) ? v.duration : null;
  return {
    t: t === null ? null : Math.round(t),
    d: d === null ? null : Math.round(d),
    paused: v ? v.paused : null,
    ended: v ? v.ended : null,
    hidden: document.hidden,
    updates: document.querySelectorAll('script[src*="updateTime.ashx"]').length,
    clock: (typeof m_time !== 'undefined') ? m_time : null,
  };
}"""


# ---------------------------------------------------------------- 纯判定（可单测）


def classify(sample: dict[str, Any] | None, prev: dict[str, Any] | None = None,
             elapsed: float | None = None) -> str:
    """给一次播放器采样定性 → `rebuild|hidden|ended|paused|stalled|unknown|ok`。

    顺序有讲究：**重建优先**——重建时的采样常常同时是 `paused=True`、`d=None`，
    若先判 paused 就会去 play，而真正该做的是重载（Q13）。
    """
    if not sample or (sample.get("t") is None and sample.get("d") is None):
        return "unknown"

    d = sample.get("d")
    if d is None or (isinstance(d, float) and math.isnan(d)):
        # 从来没有过有效时长 = 还没就绪（则不能把它当重建，否则会无休止重载）；
        # 先有时长后又没了 = 真的被重建了。
        return "rebuild" if (prev and prev.get("d") is not None) else "loading"

    t = sample.get("t")
    if prev and prev.get("t") is not None and t is not None:
        if t < prev["t"] - REBUILD_BACKSTEP_SECONDS:
            return "rebuild"

    if sample.get("hidden"):
        return "hidden"
    if sample.get("ended"):
        return "ended"
    if sample.get("paused"):
        return "paused"

    if prev and prev.get("t") is not None and t is not None and elapsed is not None:
        if elapsed >= STALL_MIN_ELAPSED:
            advanced = t - prev["t"]
            if advanced < 0:
                return "ok"            # 小幅回退（> -5 秒）不当重建，也不当卡顿，算抖动
            if elapsed - advanced > STALL_LAG_SECONDS:
                return "stalled"
    return "ok"


def plan_action(state: str, *, reloads: int = 0,
                seconds_since_reload: float | None = None) -> str:
    """状态 → 动作：`none|play|foreground|reload|wait|escalate`。

    `ended` 给 `escalate`：站点在 `ended` 只 `stopLoop()` 停表、**不会跳下一节**，
    也不该由驱动层擅自重播（会多起一条计时链）。
    """
    if state == "ok":
        return "none"
    if state == "loading":
        return "wait"
    if state == "paused":
        return "play"
    if state == "hidden":
        return "foreground"
    if state == "stalled":
        return "wait"
    if state == "ended":
        return "escalate"
    if state == "rebuild":
        if reloads >= MAX_RECOVERIES_PER_SECTION:
            return "escalate"
        if seconds_since_reload is not None and seconds_since_reload < RELOAD_COOLDOWN_SECONDS:
            return "wait"
        return "reload"
    return "wait"                      # unknown：不猜，等下一次采样


def quota_gap(row: dict[str, Any]) -> int | None:
    """还差多少分钟达标；缺数据返回 None（**不把缺数据当达标**）。"""
    done, required = row.get("done_minutes"), row.get("required_minutes")
    if done is None or required is None:
        return None
    return max(0, required - done)


def section_status(row: dict[str, Any]) -> str:
    """一节的综合状态 → `done|pending|in_progress|not_started|unknown`。

    `pending` = 学时已够但服务端还没置「已完成」（有滞时，实测出现过 42/42 仍「学习中」）。
    """
    state = row.get("state") or "unknown"
    if state == "done":
        return "done"
    gap = quota_gap(row)
    if gap == 0:
        return "pending"
    if state == "learning":
        return "in_progress"
    if state == "not_started":
        return "not_started"
    return "unknown"


def enough_with_margin(row: dict[str, Any],
                       margin_minutes: int = OVERRUN_MARGIN_MINUTES) -> bool:
    """达标线：`已完成学时 ≥ 学时要求 + 余量`（C11：**不停在“刚刚好”**）。

    为什么要余量：最后一分钟的 `updateTime` 碰上 503/断网就不会入账（R1 只对单次抖动自愈），
    停在刚好等于要求时，服务端少记一分钟就卡在 `要求-1`，表现为“视频播完了却还是学习中”。
    `margin_minutes=0` 时退化为“达到要求即可”。
    """
    done_n = (row or {}).get("done_minutes")
    need_n = (row or {}).get("required_minutes")
    if not isinstance(done_n, int) or not isinstance(need_n, int):
        return False
    return done_n >= need_n + margin_minutes


def pick_next_section(rows: list[dict[str, Any]], current_id: str | None = None) -> dict[str, Any] | None:
    """按页面顺序挑下一个要学的节：跳过已完成的，从当前节之后开始找。"""
    ordered = sorted(rows, key=lambda r: r.get("idx", 0))
    if current_id is not None:
        idx_of_current = next(
            (i for i, r in enumerate(ordered) if str(r.get("platform_chapter_id")) == str(current_id)),
            None,
        )
        if idx_of_current is not None:
            ordered = ordered[idx_of_current + 1:]
    for row in ordered:
        if row.get("platform_chapter_id") is None:
            continue                   # 取不到 ID 的行不能点开，跳过而不是用名称猜
        if section_status(row) != "done":
            return row
    return None


# ---------------------------------------------------------------- Playwright 薄壳


class GxksDriver:
    """把“页面操作”收在一个类里，方便换成假对象做集成测试。

    约定：**本类只做 IO，不做判定**；判定一律调用上面的纯函数。
    """

    def __init__(self, page: Any, *, settle_seconds: float = 3.0, page_timeout_ms: int = 60000) -> None:
        self.page = page
        self.settle_seconds = settle_seconds
        self.page_timeout_ms = page_timeout_ms
        self._reloads = 0
        self._last_reload: float | None = None

    # ---------------------------------------------------------- 帧定位

    def frames(self) -> list[Any]:
        return list(self.page.frames)

    def list_frame(self) -> Any | None:
        return next((f for f in self.frames() if selectors.LIST_PATH in (f.url or "")), None)

    def player_frame(self) -> Any | None:
        """播放页帧只能按 URL 找：`showframe()` 会把 iframe 名字改写（实测变 `t`）。"""
        return next((f for f in self.frames() if selectors.STUDY_FRAME_URL_HINT in (f.url or "")), None)

    # ---------------------------------------------------------- 列表读取

    @staticmethod
    def cache_busted(url: str) -> str:
        """给列表 URL 换个时间戳 `rnd`：实测固定 `rnd=100` 会读到旧快照。"""
        stamp = str(int(time.time() * 1000))
        if re.search(r"([?&]rnd=)\d+", url):
            return re.sub(r"([?&]rnd=)\d+", lambda m: m.group(1) + stamp, url)
        return url + ("&" if "?" in url else "?") + f"rnd={stamp}"

    def read_page(self, *, min_rows: int = 1, tries: int = 10) -> dict[str, Any]:
        """读当前页 → `{rows, pager}`。

        ⚠ 必须等行数稳定再解析：实测读早了只出 6 行且分页为 None（Q9），
        静默少读几节比报错更危险。
        """
        frame = self.list_frame()
        if frame is None:
            return {"rows": [], "pager": {}}
        try:
            frame.goto(self.cache_busted(frame.url), wait_until="commit", timeout=self.page_timeout_ms)
        except Exception:                                  # noqa: BLE001  网络抖动不该直接失败
            pass
        last: list[dict[str, Any]] = []
        budget = int(self.settle_seconds * 1000 * 3 / max(1, tries))
        for _ in range(tries):
            self.page.wait_for_timeout(budget)
            frame = self.list_frame()
            if frame is None:
                continue
            parsed = listparse.parse_course_page(frame.content())
            rows = parsed["rows"]
            if len(rows) >= min_rows and len(rows) == len(last):
                return parsed
            last = rows
        return listparse.parse_course_page(frame.content()) if frame else {"rows": last, "pager": {}}

    def read_row(self, section_id: str | int) -> dict[str, Any] | None:
        """回读某一节的列表行——**这是唯一的达标判据来源**。"""
        for row in self.read_page()["rows"]:
            if str(row.get("platform_chapter_id")) == str(section_id):
                return row
        return None

    # ---------------------------------------------------------- 进入节

    def open_section(self, section_id: str | int) -> bool:
        """在列表里点「开始学习」（等价于 `showframe('名', ID)`），并等播放帧就绪。"""
        frame = self.list_frame()
        if frame is None:
            return False
        clicked = frame.evaluate(
            """(id) => {
              for (const a of document.querySelectorAll('a[onclick^="showframe"]')) {
                const oc = a.getAttribute('onclick') || '';
                if (oc.replace(/\\s/g, '').includes(',' + id + ')')) { a.click(); return true; }
              }
              return false;
            }""",
            str(section_id),
        )
        if not clicked:
            return False
        for _ in range(20):
            self.page.wait_for_timeout(1000)
            if self.sample_player().get("d") is not None:
                return True
        return self.player_frame() is not None

    # ---------------------------------------------------------- 播放器

    def sample_player(self) -> dict[str, Any]:
        frame = self.player_frame()
        if frame is None:
            return {}
        try:
            return frame.evaluate(_PROBE_JS)
        except Exception:                                  # noqa: BLE001  帧正在重建时会抛
            return {}

    def play(self) -> bool:
        frame = self.player_frame()
        if frame is None:
            return False
        return bool(frame.evaluate(
            """() => {
              if (typeof player !== 'undefined' && player && player.play) { player.play(); return true; }
              const v = document.querySelector('video');
              if (v) { v.play(); return true; }
              return false;
            }"""
        ))

    def mute(self) -> bool:
        """静音。**每次重载都会生成新的 video 元素，静音必须重设**，所以巡检循环每轮都调。"""
        frame = self.player_frame()
        if frame is None:
            return False
        try:
            return bool(frame.evaluate(
                "() => { const v = document.querySelector('video');"
                " if (!v) { return false; } v.muted = true; return true; }"
            ))
        except Exception:                                  # noqa: BLE001  帧重载中会抛
            return False

    def pause(self) -> bool:
        """停表：**抵达达标后必须调**，否则学时继续累加（C9：学时按累计播放时长计）。"""
        frame = self.player_frame()
        if frame is None:
            return False
        try:
            return bool(frame.evaluate(
                "() => { let ok = false;"
                " if (typeof player !== 'undefined' && player && player.pause) { player.pause(); ok = true; }"
                " const v = document.querySelector('video'); if (v) { v.pause(); ok = true; }"
                " return ok; }"
            ))
        except Exception:                                  # noqa: BLE001
            return False

    def leave_player(self) -> bool:
        """离开播放页：唯一能**彻底**停表的手段。

        2026-10-29 12:32 实测：守播在达标后调了 `pause()`（`paused=True` 已确认），
        但约 4 分钟后**播放自行恢复**，`t` 从 2166 回到 0 重播、`d` 由 2614 变 2615
        （媒体元素被重建过），学时 43 → **48**。⇒ “调过 pause”不等于“停了”。
        只有把播放页导航走（`m_time` 所在文档被卸载）才能保证计时链不复活。
        """
        frame = self.player_frame()
        if frame is None:
            return False
        try:
            frame.goto("about:blank", wait_until="commit", timeout=20000)
            return True
        except Exception:                                  # noqa: BLE001
            return False

    def ensure_foreground(self) -> None:
        """`document.hidden` 时把窗口拉回前台：本站 hidden 会自动暂停 + 停表。"""
        try:
            self.page.bring_to_front()
        except Exception:                                  # noqa: BLE001
            pass

    def reload_player(self) -> bool:
        """重载播放页 = 开新会话。**这是重建后恢复记账的手段**（Q13）。"""
        frame = self.player_frame()
        if frame is None:
            return False
        url = frame.url
        try:
            frame.goto(url, wait_until="commit", timeout=self.page_timeout_ms)
        except Exception:                                  # noqa: BLE001
            return False
        self._reloads += 1
        self._last_reload = time.monotonic()
        self.page.wait_for_timeout(5000)
        return self.player_frame() is not None

    # ---------------------------------------------------------- 守播

    def watch(self, section_id: str | int, *, interval: float = 30.0,
              max_seconds: float = 6 * 3600, read_progress_every: float = 600.0,
              margin_minutes: int = OVERRUN_MARGIN_MINUTES,
              on_event: Any = None) -> dict[str, Any]:
        """守一节：保活 + 重建恢复 + 定期回读达标。

        `margin_minutes`：**超跑余量**（2026-10-29 用户要求）。不要停在“刚刚好”：最后那一分钟的
        `updateTime` 碰上 503/断网就不会入账，列表会卡在 `要求-1`，变成“视频播完了却还显示学习中”。
        所以判定线是 `已完成学时 ≥ 要求 + 余量`，而不是 `≥ 要求`。

        返回 `{result, seconds, reloads, rebuilds, last_row}`，
        `result` ∈ `done|pending|timeout|escalate`（由调用方决定要不要切下一节）。
        """
        started = time.monotonic()
        prev: dict[str, Any] | None = None
        prev_at = started
        rebuilds = 0
        pause_recoveries = 0
        last_updates: int | None = None
        last_update_at = started
        next_read = started
        last_row: dict[str, Any] | None = None

        def emit(kind: str, **extra: Any) -> None:
            if on_event:
                on_event({"kind": kind, "at": round(time.monotonic() - started), **extra})

        while time.monotonic() - started < max_seconds:
            now = time.monotonic()
            sample = self.sample_player()
            state = classify(sample, prev, now - prev_at)
            since_reload = None if self._last_reload is None else now - self._last_reload
            action = plan_action(state, reloads=self._reloads, seconds_since_reload=since_reload)
            emit(state, action=action, t=sample.get("t"), d=sample.get("d"))

            if state == "rebuild":
                rebuilds += 1
            if action == "play":
                # C2/C5：拉回一次可以，反复拉回就是异常，转人工而不是硬刷 play
                if pause_recoveries >= MAX_PAUSE_RECOVERIES:
                    emit("give_up", reason="paused_too_often", recoveries=pause_recoveries)
                    return {"result": "escalate", "seconds": now - started,
                            "reloads": self._reloads, "rebuilds": rebuilds, "last_row": last_row}
                pause_recoveries += 1
                self.play()
            elif action == "foreground":
                self.ensure_foreground()
            elif action == "reload":
                self.reload_player()
            elif action == "escalate":
                return {"result": "escalate", "seconds": now - started,
                        "reloads": self._reloads, "rebuilds": rebuilds, "last_row": last_row}

            if state == "ok":
                pause_recoveries = 0        # 真正在走就把计数清零，避免历史上偶发暂停把配额吃掉
            self.mute()                     # 重载后 video 是新元素，静音要每次重设

            if sample:
                prev, prev_at = sample, now

                # 上报节奏（Q16）：计时不看网络，但**记账看网络**——断网时 `m_time` 照走、
                # 那一分钟的 `updateTime` 却丢了。页面每上报一次就多一个带该 src 的 script，
                # 所以这个计数不涨就是在白学，必须告警而不是静默继续。
                ups = sample.get("updates")
                if isinstance(ups, int):
                    if last_updates is None or ups != last_updates:
                        last_updates, last_update_at = ups, now
                    elif now - last_update_at >= NO_REPORT_AFTER_SECONDS:
                        emit("no_report", stale=round(now - last_update_at), updates=ups)
                        last_update_at = now          # 每 75 秒最多报一次，不刷屏

            if now >= next_read:
                last_row = self.read_row(section_id)
                status = section_status(last_row or {})
                emit("progress", status=status, row=last_row)
                # 达标线带余量：平台侧“已完成”与“学时列”会差一分钟，卡在刚好等于要求时
                # 一旦最后一条上报丢了就永远差 1 分钟（C11）。
                done_n = (last_row or {}).get("done_minutes")
                need_n = (last_row or {}).get("required_minutes")
                enough = enough_with_margin(last_row or {}, margin_minutes)
                if enough or (status == "done" and margin_minutes <= 0):
                    if status != "done" and enough:
                        emit("margin_reached", done=done_n, need=need_n, margin=margin_minutes)
                    return {"result": "done", "seconds": now - started,
                            "reloads": self._reloads, "rebuilds": rebuilds, "last_row": last_row}
                # 收尾阶段加密回读：距目标不足 10 分钟时改每 60 秒读一次，
                # 否则 5 分钟一回读会把超跑余量拖成“多跑 5~7 分钟”（36 节就是白跑 4 小时）。
                read_every = read_progress_every
                if isinstance(done_n, int) and isinstance(need_n, int) and done_n >= need_n - 10:
                    read_every = min(read_progress_every, 60.0)
                next_read = now + read_every

            self.page.wait_for_timeout(interval * 1000)

        return {"result": "timeout", "seconds": time.monotonic() - started,
                "reloads": self._reloads, "rebuilds": rebuilds, "last_row": last_row}
