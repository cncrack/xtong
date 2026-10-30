"""第二平台浏览器后端（`adapters/gxks/browser.py`）的单测。

全程用替身，**不真起浏览器**：这里要钉死的是分支与收尾语义，不是 Playwright 本身。

重点四条：
① 缺省后端必须是 `cdp`（改这一步等于改所有现存脚本的行为）；
② `cdp` 下找不到页面 / 连不上，**都要立刻断开**再抛（Q31：常连会吞掉人工交卷的 confirm）；
③ `managed` 的 profile 必须落在 `data/profiles/<代号>`，代号非法一律拒（两账号共用一份
   登录态是最难查的错）；
④ `managed` 的无头默认 true（容器），且不受 `[stealth] headless` 影响。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from xxt.adapters.gxks import browser as gbrowser
from xxt.adapters.gxks import selectors


class FakePage:
    def __init__(self, frames: list[str] | None = None, url: str = "") -> None:
        self.frames = [SimpleNamespace(url=u) for u in (frames or [])]
        self.url = url
        self.gotos: list[str] = []

    def goto(self, url: str, **_kw) -> None:
        self.gotos.append(url)
        self.url = url


class FakeBrowser:
    def __init__(self, pages: list[FakePage]) -> None:
        self.contexts = [SimpleNamespace(pages=pages)]
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeContext:
    def __init__(self, pages: list[FakePage] | None = None) -> None:
        self.pages = pages or []
        self.closed = False

    def new_page(self) -> FakePage:
        page = FakePage()
        self.pages.append(page)
        return page

    def close(self) -> None:
        self.closed = True


class FakePlaywright:
    def __init__(self, chromium) -> None:
        self.chromium = chromium
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class FakeChromium:
    def __init__(self, browser: FakeBrowser | None = None, error: Exception | None = None):
        self.browser = browser
        self.error = error
        self.url = ""

    def connect_over_cdp(self, url: str) -> FakeBrowser:
        self.url = url
        if self.error is not None:
            raise self.error
        return self.browser


class StubSettings:
    """只提供 `browser.py` 真正会读的两个属性，避免测试依赖真实配置与 DB。"""

    def __init__(self, root: Path, raw: dict | None = None) -> None:
        self.root = root
        self.data_dir = root / "data"
        self.raw = raw or {}


def _list_page() -> FakePage:
    return FakePage(frames=[selectors.LIST_PATH], url=f"http://platform.invalid{selectors.LIST_PATH}")


class TestBackendSelection(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_is_cdp(self):
        """没配 `browser_backend` 时必须还是 cdp —— 现存脚本一行都不用改。"""
        self.assertEqual(gbrowser.backend_of(StubSettings(self.root)), "cdp")

    def test_managed_and_case_insensitive(self):
        s = StubSettings(self.root, {"gxks": {"browser_backend": " Managed "}})
        self.assertEqual(gbrowser.backend_of(s), "managed")

    def test_unknown_value_falls_back_with_warning(self):
        """拼错不该让程序起不来，但必须留痕。"""
        s = StubSettings(self.root, {"gxks": {"browser_backend": "cdpx"}})
        with self.assertLogs("xxt.adapters.gxks.browser", level="WARNING"):
            self.assertEqual(gbrowser.backend_of(s), "cdp")


class TestProfileDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_profile_under_data_profiles(self):
        path = gbrowser.profile_dir(StubSettings(self.root), "Jia")
        self.assertEqual(path, self.root / "data" / "profiles" / "jia")

    def test_bad_label_rejected(self):
        """非法代号不能回落成某个默认目录，否则两个账号会共用一份登录态。"""
        for bad in ("", "../x", "甲", "a" * 30):
            with self.subTest(bad=bad):
                with self.assertRaises(gbrowser.SessionError) as ctx:
                    gbrowser.profile_dir(StubSettings(self.root), bad)
                self.assertEqual(ctx.exception.code, "label")


class TestCdpBackend(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.settings = StubSettings(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_picks_list_page_and_closes_on_exit(self):
        page = _list_page()
        fake_browser = FakeBrowser([page])
        pw = FakePlaywright(FakeChromium(fake_browser))
        with mock.patch.object(gbrowser, "_start_playwright", return_value=pw):
            session = gbrowser.open_session(self.settings, label="jia", port=9301)
        self.assertEqual(session.backend, "cdp")
        self.assertIs(session.page, page)
        self.assertFalse(session.managed)

        session.close()
        self.assertTrue(fake_browser.closed)      # Q31：断开
        self.assertTrue(pw.stopped)
        self.assertIsNone(session.page)           # 幂等：关完就不该再留着引用

    def test_port_falls_back_to_config_then_default(self):
        fake_browser = FakeBrowser([_list_page()])
        chromium = FakeChromium(fake_browser)
        pw = FakePlaywright(chromium)
        configured = StubSettings(self.root, {"gxks": {"port_base": 9400}})
        with mock.patch.object(gbrowser, "_start_playwright", return_value=pw):
            gbrowser.open_session(configured).close()
            self.assertIn(":9400", chromium.url)
            gbrowser.open_session(StubSettings(self.root)).close()
            self.assertIn(":9222", chromium.url)

    def test_no_page_disconnects_before_raising(self):
        """连上了却没平台页面 → 断开再报错，绝不留一条常连（Q31）。"""
        fake_browser = FakeBrowser([FakePage(frames=["http://other.invalid/"])])
        pw = FakePlaywright(FakeChromium(fake_browser))
        with mock.patch.object(gbrowser, "_start_playwright", return_value=pw):
            with self.assertRaises(gbrowser.SessionError) as ctx:
                gbrowser.open_session(self.settings)
        self.assertEqual(ctx.exception.code, "no_page")
        self.assertTrue(fake_browser.closed)
        self.assertTrue(pw.stopped)

    def test_connect_error_stops_playwright(self):
        pw = FakePlaywright(FakeChromium(error=OSError("connection refused")))
        with mock.patch.object(gbrowser, "_start_playwright", return_value=pw):
            with self.assertRaises(gbrowser.SessionError) as ctx:
                gbrowser.open_session(self.settings, port=9222)
        self.assertEqual(ctx.exception.code, "connect")
        self.assertIn("remote-debugging-port=9222", ctx.exception.hint)   # 给人照抄的起法
        self.assertTrue(pw.stopped)

    def test_start_url_ignored_under_cdp(self):
        """cdp 下不导航：人已经开好页面，脚本不该把它拽走。"""
        page = _list_page()
        pw = FakePlaywright(FakeChromium(FakeBrowser([page])))
        with mock.patch.object(gbrowser, "_start_playwright", return_value=pw):
            session = gbrowser.open_session(self.settings, start_url=selectors.MAIN_PAGE)
        self.assertEqual(page.gotos, [])
        session.close()


class TestManagedBackend(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.managed = StubSettings(self.root, {"gxks": {"browser_backend": "managed"}})
        self.page = FakePage(url="about:blank")
        self.ctx = FakeContext([self.page])
        self.pw = FakePlaywright(FakeChromium())

    def tearDown(self):
        self.tmp.cleanup()

    def test_launches_profile_headless_by_default(self):
        with mock.patch.object(gbrowser, "_start_playwright", return_value=self.pw), \
             mock.patch.object(gbrowser, "persistent_launch", return_value=self.ctx) as launch:
            session = gbrowser.open_session(self.managed, label="jia",
                                            start_url=selectors.MAIN_PAGE)
        kwargs = launch.call_args
        self.assertEqual(kwargs.args[1], self.root / "data" / "profiles" / "jia")
        self.assertIs(kwargs.kwargs["headless"], True)     # 容器形态默认无头
        self.assertEqual(self.page.gotos, [selectors.MAIN_PAGE])
        self.assertTrue(session.managed)

        session.close()
        self.assertTrue(self.ctx.closed)                   # 自己起的浏览器自己关
        self.assertTrue(self.pw.stopped)

    def test_headless_can_be_turned_off(self):
        cfg = StubSettings(self.root, {"gxks": {"browser_backend": "managed",
                                               "managed_headless": False}})
        with mock.patch.object(gbrowser, "_start_playwright", return_value=self.pw), \
             mock.patch.object(gbrowser, "persistent_launch", return_value=self.ctx) as launch:
            gbrowser.open_session(cfg, label="jia").close()
        self.assertIs(launch.call_args.kwargs["headless"], False)

    def test_empty_context_gets_new_page(self):
        ctx = FakeContext([])
        with mock.patch.object(gbrowser, "_start_playwright", return_value=self.pw), \
             mock.patch.object(gbrowser, "persistent_launch", return_value=ctx):
            session = gbrowser.open_session(self.managed, label="jia")
        self.assertIsNotNone(session.page)
        session.close()

    def test_launch_error_is_wrapped(self):
        with mock.patch.object(gbrowser, "_start_playwright", return_value=self.pw), \
             mock.patch.object(gbrowser, "persistent_launch",
                               side_effect=RuntimeError("Executable doesn't exist")):
            with self.assertRaises(gbrowser.SessionError) as ctx:
                gbrowser.open_session(self.managed, label="jia")
        self.assertEqual(ctx.exception.code, "browser")
        self.assertIn("playwright install chromium", ctx.exception.hint)
        self.assertTrue(self.pw.stopped)

    def test_navigate_error_closes_context(self):
        page = mock.Mock()
        page.goto.side_effect = RuntimeError("net::ERR_NAME_NOT_RESOLVED")
        ctx = FakeContext([page])
        with mock.patch.object(gbrowser, "_start_playwright", return_value=self.pw), \
             mock.patch.object(gbrowser, "persistent_launch", return_value=ctx):
            with self.assertRaises(gbrowser.SessionError) as err:
                gbrowser.open_session(self.managed, label="jia", start_url="http://x.invalid/")
        self.assertEqual(err.exception.code, "navigate")
        self.assertTrue(ctx.closed)
        self.assertTrue(self.pw.stopped)


if __name__ == "__main__":
    unittest.main()
