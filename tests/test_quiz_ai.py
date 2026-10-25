"""弹题作答管线测试（LLM 用假实现，不触网）。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from xxt.ai.llm import LLMError
from xxt.ai.quiz import (bank_lookup, bank_record, bank_record_wrong, build_messages,
                         parse_llm_json, question_hash, solve)
from xxt.store.db import connect


class FakeLLM:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls = 0

    def chat(self, messages, temperature=0.1):
        self.calls += 1
        return self.reply


class TestParsing(unittest.TestCase):
    def test_parse_with_fences(self):
        text = '好的，答案如下：\n```json\n{"answer": ["A"], "confidence": 0.9, "reason": "定义"}\n```'
        d = parse_llm_json(text)
        self.assertEqual(d["answer"], ["A"])
        self.assertEqual(d["confidence"], 0.9)

    def test_parse_single_string_answer(self):
        d = parse_llm_json('{"answer": "对", "confidence": 0.8}')
        self.assertEqual(d["answer"], ["对"])

    def test_hash_option_order_insensitive(self):
        self.assertEqual(question_hash("题", ["A", "B"]), question_hash("题", ["B", "A"]))


class TestSolve(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.tmp.name) / "t.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_bank_hit_skips_llm(self):
        h = question_hash("1+1=?", ["A.1", "B.2"])
        bank_record(self.conn, h, "1+1=?", "single", ["A.1", "B.2"],
                    account_id=1, answer=["B.2"], verified=True)
        llm = FakeLLM("should not be called")
        r = solve(self.conn, llm, "1+1=?", "single", ["A.1", "B.2"], account_id=1)
        self.assertEqual(r["source"], "bank")
        self.assertEqual(r["answer"], ["B.2"])
        self.assertEqual(llm.calls, 0)

    def test_answer_of_other_account_is_invisible(self):
        """答案按账号隔离（2026-10-26 用户要求）：A 的答案对 B 不可见，B 必须自己问 AI。"""
        h = question_hash("2+2=?", ["A.3", "B.4"])
        bank_record(self.conn, h, "2+2=?", "single", ["A.3", "B.4"],
                    account_id=1, answer=["B.4"], verified=True)
        self.assertIsNotNone(bank_lookup(self.conn, h, account_id=1))
        self.assertIsNone(bank_lookup(self.conn, h, account_id=2))
        llm = FakeLLM('{"answer":["B.4"],"confidence":0.9,"reason":"常识"}')
        r = solve(self.conn, llm, "2+2=?", "single", ["A.3", "B.4"], account_id=2)
        self.assertEqual(r["source"], "llm")
        self.assertEqual(llm.calls, 1)

    def test_question_itself_is_shared(self):
        """题目本体共享：别的账号答过，questions 里有题干，但答案仍为空。"""
        h = question_hash("共享题", ["A", "B"])
        bank_record(self.conn, h, "共享题", "single", ["A", "B"],
                    account_id=1, answer=["A"])
        row = self.conn.execute(
            "SELECT stem FROM questions WHERE question_hash=?", (h,)).fetchone()
        self.assertEqual(row["stem"], "共享题")
        n = self.conn.execute(
            "SELECT COUNT(*) FROM answers WHERE question_hash=?", (h,)).fetchone()[0]
        self.assertEqual(n, 1)

    def test_llm_path_and_store(self):
        llm = FakeLLM('{"answer":["B"],"confidence":0.92,"reason":"常识"}')
        r = solve(self.conn, llm, "地球是圆的吗", "judge", [], account_id=3)
        self.assertEqual(r["source"], "llm")
        self.assertFalse(r["need_manual"])
        self.assertIsNotNone(bank_lookup(self.conn, r["hash"], account_id=3))
        self.assertIsNone(bank_lookup(self.conn, r["hash"], account_id=4))

    def test_low_confidence_manual(self):
        llm = FakeLLM('{"answer":["A"],"confidence":0.3}')
        r = solve(self.conn, llm, "不确定的题", "single", ["A", "B"], account_id=1)
        self.assertTrue(r["need_manual"])

    def test_llm_failure_falls_to_manual(self):
        class BadLLM:
            def chat(self, *a, **k):
                raise LLMError("boom")

        r = solve(self.conn, BadLLM(), "x", "single", ["A"], account_id=1)
        self.assertTrue(r["need_manual"])

    def test_wrong_marked_clears_answer(self):
        h = question_hash("t", ["A"])
        bank_record(self.conn, h, "t", "single", ["A"], account_id=1,
                    answer=["A"], verified=True)
        bank_record_wrong(self.conn, h, account_id=1)
        self.assertIsNone(bank_lookup(self.conn, h, account_id=1))

    def test_wrong_does_not_touch_other_accounts(self):
        """A 判错清空，不能连带清掉 B 已验证的答案。"""
        h = question_hash("共享错题", ["A", "B"])
        bank_record(self.conn, h, "共享错题", "single", ["A", "B"],
                    account_id=1, answer=["A"], verified=True)
        bank_record(self.conn, h, "共享错题", "single", ["A", "B"],
                    account_id=2, answer=["B"], verified=True)
        bank_record_wrong(self.conn, h, account_id=1)
        self.assertIsNone(bank_lookup(self.conn, h, account_id=1))
        self.assertEqual(bank_lookup(self.conn, h, account_id=2)["answer"], ["B"])

    def test_legacy_answer_is_never_auto_reused(self):
        """迁移进来的历史答案（account_id=0）不参与自动作答。"""
        h = question_hash("老题", ["A"])
        self.conn.execute(
            "INSERT INTO questions(question_hash, stem, type, options, updated_at)"
            " VALUES(?,?,?,?,datetime('now'))", (h, "老题", "single", '["A"]'))
        self.conn.execute(
            "INSERT INTO answers(question_hash, account_id, answer, source, updated_at)"
            " VALUES(?,0,?,'legacy',datetime('now'))", (h, '["A"]'))
        self.conn.commit()
        self.assertIsNone(bank_lookup(self.conn, h, account_id=0))
        self.assertIsNone(bank_lookup(self.conn, h, account_id=1))


if __name__ == "__main__":
    unittest.main()
