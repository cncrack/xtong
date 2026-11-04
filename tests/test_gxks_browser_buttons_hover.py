"""浏览器区按钮必须作用于**当前显示的那个账号**（2026-11-05 查出的真 bug）。

## 现象

账号表里鼠标**悬停**某一行时，右侧「浏览器与会话」整块会切成**那一行账号**的信息
（端口 / profile / 「面板启动 时刻」/ 已登录状态全都变）——这是 2026-11-02 用户要求的
「悬停预览」，代码在 `hoverAcctSide()` → `renderBrowserFor(state, HOVER_LABEL)`。

但浏览器区那 5 个按钮（停止 / 启动并登录 / 检查 / 恢复登录 / 重新登录）
原来调的是 `post("/api/browser/stop")` —— `post()` 自动注入 `label: LABEL`，
而 **`LABEL` 是「选中账号」，不是「悬停账号」**。

于是出现：**你看着 B 的浏览器信息，点「停止」停的是 A 的浏览器。**
「启动并登录」更危险：会把 A 的 Chrome 拉起来，而你以为在给 B 起。

## 修法

新增 `browserLabel()`：`HOVER_LABEL || LABEL` —— 浏览器区显示谁，按钮就作用于谁。
5 个按钮全部显式传 `{ label: browserLabel() }`。

## 为什么值得单测

这是个**只在前端、只在悬停时、只错账号不报错**的 bug：接口测试全绿、点下去也有
正常的「已停止」提示，唯一的问题是**停错了对象**。靠人工点很难发现，
所以这里把「按钮不许用 LABEL」钉成断言。
"""

from __future__ import annotations

import re
import unittest

from xxt.panel_gxks.page import PAGE


class TestBrowserButtonsUseShownAccount(unittest.TestCase):
    def test_helper_exists_and_prefers_hover(self):
        """`browserLabel()` 必须存在，且优先取悬停账号。"""
        self.assertIn("function browserLabel()", PAGE)
        body = PAGE.split("function browserLabel()", 1)[1].split("\n}", 1)[0]
        self.assertIn("HOVER_LABEL || LABEL", body,
                      "浏览器区按钮要作用于**当前显示**的账号（悬停优先）")

    def test_stop_button_passes_browser_label(self):
        """右侧「停止」按钮必须显式传当前显示的账号，不能靠 `post()` 的默认 `LABEL`。"""
        seg = PAGE.split('id="btn-bstop"', 1)[1].split(">", 1)[0]
        self.assertIn("browserLabel()", seg)
        self.assertNotIn('onclick="post(\'/api/browser/stop\')"', PAGE,
                         "裸调 /api/browser/stop 会用选中的 LABEL → 悬停时停错账号")

    def test_all_browser_group_actions_pass_browser_label(self):
        """启动 / 检查 / 恢复 / 重登四个按钮同样不能落到 `LABEL`。"""
        for fn, path in (
            ("startBrowser", "/api/browser/start"),
            ("checkSession", "/api/session/check"),
            ("restoreSession", "/api/session/restore"),
            ("reloginSession", "/api/session/relogin"),
        ):
            with self.subTest(fn=fn):
                body = PAGE.split(f"function {fn}(", 1)[1].split("\n}", 1)[0]
                self.assertIn(path, body)
                self.assertIn("browserLabel()", body,
                              f"{fn} 必须传当前显示的账号")

    def test_no_bare_post_for_browser_actions(self):
        """兜底：浏览器区的接口调用一律不许出现"裸 post(路径)"形态。

        `post()` 会把 `label: LABEL` 塞进 body —— 对「浏览器区」这类
        **跟着悬停变**的界面，那个默认值恰恰是错的。
        """
        for path in ("/api/browser/stop", "/api/browser/start",
                     "/api/session/check", "/api/session/restore",
                     "/api/session/relogin"):
            with self.subTest(path=path):
                bare = re.findall(
                    r"post\(\s*['\"]" + re.escape(path) + r"['\"]\s*\)", PAGE)
                self.assertEqual(bare, [],
                                 f"{path} 出现裸 post()：会用选中的 LABEL，悬停时作用于错账号")


class TestHoverStillDrivesBrowserPanel(unittest.TestCase):
    """确认前提成立：悬停确实会切换浏览器区（否则上面的修复就没有意义）。"""

    def test_hover_switches_browser_panel(self):
        body = PAGE.split("function hoverAcctSide(", 1)[1].split("\n}", 1)[0]
        self.assertIn("renderBrowserFor", body)

    def test_mouseout_restores_selected(self):
        self.assertIn('hoverAcctSide("")', PAGE)

    def test_polling_keeps_hover(self):
        """4 秒轮询不能把悬停预览弹回选中账号（否则按钮目标会中途跳变）。"""
        body = PAGE.split("async function load(", 1)[1].split("\n}", 1)[0]
        self.assertIn("HOVER_LABEL", body)


if __name__ == "__main__":
    unittest.main()
