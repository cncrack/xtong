"""学习通 M2 同步学习运行器：外层 Playwright 编排 + 页内 watcher（上报-决策，PLAN §5.3）。

流程：打开课程 → 点导航"章节"进入章节树（新版 fanyav3/studentcourse）→ 解析/更新章节库
→ 点击首个待学叶子 → 进入旧版知识点页（视频/任务点在此层，§5.8 选择器适用）
→ 循环：找视频帧 → 注入 watcher 播放至 ended → 点 #prevNextFocusNext 切下一任务点
→ 节奏全部走 core.pace 抽样（无固定延时）。

模块边界（2026-10-25 解耦）：
- 纯数据/JS 拼接 → `pagejs.py`（同步异步共用）
- 同步 page 辅助函数 → `pagesync.py`（异步引擎禁止引用）
本文件只留 `StudyRunner` 与它的编排逻辑。
"""

from __future__ import annotations

import random
import time
from typing import Any

from ...core.pace import FocusTimer, sample_gap, sample_speed, within_daily_window
from ...core.playback import is_plausible_completion
from ...core.quota import remaining_minutes, sample_daily_budget, sum_done_seconds
from ...log import get_logger
from .pagejs import WATCHER_JS, build_watcher_js, match_chapter_id  # noqa: F401  (对外兼容导出)
from .pagesync import current_task_title, find_frame_with, open_chapter_frame

log = get_logger("chaoxing.study")


