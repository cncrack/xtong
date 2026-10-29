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


def pick_pending_after(conn, course_id: int, after_pid: str | None,
                       tried: list[str] | set[str]) -> tuple[str, str] | None:
    """按 idx 顺序挑下一个待学节：先取 after_pid 之后的，到尾部再从头绕回。

    B2（2026-10-27）：原来直接 `ORDER BY idx LIMIT 1` 取全表第一个 pending ——
    当前节还没播完（仍是 pending）时，取回的永远是它自己，于是 #1 在 5.1 上反复撞；
    失败后又被标 visited，就永久跳过。现在按“当前节之后”取，本会话试过的跳过。

    2026-10-29 从 `astudy.py` 移到本模块：它只查库、不碰 page/frame，属于“两个引擎
    共用的纯数据”，而 `study.py` 反过来 import `astudy.py` 违反了模块边界纪律
    （异步侧的纯函数随时可能变成 async，混用正是 2026-10-25 账号#2 事故的成因）。
    """
    rows_p = conn.execute(
        "SELECT platform_chapter_id, name, idx FROM chapters"
        " WHERE course_id=? AND status='pending' ORDER BY idx",
        (course_id,)).fetchall()
    cur_idx = None
    if after_pid:
        r0 = conn.execute(
            "SELECT idx FROM chapters WHERE course_id=? AND platform_chapter_id=?",
            (course_id, after_pid)).fetchone()
        cur_idx = r0["idx"] if r0 else None
    after = [r for r in rows_p if cur_idx is not None and r["idx"] > cur_idx]
    before = [r for r in rows_p if cur_idx is None or r["idx"] <= cur_idx]
    for r in after + before:
        if r["platform_chapter_id"] in tried:
            continue
        return r["platform_chapter_id"], r["name"]
    return None
