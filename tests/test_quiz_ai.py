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
                    answer=["B.2"], verified=True)
        llm = FakeLLM("should not be called")
        r = solve(self.conn, llm, "1+1=?", "single", ["A.1", "B.2"])
        self.assertEqual(r["source"], "bank")
        self.assertEqual(r["answer"], ["B.2"])
        self.assertEqual(llm.calls, 0)

    def test_llm_path_and_store(self):
        llm = FakeLLM('{"answer":["B"],"confidence":0.92,"reason":"常识"}')
        r = solve(self.conn, llm, "地球是圆的吗", "judge", [])
        self.assertEqual(r["source"], "llm")
        self.assertFalse(r["need_manual"])
        self.assertIsNotNone(bank_lookup(self.conn, r["hash"]))

    def test_low_confidence_manual(self):
        llm = FakeLLM('{"answer":["A"],"confidence":0.3}')
        r = solve(self.conn, llm, "不确定的题", "single", ["A", "B"])
        self.assertTrue(r["need_manual"])

    def test_llm_failure_falls_to_manual(self):
        class BadLLM:
            def chat(self, *a, **k):
                raise LLMError("boom")

        r = solve(self.conn, BadLLM(), "x", "single", ["A"])
        self.assertTrue(r["need_manual"])

    def test_wrong_marked_clears_answer(self):
        h = question_hash("t", ["A"])
        bank_record(self.conn, h, "t", "single", ["A"], answer=["A"], verified=True)
        bank_record_wrong(self.conn, h)
        self.assertIsNone(bank_lookup(self.conn, h))


if __name__ == "__main__":
    unittest.main()
