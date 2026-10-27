"""守护「今日是否已学过」判定（2026-10-27 方案 A）。

背景：旧逻辑只要写过 `auto-fire` 就算今天已触发，哪怕子进程 30 秒后秒退——
08:11 的 #2 与 01:40 的 #1 就是这样白掉一整天的机会，且重启守护也补不回来
（fired 从 run_logs 恢复）。现在 `auto-fire` 只在回读确认会话真在跑时才写。

2026-10-27 追加：`_revive_if_dead` 撤销“今日已学”原本也写 `auto-error`，
用户看到日志问“为什么是 auto-error”——已拆为 `auto-revoke`。
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

    def test_auto_revoke_rolls_back_fired(self):
        """`auto-revoke` = 撤回“今日已学”（会话早退、今日 0 个视频）。

        2026-10-27 用户看到日志写“auto-error”很困惑：那条其实不是新一轮失败，
        而是撤销。改为独立事件名后，无论隔多久都必须被判为“今日未学”。
        """
        ok, tries = _replay_auto_events([
            (T.format("14:03:56"), "auto-try"),
            (T.format("14:04:20"), "auto-fire"),
            (T.format("16:30:00"), "auto-revoke"),  # 隔了 2 小时也要撤销
        ])
        self.assertFalse(ok)
        self.assertEqual(tries, 1)

    def test_revoke_then_retry_can_still_succeed(self):
        ok, tries = _replay_auto_events([
            (T.format("09:10:00"), "auto-try"),
            (T.format("09:10:43"), "auto-fire"),
            (T.format("09:14:00"), "auto-revoke"),
            (T.format("14:10:00"), "auto-try"),
            (T.format("14:10:40"), "auto-fire"),
        ])
        self.assertTrue(ok)
        self.assertEqual(tries, 2)

    def test_auto_suspend_rolls_back_like_revoke(self):
        """`auto-suspend` = 挂起（打不开/网络/风控）→ 退避后自动恢复。

        Q19（2026-10-27）：可恢复的失败不能算“今日已学”，否则一整天不再补跑。
        """
        ok, tries = _replay_auto_events([
            (T.format("10:00:00"), "auto-try"),
            (T.format("10:00:30"), "auto-fire"),
            (T.format("10:20:00"), "auto-suspend"),
        ])
        self.assertFalse(ok)
        self.assertEqual(tries, 1)

    def test_auto_done_counts_as_finished(self):
        """`auto-done` = 今日目标达成（额度/任务数达标）→ 重启守护不能再刷一次。"""
        ok, tries = _replay_auto_events([
            (T.format("10:00:00"), "auto-try"),
            (T.format("10:00:30"), "auto-fire"),
            (T.format("15:00:00"), "auto-done"),
        ])
        self.assertTrue(ok)
        self.assertEqual(tries, 1)

    def test_auto_hardstop_blocks_restart(self):
        """`auto-hardstop` = 硬停（次数/退避用尽）→ 重启守护不能再撞一次。

        2026-10-28（B）：硬停以前借用 `auto-error`，而回放只认“5 分钟内紧跟
        auto-fire”才算失败——秒退型硬停重启后会被重新发起。
        """
        ok, tries = _replay_auto_events([
            (T.format("09:16:00"), "auto-try"),
            (T.format("09:16:30"), "auto-fire"),
            (T.format("09:16:50"), "auto-hardstop"),   # 秒退型硬停（<300 秒）
        ])
        self.assertTrue(ok)
        self.assertEqual(tries, 1)

    def test_hardstop_then_manual_try_starts_a_new_round(self):
        # 硬停后用户手动又开一轮：以最后一轮为准（不能被旧的硬停锁住）
        ok, _tries = _replay_auto_events([
            (T.format("09:16:00"), "auto-try"),
            (T.format("09:16:50"), "auto-hardstop"),
            (T.format("15:00:00"), "auto-try"),
        ])
        self.assertFalse(ok)

    def test_done_then_try_starts_a_new_round(self):
        # 达成后又手动开了一轮：以最后一轮为准（不能被旧的 auto-done 锁住）
        ok, _tries = _replay_auto_events([
            (T.format("10:00:00"), "auto-try"),
            (T.format("15:00:00"), "auto-done"),
            (T.format("16:00:00"), "auto-try"),
        ])
        self.assertFalse(ok)

    def test_no_events(self):
        self.assertEqual(_replay_auto_events([]), (False, 0))


if __name__ == "__main__":
    unittest.main()
