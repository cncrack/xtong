"""批次（plan）测试：统一排课、账号冲突、与手动启动的关系（不触网、不拉进程）。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from xxt.core.plans import (busy_accounts, course_for_account, create_plan,
                            delete_plan, list_plans, set_enabled, update_plan)
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
        self.assertEqual(course_for_account(self.conn, 1), {"name": "数据结构", "pid": None})
        self.assertEqual(course_for_account(self.conn, 2), {"name": "数据结构", "pid": None})
        self.assertIsNone(course_for_account(self.conn, 3))  # 未排批次的账号走轮转
        plans = list_plans(self.conn)
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]["account_ids"], [1, 2])
        self.assertTrue(plans[0]["enabled"])

    def test_course_pid_anchor(self):
        """批次可带平台课程ID；course_for_account 原样带出（运行按 pid 精确匹配）。"""
        self.assertTrue(create_plan(self.conn, "A", "数据结构", [1], course_pid="c9")["ok"])
        self.assertEqual(course_for_account(self.conn, 1), {"name": "数据结构", "pid": "c9"})
        self.assertEqual(list_plans(self.conn)[0]["course_pid"], "c9")

    def test_update_plan_sets_and_clears_pid(self):
        pid = create_plan(self.conn, "A", "数据结构", [1])["id"]
        self.assertIsNone(list_plans(self.conn)[0]["course_pid"])  # 老式批次无 pid
        self.assertTrue(update_plan(self.conn, pid, course_pid="c9")["ok"])
        self.assertEqual(list_plans(self.conn)[0]["course_pid"], "c9")
        self.assertTrue(update_plan(self.conn, pid, course_pid="")["ok"])  # 空串=清空
        self.assertIsNone(list_plans(self.conn)[0]["course_pid"])

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
        self.assertEqual(course_for_account(self.conn, 1), {"name": "操作系统", "pid": None})
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

    # ---- update_plan（编辑已建批次：改名 / 换课程 / 增删账号）----

    def test_update_plan_rename_and_course(self):
        pid = create_plan(self.conn, "A", "数据结构", [1])["id"]
        r = update_plan(self.conn, pid, name="B", course_name="操作系统")
        self.assertTrue(r["ok"])
        p = list_plans(self.conn)[0]
        self.assertEqual(p["name"], "B")
        self.assertEqual(p["course_name"], "操作系统")
        self.assertEqual(p["account_ids"], [1])  # 未传 account_ids → 保持不变
        self.assertEqual(course_for_account(self.conn, 1), {"name": "操作系统", "pid": None})

    def test_update_plan_change_accounts(self):
        pid = create_plan(self.conn, "A", "数据结构", [1])["id"]
        r = update_plan(self.conn, pid, account_ids=[2, 3])
        self.assertTrue(r["ok"])
        self.assertEqual(list_plans(self.conn)[0]["account_ids"], [2, 3])
        self.assertIsNone(course_for_account(self.conn, 1))  # 移出后回归轮转
        self.assertEqual(course_for_account(self.conn, 2), {"name": "数据结构", "pid": None})

    def test_update_plan_reject_empty_name_or_accounts(self):
        pid = create_plan(self.conn, "A", "数据结构", [1])["id"]
        self.assertFalse(update_plan(self.conn, pid, name="")["ok"])
        self.assertFalse(update_plan(self.conn, pid, course_name="")["ok"])
        self.assertFalse(update_plan(self.conn, pid, account_ids=[])["ok"])
        self.assertEqual(list_plans(self.conn)[0]["account_ids"], [1])  # 均未改动

    def test_update_plan_missing(self):
        self.assertFalse(update_plan(self.conn, 999, name="X")["ok"])

    def test_update_plan_conflict_excludes_self(self):
        a = create_plan(self.conn, "A", "数据结构", [1])["id"]
        create_plan(self.conn, "B", "操作系统", [2])
        # 把 A 的账号改成 [1,2]：2 已在启用批次 B 里 → 拒绝
        r = update_plan(self.conn, a, account_ids=[1, 2])
        self.assertFalse(r["ok"])
        self.assertIn("已在其他启用中的批次里", r["error"])
        # 仅调整 A 自己的账号（仍含自己批次的 #1）→ 排除自身，不与自己冲突
        self.assertTrue(update_plan(self.conn, a, account_ids=[1])["ok"])


if __name__ == "__main__":
    unittest.main()
