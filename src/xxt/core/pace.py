"""拟人化节奏引擎（PLAN §5.5）。

原则：节奏随机而非处处停顿。所有参数来自配置区间，运行时抽样；代码禁止固定 sleep。
纯逻辑模块，不依赖 playwright，可单测。
"""

from __future__ import annotations

import random
from datetime import datetime, time as dtime
from typing import Any


def sample_gap(rng: random.Random, pace: dict[str, Any]) -> float:
    """切节间隔（秒）：三档加权抽样——连看 / 短歇 / 离开。"""
    ranges = [pace["gap_browse"], pace["gap_short"], pace["gap_away"]]
    weights = pace["gap_weights"]
    lo, hi = rng.choices(ranges, weights=weights, k=1)[0]
    return float(rng.uniform(lo, hi))


def sample_speed(rng: random.Random, pace: dict[str, Any]) -> float:
    """播放倍速：区间内随机，保留两位。"""
    lo, hi = pace["speed_range"]
    return round(float(rng.uniform(lo, hi)), 2)


def within_daily_window(pace: dict[str, Any], now: datetime | None = None) -> bool:
    """是否在允许学习时段内（本地时间），跨午夜窗口支持（如 22:00-06:00）。"""
    start_s, end_s = pace["daily_window"]
    fmt = "%H:%M"

    def parse(s: str) -> dtime:
        h, m = s.split(":")
        return dtime(int(h), int(m))

    start, end = parse(start_s), parse(end_s)
    t = (now or datetime.now()).time()
    if start <= end:
        return start <= t <= end
    return t >= start or t <= end  # 跨午夜


class FocusTimer:
    """专注会话模型：连续学习 focus_minutes 区间随机时长后，休息 rest_minutes 区间随机。"""

    def __init__(self, rng: random.Random, pace: dict[str, Any]) -> None:
        self.rng = rng
        self.pace = pace
        self._focus_until: datetime | None = None

    def next_focus_minutes(self) -> float:
        lo, hi = self.pace["focus_minutes"]
        return float(self.rng.uniform(lo, hi))

    def next_rest_seconds(self) -> float:
        lo, hi = self.pace["rest_minutes"]
        return float(self.rng.uniform(lo, hi)) * 60
