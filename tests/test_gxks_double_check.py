"""交卷前二次验证（2026-11-28 用户要求）。

## 背景

实测 1.6 第 2 题：`2025年是人民海军成立75周年。`，平台标准 = **错**
（人民海军 1949-04-23 成立，2025 是 76 周年）。

AI 的 reasoning 原文：「人民海军成立于1949年，**2024年为75周年，题目表述有误**，
但根据常见公考题库逻辑，此处可能指2024年...」——**自己算出不对，还是答了「对」**。

所以加「二次验证」：答完换个角色（校对者）独立复核一遍。

## 三条硬规则（本测试逐条锁住）

① 题库命中的题**不验**（平台官方答案是权威，验了可能把对的改错）
② 不一致时**不自动改答案**（保留第一遍，只提示）
③ 验证出错**不影响主流程**
"""

from __future__ import annotations

import pytest

from xxt.adapters.gxks import work

JUDGE_Q = {
    "ordinal": 1, "qid": "9385824", "type_code": "4", "type_name": "判断题",
    "stem": "2025年是人民海军成立75周年。", "max_score": 34.0,
    "options": ["对", "错"], "option_keys": ["1", "0"],
}


def _ask_says_dui(_q):
    """第一遍：AI 答「对」（value=1）—— 就是真实出错的那种。"""
    return {"keys": ["1"], "confidence": 0.95, "reason": "75周年"}


def _verify_says_cuo(_q, _k):
    """第二遍：校对者算出应为「错」（value=0）。"""
    return {"keys": ["0"], "agree": False, "confidence": 0.9,
            "reason": "1949+75=2024，差一年"}


def test_disagreement_keeps_first_answer():
    """规则②：不一致时**保留第一遍**，只在 verify 里记建议。

    自动改等于用第二个可能错的答案替换第一个——改不改由人决定。
    """
    plan = work.plan_answers(
        [JUDGE_Q], allowed_types={"2", "4"},
        ask_llm=_ask_says_dui, verify_llm=_verify_says_cuo)
    q = plan[0]
    assert q["keys"] == ["1"], "答案必须保留第一遍"
    assert q["verify"]["agree"] is False
    assert q["verify"]["suggested"] == ["0"]
    assert q["verify"]["checked"] is True


def test_agreement_marked_ok():
    def verify(_q, _k):
        return {"keys": ["1"], "agree": True, "confidence": 0.9, "reason": "ok"}
    plan = work.plan_answers(
        [JUDGE_Q], allowed_types={"2", "4"},
        ask_llm=_ask_says_dui, verify_llm=verify)
    assert plan[0]["verify"]["agree"] is True
    assert plan[0]["verify"]["suggested"] == []      # 一致时不该有建议


def test_bank_hit_is_not_verified():
    """规则①：题库命中的题不验（省一次调用，也不会把对的改错）。"""
    calls = []

    def verify(_q, _k):
        calls.append(1)
        return {"keys": ["0"], "agree": False}

    plan = work.plan_answers(
        [JUDGE_Q], allowed_types={"2", "4"},
        bank=lambda _s, _o: {"keys": ["1"], "confidence": 1.0},
        ask_llm=_ask_says_dui, verify_llm=verify)
    assert plan[0]["source"] == "bank"
    assert calls == [], "题库命中的题不该调用验证"
    assert "verify" not in plan[0]


def test_verify_error_does_not_break():
    """规则③：验证抛错不能影响主流程。"""
    def boom(_q, _k):
        raise RuntimeError("API 超时")

    plan = work.plan_answers(
        [JUDGE_Q], allowed_types={"2", "4"},
        ask_llm=_ask_says_dui, verify_llm=boom)
    assert plan[0]["keys"] == ["1"]                  # 答案仍在
    v = plan[0]["verify"]
    assert v["checked"] is False
    assert "API 超时" in v["error"]


def test_verify_optional():
    """不传 verify_llm 时行为与从前完全一致（向后兼容）。"""
    plan = work.plan_answers(
        [JUDGE_Q], allowed_types={"2", "4"}, ask_llm=_ask_says_dui)
    assert plan[0]["keys"] == ["1"]
    assert "verify" not in plan[0]
