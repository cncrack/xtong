"""学习安排（plan）：把"某门课"统一排给一批账号。

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
    """全部学习安排（含账号列表与人数）。"""
    plans = [dict(r) for r in conn.execute(
        "SELECT id, name, course_name, course_pid, enabled, created_at"
        " FROM plans ORDER BY id")]
    for p in plans:
        p["account_ids"] = [r["account_id"] for r in conn.execute(
            "SELECT account_id FROM plan_accounts WHERE plan_id=? ORDER BY account_id",
            (p["id"],))]
        p["enabled"] = bool(p["enabled"])
    return plans


def busy_accounts(conn: sqlite3.Connection, exclude_plan: int | None = None) -> dict[int, str]:
    """已在**启用中**学习安排里的账号 → {account_id: 学习安排名}。

    同一账号不允许同时挂在两个启用学习安排上：否则"今天刷哪门课"没有确定答案。
    """
    sql = ("SELECT pa.account_id, p.name FROM plan_accounts pa"
           " JOIN plans p ON p.id = pa.plan_id WHERE p.enabled = 1")
    args: list[Any] = []
    if exclude_plan is not None:
        sql += " AND p.id != ?"
        args.append(exclude_plan)
    return {r["account_id"]: r["name"] for r in conn.execute(sql, args)}


def create_plan(conn: sqlite3.Connection, name: str, course_name: str,
                account_ids: list[int], enabled: bool = True,
                course_pid: str | None = None) -> dict[str, Any]:
    """新建学习安排。返回 {"ok": True, "id": N} 或 {"ok": False, "error": ...}。

    `course_pid` = 平台课程ID（跨账号唯一锚点）：新学习安排优先按它匹配；缺省（老式
    仅课名）仍按课名匹配。二者都存，pid 只用于运行匹配，课名仅显示。
    """
    name, course_name = (name or "").strip(), (course_name or "").strip()
    course_pid = (course_pid or "").strip() or None
    if not name or not course_name:
        return {"ok": False, "error": "学习安排名与课程名都不能为空"}
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
                    "error": f"这些账号已在其他启用中的学习安排里：{detail}。"
                             "请先停用原学习安排，或把它们从本学习安排剔除"}
    cur = conn.execute(
        "INSERT INTO plans(name, course_name, course_pid, enabled, extra_json, created_at)"
        " VALUES(?,?,?,?,?,?)",
        (name, course_name, course_pid, 1 if enabled else 0, json.dumps({}, ensure_ascii=False),
         time.strftime("%Y-%m-%dT%H:%M:%S")))
    pid = int(cur.lastrowid)
    conn.executemany("INSERT OR IGNORE INTO plan_accounts(plan_id, account_id) VALUES(?,?)",
                     [(pid, i) for i in ids])
    conn.commit()
    return {"ok": True, "id": pid}


def set_enabled(conn: sqlite3.Connection, plan_id: int, enabled: bool) -> dict[str, Any]:
    """启用/停用学习安排。启用时同样要检查账号冲突。"""
    row = conn.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
    if row is None:
        return {"ok": False, "error": "学习安排不存在"}
    if enabled:
        ids = [r["account_id"] for r in conn.execute(
            "SELECT account_id FROM plan_accounts WHERE plan_id=?", (plan_id,))]
        busy = busy_accounts(conn, exclude_plan=plan_id)
        clash = {i: busy[i] for i in ids if i in busy}
        if clash:
            detail = "、".join(f"#{i}（已在《{n}》）" for i, n in clash.items())
            return {"ok": False, "error": f"无法启用：{detail} 仍在其他启用学习安排里"}
    conn.execute("UPDATE plans SET enabled=? WHERE id=?",
                 (1 if enabled else 0, plan_id))
    conn.commit()
    return {"ok": True}


def update_plan(conn: sqlite3.Connection, plan_id: int, *, name: str | None = None,
                course_name: str | None = None, account_ids: list[int] | None = None,
                enabled: bool | None = None, course_pid: str | None = None) -> dict[str, Any]:
    """改已建学习安排：名称 / 课程 / 账号清单 / 启停（None = 该项不变）。

    `course_pid`：传入即覆盖（传空串 = 清空，退回按课名匹配）；None = 不动。
    账号是关键：用户在"编辑学习安排"里加号减号都走这里。启用态下要重查冲突，
    但**排除自己**（否则自己学习安排里的账号会跟自己撞车）。
    """
    row = conn.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
    if row is None:
        return {"ok": False, "error": "学习安排不存在"}
    new_name = row["name"] if name is None else (name or "").strip()
    new_course = row["course_name"] if course_name is None else (course_name or "").strip()
    new_pid = row["course_pid"] if course_pid is None else ((course_pid or "").strip() or None)
    if not new_name or not new_course:
        return {"ok": False, "error": "学习安排名与课程名都不能为空"}
    if account_ids is None:
        ids = [r["account_id"] for r in conn.execute(
            "SELECT account_id FROM plan_accounts WHERE plan_id=?", (plan_id,))]
    else:
        ids = sorted({int(a) for a in account_ids})
        if not ids:
            return {"ok": False, "error": "至少保留一个账号（要清空请直接删除学习安排）"}
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
                    "error": f"这些账号已在其他启用中的学习安排里：{detail}。"
                             "请先停用原学习安排，或把它们从本学习安排剔除"}
    conn.execute("UPDATE plans SET name=?, course_name=?, course_pid=?, enabled=? WHERE id=?",
                 (new_name, new_course, new_pid, 1 if new_enabled else 0, plan_id))
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


def course_for_account(conn: sqlite3.Connection, account_id: int) -> dict[str, Any] | None:
    """该账号此刻应刷的课程（来自启用中的学习安排）→ {"name","pid"}；无学习安排则 None → 走课程轮转。

    返回 pid（平台课程ID）而不只是课名：调用方据此精确匹配课程，避免同名歧义。
    老学习安排 pid 为 None → 调用方退回按课名匹配。
    """
    row = conn.execute(
        "SELECT p.course_name, p.course_pid FROM plans p"
        " JOIN plan_accounts pa ON pa.plan_id = p.id"
        " WHERE p.enabled = 1 AND pa.account_id = ? ORDER BY p.id LIMIT 1",
        (account_id,)).fetchone()
    if row is None:
        return None
    return {"name": row["course_name"], "pid": row["course_pid"]}


def account_has_course(conn: sqlite3.Connection, account_id: int,
                       pid: str | None = None, name: str | None = None) -> bool:
    """该账号是否**真抓到了**这门课（pid 优先，其次课名模糊）。

    为什么要这个（2026-10-26 修）：学习安排指定的课若这个号没抓过，仍会 spawn
    子进程，而面板已返回 `ok=True` → auto 记为“今日已触发”，该号当天白干一次。
    启动前本地核一下（零平台访问），缺课就直接跳过、不消耗当日机会。
    """
    pid = (pid or "").strip()
    if pid:
        return conn.execute(
            "SELECT 1 FROM courses WHERE account_id=? AND platform_course_id=? LIMIT 1",
            (account_id, pid)).fetchone() is not None
    name = (name or "").strip()
    if not name:
        return False
    return conn.execute(
        "SELECT 1 FROM courses WHERE account_id=? AND name LIKE ? LIMIT 1",
        (account_id, f"%{name}%")).fetchone() is not None
