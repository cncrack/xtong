"""给「学习进度改名 + 课程切换 + 签名覆盖进度」补测试（2026-11-03）。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from xxt.panel_gxks.page import PAGE


class TestLearningProgressTitle(unittest.TestCase):
    """用户要求：「把节进度改为上学习进度」。"""

    def test_title_is_learning_progress(self):
        self.assertIn("<h2>学习进度", PAGE)
        self.assertNotIn("<h2>节进度", PAGE)


class TestCourseSwitcher(unittest.TestCase):
    """课程切换器（缓存按课分开，面板只看一份会永远停在旧课）。"""

    def test_select_exists_and_handler_defined(self):
        self.assertIn('id="sel-course"', PAGE)
        self.assertIn("function pickCourse(", PAGE)

    def test_load_passes_course_to_api(self):
        body = PAGE.split("async function load(", 1)[1].split("\n}", 1)[0]
        self.assertIn("&course=", body)
        self.assertIn("encodeURIComponent(COURSE)", body)

    def test_render_course_select_uses_courses_cached(self):
        body = PAGE.split("function renderCourseSelect(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("state.courses_cached", body)
        self.assertIn("selected", body)

    def test_refresh_sections_prefers_user_course(self):
        """刷新节列表必须写用户选的那门课，而不是后端上次读到的那门。"""
        body = PAGE.split("function refreshSections(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("COURSE ||", body)

    def test_pick_course_resets_signature(self):
        """换课要作废 SEC_SIG，否则表格不重建、看着像没反应。"""
        body = PAGE.split("function pickCourse(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn('SEC_SIG = ""', body)


class TestProgressSignatureCoversLearning(unittest.TestCase):
    """签名必须覆盖会变的数字，否则刷课时进度不动（2026-11-03 用户发现）。"""

    def test_signature_includes_minutes_and_row_fingerprint(self):
        body = PAGE.split("function renderSections(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("s.minutes_done", body)
        self.assertIn('r.state || ""', body)
        self.assertIn("fp", body)

    def test_empty_state_explains_which_kind_of_empty(self):
        """空态要说清是「这门课学完了」还是「这是别的课的缓存」。"""
        body = PAGE.split("function renderSections(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("刷新节列表", body)
        self.assertIn("另一门课", body)


class TestListCourseCachesBackend(unittest.TestCase):
    """后端：列出账号所有课程缓存（供切换器用）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, name: str, data: dict) -> None:
        (self.dir / name).write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def test_lists_legacy_and_course_caches(self):
        from xxt.panel_gxks import app
        self._write("sections_default.json", {
            "rows": [{"category": "军事理论（字幕版）", "state": "done"}],
            "summary": {"sections": 1, "done": 1}})
        self._write("sections_default_32.json", {
            "course": "32",
            "rows": [{"category": "全民国家安全教育公开课", "state": "learning"}],
            "summary": {"sections": 3, "done": 1}})
        out = app._list_course_caches(self.dir, "default")
        self.assertEqual(len(out), 2)
        names = {o["name"] for o in out}
        self.assertIn("军事理论（字幕版）", names)
        self.assertIn("全民国家安全教育公开课", names)
        vals = {o["value"] for o in out}
        self.assertIn("32", vals)

    def test_empty_dir_returns_empty_list(self):
        from xxt.panel_gxks import app
        self.assertEqual(app._list_course_caches(self.dir, "default"), [])
