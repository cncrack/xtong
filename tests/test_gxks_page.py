"""控制台页面（`panel_gxks/page.py`）的静态断言：只读字符串，不起服务、不连浏览器。

守四件事：
① 节进度必须分成「待学 / 已完成」两块，且折叠状态要能在 4 秒轮询重绘后保住；
② 每个 `onclick="fn(...)"` 调用的 `fn` 在页面里真有定义——2026-10-30 实测踩过：
   「刷新节列表」调的是一个从未定义过的 `refresh()`，点了就 ReferenceError；
③ 进度条的学时口径取 min(已学, 要求)，否则 543/340 这种会把进度顶过 100%；
④ 单文件打包约束：不引任何外部 CDN / 静态资源。
"""

from __future__ import annotations

import re
import unittest

from xxt.panel_gxks.page import PAGE

FUNC_DEF = re.compile(r"(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(")
ONCLICK = re.compile(r'onclick="([A-Za-z_$][\w$]*)\s*\(')

SECTION_IDS = ("sec-accounts", "sec-browser", "sec-sections", "sec-work", "sec-ops")


class TestHandlers(unittest.TestCase):
    def test_every_onclick_handler_is_defined(self):
        used = set(ONCLICK.findall(PAGE))
        defined = set(FUNC_DEF.findall(PAGE))
        self.assertEqual(used - defined, set(), "有 onclick 指向了未定义的函数")

    def test_refresh_button_calls_defined_handler(self):
        # 曾经写成 onclick="refresh()"，而 refresh 根本不存在
        self.assertIn('onclick="refreshSections()"', PAGE)

    def test_no_stray_format_placeholder(self):
        # 手写字符串拼接时漏过 %s，页面直接显示字面量
        self.assertNotIn("%s", PAGE)


class TestSectionsLayout(unittest.TestCase):
    def test_split_into_todo_and_done_blocks(self):
        self.assertIn('data-blk="todo"', PAGE)
        self.assertIn('data-blk="done"', PAGE)

    def test_open_state_survives_poll_rebuild(self):
        # 4 秒轮询会整块重绘：折叠状态得记在 JS 里，并在重建时按变量还原
        self.assertIn("SEC_OPEN[d.dataset.blk] = d.open", PAGE)
        self.assertIn('(SEC_OPEN.todo ? " open" : "")', PAGE)
        self.assertIn("if (sig === SEC_SIG) return;", PAGE)

    def test_hours_use_clamped_sum(self):
        self.assertIn("Math.min(numv(r.done_minutes), numv(r.required_minutes))", PAGE)

    def test_progressbar_exposes_aria(self):
        self.assertIn('role="progressbar"', PAGE)
        self.assertIn("aria-valuenow=", PAGE)

    def test_empty_states_when_a_block_has_no_rows(self):
        self.assertIn("待学区已清空", PAGE)
        self.assertIn("还没有学完的节", PAGE)


    def test_numeric_cells_are_right_aligned(self):
        # 实测踩过：th.num 右对齐而 td.num 没补 → 表头贴右、数字靠左，同列不对齐
        self.assertIn("td.num{font-variant-numeric:tabular-nums;white-space:nowrap;"
                      "text-align:right}", PAGE)

    def test_ops_first_columns_do_not_wrap(self):
        # 并排布局下「动作」列曾被挤到 45px，四个字被拓成两行
        self.assertIn("#ops td:nth-child(-n+3){white-space:nowrap}", PAGE)


class TestPageShell(unittest.TestCase):
    def test_grid_layout_targets_exist(self):
        self.assertIn("grid-template-columns:repeat(12", PAGE)
        for sec in SECTION_IDS:
            self.assertIn(f'id="{sec}"', PAGE, f"网格样式指向的 #{sec} 在页面上不存在")

    def test_no_external_assets(self):
        # 单文件 exe：页面必须自包含，离线也不能少东西
        for bad in ('src="http', 'href="http', "@import", "<link "):
            self.assertNotIn(bad, PAGE)


if __name__ == "__main__":
    unittest.main()
