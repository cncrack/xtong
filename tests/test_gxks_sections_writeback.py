"""给「新课程节回写」与「面板热重载」补测试（2026-11-03）。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from xxt.adapters.gxks import sections_cache


class TestUpdateRowCreatesForNewCourse(unittest.TestCase):
    """用户踩到的坑：今天刷的是**另一门课**，老缓存里没这些节 →
    `update_row` 静默跳过 → 面板永远停在 10-30 的旧数据。

    修法：找不到该节时**新建/追加**到本课程那份缓存，而不是返回 False。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_creates_cache_when_course_has_no_file(self):
        ok = sections_cache.update_row(
            self.dir, "default", "99", "1301",
            done_minutes=35, state="done", state_text="已完成",
            name="2.4 公开课（一）", required_minutes=35, idx=0,
            category="军事理论")
        self.assertTrue(ok)
        f = sections_cache.path(self.dir, "default", "99")
        self.assertTrue(f.exists(), "新课程必须建出带课程后缀的缓存文件")
        data = json.loads(f.read_text(encoding="utf-8"))
        self.assertEqual(len(data["rows"]), 1)
        row = data["rows"][0]
        self.assertEqual(row["platform_chapter_id"], "1301")
        self.assertEqual(row["state"], "done")
        self.assertEqual(row["done_minutes"], 35)
        self.assertEqual(data["summary"]["sections"], 1)
        self.assertEqual(data["summary"]["done"], 1)

    def test_appends_second_section_to_same_course(self):
        for sid, name in (("1301", "2.4 一"), ("1302", "2.5 二")):
            sections_cache.update_row(
                self.dir, "default", "99", sid, done_minutes=30, state="done",
                name=name, required_minutes=30, category="军事理论")
        data = json.loads(
            sections_cache.path(self.dir, "default", "99").read_text(encoding="utf-8"))
        self.assertEqual(len(data["rows"]), 2)
        self.assertEqual(data["summary"]["done"], 2)

    def test_updates_existing_row_instead_of_duplicating(self):
        sections_cache.update_row(self.dir, "default", "99", "1301",
                                  done_minutes=10, state="learning",
                                  name="2.4 一", required_minutes=35)
        sections_cache.update_row(self.dir, "default", "99", "1301",
                                  done_minutes=35, state="done",
                                  name="2.4 一", required_minutes=35)
        data = json.loads(
            sections_cache.path(self.dir, "default", "99").read_text(encoding="utf-8"))
        self.assertEqual(len(data["rows"]), 1, "同一节不能重复成两行")
        self.assertEqual(data["rows"][0]["done_minutes"], 35)
        self.assertEqual(data["rows"][0]["state"], "done")

    def test_read_at_marks_local_writeback(self):
        """read_at 参与面板 SEC_SIG：带标记才会触发重建。"""
        sections_cache.update_row(self.dir, "default", "99", "1301",
                                  done_minutes=5, state="learning", name="2.4 一")
        data = json.loads(
            sections_cache.path(self.dir, "default", "99").read_text(encoding="utf-8"))
        self.assertIn("本地回写", data["read_at"])

    def test_no_name_does_not_write_meaningless_row(self):
        """没有节名时宁可空着，也不写一行看不出是什么的数据。"""
        sections_cache.update_row(self.dir, "default", "99", "1301",
                                  done_minutes=5, state="learning")
        f = sections_cache.path(self.dir, "default", "99")
        if f.exists():
            data = json.loads(f.read_text(encoding="utf-8"))
            self.assertEqual(data["rows"], [])


class TestPanelReloadConfig(unittest.TestCase):
    """热重载默认开（用户 2026-11-03：「稳定挂机为何要关掉」）。"""

    def test_serve_reads_reload_flag_default_true(self):
        from xxt.panel_gxks import app
        import inspect
        src = inspect.getsource(app.serve)
        self.assertIn('panel.get("reload", True)', src,
                      "默认必须是开：不加配置也要热重载")
        self.assertIn("reload=True", src)
        self.assertIn("reload_dirs", src)

    def test_reload_uses_factory_import_string(self):
        """uvicorn 的 reload 只认导入字符串，传 app 实例会直接报错。"""
        from xxt.panel_gxks import app
        import inspect
        src = inspect.getsource(app.serve)
        self.assertIn("factory=True", src)
        # 必须是**无参**工厂：create_app(settings) 需要参数，uvicorn 无参调用会崩
        self.assertIn("xxt.panel_gxks.app:app_factory", src)
        self.assertIn("def app_factory()", inspect.getsource(app))
