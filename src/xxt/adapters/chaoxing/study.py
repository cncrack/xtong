"""学习通 M2 学习运行器：外层 Playwright 编排 + 页内 watcher（上报-决策，PLAN §5.3）。

流程：打开课程 → 点导航"章节"进入章节树（新版 fanyav3/studentcourse）→ 解析/更新章节库
→ 点击首个待学叶子 → 进入旧版知识点页（视频/任务点在此层，§5.8 选择器适用）
→ 循环：找视频帧 → 注入 watcher 播放至 ended → 点 #prevNextFocusNext 切下一任务点
→ 节奏全部走 core.pace 抽样（无固定延时）。
"""

from __future__ import annotations

import json
import random
import time
from pathlib import Path
from typing import Any

from ...core.pace import FocusTimer, sample_gap, sample_speed, within_daily_window
from ...core.quota import sample_daily_budget
from ...log import get_logger

log = get_logger("chaoxing.study")

WATCHER_JS = (Path(__file__).parent / "watcher.js").read_text(encoding="utf-8")


def find_frame_with(page, selector: str, timeout_s: int = 30):
    """轮询所有 frame 直到某 frame 出现选择器（条件等待，非行为延时）。"""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        for fr in page.frames:
            try:
                if fr.locator(selector).count() > 0:
                    return fr
            except Exception:
                continue
        page.wait_for_timeout(800)
    return None


