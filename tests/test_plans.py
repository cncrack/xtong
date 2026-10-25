"""批次（plan）测试：统一排课、账号冲突、与手动启动的关系（不触网、不拉进程）。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from xxt.core.plans import (busy_accounts, course_for_account, create_plan,
                            delete_plan, list_plans, set_enabled)
from xxt.store.db import connect


class TestPlans(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.tmp.name) / "t.db")
        self.conn.execute("DELETE FROM accounts")  # connect() 会种一个 default 账号
        for aid, label in ((1, "甲"), (2, "乙"), (3, "丙")):
            self.conn.execute(
                "INSERT INTO accounts(id, label, status, created_at, priority)"
                " VALUES(?, ?, 'active', datetime('now'), 1.0)", (aid, label))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_create_and_lookup(self):
        r = create_plan(self.conn, "十月数据结构", "数据结构", [1, 2])
        self.assertTrue(r["ok"])
        self.assertEqual(course_for_account(self.conn, 1), "数据结构")
        self.assertEqual(course_for_account(self.conn, 2), "数据结构")
        self.assertIsNone(course_for_account(self.conn, 3))  # 未排批次的账号走轮转
        plans = list_plans(self.conn)
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]["account_ids"], [1, 2])
        self.assertTrue(plans[0]["enabled"])

    def test_reject_empty_name_or_accounts(self):
        self.assertFalse(create_plan(self.conn, "", "数据结构", [1])["ok"])
        self.assertFalse(create_plan(self.conn, "批次", "", [1])["ok"])
        self.assertFalse(create_plan(self.conn, "批次", "数据结构", [])["ok"])
        self.assertFalse(create_plan(self.conn, "批次", "数据结构", [99])["ok"])

    def test_same_account_cannot_be_in_two_enabled_plans(self):
        """同一账号同时挂两个启用批次 → "今天刷哪门课"没有确定答案，必须拒绝。"""
        self.assertTrue(create_plan(self.conn, "A", "数据结构", [1])["ok"])
        r = create_plan(self.conn, "B", "操作系统", [1, 2])
        self.assertFalse(r["ok"])
        self.assertIn("已在其他启用中的批次里", r["error"])
        # 不带冲突账号则允许
        self.assertTrue(create_plan(self.conn, "B", "操作系统", [2])["ok"])
        self.assertEqual(busy_accounts(self.conn), {1: "A", 2: "B"})

    def test_disable_switches_course_back_to_rotation(self):
        r = create_plan(self.conn, "A", "数据结构", [1])
        pid = r["id"]
        self.assertTrue(set_enabled(self.conn, pid, False)["ok"])
        self.assertIsNone(course_for_account(self.conn, 1))
        # 停用后账号可被别的批次占用
        self.assertTrue(create_plan(self.conn, "B", "操作系统", [1])["ok"])

    def test_enable_checks_conflict(self):
        a = create_plan(self.conn, "A", "数据结构", [1])["id"]
        set_enabled(self.conn, a, False)
        b = create_plan(self.conn, "B", "操作系统", [1])["id"]
        r = set_enabled(self.conn, a, True)
        self.assertFalse(r["ok"])
        self.assertIn("无法启用", r["error"])
        self.assertEqual(course_for_account(self.conn, 1), "操作系统")
        self.assertTrue(set_enabled(self.conn, b, False)["ok"])
        self.assertTrue(set_enabled(self.conn, a, True)["ok"])

    def test_delete_cascades(self):
        pid = create_plan(self.conn, "A", "数据结构", [1, 2])["id"]
        self.assertTrue(delete_plan(self.conn, pid))
        self.assertEqual(list_plans(self.conn), [])
        n = self.conn.execute(
            "SELECT COUNT(*) FROM plan_accounts WHERE plan_id=?", (pid,)).fetchone()[0]
        self.assertEqual(n, 0)
        self.assertIsNone(course_for_account(self.conn, 1))

    def test_plan_has_no_time_fields(self):
        """批次不携带开跑时间：错峰由各账号 start_at / 窗口决定（用户明确要求）。"""
        r = create_plan(self.conn, "A", "数据结构", [1])
        self.assertTrue(r["ok"])
        cols = {row["name"] for row in self.conn.execute("PRAGMA table_info(plans)")}
        self.assertFalse({"start_at", "start_time", "begin_at"} & cols)


if __name__ == "__main__":
    unittest.main()
