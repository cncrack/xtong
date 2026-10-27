"""守护「失败处理」两支（2026-10-27 P0-3d）。

1) `_auto_limits`：次数/退避/回读等待/抖动/封顶来自 `config/settings.toml` 的 `[auto]`，
   不再写死在代码里；写坏或越界要有兜底，`max_tries=0` 表示不限。
2) `_session_end_reason`：把子进程 `session-end` 自报的停止原因抄进 auto-error，
   不再用"多为窗口外或额度已满"这类猜测。
3) `_backoff_minutes`：退避指数递增 + 随机抖动 + 封顶（2026-10-27 用户要求
   "可以设置随机，模拟人的行为"）。
4) `_failure_action`（Q19，2026-10-27）：额度/任务数优先级高于重试次数——
   打不开、网络抖这类可恢复失败只能「长退避挂起」，不能一碰次数就判今天收工。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from xxt.cli import (_auto_limits, _backoff_minutes, _epoch, _failure_action,
                     _load_retry_after, _persist_retry_after, _revive_plan,
                     _session_end_reason, _try_label)
from xxt.core.diagnose import actions_for, classify, is_suspendable, tier_for
from xxt.store.db import connect


class TestAutoLimits(unittest.TestCase):
    def test_defaults(self):
        # 默认：2 次 / 退避 15 分 / 回读 25 秒 / 抖动 40% / 封顶 90 分 / 硬上限 4 轮
        self.assertEqual(_auto_limits(None), (2, 15, 25, 40, 90, 4))
        self.assertEqual(_auto_limits({}), (2, 15, 25, 40, 90, 4))

    def test_override(self):
        cfg = {"max_tries": 3, "retry_minutes": 30, "confirm_seconds": 40,
               "retry_jitter_pct": 50, "retry_max_minutes": 120,
               "hard_tries_cap": 6}
        self.assertEqual(_auto_limits(cfg), (3, 30, 40, 50, 120, 6))

    def test_zero_tries_means_unlimited(self):
        self.assertEqual(_auto_limits({"max_tries": 0})[0], 0)

    def test_out_of_range_is_clamped(self):
        # 上限防手滑（10 次 / 240 分 / 300 秒 / 100% / 480 分 / 20 轮），下限防刷风控
        self.assertEqual(
            _auto_limits({"max_tries": 99, "retry_minutes": 0, "confirm_seconds": 5,
                          "retry_jitter_pct": 999, "retry_max_minutes": 5,
                          "hard_tries_cap": 999}),
            (10, 1, 15, 100, 15, 20),
        )
        self.assertEqual(
            _auto_limits({"max_tries": -3, "retry_minutes": 999, "confirm_seconds": 9999,
                          "retry_jitter_pct": -5, "retry_max_minutes": 9999,
                          "hard_tries_cap": 0}),
            (0, 240, 300, 0, 480, 1),
        )

    def test_bad_value_falls_back_to_default(self):
        cfg = {"max_tries": "abc", "retry_minutes": None, "confirm_seconds": "",
               "retry_jitter_pct": [], "retry_max_minutes": {}, "hard_tries_cap": []}
        self.assertEqual(_auto_limits(cfg), (2, 15, 25, 40, 90, 4))


class TestBackoffMinutes(unittest.TestCase):
    """退避：指数递增 + 抖动 + 封顶（纯本地计算，零平台请求）。"""

    def test_grows_exponentially_without_jitter(self):
        self.assertEqual(_backoff_minutes(15, 1, 0, 90), 15)
        self.assertEqual(_backoff_minutes(15, 2, 0, 90), 30)
        self.assertEqual(_backoff_minutes(15, 3, 0, 90), 60)

    def test_capped(self):
        self.assertEqual(_backoff_minutes(15, 4, 0, 90), 90)
        self.assertEqual(_backoff_minutes(60, 6, 0, 90), 90)

    def test_jitter_stays_within_band(self):
        # base=15, jitter=40% → 9~21 分钟；多抽几次都必须落在这个区间里
        for _ in range(200):
            v = _backoff_minutes(15, 1, 40, 90)
            self.assertGreaterEqual(v, 9)
            self.assertLessEqual(v, 21)

    def test_jitter_actually_varies(self):
        seen = {_backoff_minutes(30, 1, 40, 240) for _ in range(60)}
        self.assertGreater(len(seen), 1, "固定值等于没有随机")


class TestFailureAction(unittest.TestCase):
    """Q19（2026-10-27）：可恢复的失败不许一碰次数就收工。"""

    def test_suspendable_short_backoff_before_max_tries(self):
        action, wait = _failure_action("structure", 1, 3, 0, 4, 15, 0, 90)
        self.assertEqual(action, "suspend")
        self.assertEqual(wait, 15)

    def test_suspendable_goes_long_backoff_after_max_tries(self):
        # 次数用尽 ≠ 今天收工：转长退避（按封顶间隔），继续在当天窗口里找机会
        action, wait = _failure_action("structure", 3, 3, 0, 4, 15, 0, 90)
        self.assertEqual(action, "suspend")
        self.assertEqual(wait, 90)

    def test_hard_stop_only_after_hard_cap(self):
        action, wait = _failure_action("structure", 5, 3, 4, 4, 15, 0, 90)
        self.assertEqual((action, wait), ("hard_stop", 0))

    def test_unlimited_tries_never_hard_stops_early(self):
        self.assertEqual(_failure_action("network", 9, 0, 0, 4, 15, 0, 90)[0],
                         "suspend")

    def test_unknown_is_suspendable(self):
        # 归类不出原因时宁可退避再试，也不能误判“今天没戏”
        self.assertEqual(_failure_action("unknown", 1, 2, 0, 4, 15, 0, 90)[0],
                         "suspend")

    def test_needs_human_or_done_is_hard_stop(self):
        for code in ("login", "quota", "done", "window"):
            self.assertEqual(_failure_action(code, 1, 2, 0, 4, 15, 0, 90),
                             ("hard_stop", 0), code)


class TestClassify(unittest.TestCase):
    """停止原因 → 类别 → 档位（子进程实际写出的那些原话）。"""

    def test_real_stop_reasons(self):
        cases = {
            "知行合一｜找不到下一节按钮｜视频 0 个": "structure",
            "连续 3 节都打不开任务页（1.1、1.2、1.3）": "structure",
            "时长额度达标（本会话视频 105 分钟）": "quota",
            "任务数额度达标（今日已完成 57 个）": "quota",
            "超出每日学习时段": "window",
            "打不开章节树（登录态可能已失效，或课程链接过期）——请重新登录该账号": "login",
            "无待学章节": "done",
            "net::ERR_TIMED_OUT": "network",
            "出现验证码，请稍后再试": "risk",
            # 17:17:15 #1 的原话（事件管道失效自保退出），以前落到“未归类”
            "知行合一｜异常中止：事件管道失效，中止会话防止盲切快进｜视频 18 个": "crash",
            "": "unknown",
        }
        for text, want in cases.items():
            self.assertEqual(classify(text), want, text)

    def test_precise_cause_wins_over_generic_crash(self):
        """「异常终止 + net::」同句时必须是网络，不能被 crash 抢走（17:17 #2 实况）。"""
        self.assertEqual(
            classify("知行合一｜异常终止：Error('Page.goto: net::ERR_NETWORK_IO_SUSPENDED'),")
            , "network")

    def test_crash_is_suspendable_and_offers_rerun(self):
        self.assertTrue(is_suspendable("crash"))
        self.assertEqual(tier_for("crash"), "suspended")
        self.assertIn("立即补跑", actions_for("crash"))

    def test_tier_mapping(self):
        self.assertEqual(tier_for("quota"), "achieved")
        self.assertEqual(tier_for("done"), "achieved")
        self.assertEqual(tier_for("login"), "needs_you")
        self.assertEqual(tier_for("structure"), "suspended")
        self.assertEqual(tier_for("window"), "hard_stop")


