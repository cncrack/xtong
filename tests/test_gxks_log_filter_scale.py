"""日志过滤下拉的两个真 bug（2026-11-08 用户实拍反馈）。

> 「在面板日志记录显示区域的最右边的全部账号会有很多的账号，有没有考虑过
>   当账号很多的时候如何处理呢，是用搜索，还是怎么样呢？
>   在选择单账号时，显示的还是第一个账号的，第二个账号如何切换显示呢，
>   后面的全部账号也不能选择，是灰色的。」

## 根因（代码级）

1. `renderLogFilter()` **只在 `LOG_MODE === "merged"` 分支里被调用**（原 `page.py:3266`）
   → 切到「单账号」后下拉从不重绘，且 `sel.disabled = LOG_MODE !== "merged"`
   把它置灰 → 用户看到「后面全部账号不能选、是灰的」。
2. 单账号分支 `renderLogBox()` / `renderRunLog()` 都用 `LABEL`（当前选中账号）
   挑任务，而切换账号只能靠**点账号表行**（`pick(label)` → `LABEL = label`）；
   日志区自己没有账号选择器 → 「第二个账号切不过去」。
3. 过滤下拉是普通 `<select>`，把**全部账号平铺**成 option；
   100 账号 = 101 项，没有考虑规模。

## 修法

- `LOG_FILTER` 在**两种模式**下都生效：merged = 过滤合并流；single = 决定看哪个账号。
- `renderLogFilter()` 在两种模式下都调用（移到 `renderRunLog` 分支外）；
  下拉**不再 disabled**；single 模式隐藏「全部账号」option（那是合并流入口，由
  `#log-mode` 管），避免选了没反应。
- 新增 `setLogFilter(v)`：single 模式下选账号即立刻 `renderLogBox()`，
  不必去点账号表行。
- 新增 `#log-filter-q` 搜索框：账号数 >12 时显示，按显示名/代号做**本地即时过滤**
  （不引入虚拟滚动，T7/T8 仍排到 >50 账号）；含 375px 断点。
- `appendLogLines` 单账号分支的重绘判据从 `LABEL` 改为 `LOG_FILTER || LABEL`，
  否则选了别的账号后新日志不刷新。

## 不做的（守边界）

- 不引入虚拟滚动 / 页签（T7/T8 按约定排到 >50 账号）
- 不动 `LOG_LINES` 分桶与合并流渲染器（T5 的回退路径保持完好）
"""

from __future__ import annotations

import unittest

from xxt.panel_gxks.page import PAGE


class TestLogFilterAlwaysUsable(unittest.TestCase):
    def test_markup_has_search_box(self):
        self.assertIn('id="log-filter-q"', PAGE)
        self.assertIn('oninput="renderLogFilter()"', PAGE)

    def test_markup_calls_set_log_filter(self):
        self.assertIn('onchange="setLogFilter(this.value)"', PAGE)
        self.assertNotIn('onchange="LOG_FILTER=this.value; renderLogBox()"', PAGE)

    def test_filter_not_disabled_in_single_mode(self):
        """原来 `sel.disabled = LOG_MODE !== "merged"` 把单账号模式置灰。"""
        seg = PAGE.split("function renderLogFilter()", 1)[1].split("// 下拉选择", 1)[0]
        self.assertIn("sel.disabled = false;", seg)
        self.assertNotIn("sel.disabled = LOG_MODE", seg)

    def test_filter_rendered_in_both_modes(self):
        """renderLogFilter 必须在 merged 分支之外也调用一次。"""
        seg = PAGE.split("function renderRunLog(state)", 1)[1][:900]
        self.assertIn("renderLogFilter();", seg)
        self.assertIn('if (LOG_MODE === "merged")', seg)
        # 调用在 if 之前（split 位置比较）
        self.assertLess(seg.index("renderLogFilter();"),
                        seg.index('if (LOG_MODE === "merged")'))

    def test_single_mode_hides_all_accounts_option(self):
        seg = PAGE.split("function renderLogFilter()", 1)[1].split("// 下拉选择", 1)[0]
        self.assertIn("isSingle", seg)
        self.assertIn("let html = isSingle ? \"\" :", seg)

    def test_search_box_only_after_12_accounts(self):
        self.assertIn("cards.length > 12", PAGE)

    def test_mobile_breakpoint(self):
        self.assertIn("#log-filter-q{width:96px", PAGE)


class TestSingleModeCanSwitchAccount(unittest.TestCase):
    def test_set_log_filter_exists(self):
        self.assertIn("function setLogFilter(v)", PAGE)
        self.assertIn("LOG_FILTER = v || \"\";", PAGE)

    def test_render_log_box_uses_filter(self):
        seg = PAGE.split("function renderLogBox()", 1)[1].split("function renderMergedLogBox", 1)[0] \
              if "function renderMergedLogBox" in PAGE else PAGE.split("function renderLogBox()", 1)[1][:1200]
        # 实际 renderLogBox 在 renderMergedLogBox 之后，取它自己的开头
        own = PAGE.rsplit("function renderLogBox()", 1)[1][:500]
        self.assertIn("LOG_FILTER", own)
        self.assertIn("LOG_MODE === \"single\"", own)

    def test_run_log_uses_filter(self):
        seg = PAGE.split("function renderRunLog(state)", 1)[1].split("const running =", 1)[0]
        self.assertIn("LOG_FILTER", seg)
        self.assertIn("pickLogJob(jobs,", seg)

    def test_append_uses_filter(self):
        seg = PAGE.split("function appendLogLines(", 1)[1].split("// 日志标题栏", 1)[0]
        self.assertIn("viewLabel", seg)
        self.assertIn("LOG_FILTER || LABEL", seg)

    def test_set_mode_syncs_filter(self):
        seg = PAGE.split("function setLogMode(v)", 1)[1].split("// T5：合并流的渲染器", 1)[0]
        self.assertIn("LOG_FILTER = LABEL || \"\"", seg)
        self.assertIn("renderLogFilter();", seg)


if __name__ == "__main__":
    unittest.main()
