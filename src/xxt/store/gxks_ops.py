"""第二平台操作日志：面板「操作记录」的唯一写入点。

为什么单独一张表：超星的 `run_logs` 绑 `accounts(id)` + 章节外键，新站账号是
`account_label`（一个浏览器 profile 一个标签），两侧主键类型不同；混进同一张表
会让新站日志在超星面板里变成孤儿行，也让超星的结构改动牵连新站。因此在新站
专用的 `gxks_questions.db` 里单独建 `gx_ops`。

约定（用户 2026-10-30 明确要求「日志也要记录操作记录」）：
- 所有会改真实状态的按钮（开始学习 / 做练习 / 停止 / 交卷）都必须留痕；
- `detail` 只放人可读的一句话，`payload_json` 放结构化细节（逐题答案等）；
- `code` 带机器码（risk / network / structure / login / quota），面板按码渲染，
  中文关键词只作兜底，避免"看日志猜原因"。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

# 面板「操作记录」用的动作词表。新增动作先加到这里，避免各写各的字符串。
ACTIONS = (
    "study-start",   # 开始学习某节
    "study-done",    # 某节学时达标并收尾
    "study-stop",    # 主动停止学习
    "work-open",     # 打开某节练习（新建答卷）
    "work-fill",     # 题目已回填（不提交）
    "work-submit",   # 交卷（人工或 auto_submit）
    "sections-read", # 读了一次节列表（面板「刷新」）
    "stop",          # 通用停止（面板 ■ 按钮）
    "error",         # 任何失败；detail 写原因，code 带机器码
)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def record_op(
    conn: sqlite3.Connection,
    *,
    account_label: str,
    action: str,
    detail: str,
    result: str = "ok",
    section_id: str | None = None,
    section_name: str | None = None,
    exercise_id: str | None = None,
    code: str | None = None,
    payload: Any = None,
    at: str | None = None,
) -> int:
    """写一条操作记录，返回自增 id。

    `action` 不在 `ACTIONS` 里不报错也不静默改写——写进去但不拦截，
    由面板层决定怎么显示；这样新增动作不必等这张表先改（日志比规范重要：
    丢一条记录比多一个陌生词更糟）。
    """
    payload_json = None
    if payload is not None:
        payload_json = json.dumps(payload, ensure_ascii=False, default=str)
    with conn:
        cur = conn.execute(
            "INSERT INTO gx_ops(at, account_label, action, result, section_id,"
            " section_name, exercise_id, detail, code, payload_json)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (at or _now(), account_label, action, result, section_id,
             section_name, exercise_id, detail, code, payload_json),
        )
    return int(cur.lastrowid or 0)


def recent_ops(
    conn: sqlite3.Connection,
    *,
    account_label: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """按时间倒序取操作记录；`account_label=None` 取全部账号。"""
    limit = max(1, min(int(limit), 500))
    if account_label:
        rows = conn.execute(
            "SELECT * FROM gx_ops WHERE account_label=? ORDER BY id DESC LIMIT ?",
            (account_label, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM gx_ops ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        raw = item.get("payload_json")
        if raw:
            try:
                item["payload"] = json.loads(raw)
            except json.JSONDecodeError:
                item["payload"] = None
        else:
            item["payload"] = None
        item.pop("payload_json", None)
        out.append(item)
    return out
