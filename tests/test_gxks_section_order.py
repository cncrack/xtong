"""回归测试：待学/已完成必须按**章节顺序**排（2026-11-03 用户实拍乱序）。

## 根因

缓存里的 `idx` 是**每页重新从 0 编号**的（`gxks_sections._slim(raw, page)`），
所以第 2 页的 idx 又是 0,1,2… → 跨页交错 → 按 idx 排会得到
3.4、4.5、3.5、4.6、3.6… 这种乱序。

正确顺序 = 节名里的**章节号**（"3.4 当代中国军事思想" → 3.4），
没有章节号时退回节 ID（实测连号递增）。
"""

from __future__ import annotations

import unittest

from xxt.panel_gxks.page import PAGE


class TestSectionOrdering(unittest.TestCase):
    def test_order_key_prefers_chapter_number(self):
        body = PAGE.split("function renderSections(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("orderKey", body)
        # 章节号优先
        self.assertIn(r"match(/(\d+)\.(\d+)/)", body)

    def test_does_not_sort_by_idx_only(self):
        """不能再出现「按 idx 排」的旧写法。"""
        body = PAGE.split("function renderSections(", 1)[1].split("\nfunction ", 1)[0]
        self.assertNotIn("numv(a.idx) - numv(b.idx)", body,
                         "待学/已完成不能按 idx 排（idx 每页重编，会乱序）")

    def test_learning_not_pinned_to_top(self):
        """「学习中」不置顶 —— 置顶会把它从章节位置挪走，顺序又乱。"""
        body = PAGE.split("function renderSections(", 1)[1].split("\nfunction ", 1)[0]
        self.assertNotIn('b.state === "learning") - (a.state === "learning")', body)

    def test_both_blocks_use_ordered_rows(self):
        body = PAGE.split("function renderSections(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("secTableHtml(todoRows, true)", body)
        self.assertIn("secTableHtml(doneRowsOrdered, false)", body)


class TestSectionsCacheIdxIsPerPage(unittest.TestCase):
    """`idx` 每页重编 —— 这条是排序不能靠它的**根据**。"""

    def test_slim_resets_idx_per_page(self):
        from pathlib import Path
        text = Path("scripts/gxks_sections.py").read_text(encoding="utf-8")
        self.assertIn("def _slim(", text)
        # idx 来自 parse_course_rows 的页内计数 → 每页从 0 开始
        self.assertIn('"idx"', text)