class TestSessionEndReason(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.tmp.name) / "t.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _seed(self, ts: str, event: str, detail: str, aid: int = 1) -> None:
        self.conn.execute(
            "INSERT INTO run_logs(ts, level, event, detail, account_id) VALUES(?,?,?,?,?)",
            (ts, "INFO", event, detail, aid),
        )
        self.conn.commit()

    def test_takes_last_two_segments(self):
        self._seed("2026-10-27T09:13:59", "session-end",
                   "知行合一，做最好的自己｜找不到下一节按钮｜视频 0 个")
        got = _session_end_reason(self.conn, 1, "2026-10-27T09:10:00")
        self.assertEqual(got, "找不到下一节按钮｜视频 0 个")

    def test_short_detail_kept_whole(self):
        self._seed("2026-10-27T09:13:59", "session-end", "打不开章节树")
        got = _session_end_reason(self.conn, 1, "2026-10-27T09:10:00")
        self.assertEqual(got, "打不开章节树")

    def test_ignores_earlier_session(self):
        # 触发之前那次的停止原因不能顶替本轮（否则会把昨天的原因贴到今天）
        self._seed("2026-10-27T08:00:00", "session-end", "旧课程｜额度已满｜视频 3 个")
        got = _session_end_reason(self.conn, 1, "2026-10-27T09:10:00")
        self.assertEqual(got, "")

    def test_other_account_and_event_ignored(self):
        self._seed("2026-10-27T09:13:59", "session-end", "别人｜停止｜视频 1 个", aid=2)
        self._seed("2026-10-27T09:13:59", "video-done", "本号｜某节｜470s", aid=1)
        self.assertEqual(_session_end_reason(self.conn, 1, "2026-10-27T09:10:00"), "")

    def test_null_detail_is_empty(self):
        self._seed("2026-10-27T09:13:59", "session-end", None)
        self.assertEqual(_session_end_reason(self.conn, 1, "2026-10-27T09:10:00"), "")


