"""`goto_page` 必须**双向**翻页（2026-11-04 真 bug：只学了 1 节就停）。

## 现象

用户：「怎么停了，只学习1节吗」

## 根因

`goto_page()` 原来只会 `next_page()`（往后翻），**没有往前翻的能力**：

```python
if cur < target and not pager.get("has_next"):
    return False
if not self.next_page():        # ← 只有下一页
    return False
```

刷完 1304 后要重新挑节，列表停在第 4 页、目标在第 1 页 → `cur > target` 不满足
任何分支，仍然去点「下一页」→ 4/4 已是最后一页 → 失败 → 返回 False。

而 `find_section()` 第一行就是：

```python
if not self.goto_page(1):
    return None
```

→ 直接返回 None → 脚本打印「[DONE] 列表里没有未完成的节了」**错误收工**。

实测第 4 页当时明明有 **9 节「未学习」**。

## 这个测试守什么

1. `goto_page` 在 `cur > target` 时必须走「上一页」，不能走「下一页」；
2. `prev_page()` 存在且用 `PAGER_PREV` 选择器；
3. `find_section` 不会因为翻不到第 1 页就误判「全部完成」。
"""

from __future__ import annotations

import inspect
import unittest
from unittest import mock

from xxt.adapters.gxks import driver as gdriver
from xxt.adapters.gxks import selectors


class TestGotoPageBidirectional(unittest.TestCase):
    def test_prev_page_exists_and_uses_selector(self):
        self.assertTrue(hasattr(gdriver.GxksDriver, "prev_page"))
        src = inspect.getsource(gdriver.GxksDriver.prev_page)
        self.assertIn("PAGER_PREV", src)
        self.assertEqual(selectors.PAGER_PREV, "#PageSplit1_BtnPrevious")

    def test_goto_page_can_go_backwards(self):
        """当前页 > 目标页时，必须调用 prev_page（不是 next_page）。"""
        src = inspect.getsource(gdriver.GxksDriver.goto_page)
        self.assertIn("prev_page", src, "goto_page 必须能往前翻")
        self.assertIn("else:", src)

    def test_goto_page_direction_logic(self):
        """用假对象验证方向：第 4 页 → 第 1 页 应连续调 prev_page。"""
        drv = gdriver.GxksDriver.__new__(gdriver.GxksDriver)
        pages = [4, 3, 2, 1]
        calls = {"next": 0, "prev": 0}

        def fake_pager():
            return {"current": pages[0] if pages else 1, "has_next": True}

        def fake_prev():
            calls["prev"] += 1
            if len(pages) > 1:
                pages.pop(0)
            return True

        def fake_next():
            calls["next"] += 1
            return True

        drv.current_pager = fake_pager
        drv.prev_page = fake_prev
        drv.next_page = fake_next
        self.assertTrue(drv.goto_page(1))
        self.assertEqual(calls["prev"], 3, "4→1 要走 3 次上一页")
        self.assertEqual(calls["next"], 0, "不能往后翻")

    def test_goto_page_forward_still_works(self):
        """往前翻的能力不能被破坏（回归）。"""
        drv = gdriver.GxksDriver.__new__(gdriver.GxksDriver)
        pages = [1, 2, 3]
        calls = {"next": 0, "prev": 0}

        def fake_pager():
            return {"current": pages[0] if pages else 3, "has_next": True}

        def fake_next():
            calls["next"] += 1
            if len(pages) > 1:
                pages.pop(0)
            return True

        def fake_prev():
            calls["prev"] += 1
            return True

        drv.current_pager = fake_pager
        drv.prev_page = fake_prev
        drv.next_page = fake_next
        self.assertTrue(drv.goto_page(3))
        self.assertEqual(calls["next"], 2)
        self.assertEqual(calls["prev"], 0)


class TestFindSectionNotFooledEmpty(unittest.TestCase):
    """`find_section` 不能因为「翻不回第 1 页」就断言「全部完成」。"""

    def test_find_section_starts_from_page_one(self):
        src = inspect.getsource(gdriver.GxksDriver.find_section)
        self.assertIn("goto_page(1)", src)

    def test_returns_none_only_after_scanning_all_pages(self):
        """返回 None 的前提是「翻完了所有页都没找到」，不是「翻不动了」。"""
        src = inspect.getsource(gdriver.GxksDriver.find_section)
        self.assertIn("has_next", src, "要真的翻到没有下一页才算扫完")


if __name__ == "__main__":
    unittest.main()
