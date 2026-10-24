"""平台适配器接口。核心层只依赖本接口，不直接依赖具体平台细节。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class PlatformAdapter(ABC):
    """每个目标平台实现一份：登录、课程抓取、视频学习、弹题读取与提交。"""

    name: str = ""

    @abstractmethod
    def login(self, state_path: Path) -> bool:
        """人工辅助登录并保存会话。"""

    @abstractmethod
    def probe_session(self, state_path: Path) -> tuple[str, str | None]:
        """探测会话有效性：返回 (valid|invalid|no_state, 平台用户名或 None)。"""

    @abstractmethod
    def fetch_courses(self, state_path: Path) -> list[dict[str, Any]]:
        """抓取课程列表，返回 [{platform_course_id, name, url}]。"""

    @abstractmethod
    def fetch_chapters(self, course_row: dict[str, Any], state_path: Path) -> list[dict[str, Any]]:
        """抓取课程章节树叶子节点，返回 [{platform_chapter_id, name, idx, done_on_platform}]。"""

    # M2+ 接口：学习运行器（study.StudyRunner）直接在平台包内实现，不经本抽象调用
