"""运行日志的**高度**与**自动滚到最新**（2026-11-04 用户要求）。

## 用户原话

> 「运行日志高度调大些。然后日志显示老是被4秒刷新，看还得从头开始，
>   每次都要滚动条向下滚查看，很麻烦，能不能日志最新自动上滚。」

## 现象与根因

这页每 4 秒整块重绘 `#runlog` 的 `innerHTML`。**重绘会把滚动位置重置到顶部**，
所以每 4 秒都跳回最上面 —— 用户必须一直往下滚才能看最新一行。

## 处理两条（缺一不可）

1. **贴底跟随**：原来就在底部（`scrollHeight - scrollTop - clientHeight < 24`）
   → 重绘后滚到底，永远看最新。
2. **别抢用户的滚动**：用户往上翻看历史时，重绘后**还原原来的 scrollTop**，
   否则每 4 秒把他拽回底部，同样很难用。

另外把容器限高（`max-height:min(52vh,520px)`）—— 原来没有固定高度，
日志一长整页被撑得很高。
"""

from __future__ import annotations

import unittest

from xxt.panel_gxks.page import PAGE


class TestRunLogHeight(unittest.TestCase):
    def test_container_has_max_height(self):
        self.assertIn("#runlog{max-height:min(52vh,520px)", PAGE,
                      "运行日志要限高并在内部滚动，否则整页被长日志撑高")

    def test_container_scrolls(self):
        seg = PAGE.split("#runlog{", 1)[1].split("}", 1)[0]
        self.assertIn("overflow-y:auto", seg)

    def test_mobile_breakpoint(self):
        """窄屏也要限高（375px 约定）。"""
        self.assertIn("#runlog{max-height:46vh}", PAGE)


class TestRunLogAutoScroll(unittest.TestCase):
    """**倒序渲染：最新那行永远在最上面**（2026-11-04 用户明确要求）。

    用户原话「日志没有把最新的那一每次显示在上面，如果我要看以往日志，
    我可以鼠标往上滚」。

    之前实现成「正序 + 自动滚到底部」，方向搞反了：正序时最新行在最下面，
    必须滚到底才看得见，而每 4 秒重绘又会重置滚动位置 → 表现为「一直跳」。
    """

    def setUp(self):
        self.body = PAGE.split("function renderRunLog(", 1)[1].split("\nfunction ", 1)[0]

    def test_renders_tail_reversed(self):
        """必须把 tail **倒序**再渲染（最新在最前）。"""
        self.assertIn("tail.slice().reverse()", self.body,
                      "日志要倒序渲染，最新行在最上面")

    def test_resets_to_top_when_at_top(self):
        """贴在顶部 = 在看最新，重绘后回到顶部（天然跟最新，不跳）。"""
        self.assertIn("wasAtTop", self.body)
        self.assertIn("box.scrollTop = 0", self.body)

    def test_keeps_position_when_scrolled_down(self):
        """用户往下滚看历史时不能抢滚动。"""
        self.assertIn("keepTop", self.body)

    # ---------------------------------------------------------------- 2026-11-07 新增
    #
    # 用户**第二次**反馈同一件事：「我要往处查日志的时候，又会被刷新」。
    #
    # 只记 `scrollTop` 不够：新行插在**顶部**，内容整体下移一行，而 `scrollTop` 是
    # 像素偏移 → 你正看第 100 行，新来一行就变成在看第 99 行，每 30 秒（tick）滑一次。
    # 改法是「**锚点行号** + 翻历史时**暂停跟随**」。

    def test_anchors_by_line_index_not_pixels(self):
        """滚下去看历史时，必须按**行号**回滚（不是按像素）。"""
        self.assertIn("LOG_ANCHOR", self.body)
        self.assertIn("RUNLOG_ANCHOR_LINE", self.body)
        # 反证：不能只 restore keepTop 就完事（那正是被打断的写法）
        self.assertIn("anchor * lh", self.body,
                      "应按锚点行号 × 行高回滚，否则新行插入顶部会漂移")

    def test_pauses_following_when_scrolled_down(self):
        """离开顶部 → 暂停跟随；`wasAtTop` 必须同时看 `RUNLOG_FOLLOW`。"""
        self.assertIn("RUNLOG_FOLLOW", self.body)
        self.assertIn("keepTop < 24 && RUNLOG_FOLLOW", self.body)

    def test_has_back_to_latest_button(self):
        """暂停跟随后要有一个「回到最新」按钮，否则用户不知道怎么恢复。"""
        self.assertIn("scrolledToTop()", self.body)
        self.assertIn("回到最新", self.body)

    def test_scroll_listener_registered(self):
        """滚动事件要真的绑定（否则 `RUNLOG_FOLLOW` 永远不翻转）。"""
        self.assertIn('el("runlog").addEventListener("scroll"', PAGE)
        self.assertIn("RUNLOG_FOLLOW = false", PAGE)

    def test_log_header_shows_who(self):
        """**账号归属要显眼**（用户：「日志里面也看不出是谁的日志」）。

        原来只在 meta 里写 `· default（study 起于 22:41）` 一行小字，
        多账号同时跑时分不清。现在给一个**带底色的账号徽标** + 任务类型标签。
        """
        # 两个渲染路径都必须用它（renderLogBox / renderRunLog）
        self.assertIn("logMetaHtml(", self.body)
        helper = PAGE.split("function logMetaHtml(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("runlog-who", helper)
        self.assertIn("runlog-kind", helper)
        # 显示名优先、代号作小字（甲同学 vs default）
        self.assertIn("runlog-lab", helper)
        self.assertIn(".runlog-who{", PAGE)

    def test_has_hint_text(self):
        """要有「最新在上」的说明，用户才知道怎么翻历史。"""
        self.assertIn("runlog-hint", self.body)
        self.assertIn("最新在上", self.body)


class TestStalledNowReloads(unittest.TestCase):
    """顺带守住 stalled 的改判（别再退回「干等」）。"""

    def test_stalled_reloads(self):
        from xxt.adapters.gxks import driver
        self.assertEqual(driver.plan_action("stalled"), "reload")
        self.assertNotEqual(driver.plan_action("stalled"), "wait")


if __name__ == "__main__":
    unittest.main()
