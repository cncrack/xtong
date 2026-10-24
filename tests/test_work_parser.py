"""P2.5 作业页解析测试（合成夹具：按 2.12 实况结构构造）。"""

from __future__ import annotations

import unittest
from pathlib import Path

from xxt.adapters.chaoxing.work import parse_work_questions

FIXTURE = Path(__file__).parent / "fixtures" / "work_page_sample.html"


class TestParseWork(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qs = parse_work_questions(FIXTURE.read_text(encoding="utf-8"))

    def test_parses_three_questions(self):
        self.assertEqual(len(self.qs), 3)

    def test_types_mapped(self):
        self.assertEqual([q["qtype_cn"] for q in self.qs], ["简答题", "单选题", "判断题"])
        self.assertEqual([q["qtype"] for q in self.qs], ["fill", "single", "judge"])

    def test_stem_strips_type_marker(self):
        self.assertNotIn("【", self.qs[0]["stem"])
        self.assertIn("大学生活", self.qs[0]["stem"])

    def test_options_collected_for_choice(self):
        self.assertEqual(len(self.qs[1]["options"]), 4)
        self.assertEqual(self.qs[2]["options"], [])  # 判断题无选项容器（本题结构）


if __name__ == "__main__":
    unittest.main()
