"""个人空间 DOM 画像解析（合成数据，不用真实页面转储）。"""

import unittest

from xxt.adapters.chaoxing.profile_read import _looks_like_school, _parse_space_dom


class LooksLikeSchoolTest(unittest.TestCase):
    def test_accepts_college_and_university(self):
        self.assertTrue(_looks_like_school("示范学院"))
        self.assertTrue(_looks_like_school("某某大学"))
        self.assertTrue(_looks_like_school("某某职业技术学院"))

    def test_rejects_non_school_lines(self):
        self.assertFalse(_looks_like_school("输入邀请码"))
        self.assertFalse(_looks_like_school("学院"))          # 太短
        self.assertFalse(_looks_like_school("2026级 学院"))   # 含数字
        self.assertFalse(_looks_like_school("学" * 40 + "学院"))  # 太长
        self.assertFalse(_looks_like_school(""))


class ParseSpaceDomTest(unittest.TestCase):
    def test_header_first_line_is_school(self):
        got = _parse_space_dom("测试工程学院\n输入邀请码\n张三", "张三")
        self.assertEqual(got, {"school": "测试工程学院", "name": "张三"})

    def test_skips_noise_lines(self):
        got = _parse_space_dom("输入邀请码\n测试大学\n李四", "李四")
        self.assertEqual(got["school"], "测试大学")
        self.assertEqual(got["name"], "李四")

    def test_no_school_gives_empty(self):
        got = _parse_space_dom("输入邀请码\n课程", "王五")
        self.assertEqual(got["school"], "")
        self.assertEqual(got["name"], "王五")

    def test_bad_name_rejected(self):
        self.assertEqual(_parse_space_dom("", "123456")["name"], "")
        self.assertEqual(_parse_space_dom("", "x" * 30)["name"], "")

    def test_empty_inputs(self):
        self.assertEqual(_parse_space_dom("", ""), {"school": "", "name": ""})


if __name__ == "__main__":
    unittest.main()
