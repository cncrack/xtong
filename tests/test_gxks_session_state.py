"""登录态快照（保存 / 注入）与凭据路径的单元测试。

守三件事：
① 快照文件名必须命中 `.gitignore` 的 `storage_state*.json`（等同登录态，不能入库）；
② `state_summary()` **只**给 cookie 的名字与数量，绝不返回值；
③ `restore_cookies()` 只注入平台域下发的 cookie，且不导航、不发请求。

全程替身：不连浏览器、不发请求、不碰真实 data/。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from xxt.adapters.gxks import browser as gbrowser
from xxt.adapters.gxks import login as glogin

PLATFORM_COOKIE = {"name": ".ASPXAUTH", "value": "SECRET-A",
                   "domain": "www.gaoxiaokaoshi.com", "path": "/", "expires": -1}
FOREIGN_COOKIE = {"name": "other", "value": "SECRET-B",
                  "domain": ".example.com", "path": "/", "expires": -1}


def _settings(root: Path):
    return SimpleNamespace(data_dir=root / "data", raw={})


def _write_state(path: Path, cookies: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"cookies": cookies, "origins": []}, ensure_ascii=False),
                    encoding="utf-8")


class FakeContext:
    def __init__(self, cookies: list[dict] | None = None) -> None:
        self.cookies_list = cookies or []
        self.added: list[dict] = []
        self.state_paths: list[str] = []

    def storage_state(self, path: str) -> None:
        self.state_paths.append(path)
        _write_state(Path(path), self.cookies_list)

    def add_cookies(self, cookies: list[dict]) -> None:
        self.added.extend(cookies)


class TestStatePath(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.settings = _settings(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_name_is_gitignored(self):
        p = gbrowser.state_path(self.settings, "")
        self.assertEqual(p.name, "storage_state_gxks.json")
        self.assertTrue(p.name.startswith("storage_state"))   # .gitignore / push_check 的拦截前缀

    def test_label_gets_own_file(self):
        p = gbrowser.state_path(self.settings, "Jia")
        self.assertEqual(p.name, "storage_state_gxks_jia.json")
        self.assertEqual(p.parent, self.root / "data")

    def test_invalid_label_is_refused(self):
        with self.assertRaises(gbrowser.SessionError) as ctx:
            gbrowser.state_path(self.settings, "../etc")
        self.assertEqual(ctx.exception.code, "label")


class TestStateSummary(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.path = self.root / "data" / "storage_state_gxks_jia.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_file(self):
        self.assertEqual(gbrowser.state_summary(self.path), {"saved": False})

    def test_broken_file_counts_as_not_saved(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{not json", encoding="utf-8")
        self.assertEqual(gbrowser.state_summary(self.path), {"saved": False})

    def test_summary_lists_names_but_never_values(self):
        with_expiry = dict(PLATFORM_COOKIE, name="Clerk", expires=1_700_000_000)
        _write_state(self.path, [PLATFORM_COOKIE, with_expiry])
        info = gbrowser.state_summary(self.path)
        self.assertTrue(info["saved"])
        self.assertEqual(info["cookies"], 2)
        self.assertEqual(sorted(info["names"]), [".ASPXAUTH", "Clerk"])
        self.assertEqual(info["session_level"], [".ASPXAUTH"])   # 只有它没有 expires
        self.assertNotIn("SECRET-A", json.dumps(info, ensure_ascii=False))


class TestSaveAndRestore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.settings = _settings(self.root)
        self.path = gbrowser.state_path(self.settings, "jia")

    def tearDown(self):
        self.tmp.cleanup()

    # ---------------------------------------------------------------- context_of
    def test_context_of_prefers_session_context(self):
        ctx = FakeContext()
        self.assertIs(gbrowser.context_of(gbrowser.Session("managed", context=ctx)), ctx)

    def test_context_of_falls_back_to_first_cdp_context(self):
        ctx = FakeContext()
        session = gbrowser.Session("cdp", browser=SimpleNamespace(contexts=[ctx]))
        self.assertIs(gbrowser.context_of(session), ctx)

    def test_context_of_none_when_nothing(self):
        self.assertIsNone(gbrowser.context_of(gbrowser.Session("cdp")))

    # ---------------------------------------------------------------- save
    def test_save_state_writes_file_and_returns_summary(self):
        ctx = FakeContext([PLATFORM_COOKIE])
        info = gbrowser.save_state(gbrowser.Session("cdp", context=ctx), self.path)
        self.assertTrue(self.path.exists())
        self.assertTrue(info["saved"])
        self.assertEqual(info["cookies"], 1)
        self.assertEqual(ctx.state_paths, [str(self.path)])

    def test_save_state_without_context_raises(self):
        with self.assertRaises(gbrowser.SessionError):
            gbrowser.save_state(gbrowser.Session("cdp"), self.path)

    # ---------------------------------------------------------------- restore
    def test_restore_injects_only_platform_cookies(self):
        _write_state(self.path, [PLATFORM_COOKIE, FOREIGN_COOKIE])
        ctx = FakeContext()
        n = gbrowser.restore_cookies(gbrowser.Session("cdp", context=ctx), self.path)
        self.assertEqual(n, 1)
        self.assertEqual([c["name"] for c in ctx.added], [".ASPXAUTH"])

    def test_restore_missing_file_is_zero(self):
        ctx = FakeContext()
        self.assertEqual(gbrowser.restore_cookies(gbrowser.Session("cdp", context=ctx),
                                                  self.path), 0)
        self.assertEqual(ctx.added, [])

    def test_restore_broken_file_is_zero(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("not json", encoding="utf-8")
        ctx = FakeContext()
        self.assertEqual(gbrowser.restore_cookies(gbrowser.Session("cdp", context=ctx),
                                                  self.path), 0)
        self.assertEqual(ctx.added, [])

    def test_restore_state_roundtrip(self):
        """save 出来的文件能原样 restore 回去（不必手写 cookie 结构）。"""
        save_ctx = FakeContext([PLATFORM_COOKIE])
        gbrowser.save_state(gbrowser.Session("cdp", context=save_ctx), self.path)
        restore_ctx = FakeContext()
        n = gbrowser.restore_cookies(gbrowser.Session("cdp", context=restore_ctx), self.path)
        self.assertEqual(n, 1)
        self.assertEqual(restore_ctx.added[0]["value"], "SECRET-A")


class TestCredentialsPath(unittest.TestCase):
    def test_path_follows_data_dir(self):
        settings = _settings(Path("E:/xxt-gxks"))
        self.assertEqual(glogin.credentials_path(settings),
                         Path("E:/xxt-gxks") / "data" / "account.local.json")

    def test_placeholder_counts_as_not_configured(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "account.local.json"
            p.write_text(json.dumps({"name": "在这里填学号", "pw": "x"}), encoding="utf-8")
            self.assertFalse(glogin.is_configured(p))

    def test_real_looking_credentials_are_configured(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "account.local.json"
            p.write_text(json.dumps({"name": "s1001", "pw": "pw"}), encoding="utf-8")
            self.assertTrue(glogin.is_configured(p))


if __name__ == "__main__":
    unittest.main()
