"""XXT — 网课自动化学习助手（超星学习通适配）。"""

import os
import sys
from pathlib import Path

__version__ = "0.1.0"

# exe 模式：playwright 会去包内找浏览器，改为使用用户级缓存（首次部署目标机需
# `playwright install chromium`，或拷贝 %LOCALAPPDATA%/ms-playwright，见 docs/BUILD.md）
if getattr(sys, "frozen", False) and not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
    _cache = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
    if _cache.exists():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(_cache)
