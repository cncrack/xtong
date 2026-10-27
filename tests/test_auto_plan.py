"""守护侧计划/窗口纯函数测试（2026-10-27 加固）。

对应两处真缺口：
① 随机开刷时刻可能越出学习窗口（短窗口必然越界）→ 到点秒退、白耗当日机会；
② 窗口预校验依赖 `within_daily_window`，其跨午夜语义必须与账号级窗口一致。
"""

from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime

from xxt.cli import (
    _course_ref,
    _last_auto_start,
    _manual_stopped,
    _random_start,
    _session_end_after,
)
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


class TestSessionRoundWindow(unittest.TestCase):
    """P1（2026-10-28）：复活判定只能看**本轮**的 session-end。

    旧实现取“当天最后一条 session-end”，等于把上一轮的原因当成这一轮：
    上一轮打不开 → 白复活一次；上一轮额度达标 → 今天就被误钉死。
    """

    DAY = "2026-10-27"

    def _conn(self, rows):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE run_logs(id INTEGER PRIMARY KEY AUTOINCREMENT,"
                     " ts TEXT, event TEXT, detail TEXT, account_id INTEGER DEFAULT 1)")
        for ts, event, detail in rows:
            conn.execute("INSERT INTO run_logs(ts, event, detail, account_id)"
                         " VALUES(?,?,?,1)", (ts, event, detail))
        conn.commit()
        return conn

    def test_last_auto_start_is_current_round(self):
        conn = self._conn([
            (f"{self.DAY}T09:10:00", "auto-try", "第 1/3 次发起"),
            (f"{self.DAY}T14:00:00", "auto-try", "第 2/3 次发起"),
        ])
        try:
            self.assertEqual(_last_auto_start(conn, 1, self.DAY),
                             f"{self.DAY}T14:00:00")
        finally:
            conn.close()

    def test_session_end_before_round_start_is_ignored(self):
        """上一轮“打不开”不能拿来当本轮原因（否则白复活一次）。"""
        conn = self._conn([
            (f"{self.DAY}T09:10:00", "auto-try", "第 1/3 次发起"),
            (f"{self.DAY}T09:13:00", "session-end", "课程｜连续 3 节打不开｜视频 0 个"),
            (f"{self.DAY}T14:00:00", "auto-try", "第 2/3 次发起"),
            (f"{self.DAY}T14:20:00", "session-end", "课程｜时长额度达标｜视频 12 个"),
        ])
        try:
            since = _last_auto_start(conn, 1, self.DAY)
            row = _session_end_after(conn, 1, since)
            self.assertIn("额度达标", row["detail"])
            # 本轮还没有 session-end → 不能被上一轮的“打不开”骗到
            self.assertIsNone(_session_end_after(conn, 1, f"{self.DAY}T14:30:00"))
        finally:
            conn.close()

    def test_other_account_and_day_isolated(self):
        conn = self._conn([
            (f"{self.DAY}T09:10:00", "auto-try", "别人的"),
            ("2026-10-26T09:10:00", "auto-try", "昨天的"),
        ])
        try:
            self.assertEqual(_last_auto_start(conn, 2, self.DAY), "")
            self.assertIsNone(_session_end_after(conn, 2, "2026-10-01T00:00:00"))
        finally:
            conn.close()


class TestManualStop(unittest.TestCase):
    """Q29：手动停 = 不占次数、今日让位（明日自愈）。

    2026-10-27 实测：18:21:17 面板手动停 → 18:21:40 守护写「硬停（已试 4/4 轮）」，
    就是这里没区分“手动停”与“秒退”。
    """

    DAY = "2026-10-27"

    def _conn(self, rows):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE run_logs(id INTEGER PRIMARY KEY AUTOINCREMENT,"
                     " ts TEXT, event TEXT, detail TEXT, account_id INTEGER DEFAULT 1)")
        for ts, event in rows:
            conn.execute("INSERT INTO run_logs(ts, event, account_id) VALUES(?,?,1)",
                         (ts, event))
        conn.commit()
        return conn

    def test_stop_after_fire_counts_as_manual(self):
        conn = self._conn([
            (f"{self.DAY}T09:10:00", "auto-try"),
            (f"{self.DAY}T09:20:00", "session-stop"),
        ])
        try:
            self.assertTrue(_manual_stopped(conn, 1, f"{self.DAY}T09:10:00"))
            # 停止发生在“本轮起点”之前 → 属于上一轮，不算
            self.assertFalse(_manual_stopped(conn, 1, f"{self.DAY}T09:25:00"))
        finally:
            conn.close()

    def test_session_restart_after_stop_is_not_manual_stop(self):
        """停一下 → 又手动 ▶ → 这次会话自己退出：不能当成手动停（否则整天让位）。"""
        conn = self._conn([
            (f"{self.DAY}T09:10:00", "auto-try"),
            (f"{self.DAY}T09:20:00", "session-stop"),
            (f"{self.DAY}T09:30:00", "session-start"),
        ])
        try:
            self.assertFalse(_manual_stopped(conn, 1, f"{self.DAY}T09:10:00"))
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
