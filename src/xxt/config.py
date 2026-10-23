"""配置加载：config/settings.toml，缺失项用内置默认值补齐。"""

from __future__ import annotations

import os
import tomllib
from copy import deepcopy
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, Any] = {
    "app": {"data_dir": "data"},
    "platform": {
        "name": "chaoxing",
        "login_url": "https://passport2.chaoxing.com/login",
        "space_url": "https://i.chaoxing.com/",
    },
    "llm": {
        "provider": "glm",
        "base_url": "https://open.bigmodel.cn/api/paas/v4/",
        "model": "glm-4-flash",
        "api_key_env": "XXT_LLM_API_KEY",
    },
    "learn": {
        "speed": 1.0,
        "daily_max_minutes": 240,
    },
    "pace": {
        "gap_browse": [3, 15],
        "gap_short": [30, 90],
        "gap_away": [180, 600],
        "gap_weights": [70, 20, 10],
        "focus_minutes": [25, 60],
        "rest_minutes": [5, 15],
        "daily_window": ["08:00", "23:00"],
        "speed_range": [1.0, 1.25],
    },
    "quiz": {"enabled": False, "mode": "semi"},
    "stealth": {"headless": True},
}


def find_project_root() -> Path:
    """定位项目根：XXT_ROOT 环境变量优先，否则向上查找 config/settings.toml 或 .git。"""
    env = os.environ.get("XXT_ROOT")
    if env:
        return Path(env).resolve()
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "config" / "settings.toml").exists() or (parent / ".git").exists():
            return parent
    # 源码布局 src/xxt/config.py → 项目根
    return here.parents[2]


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> None:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


class Settings:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or find_project_root()).resolve()
        data = deepcopy(DEFAULTS)
        path = self.root / "config" / "settings.toml"
        if path.exists():
            with open(path, "rb") as f:
                _deep_merge(data, tomllib.load(f))
        self.raw = data
        self.data_dir = (self.root / data["app"]["data_dir"]).resolve()
        self.db_path = self.data_dir / "xxt.db"
        self.log_dir = self.data_dir / "logs"
        self.platform: dict[str, Any] = data["platform"]
        self.llm: dict[str, Any] = data["llm"]
        self.learn: dict[str, Any] = data["learn"]
        self.pace: dict[str, Any] = data["pace"]
        self.quiz: dict[str, Any] = data["quiz"]
        self.stealth: dict[str, Any] = data["stealth"]

    @property
    def default_state_path(self) -> Path:
        """默认账号（单用户阶段）的浏览器会话文件。"""
        return self.data_dir / "accounts" / "default" / "state.json"
