"""章节状态标记（2026-10-27 P0-3a）。

事故：#1 于 10-25 22:47 完整播完「增强自我信心（上）」（video-done 记了 470s），
会话结束时旧代码无条件写 `status='visited'` 把它从 done 改回去 → 卡片进度少算一节
（26/70 而不是 27/70），且下次可能被重新选中。现在 done 一律不许降级。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from xxt.adapters.chaoxing.astudy import mark_entry_visited
from xxt.store.db import connect


class TestMarkEntryVisited(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.tmp.name) / "t.db")
        self.conn.execute(
            "INSERT INTO courses(account_id, platform_course_id, name) VALUES(1,'c1','课程甲')")
        self.conn.commit()
        self.cid = self.conn.execute("SELECT id FROM courses").fetchone()["id"]
        for i, (pid, st) in enumerate([
                ("p_done", "done"), ("p_pending", "pending"),
                ("p_visited", "visited"), ("p_failed", "failed")]):
            self.conn.execute(
                "INSERT INTO chapters(course_id, platform_chapter_id, name, idx, status)"
                " VALUES(?,?,?,?,?)", (self.cid, pid, f"节{i}", i, st))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _status(self, pid: str) -> str:
        return self.conn.execute(
            "SELECT status FROM chapters WHERE course_id=? AND platform_chapter_id=?",
            (self.cid, pid)).fetchone()["status"]

    def test_done_is_not_downgraded(self):
        mark_entry_visited(self.conn, self.cid, "p_done")
        self.assertEqual(self._status("p_done"), "done")

    def test_pending_becomes_visited(self):
        mark_entry_visited(self.conn, self.cid, "p_pending")
        self.assertEqual(self._status("p_pending"), "visited")

    def test_other_states_untouched_when_only_pending(self):
        # 兜底换节只处理仍待学的，已 visited/failed 的不动（避免掩盖失败原因）
        mark_entry_visited(self.conn, self.cid, "p_visited", only_pending=True)
        mark_entry_visited(self.conn, self.cid, "p_failed", only_pending=True)
        self.assertEqual(self._status("p_visited"), "visited")
        self.assertEqual(self._status("p_failed"), "failed")

    def test_only_pending_still_marks_pending(self):
        mark_entry_visited(self.conn, self.cid, "p_pending", only_pending=True)
        self.assertEqual(self._status("p_pending"), "visited")

    def test_unknown_chapter_is_noop(self):
        mark_entry_visited(self.conn, self.cid, "p_not_exist")
        self.assertEqual(self._status("p_pending"), "pending")


if __name__ == "__main__":
    unittest.main()
