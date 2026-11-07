"""跨页回读 + 分页按钮点击方式（2026-11-07 抓到的 P0）。

## 事故（代价：一整节白跑 24 分钟，被误判成"学时不涨"）

实测日志（`gxks-study-default-20261107-224125.log`）：

```
[23:02:11] ended
[23:02:11] ended_short 视频播完但列表只 None/None（快照可能过期）
结果 escalate；用时 24.3 分钟；列表 None/None None
⚠ 本节非正常收场（学时不涨）→ 停，不再换节硬跑
```

但**只读核实平台**后：1317 `done_minutes = 19 / required = 19`、`state = done`。
也就是**平台记上了、学完了**，本地却判失败。

## 两个根因（都已修）

### ① `read_row()` 只读当前页

```python
for row in self.refresh_here()["rows"]:   # ← 只有当前页
```

1317 在**第 5 页**（课程 6 页）→ 永远 `None` → `列表 None/None` → 判"学时不涨"。

### ② 分页按钮用 `elem.click()` 会卡满 60 秒超时

按钮是 `<a href="javascript:__doPostBack('PageSplit1$BtnPrevious','')">`。
`click()` 触发整页回发，Playwright 等 action 完成 → **Timeout 60000ms exceeded**。
换成页内 `eval` 那个 `javascript:` 表达式，**立刻翻页成功**（6→5 页，实测）。

两个根因**叠加**才致命：翻不动 + 只读当页 = 回读永远失败。

## 守什么

1. `read_row` 必须能**跨页**找到目标（调 `find_section`，不只是 `refresh_here`）；
2. `next_page` / `prev_page` 必须用 `__doPostBack` 而不是 `click()`；
3. 找不到时的**兜底**仍试一次当前页（翻页偶发失败不至于整节作废）。
"""

from __future__ import annotations

import inspect
import unittest

from xxt.adapters.gxks import driver as gdriver


class TestReadRowIsCrossPage(unittest.TestCase):
    def test_read_row_finds_across_pages(self):
        """`read_row` 必须走**逐页找**，不能只读当前页。

        这条是直接的回归点：谁把它改回 `for row in self.refresh_here()["rows"]`，
        跨页的节又会读不到、整节被误判"学时不涨"。
        """
        src = inspect.getsource(gdriver.GxksDriver.read_row)
        self.assertIn("find_section", src,
                      "read_row 必须跨页找（find_section），否则第 5 页的节永远读不到")
        self.assertIn("refresh_here", src, "应保留当前页兜底")


class TestPagerUsesPostBack(unittest.TestCase):
    def test_next_page_does_not_use_element_click(self):
        """`next_page` 不能用 `elem.click()`（会卡满 60 秒超时）。"""
        src = inspect.getsource(gdriver.GxksDriver.next_page)
        self.assertNotIn(".click(", src,
                         "分页按钮是 __doPostBack 链接，click() 会 Timeout 60000ms")
        self.assertIn("_click_pager", src)

    def test_prev_page_does_not_use_element_click(self):
        src = inspect.getsource(gdriver.GxksDriver.prev_page)
        self.assertNotIn(".click(", src)
        self.assertIn("_click_pager", src)

    def test_click_pager_evaluates_dopostback(self):
        """`_click_pager` 必须真的调 `__doPostBack`（页内 eval），而不是 Selenium 式点击。"""
        src = inspect.getsource(gdriver.GxksDriver._click_pager)
        self.assertIn("__doPostBack", src)
        self.assertIn("eval(", src)
        # 兼容兜底：href 里没有 __doPostBack 时才 click
        self.assertIn("a.click()", src)

    def test_pager_button_ids_match_selectors(self):
        """`_click_pager` 收到的是**裸 id**（不带 `#`），否则 `getElementById` 找不到。"""
        src = inspect.getsource(gdriver.GxksDriver.next_page)
        self.assertIn('lstrip("#")', src)
        prev_src = inspect.getsource(gdriver.GxksDriver.prev_page)
        self.assertIn('lstrip("#")', prev_src)


class TestFakeFrameBehavior(unittest.TestCase):
    """用假 frame 验证真实交互（不连浏览器）。"""

    def _driver(self, fake_frame):
        drv = gdriver.GxksDriver.__new__(gdriver.GxksDriver)
        drv.page = _FakePage()
        drv.page_timeout_ms = 1000
        drv.settle_seconds = 0.0
        drv.list_frame = lambda: fake_frame
        return drv

    def test_click_pager_reports_no_btn(self):
        drv = self._driver(_FakeFrame(js_result="no-btn"))
        self.assertFalse(drv.next_page())

    def test_click_pager_success(self):
        frame = _FakeFrame(js_result="postback")
        drv = self._driver(frame)
        self.assertTrue(drv.next_page())
        self.assertIn("PageSplit1_BtnNext", frame.scripts[-1][1])

    def test_click_pager_swallows_errors(self):
        drv = self._driver(_FakeFrame(raise_on_eval=True))
        self.assertFalse(drv.next_page())


class _FakePage:
    def wait_for_timeout(self, _ms):
        return None


class _FakeFrame:
    def __init__(self, js_result="postback", raise_on_eval=False):
        self._js = js_result
        self._raise = raise_on_eval
        self.scripts: list[tuple] = []

    def evaluate(self, script, arg=None):
        self.scripts.append((script, arg))
        if self._raise:
            raise RuntimeError("boom")
        return self._js

    def query_selector(self, _sel):
        return _FakeBtn()

    def content(self):
        return "<html></html>"


class _FakeBtn:
    def is_enabled(self):
        return True


if __name__ == "__main__":
    unittest.main()
