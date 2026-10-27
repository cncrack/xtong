"""每日额度抽样与知识点标题匹配测试。"""

from __future__ import annotations

import random
import sqlite3
import tempfile
import unittest
from pathlib import Path

from xxt.adapters.chaoxing.study import match_chapter_id
from xxt.config import DEFAULTS
from xxt.core.quota import (
    budget_floor,
    parse_duration_seconds,
    reached_locally,
    remaining_minutes,
    sample_daily_budget,
    sum_done_seconds,
)
from xxt.store.db import connect


class TestQuota(unittest.TestCase):
    def test_budget_floats_within_ratio(self):
        rng = random.Random(7)
        quota = DEFAULTS["quota"]
        lo, hi = quota["float_ratio"]
        for _ in range(100):
            minutes, tasks = sample_daily_budget(rng, quota)
            self.assertGreaterEqual(minutes, quota["daily_minutes"] * lo - 1e-6)
            self.assertLessEqual(minutes, quota["daily_minutes"] * hi + 1e-6)
            self.assertGreaterEqual(tasks, int(quota["daily_tasks"] * lo))
            self.assertLessEqual(tasks, quota["daily_tasks"])

    def test_zero_means_unlimited(self):
        rng = random.Random(1)
        quota = dict(DEFAULTS["quota"], daily_minutes=0, daily_tasks=0)
        minutes, tasks = sample_daily_budget(rng, quota)
        self.assertEqual((minutes, tasks), (0.0, 0))


class TestQuotaFloor(unittest.TestCase):
    """额度**下限**判定（2026-10-28 P1：守护侧的“达成闸门”）。

    子会话每次启动都会重抽一次额度，所以“今天算不算学够”必须拿下限比；
    否则子进程报一句“额度达标”、本地其实差很多时，守护会直接钉死今天。
    """

    def test_floor_uses_ratio_low_bound(self):
        quota = {"daily_minutes": 240, "daily_tasks": 60, "float_ratio": [0.5, 1.0]}
        self.assertEqual(budget_floor(quota), (120, 30))

    def test_zero_means_unlimited(self):
        self.assertEqual(budget_floor({"daily_minutes": 0, "daily_tasks": 0,
                                       "float_ratio": [0.7, 1.0]}), (0, 0))
        self.assertTrue(reached_locally({"daily_minutes": 0, "daily_tasks": 0},
                                        0, 0))

    def test_reached_by_tasks_or_minutes(self):
        quota = {"daily_minutes": 100, "daily_tasks": 30, "float_ratio": [0.5, 1.0]}
        # 下限：15 个 / 50 分钟（都是 base × 浮动下限）
        self.assertEqual(budget_floor(quota), (50, 15))
        self.assertFalse(reached_locally(quota, 14, 0))
        self.assertTrue(reached_locally(quota, 15, 0))          # 任务数达标
        self.assertTrue(reached_locally(quota, 0, 50 * 60))     # 时长达标
        self.assertFalse(reached_locally(quota, 1, 49 * 60))

    def test_bad_ratio_falls_back(self):
        """老配置缺 float_ratio 时按 1.0 算，不能直接抛异常。"""
        self.assertEqual(budget_floor({"daily_minutes": 10, "daily_tasks": 4}),
                         (10, 4))


class TestDurationQuota(unittest.TestCase):
    """时长额度跨会话累计（2026-10-25 修正：不再用进程内计时）。"""

    def test_parse_duration_from_video_done_detail(self):
        self.assertEqual(parse_duration_seconds("课程A｜知识点1｜571s"), 571)
        self.assertEqual(parse_duration_seconds("课｜2s 速成｜480s"), 480)  # 标题带 s 不误判
        self.assertEqual(parse_duration_seconds("机制上线前的旧记录"), 0)
        self.assertEqual(parse_duration_seconds(""), 0)

    def test_sum_and_remaining_across_sessions(self):
        details = ["课｜a｜600s", "课｜b｜300s", "课｜旧记录"]
        self.assertEqual(sum_done_seconds(details), 900)  # 15 分钟
        # 额度 30 分钟、今日已学 15 分钟 → 本会话还剩 15 分钟
        self.assertAlmostEqual(remaining_minutes(30, 900), 15.0)
        # 今日已学超过额度 → 拒绝启动
        self.assertLessEqual(remaining_minutes(10, 900), 0)
        # 0 表示不限：由调用方判断，函数返回 0
        self.assertEqual(remaining_minutes(0, 900), 0.0)


class TestMatchChapter(unittest.TestCase):
    def test_fuzzy_match_by_title(self):
        with tempfile.TemporaryDirectory() as td:
            conn = connect(Path(td) / "t.db")
            try:
                conn.execute(
                    "INSERT INTO courses(account_id, platform_course_id, name) "
                    "VALUES(1, 'c1', '测试课程')"
                )
                conn.commit()
                conn.execute(
                    "INSERT INTO chapters(course_id, platform_chapter_id, name, idx) "
                    "VALUES(1, 'k1', '第一章｜2.1 尽快适应大学生活（上）', 0)"
                )
                conn.commit()
                cid = match_chapter_id(conn, 1, "尽快适应大学生活（上）")
                self.assertIsNotNone(cid)
                self.assertIsNone(match_chapter_id(conn, 1, "不存在的标题"))
                self.assertIsNone(match_chapter_id(conn, 999, "尽快适应大学生活（上）"))
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
