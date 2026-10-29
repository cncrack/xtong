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
from urllib.parse import urlparse

from . import listparse, selectors

_LOGIN_PATHS = {urlparse(p).path.lower() for p in selectors.LOGIN_PAGES}

# ---------------------------------------------------------------- 判定阈值

REBUILD_BACKSTEP_SECONDS = 5      # t 比上一次回退超过 5 秒 → 视为媒体重建
STALL_MIN_ELAPSED = 30            # 至少观察 30 秒才判“卡顿”，避免抖动误判
STALL_LAG_SECONDS = 10            # 播放位置落后实时超过 10 秒 → 判卡顿（实测卡顿时 30 秒只走 15 秒）
RELOAD_COOLDOWN_SECONDS = 180     # 两次重载之间至少隔 3 分钟，防重载风暴
MAX_RECOVERIES_PER_SECTION = 6    # 单节重载上限，超了转人工
MAX_PAUSE_RECOVERIES = 3          # 同一节反复被判 paused 时最多拉回几次（C2：不能无限 play）
NO_REPORT_AFTER_SECONDS = 75      # 上报是 60 秒一拍；超过 75 秒没新增上报 = 网络/上报断了
OVERRUN_MARGIN_MINUTES = 2        # 达标不能“刚刚好”：学时 ≥ 要求 + 本余量 才停（C11）
MAX_REPLAYS_PER_SECTION = 1         # 视频播完还差学时 → 重播补足，最多补播几次（Q26）
ENDED_SETTLE_SECONDS = 240        # `ended` 后兜底等待期：fresh 回读仍差时再等一会儿（1.8 实测通常是 0s）
REPLAY_WINDOW_SECONDS = 300       # 补播只盯 5 分钟：够了立刻收工，不再白播整段

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


def should_replay(row: dict[str, Any] | None, replays: int,
                  max_replays: int = MAX_REPLAYS_PER_SECTION) -> bool:
    """视频播完、**fresh 回读且等过等待期**后学时仍差 → 是否值得重播补足（Q26）。

    2026-10-29 1.7 实测：ended 时列表 19/20（用的是 1 分钟前的回读快照），旧代码直接判“学时不涨”
    并停了整批；事后回读已是 20/20 `done`。1.8 复验把因果钉死：ended 瞬间 **fresh 回读**就到 20/20
    （`settled=0`）⇒ 1.7 那次是**快照过期**，不是丢分钟。

    等过 `ENDED_SETTLE_SECONDS` 仍差才是真差：重播补上（重播是增量记账，Q12：1.1 实测 46→54，不清零）。
    缺数据（None）一律不补：判不了就不动，交给人工看。
    """
    if replays >= max_replays:
        return False
    done_n = (row or {}).get("done_minutes")
    need_n = (row or {}).get("required_minutes")
    if not isinstance(done_n, int) or not isinstance(need_n, int):
        return False
    return done_n < need_n


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


def at_login_page(url: str) -> bool:
    """当前 URL 是不是登录页 ⇒ **会话已失效的硬证据**（Q20）。

    2026-10-29 12:49 实测：守播中途整页被踢到 `Loginb.aspx`，播放帧与列表帧一起消失，
    所有采样变 null，`classify` 只能给 `unknown`，于是守播**静默转圈到总超时**。
    按路径精确比对（不用子串：`HidLogin.aspx` 包含 `login.aspx`，会误伤）。
    """
    path = urlparse(url or "").path.lower()
    return bool(path) and path in _LOGIN_PATHS


def shorten_url(url: str, limit: int = 80) -> str:
    """日志用短 URL：去掉 `rnd` 参数，避免每次回读都是一串随机数。"""
    clean = re.sub(r"([?&])rnd=[^&]*", "", url or "").replace("?&", "?")
    return clean[:limit]


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


