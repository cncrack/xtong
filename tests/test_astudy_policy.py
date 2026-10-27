"""节级策略测试（2026-10-27 B1/B2 修复的回归网）。

覆盖三件曾在实况里出错、且都不需要真实平台就能验证的事：
1. 选下一节必须“从当前节之后”往后取，不能取全表第一个 pending（B2）
2. “没学过/没完成”的节保持 pending，不能被当成 visited 跳过
3. 尝试次数来自 [learn] 配置并有安全钳制
"""

from __future__ import annotations

import sqlite3
import unittest

from xxt.adapters.chaoxing.astudy import (
    _learn_int,
    is_non_video_name,
    non_video_marks,
    pick_pending_after,
    quota_reached,
)


def _db(rows: list[tuple[int, str, str, str]]) -> sqlite3.Connection:
    """rows: (idx, platform_chapter_id, name, status) —— 合成数据，不碰真实库。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE chapters(id INTEGER PRIMARY KEY, course_id INT,"
                 " platform_chapter_id TEXT, name TEXT, idx INT, status TEXT)")
    for idx, pid, name, status in rows:
        conn.execute("INSERT INTO chapters(course_id, platform_chapter_id, name, idx, status)"
                     " VALUES(1,?,?,?,?)", (pid, name, idx, status))
    conn.commit()
    return conn


class TestPickPendingAfter(unittest.TestCase):
    def setUp(self):
        # 模拟 #1 的现场：5.1 播了 441/551s 仍未完成 → 保持 pending
        self.conn = _db([
            (0, "p0", "1.1 已学完", "done"),
            (1, "p1", "1.2 待学（在前）", "pending"),
            (2, "p2", "2.1 当前节", "pending"),
            (3, "p3", "2.2 下一节", "pending"),
            (4, "p4", "2.3 再下一节", "pending"),
        ])

    def test_returns_next_after_current_not_first(self):
        """回归 B2：当前节 p2 仍是 pending，旧的 LIMIT 1 会返回 p1（或 p2 自己）。"""
        got = pick_pending_after(self.conn, 1, "p2", tried=[])
        self.assertEqual(got, ("p3", "2.2 下一节"))

    def test_wraps_to_head_when_at_tail(self):
        got = pick_pending_after(self.conn, 1, "p4", tried=[])
        self.assertEqual(got, ("p1", "1.2 待学（在前）"))

    def test_skips_tried_within_session(self):
        got = pick_pending_after(self.conn, 1, "p2", tried=["p3"])
        self.assertEqual(got[0], "p4")

    def test_none_when_all_pending_tried(self):
        self.assertIsNone(pick_pending_after(self.conn, 1, "p2", tried=["p1", "p2", "p3", "p4"]))

    def test_done_chapters_never_selected(self):
        for after in ("p0", "p1", "p2", "p3", "p4", None):
            got = pick_pending_after(self.conn, 1, after, tried=[])
            self.assertIsNotNone(got)
            self.assertNotEqual(got[0], "p0")


class TestNonVideoName(unittest.TestCase):
    def test_job_nodes_are_non_video(self):
        for name in ("第一章｜2.12 作业", "第三章 讨论", "X 测验", "期末考试", "阅读材料"):
            self.assertTrue(is_non_video_name(name), name)

    def test_video_sections_are_video(self):
        for name in ("第四章｜5.1 改变学习方法（上）", "第三章｜4.5 增强自我 信心（上）"):
            self.assertFalse(is_non_video_name(name), name)

    def test_empty(self):
        self.assertFalse(is_non_video_name(""))
        self.assertFalse(is_non_video_name(None))


class TestPartialRatio(unittest.TestCase):
    """B3：只有“真的播了大半”才值得重载续播，否则白白多跑一次真实访问。"""

    def _mk(self, dur, cur):
        from xxt.adapters.chaoxing.astudy import AsyncStudyRunner

        obj = object.__new__(AsyncStudyRunner)  # 不跑 __init__，只测纯计算
        obj._last_video_dur = dur
        obj._last_progress = cur
        return obj._partial_ratio()

    def test_typical_interruption_80_percent(self):
        # 实况：#1 在 441/551s 被当“结束”，已播 80% → 应该续播
        self.assertAlmostEqual(self._mk(551, 441), 0.80, places=2)

    def test_zero_duration_is_zero(self):
        self.assertEqual(self._mk(0, 0), 0.0)

    def test_just_started_is_small(self):
        self.assertLess(self._mk(551, 30), 0.1)


class TestLearnInt(unittest.TestCase):
    class S:
        def __init__(self, learn):
            self.learn = learn

    def test_reads_config(self):
        self.assertEqual(_learn_int(self.S({"enter_retries": 4}), "enter_retries", 2, 1, 5), 4)

    def test_clamps_to_range(self):
        self.assertEqual(_learn_int(self.S({"enter_retries": 99}), "enter_retries", 2, 1, 5), 5)
        self.assertEqual(_learn_int(self.S({"enter_retries": 0}), "enter_retries", 2, 1, 5), 1)

    def test_bad_value_falls_back(self):
        self.assertEqual(_learn_int(self.S({"max_skip_chapters": "abc"}),
                                    "max_skip_chapters", 3, 1, 10), 3)
        self.assertEqual(_learn_int(self.S({}), "max_skip_chapters", 3, 1, 10), 3)


class TestQuotaReached(unittest.TestCase):
    """额度口径（2026-10-27 用户拍板）：只算看过的视频，不算拟人间隔。"""

    def test_counts_video_seconds_only(self):
        # 174 分钟额度：墙钟再长也不算，视频秒数够了才算
        self.assertFalse(quota_reached(0, 174))
        self.assertFalse(quota_reached(173 * 60, 174))
        self.assertTrue(quota_reached(174 * 60, 174))

    def test_zero_or_none_budget_means_unlimited(self):
        self.assertFalse(quota_reached(10 ** 6, 0))
        self.assertFalse(quota_reached(10 ** 6, None))

    def test_wallclock_does_not_leak_in(self):
        # 回归点：旧口径用墙钟，186 分钟墙钟 + 105 分钟视频会被判“达标”
        self.assertFalse(quota_reached(105 * 60, 174))


class TestNonVideoMarks(unittest.TestCase):
    def test_default_when_unset(self):
        class S:
            learn = {}

        marks = non_video_marks(S())
        self.assertIn("作业", marks)
        self.assertTrue(is_non_video_name("第一章｜2.12 作业", marks))
        self.assertFalse(is_non_video_name("5.1 改变学习方法（上）", marks))

    def test_override_by_config(self):
        class S:
            learn = {"non_video_marks": "小组任务,直播"}

        marks = non_video_marks(S())
        self.assertEqual(marks, ("小组任务", "直播"))
        self.assertTrue(is_non_video_name("第三章｜小组任务", marks))
        self.assertFalse(is_non_video_name("第三章｜作业", marks))  # 覆盖后旧词不再生效

    def test_bad_value_falls_back_to_default(self):
        class S:
            learn = {"non_video_marks": "   "}

        self.assertEqual(non_video_marks(S()), non_video_marks(None))


if __name__ == "__main__":
    unittest.main()
