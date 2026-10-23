"""日志：控制台 + 按天滚动的文件（data/logs/xxt.log）。"""

from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

_FMT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"


def setup_logging(log_dir: Path, verbose: bool = False) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("xxt")
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    if root.handlers:  # 已初始化，不重复挂 handler
        return
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(_FMT))
    root.addHandler(console)
    file_handler = TimedRotatingFileHandler(
        log_dir / "xxt.log", when="midnight", backupCount=14, encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter(_FMT))
    root.addHandler(file_handler)


def get_logger(name: str) -> logging.Logger:
    name = name.removeprefix("xxt.")
    return logging.getLogger(f"xxt.{name}")
