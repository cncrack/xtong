"""回归测试：列表页必须待在**外层框架的 iframe** 里（2026-11-03 实测修）。

## 背景

2026-11-03 用户发现「刷新完节列表还是 9 节、面板数据不动」。
实测根因：直接 GET 裸的 `LibraryStudyList.aspx` 也能读到行，但**分页控件不渲染**：

| 上下文 | 行数 | 分页 |
|---|---|---|
| 裸页（`LibraryStudyList.aspx`） | 6~7 行 | **无**（`PageSplit1_BtnNext` 不存在） |
| iframe（`MainPage.aspx` 的 `frame[name=main]`） | 9 行 | `1/4`，有下一页 |

于是 `read_page()` 的 GET cache-bust 把 iframe 导航成裸页 → 后续 `next_page()`
找不到按钮 → 「第 2 页之后翻不动，停在 2/4」→ **只能读到第 1 页**。

修复：`driver.ensure_list_context()` —— 读列表前确保分页控件存在，
不存在就回到学习入口页重进；`read_page()` 改成**在 iframe 内触发回发刷新**
（点搜索按钮），不再 GET 裸页。
"""

from __future__ import annotations

import inspect
import unittest

from xxt.adapters.gxks import driver


class TestListContext(unittest.TestCase):
    def test_driver_has_ensure_list_context(self):
        self.assertTrue(hasattr(driver.GxksDriver, "ensure_list_context"))

    def test_ensure_context_is_called_by_read_page(self):
        src = inspect.getsource(driver.GxksDriver.read_page)
        self.assertIn("ensure_list_context()", src)

    def test_read_page_does_not_get_bare_page(self):
        """不能 GET 裸页 —— 那会让 iframe 失去分页控件。"""
        src = inspect.getsource(driver.GxksDriver.read_page)
        self.assertNotIn("frame.goto(self.cache_busted(", src,
                         "read_page 不得再 GET 裸的列表页（分页控件会消失）")

    def test_refresh_here_uses_context(self):
        """refresh_here 的第 1 页分支走 read_page，也就继承了上下文保证。"""
        src = inspect.getsource(driver.GxksDriver.refresh_here)
        self.assertIn("read_page(", src)

    def test_ensure_context_documents_the_trap(self):
        src = inspect.getsource(driver.GxksDriver.ensure_list_context)
        self.assertIn("分页控件", src)


class TestSectionsScriptPagination(unittest.TestCase):
    """脚本侧的翻页采集：`_gather` 要能一路翻到底。"""

    def test_gather_uses_read_page_and_next_page(self):
        from pathlib import Path
        text = Path("scripts/gxks_sections.py").read_text(encoding="utf-8")
        self.assertIn("drv.read_page(", text)
        self.assertIn("drv.next_page()", text)
        # 翻页失败要**留痕**（之前只有 stderr 一行，看板上无感知）
        self.assertIn("翻不动", text)
