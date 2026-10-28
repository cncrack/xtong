"""平台适配层：按配置的平台名查找对应适配器。"""

from __future__ import annotations

from ..config import Settings
from .base import PlatformAdapter
from .chaoxing import ChaoxingAdapter
from .gxks import GxksAdapter

_ADAPTERS = {"chaoxing": ChaoxingAdapter, "gxks": GxksAdapter}


def get_adapter(settings: Settings) -> PlatformAdapter:
    name = settings.platform["name"]
    try:
        cls = _ADAPTERS[name]
    except KeyError:
        raise ValueError(f"未知平台适配器：{name}（可选：{sorted(_ADAPTERS)}）") from None
    return cls(settings)
