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
                              "course": "课程", "account": "账号"}
UNSET = "未标注"


def get_profile(conn: sqlite3.Connection, account_id: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT student_no, grade, major, term, source FROM account_profiles"
        " WHERE account_id=?", (account_id,)).fetchone()
    if row is None:
        return {"account_id": account_id, "student_no": "", "grade": "", "major": "",
                "term": "", "source": ""}
    return {"account_id": account_id, "student_no": row["student_no"] or "",
            "grade": row["grade"] or "", "major": row["major"] or "",
            "term": row["term"] or "", "source": row["source"] or ""}


def set_profile(conn: sqlite3.Connection, account_id: int, *, student_no: str = "",
                grade: str = "", major: str = "", term: str = "",
                source: str = "manual") -> dict[str, Any]:
    """写入/更新画像（留空即清空该维度，统计里会归入"未标注"）。"""
    if conn.execute("SELECT 1 FROM accounts WHERE id=?", (account_id,)).fetchone() is None:
        return {"ok": False, "error": f"账号 #{account_id} 不存在"}
    conn.execute(
        "INSERT INTO account_profiles(account_id, student_no, grade, major, term,"
        " source, updated_at) VALUES(?,?,?,?,?,?,datetime('now'))"
        " ON CONFLICT(account_id) DO UPDATE SET student_no=excluded.student_no,"
        " grade=excluded.grade, major=excluded.major, term=excluded.term,"
        " source=excluded.source, updated_at=excluded.updated_at",
        (account_id, (student_no or "").strip() or None,
         (grade or "").strip() or None, (major or "").strip() or None,
         (term or "").strip() or None, source or "manual"))
    conn.commit()
    return {"ok": True, **get_profile(conn, account_id)}


def list_profiles(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """全部账号 + 画像（未填的返回空串，前端可直接编辑）。"""
    out = []
    for r in conn.execute(
            "SELECT a.id, a.label, a.status, a.platform_username, p.student_no,"
            " p.grade, p.major, p.term, p.source"
            " FROM accounts a LEFT JOIN account_profiles p ON p.account_id = a.id"
            " ORDER BY a.id"):
        out.append({"account_id": r["id"], "label": r["label"], "status": r["status"],
                    "name": r["platform_username"] or "",
                    "student_no": r["student_no"] or "", "grade": r["grade"] or "",
                    "major": r["major"] or "", "term": r["term"] or "",
                    "source": r["source"] or ""})
    return out


def aggregate(conn: sqlite3.Connection, *, by: str = "grade", days: int = 7,
              q: str = "") -> dict[str, Any]:
    """按维度聚合。days<=0 表示全部历史；q 为关键字（命中维度值/账号名/学号/课程名）。

    返回值含 `trend`（按天的视频数/分钟数时间序列，范围与筛选一致）。
    """
    if by not in DIMENSIONS:
        return {"ok": False, "error": f"未知维度：{by}"}
    cutoff = ""
    if days and days > 0:
        cutoff = (datetime.now() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    profiles = {r["account_id"]: r for r in list_profiles(conn)}
    sql = ("SELECT l.account_id, l.event, l.detail, l.ts FROM run_logs l"
           " WHERE l.account_id != 0 AND l.event IN ('video-done', 'work-start')")
    args: list[Any] = []
    if cutoff:
        sql += " AND l.ts >= ?"
        args.append(cutoff)
    ql = (q or "").strip().lower()

    buckets: dict[str, dict[str, Any]] = {}
    trend: dict[str, dict[str, Any]] = {}
    for r in conn.execute(sql, args):
        # 按课程聚合时只统计视频：work-start 的详情里没有课程名（`作业名｜模式`），
        # 硬归组会冒出「2.12」这种伪课程，宁可不计也不编。
        if by == "course" and r["event"] != "video-done":
            continue
        aid = int(r["account_id"])
        prof = profiles.get(aid) or {}
        if by == "account":
            key = f"#{aid} {prof.get('label') or ''}".strip()
        elif by == "course":
            key = _course_of(r["detail"]) or UNSET
        else:
            key = (prof.get(by) or "").strip() or UNSET
        if ql:
            hay = " ".join([key, str(prof.get("label") or ""),
                            str(prof.get("name") or ""),
                            str(prof.get("student_no") or "")]).lower()
            if ql not in hay:
                continue
        b = buckets.setdefault(key, {"key": key, "accounts": set(), "videos": 0,
                                     "seconds": 0, "works": 0})
        b["accounts"].add(aid)
        day = (r["ts"] or "")[:10]
        t = trend.setdefault(day, {"date": day, "videos": 0, "seconds": 0, "works": 0})
        if r["event"] == "video-done":
            b["videos"] += 1
            b["seconds"] += _seconds(r["detail"])
            t["videos"] += 1
            t["seconds"] += _seconds(r["detail"])
        else:
            b["works"] += 1
            t["works"] += 1

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
            "since": cutoff or "全部", "q": q or "", "rows": rows,
            "trend": [{"date": d["date"], "videos": d["videos"],
                       "minutes": round(d["seconds"] / 60, 1), "works": d["works"]}
                      for d in sorted(trend.values(), key=lambda x: x["date"])
                      if d["date"]],
            "total": {"accounts": len(total["accounts"]), "videos": total["videos"],
                      "minutes": round(total["seconds"] / 60, 1), "works": total["works"]}}


def _course_of(detail: str | None) -> str:
    """video-done 详情的第一个字段就是课程名（`课程｜知识点｜123s`）。"""
    return (detail or "").split("｜")[0].strip()


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
