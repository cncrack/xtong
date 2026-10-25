"""学习统计（面板「📈 统计」页）：按年级/专业/学期/账号聚合战果。

画像（年级/专业/开学期）来自 `account_profiles`——用户 2026-10-26 要求统计能分清
"哪个年级哪个专业哪个开学期"，因此单独成列，不塞进 `note`（否则无法按维度聚合）。

口径与额度一致（复用 `core/quota`）：视频数 = `video-done` 条数，时长 = 详情尾部秒数累加。
`account_id=0` 的行（批次类全局日志）不参与统计。
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timedelta
from typing import Any

from .quota import sum_done_seconds

DIMENSIONS: dict[str, str] = {"grade": "年级", "major": "专业", "term": "学期",
                              "account": "账号"}
UNSET = "未标注"


def get_profile(conn: sqlite3.Connection, account_id: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT grade, major, term FROM account_profiles WHERE account_id=?",
        (account_id,)).fetchone()
    if row is None:
        return {"account_id": account_id, "grade": "", "major": "", "term": ""}
    return {"account_id": account_id, "grade": row["grade"] or "",
            "major": row["major"] or "", "term": row["term"] or ""}


def set_profile(conn: sqlite3.Connection, account_id: int, *, grade: str = "",
                major: str = "", term: str = "") -> dict[str, Any]:
    """写入/更新画像（留空即清空该维度，统计里会归入"未标注"）。"""
    if conn.execute("SELECT 1 FROM accounts WHERE id=?", (account_id,)).fetchone() is None:
        return {"ok": False, "error": f"账号 #{account_id} 不存在"}
    conn.execute(
        "INSERT INTO account_profiles(account_id, grade, major, term, updated_at)"
        " VALUES(?,?,?,?,datetime('now'))"
        " ON CONFLICT(account_id) DO UPDATE SET grade=excluded.grade,"
        " major=excluded.major, term=excluded.term, updated_at=excluded.updated_at",
        (account_id, (grade or "").strip() or None, (major or "").strip() or None,
         (term or "").strip() or None))
    conn.commit()
    return {"ok": True, **get_profile(conn, account_id)}


def list_profiles(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """全部账号 + 画像（未填的返回空串，前端可直接编辑）。"""
    out = []
    for r in conn.execute(
            "SELECT a.id, a.label, a.status, p.grade, p.major, p.term"
            " FROM accounts a LEFT JOIN account_profiles p ON p.account_id = a.id"
            " ORDER BY a.id"):
        out.append({"account_id": r["id"], "label": r["label"], "status": r["status"],
                    "grade": r["grade"] or "", "major": r["major"] or "",
                    "term": r["term"] or ""})
    return out


def aggregate(conn: sqlite3.Connection, *, by: str = "grade", days: int = 7) -> dict[str, Any]:
    """按维度聚合。days<=0 表示全部历史；否则统计含今天在内的最近 days 天。"""
    if by not in DIMENSIONS:
        return {"ok": False, "error": f"未知维度：{by}"}
    cutoff = ""
    if days and days > 0:
        cutoff = (datetime.now() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    profiles = {r["account_id"]: r for r in list_profiles(conn)}
    sql = ("SELECT l.account_id, l.event, l.detail FROM run_logs l"
           " WHERE l.account_id != 0 AND l.event IN ('video-done', 'work-start')")
    args: list[Any] = []
    if cutoff:
        sql += " AND l.ts >= ?"
        args.append(cutoff)

    buckets: dict[str, dict[str, Any]] = {}
    for r in conn.execute(sql, args):
        aid = int(r["account_id"])
        prof = profiles.get(aid) or {}
        if by == "account":
            key = f"#{aid} {prof.get('label') or ''}".strip()
        else:
            key = (prof.get(by) or "").strip() or UNSET
        b = buckets.setdefault(key, {"key": key, "accounts": set(), "videos": 0,
                                     "seconds": 0, "works": 0})
        b["accounts"].add(aid)
        if r["event"] == "video-done":
            b["videos"] += 1
            b["seconds"] += _seconds(r["detail"])
        else:
            b["works"] += 1

    total = {"accounts": set(), "videos": 0, "seconds": 0, "works": 0}
    rows = []
    for b in buckets.values():
        total["accounts"] |= b["accounts"]
        total["videos"] += b["videos"]
        total["seconds"] += b["seconds"]
        total["works"] += b["works"]
        rows.append({"key": b["key"], "accounts": len(b["accounts"]),
                     "videos": b["videos"], "minutes": round(b["seconds"] / 60, 1),
                     "works": b["works"]})
    rows.sort(key=lambda x: (-x["videos"], -x["minutes"]))
    return {"ok": True, "by": by, "by_name": DIMENSIONS[by], "days": days,
            "since": cutoff or "全部", "rows": rows,
            "total": {"accounts": len(total["accounts"]), "videos": total["videos"],
                      "minutes": round(total["seconds"] / 60, 1), "works": total["works"]}}


def _seconds(detail: str | None) -> int:
    """单条 video-done 的观看秒数（`课程｜知识点｜123s`），解析不出算 0。"""
    return sum_done_seconds([detail or ""])


def today_summary(conn: sqlite3.Connection) -> dict[str, int]:
    """今日总览（顶部指标用；不区分画像）。"""
    today = time.strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT detail FROM run_logs WHERE event='video-done' AND ts LIKE ?",
        (f"{today}%",)).fetchall()
    return {"videos": len(rows),
            "minutes": round(sum_done_seconds(r["detail"] for r in rows) / 60, 1)}
