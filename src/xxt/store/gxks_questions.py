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


def _content_digest(bundle: dict) -> str:
    """只对“本次作答内容”取摘要。

    不能把 `course_id` / `section_id` / `section_name` 一类元信息算进来：补全分类信息
    不是新作答，算进去就会多出一条快照与一份重复答案（2026-10-30 修正）。
    """
    return _digest({"exam_start_id": bundle.get("exam_start_id"),
                    "exercise_id": bundle.get("exercise_id"),
                    "questions": bundle.get("questions")})


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
    digest = _content_digest(bundle)
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
        # 先补分类信息再做幂等判定：补 course_id/section_id 不算新快照。
        conn.execute(
            "UPDATE gx_q_papers SET course_id=COALESCE(NULLIF(course_id,''),?), "
            "section_id=COALESCE(NULLIF(section_id,''),?), section_name=COALESCE(NULLIF(section_name,''),?), "
            "paper_label=COALESCE(NULLIF(paper_label,''),?), start_time=COALESCE(NULLIF(start_time,''),?) "
            "WHERE id=?", tuple(bundle.get(k, "") for k in (
                "course_id", "section_id", "section_name", "paper_label", "start_time")) + (paper_id,))
        existing = conn.execute(
            "SELECT id FROM gx_q_imports WHERE paper_id=? AND digest=?", (paper_id, digest)
        ).fetchone()
        if existing:
            return {"paper_id": paper_id, "import_id": existing[0], "inserted": False,
                    "questions": len(bundle["questions"])}
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
            # **主观题只入题目、不入作答**（用户 2026-11-24 方案 A 定案）。
            #
            # 原话：「客观题的题目和答案要入，主观题，题目要入，答案暂不考虑入，
            #        因为每个人不一样」。
            #
            # 所以主观题：
            #   - `gx_q_items` 照写（题目共享，下次遇到同题 AI 知道它出现过）；
            #   - `gx_q_answers` 写一行**空作答**（picked/standard 全 NULL）——
            #     为什么不干脆不写：`gx_q_answers.import_id` 是这次快照的一部分，
            #     缺行会让「答卷完整性」校验（题数对不上）失败。
            #     `lookup_answer` 只认 `standard_source='site'`，NULL 自然不会被复用。
            if not is_objective(q.get("type_code", "")):
                a = {}
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


def backfill_classification(conn: sqlite3.Connection, *, account_label: str,
                           exam_start_id: str, course_id: str = "",
                           section_id: str = "", section_name: str = "") -> bool:
    """给已入库的答卷补分类信息（只填空值，不覆盖已有值）；返回是否改了行。

    用于 2026-10-30 之前的快照——那时解析器还没采集 `course_id` / `section_id`。
    与重新导入的区别：不动 `gx_q_imports` / `gx_q_answers`，不会多出重复证据。
    """
    with conn:
        cur = conn.execute(
            "UPDATE gx_q_papers SET course_id=COALESCE(NULLIF(course_id,''),?), "
            "section_id=COALESCE(NULLIF(section_id,''),?), "
            "section_name=COALESCE(NULLIF(section_name,''),?) "
            "WHERE account_label=? AND exam_start_id=?",
            (course_id, section_id, section_name, account_label, exam_start_id))
    return cur.rowcount > 0


def bank_items(conn: sqlite3.Connection, *, limit: int = 0) -> list[dict]:
    """按指纹归并题目：同一道题在不同节、不同题号下只出现一次（检索与导出用）。

    归并键必须是 `fingerprint` 而不是题号：同一道题在不同节里的 `platform_qid` 会不同，
    按题号去重会把同一题导出成多行（2026-10-30 发现的设计缺口）。
    """
    sql = ("SELECT fingerprint, MIN(id) AS item_id, type_code, stem, COUNT(*) AS occurrences, "
           "GROUP_CONCAT(DISTINCT platform_qid) AS platform_qids "
           "FROM gx_q_items GROUP BY fingerprint ORDER BY item_id")
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [dict(r) for r in conn.execute(sql)]


def bank_counts(conn: sqlite3.Connection) -> dict:
    """本地统计不访问平台；作答条数不包含重复采集的历史快照。"""
    return {
        "papers": conn.execute("SELECT COUNT(*) FROM gx_q_papers").fetchone()[0],
        "question_versions": conn.execute("SELECT COUNT(*) FROM gx_q_items").fetchone()[0],
        "attempt_questions": conn.execute("SELECT COUNT(*) FROM gx_q_paper_items").fetchone()[0],
        "snapshots": conn.execute("SELECT COUNT(*) FROM gx_q_imports").fetchone()[0],
    }


def _norm(text: object) -> str:
    return " ".join(str(text or "").split())


