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
