"""超星学习通适配器。

平台 URL 来自配置；页面选择器统一放 selectors.py，平台改版时只改那一个文件。
课程抓取需在登录可用后实况核对页面结构再实现（见 docs/devlog）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..base import PlatformAdapter
from ...config import Settings
from ...log import get_logger

log = get_logger("chaoxing")


class ChaoxingAdapter(PlatformAdapter):
    name = "chaoxing"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.login_url: str = settings.platform["login_url"]
        self.space_url: str = settings.platform["space_url"]

    def login(self, state_path: Path) -> bool:
        from ...session.login import manual_login

        return manual_login(self.login_url, state_path)

    def probe_session(self, state_path: Path) -> str:
        from ...session.login import probe_session

        return probe_session(self.space_url, state_path)

    def fetch_courses(self, state_path: Path) -> list[dict[str, Any]]:
        raise NotImplementedError("M1 待办：需实况核对课程页选择器后实现（见 docs/devlog）")
