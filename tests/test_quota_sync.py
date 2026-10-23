"""每日额度抽样与知识点标题匹配测试。"""

from __future__ import annotations

import random
import sqlite3
import tempfile
import unittest
from pathlib import Path

from xxt.adapters.chaoxing.study import match_chapter_id
from xxt.config import DEFAULTS
from xxt.core.quota import sample_daily_budget
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