class StudyRunner:
    """单账号、单课程的一次学习会话（长会话模式：一次浏览器上下文跑到底）。"""

    def __init__(self, settings, state_path: Path, conn, seed: int | None = None,
                 account_id: int = 1, quota: dict[str, Any] | None = None,
                 pace: dict[str, Any] | None = None) -> None:
        self.settings = settings
        self.state_path = state_path
        self.conn = conn
        self.account_id = account_id
        self.rng = random.Random(seed)
        # quota / pace 由调用方传"账号级覆盖后的生效值"（core.accounts.effective_*）。
        # 2026-10-25 修：此前直接吃 settings.quota/pace，CLI `xxt learn` 因而
        # 完全忽略账号管理里设的额度与时段窗口（面板/mlearn 走异步版，一直是对的）。
        self.pace = pace or settings.pace
        self.quota = quota or settings.quota
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

    def _handle_quiz(self, video_frame, ev: dict[str, Any]) -> None:
        """弹题决策入口：管线作答 → 日志落库 → 半自动只填/全自动填交（§5.4）。"""
        from ...ai.llm import LLMClient
        from ...ai.quiz import handle_quiz

        try:
            dec = handle_quiz(self.settings.quiz, self.conn,
                              LLMClient(self.settings.llm, data_dir=self.settings.data_dir),
                              ev, account_id=self.account_id)
        except Exception as exc:  # 决策异常不干扰播放
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
                r = video_frame.evaluate(
                    "([ans, sub]) => window.__xxtQuizFill && window.__xxtQuizFill(ans, sub)",
                    [dec.get("answer", []), dec.get("action") == "fill_submit"])
                log.info("弹题回填结果：%s", r)
            except Exception as exc:
                log.warning("弹题回填失败（选择器待核对）：%r", exc)

    def _play_video(self, page, video_frame, knowledge_label: str) -> bool | str:
        """注入 watcher 并等待播放结束。True=真播完；"already"=平台已完成；False=没播成。

        2026-10-25 与异步版同步修：点"下一节"后旧文档的 tick 会残留在队列里，
        一 drain 就会把上一节的"已到末尾"当成新视频播完（0 秒假 video-done）。
        """
        rate = sample_speed(self.rng, self.pace)
        js = build_watcher_js(rate)
        self._drain()  # 丢弃上一节残留事件
        ret = video_frame.evaluate(js)
        if ret != "ok":
            log.warning("[%s] watcher 注入返回 %s（跳过该视频）", knowledge_label, ret)
            return False
        self._drain()
        injected = time.monotonic()
        log.info("[%s] 开始播放：倍速 %.2f", knowledge_label, rate)

        deadline_margin, max_wait = 90, 600
        ended, last_report, start = False, 0.0, time.monotonic()
        last_dur = 0
        saw_progress, at_end_streak = False, 0
        while time.monotonic() - start < max_wait:
            got = self._drain()
            if not got and time.monotonic() - max(self._last_event_at, start) > 90:
                raise RuntimeError("事件管道失效（watcher 事件 90 秒未达），中止会话防止盲切快进")
            for ev in got:
                t = ev.get("type")
                if t == "ended":
                    ended = True
                elif t == "quiz-popup":
                    self._handle_quiz(video_frame, ev)
                elif t in ("error", "play-blocked"):
                    log.warning("[%s] 播放异常事件：%s", knowledge_label, t)
                elif t == "tick" and ev.get("dur"):
                    dur, cur = int(ev["dur"]), float(ev["cur"])
                    last_dur = dur
                    if cur < dur - 5:
                        saw_progress, at_end_streak = True, 0
                    if cur >= dur - 0.5:  # attach 时可能已播完（也可能是残留状态）
                        at_end_streak += 1
                        if is_plausible_completion(
                                saw_progress=saw_progress,
                                wall_seconds=time.monotonic() - injected,
                                duration=dur, rate=rate):
                            ended = True
                    if time.monotonic() - last_report > 30:
                        last_report = time.monotonic()
                        log.info(
                            "[%s] 进度 %.0f/%.0fs", knowledge_label, cur, dur
                        )
                        max_wait = min(
                            max_wait, (time.monotonic() - start) + (dur - cur) / rate + deadline_margin
                        )
            if at_end_streak >= 5 and not saw_progress:
                log.warning("[%s] 一上来就在末尾（%.0fs），判为平台已完成，不记账",
                            knowledge_label, last_dur)
                self._last_video_dur = 0
                return "already"
            if ended:
                break
            try:
                page.wait_for_timeout(1000)  # 事件泵轮询
            except Exception:
                break
        self._drain()
        self._last_video_dur = last_dur  # 供 video-done 记录真实观看时长
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
        elif not within_daily_window(self.pace):
            # 时段检查必须在任何浏览器动作之前——窗口外连课程页都不应访问
            stats["stop_reason"] = "超出每日学习时段"
            log.info("拒绝启动：%s", stats["stop_reason"])
            return stats
        # 前置检查（全本地：待学章节 + 额度）——不达标即返回，零浏览器、零平台访问（与异步版一致）
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

        # v0.10 额度口径：账号×天。两项都按"今日已用"扣减（时长取 video-done 累计秒数，
        # 跨会话有效；2026-10-25 起不再用进程内计时，否则重启一次就多一份额度）
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
                log.info("拒绝启动：%s", stats["stop_reason"])
                return stats
        if budget_minutes:
            left_minutes = remaining_minutes(budget_minutes, done_seconds)
            if left_minutes <= 0:
                stats["stop_reason"] = f"时长额度达标（今日已学 {done_seconds // 60} 分钟）"
                log.info("拒绝启动：%s", stats["stop_reason"])
                return stats
            budget_minutes = left_minutes
        log.info("当日剩余额度：时长 %s 分钟 / 任务 %s 个（任一达标即停，0 即不限；"
                 "今日已学 %s 分钟 %s 个）",
                 f"{budget_minutes:.0f}" if budget_minutes else "不限",
                 budget_tasks or "不限", done_seconds // 60, done_today)

        with sync_playwright() as p:
            from ...core.stealth import LAUNCH_ARGS, hardened_context

            browser = p.chromium.launch(headless=True, args=LAUNCH_ARGS)
            context = hardened_context(browser, storage_state=str(self.state_path))
            context.expose_binding("__xxtEvent", self._on_event)
            page = context.new_page()
            try:
                tree_frame = open_chapter_frame(page, course_row["url"])
                if tree_frame is None:
                    # 与异步版一致：原直接 .locator() 会抛 AttributeError，原因是"会话失效/链接过期"
                    stats["stop_reason"] = "打不开章节树（登录态可能已失效，或课程链接过期）——请重新登录该账号"
                    log.warning("%s", stats["stop_reason"])
                    return stats
                if not window_ok():
                    stats["stop_reason"] = "超出每日学习时段"
                    return stats  # 此处为会话中途越窗（browser 已开），交由 finally 收尾
                log.info("进入知识点：%s（%s）", leaf_name, kid)
                tree_frame.locator(f"#cur{kid}").first.click(timeout=15_000)

                # 旧版页循环：视频 → next → 视频 …（受 limit / 每日双额度 / 时段窗口三重约束）
                study_start: float | None = None
                no_video_streak = 0
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
                            played = self._play_video(page, video_frame, title)
                            # 经"下一节"推进的知识点按任务标题精确落库
                            cid = match_chapter_id(self.conn, course_row["id"], title)
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
                                        "UPDATE chapters SET status='done' WHERE id=?", (cid,)
                                    )
                                self.conn.commit()
                            elif played == "already":
                                # 平台已完成：只标章节，不记账（与异步版口径一致）
                                if cid is not None:
                                    self.conn.execute(
                                        "UPDATE chapters SET status='done' WHERE id=?", (cid,)
                                    )
                                    self.conn.commit()
                        else:
                            no_video_streak += 1
                            log.info("跳过非视频任务点（第 %d 次，如作业/阅读）", no_video_streak)
                            if no_video_streak >= 4:  # 连续4个无视频才认头（章节间的作业点应穿过）
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

                    # 越窗后不必再点"下一节"（省一次页面推进 + 一个拟人间隔，同 astudy）
                    if not window_ok():
                        stats["stop_reason"] = "超出每日学习时段"
                        break

                    # 切下一任务点（旧版页的"下一节"按钮）
                    # 每轮重取：切任务点后 iframe 会重建，缓存旧 frame 必然 detached，
                    # click 超时后会被误判为"到课程末尾"而提前结束（同 astudy 2026-10-25 修复）
                    next_frame = find_frame_with(page, "#prevNextFocusNext", timeout_s=10)
                    if next_frame is None:
                        stats["stop_reason"] = "找不到下一节按钮，结束"
                        break
                    clicked = False
                    for attempt in range(2):
                        try:
                            next_frame.locator("#prevNextFocusNext").first.click(timeout=8_000)
                            clicked = True
                            break
                        except Exception:
                            if attempt:  # 第二次仍失败才算真不可点
                                break
                            next_frame = find_frame_with(page, "#prevNextFocusNext", timeout_s=10)
                            if next_frame is None:
                                break
                    if not clicked:
                        # 以本地章节表为准：还有待学就不是"到末尾"（同 astudy 修复）
                        left = self.conn.execute(
                            "SELECT COUNT(*) AS c FROM chapters WHERE course_id=?"
                            " AND status='pending'", (course_row["id"],)).fetchone()["c"]
                        if left:
                            stats["stop_reason"] = (
                                f"下一节按钮不可点（页面未就绪），本地仍有 {left} 节待学"
                                "——重新启动会话即可继续")
                        else:
                            stats["stop_reason"] = "全部章节已学完（无下一节）"
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
                    # 入口叶子只标 visited（路过）——done 仅留给真实完成的视频/作业（标题匹配路径）
                    "UPDATE chapters SET status='visited' WHERE course_id=? AND platform_chapter_id=?",
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
