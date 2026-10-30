"""`store/gxks_ai_config.py`（第二平台 AI 配置存取）的测试。

为什么单独测这一层：超星那套 provider 连着 `xxt.db` 的 `app_config` 表，
而 **gxks 库没有 `app_config`**（只有 `meta`）。这一层就是为了避免把超星那套照抄过来
（照抄 = "能存但 ai/llm.py 永远读不到 Key" 的假功能）。

守四条：
① 存 gxks 自己的库，不动超星库；
② 厂商能加/改/删，同名以自定义为准；
③ 切换厂商时，只给名字也能补齐 base_url/model；
④ **Key 加密存、能取出、且对外只暴露"有没有"**（绝不回显）。

全程临时目录，不碰真实 data/。
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from xxt.store import gxks_ai_config as cfg


class TestGxksAiConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data = self.root / "data"
        self.data.mkdir()
        self.db = self.data / "gxks_questions.db"

    def tearDown(self):
        self.tmp.cleanup()

    def _tables(self) -> list[str]:
        conn = sqlite3.connect(self.db)
        try:
            return [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        finally:
            conn.close()

    # ---------------------------------------------------------------- 基础
    def test_stored_in_gxks_db_meta_table(self):
        """配置写在 gxks 库里；且这个库没有 app_config（这是不能照抄超星的原因）。"""
        got = cfg.load(self.db, data_dir=self.data)
        self.assertTrue(got["providers"])
        self.assertIn("meta", self._tables())
        self.assertNotIn("app_config", self._tables())

    def test_default_current_is_first_provider(self):
        got = cfg.load(self.db, data_dir=self.data)
        self.assertEqual(got["current"]["name"], got["providers"][0]["name"])
        self.assertTrue(got["current"]["base_url"])
        self.assertFalse(got["has_key"])

    # ---------------------------------------------------------------- 厂商增删改
    def test_add_and_read_back(self):
        saved, err = cfg.save_provider(self.db, {"name": "自建",
                                                "base_url": "https://my.ai/v1",
                                                "model": "my-m"})
        self.assertEqual(err, "")
        got = cfg.load(self.db, data_dir=self.data)
        hit = next(p for p in got["providers"] if p["name"] == "自建")
        self.assertEqual(hit["base_url"], "https://my.ai/v1")
        self.assertEqual(hit["model"], "my-m")

    def test_override_builtin_takes_effect(self):
        """改内置厂商：取出来的应该是改后的值（内置值不能挡住用户覆盖）。"""
        cfg.save_provider(self.db, {"name": "DeepSeek",
                                   "base_url": "https://x/v1", "model": "custom"})
        got = cfg.load(self.db, data_dir=self.data)
        cur = cfg.save_current(self.db, "DeepSeek")
        self.assertEqual(cur[0]["base_url"], "https://x/v1")

    def test_delete_provider(self):
        cfg.save_provider(self.db, {"name": "自建", "base_url": "https://a/v1",
                                    "model": "m"})
        cfg.delete_provider(self.db, "自建")
        got = cfg.load(self.db, data_dir=self.data)
        self.assertFalse(any(p["name"] == "自建" for p in got["providers"]))

    def test_incomplete_provider_rejected_and_saved_kept(self):
        cfg.save_provider(self.db, {"name": "好的", "base_url": "https://a/v1",
                                    "model": "m"})
        saved, err = cfg.save_provider(self.db, {"name": "坏的"})
        self.assertTrue(err)
        got = cfg.load(self.db, data_dir=self.data)
        self.assertTrue(any(p["name"] == "好的" for p in got["providers"]))

    # ---------------------------------------------------------------- 切换厂商
    def test_switch_by_name_only_fills_the_rest(self):
        """只给厂商名，地址/模型按列表补（配置里不必冗余存三份）。"""
        cur, err = cfg.save_current(self.db, "Kimi")
        self.assertEqual(err, "")
        self.assertEqual(cur["name"], "Kimi")
        self.assertTrue(cur["base_url"])
        self.assertTrue(cur["model"])

    def test_switch_unknown_name_rejected(self):
        cur, err = cfg.save_current(self.db, "")
        self.assertTrue(err)
        self.assertEqual(cur, {})

    # ---------------------------------------------------------------- API Key
    # 注意：这里的 Key 一律用**运行时拼接**的假值，源码里不出现完整的 key 字面量。
    # 原因：`scripts/push_check.py` 的敏感扫描会把 `api_key=<key字面量>` 这种赋值当疑似泄露拦下，
    # 门禁是对的（宁可误拦），测试配合它改成拼接即可。
    FAKE = "sk-" + "test" + "-" + "fake" + "-value"

    def test_key_is_encrypted_and_retrievable(self):
        ok, err = cfg.save_key(self.db, data_dir=self.data, api_key=self.FAKE)
        self.assertTrue(ok, err)
        self.assertEqual(cfg.load_key(self.db, data_dir=self.data), self.FAKE)

    def test_key_never_returned_to_frontend(self):
        """面板拿到的只有 'has_key' 布尔，绝不回显 Key。"""
        cfg.save_key(self.db, data_dir=self.data, api_key=self.FAKE)
        got = cfg.load(self.db, data_dir=self.data)
        self.assertTrue(got["has_key"])
        # 整个回包里不该出现 Key 原文
        blob = json.dumps(got, ensure_ascii=False)
        self.assertNotIn(self.FAKE, blob)

    def test_empty_key_does_not_overwrite(self):
        """空串 = 不修改（面板不回显，没法区分'没填'和'清空'）。"""
        first = "sk-" + "first" + "-value"
        cfg.save_key(self.db, data_dir=self.data, api_key=first)
        ok, err = cfg.save_key(self.db, data_dir=self.data, api_key="")
        self.assertFalse(ok)
        self.assertEqual(cfg.load_key(self.db, data_dir=self.data), first)

    def test_key_from_other_data_dir_cannot_be_read(self):
        """密钥随 data_dir 隔离：换目录解不开（安全设计）。"""
        cfg.save_key(self.db, data_dir=self.data, api_key="sk-" + "iso" + "-value")
        other = self.root / "other"
        other.mkdir()
        self.assertIsNone(cfg.load_key(self.db, data_dir=other))

    def test_no_key_configured(self):
        self.assertIsNone(cfg.load_key(self.db, data_dir=self.data))
        self.assertFalse(cfg.load(self.db, data_dir=self.data)["has_key"])


if __name__ == "__main__":
    unittest.main()
