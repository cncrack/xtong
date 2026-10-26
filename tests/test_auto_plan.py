"""守护侧计划/窗口纯函数测试（2026-10-27 加固）。

对应两处真缺口：
① 随机开刷时刻可能越出学习窗口（短窗口必然越界）→ 到点秒退、白耗当日机会；
② 窗口预校验依赖 `within_daily_window`，其跨午夜语义必须与账号级窗口一致。
"""

from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime

from xxt.cli import _course_ref, _random_start
from xxt.core.pace import within_daily_window


class TestRandomStart(unittest.TestCase):
    def test_short_window_never_exceeds(self):
        """窗口 30 分钟：300 次抽样都必须落在窗口内（旧实现会出现 23:59）。"""
        for _ in range(300):
            t = _random_start("23:00", "23:30")
            self.assertGreaterEqual(t, "23:00")
            self.assertLessEqual(t, "23:30")

    def test_never_produces_24xx(self):
        for _ in range(300):
            t = _random_start("23:00", "23:59")
            self.assertLess(t, "24:00")
            self.assertEqual(len(t), 5)

    def test_cross_midnight_window(self):
        for _ in range(300):
            t = _random_start("22:00", "02:00")
            self.assertTrue(t >= "22:00" or t <= "02:00", t)

    def test_bad_input_falls_back(self):
        self.assertEqual(_random_start("xx", "yy"), "09:00")
        self.assertEqual(_random_start("23:00", "abc"), "09:00")


class TestCourseRef(unittest.TestCase):
    """选课结果两种形态：学习安排给 dict（pid），课程轮转给 sqlite3.Row。

    2026-10-27 实测：轮转路径走 `.get("pid")` 抛 AttributeError，守护一触发就崩。
    """

    def _row(self, sql: str) -> sqlite3.Row:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(sql)
        row = conn.execute("SELECT * FROM courses").fetchone()
        conn.close()
        return row

    def test_rotation_row_uses_platform_course_id(self):
        row = self._row(
            "CREATE TABLE courses(name TEXT, platform_course_id TEXT);"
            "INSERT INTO courses VALUES('轮转课','p-123');")
        self.assertEqual(_course_ref(row), ("轮转课", "p-123"))

    def test_row_without_pid_column(self):
        row = self._row("CREATE TABLE courses(name TEXT);INSERT INTO courses VALUES('课A');")
        self.assertEqual(_course_ref(row), ("课A", None))

    def test_plan_dict(self):
        self.assertEqual(_course_ref({"name": "安排课", "pid": "p-9"}), ("安排课", "p-9"))
        self.assertEqual(_course_ref({"name": "安排课"}), ("安排课", None))


class TestWindowGuard(unittest.TestCase):
    """守护侧预校验与子进程共用同一判定，避免“守护放行、子进程秒退”。"""

    def test_inside_and_outside(self):
        pace = {"daily_window": ["08:00", "23:00"]}
        self.assertTrue(within_daily_window(pace, datetime(2026, 10, 27, 10, 0)))
        self.assertFalse(within_daily_window(pace, datetime(2026, 10, 27, 2, 0)))
        self.assertFalse(within_daily_window(pace, datetime(2026, 10, 27, 23, 30)))

    def test_cross_midnight(self):
        pace = {"daily_window": ["22:00", "02:00"]}
        self.assertTrue(within_daily_window(pace, datetime(2026, 10, 27, 23, 0)))
        self.assertTrue(within_daily_window(pace, datetime(2026, 10, 27, 1, 0)))
        self.assertFalse(within_daily_window(pace, datetime(2026, 10, 27, 12, 0)))


if __name__ == "__main__":
    unittest.main()
