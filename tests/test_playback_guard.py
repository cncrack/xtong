"""播放完成可信判定（2026-10-25 账号#2 假 video-done 事故回归防护）。

事故：点"下一节"后 0 秒就报 495/495s，写了一条 `正确认识自我（下）｜494s`——
那个 494s 其实是上一节的时长。原因：watcher 每 3s 发 tick，点"下一节"后旧文档仍在发，
拟人间隔期间残留在事件队列里，新一轮一 drain 就当成新视频播完了。
"""

from __future__ import annotations

import unittest

from xxt.core.playback import is_plausible_completion


class TestPlausibleCompletion(unittest.TestCase):
    def test_stale_player_read_is_rejected(self):
        # 事故现场：注入后 0.0 秒就说 495/495s（上一节的时长），必须拒
        self.assertFalse(is_plausible_completion(
            saw_progress=False, wall_seconds=0.0, duration=495, rate=1.17))

    def test_real_progress_is_trusted(self):
        # 见过 cur < dur-5 的进度 → 真的在播，哪怕耗时短也认
        self.assertTrue(is_plausible_completion(
            saw_progress=True, wall_seconds=1.0, duration=495, rate=1.17))

    def test_resume_with_matching_elapsed_is_trusted(self):
        # 没见到进度（一进来就接近末尾），但耗时与"时长/倍速"匹配 → 续播，可信
        # 30s 视频、倍速 1.0 → 门槛 min(30*0.5, 20) = 15s
        self.assertTrue(is_plausible_completion(
            saw_progress=False, wall_seconds=16.0, duration=30, rate=1.0))
        # 12s 就宣告完成 → 对 30s 视频来说太快，拒绝（宁可走"平台已完成"分支不记账）
        self.assertFalse(is_plausible_completion(
            saw_progress=False, wall_seconds=12.0, duration=30, rate=1.0))

    def test_long_video_gate_is_capped(self):
        # 长视频不必等满一半：门槛封顶 20 秒
        self.assertTrue(is_plausible_completion(
            saw_progress=False, wall_seconds=21.0, duration=3000, rate=1.0))
        self.assertFalse(is_plausible_completion(
            saw_progress=False, wall_seconds=19.0, duration=3000, rate=1.0))

    def test_unknown_metadata_does_not_block(self):
        # 时长/倍速缺失时信息不足，不阻断正常流程
        self.assertTrue(is_plausible_completion(
            saw_progress=False, wall_seconds=0.0, duration=0, rate=1.0))
        self.assertTrue(is_plausible_completion(
            saw_progress=False, wall_seconds=0.0, duration=300, rate=0))


if __name__ == "__main__":
    unittest.main()
