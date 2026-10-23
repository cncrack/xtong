"""每日双额度（PLAN v0.6 §5.5/§5.6）：时长 + 任务数，任一达标即停；当日额度在上限内随机浮动。"""

from __future__ import annotations

import random
from typing import Any


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
