"""每日双额度（PLAN v0.6 §5.5/§5.6）：时长 + 任务数，任一达标即停；当日额度在上限内随机浮动。

时长口径（2026-10-25 修正）：按 run_logs 当日 `video-done` 详情的累计秒数算，
**跨会话有效**。此前用进程内 `time.monotonic()` 计时，停掉再启动即可重新计满，
与"每天 240 分钟"的语义不符（重启一次就多刷一整份额度）。
"""

from __future__ import annotations

import random
import re
import sqlite3
import time
from typing import Any, Iterable

# video-done 的 detail 形如 `课程名｜知识点标题｜123s`（全角竖线分隔，秒数在末尾）
_DUR_RE = re.compile(r"｜(\d+)s$")


def sample_daily_budget(rng: random.Random, quota: dict[str, Any]) -> tuple[float, int]:
    """抽样当日额度。返回 (分钟, 任务数)；0 表示该项不限。"""
    lo, hi = quota["float_ratio"]
    minutes = (
        0.0
        if quota["daily_minutes"] <= 0
        else float(quota["daily_minutes"] * rng.uniform(lo, hi))
    )
    tasks = (
        0
        if quota["daily_tasks"] <= 0
        else max(1, int(quota["daily_tasks"] * rng.uniform(lo, hi)))
    )
    return minutes, tasks


def budget_floor(quota: dict[str, Any]) -> tuple[int, int]:
    """当日额度的**下限**（分钟, 任务数）；0 表示该项不限。

    额度在上限内随机浮动（float_ratio），但**判定今天算不算学够必须用下限**：
    子会话每次启动都会重新抽一次额度，只有总量到了下限，才谈得上“达成”。
    2026-10-28 守护侧的“达成闸门”就靠这两个数（Q29/P1）。
    """
    lo = (quota.get("float_ratio") or (1.0, 1.0))[0]
    tasks = int(quota.get("daily_tasks") or 0)
    minutes = float(quota.get("daily_minutes") or 0) * float(lo)
    return (int(minutes), max(1, int(tasks * float(lo))) if tasks > 0 else 0)


def reached_locally(quota: dict[str, Any], n_tasks: int, done_seconds: int) -> bool:
    """本地事实（今日已播个数/秒数）是否已够额度下限；两项都不限 → True。

    与 `sample_daily_budget` 共用同一份 quota 口径，不另起一套。
    """
    minutes_lo, tasks_lo = budget_floor(quota)
    if not minutes_lo and not tasks_lo:
        return True
    if tasks_lo and n_tasks >= tasks_lo:
        return True
    return bool(minutes_lo) and done_seconds >= minutes_lo * 60


def parse_duration_seconds(detail: str) -> int:
    """从 video-done 详情解析观看秒数；解析不出（机制上线前的历史记录）返回 0。"""
    m = _DUR_RE.search(detail or "")
    return int(m.group(1)) if m else 0


def sum_done_seconds(details: Iterable[str]) -> int:
    """累计一组 video-done 详情的观看秒数（今日已学多久，跨会话）。"""
    return sum(parse_duration_seconds(d) for d in details)


def remaining_minutes(budget_minutes: float, done_seconds: int) -> float:
    """把"当日额度"换算成本次会话还能学多少分钟；<=0 表示今日已达标。"""
    if not budget_minutes:
        return 0.0
    return budget_minutes - done_seconds / 60


def video_done_counted(conn, *, account_id: int, course_name: str, title: str,
                       day: str | None = None) -> bool:
    """这一节今天是否已经记过 `video-done`（C，2026-10-29）。

    为什么需要：额度的两个口径都直接数当天的 `video-done` 行（见模块文档），
    所以**同一节被记两次就是白扣一份额度**。实测 2026-10-29 账号#1：watcher
    注入失败 → 中断日志读到上一节遗留的“100%” → 误判“值得续播” → 整节重播 →
    21:33:15 与 21:39:16 两条同样的 `346s`。多花一次真实页面访问，还虚耗额度、
    挤掉本该学的下一节。

    判据只用 `detail` 里的既有结构（`课程名｜节名｜Ns`），用 `instr` 精确子串匹配
    而不是 LIKE：节名里可能含 `%`/`_` 这类 LIKE 元字符。查询失败一律按“没记过”
    处理——宁可多记一次，也不要因为查库异常把真播完的一节丢掉不记账。
    """
    try:
        row = conn.execute(
            "SELECT 1 FROM run_logs WHERE event='video-done' AND account_id=?"
            " AND ts LIKE ? AND instr(detail, ?) > 0 LIMIT 1",
            (account_id, f"{(day or time.strftime('%Y-%m-%d'))}%",
             f"{course_name}｜{title}｜"),
        ).fetchone()
    except sqlite3.Error:
        return False
    return row is not None
