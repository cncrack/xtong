"""课程轮换策略测试（合成数据）。"""

from __future__ import annotations

import random
import sqlite3
import tempfile
import unittest
from pathlib import Path

from xxt.core.rotation import course_weights, pick_courses
from xxt.store.db import connect


def seed_courses(conn: sqlite3.Connection) -> None:
    """两门课：A（10 章 5 待学，优先级 3）、B（4 章 4 待学，默认优先级 1）。

    两门都 `auto_grind=1`（已纳入自动刷）——v10 后轮转只认显式纳入的课，
    夹具默认纳入才能继续测权重/挑选逻辑。
    """
    conn.execute("INSERT INTO courses(account_id, platform_course_id, name, auto_grind) "
                 "VALUES(1,'ca','课程甲',1)")
    conn.execute("INSERT INTO courses(account_id, platform_course_id, name, auto_grind) "
                 "VALUES(1,'cb','课程乙',1)")
    conn.commit()
    ca = conn.execute("SELECT id FROM courses WHERE name='课程甲'").fetchone()["id"]
    cb = conn.execute("SELECT id FROM courses WHERE name='课程乙'").fetchone()["id"]
    for i in range(10):
        conn.execute("INSERT INTO chapters(course_id, platform_chapter_id, name, idx, status) "
                     "VALUES(?,?,?,?,?)", (ca, f"a{i}", f"甲{i}", i, "done" if i < 5 else "pending"))
    for i in range(4):
        conn.execute("INSERT INTO chapters(course_id, platform_chapter_id, name, idx, status) "
                     "VALUES(?,?,?,?,?)", (cb, f"b{i}", f"乙{i}", i, "pending"))
    conn.commit()


class TestRotation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.tmp.name) / "t.db")
        seed_courses(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_weights_skip_finished_and_empty(self):
        w = course_weights(self.conn, 1, {})
        names = {r["name"] for _, r in w}
        self.assertEqual(names, {"课程甲", "课程乙"})
        wa = next(wt for wt, r in w if r["name"] == "课程甲")
        wb = next(wt for wt, r in w if r["name"] == "课程乙")
        self.assertAlmostEqual(wa, 0.5)   # 优先级1 × 5/10
        self.assertAlmostEqual(wb, 1.0)   # 优先级1 × 4/4

    def test_priority_multiplies_weight(self):
        ca = self.conn.execute("SELECT id FROM courses WHERE name='课程甲'").fetchone()["id"]
        w = course_weights(self.conn, 1, {str(ca): 3.0})
        wa = next(wt for wt, r in w if r["name"] == "课程甲")
        self.assertAlmostEqual(wa, 1.5)   # 3.0 × 0.5

    def test_course_not_auto_grind_is_excluded(self):
        """v10 核心：未纳入自动刷（auto_grind=0）的课不得进轮转池——即使有树、有 pending。

        根治“抓了章节树就被守护偷刷”：抓树不再自动进池，必须显式开启。
        """
        self.conn.execute(
            "INSERT INTO courses(account_id, platform_course_id, name, auto_grind) "
            "VALUES(1,'cc','课程丙',0)")
        cc = self.conn.execute("SELECT id FROM courses WHERE name='课程丙'").fetchone()["id"]
        for i in range(6):  # 有树且全 pending，若不开关就会被轮转
            self.conn.execute(
                "INSERT INTO chapters(course_id, platform_chapter_id, name, idx, status) "
                "VALUES(?,?,?,?, 'pending')", (cc, f"c{i}", f"丙{i}", i))
        self.conn.commit()
        names = {r["name"] for _, r in course_weights(self.conn, 1, {})}
        self.assertNotIn("课程丙", names)          # 未纳入 → 排除
        self.assertIn("课程甲", names)               # 已纳入 → 保留
        # 显式纳入后立刻进池
        self.conn.execute("UPDATE courses SET auto_grind=1 WHERE name='课程丙'")
        self.conn.commit()
        names2 = {r["name"] for _, r in course_weights(self.conn, 1, {})}
        self.assertIn("课程丙", names2)

    def test_pick_returns_unique_rows(self):
        rotation = {"courses_per_day": [2, 2], "priorities": {}}
        picks = pick_courses(self.conn, random.Random(3), rotation, count=2)
        self.assertEqual(len(picks), 2)
        self.assertEqual(len({r["id"] for r in picks}), 2)

    def test_pick_count_capped_by_candidates(self):
        rotation = {"courses_per_day": [5, 5], "priorities": {}}
        picks = pick_courses(self.conn, random.Random(3), rotation)
        self.assertEqual(len(picks), 2)  # 只有 2 门候选


if __name__ == "__main__":
    unittest.main()
