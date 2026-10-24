"""弹题作答管线（PLAN §5.4）：本地题库优先 → LLM 兜底 → 置信度门限 → 结果回写题库。

纯逻辑模块（不碰浏览器/平台），LLM 客户端可注入假实现做单测。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from .llm import LLMClient, LLMError

# 题型：single 单选 / multi 多选 / judge 判断 / fill 填空
QTYPES = ("single", "multi", "judge", "fill")
MIN_CONFIDENCE = 0.6  # 低于此值 → need_manual（半自动模式由人工确认）

_PROMPT = """你是在线课程的学习者，请回答下面的课程测验题。

题型：{qtype_desc}
题干：{stem}
选项：
{options}
{context}要求：基于题干与课程常识作答；只输出一个 JSON 对象，不要任何其他文字：
{{"answer": {answer_shape}, "confidence": 0到1的小数, "reason": "20字内依据"}}"""


def question_hash(stem: str, options: list[str]) -> str:
    """题干+选项 规范化哈希（题库主键，选项顺序不敏感）。"""
    norm = (stem or "").strip() + "|" + "|".join(sorted(o.strip() for o in options or []))
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()


def _qtype_desc(qtype: str) -> str:
    return {"single": "单选题", "multi": "多选题", "judge": "判断题", "fill": "填空题"}.get(qtype, qtype)


def _answer_shape(qtype: str) -> str:
    return '["选项文本或A/B/C"]' if qtype in ("single", "multi") else '"答案文本或对/错"'


def build_messages(stem: str, qtype: str, options: list[str], context: str = "") -> list[dict[str, str]]:
    ctx = f"课程背景资料：{context}\n" if context else ""
    text = _PROMPT.format(
        qtype_desc=_qtype_desc(qtype), stem=stem.strip(),
        options="\n".join(f"- {o}" for o in options) or "（无选项）",
        context=ctx, answer_shape=_answer_shape(qtype),
    )
    return [{"role": "user", "content": text}]


def parse_llm_json(text: str) -> dict[str, Any]:
    """容错解析模型输出（剥离 ```json 围栏/前后杂文）。"""
    cleaned = text.strip()
    if "```" in cleaned:
        for part in cleaned.split("```"):
            part = part.strip()
            if part.startswith("json"):
                part = part[4:]
            if part.startswith("{"):
                cleaned = part
                break
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start >= 0 and end > start:
        cleaned = cleaned[start:end + 1]
    data = json.loads(cleaned)
    answer = data.get("answer")
    if isinstance(answer, str):
        answer = [answer]
    return {
        "answer": [str(a).strip() for a in (answer or [])],
        "confidence": float(data.get("confidence", 0)),
        "reason": str(data.get("reason", ""))[:60],
    }


def bank_lookup(conn: sqlite3.Connection, h: str) -> dict[str, Any] | None:
    """题库命中：仅返回已验证（answer 非空）的记录。"""
    row = conn.execute(
        "SELECT answer, type, correct_count FROM questions WHERE question_hash=? AND answer IS NOT NULL",
        (h,),
    ).fetchone()
    if row is None:
        return None
    return {"answer": json.loads(row["answer"]), "qtype": row["type"],
            "correct_count": row["correct_count"], "confidence": 1.0}


def bank_record(conn: sqlite3.Connection, h: str, stem: str, qtype: str,
                options: list[str], answer: list[str] | None = None,
                verified: bool = False) -> None:
    """写入/更新题库；verified=True 表示判题确认正确（correct_count+1）。"""
    conn.execute(
        """INSERT INTO questions(question_hash, stem, type, options, answer,
                                 correct_count, wrong_count, updated_at)
           VALUES(?,?,?,?,?,0,0,datetime('now'))
           ON CONFLICT(question_hash) DO UPDATE SET
             answer = COALESCE(excluded.answer, questions.answer),
             correct_count = questions.correct_count + ?,
             updated_at = datetime('now')""",
        (h, stem.strip(), qtype, json.dumps(options, ensure_ascii=False),
         json.dumps(answer, ensure_ascii=False) if answer else None,
         1 if verified else 0),
    )
    conn.commit()


def bank_record_wrong(conn: sqlite3.Connection, h: str) -> None:
    conn.execute(
        "UPDATE questions SET wrong_count = wrong_count + 1, answer = NULL,"
        " updated_at = datetime('now') WHERE question_hash = ?", (h,),
    )
    conn.commit()


def solve(conn: sqlite3.Connection, llm: LLMClient | Any, stem: str, qtype: str,
          options: list[str], context: str = "") -> dict[str, Any]:
    """作答一条题目。返回：
    {source: bank|llm|manual, answer: [...], confidence, reason?, need_manual}
    """
    h = question_hash(stem, options)
    hit = bank_lookup(conn, h)
    if hit is not None:
        return {"source": "bank", **hit, "need_manual": False, "hash": h}

    try:
        raw = llm.chat(build_messages(stem, qtype, options, context))
        parsed = parse_llm_json(raw)
    except (LLMError, ValueError, KeyError):
        return {"source": "manual", "answer": [], "confidence": 0.0,
                "need_manual": True, "hash": h,
                "reason": "AI 调用/解析失败"}
    bank_record(conn, h, stem, qtype, options, answer=parsed["answer"])  # 先存候选
    if parsed["confidence"] < MIN_CONFIDENCE or not parsed["answer"]:
        return {"source": "manual", **parsed, "need_manual": True, "hash": h}
    return {"source": "llm", **parsed, "need_manual": False, "hash": h}


def handle_quiz(quiz_cfg: dict[str, Any], conn: sqlite3.Connection, llm: LLMClient | Any,
                payload: dict[str, Any]) -> dict[str, Any]:
    """弹题事件的决策入口（watcher 上报 quiz-popup 后由运行器调用）。

    返回 action ∈ ignore（未启用，不干扰平台默认行为）/ manual（转人工）/ fill（半自动：只填不交）/
    fill_submit（全自动：填并提交——仅在 mode=auto 且高置信度时）。
    """
    if not quiz_cfg.get("enabled"):
        return {"action": "ignore"}
    stem = str(payload.get("stem") or "").strip()
    if not stem:
        return {"action": "manual", "reason": "题干解析失败"}
    result = solve(conn, llm, stem, payload.get("qtype") or "single",
                   payload.get("options") or [], payload.get("context") or "")
    if result["need_manual"]:
        return {"action": "manual", **result}
    return {"action": "fill_submit" if quiz_cfg.get("mode") == "auto" else "fill", **result}
