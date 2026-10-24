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
from ...core.quota import sample_daily_budget
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
    from .study import username_sniffer

    sniffer, holder = username_sniffer()
    page.on("response", sniffer)
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
        rate = sample_speed(self.rng, self.pace)
        js = WATCHER_JS.replace("__XXT_RATE__", str(rate))
        if await video_frame.evaluate(js) != "ok":
            log.warning("[%s] watcher 注入失败（跳过）", label)
            return False
        log.info("[%s] 开始播放：倍速 %.2f", label, rate)
        max_wait, ended, last_report, start = 600, False, 0.0, time.monotonic()
        while time.monotonic() - start < max_wait:
            got = self._drain()
            if not got and time.monotonic() - max(self._last_event_at, start) > 90:
                raise RuntimeError("事件管道失效，中止会话防止盲切快进")
            for ev in got:
                t = ev.get("type")
                if t == "ended":
                    ended = True
                elif t == "tick" and ev.get("dur"):
                    if ev["cur"] >= ev["dur"] - 0.5:
                        ended = True
                    if time.monotonic() - last_report > 30:
                        last_report = time.monotonic()
                        log.info("[%s] 进度 %.0f/%.0fs", label, ev["cur"], ev["dur"])
                        max_wait = min(max_wait, (time.monotonic() - start)
                                       + (ev["dur"] - ev["cur"]) / rate + 90)
            if ended:
                break
            try:
                await page.wait_for_timeout(1000)
            except Exception:
                break
        self._drain()
        log.info("[%s] 播放结束（ended=%s）", label, ended)
        return ended

    async def run(self, course_row: dict[str, Any], limit: int | None = None,
                  ignore_window: bool = False) -> dict[str, Any]:
        from playwright.async_api import async_playwright

        stats = {"videos": 0, "stop_reason": ""}
        if not ignore_window and not within_daily_window(self.pace):
            stats["stop_reason"] = "超出每日学习时段"  # 前置：零浏览器访问
            return stats
        async with async_playwright() as p:
            launch_kw: dict[str, Any] = {"headless": True}
            browser = await p.chromium.launch(**launch_kw)
            context_kw: dict[str, Any] = {"storage_state": str(self.state_path)}
            if self.proxy:
                context_kw["proxy"] = {"server": self.proxy}
            context = await browser.new_context(**context_kw)
            await context.expose_binding("__xxtEvent", self._on_event)
            page = await context.new_page()
            try:
                tree_frame = await _open_chapter_frame(page, course_row["url"])
                pending = self.conn.execute(
                    "SELECT platform_chapter_id, name FROM chapters WHERE course_id=? "
                    "AND status='pending' ORDER BY idx LIMIT 1", (course_row["id"],)
                ).fetchone()
                if pending is None:
                    stats["stop_reason"] = "无待学章节"
                    return stats
                kid, leaf_name = pending["platform_chapter_id"], pending["name"]

                budget_minutes, budget_tasks = sample_daily_budget(self.rng, self.quota)
                today = time.strftime("%Y-%m-%d")
                done_today = self.conn.execute(
                    "SELECT COUNT(*) AS c FROM run_logs WHERE event='video-done' AND ts LIKE ?"
                    " AND account_id = ?",
                    (f"{today}%", self.account_id),
                ).fetchone()["c"]
                if budget_tasks:
                    budget_tasks = max(0, budget_tasks - done_today)
                    if budget_tasks == 0:
                        stats["stop_reason"] = f"任务数额度达标（今日已完成 {done_today}）"
                        return stats
                log.info("当日剩余额度：%.0f 分钟 / %s 个", budget_minutes, budget_tasks or "不限")

                log.info("进入知识点：%s（%s）", leaf_name, kid)
                await tree_frame.locator(f"#cur{kid}").first.click(timeout=15_000)

                study_start: float | None = None
                no_video_streak, next_frame = 0, None
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
                                     "video-done", f"{course_row['name']}｜{title}",
                                     self.account_id))
                                cid = match_chapter_id(self.conn, course_row["id"], title)
                                if cid is not None:
                                    self.conn.execute(
                                        "UPDATE chapters SET status='done' WHERE id=?", (cid,))
                                self.conn.commit()
                        else:
                            no_video_streak += 1
                            if no_video_streak >= 2:
                                stats["stop_reason"] = "连续无视频任务点，本段结束"
                                break
                    except RuntimeError:
                        raise
                    except Exception as exc:
                        log.error("视频处理异常（跳过继续）：%r", exc)
                        no_video_streak += 1
                        if no_video_streak >= 3:
                            stats["stop_reason"] = "连续异常过多，中止"
                            break

                    if next_frame is None:
                        next_frame = await _find_frame_with(page, "#prevNextFocusNext", timeout_s=10)
                    if next_frame is None:
                        stats["stop_reason"] = "找不到下一节按钮，结束"
                        break
                    try:
                        await next_frame.locator("#prevNextFocusNext").first.click(timeout=8_000)
                    except Exception:
                        stats["stop_reason"] = "下一节按钮不可点（可能已到课程末尾）"
                        break

                    gap = sample_gap(self.rng, self.pace)
                    log.info("拟人间隔 %.0f 秒后继续", gap)
                    waited = 0.0
                    while waited < gap:
                        step = min(1.0, gap - waited)
                        await page.wait_for_timeout(int(step * 1000))
                        waited += step

                self.conn.execute(
                    "UPDATE chapters SET status='done' WHERE course_id=? AND platform_chapter_id=?",
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
        async with sem:
            # 错峰：各账号启动前随机等待（0~180s，账号间拉开）
            jitter = random.uniform(0, 180)
            log.info("账号#%s %s：错峰 %.0f 秒后启动", row["id"], row["label"], jitter)
            await asyncio.sleep(jitter)
            state = settings.data_dir / "accounts" / str(row["id"]) / "state.json"
            if not state.exists():
                log.warning("账号#%s 无会话（state.json 缺失），跳过——先为其登录", row["id"])
                results.append({"account": row["label"], "videos": 0, "stop_reason": "未登录"})
                return
            runner = AsyncStudyRunner(
                settings, state, conn,
                pace=effective_pace(settings, row),
                quota=effective_quota(settings, row),
                proxy=row["proxy"],
                account_id=row["id"],
            )
            stats = await runner.run(course_row, limit=limit)
            stats["account"] = row["label"]
            results.append(stats)

    async with asyncio.TaskGroup() as tg:
        for row in account_rows:
            tg.create_task(supervise(row))
    return results
