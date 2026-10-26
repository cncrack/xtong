"""面板接口测试（TestClient + 临时库）。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from xxt.config import Settings
from xxt.panel.app import create_app
from xxt.store.db import connect


class TestPanel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.settings = Settings(root=root)
        conn = connect(self.settings.db_path)
        conn.execute(
            "INSERT INTO courses(account_id, platform_course_id, name) VALUES(1,'c1','测试课')")
        conn.commit()
        cid = conn.execute("SELECT id FROM courses").fetchone()["id"]
        conn.execute(
            "INSERT INTO chapters(course_id, platform_chapter_id, name, idx, status) "
            "VALUES(?, 'k1', '1.1 测试节', 0, 'done')", (cid,))
        conn.execute(
            "INSERT INTO chapters(course_id, platform_chapter_id, name, idx, status) "
            "VALUES(?, 'k2', '1.2 测试节', 1, 'pending')", (cid,))
        conn.execute(
            "INSERT INTO run_logs(ts, level, event, detail) "
            "VALUES('2026-01-01T10:00:00','INFO','video-done','测试课｜1.1')")
        conn.commit()
        conn.close()
        self.client = TestClient(create_app(self.settings))

    def tearDown(self):
        self.tmp.cleanup()

    # 真人浏览器 UA：`source=ui` 只有配上它才算「面板按钮」（2026-10-27 纠偏）
    UI_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"}

    def test_overview_account_cards(self):
        r = self.client.get("/api/overview")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        acc = data["accounts"][0]
        self.assertIn("has_session", acc)
        self.assertFalse(acc["has_session"])  # 临时目录无 state.json
        self.assertEqual(acc["courses"][0]["total"], 2)

    def test_plan_created_disabled_by_default(self):
        r = self.client.post("/api/plans", json={
            "name": "测试批次", "course_name": "测试课", "account_ids": [1]})
        self.assertTrue(r.json()["ok"])
        pid = r.json()["id"]
        plans = self.client.get("/api/plans").json()["plans"]
        row = next(p for p in plans if p["id"] == pid)
        self.assertFalse(row["enabled"])  # 新建批次默认不启用，需手动启用

    def test_profile_roundtrip_includes_platform_fields(self):
        r = self.client.post("/api/profiles", json={
            "account_id": 1, "name": "张三", "student_no": "2024001",
            "grade": "2024级", "major": "计算机", "term": "2026秋",
            "school": "某某大学", "dept": "计算机学院", "cls": "1班"})
        self.assertTrue(r.json()["ok"])
        p = next(x for x in self.client.get("/api/profiles").json()["profiles"]
                 if x["account_id"] == 1)
        self.assertEqual(p["school"], "某某大学")
        self.assertEqual(p["dept"], "计算机学院")
        self.assertEqual(p["cls"], "1班")

    def test_plan_course_pid_roundtrip(self):
        r = self.client.post("/api/plans", json={
            "name": "PID批次", "course_name": "测试课", "course_pid": "c1",
            "account_ids": [1]})
        self.assertTrue(r.json()["ok"])
        row = next(p for p in self.client.get("/api/plans").json()["plans"]
                   if p["id"] == r.json()["id"])
        self.assertEqual(row["course_pid"], "c1")  # 平台课程ID锚点已存

    def test_legacy_plan_without_pid_still_creates(self):
        r = self.client.post("/api/plans", json={
            "name": "老式批次", "course_name": "测试课", "account_ids": [1]})
        self.assertTrue(r.json()["ok"])
        row = next(p for p in self.client.get("/api/plans").json()["plans"]
                   if p["id"] == r.json()["id"])
        self.assertIsNone(row["course_pid"])  # 无 pid → 老式批次仍按课名匹配

    def test_plan_update_sets_course_pid(self):
        pid = self.client.post("/api/plans", json={
            "name": "X", "course_name": "测试课", "account_ids": [1]}).json()["id"]
        r = self.client.post(f"/api/plans/{pid}/update", json={"course_pid": "c1"})
        self.assertTrue(r.json()["ok"])
        row = next(p for p in self.client.get("/api/plans").json()["plans"]
                   if p["id"] == pid)
        self.assertEqual(row["course_pid"], "c1")

    def test_overview_courses_include_platform_course_id(self):
        acc = self.client.get("/api/overview").json()["accounts"][0]
        self.assertEqual(acc["courses"][0]["platform_course_id"], "c1")

    def test_course_for_account_returns_pid(self):
        conn = connect(self.settings.db_path)
        try:
            from xxt.core import plans as planmod

            planmod.create_plan(conn, "P", "测试课", [1], enabled=True, course_pid="c1")
            self.assertEqual(planmod.course_for_account(conn, 1),
                             {"name": "测试课", "pid": "c1"})
        finally:
            conn.close()

    def test_settings_roundtrip(self):
        r = self.client.put("/api/settings", json={
            "quiz_enabled": True, "window_start": "09:30", "window_end": "22:30",
            "daily_minutes": 120, "daily_tasks": 30,
        })
        self.assertEqual(r.json(), {"ok": True})
        s = self.client.get("/api/settings").json()
        self.assertTrue(s["quiz_enabled"])
        self.assertEqual((s["window_start"], s["window_end"]), ("09:30", "22:30"))
        self.assertEqual(s["daily_minutes"], 120)

    def test_settings_rejects_bad_values(self):
        r = self.client.put("/api/settings", json={"daily_minutes": -5})
        self.assertFalse(r.json()["ok"])
        r2 = self.client.put("/api/settings", json={"window_start": "9:30", "window_end": "22:00"})
        self.assertFalse(r2.json()["ok"])

    def test_progress_shape(self):
        r = self.client.get("/api/progress")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["courses"][0]["name"], "测试课")
        self.assertEqual((data["courses"][0]["done"], data["courses"][0]["total"]), (1, 2))

    def test_accounts_no_credentials(self):
        body = self.client.get("/api/accounts").text
        self.assertNotIn("password", body)
        self.assertNotIn("username_enc", body)

    def test_index_renders(self):
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_logs_accounts_filter(self):
        """日志页面包屑筛选：accounts=1,2 走 IN，单号准确。"""
        conn = connect(self.settings.db_path)
        conn.execute("INSERT INTO run_logs(ts, level, event, detail, account_id) "
                     "VALUES('2026-01-01T11:00:00','INFO','plan-create','A',0)")
        conn.execute("INSERT INTO run_logs(ts, level, event, detail, account_id) "
                     "VALUES('2026-01-01T11:01:00','INFO','video-done','B',1)")
        conn.commit()
        conn.close()
        only1 = self.client.get("/api/logs?n=50&accounts=1").json()["logs"]
        self.assertTrue(only1 and all(x["account_id"] == 1 for x in only1))
        both = self.client.get("/api/logs?n=50&accounts=1,2").json()["logs"]
        self.assertTrue({x["account_id"] for x in both} <= {1, 2})

    def test_logs_sys_source_reads_file(self):
        """系统日志视图：读 xxt.log，按 min_level 过滤，sub=1 并入子进程 print 行。"""
        logdir = self.settings.data_dir / "logs"
        logdir.mkdir(parents=True, exist_ok=True)
        (logdir / "xxt.log").write_text(
            "2026-01-01 10:00:00,001 INFO [xxt.panel] 普通一行\n"
            "2026-01-01 10:00:01,002 WARNING [xxt.cli] 警告一行\n",
            encoding="utf-8")
        (logdir / "sub-auto-1.log").write_text(
            "2026-01-01 10:00:02,003 INFO [xxt.cli] 子进程日志行\n"
            "[auto] 账号#1 今日计划 09:00 开刷\n",
            encoding="utf-8")
        warn = self.client.get("/api/logs?source=sys&min_level=WARNING").json()["logs"]
        self.assertEqual([x["level"] for x in warn], ["WARNING"])
        self.assertEqual(warn[0]["who"], "系统")
        info = self.client.get("/api/logs?source=sys&min_level=INFO").json()["logs"]
        self.assertEqual(len(info), 2)  # 子进程文件未入选时只有 xxt.log 两行
        sub = self.client.get("/api/logs?source=sys&min_level=INFO&sub=1").json()["logs"]
        events = [x["event"] for x in sub]
        self.assertIn("xxt.panel", events)
        self.assertIn("xxt.cli", events)
        self.assertTrue(any(e.startswith("sub:") for e in events))
        self.assertFalse(any("子进程日志行" in (x["detail"] or "") and x["event"].startswith("sub:")
                             for x in sub))  # 子进程文件里的 logging 行不重复

    def test_auto_start_records_source_and_rejects_second(self):
        """启停来源必须可追溯：面板按钮 → 面板按钮；重复启动被拒要留 WARNING。"""
        import os
        from unittest import mock

        import xxt.panel.app as appmod

        fake = mock.Mock(pid=os.getpid())  # 用自身 pid：_pid_alive 判活，但不会真被 kill
        fake.poll.return_value = None
        with mock.patch.object(appmod, "_spawn", return_value=fake) as spawn:
            self.assertTrue(self.client.post("/api/auto/start?source=ui",
                                             headers=self.UI_HEADERS).json()["ok"])
            self.assertFalse(self.client.post("/api/auto/start?source=ui",
                                              headers=self.UI_HEADERS).json()["ok"])
            self.assertEqual(spawn.call_count, 1)  # 第二次没再拉起
        try:
            conn = connect(self.settings.db_path)
            rows = conn.execute(
                "SELECT level, event, detail FROM run_logs "
                "WHERE event='auto-request' ORDER BY id").fetchall()
            conn.close()
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["level"], "INFO")
            self.assertIn("面板按钮", rows[0]["detail"])
            self.assertEqual(rows[1]["level"], "WARNING")
            self.assertIn("被拒", rows[1]["detail"])
        finally:
            appmod._AUTO = None

    def test_auto_start_api_flagged_by_ua(self):
        """非 UI 入口（脚本 / httpx）记成 API 并带 UA 特征，事后能区分谁开的守护。"""
        import os
        from unittest import mock

        import xxt.panel.app as appmod

        fake = mock.Mock(pid=os.getpid())
        fake.poll.return_value = None
        with mock.patch.object(appmod, "_spawn", return_value=fake):
            r = self.client.post("/api/auto/start",
                                 headers={"User-Agent": "python-httpx/0.27.0"})
            self.assertTrue(r.json()["ok"])
        try:
            conn = connect(self.settings.db_path)
            row = conn.execute(
                "SELECT detail FROM run_logs WHERE event='auto-request' "
                "ORDER BY id DESC LIMIT 1").fetchone()
            conn.close()
            self.assertIn("API(httpx)", row["detail"])
        finally:
            appmod._AUTO = None

    def _last_auto_request(self) -> tuple[str, str]:
        conn = connect(self.settings.db_path)
        row = conn.execute("SELECT level, detail FROM run_logs WHERE event='auto-request' "
                           "ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()
        return row["level"], row["detail"]

    def _start_once(self, **kw):
        import os
        from unittest import mock

        import xxt.panel.app as appmod

        fake = mock.Mock(pid=os.getpid())
        fake.poll.return_value = None
        try:
            with mock.patch.object(appmod, "_spawn", return_value=fake):
                return self.client.post("/api/auto/start", **kw)
        finally:
            appmod._AUTO = None

    def test_ui_claim_with_script_ua_is_not_panel_button(self):
        """自称 `source=ui` 但 UA 是 httpx → 不能记“面板按钮”（脚本伪装的盲区）。"""
        r = self._start_once(params={"source": "ui"},
                             headers={"User-Agent": "python-httpx/0.27.0"})
        self.assertTrue(r.json()["ok"])
        level, detail = self._last_auto_request()
        self.assertNotIn("面板按钮", detail)
        self.assertIn("自称 UI", detail)
        self.assertEqual(level, "WARNING")  # 伪装要黄字，日志里一眼看见

    def test_diag_header_marks_diagnostic_script(self):
        """诊断脚本带 `X-Diag` 就算 UA 像浏览器也记成诊断脚本（主动声明优先）。"""
        r = self._start_once(headers={**self.UI_HEADERS, "X-Diag": "q_readback.py"})
        self.assertTrue(r.json()["ok"])
        level, detail = self._last_auto_request()
        self.assertIn("诊断脚本(q_readback.py)", detail)
        self.assertEqual(level, "INFO")

    def test_source_diag_without_header(self):
        r = self._start_once(params={"source": "diag"})
        self.assertTrue(r.json()["ok"])
        self.assertIn("诊断脚本", self._last_auto_request()[1])

    def _write_stale_auto_marker(self, age: float) -> Path:
        """写 auto.json 并把 mtime 往前推 age 秒（模拟 pid 被复用）。"""
        import json as _json
        import os
        import time as _time

        run = self.settings.data_dir / "run"
        run.mkdir(parents=True, exist_ok=True)
        marker = run / "auto.json"
        marker.write_text(_json.dumps({"pid": os.getpid(), "since": "00:00"}),
                          encoding="utf-8")
        old = _time.time() - age
        os.utime(marker, (old, old))
        return marker

    def test_auto_start_not_blocked_by_reused_pid(self):
        """标记里的 pid 被别的进程复用时，不能一直回“已在运行”把守护永久锁死。"""
        import os
        from unittest import mock

        import xxt.panel.app as appmod

        self._write_stale_auto_marker(3600)
        fake = mock.Mock(pid=os.getpid())
        fake.poll.return_value = None
        try:
            with mock.patch.object(appmod, "_spawn", return_value=fake) as spawn:
                r = self.client.post("/api/auto/start?source=ui")
            self.assertTrue(r.json()["ok"])
            self.assertEqual(spawn.call_count, 1)
        finally:
            appmod._AUTO = None

    def test_auto_stop_never_kills_reused_pid(self):
        """pid 归属不符时只清标记：宁可停不掉，也不能 taskkill 无关进程。"""
        from unittest import mock

        import xxt.panel.app as appmod

        appmod._AUTO = None  # 确保走“孤儿标记”分支
        marker = self._write_stale_auto_marker(3600)
        with mock.patch.object(appmod.subprocess, "run") as run_cmd:
            d = self.client.post("/api/auto/stop?source=ui").json()
        self.assertFalse(d["ok"])
        self.assertFalse(run_cmd.called)
        self.assertFalse(marker.exists())
        conn = connect(self.settings.db_path)
        row = conn.execute(
            "SELECT detail FROM run_logs WHERE event='auto-stop' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        self.assertIn("未强杀", row["detail"])

    def test_auto_stop_kills_owned_pid(self):
        """归属成立时正常强杀（mock 掉 subprocess，不真杀自己）。"""
        from unittest import mock

        import xxt.panel.app as appmod

        appmod._AUTO = None
        self._write_stale_auto_marker(0)  # 文件就是刚写的 → 归属成立
        with mock.patch.object(appmod.subprocess, "run") as run_cmd:
            d = self.client.post("/api/auto/stop?source=ui").json()
        self.assertTrue(d["ok"])
        self.assertTrue(run_cmd.called)


if __name__ == "__main__":
    unittest.main()
