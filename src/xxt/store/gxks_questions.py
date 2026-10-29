"""可累积题库：题目版本共享，作答及答案证据按账号与答卷隔离。"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from datetime import datetime, timezone


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _score(value: object) -> None:
    if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                              or not math.isfinite(value) or value < 0):
        raise ValueError("分值必须为非负有限数或 null")


def _validate(bundle: dict) -> None:
    if not re.fullmatch(r"[a-z0-9_-]+", bundle.get("account_label", "")):
        raise ValueError("账号必须使用本地代号")
    for key in ("exam_start_id", "exercise_id"):
        if not isinstance(bundle.get(key), str) or not bundle[key]:
            raise ValueError(f"缺少 {key}")
    questions = bundle.get("questions")
    if not isinstance(questions, list) or not questions:
        raise ValueError("不能导入空题目列表")
    ordinals = []
    for q in questions:
        ordinal = q.get("ordinal")
        if not isinstance(ordinal, int) or isinstance(ordinal, bool) or ordinal < 1:
            raise ValueError("题号必须为正整数")
        ordinals.append(ordinal)
        for key in ("type_code", "stem", "stem_html"):
            if not isinstance(q.get(key), str):
                raise ValueError(f"题目缺少 {key}")
        if not q["type_code"]:
            raise ValueError("题型原始编码不能为空")
        if not isinstance(q.get("parts"), list):
            raise ValueError("题目部件必须为动态列表")
        for part in q["parts"]:
            if not isinstance(part, dict) or not part.get("kind"):
                raise ValueError("题目部件缺少 kind")
        answer = q.get("answer", {})
        for value, source in (("picked", "picked_source"), ("standard", "standard_source")):
            if answer.get(value) is not None and answer.get(source) not in {"human", "site", "ai"}:
                raise ValueError("答案必须标明 human/site/ai 来源")
        if answer.get("is_correct") is not None and not isinstance(answer["is_correct"], bool):
            raise ValueError("判题结果必须为 bool 或 null")
        _score(q.get("max_score"))
        _score(answer.get("score_got"))
        if (answer.get("score_got") is not None and q.get("max_score") is not None
                and answer["score_got"] > q["max_score"]):
            raise ValueError("单题得分不能超过满分")
    if len(set(ordinals)) != len(ordinals):
        raise ValueError("同一答卷的题号不能重复")
    _json(bundle)


def import_bundle(conn: sqlite3.Connection, bundle: dict) -> dict:
    """原子导入已脱敏的规范快照；相同内容跳过，新判分追加证据不覆盖旧答案。"""
    _validate(bundle)
    if conn.in_transaction:
        raise ValueError("导入前连接不能有未提交事务")
    digest = _digest(bundle)
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        metadata = tuple(bundle.get(k, "") for k in (
            "account_label", "exam_start_id", "exercise_id", "course_id", "section_id",
            "section_name", "paper_label", "start_time"))
        conn.execute(
            "INSERT OR IGNORE INTO gx_q_papers(account_label,exam_start_id,exercise_id,course_id,"
            "section_id,section_name,paper_label,start_time) VALUES(?,?,?,?,?,?,?,?)", metadata)
        paper = conn.execute(
            "SELECT * FROM gx_q_papers WHERE account_label=? AND exam_start_id=?", metadata[:2]
        ).fetchone()
        paper_id = paper["id"]
        for k in ("exercise_id", "course_id", "section_id", "section_name", "paper_label", "start_time"):
            if paper[k] and bundle.get(k) and paper[k] != bundle[k]:
                raise ValueError("同一答卷的归属或元信息不一致，拒绝混入")
        existing = conn.execute(
            "SELECT id FROM gx_q_imports WHERE paper_id=? AND digest=?", (paper_id, digest)
        ).fetchone()
        if existing:
            return {"paper_id": paper_id, "import_id": existing[0], "inserted": False,
                    "questions": len(bundle["questions"])}
        conn.execute(
            "UPDATE gx_q_papers SET course_id=COALESCE(NULLIF(course_id,''),?), "
            "section_id=COALESCE(NULLIF(section_id,''),?), section_name=COALESCE(NULLIF(section_name,''),?), "
            "paper_label=COALESCE(NULLIF(paper_label,''),?), start_time=COALESCE(NULLIF(start_time,''),?) "
            "WHERE id=?", tuple(bundle.get(k, "") for k in (
                "course_id", "section_id", "section_name", "paper_label", "start_time")) + (paper_id,))
        for code, name in bundle.get("type_map", {}).items():
            conn.execute("INSERT OR IGNORE INTO gx_q_types(code,name) VALUES(?,?)", (code, name))
        cur = conn.execute(
            "INSERT INTO gx_q_imports(paper_id,digest,imported_at,raw_json) VALUES(?,?,?,?)",
            (paper_id, digest, datetime.now(timezone.utc).isoformat(), _json(bundle)))
        import_id = cur.lastrowid
        old_ordinals = {r[0] for r in conn.execute(
            "SELECT ordinal FROM gx_q_paper_items WHERE paper_id=?", (paper_id,))}
        new_ordinals = {q["ordinal"] for q in bundle["questions"]}
        if old_ordinals and old_ordinals != new_ordinals:
            raise ValueError("同一答卷题目数量或题号变化，拒绝部分覆盖")
        for q in bundle["questions"]:
            content = {k: q.get(k, "") for k in ("type_code", "stem", "stem_html", "parts", "extra")}
            fingerprint = _digest(content)
            qid = str(q.get("qid") or "")
            conn.execute("INSERT OR IGNORE INTO gx_q_types(code,name) VALUES(?,?)",
                         (q["type_code"], q.get("type_name") or "未知题型"))
            conn.execute(
                "INSERT OR IGNORE INTO gx_q_items(platform_qid,fingerprint,type_code,stem,stem_html,raw_json) "
                "VALUES(?,?,?,?,?,?)", (qid, fingerprint, q["type_code"], q["stem"], q["stem_html"], _json(content)))
            item_id = conn.execute(
                "SELECT id FROM gx_q_items WHERE platform_qid=? AND fingerprint=?", (qid, fingerprint)
            ).fetchone()[0]
            for i, part in enumerate(q["parts"]):
                conn.execute(
                    "INSERT OR IGNORE INTO gx_q_parts(item_id,ordinal,kind,part_key,content_json) "
                    "VALUES(?,?,?,?,?)", (item_id, i, part["kind"], str(part.get("key", "")), _json(part)))
            old = conn.execute(
                "SELECT item_id,max_score FROM gx_q_paper_items WHERE paper_id=? AND ordinal=?",
                (paper_id, q["ordinal"])).fetchone()
            if old and (old[0] != item_id or old[1] != q.get("max_score")):
                raise ValueError("同一答卷题目版本或分值变化，拒绝覆盖")
            conn.execute(
                "INSERT OR IGNORE INTO gx_q_paper_items(paper_id,ordinal,item_id,max_score) VALUES(?,?,?,?)",
                (paper_id, q["ordinal"], item_id, q.get("max_score")))
            a = q.get("answer", {})
            conn.execute(
                "INSERT INTO gx_q_answers(import_id,ordinal,picked_json,picked_source,standard_json,"
                "standard_source,score_got,is_correct,explanation_html) VALUES(?,?,?,?,?,?,?,?,?)",
                (import_id, q["ordinal"], _json(a["picked"]) if a.get("picked") is not None else None,
                 a.get("picked_source"), _json(a["standard"]) if a.get("standard") is not None else None,
                 a.get("standard_source"), a.get("score_got"), a.get("is_correct"),
                 a.get("explanation_html", "")))
    return {"paper_id": paper_id, "import_id": import_id, "inserted": True,
            "questions": len(bundle["questions"])}


def read_attempt(conn: sqlite3.Connection, *, account_label: str, exam_start_id: str) -> dict | None:
    """按账号取最新完整快照，不把别人的作答作为当前账号答案。"""
    row = conn.execute(
        "SELECT i.raw_json FROM gx_q_imports i JOIN gx_q_papers p ON p.id=i.paper_id "
        "WHERE p.account_label=? AND p.exam_start_id=? ORDER BY i.id DESC LIMIT 1",
        (account_label, exam_start_id)).fetchone()
    return json.loads(row[0]) if row else None


def bank_counts(conn: sqlite3.Connection) -> dict:
    """本地统计不访问平台；作答条数不包含重复采集的历史快照。"""
    return {
        "papers": conn.execute("SELECT COUNT(*) FROM gx_q_papers").fetchone()[0],
        "question_versions": conn.execute("SELECT COUNT(*) FROM gx_q_items").fetchone()[0],
        "attempt_questions": conn.execute("SELECT COUNT(*) FROM gx_q_paper_items").fetchone()[0],
        "snapshots": conn.execute("SELECT COUNT(*) FROM gx_q_imports").fetchone()[0],
    }