class TestRevivePlan(unittest.TestCase):
    """已 fired 但会话早退 → 要不要收回“今日已学”并补跑（2026-10-27 补漏）。"""

    END = "2026-10-27T09:13:59"

    def test_had_videos_keeps_fired(self):
        # 今天真刷过（额度/超窗/做完而退）→ 不能收回
        self.assertEqual(
            _revive_plan(_epoch(self.END) + 3600, self.END, 5, 1, 1, 2, 15), (False, 0))

    def test_not_started_by_guard_is_ignored(self):
        # 面板 ▶ / 手动启的会话失败，不代用户补跑（无 auto-try）
        self.assertEqual(
            _revive_plan(_epoch(self.END) + 3600, self.END, 0, 0, 0, 2, 15), (False, 0))

    def test_tries_exhausted_keeps_fired(self):
        self.assertEqual(
            _revive_plan(_epoch(self.END) + 3600, self.END, 0, 2, 2, 2, 15), (False, 0))

    def test_unlimited_tries_allows_revive(self):
        self.assertEqual(
            _revive_plan(_epoch(self.END) + 3600, self.END, 0, 0, 1, 0, 15), (True, 0))

    def test_long_gap_revives_immediately(self):
        # #1 今早 09:13 退出、10:50 才巡检到 → 不必再空等 15 分钟
        self.assertEqual(
            _revive_plan(_epoch(self.END) + 97 * 60, self.END, 0, 1, 1, 2, 15), (True, 0))

    def test_exact_retry_window_revives_immediately(self):
        self.assertEqual(
            _revive_plan(_epoch(self.END) + 15 * 60, self.END, 0, 1, 1, 2, 15), (True, 0))

    def test_recent_exit_waits_remaining_minutes(self):
        # 刚退出 3 分钟 → 还要等 12 分钟，避免连续访问
        self.assertEqual(
            _revive_plan(_epoch(self.END) + 3 * 60, self.END, 0, 1, 1, 2, 15), (True, 12))

    def test_missing_session_end_revives_immediately(self):
        # 进程崩了、没写 session-end：当作已隔够久
        self.assertEqual(_revive_plan(_epoch(self.END), "", 0, 1, 1, 2, 15), (True, 0))

    def test_force_revives_even_after_progress(self):
        # Q19：今天学过 5 个但被“打不开”掉停 → 未达标就该接着补
        self.assertEqual(
            _revive_plan(_epoch(self.END) + 3600, self.END, 5, 1, 1, 2, 15, force=True),
            (True, 0))

    def test_force_still_respects_cap(self):
        # force 不是无上限：次数用尽必须停，否则就是死循环刷平台
        self.assertEqual(
            _revive_plan(_epoch(self.END) + 3600, self.END, 5, 4, 4, 4, 15, force=True),
            (False, 0))


class TestTryLabel(unittest.TestCase):
    """发起次数说法：不能出现“第 4/3 次”这种分母比序号小的字（用户读不懂）。"""

    def test_within_short_backoff(self):
        self.assertEqual(_try_label(2, 3, 4), "第 2/3 次发起")

    def test_exact_cap_is_still_fraction(self):
        self.assertEqual(_try_label(3, 3, 4), "第 3/3 次发起")

    def test_over_cap_explains_long_backoff(self):
        got = _try_label(4, 3, 4)
        self.assertNotIn("4/3", got)
        self.assertIn("短退避 3 次已用尽", got)
        self.assertIn("硬上限 4 轮", got)

    def test_unlimited_tries(self):
        self.assertEqual(_try_label(7, 0, 4), "第 7/不限 次发起")


class TestRetryAfterPersistence(unittest.TestCase):
    """退避时刻表落库（守护重启不能把“正在退避”忘掉，2026-10-27）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "t.db"
        self.conn = connect(self.db)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_roundtrip(self):
        _persist_retry_after(self.db, "2026-10-27", {1: "17:32", 2: "18:05"})
        self.assertEqual(_load_retry_after(self.db, "2026-10-27"),
                         {1: "17:32", 2: "18:05"})

    def test_empty_mapping_clears(self):
        _persist_retry_after(self.db, "2026-10-27", {1: "17:32"})
        _persist_retry_after(self.db, "2026-10-27", {})
        self.assertEqual(_load_retry_after(self.db, "2026-10-27"), {})

    def test_zero_wait_is_not_persisted(self):
        # wait_m=0 时写的是空串：“下一轮即恢复”不该被当成退避时刻存下来
        _persist_retry_after(self.db, "2026-10-27", {1: ""})
        self.assertEqual(_load_retry_after(self.db, "2026-10-27"), {})

    def test_other_day_is_discarded(self):
        # 0 点后全部重算（用户 2026-10-27 明确要求）
        _persist_retry_after(self.db, "2026-10-27", {1: "17:32"})
        self.assertEqual(_load_retry_after(self.db, "2026-10-28"), {})


if __name__ == "__main__":
    unittest.main()
