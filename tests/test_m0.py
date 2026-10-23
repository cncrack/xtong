"""M0 冒烟测试：配置默认值 + 数据库建表。

运行：python -m unittest discover -s tests -v
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from xxt.config import Settings
from xxt.store.db import connect


class TestSettings(unittest.TestCase):
    def test_defaults_without_toml(self):
        with tempfile.TemporaryDirectory() as td:
            s = Settings(root=Path(td))
            self.assertEqual(s.platform["name"], "chaoxing")
            self.assertTrue(s.data_dir.is_absolute())
            self.assertFalse(s.quiz["enabled"])  # 弹题 AI 后置到 P2，默认关闭


class TestDB(unittest.TestCase):
    def test_connect_creates_schema_and_default_account(self):
        with tempfile.TemporaryDirectory() as td:
            conn = connect(Path(td) / "xxt.db")
            try:
                tables = {
                    r["name"]
                    for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                # AUTOINCREMENT 会附带生成 sqlite_sequence，因此断言用包含而非相等
                self.assertLessEqual(
                    {"meta", "accounts", "courses", "chapters", "questions", "run_logs"},
                    tables,
                )
                n = conn.execute("SELECT COUNT(*) AS c FROM accounts").fetchone()["c"]
                self.assertEqual(n, 1)
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
