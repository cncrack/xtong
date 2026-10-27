"""async 版学习运行器（v0.13 决策：M4 一步到位 asyncio + async Playwright）。

移植纪律（devlog 17）：同步版 study.py 保留至本模块通过同一套验收（整课实跑 + 测试全绿）后删除。
策略纯逻辑（额度/节奏/标题同步）复用 `core/*` 与 `pagejs.py`——**只允许纯数据**；
本模块绝不 import `study.py` / `pagesync.py`（同步 API 函数会静默返回协程，
2026-10-25 账号#2 事故即由此产生，见 devlog 26；守护测试 tests/test_module_boundaries.py）。
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from typing import Any

from ...core.accounts import effective_pace, effective_quota
from ...core.pace import sample_gap, sample_speed, within_daily_window
from ...core.playback import is_plausible_completion
from ...core.quota import remaining_minutes, sample_daily_budget, sum_done_seconds
from ...log import get_logger
from .pagejs import WATCHER_JS, build_watcher_js, match_chapter_id  # noqa: F401

log = get_logger("chaoxing.astudy")


async def _find_frame_with(page, selector: str, timeout_s: int = 30):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        for fr in page.frames:
            try:
                if await fr.locator(selector).count() > 0:
                    return fr
            except Exception:
                continue
        await page.wait_for_timeout(800)
    return None


async def _wait_task_page(page, timeout_s: float = 15.0) -> bool:
    """点开知识点后确认“真的停在任务页”：出现 <video> 或底部「下一节」工具栏。

    2026-10-27：#1 冷启动点「4.8 学会积极思维（下）」后，25 秒内任何 frame 都没
    <video>、10 秒内也找不到 `#prevNextFocusNext`——页面根本没跳过去，旧逻辑却把它
    当作“非视频任务点 + 课程到头”直接收工（视频 0 个）。同节 #2 能正常播 463 秒。
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        for fr in page.frames:
            try:
                if await fr.locator("video").count() or \
                        await fr.locator("#prevNextFocusNext").count():
                    return True
            except Exception:
                continue
        await page.wait_for_timeout(700)
    return False


# 非视频节点名关键词：这些节本来就没有 video，不能拿“有没有 video-done”判完成。
# 2026-10-27：既用于回补历史误标数据，也用于会话收尾时决定该不该记 visited。
_NON_VIDEO_MARKS = ("作业", "测验", "讨论", "阅读", "考试", "练习", "公告")


def is_non_video_name(name: str | None) -> bool:
    return bool(name) and any(m in name for m in _NON_VIDEO_MARKS)


def mark_entry_visited(conn, course_id: int, chapter_pid: str,
                       only_pending: bool = False) -> None:
    """标记章节“路过”（visited）。**已 `done` 的一律不降级**（2026-10-27 修）。

    旧代码无条件写 visited：10-25 22:47 #1 已完整播完「增强自我信心（上）」并记了
    video-done，会话结束时仍被改回 visited → 卡片进度少算一节（26/70 而非 27/70）。
    `only_pending=True`：只在章节仍为 pending 时才标（兜底换节时用，不碰已学完的）。
    """
    sql = ("UPDATE chapters SET status='visited' WHERE course_id=?"
           " AND platform_chapter_id=? AND status<>'done'")
    if only_pending:
        sql += " AND status='pending'"
    conn.execute(sql, (course_id, chapter_pid))
    conn.commit()


def _learn_int(settings, key: str, default: int, lo: int, hi: int) -> int:
    """读 `[learn]` 里的整数并钳到安全区间。

    2026-10-27 用户要求：把“尝试几次就放弃”从代码里的魔法数字改成配置项
    （否则调参要改代码、重打包）。写错类型/越界一律回落到默认值。
    """
    try:
        val = int((getattr(settings, "learn", None) or {}).get(key, default))
    except (TypeError, ValueError):
        val = default
    return max(lo, min(hi, val))


