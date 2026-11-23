"""G-9 第 1 级 / G-8：交卷前核对单 build_review（2026-11-11 用户定案）。

## 用户定案的两条关键口径

1. **客观题 vs 主观题分开**
   - 客观题（单选/多选/判断）答案唯一：命中题库 `site` 标准答案直接用；
     没有才走 AI，标 `no_site_answer` 等人工核对（待验证）。
   - 主观题答案不唯一：**禁止自动入库、禁止跨账号共享**，一律进待确认。

2. **`confidence` 不作闸门**
   它是 LLM 自报的主观确定度（`_make_llm` 里 `data.get("confidence", 0.0)`），
   无校准、不是准确率。只作展示提示，**不参与任何自动决策**。

唯一的硬条件 = 该题有没有平台 `site` 标准答案（`source == "bank"`）。

`auto_eligible` 只表示「数据上满足第 2 级 guarded 的条件」；
第 2 级**本轮未启用**，调用方不得据此直接交卷。
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_work():
    spec = importlib.util.spec_from_file_location(
        "gxks_work_under_test", ROOT / "scripts" / "gxks_work.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def q(ordinal, *, source, type_code="2", confidence=1.0, type_name="单选", stem="s"):
    return {"ordinal": ordinal, "qid": str(ordinal), "type_code": type_code,
            "type_name": type_name, "stem": stem, "options": ["a", "b", "c"],
            "keys": ["a"], "source": source, "confidence": confidence, "reason": ""}


class TestObjectiveFromBank(unittest.TestCase):
    """全客观题 + 全命中题库 site 标准答案 -> 数据上满足第 2 级。"""

    def test_all_bank_objective_is_auto_eligible(self):
        m = _load_work()
        r = m.build_review([q(1, source="bank"), q(2, source="bank")])
        self.assertEqual(r["risks"], [])
        self.assertTrue(r["auto_eligible"])
        self.assertEqual(r["counts"]["bank"], 2)
        self.assertEqual(r["counts"]["risk"], 0)


class TestAIWithoutSiteAnswer(unittest.TestCase):
    """客观题没命中题库 -> 走 AI -> 必须人核对（no_site_answer）。"""

    def test_ai_objective_needs_review(self):
        m = _load_work()
        r = m.build_review([q(1, source="ai")])
        self.assertFalse(r["auto_eligible"])
        self.assertEqual(len(r["risks"]), 1)
        risk = r["risks"][0]
        self.assertIn("ai_used", risk["reasons"])
        self.assertIn("no_site_answer", risk["reasons"])
        self.assertEqual(risk["primary_reason"], "ai_used")

    def test_mixed_bank_and_ai(self):
        m = _load_work()
        r = m.build_review([q(1, source="bank"), q(2, source="ai")])
        self.assertFalse(r["auto_eligible"])
        self.assertEqual(len(r["risks"]), 1)
        self.assertEqual(r["risks"][0]["ordinal"], 2)


class TestConfidenceIsNotAGate(unittest.TestCase):
    """confidence 只影响提示，不影响是否进核对单（AI 题本来就都要人看）。"""

    def test_high_confidence_ai_still_risky(self):
        m = _load_work()
        r = m.build_review([q(1, source="ai", confidence=1.0)])
        self.assertFalse(r["auto_eligible"])
        self.assertEqual(len(r["risks"]), 1)
        # 高置信不追加 low_confidence
        self.assertNotIn("low_confidence", r["risks"][0]["reasons"])

    def test_low_confidence_adds_hint_only(self):
        m = _load_work()
        r = m.build_review([q(1, source="ai", confidence=0.3)])
        self.assertIn("low_confidence", r["risks"][0]["reasons"])
        # 但主因仍是 ai_used —— confidence 不改变判定顺序
        self.assertEqual(r["risks"][0]["primary_reason"], "ai_used")

    def test_bank_never_flagged_even_with_low_confidence_value(self):
        """题库命中就是平台标准答案，模型自评再低也不该被标风险。"""
        m = _load_work()
        r = m.build_review([q(1, source="bank", confidence=0.0)])
        self.assertEqual(r["risks"], [])
        self.assertTrue(r["auto_eligible"])


class TestSubjectiveBlocked(unittest.TestCase):
    """主观题禁止自动入库/共享，一律需人确认（哪怕题库有答案）。"""

    def test_subjective_flagged_even_when_bank(self):
        m = _load_work()
        r = m.build_review([q(1, source="bank", type_code="9", type_name="简答")])
        self.assertEqual(len(r["risks"]), 1)
        self.assertIn("subjective", r["risks"][0]["reasons"])
        self.assertFalse(r["auto_eligible"])

    def test_subjective_with_ai(self):
        m = _load_work()
        r = m.build_review([q(1, source="ai", type_code="9")])
        self.assertIn("subjective", r["risks"][0]["reasons"])
        self.assertFalse(r["auto_eligible"])


class TestEdgeCases(unittest.TestCase):
    def test_empty_plan_not_eligible(self):
        m = _load_work()
        r = m.build_review([])
        self.assertFalse(r["auto_eligible"])
        self.assertEqual(r["counts"]["total"], 0)

    def test_counts_consistent(self):
        m = _load_work()
        r = m.build_review([q(1, source="bank"), q(2, source="ai"), q(3, source="ai")])
        c = r["counts"]
        self.assertEqual(c["total"], 3)
        self.assertEqual(c["bank"], 1)
        self.assertEqual(c["ai"], 2)
        self.assertEqual(c["risk"], 2)

    def test_custom_threshold(self):
        m = _load_work()
        r = m.build_review([q(1, source="ai", confidence=0.7)], low_confidence=0.5)
        self.assertNotIn("low_confidence", r["risks"][0]["reasons"])
        r2 = m.build_review([q(1, source="ai", confidence=0.7)], low_confidence=0.9)
        self.assertIn("low_confidence", r2["risks"][0]["reasons"])


if __name__ == "__main__":
    unittest.main()
