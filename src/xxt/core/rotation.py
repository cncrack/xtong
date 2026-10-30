"""课程轮换策略（PLAN v0.8 §5.5/§5.6）：

当日学哪些课 = 优先级（账号×课程配置）× 未完成度（自动计算）加权随机抽样。
P0 单进程：一次会话主学 1 门（穿插由每日多次会话/自动化实现）；P1 调度器接管多门轮播。
纯逻辑，不依赖 playwright，可单测。
"""

from __future__ import annotations

import random
import sqlite3
from typing import Any


def course_weights(conn: sqlite3.Connection, account_id: int,
                   priorities: dict[str, float]) -> list[tuple[float, sqlite3.Row]]:
    """计算各候选课程权重：优先级 × 未完成度。跳过无章节、已完结、**未纳入自动刷**的课程。

    v10（2026-10-30 会话72）：只选 `auto_grind=1` 的课。以前“有树且 pending>0”就进轮转，
    导致用户“只是抓章节看一眼”的课被守护自动开刷（“抓了树就被偷刷”）。现在自动刷是课程级
    opt-in：默认关，用户在面板显式打开才纳入轮转池。手动 ▶ 不走这里，不受此开关约束。
    """
    rows = conn.execute(
        "SELECT id, name FROM courses WHERE account_id = ? AND auto_grind = 1", (account_id,)
    ).fetchall()
    cands: list[tuple[float, sqlite3.Row]] = []
    for r in rows:
        total = conn.execute(
            "SELECT COUNT(*) AS c FROM chapters WHERE course_id = ?", (r["id"],)
        ).fetchone()["c"]
        if not total:
            continue
        pending = conn.execute(
            "SELECT COUNT(*) AS c FROM chapters WHERE course_id = ? AND status = 'pending'",
            (r["id"],),
        ).fetchone()["c"]
        if pending == 0:
            continue
        pri = float(priorities.get(str(r["id"]), priorities.get(r["name"], 1.0)))
        cands.append((pri * pending / total, r))
    return cands


def pick_courses(conn: sqlite3.Connection, rng: random.Random, rotation: dict[str, Any],
                 account_id: int = 1, count: int | None = None) -> list[sqlite3.Row]:
    """加权随机抽出今日要学的课程（有序，第一个为主学课程）。"""
    cands = course_weights(conn, account_id, rotation.get("priorities", {}))
    if not cands:
        return []
    if count is None:
        lo, hi = rotation.get("courses_per_day", [1, 3])
        count = rng.randint(int(lo), int(hi))
    count = max(1, min(count, len(cands)))
    picks: list[sqlite3.Row] = []
    pool = list(cands)
    while pool and len(picks) < count:
        chosen = rng.choices(pool, weights=[w for w, _ in pool], k=1)[0]
        picks.append(chosen[1])
        pool.remove(chosen)
    return picks
