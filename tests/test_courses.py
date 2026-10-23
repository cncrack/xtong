"""课程列表解析测试（夹具为合成数据，结构与真实接口一致）。"""

from __future__ import annotations

import unittest
from pathlib import Path

from xxt.adapters.chaoxing.courses import parse_course_cards

FIXTURE = Path(__file__).parent / "fixtures" / "courselist_sample.html"


class TestParseCourseCards(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.courses = parse_course_cards(FIXTURE.read_text(encoding="utf-8"))

    def test_extracts_two_courses(self):
        self.assertEqual(len(self.courses), 2)

    def test_fields(self):
        first = self.courses[0]
        self.assertEqual(first["platform_course_id"], "200001")
        self.assertEqual(first["clazz_id"], "100001")
        self.assertEqual(first["person_id"], "900001")
        self.assertEqual(first["name"], "测试课程一（置顶示例）")
        self.assertIn("courseid=200001", first["url"])
        second = self.courses[1]
        self.assertEqual(second["platform_course_id"], "200002")
        self.assertEqual(second["name"], "测试课程二：高等数学导论")


if __name__ == "__main__":
    unittest.main()