def pick_pending_after(conn, course_id: int, after_pid: str | None,
                       tried: list[str] | set[str]) -> tuple[str, str] | None:
    """按 idx 顺序挑下一个待学节：先取 after_pid 之后的，到尾部再从头绕回。

    B2（2026-10-27）：原来直接 `ORDER BY idx LIMIT 1` 取全表第一个 pending ——
    当前节还没播完（仍是 pending）时，取回的永远是它自己，于是 #1 在 5.1 上反复撞；
    失败后又被标 visited，就永久跳过。现在按“当前节之后”取，本会话试过的跳过。
    """
    rows_p = conn.execute(
        "SELECT platform_chapter_id, name, idx FROM chapters"
        " WHERE course_id=? AND status='pending' ORDER BY idx",
        (course_id,)).fetchall()
    cur_idx = None
    if after_pid:
        r0 = conn.execute(
            "SELECT idx FROM chapters WHERE course_id=? AND platform_chapter_id=?",
            (course_id, after_pid)).fetchone()
        cur_idx = r0["idx"] if r0 else None
    after = [r for r in rows_p if cur_idx is not None and r["idx"] > cur_idx]
    before = [r for r in rows_p if cur_idx is None or r["idx"] <= cur_idx]
    for r in after + before:
        if r["platform_chapter_id"] in tried:
            continue
        return r["platform_chapter_id"], r["name"]
    return None


async def _frame_alive(frame) -> bool:
    """帧句柄是否还能用。

    2026-10-27：任务点/章节切换后 iframe 会被页面重建，旧句柄随即 detached
    （#1 实测 5.1/5.2/5.3 连续 3 次 "Frame was detached"）。缓存 frame 前先探活。
    """
    if frame is None:
        return False
    try:
        await frame.evaluate("1")
        return True
    except Exception:
        return False


async def _open_chapter_frame(page, course_url: str):
    """双版本兼容的章节树定位（与同步版 open_chapter_frame 逻辑一致）。"""
    # 昵称截获必须用**异步**处理器：同步版 username_sniffer 里的 resp.json() 在
    # async Playwright 下返回协程，事件回调又是同步的 → 永远不 await，
    # 只留下 "coroutine 'Response.json' was never awaited" 的 RuntimeWarning，
    # 昵称永远抓不到（与 2026-10-25 会话崩溃同源的 sync/async 混用）。
    holder: dict[str, str] = {}

    async def _on_response(response) -> None:
        if "stu-base-info" in response.url and "name" not in holder:
            try:
                info = (await response.json()).get("userInfo") or {}
                holder["name"] = info.get("username") or ""
            except Exception:
                pass

    page.on("response", _on_response)
    page._xxt_name_holder = holder
    await page.goto(course_url, wait_until="domcontentloaded", timeout=60_000)
    await page.wait_for_timeout(4_000)

    tree_frame = await _find_frame_with(page, ".chapter_item[id^=cur]", timeout_s=8)
    if tree_frame is not None:
        return tree_frame

    for fr in page.frames:
        url = fr.url or ""
        if "/stu?" not in url and not url.rstrip("/").endswith("/stu"):
            continue
        try:
            loc = fr.locator("text=章节").first
            if await loc.count():
                await loc.click(timeout=10_000)
                break
        except Exception:
            continue

    tree_frame = await _find_frame_with(page, ".chapter_item[id^=cur]", timeout_s=25)
    if tree_frame is None:
        raise RuntimeError("未找到课程主页面（可能课程入口改版）")
    return tree_frame


async def _current_task_title(page) -> str:
    fr = await _find_frame_with(page, ".prev_title", timeout_s=3)
    if fr is None:
        return ""
    try:
        return (await fr.locator(".prev_title").first.inner_text(timeout=3_000)).strip()
    except Exception:
        return ""


