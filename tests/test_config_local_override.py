"""配置分层测试（2026-10-30 P0）：本机覆盖层 + 面板环境变量。

为什么单独守这一层：`config/settings.toml` 是两个平台共用的一份**被跟踪文件**，
第二平台的差异改走 `config/settings.local.toml`（不入库）。这一层若失效，
表现是"改了配置不生效"或"合并回 main 把超星配置顶掉"——两种情况都不会报错，
所以只能靠测试盯住。
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from xxt.config import Settings

_TOML = """[platform]
name = "chaoxing"
login_url = "https://example.invalid/login"

[quota]
daily_minutes = 240
daily_tasks = 60
"""

_LOCAL = """[platform]
name = "gxks"

[panel]
host = "127.0.0.1"
port = 8301

[gxks]
auto_submit = false
allowed_types = "2"
"""


class TestLocalOverride(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "config").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, name: str, text: str) -> None:
        (self.root / "config" / name).write_text(text, encoding="utf-8")

    def test_local_overrides_tracked_toml(self):
        """覆盖层赢；它没提到的键仍来自被跟踪的 settings.toml。"""
        self._write("settings.toml", _TOML)
        self._write("settings.local.toml", _LOCAL)
        s = Settings(root=self.root)
        self.assertEqual(s.platform["name"], "gxks")
        self.assertEqual(s.platform["login_url"], "https://example.invalid/login")  # 未被覆盖
        self.assertEqual(s.raw["panel"], {"host": "127.0.0.1", "port": 8301})
        self.assertEqual(s.raw["gxks"]["allowed_types"], "2")
        # 深度合并：不能把整个 [quota] 段抹掉
        self.assertEqual(s.quota["daily_minutes"], 240)
        self.assertEqual(s.quota["daily_tasks"], 60)

    def test_no_local_file_keeps_toml(self):
        self._write("settings.toml", _TOML)
        s = Settings(root=self.root)
        self.assertEqual(s.platform["name"], "chaoxing")
        self.assertNotIn("gxks", s.raw)

    def test_broken_local_file_is_ignored(self):
        """覆盖层写坏了不能拖垮程序，也不能只生效一半。"""
        self._write("settings.toml", _TOML)
        self._write("settings.local.toml", "[platform\nname = ")
        with self.assertLogs("xxt.config", level="WARNING"):
            s = Settings(root=self.root)
        self.assertEqual(s.platform["name"], "chaoxing")

    def test_env_overrides_panel(self):
        self._write("settings.toml", _TOML)
        self._write("settings.local.toml", _LOCAL)
        with mock.patch.dict(os.environ, {"XXT_PANEL_HOST": "0.0.0.0",
                                          "XXT_PANEL_PORT": "8399"}):
            s = Settings(root=self.root)
        self.assertEqual(s.raw["panel"], {"host": "0.0.0.0", "port": 8399})

    def test_env_creates_panel_section_when_absent(self):
        """没有 [panel] 段时环境变量也要能建出这一段（容器部署场景）。"""
        self._write("settings.toml", _TOML)
        with mock.patch.dict(os.environ, {"XXT_PANEL_HOST": "0.0.0.0"}):
            s = Settings(root=self.root)
        self.assertEqual(s.raw["panel"]["host"], "0.0.0.0")
        # 未设端口时不凭空造值：由各平台面板自己的缺省值兜（超星 8300 / 第二平台 8301）
        self.assertNotIn("port", s.raw["panel"])

    def test_bad_env_value_is_ignored(self):
        self._write("settings.toml", _TOML)
        self._write("settings.local.toml", _LOCAL)
        with mock.patch.dict(os.environ, {"XXT_PANEL_PORT": "abc"}):
            with self.assertLogs("xxt.config", level="WARNING"):
                s = Settings(root=self.root)
        self.assertEqual(s.raw["panel"]["port"], 8301)


if __name__ == "__main__":
    unittest.main()
