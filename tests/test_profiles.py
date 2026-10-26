"""画像字段语义测试（2026-10-27 画像表纠偏）。

三条守护：
① `name` 手填姓名**不得**回退成平台昵称（前端曾把 display_name 填进姓名框，一点保存就污染）；
② `source=None` 表示“一个字没改”，必须保留库里原来源，不能被抹成「手动」；
③ 真改了字段时来源才应更新。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from xxt.config import Settings
from xxt.core import stats
from xxt.store.db import connect


class TestProfileFields(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(root=Path(self.tmp.name))
        self.conn = connect(self.settings.db_path)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_name_does_not_fall_back_to_nickname(self):
        """name 只存手填值；昵称走 platform_name/display_name，绝不写进 name。"""
        self.conn.execute("UPDATE accounts SET platform_username='林鑫阳' WHERE id=1")
        self.conn.commit()
        p = stats.get_profile(self.conn, 1)
        self.assertEqual(p["name"], "")
        self.assertEqual(p["platform_name"], "林鑫阳")
        self.assertEqual(p["display_name"], "林鑫阳")  # 仅供显示

    def test_source_none_keeps_platform(self):
        """无改动保存（source=None）不能把「平台」抹成「手动」。"""
        stats.set_profile(self.conn, 1, name="张三", school="某大学", source="platform")
        stats.set_profile(self.conn, 1, name="张三", school="某大学", source=None)
        self.assertEqual(stats.get_profile(self.conn, 1)["source"], "platform")

    def test_source_none_on_new_row_is_empty(self):
        stats.set_profile(self.conn, 1, grade="2026", source=None)
        self.assertEqual(stats.get_profile(self.conn, 1)["source"], "")

    def test_manual_edit_turns_source_manual(self):
        stats.set_profile(self.conn, 1, name="张三", source="platform")
        stats.set_profile(self.conn, 1, name="李四", source="manual")
        self.assertEqual(stats.get_profile(self.conn, 1)["source"], "manual")

    def test_platform_read_still_only_fills_blanks(self):
        """手填过的姓名不被平台值覆盖（原有承诺不能被这次改动破坏）。"""
        stats.set_profile(self.conn, 1, name="手填名", source="manual")
        res = stats.merge_platform_profile(self.conn, 1,
                                           {"name": "平台名", "school": "某大学"})
        p = stats.get_profile(self.conn, 1)
        self.assertEqual(p["name"], "手填名")
        self.assertEqual(p["school"], "某大学")
        self.assertIn("name", res["skipped"])
        self.assertIn("school", res["filled"])

    def test_platform_name_equal_to_nickname_is_ignored(self):
        """C 方案：平台“姓名”与昵称完全相同时视为昵称，不写 name 且记入 nickname_only。"""
        self.conn.execute("UPDATE accounts SET platform_username='林鑫阳' WHERE id=1")
        self.conn.commit()
        res = stats.merge_platform_profile(self.conn, 1,
                                           {"name": "林鑫阳", "school": "某大学"})
        p = stats.get_profile(self.conn, 1)
        self.assertEqual(p["name"], "")
        self.assertEqual(p["school"], "某大学")
        self.assertIn("name", res["nickname_only"])
        self.assertNotIn("name", res["filled"])

    def test_platform_name_differs_from_nickname_is_filled(self):
        """与昵称不同的姓名仍当作真名回填（不能反过来把有效值丢掉）。"""
        self.conn.execute("UPDATE accounts SET platform_username='小明' WHERE id=1")
        self.conn.commit()
        res = stats.merge_platform_profile(self.conn, 1, {"name": "张小明"})
        self.assertEqual(stats.get_profile(self.conn, 1)["name"], "张小明")
        self.assertIn("name", res["filled"])
        self.assertNotIn("name", res["nickname_only"])


if __name__ == "__main__":
    unittest.main()
