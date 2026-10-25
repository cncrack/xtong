"""页内脚本与纯数据：**同步/异步两个引擎唯一的共用通道**。

设计约束（2026-10-25 devlog 28）：
- 本模块只放"不需要 page/browser 对象"的东西——JS 文本、选择器拼接、SQL 匹配。
- 因此谁 import 都安全：异步引擎拿到的是字符串，不涉及任何同步 Playwright 对象。
- 任何需要 `page.xxx()` / `frame.xxx()` 的辅助函数，一律放 `pagesync.py`（同步专用）。
"""

from __future__ import annotations

import json
from pathlib import Path

from .selectors import QUIZ_SELECTORS

WATCHER_JS = (Path(__file__).parent / "watcher.js").read_text(encoding="utf-8")


def build_watcher_js(rate: float) -> str:
    """按当前倍速与弹题选择器生成注入脚本。"""
    return (WATCHER_JS
            .replace("__XXT_RATE__", str(rate))
            .replace("__XXT_QUIZ__", json.dumps(QUIZ_SELECTORS, ensure_ascii=False)))


def match_chapter_id(conn, course_id: int, title: str) -> int | None:
    """用旧版页任务标题模糊匹配章节库记录（如"尽快适应大学生活（上）"→ 2.1 记录 id）。"""
    if not title:
        return None
    row = conn.execute(
        "SELECT id FROM chapters WHERE course_id = ? AND name LIKE ? LIMIT 1",
        (course_id, f"%{title}%"),
    ).fetchone()
    return row["id"] if row else None
