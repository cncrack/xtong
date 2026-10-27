"""守护「失败处理」两支（2026-10-27 P0-3d）。

1) `_auto_limits`：次数/退避/回读等待来自 `config/settings.toml` 的 `[auto]`，
   不再写死在代码里；写坏或越界要有兜底，`max_tries=0` 表示不限。
2) `_session_end_reason`：把子进程 `session-end` 自报的停止原因抄进 auto-error，
   不再用"多为窗口外或额度已满"这类猜测。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from xxt.cli import _auto_limits, _epoch, _revive_plan, _session_end_reason
from xxt.store.db import connect


class TestAutoLimits(unittest.TestCase):
    def test_defaults(self):
        # 未配置 [auto] 时用内置默认：每日 2 次 / 退避 15 分钟 / 回读 25 秒
        self.assertEqual(_auto_limits(None), (2, 15, 25))
        self.assertEqual(_auto_limits({}), (2, 15, 25))

    def test_override(self):
        cfg = {"max_tries": 3, "retry_minutes": 30, "confirm_seconds": 40}
        self.assertEqual(_auto_limits(cfg), (3, 30, 40))

    def test_zero_tries_means_unlimited(self):
        self.assertEqual(_auto_limits({"max_tries": 0})[0], 0)

    def test_out_of_range_is_clamped(self):
        # 上限防手滑（10 次 / 240 分钟 / 300 秒），下限防刷风控（1 分钟 / 15 秒）
        self.assertEqual(
            _auto_limits({"max_tries": 99, "retry_minutes": 0, "confirm_seconds": 5}),
            (10, 1, 15),
        )
        self.assertEqual(
            _auto_limits({"max_tries": -3, "retry_minutes": 999, "confirm_seconds": 9999}),
            (0, 240, 300),
        )

    def test_bad_value_falls_back_to_default(self):
        cfg = {"max_tries": "abc", "retry_minutes": None, "confirm_seconds": ""}
        self.assertEqual(_auto_limits(cfg), (2, 15, 25))


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


if __name__ == "__main__":
    unittest.main()
