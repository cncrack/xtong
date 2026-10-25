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


if __name__ == "__main__":
    unittest.main()
