"""G-12（2026-11-08）：节列表刷新必须按「页面当前真实课程」落缓存文件。

## 现象

甲同学（default）的节列表缓存永远停在 `sections_default.json`（`course=""`、
多出旧版 `pager` 字段），点「刷新节列表」实测 20:35:11 前后**同一个文件**。
而 test 是 `sections_test_32.json` —— 两个账号的缓存形态不一致，面板
多显示一个 `pager` 徽标，用户说「两个账号进度区不一样显示」。

## 根因

`gxks_sections.py` 里 `cache = sections_cache.path(...)` 在**连浏览器之前**就算好了，
且全程没调 `driver.current_course()`。面板传 `--course ""` 时：
刷新 → 写回无后缀文件 → 面板 `course_value` 仍空 → 下次再传空，死循环。

对照：`run_gxks.py` 每次都 `current_course()`（日志打印「当前课程 value=32」）→ 按课回写。

## 修法

`drv = GxksDriver(page)` 之后：
1. 没指定课程时 `course = drv.current_course()`（ddlClass 的 selected value，零额外请求）；
2. **重算** `cache` 路径（第一次算时还没连浏览器），落 `sections_<label>_<course>.json`；
3. `result["course"]` 本来就是变量，自动带上真实值；
4. `select_course()` 只在**用户显式给了 --course** 时调用（读到当前课不该再切一次）。

旧文件保留但不再被写；`sections_cache.read_paths()` 已有回落逻辑。
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "scripts" / "gxks_sections.py"


class TestRefreshUsesCurrentCourse(unittest.TestCase):
    def _main_source(self) -> str:
        """取 `main()` 函数源码（不含模块级 import），便于按语句核对。"""
        tree = ast.parse(SRC.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "main":
                return ast.get_source_segment(SRC.read_text(encoding="utf-8"), node) or ""
        self.fail("main() not found")

    def test_reads_current_course_when_not_given(self):
        """没指定 --course → 调 current_course()，且 path 在它之后重算。"""
        src = self._main_source()
        i_read = src.find("course = drv.current_course()")
        self.assertGreater(i_read, 0, "必须调 driver.current_course()")
        # 必须包在 `if not course` 里（指定课程优先，不能被覆盖）
        self.assertIn("if not course:", src[:i_read][-200:])
        # 重算路径必须在读 course 之后
        i_path = src.find("cache = sections_cache.path(debug_dir, label, course)", i_read)
        self.assertGreater(i_path, i_read, "path 必须在拿到真实 course 后重算")

    def test_select_course_only_when_explicitly_given(self):
        """读到当前课不该再 select_course 切一次（避免无谓回发 / 切课失败）。"""
        src = self._main_source()
        self.assertIn("if course and args.course:", src)
        self.assertNotIn("if course:\n            if not drv.select_course", src)

    def test_result_keeps_course_from_variable(self):
        """result["course"] 用变量，自动带上真实值，不写死空串。"""
        src = self._main_source()
        self.assertIn('"course": course', src)

    def test_help_text_no_longer_promises_legacy_filename(self):
        """help 文案不能再说「空 = 落 sections_<代号>.json」（那是旧行为）。"""
        text = SRC.read_text(encoding="utf-8")
        self.assertIn("缓存落 sections_<代号>_<课程>.json", text)
        self.assertIn("G-12", text)

    def test_no_syntax_error(self):
        ast.parse(SRC.read_text(encoding="utf-8"))


class TestPathRuleStillPrefersCourseFile(unittest.TestCase):
    """面板读方必须优先读带课程的文件（G-12 改完才会读到新文件）。"""

    def test_pick_prefers_file_with_course_suffix(self):
        from xxt.panel_gxks import app

        src = Path(app.__file__).read_text(encoding="utf-8")
        self.assertIn("with_course", src)
        self.assertIn("for p in [*with_course, *legacy]:", src)


if __name__ == "__main__":
    unittest.main()
