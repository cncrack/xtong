"""G-8 定稿（2026-11-11）：gx_pending 待确认队列 + schema v4 升级。

用户拍板：
- 客观题做对提交后标准答案入共享题库，其他账号同课直接调用；主观题不行
- 无标准答案 → 待验证 → AI 生成 → 人核对
- 「那按你建议还是建 gx_pending 表吧」
- 不明确提示到「哪个章节哪里出问题」，且可跳过继续下一题

本文件守护：① 旧库 v3 原地升 v4 不重建数据 ② 一行=一题、不重复建
③ 状态机只允许 pending 出发、终态不可改 ④ 留痕字段 ⑤ 按账号统计
⑥ 超时过期不删行 ⑦ 动作词表新增三条。
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from xxt.store import gxks_ops, gxks_pending
from xxt.store.schema_gxks import GXKS_SCHEMA_VERSION, open_question_bank


class TestSchemaUpgrade(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "bank.db"

    def tearDown(self):
        self.tmp.cleanup()

    def test_version_is_4_and_table_exists(self):
        conn = open_question_bank(self.db)
        self.assertEqual(GXKS_SCHEMA_VERSION, 4)
        self.assertEqual(
            conn.execute("SELECT value FROM meta WHERE key='gxks_schema_version'").fetchone()[0],
            "4",
        )
        self.assertTrue(conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='gx_pending'"
        ).fetchone())
        conn.close()

    def test_v3_data_survives_upgrade(self):
        """旧库里已有 gx_ops 记录，升级不能丢（IF NOT EXISTS + 不重建）。"""
        conn = open_question_bank(self.db)
        gxks_ops.record_op(conn, account_label="jia", action="work-fill", detail="旧记录")
        conn.close()
        conn = open_question_bank(self.db)  # 再打开 = 再 migrate 一次
        rows = gxks_ops.recent_ops(conn)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action"], "work-fill")
        conn.close()

    def test_migrate_is_idempotent(self):
        for _ in range(3):
            conn = open_question_bank(self.db)
            conn.close()
        conn = open_question_bank(self.db)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM gx_pending").fetchone()[0], 0)
        conn.close()


class TestPendingCrud(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = open_question_bank(Path(self.tmp.name) / "bank.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _add(self, ordinal=1, reason="ai_used", **kw):
        return gxks_pending.add(
            self.conn, account_label="jia", question_ordinal=ordinal, reason=reason,
            course_id="32", section_id="1298", section_name="2.1 国家安全概述",
            exercise_id="14669", type_code="2", stem="题干",
            risk={"source": "ai", "confidence": 0.4}, work_file="work_jia_1.json", **kw
        )

    def test_add_returns_id_and_fields(self):
        pid = self._add()
        rows = gxks_pending.list_pending(self.conn)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["id"], pid)
        self.assertEqual(r["status"], "pending")
        self.assertEqual(r["question_ordinal"], 1)
        self.assertEqual(r["section_name"], "2.1 国家安全概述")
        self.assertEqual(r["risk"]["source"], "ai")

    def test_no_duplicate_pending_for_same_question(self):
        a = self._add()
        b = self._add(reason="low_confidence")
        self.assertEqual(a, b)
        self.assertEqual(len(gxks_pending.list_pending(self.conn)), 1)
        # 风险被更新成新原因
        self.assertEqual(gxks_pending.list_pending(self.conn)[0]["reason"], "low_confidence")

    def test_different_ordinals_are_separate_rows(self):
        """一行 = 一题，才能「跳过继续下一题」。"""
        self._add(ordinal=1)
        self._add(ordinal=2)
        self.assertEqual(len(gxks_pending.list_pending(self.conn)), 2)

    def test_decide_records_who_when_why(self):
        pid = self._add()
        self.assertTrue(gxks_pending.decide(
            self.conn, pid, status="verified", decided_by="人工", note="已核对平台判分页"))
        r = gxks_pending.list_pending(self.conn, status=None)[0]
        self.assertEqual(r["status"], "verified")
        self.assertEqual(r["decided_by"], "人工")
        self.assertTrue(r["decided_at"])
        self.assertIn("核对", r["decision_note"])

    def test_terminal_cannot_be_changed_again(self):
        """终态不可覆盖，防已有决定被冲掉。"""
        pid = self._add()
        gxks_pending.decide(self.conn, pid, status="skipped", decided_by="甲")
        self.assertFalse(gxks_pending.decide(
            self.conn, pid, status="verified", decided_by="乙"))
        self.assertEqual(gxks_pending.list_pending(self.conn, status=None)[0]["decided_by"], "甲")

    def test_illegal_status_raises(self):
        pid = self._add()
        with self.assertRaises(ValueError):
            gxks_pending.decide(self.conn, pid, status="pending", decided_by="甲")

    def test_illegal_reason_raises(self):
        with self.assertRaises(ValueError):
            self._add(reason="made_up")

    def test_pending_counts_by_account(self):
        self._add(ordinal=1)
        self._add(ordinal=2)
        gxks_pending.add(self.conn, account_label="yi", question_ordinal=1,
                         reason="no_site_answer", exercise_id="14669")
        counts = gxks_pending.pending_counts(self.conn)
        self.assertEqual(counts["jia"], 2)
        self.assertEqual(counts["yi"], 1)
        self.assertEqual(counts["__total__"], 3)

    def test_list_all_statuses_for_audit(self):
        """留痕可查：已决定的也要能查出来（status=None）。"""
        pid = self._add()
        gxks_pending.decide(self.conn, pid, status="verified", decided_by="人工")
        self.assertEqual(len(gxks_pending.list_pending(self.conn)), 0)  # 默认只看 pending
        self.assertEqual(len(gxks_pending.list_pending(self.conn, status=None)), 1)

    def test_expire_stale_keeps_row_and_leaves_note(self):
        gxks_pending.add(self.conn, account_label="jia", question_ordinal=1,
                         reason="ai_used", exercise_id="14669",
                         at="2020-01-01 00:00:00")
        n = gxks_pending.expire_stale(self.conn, hours=1, at="2020-01-01 02:00:00")
        self.assertEqual(n, 1)
        r = gxks_pending.list_pending(self.conn, status=None)[0]
        self.assertEqual(r["status"], "expired")
        self.assertIn("超时", r["decision_note"])
        self.assertEqual(len(gxks_pending.list_pending(self.conn, status=None)), 1)


class TestActionWords(unittest.TestCase):
    def test_new_actions_registered(self):
        for a in ("work-review", "work-verify", "work-skip"):
            self.assertIn(a, gxks_ops.ACTIONS)


if __name__ == "__main__":
    unittest.main()