def report_summary(reports: list[dict[str, Any]]) -> dict[str, Any]:
    """上报状态码汇总（Q20 取证用）。

    会话失效时最容易分辨的就是这里：上报返回 **200** = 服务端照收（问题在页面侧）；
    返回 **302** = 被重定向到登录页（会话已死）；**500** = 服务端报错。
    只统计状态码，不碰 URL 参数与响应体。
    """
    counts: dict[str, int] = {}
    for r in reports or []:
        key = str(r.get("status"))
        counts[key] = counts.get(key, 0) + 1
    tail = [r.get("status") for r in (reports or [])[-5:]]
    return {"count": len(reports or []), "by_status": counts, "last": tail}


def meets_required(row: dict[str, Any]) -> bool:
    """列表学时是否已达「学时要求」（**不含余量**）。

    单独拎出来是因为 **视频播完（`ended`）时要求与余量可能无法同时满足**：
    2026-10-29 1.3 实测——视频时长 47 分 53 秒，播完时列表 46（要求 45，余量线 47）。
    此时已达标且去无可去（没法再超跑），必须按达标收工，而不是转人工。
    """
    done_n = (row or {}).get("done_minutes")
    need_n = (row or {}).get("required_minutes")
    if not isinstance(done_n, int) or not isinstance(need_n, int):
        return False
    return done_n >= need_n


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

    def _parse_settled(self, *, min_rows: int = 1, tries: int = 10) -> dict[str, Any]:
        """等行数稳定再解析当前 DOM（不自己导航）。

        ⚠ 实测读早了只出 6 行且分页为 None（Q9），静默少读几节比报错更危险。
        """
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
        frame = self.list_frame()
        return listparse.parse_course_page(frame.content()) if frame else {"rows": last, "pager": {}}

    def read_page(self, *, min_rows: int = 1, tries: int = 10) -> dict[str, Any]:
        """读**第 1 页** → `{rows, pager}`（GET 重载 + cache-bust）。

        ⚠ GET 重载会把手动翻到的页号**丢掉**（实测 2026-10-29 17:52：goto_page(2) 后
        `read_page()` 回到 `1/4`）——翻页是 `__doPostBack` 回发，页号只在 viewstate 里。
        所以第 2 页及之后的刷新一律用 `refresh_here()`，不要用本方法。
        """
        frame = self.list_frame()
        if frame is None:
            return {"rows": [], "pager": {}}
        try:
            frame.goto(self.cache_busted(frame.url), wait_until="commit", timeout=self.page_timeout_ms)
        except Exception:                                  # noqa: BLE001  网络抖动不该直接失败
            pass
        return self._parse_settled(min_rows=min_rows, tries=tries)

    def prev_page(self) -> bool:
        """点「上一页」（同样是 ASP.NET 回发）。"""
        frame = self.list_frame()
        if frame is None:
            return False
        try:
            btn = frame.query_selector(selectors.PAGER_PREV)
            if btn is None:
                return False
            btn.click(timeout=self.page_timeout_ms)
        except Exception:                                  # noqa: BLE001
            return False
        self.page.wait_for_timeout(self.settle_seconds * 1000)
        return True

    def refresh_here(self, *, min_rows: int = 1, tries: int = 10) -> dict[str, Any]:
        """刷新**当前页**并解析（第 2 页及之后靠回发绕缓存，GET 会跳回第 1 页）。

        第 1 页直接用 GET cache-bust（便宜且不变页号）；其余页用「下一页→上一页」
        或「上一页→下一页」来回一次取得新响应（Q8 已用同法绕过服务端缓存）。
        """
        pager = self.current_pager()
        cur = pager.get("current")
        if not isinstance(cur, int) or cur <= 1:
            return self.read_page(min_rows=min_rows, tries=tries)
        if pager.get("has_next"):
            self.next_page()
            self.prev_page()
        elif pager.get("has_prev"):
            self.prev_page()
            self.next_page()
        return self._parse_settled(min_rows=min_rows, tries=tries)

    def read_row(self, section_id: str | int) -> dict[str, Any] | None:
        """回读某一节的列表行——**这是唯一的达标判据来源**。"""
        for row in self.refresh_here()["rows"]:
            if str(row.get("platform_chapter_id")) == str(section_id):
                return row
        return None

    # ---------------------------------------------------------- 课程（多课程）

    def list_courses(self) -> list[dict[str, Any]]:
        """读课程下拉：`[{value, text, selected}]`（一页就能拿全，不用翻页）。

        【实测 2026-10-29】当前账号只有 1 项（`32 / 军事理论`）。列表页一旦有第二门课，
        这里会自动多出一项——**调用方不必知道具体课程**。
        """
        frame = self.list_frame()
        if frame is None:
            return []
        try:
            return frame.evaluate(
                """(sel) => {
                  const s = document.querySelector(sel);
                  if (!s) return [];
                  return Array.from(s.options).map(o => ({
                    value: o.value, text: (o.text || '').trim(), selected: o.selected }));
                }""", f"#{selectors.COURSE_SELECT_ID}")
        except Exception:                                  # noqa: BLE001
            return []

    def select_course(self, value: str | int) -> bool:
        """切到指定课程并**回读校验**；校验不过返回 False。

        ⚠ 切换方式**未证实**：当前账号 `ddlClass` 只有一门课、`onchange` 为空，无法确认站点
        用哪种方式切换（`__doPostBack('ddlClass','')`？换 URL？）。所以这里只做“最低假设”：
        选中 + 派发 change + 若页面有 `__doPostBack` 就回发一次，然后回读 `selected` 校验。
        失败由调用方报“该课程切不过去”，**不重试、不猜**（“不能硬闯”的同一原则）。
        """
        frame = self.list_frame()
        if frame is None:
            return False
        try:
            ok = frame.evaluate(
                """([sel, val]) => {
                  const s = document.querySelector(sel);
                  if (!s) return false;
                  const id = sel.replace(/^#/, '');
                  const hit = Array.from(s.options).some(o => o.value === String(val));
                  if (!hit) return false;
                  s.value = String(val);
                  s.dispatchEvent(new Event('change', { bubbles: true }));
                  try { if (typeof __doPostBack === 'function') __doPostBack(id, ''); } catch (e) {}
                  return true;
                }""", [f"#{selectors.COURSE_SELECT_ID}", str(value)])
        except Exception:                                  # noqa: BLE001
            return False
        if not ok:
            return False
        self.page.wait_for_timeout(self.settle_seconds * 1000)
        for c in self.list_courses():
            if str(c.get("value")) == str(value):
                return bool(c.get("selected"))
        return False

    # ---------------------------------------------------------- 翻页

    def current_pager(self) -> dict[str, Any]:
        """从当前 DOM 读分页状态（**不重载页面**）：`{current, total, has_next, has_prev}`。"""
        frame = self.list_frame()
        if frame is None:
            return {}
        try:
            return listparse.parse_pager(frame.content())
        except Exception:                                  # noqa: BLE001
            return {}

    def next_page(self) -> bool:
        """点「下一页」。分页是 ASP.NET 回发（`__doPostBack`），**不能用 URL 参数翻页**。"""
        frame = self.list_frame()
        if frame is None:
            return False
        try:
            btn = frame.query_selector(selectors.PAGER_NEXT)
            if btn is None:
                return False
            btn.click(timeout=self.page_timeout_ms)
        except Exception:                                  # noqa: BLE001
            return False
        self.page.wait_for_timeout(self.settle_seconds * 1000)
        return True

    def goto_page(self, target: int, *, max_steps: int = 12) -> bool:
        """翻到第 `target` 页（1 基）。已在目标页直接返回 True；失败返回 False。"""
        target = max(1, int(target))
        for _ in range(max_steps):
            pager = self.current_pager()
            cur = pager.get("current")
            if not isinstance(cur, int):
                return False
            if cur == target:
                return True
            if cur < target and not pager.get("has_next"):
                return False
            if not self.next_page():
                return False
            if self.current_pager().get("current") == cur:   # 点了没动：不硬转
                return False
        return False

    def find_section(self, section_ids: list[str] | None = None,
                     *, max_pages: int = 12) -> dict[str, Any] | None:
        """逐页找目标节，**找到后停在该页**（后续 `read_row` 靠当前页）。

        `section_ids` 为空 → 找第一个未完成（`state != done`）的节；给了就找 id 命中的。
        2026-10-29 实测：列表 `1/4` 共 **36 节**，而调度器原只在当前页挑节 ⇒ 第 1 页 9 节
        跑完后会误报“没有未完成的节”而收工。
        """
        if not self.goto_page(1):
            return None
        page_no = 1
        while page_no <= max_pages:
            # 第 1 页用 GET（顺带拿新数据）；翻过的页只能解析当前 DOM，再 GET 就回第 1 页了
            parsed = self.read_page() if page_no == 1 else self._parse_settled()
            for row in parsed["rows"]:
                if section_ids:
                    if str(row.get("platform_chapter_id")) in section_ids:
                        return row
                elif section_status(row) != "done":
                    return row
            if not (parsed.get("pager") or {}).get("has_next"):
                return None
            if not self.next_page():
                return None
            page_no += 1
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
              max_replays: int = MAX_REPLAYS_PER_SECTION,
              on_event: Any = None) -> dict[str, Any]:
        """守一节：保活 + 重建恢复 + 定期回读达标。

        `margin_minutes`：**超跑余量**（2026-10-29 用户要求）。不要停在“刚刚好”：最后那一分钟的
        `updateTime` 碰上 503/断网就不会入账，列表会卡在 `要求-1`，变成“视频播完了却还显示学习中”。
        所以判定线是 `已完成学时 ≥ 要求 + 余量`，而不是 `≥ 要求`。

        `max_replays`：视频播完但学时**连要求都没到**时，重播补足（Q26）。重播是增量记账（Q12），
        不会清零已得学时；补播阶段只盯 `REPLAY_WINDOW_SECONDS`，一旦够要求立即收工。

        返回 `{result, seconds, reloads, rebuilds, last_row}`，
        `result` ∈ `done|pending|timeout|escalate`（由调用方决定要不要切下一节）。
        """
        started = time.monotonic()
        watch_until = started + max_seconds
        replays = 0
        ended_at: float | None = None
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

        # Q20 取证：只记上报的**状态码**，不记 URL 参数、不记响应体（它们含 SessionId/StudentId）。
        # 2026-10-29 实测：正常上报返回 200；若会话失效能直接看到 302/500，一眼分辨“谁的问题”。
        reports: list[dict[str, Any]] = []

        def on_response(resp: Any) -> None:
            try:
                if "updateTime" not in (resp.url or ""):
                    return
                reports.append({"at": round(time.monotonic() - started), "status": resp.status})
                del reports[:-200]
                if resp.status not in (200, 204, 304):
                    emit("report_bad", status=resp.status, total=len(reports))
            except Exception:                              # noqa: BLE001
                pass

        try:
            self.page.on("response", on_response)
        except Exception:                                  # noqa: BLE001
            pass

        while time.monotonic() < watch_until:
            now = time.monotonic()

            # 会话失效必须先认出来（Q20）：掉登录时整页被踢到 Login.aspx/Loginb.aspx，
            # 播放帧和列表帧一起消失 → 采样全 null → 只能得到 `unknown`，守播会**静默**转圈
            # 到总超时（2026-10-29 12:49 实测）。掉登录不是“页面慢”，必须立即报出去。
            if at_login_page(self.page.url):
                emit("session_lost", url=shorten_url(self.page.url), reports=report_summary(reports))
                return {"result": "session_lost", "seconds": now - started,
                        "reloads": self._reloads, "rebuilds": rebuilds, "last_row": last_row,
                        "reports": report_summary(reports)}

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
                # 视频播完是**正常边界**，不是异常：`ended` 后站点 `stopLoop()`（实测 m_time 冻在 2816）。
                # 只要列表已达“要求”（即便够不着余量线），就按达标收工——没东西可再播了。
                if state == "ended":
                    # 1.7 的“19/20”其实是**快照过期**：ended 时用的是 1 分钟前的回读值。
                    # 1.8 复验：ended 瞬间 fresh 回读就到 20/20 done（`settled=0`）⇒ 必须立即回读。
                    # 若仍差，进 `ENDED_SETTLE_SECONDS` 等待期（果真有落库延迟也兜得住），再考虑补播。
                    if ended_at is None:
                        ended_at = now
                        emit("ended_short", row=last_row, settle=ENDED_SETTLE_SECONDS)
                    if now >= next_read:
                        fresh = self.read_row(section_id)
                        next_read = now + 60.0
                        # 读不到行（列表帧消失等）时不拿旧值下结论，继续等下一拍
                        if fresh is not None:
                            last_row = fresh
                            emit("progress", status=section_status(last_row), row=last_row, fresh=True)
                            if meets_required(last_row):
                                emit("ended_reached", row=last_row, settled=round(now - ended_at))
                                return {"result": "done", "seconds": now - started,
                                        "reloads": self._reloads, "rebuilds": rebuilds,
                                        "last_row": last_row, "reason": "ended_at_required",
                                        "replays": replays}
                    if now - ended_at < ENDED_SETTLE_SECONDS:
                        continue
                    # 等过了仍不够（1.7 的 19/20 真差）：重播补上（Q12 证明是增量记账），
                    # 在补播窗口内一旦达标立即收工。
                    if should_replay(last_row, replays, max_replays):
                        replays += 1
                        emit("replay_short", row=last_row, replay=replays, limit=max_replays)
                        if self.reload_player():
                            self.play()
                            prev, prev_at = None, now          # t 归零，不清基线会被判 rebuild
                            last_updates, last_update_at = None, now
                            ended_at = None
                            next_read = now + 10.0
                            watch_until = min(watch_until, now + REPLAY_WINDOW_SECONDS)
                            continue
                        emit("replay_failed", replay=replays)
                return {"result": "escalate", "seconds": now - started,
                        "reloads": self._reloads, "rebuilds": rebuilds, "last_row": last_row,
                        "replays": replays}

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
                        emit("no_report", stale=round(now - last_update_at), updates=ups,
                             reports=report_summary(reports))
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
                # 补播阶段放宽到“要求”：重播只为补回丢掉的那一分钟，不该再追余量（1.7 就 20 分时长）。
                if enough or (status == "done" and margin_minutes <= 0) \
                        or (replays > 0 and meets_required(last_row or {})):
                    if status != "done" and enough:
                        emit("margin_reached", done=done_n, need=need_n, margin=margin_minutes)
                    if replays > 0 and not enough:
                        emit("replay_filled", row=last_row, replays=replays)
                    return {"result": "done", "seconds": now - started,
                            "reloads": self._reloads, "rebuilds": rebuilds, "last_row": last_row,
                            "replays": replays}
                # 收尾阶段加密回读：距目标不足 10 分钟时改每 60 秒读一次，
                # 否则 5 分钟一回读会把超跑余量拖成“多跑 5~7 分钟”（36 节就是白跑 4 小时）。
                read_every = read_progress_every
                if isinstance(done_n, int) and isinstance(need_n, int) and done_n >= need_n - 10:
                    read_every = min(read_progress_every, 60.0)
                next_read = now + read_every

            self.page.wait_for_timeout(interval * 1000)

        return {"result": "timeout", "seconds": time.monotonic() - started,
                "reloads": self._reloads, "rebuilds": rebuilds, "last_row": last_row}
