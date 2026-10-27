"""章节树解析与节奏引擎测试（夹具均为合成数据）。"""

from __future__ import annotations

import random
import unittest
from datetime import datetime
from pathlib import Path

from xxt.adapters.chaoxing.chapters import parse_chapter_tree
from xxt.config import DEFAULTS
from xxt.core.pace import hm_waiting, sample_gap, sample_speed, within_daily_window

FIXTURE = Path(__file__).parent / "fixtures" / "chapter_tree_sample.html"


class TestParseChapterTree(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.leaves = parse_chapter_tree(FIXTURE.read_text(encoding="utf-8"))

    def test_leaf_count_and_order(self):
        self.assertEqual([l["platform_chapter_id"] for l in self.leaves],
                         ["900000001", "900000002", "900000003"])
        self.assertEqual([l["idx"] for l in self.leaves], [0, 1, 2])

    def test_names_contain_unit_and_number(self):
        self.assertIn("第一章 测试导论", self.leaves[0]["name"])
        self.assertIn("1.1", self.leaves[0]["name"])
        self.assertIn("测试导言", self.leaves[0]["name"])
        self.assertIn("第二章 测试实践", self.leaves[2]["name"])

    def test_platform_done_flag(self):
        self.assertTrue(self.leaves[0]["done_on_platform"])   # icon_yiwanc
        self.assertFalse(self.leaves[1]["done_on_platform"])
        self.assertEqual(self.leaves[2]["job_count"], 2)


class TestPace(unittest.TestCase):
    def setUp(self):
        self.rng = random.Random(42)
        self.pace = DEFAULTS["pace"]

    def test_gap_within_configured_tiers(self):
        tiers = [self.pace["gap_browse"], self.pace["gap_short"], self.pace["gap_away"]]
        for _ in range(200):
            gap = sample_gap(self.rng, self.pace)
            self.assertTrue(any(lo <= gap <= hi for lo, hi in tiers), gap)

    def test_speed_in_range(self):
        for _ in range(50):
            sp = sample_speed(self.rng, self.pace)
            lo, hi = self.pace["speed_range"]
            self.assertLessEqual(lo, sp)
            self.assertGreaterEqual(hi, sp)

    def test_daily_window(self):
        pace = dict(self.pace, daily_window=["08:00", "23:00"])
        self.assertTrue(within_daily_window(pace, datetime(2026, 1, 1, 12, 0)))
        self.assertFalse(within_daily_window(pace, datetime(2026, 1, 1, 23, 30)))
        cross = dict(self.pace, daily_window=["22:00", "06:00"])
        self.assertTrue(within_daily_window(cross, datetime(2026, 1, 1, 23, 0)))
        self.assertTrue(within_daily_window(cross, datetime(2026, 1, 1, 5, 0)))
        self.assertFalse(within_daily_window(cross, datetime(2026, 1, 1, 12, 0)))


class TestHmWaiting(unittest.TestCase):
    """退避时刻是否未到（2026-10-28 A：跨零点不能再用字符串比较）。"""

    def test_same_day_future_waits(self):
        self.assertTrue(hm_waiting("09:00", "09:16"))

    def test_same_day_passed_runs(self):
        self.assertFalse(hm_waiting("09:20", "09:16"))

    def test_exact_moment_runs(self):
        # 到点即可跑（旧语义：`now < target` 为假 → 不再等）
        self.assertFalse(hm_waiting("09:16", "09:16"))

    def test_across_midnight_waits(self):
        # 23:50 挂起 30 分钟 → 00:20；字符串比较会误判“已过”立刻重试
        self.assertTrue(hm_waiting("23:51", "00:20"))
        self.assertTrue(hm_waiting("23:59", "00:05"))

    def test_across_midnight_passed_runs(self):
        self.assertFalse(hm_waiting("00:25", "00:20"))
        self.assertFalse(hm_waiting("08:00", "00:20"))

    def test_twelve_hour_boundary(self):
        # 退避封顶 8 小时 < 12 小时，所以环形判据不会把真的已过当成未到
        self.assertTrue(hm_waiting("10:00", "22:00"))
        self.assertFalse(hm_waiting("10:00", "22:01"))

    def test_broken_value_runs(self):
        # 值坏了当已过：宁可重试，也不能永久卡在退避里
        self.assertFalse(hm_waiting("09:00", ""))
        self.assertFalse(hm_waiting("09:00", "abc"))


if __name__ == "__main__":
    unittest.main()
