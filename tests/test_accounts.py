"""多账号地基测试：加密、迁移、三层配置合并。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from xxt.config import DEFAULTS, Settings
from xxt.core import accounts as acc
from xxt.core.crypto import decrypt, encrypt, master_key
from xxt.store.db import connect


class TestCrypto(unittest.TestCase):
    def test_roundtrip(self):
        key = master_key(Path(tempfile.mkdtemp()))
        blob = encrypt(key, " secre)t密码Pass!# ")
        self.assertNotIn(b"Pass", blob)
        self.assertEqual(decrypt(key, blob), " secre)t密码Pass!# ")

    def test_unique_nonce(self):
        key = b"k" * 32
        self.assertNotEqual(encrypt(key, "x"), encrypt(key, "x"))


class TestAccounts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.tmp.name) / "t.db")
        self.key = master_key(Path(self.tmp.name))

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_migration_added_columns(self):
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(accounts)")}
        self.assertLessEqual(
            {"username_enc", "password_enc", "window_start", "proxy", "priority", "start_at",
             "daily_max_minutes", "daily_max_tasks", "window_end"}, cols
        )

    def test_add_list_credentials(self):
        account_id = acc.add_account(self.conn, self.key, "小号A", "user1", "pw1", priority=2.0)
        rows = acc.list_accounts(self.conn)
        self.assertEqual(rows[-1]["label"], "小号A")
        self.assertEqual(rows[-1]["priority"], 2.0)
        self.assertNotIn("password", " ".join(rows[-1].keys()))
        user, pw = acc.get_credentials(self.conn, self.key, account_id)
        self.assertEqual((user, pw), ("user1", "pw1"))

    def test_overrides_merge(self):
        settings = Settings.__new__(Settings)  # 不走文件，直接注入默认
        settings.pace = dict(DEFAULTS["pace"])
        settings.quota = dict(DEFAULTS["quota"])
        account_id = acc.add_account(self.conn, self.key, "B", "u", "p")
        acc.set_overrides(self.conn, str(account_id), window_start="09:30", window_end="22:30",
                          daily_max_tasks=20)
        row = acc.find_account(self.conn, str(account_id))
        pace = acc.effective_pace(settings, row)
        quota = acc.effective_quota(settings, row)
        self.assertEqual(pace["daily_window"], ["09:30", "22:30"])
        self.assertEqual(quota["daily_tasks"], 20)
        self.assertEqual(quota["daily_minutes"], DEFAULTS["quota"]["daily_minutes"])

    def test_remove(self):
        acc.add_account(self.conn, self.key, "C", "u", "p")
        self.assertTrue(acc.remove_account(self.conn, "C"))
        self.assertFalse(acc.remove_account(self.conn, "不存在"))


if __name__ == "__main__":
    unittest.main()
