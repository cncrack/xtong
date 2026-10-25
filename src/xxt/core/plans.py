"""批次（plan）：把"某门课"统一排给一批账号。

**不含任何时间字段**——开跑时刻仍由每账号自己的 `start_at` 与学习窗口决定，
因此"同批任务"天然错峰（用户 2026-10-26 要求：可以统一起任务，不能同一时刻开刷）。

与"账号卡片 ▶"的关系（防多开的关键）：
本模块**只回答"这个账号今天该刷哪门课"**，绝不自己拉进程。真正的启动统一走
面板 `/api/study/start` —— 与手动按钮共用同一把锁（进程内 `_JOBS` +
`data/run/<id>.json` 标记）。哪个先开，另一个就会收到"已有进行中的会话"。
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any


def list_plans(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """全部批次（含账号列表与人数）。"""
    plans = [dict(r) for r in conn.execute(
        "SELECT id, name, course_name, enabled, created_at FROM plans ORDER BY id")]
    for p in plans:
        p["account_ids"] = [r["account_id"] for r in conn.execute(
            "SELECT account_id FROM plan_accounts WHERE plan_id=? ORDER BY account_id",
            (p["id"],))]
        p["enabled"] = bool(p["enabled"])
    return plans


def busy_accounts(conn: sqlite3.Connection, exclude_plan: int | None = None) -> dict[int, str]:
    """已在**启用中**批次里的账号 → {account_id: 批次名}。

    同一账号不允许同时挂在两个启用批次上：否则"今天刷哪门课"没有确定答案。
    """
    sql = ("SELECT pa.account_id, p.name FROM plan_accounts pa"
           " JOIN plans p ON p.id = pa.plan_id WHERE p.enabled = 1")
    args: list[Any] = []
    if exclude_plan is not None:
        sql += " AND p.id != ?"
        args.append(exclude_plan)
    return {r["account_id"]: r["name"] for r in conn.execute(sql, args)}


def create_plan(conn: sqlite3.Connection, name: str, course_name: str,
                account_ids: list[int], enabled: bool = True) -> dict[str, Any]:
    """新建批次。返回 {"ok": True, "id": N} 或 {"ok": False, "error": ...}。"""
    name, course_name = (name or "").strip(), (course_name or "").strip()
    if not name or not course_name:
        return {"ok": False, "error": "批次名与课程名都不能为空"}
    ids = sorted({int(a) for a in account_ids or []})
    if not ids:
        return {"ok": False, "error": "至少勾选一个账号"}
    exist = {r["id"] for r in conn.execute(
        f"SELECT id FROM accounts WHERE id IN ({','.join('?' * len(ids))})", ids)}
    missing = [i for i in ids if i not in exist]
    if missing:
        return {"ok": False, "error": f"账号不存在：{missing}"}
    if enabled:
        busy = busy_accounts(conn)
        clash = {i: busy[i] for i in ids if i in busy}
        if clash:
            detail = "、".join(f"#{i}（已在《{n}》）" for i, n in clash.items())
            return {"ok": False,
                    "error": f"这些账号已在其他启用中的批次里：{detail}。"
                             "请先停用原批次，或把它们从本批次剔除"}
    cur = conn.execute(
        "INSERT INTO plans(name, course_name, enabled, extra_json, created_at)"
        " VALUES(?,?,?,?,?)",
        (name, course_name, 1 if enabled else 0, json.dumps({}, ensure_ascii=False),
         time.strftime("%Y-%m-%dT%H:%M:%S")))
    pid = int(cur.lastrowid)
    conn.executemany("INSERT OR IGNORE INTO plan_accounts(plan_id, account_id) VALUES(?,?)",
                     [(pid, i) for i in ids])
    conn.commit()
    return {"ok": True, "id": pid}


def set_enabled(conn: sqlite3.Connection, plan_id: int, enabled: bool) -> dict[str, Any]:
    """启用/停用批次。启用时同样要检查账号冲突。"""
    row = conn.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
    if row is None:
        return {"ok": False, "error": "批次不存在"}
    if enabled:
        ids = [r["account_id"] for r in conn.execute(
            "SELECT account_id FROM plan_accounts WHERE plan_id=?", (plan_id,))]
        busy = busy_accounts(conn, exclude_plan=plan_id)
        clash = {i: busy[i] for i in ids if i in busy}
        if clash:
            detail = "、".join(f"#{i}（已在《{n}》）" for i, n in clash.items())
            return {"ok": False, "error": f"无法启用：{detail} 仍在其他启用批次里"}
    conn.execute("UPDATE plans SET enabled=? WHERE id=?",
                 (1 if enabled else 0, plan_id))
    conn.commit()
    return {"ok": True}


def update_plan(conn: sqlite3.Connection, plan_id: int, *, name: str | None = None,
                course_name: str | None = None, account_ids: list[int] | None = None,
                enabled: bool | None = None) -> dict[str, Any]:
    """改已建批次：名称 / 课程 / 账号清单 / 启停（None = 该项不变）。

    账号是关键：用户在"编辑批次"里加号减号都走这里。启用态下要重查冲突，
    但**排除自己**（否则自己批次里的账号会跟自己撞车）。
    """
    row = conn.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
    if row is None:
        return {"ok": False, "error": "批次不存在"}
    new_name = row["name"] if name is None else (name or "").strip()
    new_course = row["course_name"] if course_name is None else (course_name or "").strip()
    if not new_name or not new_course:
        return {"ok": False, "error": "批次名与课程名都不能为空"}
    if account_ids is None:
        ids = [r["account_id"] for r in conn.execute(
            "SELECT account_id FROM plan_accounts WHERE plan_id=?", (plan_id,))]
    else:
        ids = sorted({int(a) for a in account_ids})
        if not ids:
            return {"ok": False, "error": "至少保留一个账号（要清空请直接删除批次）"}
        exist = {r["id"] for r in conn.execute(
            f"SELECT id FROM accounts WHERE id IN ({','.join('?' * len(ids))})", ids)}
        missing = [i for i in ids if i not in exist]
        if missing:
            return {"ok": False, "error": f"账号不存在：{missing}"}
    new_enabled = bool(row["enabled"]) if enabled is None else bool(enabled)
    if new_enabled:
        busy = busy_accounts(conn, exclude_plan=plan_id)
        clash = {i: busy[i] for i in ids if i in busy}
        if clash:
            detail = "、".join(f"#{i}（已在《{n}》）" for i, n in clash.items())
            return {"ok": False,
                    "error": f"这些账号已在其他启用中的批次里：{detail}。"
                             "请先停用原批次，或把它们从本批次剔除"}
    conn.execute("UPDATE plans SET name=?, course_name=?, enabled=? WHERE id=?",
                 (new_name, new_course, 1 if new_enabled else 0, plan_id))
    if account_ids is not None:
        conn.execute("DELETE FROM plan_accounts WHERE plan_id=?", (plan_id,))
        conn.executemany("INSERT OR IGNORE INTO plan_accounts(plan_id, account_id)"
                         " VALUES(?,?)", [(plan_id, i) for i in ids])
    conn.commit()
    return {"ok": True, "id": plan_id, "account_ids": ids}


def delete_plan(conn: sqlite3.Connection, plan_id: int) -> bool:
    conn.execute("DELETE FROM plans WHERE id=?", (plan_id,))  # 关联行由外键级联删除
    conn.commit()
    return True


def course_for_account(conn: sqlite3.Connection, account_id: int) -> str | None:
    """该账号此刻应刷的课程（来自启用中的批次）；无批次则 None → 走课程轮转。"""
    row = conn.execute(
        "SELECT p.course_name FROM plans p"
        " JOIN plan_accounts pa ON pa.plan_id = p.id"
        " WHERE p.enabled = 1 AND pa.account_id = ? ORDER BY p.id LIMIT 1",
        (account_id,)).fetchone()
    return row["course_name"] if row else None
