"""守护「今日是否已学过」判定（2026-10-27 方案 A）。

背景：旧逻辑只要写过 `auto-fire` 就算今天已触发，哪怕子进程 30 秒后秒退——
08:11 的 #2 与 01:40 的 #1 就是这样白掉一整天的机会，且重启守护也补不回来
（fired 从 run_logs 恢复）。现在 `auto-fire` 只在回读确认会话真在跑时才写。
"""

from __future__ import annotations

import unittest

from xxt.cli import _replay_auto_events

T = "2026-10-27T{0}"


class TestReplayAutoEvents(unittest.TestCase):
    def test_new_flow_success(self):
        ok, tries = _replay_auto_events([
            (T.format("08:11:14"), "auto-try"),
            (T.format("08:11:50"), "auto-fire"),
        ])
        self.assertTrue(ok)
        self.assertEqual(tries, 1)

    def test_new_flow_failed_then_retry_success(self):
        ok, tries = _replay_auto_events([
            (T.format("08:11:14"), "auto-try"),
            (T.format("08:11:44"), "auto-error"),
            (T.format("08:30:00"), "auto-try"),
            (T.format("08:30:40"), "auto-fire"),
        ])
        self.assertTrue(ok)
        self.assertEqual(tries, 2)

    def test_new_flow_all_failed(self):
        ok, tries = _replay_auto_events([
            (T.format("08:11:14"), "auto-try"),
            (T.format("08:11:44"), "auto-error"),
            (T.format("08:30:00"), "auto-try"),
            (T.format("08:30:30"), "auto-error"),
        ])
        self.assertFalse(ok)  # 全部失败 → 允许下次启动再排
        self.assertEqual(tries, 2)

    def test_legacy_fire_with_error_is_not_success(self):
        """老数据没有 auto-try：秒退的那条 auto-fire 不能被当成“今天已学”。"""
        ok, tries = _replay_auto_events([
            (T.format("01:40:12"), "auto-fire"),
            (T.format("01:40:42"), "auto-error"),
        ])
        self.assertFalse(ok)
        self.assertEqual(tries, 1)

    def test_legacy_fire_alone_is_success(self):
        ok, tries = _replay_auto_events([(T.format("08:11:14"), "auto-fire")])
        self.assertTrue(ok)
        self.assertEqual(tries, 1)

    def test_no_events(self):
        self.assertEqual(_replay_auto_events([]), (False, 0))


if __name__ == "__main__":
    unittest.main()
