"""async 版学习运行器（v0.13 决策：M4 一步到位 asyncio + async Playwright）。

移植纪律（devlog 17）：同步版 study.py 保留至本模块通过同一套验收（整课实跑 + 测试全绿）后删除。
策略纯逻辑（额度/节奏/标题同步）直接复用 study.py 的函数，仅执行层换 async API。
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from pathlib import Path
from typing import Any

from ...core.accounts import effective_pace, effective_quota
from ...core.pace import sample_gap, sample_speed, within_daily_window
from ...core.quota import remaining_minutes, sample_daily_budget, sum_done_seconds
from ...log import get_logger
from .study import WATCHER_JS, match_chapter_id

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
            dec = handle_quiz(self.settings.quiz, self.conn, LLMClient(self.settings.llm, data_dir=self.settings.data_dir), ev)
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

    async def _play_video(self, page, video_frame, label: str) -> bool:
        from .study import build_watcher_js

        rate = sample_speed(self.rng, self.pace)
        js = build_watcher_js(rate)
        if await video_frame.evaluate(js) != "ok":
            log.warning("[#%s][%s] watcher 注入失败（跳过）", self.account_id, label)
            return False
        log.info("[#%s][%s] 开始播放：倍速 %.2f", self.account_id, label, rate)
        max_wait, ended, last_report, start = 600, False, 0.0, time.monotonic()
        last_dur = 0
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
                    last_dur = int(ev["dur"])
                    if ev["cur"] >= ev["dur"] - 0.5:
                        ended = True
                    if time.monotonic() - last_report > 30:
                        last_report = time.monotonic()
                        log.info("[#%s][%s] 进度 %.0f/%.0fs", self.account_id, label, ev["cur"], ev["dur"])
                        max_wait = min(max_wait, (time.monotonic() - start)
                                       + (ev["dur"] - ev["cur"]) / rate + 90)
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
                log.info("[#%s] 进入知识点：%s（%s）", self.account_id, leaf_name, kid)
                await tree_frame.locator(f"#cur{kid}").first.click(timeout=15_000)

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
                            if await self._play_video(page, video_frame, title):
                                stats["videos"] += 1
                                self.conn.execute(
                                    "INSERT INTO run_logs(ts, level, event, detail, account_id)"
                                    " VALUES(?,?,?,?,?)",
                                    (time.strftime("%Y-%m-%dT%H:%M:%S"), "INFO",
                                     "video-done",
                                     f"{course_row['name']}｜{title}｜{getattr(self, '_last_video_dur', 0)}s",
                                     self.account_id))
                                cid = match_chapter_id(self.conn, course_row["id"], title)
                                if cid is not None:
                                    self.conn.execute(
                                        "UPDATE chapters SET status='done' WHERE id=?", (cid,))
                                self.conn.commit()
                        else:
                            no_video_streak += 1
                            log.info("[#%s] 跳过非视频任务点（第 %d 次，如作业/阅读）", self.account_id, no_video_streak)
                            if no_video_streak >= 4:  # 连续4个无视频才认头（章节间的作业点应穿过）
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

                    # 每轮重取：#prevNextFocusNext 在任务点/章节切换后 iframe 会重建，
                    # 缓存旧 frame 必然 detached。2026-10-25 实测：账号#2 因复用失效 frame，
                    # click 超时 8s 后抛异常，被误判为"到课程末尾"提前结束（视频 0 个）。
                    next_frame = await _find_frame_with(page, "#prevNextFocusNext", timeout_s=10)
                    if next_frame is None:
                        stats["stop_reason"] = "找不到下一节按钮，结束"
                        break
                    clicked = False
                    for attempt in range(2):
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
                        stats["stop_reason"] = "下一节按钮不可点（重取后仍失败，可能确已到末尾）"
                        break

                    gap = sample_gap(self.rng, self.pace)
                    log.info("[#%s] 拟人间隔 %.0f 秒后继续", self.account_id, gap)
                    waited = 0.0
                    while waited < gap:
                        step = min(1.0, gap - waited)
                        await page.wait_for_timeout(int(step * 1000))
                        waited += step

                self.conn.execute(
                    # 入口叶子只标 visited（路过）——done 仅留给真实完成的视频/作业（标题匹配路径）
                    "UPDATE chapters SET status='visited' WHERE course_id=? AND platform_chapter_id=?",
                    (course_row["id"], kid))
                self.conn.commit()
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
            # 关键：各账号用自己的课程入口（clazzid/cpi 因账号而异，不可共用 URL）
            course = conn.execute(
                "SELECT * FROM courses WHERE account_id = ? AND platform_course_id = ? LIMIT 1",
                (row["id"], course_row["platform_course_id"]),
            ).fetchone()
            if course is None:
                results.append({"account": row["label"], "account_id": row["id"], "videos": 0, "stop_reason": "该账号无此课程"})
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
