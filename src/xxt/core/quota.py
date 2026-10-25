"""每日双额度（PLAN v0.6 §5.5/§5.6）：时长 + 任务数，任一达标即停；当日额度在上限内随机浮动。

时长口径（2026-10-25 修正）：按 run_logs 当日 `video-done` 详情的累计秒数算，
**跨会话有效**。此前用进程内 `time.monotonic()` 计时，停掉再启动即可重新计满，
与"每天 240 分钟"的语义不符（重启一次就多刷一整份额度）。
"""

from __future__ import annotations

import random
import re
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
