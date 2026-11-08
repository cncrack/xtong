"""显示口径统一（2026-11-08 用户澄清「我指的不同是显示的不同」）。

## 用户的两条要求

> 「待学这边应该显示的是全部是多少节，正在学第几节」
> 「在账号列表中课程里面的 7/8 节是有问题的吧，这个应该显示学了几节，总的有多少节吧」

## 上一轮我理解偏了

我一直在解释「两个账号内容不同是对的」（那是**数据**差异），
而用户说的是**显示口径**：

1. **待学区**：一个账号列出全部待学节（甲 2 行），另一个只列出正在学的那 1 行
   —— 看起来像两套逻辑。**代码其实只有一套**（`rows.filter(state !== "done")`），
   差异来自 test 缓存只有 8 行（缓存不全，G-15 已修）。但用户要的信息确实缺失：
   **待学总共几节、正在学第几节**，界面上没有直接写出来。
2. **账号表「课程」列**：`7/8 节` 里的 `8` 是**缓存行数**，不是课程总节数
   —— 用户读成「这门课只有 8 节」，完全合理。应写成「已学 7 节 / 共 36 节」，
   缓存不全时写「已学 7 节 · 总数未知」。

## 修法

- 待学区顶部标签加 `正在学第 N / M 节`（N = 待学列表位置，M = 待学总数）；
- 待学区折叠标题写 `共 M 节 · 还差 X 分钟 · 正在学第 N 节`（折叠时也看得到）；
- 账号表课程列改 `已学 X 节 / 共 Y 节`；缓存不全时 `已学 X 节 · 总数未知`，
  **绝不拿缓存行数冒充总节数**；
- 顶部节数进度条在缓存不全时加注「缓存不全，总节数未知」。
"""

from __future__ import annotations

import unittest

from xxt.panel_gxks.page import PAGE


class TestTodoSectionShowsTotalAndCurrent(unittest.TestCase):
    def test_learning_position_computed_from_todo_list(self):
        """序号取待学列表里的位置（1 起），不是平台节号。"""
        self.assertIn('const learningRow = todoRows.find(r => r.state === "learning")',
                      PAGE)
        self.assertIn("const learningNo = learningRow ? todoRows.indexOf(learningRow) + 1 : 0",
                      PAGE)

    def test_tag_row_shows_current_over_total(self):
        self.assertIn("正在学第 ", PAGE)
        self.assertIn('"正在学第 " + learningNo + " / " + todoRows.length + " 节</span>"', PAGE)

    def test_no_fake_number_when_nothing_learning(self):
        """没有 learning 行就不写这一段（不能显示「正在学第 0 节」）。"""
        self.assertIn("(learningRow\n      ? ", PAGE)

    def test_collapsed_summary_also_shows_position(self):
        """折叠标题也要有「正在学第 N 节」—— 折叠时用户同样想知道。"""
        self.assertIn('(learningRow ? " · 正在学第 " + learningNo + " 节" : "")', PAGE)

    def test_summary_wording_is_total(self):
        """待学区标题要写「共 M 节」，不是裸数字。"""
        self.assertIn('\'<span class="sum">共 \' + todoRows.length + " 节 · 还差 "', PAGE)


class TestCourseColumnShowsLearnedAndTotal(unittest.TestCase):
    def test_column_uses_explicit_wording(self):
        """不能再用 `done/total 节` 这种有歧义的写法。"""
        self.assertIn("已学 ' + esc(secDone) + \" 节 / 共 \" + esc(secTotal) + \" 节\"", PAGE)
        self.assertNotIn('esc(sec.done) + "/" + esc(sec.total) + " 节"', PAGE)

    def test_partial_cache_does_not_fake_total(self):
        """缓存不全时写「总数未知」，绝不拿缓存行数当总节数。"""
        self.assertIn("已学 ' + esc(secDone) +\n          ' 节 · 总数未知", PAGE)
        self.assertIn("c.sections_partial", PAGE)

    def test_progress_bar_notes_unknown_total(self):
        self.assertIn("缓存不全，总节数未知（点「刷新节列表」补全）", PAGE)

    def test_total_known_flag(self):
        self.assertIn("const totalKnown = !card.sections_partial;", PAGE)


class TestOneCodePathNotTwo(unittest.TestCase):
    """待学区只有一套过滤逻辑 —— 不存在「按账号走不同分支」。"""

    def test_single_filter_expression(self):
        self.assertEqual(PAGE.count("rows.filter(r => r.state !== \"done\")"), 1)

    def test_no_account_specific_branch_in_sections(self):
        seg = PAGE.split("function renderSections(state)", 1)[1].split("async function refreshSections", 1)[0]
        self.assertNotIn('state.label === "default"', seg)
        self.assertNotIn("state.label === 'default'", seg)


if __name__ == "__main__":
    unittest.main()