class AsyncStudyRunner:
    """async 单账号学习会话（与同步版行为对齐；多账号经 scheduler 并发驱动）。"""

    def __init__(self, settings, state_path: Path, conn,
                 pace: dict | None = None, quota: dict | None = None,
                 proxy: str | None = None, rng: random.Random | None = None,
                 account_id: int = 1) -> None:
        self.settings = settings
        self.state_path = state_path
        self.conn = conn
        self.account_id = account_id
        self.pace = pace or settings.pace
        self.quota = quota or settings.quota
        self.proxy = proxy
        self.rng = rng or random.Random()
        self.events: list[dict[str, Any]] = []
        self._last_event_at = 0.0

    async def _handle_quiz(self, video_frame, ev: dict[str, Any]) -> None:
        """弹题决策（与同步版逻辑一致：管线作答→日志→半自动只填/全自动填交）。"""
        from ...ai.llm import LLMClient
        from ...ai.quiz import handle_quiz

        try:
            dec = handle_quiz(self.settings.quiz, self.conn,
                              LLMClient(self.settings.llm, data_dir=self.settings.data_dir),
                              ev, account_id=self.account_id)
        except Exception as exc:
            dec = {"action": "manual", "reason": repr(exc)}
        log.info("弹题：%s → %s", (ev.get("stem") or "")[:30], dec.get("action"))
        self.conn.execute(
            "INSERT INTO run_logs(ts, level, event, detail, account_id) VALUES(?,?,?,?,?)",
            (time.strftime("%Y-%m-%dT%H:%M:%S"),
             "WARNING" if dec["action"] == "manual" else "INFO",
             "quiz-" + str(dec.get("action")),
             f"{(ev.get('stem') or '')[:40]}｜{dec.get('source', '-')}｜"
             f"置信{dec.get('confidence', 0):.2f}｜{dec.get('answer', [])}",
             self.account_id))
        self.conn.commit()
        if dec.get("action") in ("fill", "fill_submit"):
            try:
                r = await video_frame.evaluate(
                    "([ans, sub]) => window.__xxtQuizFill && window.__xxtQuizFill(ans, sub)",
                    [dec.get("answer", []), dec.get("action") == "fill_submit"])
                log.info("弹题回填结果：%s", r)
            except Exception as exc:
                log.warning("弹题回填失败（选择器待核对）：%r", exc)

    def _on_event(self, source, payload: str) -> None:
        try:
            self.events.append(json.loads(payload))
            self._last_event_at = time.monotonic()
        except Exception:
            pass

    def _drain(self) -> list[dict[str, Any]]:
        out, self.events = self.events, []
        return out

    async def _play_video(self, page, video_frame, label: str) -> bool | str:
        """播放一个视频。返回 True=真播完（可记账）；"already"=平台本就已完成；
        False=没播成（不记账）。三态区分是 2026-10-25 假 video-done 事故的修法。"""
        rate = sample_speed(self.rng, self.pace)
        js = build_watcher_js(rate)
        # 队列里可能残留上一节 watcher 的事件：watcher 每 3s 发 tick，点"下一节"后
        # 旧文档还在发，拟人间隔期间一直往队列里写。不清掉就会把"上一节 495/495s"
        # 当成新视频已播完 → 0 秒写一条假 video-done 并把章节误标 done
        # （2026-10-25 账号#2 实证：22:49:53 与 22:50:01 两条记录时长完全相同）。
        self._drain()
        if await video_frame.evaluate(js) != "ok":
            log.warning("[#%s][%s] watcher 注入失败（跳过）", self.account_id, label)
            return False
        self._drain()  # 注入瞬间到达的 watcher-ready 等也属"旧状态"，一并丢弃
        injected = time.monotonic()
        log.info("[#%s][%s] 开始播放：倍速 %.2f", self.account_id, label, rate)
        max_wait, ended, last_report, start = 600, False, 0.0, time.monotonic()
        last_dur = 0
        saw_progress = False   # 是否见过"明确未播完"的进度（可信播放的必要证据）
        at_end_streak = 0      # 连续"一上来就在末尾"的 tick 数
        while time.monotonic() - start < max_wait:
            got = self._drain()
            if not got and time.monotonic() - max(self._last_event_at, start) > 90:
                raise RuntimeError("事件管道失效，中止会话防止盲切快进")
            for ev in got:
                t = ev.get("type")
                if t == "ended":
                    ended = True
                elif t == "quiz-popup":
                    await self._handle_quiz(video_frame, ev)
                elif t == "tick" and ev.get("dur"):
                    dur, cur = int(ev["dur"]), float(ev["cur"])
                    last_dur = dur
                    if cur < dur - 5:
                        saw_progress = True
                        at_end_streak = 0
                    if cur >= dur - 0.5:
                        at_end_streak += 1
                        # 只有"确实播过"或"耗时与时长匹配"才算完成，否则视为播放器
                        # 还没换到新视频（残留状态），继续观察而不是宣布完成。
                        if is_plausible_completion(
                                saw_progress=saw_progress,
                                wall_seconds=time.monotonic() - injected,
                                duration=dur, rate=rate):
                            ended = True
                    if time.monotonic() - last_report > 30:
                        last_report = time.monotonic()
                        log.info("[#%s][%s] 进度 %.0f/%.0fs", self.account_id, label, cur, dur)
                        max_wait = min(max_wait, (time.monotonic() - start)
                                       + (dur - cur) / rate + 90)
            if at_end_streak >= 5 and not saw_progress:
                # 约 15 秒（每 3s 一 tick）始终停在末尾且从未见过进度：
                # 判定"平台侧本来就已完成"，只标章节不记额度，避免额度虚增与死循环。
                log.warning("[#%s][%s] 一上来就在末尾（%.0fs），判为平台已完成，不记账",
                            self.account_id, label, last_dur)
                self._last_video_dur = 0
                return "already"
            if ended:
                break
            try:
                await page.wait_for_timeout(1000)
            except Exception:
                break
        self._drain()
        self._last_video_dur = last_dur
        log.info("[#%s][%s] 播放结束（ended=%s）", self.account_id, label, ended)
        return ended

    async def run(self, course_row: dict[str, Any], limit: int | None = None,
                  ignore_window: bool = False) -> dict[str, Any]:
        from playwright.async_api import async_playwright

        stats = {"videos": 0, "stop_reason": ""}
        if not ignore_window and not within_daily_window(self.pace):
            stats["stop_reason"] = "超出每日学习时段"  # 前置：零浏览器访问
            return stats
        # 前置检查（全本地：待学章节 + 额度）——任一不达标即返回，零浏览器、零平台访问。
        # 2026-10-25 实测：#1 额度已达标时仍先打开了课程页才判定（白跑一次真实访问），故前置。
        pending = self.conn.execute(
            "SELECT platform_chapter_id, name FROM chapters WHERE course_id=? "
            "AND status='pending' ORDER BY idx LIMIT 1", (course_row["id"],)
        ).fetchone()
        if pending is None:
            stats["stop_reason"] = "无待学章节"
            return stats
        kid, leaf_name = pending["platform_chapter_id"], pending["name"]

        # 额度口径（2026-10-25 修正）：两项都按"今日已用"扣减，跨会话有效。
        # 时长从 run_logs 的 video-done 详情累计秒数算，不再用进程内计时——
        # 否则停掉再启动就多出一整份额度，"每天 N 分钟"名不副实。
        budget_minutes, budget_tasks = sample_daily_budget(self.rng, self.quota)
        today = time.strftime("%Y-%m-%d")
        rows = self.conn.execute(
            "SELECT detail FROM run_logs WHERE event='video-done' AND account_id=?"
            " AND ts LIKE ?", (self.account_id, f"{today}%"),
        ).fetchall()
        done_today = len(rows)
        done_seconds = sum_done_seconds(r["detail"] or "" for r in rows)
        if budget_tasks:
            budget_tasks = max(0, budget_tasks - done_today)
            if budget_tasks == 0:
                stats["stop_reason"] = f"任务数额度达标（今日已完成 {done_today} 个）"
                return stats
        if budget_minutes:
            left_minutes = remaining_minutes(budget_minutes, done_seconds)
            if left_minutes <= 0:
                stats["stop_reason"] = (
                    f"时长额度达标（今日已学 {done_seconds // 60} 分钟）")
                return stats
            budget_minutes = left_minutes
        log.info("[#%s] 当日剩余额度：%s 分钟 / %s 个（0 即不限；今日已学 %s 分钟 %s 个）",
                 self.account_id,
                 f"{budget_minutes:.0f}" if budget_minutes else "不限",
                 budget_tasks or "不限", done_seconds // 60, done_today)

        # 节级重试策略全部来自 [learn]（2026-10-27 配置化）：
        #   enter_retries     单节点开最多点几次（每次等任务页 15 秒）
        #   max_skip_chapters 连续几节打不开就收工
        #   non_video_streak  连续几个非视频任务点算本段结束
        enter_retries = _learn_int(self.settings, "enter_retries", 2, 1, 5)
        max_skip = _learn_int(self.settings, "max_skip_chapters", 3, 1, 10)
        non_video_streak_cap = _learn_int(self.settings, "non_video_streak", 4, 1, 20)
        log.info("[#%s] 节级策略：单节最多点 %d 次｜连续 %d 节打不开收工｜连续 %d 个非视频点结束",
                 self.account_id, enter_retries, max_skip, non_video_streak_cap)

        async with async_playwright() as p:
            from ...core.stealth import LAUNCH_ARGS, async_hardened_context

            browser = await p.chromium.launch(headless=True, args=LAUNCH_ARGS)
            ctx_kw: dict[str, Any] = {"storage_state": str(self.state_path)}
            if self.proxy:
                ctx_kw["proxy"] = {"server": self.proxy}
            context = await async_hardened_context(browser, **ctx_kw)
            await context.expose_binding("__xxtEvent", self._on_event)
            page = await context.new_page()
            try:
                tree_frame = await _open_chapter_frame(page, course_row["url"])
                if tree_frame is None:
                    # 2026-10-25：这里原本直接 .locator() → AttributeError 被吞进黑洞，
                    # 表现为"点了开始、日志只有额度行、进程已死"。给出可读原因。
                    stats["stop_reason"] = "打不开章节树（登录态可能已失效，或课程链接过期）——请重新登录该账号"
                    log.warning("[#%s] %s", self.account_id, stats["stop_reason"])
                    return stats
                # 入口/翻页兜底（2026-10-27 P0-3b/3c）：打不开任务页就换下一节，
                # 连续 3 节都不行才收工，并把试过哪几节写进 stop_reason。
                tried_entry: list[str] = []

                async def _enter_chapter(target_kid: str) -> bool:
                    """点开知识点并确认真进了任务页；不成就再点一次（章节组可能会被折叠）。"""
                    nonlocal tree_frame
                    for attempt in range(enter_retries):
                        # B1（2026-10-27）：同一会话里章节树 frame 只取一次是不够的——
                        # 播完一节后页面会重建 iframe，旧句柄必然 detached，于是后面每一节
                        # 都点不动（#1 的 5.1/5.2/5.3 就这么被白丢）。点之前先探活，失效重取。
                        if not await _frame_alive(tree_frame):
                            log.warning("[#%s] 章节树 frame 已失效（iframe 被重建），重新获取",
                                        self.account_id)
                            again = await _open_chapter_frame(page, course_row["url"])
                            if not await _frame_alive(again):
                                continue
                            tree_frame = again
                        try:
                            await tree_frame.locator(f"#cur{target_kid}").first.click(timeout=15_000)
                        except Exception as exc:
                            log.warning("[#%s] 点击知识点 %s 失败：%r",
                                        self.account_id, target_kid, exc)
                            tree_frame = None  # 下一轮强制重取，不再拿死句柄重试
                            continue
                        if await _wait_task_page(page, 15):
                            return True
                        log.warning("[#%s] 知识点 %s 点击后 15 秒未见任务页（第 %d 次）",
                                    self.account_id, target_kid, attempt + 1)
                    return False

                async def _jump_next_pending(tag: str,
                                             after_pid: str | None = None) -> tuple[str, str] | None:
                    """回章节列表按本地 pending 顺序进下一节（最多试 max_skip 节）。"""
                    for _ in range(max_skip):
                        pick = pick_pending_after(self.conn, course_row["id"], after_pid,
                                                  tried_entry)
                        if pick is None:
                            stats["stop_reason"] = f"{tag}；本地已无待学章节"
                            return None
                        k2, n2 = pick
                        log.info("[#%s] %s → 改从章节列表进下一节：%s",
                                 self.account_id, tag, n2)
                        if await _enter_chapter(k2):
                            return k2, n2
                        # 进不去就是没学成：保持 pending 待下次再试，只在本会话内跳过。
                        # （旧代码在这里写 visited，等于"没看过的节永久放弃"，
                        #   2026-10-27 用户明确要求：没学过/没完成一律算待学。）
                        tried_entry.append(k2)
                        log.warning("[#%s] %s 打不开，保持待学（本会话内不再试）：%s",
                                    self.account_id, tag, n2)
                    stats["stop_reason"] = (f"{tag}；连续 {max_skip} 节都打不开任务页"
                                            f"（{'、'.join(tried_entry[-3:])}）")
                    return None

                # 进入首个待学章节（同上：失败就换下一节，最多 3 节）
                entered = False
                while not entered and len(tried_entry) < max_skip:
                    log.info("[#%s] 进入知识点：%s（%s）", self.account_id, leaf_name, kid)
                    if await _enter_chapter(kid):
                        entered = True
                        break
                    # 同上：进不去不算学过，保持 pending，下次会话还会再试
                    tried_entry.append(kid)
                    log.warning("[#%s] 首节打不开任务页，保持待学：%s",
                                self.account_id, leaf_name)
                    pick = pick_pending_after(self.conn, course_row["id"], kid, tried_entry)
                    if pick is None:
                        break
                    kid, leaf_name = pick
                if not entered:
                    stats["stop_reason"] = (f"连续 {len(tried_entry)} 节都打不开任务页"
                                            f"（{'、'.join(tried_entry)}）")
                    log.warning("[#%s] %s", self.account_id, stats["stop_reason"])
                    return stats

                study_start: float | None = None
                no_video_streak = 0
                while limit is None or stats["videos"] < limit:
                    if not ignore_window and not within_daily_window(self.pace):
                        stats["stop_reason"] = "超出每日学习时段"
                        break
                    if budget_tasks and stats["videos"] >= budget_tasks:
                        stats["stop_reason"] = "任务数额度达标"
                        break
                    if budget_minutes and study_start and \
                            (time.monotonic() - study_start) / 60 >= budget_minutes:
                        stats["stop_reason"] = "时长额度达标"
                        break
                    try:
                        video_frame = await _find_frame_with(page, "video", timeout_s=25)
                        if video_frame is not None:
                            no_video_streak = 0
                            if study_start is None:
                                study_start = time.monotonic()
                            title = await _current_task_title(page) or leaf_name
                            played = await self._play_video(page, video_frame, title)
                            cid = match_chapter_id(self.conn, course_row["id"], title)
                            if cid is not None:
                                # B2 配套：把“当前节”指向刚播完的那节。
                                # 以前 kid/leaf_name 一直停在节目录时的值，“下一节”按钮
                                # 已经跳到新节了它也不知道 → 回节列表兑底时按旧的 idx
                                # 往后取，顺序会错。
                                r_cur = self.conn.execute(
                                    "SELECT platform_chapter_id, name FROM chapters WHERE id=?",
                                    (cid,)).fetchone()
                                if r_cur and r_cur["platform_chapter_id"]:
                                    kid, leaf_name = r_cur["platform_chapter_id"], r_cur["name"]
                            if played is True:
                                stats["videos"] += 1
                                self.conn.execute(
                                    "INSERT INTO run_logs(ts, level, event, detail, account_id)"
                                    " VALUES(?,?,?,?,?)",
                                    (time.strftime("%Y-%m-%dT%H:%M:%S"), "INFO",
                                     "video-done",
                                     f"{course_row['name']}｜{title}｜{getattr(self, '_last_video_dur', 0)}s",
                                     self.account_id))
                                if cid is not None:
                                    self.conn.execute(
                                        "UPDATE chapters SET status='done' WHERE id=?", (cid,))
                                self.conn.commit()
                            elif played == "already":
                                # 平台侧本来就已完成：只标章节，不写 video-done（免得额度虚增），
                                # 也不加视频数——但必须标 done，否则下次又选中它而死循环。
                                if cid is not None:
                                    self.conn.execute(
                                        "UPDATE chapters SET status='done' WHERE id=?", (cid,))
                                    self.conn.commit()
                        else:
                            no_video_streak += 1
                            log.info("[#%s] 跳过非视频任务点（第 %d 次，如作业/阅读）", self.account_id, no_video_streak)
                            if no_video_streak >= non_video_streak_cap:  # 连续无视频才认头（作业点应穿过）
                                stats["stop_reason"] = "连续无视频任务点，本段结束"
                                break
                    except RuntimeError:
                        raise
                    except Exception as exc:
                        log.error("[#%s] 视频处理异常（跳过继续）：%r", self.account_id, exc)
                        no_video_streak += 1
                        if no_video_streak >= 3:
                            stats["stop_reason"] = "连续异常过多，中止"
                            break

                    # 越窗后不必再点"下一节"：一次真实页面推进 + 一个拟人间隔都是浪费。
                    # 2026-10-25 实测：#1 23:02:25 播完后仍点了下一节、等满 69 秒间隔，
                    # 23:03:36 才发现超窗——检查点前移即可省掉这两步。
                    if not ignore_window and not within_daily_window(self.pace):
                        stats["stop_reason"] = "超出每日学习时段"
                        break

                    # 每轮重取：#prevNextFocusNext 在任务点/章节切换后 iframe 会重建，
                    # 缓存旧 frame 必然 detached。2026-10-25 实测：账号#2 因复用失效 frame，
                    # click 超时 8s 后抛异常，被误判为"到课程末尾"提前结束（视频 0 个）。
                    next_frame = await _find_frame_with(page, "#prevNextFocusNext", timeout_s=10)
                    if next_frame is None:
                        # P0-3c（2026-10-27）：找不到“下一节”不再直接收工——回章节列表
                        # 按本地 pending 继续（#1 就是这么在 4.8 上白掉一整次机会的）。
                        nxt = await _jump_next_pending("找不到下一节按钮", kid)
                        if nxt is None:
                            break
                        kid, leaf_name = nxt
                        continue
                    clicked = False
                    for attempt in range(enter_retries):
                        try:
                            await next_frame.locator("#prevNextFocusNext").first.click(timeout=8_000)
                            clicked = True
                            break
                        except Exception:
                            if attempt:  # 第二次仍失败才算真不可点
                                break
                            next_frame = await _find_frame_with(page, "#prevNextFocusNext",
                                                               timeout_s=10)
                            if next_frame is None:
                                break
                    if not clicked:
                        # "没下一节"不等于"课程到头了"：2026-10-25 账号#2 在还有 47 节
                        # 待学时被误报"确已到末尾"。先按本地 pending 兜底进下一节，
                        # 确实没得进才收工（2026-10-27 P0-3c）。
                        nxt = await _jump_next_pending("下一节按钮不可点", kid)
                        if nxt is not None:
                            kid, leaf_name = nxt
                            continue
                        break

                    gap = sample_gap(self.rng, self.pace)
                    log.info("[#%s] 拟人间隔 %.0f 秒后继续", self.account_id, gap)
                    waited = 0.0
                    while waited < gap:
                        step = min(1.0, gap - waited)
                        await page.wait_for_timeout(int(step * 1000))
                        waited += step

                # 会话收尾（2026-10-27 语义修正）：现在 visited = “非视频节点穿过了”，
                # 不再是“这节进过但没学成”。旧代码在这里无条件写入，会把最后一次
                # 播放失败/没播完的视频也标成 visited → 明天不再选它，等于永久漏学。
                # 用户口径：没学过/没完成 → 保持 pending。
                if is_non_video_name(leaf_name):
                    mark_entry_visited(self.conn, course_row["id"], kid)
            except RuntimeError as exc:
                stats["stop_reason"] = f"异常中止：{exc}"
                log.error("%s", exc)
            finally:
                try:
                    await context.storage_state(path=str(self.state_path))
                except Exception:
                    pass
                await browser.close()
        return stats


async def run_accounts(settings, conn, account_rows, course_row: dict[str, Any],
                       limit: int | None = None, max_concurrency: int = 2) -> list[dict[str, Any]]:
    """多账号并发驱动（§5.6 落地要点）：TaskGroup 结构化并发 + Semaphore 上限 + 启动错峰。"""
    sem = asyncio.Semaphore(max_concurrency)
    results: list[dict[str, Any]] = []

    async def supervise(row) -> None:
        from ...core.accounts import is_valid

        if not is_valid(row):
            results.append({"account": row["label"], "account_id": row["id"], "videos": 0,
                            "stop_reason": f"账号已过有效期（{row['valid_until']}）"})
            return
        async with sem:
            # 错峰：各账号启动前随机等待（0~180s，账号间拉开）
            jitter = random.uniform(0, 180)
            log.info("账号#%s %s：错峰 %.0f 秒后启动", row["id"], row["label"], jitter)
            await asyncio.sleep(jitter)
            state = settings.data_dir / "accounts" / str(row["id"]) / "state.json"
            if not state.exists():
                log.warning("账号#%s 无会话（state.json 缺失），跳过——先为其登录", row["id"])
                results.append({"account": row["label"], "account_id": row["id"], "videos": 0, "stop_reason": "未登录"})
                return
            # 关键：各账号用自己的课程入口（clazzid/cpi 因账号而异，不可共用 URL）。
            # 优先按平台课程ID精确匹配（跨账号唯一锚点，避免同名歧义）；
            # 老批次无 pid 时退回按课名全等匹配。
            cpid = course_row.get("platform_course_id")
            if cpid:
                course = conn.execute(
                    "SELECT * FROM courses WHERE account_id = ? AND platform_course_id = ? LIMIT 1",
                    (row["id"], cpid),
                ).fetchone()
            else:
                course = conn.execute(
                    "SELECT * FROM courses WHERE account_id = ? AND name = ? ORDER BY id LIMIT 1",
                    (row["id"], course_row["name"]),
                ).fetchone()
            if course is None:
                key = f"pid={cpid}" if cpid else f"课名《{course_row['name']}》"
                results.append({"account": row["label"], "account_id": row["id"], "videos": 0,
                                "stop_reason": f"该账号无此课程（{key}）——请核对或重新选课"})
                return
            runner = AsyncStudyRunner(
                settings, state, conn,
                pace=effective_pace(settings, row),
                quota=effective_quota(settings, row),
                proxy=row["proxy"],
                account_id=row["id"],
            )
            try:
                stats = await runner.run(dict(course), limit=limit)
            except Exception as exc:
                # TaskGroup 里任一任务抛异常会取消兄弟任务：一个账号崩不能连累其他账号。
                # 同时必须把 traceback 写进日志（2026-10-25：进程级静默崩溃排查过久）。
                log.error("[#%s] 会话异常终止：%r", row["id"], exc, exc_info=True)
                results.append({"account": row["label"], "account_id": row["id"],
                                "videos": 0, "stop_reason": f"异常终止：{exc!r}"})
                return
            stats["account"] = row["label"]
            stats["account_id"] = row["id"]
            results.append(stats)

    async with asyncio.TaskGroup() as tg:
        for row in account_rows:
            tg.create_task(supervise(row))
    return results
