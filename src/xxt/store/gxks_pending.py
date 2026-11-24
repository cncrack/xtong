"""G-8 待人工确认队列（2026-11-11 用户定稿）。

## 为什么单独一张表

`gx_ops` 是**只追加流水**，改不了状态。队列需要
`pending → verified / skipped / expired / submitted` 的状态迁移，
还要记「谁、何时、怎么决定」—— 流水表做不了。

## 一行 = 一题

用户原话：「要明确告诉是哪个章节哪里出问题了」「可以跳过继续下一题」。
所以粒度到 `question_ordinal`，不是整卷。这样某一题不确定时，
其它题照常、任务继续，不阻塞。

## 留痕三类（全部可查）

1. 产生：`add()` 建 `pending` + 调用方写 `gx_ops(work-review)`
2. 决定：`decide()` 更新 `decided_by/at/note` + 调用方写 `gx_ops(work-verify/work-skip)`
3. 终态：`gx_ops(work-submit)` / `expire_stale()`

## 不做的事

- 不自动交卷（G-9 第 1 级）
- 不偷偷跳过、不留痕就改状态
- 不访问平台（纯本地表）
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

# 状态机：pending 是唯一可再决定的状态；其余为终态。
PENDING = "pending"
VERIFIED = "verified"
SKIPPED = "skipped"
EXPIRED = "expired"
SUBMITTED = "submitted"
TERMINAL = (VERIFIED, SKIPPED, EXPIRED, SUBMITTED)

# 风险原因（与 gxks_work.py 的 review 字段同名）
REASONS = (
    "ai_used",          # 用了 AI 生成的答案，无平台标准答案
    "no_site_answer",   # 题库里没有该题的 site 标准答案
    "low_confidence",   # 模型自报置信度低（**仅供展示，不作闸门**）
    "type_blocked",     # 题型未实测，整卷拒填
    "check_mismatch",   # 回填后回读不一致
    "subjective",       # 主观题，禁止自动入库/共享
)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def add(
    conn: sqlite3.Connection,
    *,
    account_label: str,
    question_ordinal: int,
    reason: str,
    course_id: str = "",
    section_id: str = "",
    section_name: str = "",
    exercise_id: str = "",
    type_code: str = "",
    stem: str = "",
    risk: dict[str, Any] | None = None,
    work_file: str = "",
    op_id: int | None = None,
    at: str | None = None,
) -> int:
    """建一条待确认记录，返回自增 id。

    同一 `(account_label, exercise_id, question_ordinal)` 若已有 `pending`，
    **不重复建**（4 秒轮询/重跑不会产生一堆重复行），只更新风险与时间。
    """
    if reason not in REASONS:
        raise ValueError(f"未知风险原因：{reason}")
    now = at or _now()
    payload = json.dumps(risk or {}, ensure_ascii=False, default=str)
    with conn:
        row = conn.execute(
            "SELECT id FROM gx_pending WHERE account_label=? AND exercise_id=? "
            "AND question_ordinal=? AND status=?",
            (account_label, exercise_id, int(question_ordinal), PENDING),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE gx_pending SET reason=?, type_code=?, stem=?, risk_json=?, "
                "work_file=?, op_id=COALESCE(?,op_id), updated_at=? WHERE id=?",
                (reason, type_code, stem, payload, work_file, op_id, now, row[0]),
            )
            return int(row[0])
        cur = conn.execute(
            "INSERT INTO gx_pending(created_at,updated_at,account_label,course_id,"
            "section_id,section_name,exercise_id,question_ordinal,type_code,stem,"
            "reason,risk_json,work_file,status,op_id) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (now, now, account_label, course_id, section_id, section_name,
             exercise_id, int(question_ordinal), type_code, stem, reason,
             payload, work_file, PENDING, op_id),
        )
        return int(cur.lastrowid or 0)


def decide(
    conn: sqlite3.Connection,
    pending_id: int,
    *,
    status: str,
    decided_by: str,
    note: str = "",
    at: str | None = None,
) -> bool:
    """人工决定：核对通过 / 跳过 / 转人工。返回是否真的改了行。

    只允许从 `pending` 出发；终态不可再改（防覆盖已有决定）。
    """
    if status not in TERMINAL:
        raise ValueError(f"非法终态：{status}")
    with conn:
        cur = conn.execute(
            "UPDATE gx_pending SET status=?, decided_by=?, decided_at=?, "
            "decision_note=?, updated_at=? WHERE id=? AND status=?",
            (status, decided_by, at or _now(), note, at or _now(),
             int(pending_id), PENDING),
        )
        return cur.rowcount > 0


def verify_by_site_answer(
    conn: sqlite3.Connection,
    *,
    account_label: str,
    exercise_id: str,
    ordinals: list[int],
    at: str | None = None,
) -> int:
    """平台判分页给出了标准答案 → 把对应待确认项标 `VERIFIED`。返回改动条数。

    ## 为什么可以自动核验（2026-11-24 用户定案）

    用户原话：「平台判分页的标准答案 = 平台官方答案，对的，这个更好」。

    与 AI 自报的 `confidence` 不同，判分页的「标准答案」是**平台自己判过的**，
    不需要人工再核一遍。所以交卷入库成功后，这些题不必继续挂在待确认清单上。

    ⚠ 只动**仍是 pending** 的行（`WHERE status=PENDING`）：已经人工决定过的不覆盖。
    """
    if not ordinals:
        return 0
    marks = ",".join("?" * len(ordinals))
    with conn:
        cur = conn.execute(
            "UPDATE gx_pending SET status=?, decided_by=?, decided_at=?, "
            "decision_note=?, updated_at=? "
            "WHERE account_label=? AND exercise_id=? AND status=? "
            f"AND question_ordinal IN ({marks})",
            (VERIFIED, "site-answer", at or _now(),
             "平台判分页标准答案（官方答案，无需人工再核）", at or _now(),
             account_label, str(exercise_id), PENDING, *[int(o) for o in ordinals]),
        )
        return cur.rowcount


def expire_stale(conn: sqlite3.Connection, *, hours: float, at: str | None = None) -> int:
    """超时标 `expired`（留痕，不删行）。返回过期条数。

    由面板按配置调用；**不自动提交、不自动跳过任务**。
    """
    if hours <= 0:
        return 0
    from datetime import timedelta

    cutoff = (datetime.strptime(at or _now(), "%Y-%m-%d %H:%M:%S")
              - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    with conn:
        cur = conn.execute(
            "UPDATE gx_pending SET status=?, updated_at=?, "
            "decision_note=decision_note || ? WHERE status=? AND created_at < ?",
            (EXPIRED, at or _now(), f" [超时 {hours:g}h 自动过期]", PENDING, cutoff),
        )
        return cur.rowcount


def list_pending(
    conn: sqlite3.Connection,
    *,
    account_label: str | None = None,
    status: str | None = PENDING,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """查询队列。`status=None` 取全部状态（含已决定，用于「留痕可查」）。"""
    limit = max(1, min(int(limit), 500))
    where: list[str] = []
    args: list[Any] = []
    if account_label:
        where.append("account_label=?")
        args.append(account_label)
    if status is not None:
        where.append("status=?")
        args.append(status)
    sql = "SELECT * FROM gx_pending"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    rows = conn.execute(sql, args).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        try:
            d["risk"] = json.loads(d.pop("risk_json") or "{}")
        except (ValueError, TypeError):
            d["risk"] = {}
            d.pop("risk_json", None)
        out.append(d)
    return out


def pending_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """按账号统计待确认数（面板红点 + 多账号分组用）。一次 GROUP BY，不逐个查。"""
    rows = conn.execute(
        "SELECT account_label, COUNT(*) AS n FROM gx_pending WHERE status=? "
        "GROUP BY account_label", (PENDING,)
    ).fetchall()
    out = {str(r[0]): int(r[1]) for r in rows}
    out["__total__"] = sum(int(r[1]) for r in rows)
    return out