# ---------------------------------------------------------------- 客观题共享闸门
# 用户 2026-11-11 定案：「客观题（单选/多选/判断）如果有账号做过、提交了，
# 答案正确可以直接收入库中，其它账号直接从题库调用；主观题就不行」。
#
# 题型代码来自平台自带的 `tx_arr`（见 work.parse_tx_arr），名称不必我们维护。
# 这里只认**已实测**的客观题代码；未列出的（含简答/论述/操作题）一律不进共享池。
# 已实测：2 = 单选（allowed_types 默认值）。多选/判断等放行后把代码加进这里即可，
# 这就是「题库结构留扩展余地」的落点。
#: 客观题（答案唯一、可跨账号复用）：单选 2 / 多选 3 / 判断 4。
#: 2026-11-27 修正：原来只有 {"2"}，判断题被当成主观题
#: → 不进共享库、还被标成 subjective 待确认。判断题答案同样唯一。
OBJECTIVE_TYPE_CODES = frozenset({"2", "3", "4"})


def is_objective(type_code: str) -> bool:
    """该题型是不是客观题（答案唯一、可跨账号共享标准答案）。"""
    return str(type_code or "").strip() in OBJECTIVE_TYPE_CODES


def objective_registry(conn: sqlite3.Connection, *, type_code: str,
                       standard_source: str | None) -> bool:
    """判断一份答案证据**能否进入共享答案池**。

    三条同时满足：
    ① 是客观题（主观题禁止共享，用户定案）；
    ② `standard_source == 'site'`（平台判分页给出的客观标准答案，不是 AI 推的）；
    ③ 查库侧 `lookup_answer` 本来也只认 site，这里保持一致。

    ⚠ 注意「做对了才收入」：用户说的是答案**正确**才入库。本函数只判题型与来源，
    `is_correct` 的真伪由调用方（导入流水线）按判分页决定——见
    `import_bundle` 的 `answer.is_correct` 字段。两者分开，避免把
    「答错的题的标准答案」误当成「这题不能共享」（答错时标准答案依然可信）。
    """
    return is_objective(type_code) and standard_source == "site"


def lookup_answer(conn: sqlite3.Connection, *, stem: str, options: list[str]) -> dict | None:
    """按题干 + 选项查题库里的**平台标准答案**（在线作答用）。

    只认 `standard_source='site'`：那是平台判分给出的客观答案，跟谁做的没关系，
    所以可以给别的账号用。`ai` 来源的答案不外借——那是某个账号的推理结果，
    按 PLAN-GXKS §1.1 N3「题干可共用、答案仍按账号隔离」处理。

    选项文本参与校验：题库里存的是选项 key（a/b/c/d），一旦平台改了选项顺序，
    同一个 key 指向的内容就变了，宁可当未命中也不能填错。

    命中条件：题干完全相同，且存下的选项序列与本次题面逐项一致。
    """
    stem_norm = _norm(stem)
    opts_norm = [_norm(o) for o in options or []]
    if not stem_norm or not opts_norm:
        return None
    item_rows = conn.execute(
        "SELECT id, type_code FROM gx_q_items WHERE stem = ? ORDER BY id DESC", (stem_norm,)
    ).fetchall()
    for item_id, type_code in item_rows:
        # ⚠ 主观题禁止共享（用户 2026-11-11 定案）：即使判分页里带 site 标准答案，
        # 也不给别的账号用——简答/论述答案不唯一，跨账号复用会误导。
        if not is_objective(type_code):
            continue
        part_rows = conn.execute(
            "SELECT part_key, content_json FROM gx_q_parts "
            "WHERE item_id = ? AND kind = 'option' ORDER BY ordinal", (item_id,)
        ).fetchall()
        stored = [_norm(json.loads(r[1]).get("text")) for r in part_rows]
        # ⚠ 2026-11-28（1.6 判断题实跑）：判断题的「判分页」与「答题页」
        # 选项**展示顺序不一致** —— 判分页 <label>1、错</label><label>2、对</label>，
        # 而答题页 radio 是「对(value=1)」「错(value=0)」。
        # 严格按顺序比会永远判不中，题库里明明有答案却「题库命中 0」。
        # 所以：**判断题按集合比**（对/错 只有两个，顺序无意义，答案存的是 value 不靠下标）。
        # 单选/多选仍严格按顺序（选项顺序变了字母含义就变了）。
        if str(type_code).strip() in ("4",):
            if sorted(stored) != sorted(opts_norm):
                continue
        elif stored != opts_norm:
            continue
        answer = conn.execute(
            "SELECT a.standard_json FROM gx_q_answers a "
            "JOIN gx_q_imports im ON im.id = a.import_id "
            "JOIN gx_q_paper_items pi ON pi.paper_id = im.paper_id AND pi.ordinal = a.ordinal "
            "WHERE pi.item_id = ? AND a.standard_source = 'site' "
            "AND a.standard_json IS NOT NULL ORDER BY a.import_id DESC LIMIT 1",
            (item_id,),
        ).fetchone()
        if answer is None:
            continue
        data = json.loads(answer[0])
        keys = [str(k) for k in (data.get("keys") or []) if str(k)]
        if keys:
            return {"item_id": item_id, "keys": keys, "source": "bank",
                    "text": _norm(data.get("text"))}
    return None
