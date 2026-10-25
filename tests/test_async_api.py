"""异步/同步 Playwright 隔离测试（2026-10-25 事故回归防护）。

事故：异步学习引擎误用同步版 `hardened_context`（`browser.new_context()` 在 async 下
返回 coroutine），`ctx.add_init_script` 抛 AttributeError，会话在浏览器阶段静默崩溃——
日志只有额度行、界面无反应、traceback 被 DEVNULL 吞掉。
"""

from __future__ import annotations

import unittest
from pathlib import Path

from xxt.core.stealth import STEALTH_JS, async_hardened_context


class _FakeCtx:
    def __init__(self) -> None:
        self.scripts: list[str] = []

    async def add_init_script(self, script: str) -> None:
        self.scripts.append(script)


class _FakeBrowser:
    def __init__(self) -> None:
        self.kw: dict = {}
        self.ctx = _FakeCtx()

    async def new_context(self, **kw):
        self.kw = kw
        return self.ctx


class TestAsyncHardenedContext(unittest.IsolatedAsyncioTestCase):
    async def test_awaits_and_injects_stealth(self):
        browser = _FakeBrowser()
        ctx = await async_hardened_context(browser, storage_state="s.json")
        self.assertIs(ctx, browser.ctx)
        self.assertEqual(ctx.scripts, [STEALTH_JS])
        self.assertEqual(browser.kw["storage_state"], "s.json")
        self.assertTrue(str(browser.kw["user_agent"]).startswith("Mozilla/"))
        self.assertEqual(browser.kw["locale"], "zh-CN")
        self.assertEqual(browser.kw["viewport"], {"width": 1280, "height": 800})


class TestAsyncEngineUsesAsyncApi(unittest.TestCase):
    """源码级守护：异步引擎不得再出现同步 API 调用（两起同源事故）。"""

    def setUp(self) -> None:
        self.text = (Path(__file__).resolve().parents[1]
                     / "src" / "xxt" / "adapters" / "chaoxing" / "astudy.py").read_text(
                         encoding="utf-8")

    def test_astudy_has_no_sync_context_call(self):
        self.assertIn("await async_hardened_context(", self.text)
        self.assertNotIn("= hardened_context(", self.text)

    def test_astudy_has_no_sync_response_sniffer(self):
        # 同步 sniffer 在 async 下 resp.json() 永不 await → 昵称抓不到 + RuntimeWarning
        self.assertNotIn("from .study import username_sniffer", self.text)
        self.assertIn("await response.json()", self.text)


if __name__ == "__main__":
    unittest.main()