def open_chapter_frame(page, course_url: str):
    """进入课程并点开"章节"导航，返回章节树所在 frame。"""
    page.goto(course_url, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(4_000)
    top = next((f for f in page.frames if "fanyav3/stu" in f.url), None)
    if top is None:
        raise RuntimeError("未找到课程主页面（可能课程入口改版）")
    top.locator("text=章节").first.click(timeout=15_000)
    tree_frame = find_frame_with(page, ".chapter_item[id^=cur]", timeout_s=30)
    if tree_frame is None:
        raise RuntimeError("章节树未加载（.chapter_item 未出现）")
    return tree_frame


def current_task_title(page) -> str:
    """旧版知识点页当前任务点标题（.prev_title），拿不到返回空。"""
    fr = find_frame_with(page, ".prev_title", timeout_s=3)
    if fr is None:
        return ""
    try:
        return fr.locator(".prev_title").first.inner_text(timeout=3_000).strip()
    except Exception:
        return ""


def match_chapter_id(conn, course_id: int, title: str) -> int | None:
    """用旧版页任务标题模糊匹配章节库记录（如"尽快适应大学生活（上）"→ 2.1 记录 id）。"""
    if not title:
        return None
    row = conn.execute(
        "SELECT id FROM chapters WHERE course_id = ? AND name LIKE ? LIMIT 1",
        (course_id, f"%{title}%"),
    ).fetchone()
    return row["id"] if row else None


class StudyRunner:
    """单账号、单课程的一次学习会话（长会话模式：一次浏览器上下文跑到底）。"""

    def __init__(self, settings, state_path: Path, conn, seed: int | None = None) -> None:
        self.settings = settings
        self.state_path = state_path
        self.conn = conn
        self.rng = random.Random(seed)
        self.pace = settings.pace
        self.events: list[dict[str, Any]] = []
        self._last_event_at = 0.0
        self.focus = FocusTimer(self.rng, self.pace)

    # ---------- 事件桥 ----------

    def _on_event(self, source, payload: str) -> None:
        """expose_binding 回调：首参为 Playwright source 对象，真正的载荷在其后。"""
        try:
            self.events.append(json.loads(payload))
            self._last_event_at = time.monotonic()
        except Exception:
            pass

    def _drain(self) -> list[dict[str, Any]]:
        out, self.events = self.events, []
        return out

    # ---------- 单视频播放 ----------

    def _play_video(self, page, video_frame, knowledge_label: str) -> bool:
        """注入 watcher 并等待播放结束。返回是否完整播完。"""
        rate = sample_speed(self.rng, self.pace)
        js = WATCHER_JS.replace("__XXT_RATE__", str(rate))
        ret = video_frame.evaluate(js)
        if ret != "ok":
            log.warning("[%s] watcher 注入返回 %s（跳过该视频）", knowledge_label, ret)
            return False
        log.info("[%s] 开始播放：倍速 %.2f", knowledge_label, rate)

        deadline_margin, max_wait = 90, 600
        ended, last_report, start = False, 0.0, time.monotonic()
        while time.monotonic() - start < max_wait:
            got = self._drain()
            if not got and time.monotonic() - max(self._last_event_at, start) > 90:
                raise RuntimeError("事件管道失效（watcher 事件 90 秒未达），中止会话防止盲切快进")
            for ev in got:
                t = ev.get("type")
                if t == "ended":
                    ended = True
                elif t in ("error", "play-blocked"):
                    log.warning("[%s] 播放异常事件：%s", knowledge_label, t)
                elif t == "tick" and ev.get("dur"):
                    if ev["cur"] >= ev["dur"] - 0.5:  # attach 时可能已播完
                        ended = True
                    if time.monotonic() - last_report > 30:
                        last_report = time.monotonic()
                        log.info(
                            "[%s] 进度 %.0f/%.0fs", knowledge_label, ev["cur"], ev["dur"]
                        )
                        max_wait = min(
                            max_wait, (time.monotonic() - start) + (ev["dur"] - ev["cur"]) / rate + deadline_margin
                        )
            if ended:
                break
            try:
                page.wait_for_timeout(1000)  # 事件泵轮询
            except Exception:
                break
        self._drain()
        log.info("[%s] 播放结束（ended=%s）", knowledge_label, ended)
        return ended

    # ---------- 主循环 ----------

    def run(self, course_row: dict[str, Any], limit: int | None = None,
            ignore_window: bool = False) -> dict[str, Any]:
        from playwright.sync_api import sync_playwright

        def window_ok() -> bool:
            if ignore_window:
                return True
            return within_daily_window(self.pace)

        stats = {"videos": 0, "leaves": 0, "stop_reason": ""}
        if ignore_window:
            log.warning("已忽略每日学习时段限制（--ignore-window，仅限验收测试用）")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(storage_state=str(self.state_path))
            context.expose_binding("__xxtEvent", self._on_event)
            page = context.new_page()
            try:
                tree_frame = open_chapter_frame(page, course_row["url"])

                # 找首个待学叶子并点击（点击后本 frame 跳转到旧版知识点页）
                pending = self.conn.execute(
                    """
                    SELECT platform_chapter_id, name FROM chapters
                    WHERE course_id = ? AND status = 'pending' ORDER BY idx LIMIT 1
                    """,
                    (course_row["id"],),
                ).fetchone()
                if pending is None:
                    stats["stop_reason"] = "无待学章节"
                    return stats
                kid, leaf_name = pending["platform_chapter_id"], pending["name"]
                if not window_ok():
                    stats["stop_reason"] = "超出每日学习时段"
                    return stats
                log.info("进入知识点：%s（%s）", leaf_name, kid)
                tree_frame.locator(f"#cur{kid}").first.click(timeout=15_000)

                # 旧版页循环：视频 → next → 视频 …（受 limit / 每日双额度 / 时段窗口三重约束）
                budget_minutes, budget_tasks = sample_daily_budget(self.rng, self.settings.quota)
                log.info("当日额度：时长 %.0f 分钟 / 任务 %s 个（任一达标即停）",
                         budget_minutes, budget_tasks or "不限")
                study_start: float | None = None
                no_video_streak, next_frame = 0, None
                while limit is None or stats["videos"] < limit:
                    if not window_ok():
                        stats["stop_reason"] = "超出每日学习时段"
                        break
                    if budget_tasks and stats["videos"] >= budget_tasks:
                        stats["stop_reason"] = "任务数额度达标"
                        break
                    if budget_minutes and study_start is not None and \
                            (time.monotonic() - study_start) / 60 >= budget_minutes:
                        stats["stop_reason"] = "时长额度达标"
                        break
                    try:
                        video_frame = find_frame_with(page, "video", timeout_s=25)
                        if video_frame is not None:
                            no_video_streak = 0
                            if study_start is None:
                                study_start = time.monotonic()
                            title = current_task_title(page) or leaf_name
                            if self._play_video(page, video_frame, title):
                                stats["videos"] += 1
                                self.conn.execute(
                                    "INSERT INTO run_logs(ts, level, event, detail) VALUES(?,?,?,?)",
                                    (time.strftime("%Y-%m-%dT%H:%M:%S"), "INFO",
                                     "video-done", f"{course_row['name']}｜{title}"),
                                )
                                # 经"下一节"推进的知识点按任务标题精确落库
                                cid = match_chapter_id(self.conn, course_row["id"], title)
                                if cid is not None:
                                    self.conn.execute(
                                        "UPDATE chapters SET status='done' WHERE id=?", (cid,)
                                    )
                                self.conn.commit()
                        else:
                            no_video_streak += 1
                            if no_video_streak >= 2:
                                stats["stop_reason"] = "连续无视频任务点，本段结束"
                                break
                    except RuntimeError:
                        raise  # 管道失效等致命错误仍走安全中止
                    except Exception as exc:
                        # 单视频异常不终结会话：记失败、跳过、按节奏继续
                        log.error("视频处理异常（跳过继续）：%r", exc)
                        no_video_streak += 1
                        if no_video_streak >= 3:
                            stats["stop_reason"] = "连续异常过多，中止"
                            break

                    # 切下一任务点（旧版页的"下一节"按钮）
                    if next_frame is None:
                        next_frame = find_frame_with(page, "#prevNextFocusNext", timeout_s=10)
                    if next_frame is None:
                        stats["stop_reason"] = "找不到下一节按钮，结束"
                        break
                    try:
                        next_frame.locator("#prevNextFocusNext").first.click(timeout=8_000)
                    except Exception:
                        stats["stop_reason"] = "下一节按钮不可点（可能已到课程末尾）"
                        break

                    # 拟人切节间隔（三档加权抽样；轮询等待用 page.wait_for_timeout 实现）
                    gap = sample_gap(self.rng, self.pace)
                    log.info("拟人间隔 %.0f 秒后继续", gap)
                    waited = 0
                    while waited < gap:
                        step = min(1.0, gap - waited)
                        page.wait_for_timeout(int(step * 1000))
                        waited += step

                stats["leaves"] = stats["videos"]
                # 标记起始叶子完成（后续经"下一节"推进的知识点由 M3 精确同步）
                self.conn.execute(
                    "UPDATE chapters SET status='done' WHERE course_id=? AND platform_chapter_id=?",
                    (course_row["id"], kid),
                )
                self.conn.commit()
            except RuntimeError as exc:
                stats["stop_reason"] = f"异常中止：{exc}"
                log.error("%s", exc)
            finally:
                try:
                    context.storage_state(path=str(self.state_path))  # 滚动续期
                except Exception:
                    pass
                browser.close()
        return stats
