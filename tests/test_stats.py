"""学习统计（数据可视页）测试：画像增改、按维度聚合、口径与边界。"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from xxt.core.stats import aggregate, get_profile, list_profiles, set_profile, today_summary
from xxt.store.db import connect


class TestStats(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.tmp.name) / "t.db")
        self.conn.execute("DELETE FROM accounts")  # connect() 会种一个 default 账号
        for aid, label in ((1, "甲"), (2, "乙"), (3, "丙")):
            self.conn.execute(
                "INSERT INTO accounts(id, label, status, created_at, priority)"
                " VALUES(?, ?, 'active', datetime('now'), 1.0)", (aid, label))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _log(self, aid: int, event: str, detail: str, ts: str | None = None) -> None:
        self.conn.execute(
            "INSERT INTO run_logs(ts, level, event, detail, account_id) VALUES(?,?,?,?,?)",
            (ts or time.strftime("%Y-%m-%dT%H:%M:%S"), "INFO", event, detail, aid))

    def test_profile_roundtrip(self):
        r = set_profile(self.conn, 1, grade="2024级", major="计算机", term="2026秋")
        self.assertTrue(r["ok"])
        self.assertEqual(get_profile(self.conn, 1)["major"], "计算机")
        # 重复写为更新而非新增
        set_profile(self.conn, 1, grade="2023级", major="软件工程", term="2026秋")
        self.assertEqual(get_profile(self.conn, 1)["grade"], "2023级")
        n = self.conn.execute("SELECT COUNT(*) FROM account_profiles").fetchone()[0]
        self.assertEqual(n, 1)
        # 留空即清空 → 归入未标注
        set_profile(self.conn, 1, grade="", major="", term="")
        self.assertEqual(get_profile(self.conn, 1)["grade"], "")

    def test_profile_unknown_account(self):
        r = set_profile(self.conn, 99, grade="2024级")
        self.assertFalse(r["ok"])

    def test_aggregate_by_grade(self):
        set_profile(self.conn, 1, grade="2024级", major="计算机", term="2026秋")
        set_profile(self.conn, 2, grade="2024级", major="软件工程", term="2026秋")
        self._log(1, "video-done", "数据结构｜栈｜120s")
        self._log(1, "video-done", "数据结构｜队列｜60s")
        self._log(2, "video-done", "操作系统｜进程｜300s")
        self._log(3, "video-done", "未标注号｜某节｜60s")  # 无画像
        self._log(1, "work-start", "作业1｜AI 代答")
        self._log(0, "plan-create", "批次｜全局日志")  # 全局日志不参与统计

        d = aggregate(self.conn, by="grade", days=0)
        self.assertTrue(d["ok"])
        rows = {r["key"]: r for r in d["rows"]}
        self.assertEqual(rows["2024级"]["accounts"], 2)
        self.assertEqual(rows["2024级"]["videos"], 3)
        self.assertEqual(rows["2024级"]["minutes"], 8.0)  # (120+60+300)/60
        self.assertEqual(rows["2024级"]["works"], 1)
        self.assertEqual(rows["未标注"]["videos"], 1)
        self.assertEqual(d["total"]["videos"], 4)
        self.assertEqual(d["total"]["accounts"], 3)

    def test_aggregate_by_major_and_account(self):
        set_profile(self.conn, 1, grade="2024级", major="计算机", term="2026秋")
        self._log(1, "video-done", "课A｜一｜90s")
        self._log(2, "video-done", "课A｜二｜30s")
        m = aggregate(self.conn, by="major", days=0)
        keys = {r["key"] for r in m["rows"]}
        self.assertEqual(keys, {"计算机", "未标注"})
        a = aggregate(self.conn, by="account", days=0)
        self.assertEqual({r["key"] for r in a["rows"]}, {"#1 甲", "#2 乙"})

    def test_days_filter(self):
        self._log(1, "video-done", "旧记录｜｜60s", ts="2000-01-01T08:00:00")
        self._log(1, "video-done", "今天的｜｜60s")
        recent = aggregate(self.conn, by="grade", days=7)
        self.assertEqual(recent["total"]["videos"], 1)   # 2000 年那条被过滤
        alltime = aggregate(self.conn, by="grade", days=0)
        self.assertEqual(alltime["total"]["videos"], 2)
        self.assertEqual(alltime["since"], "全部")

    def test_bad_dimension(self):
        self.assertFalse(aggregate(self.conn, by="nickname")["ok"])

    def test_today_summary(self):
        self._log(1, "video-done", "今天｜｜30s")
        self._log(2, "video-done", "今天｜｜30s")
        self._log(1, "video-done", "昨天｜｜60s", ts="2000-01-01T08:00:00")
        s = today_summary(self.conn)
        self.assertEqual(s["videos"], 2)
        self.assertEqual(s["minutes"], 1.0)

    def test_list_profiles_covers_all_accounts(self):
        set_profile(self.conn, 2, grade="2022级")
        rows = list_profiles(self.conn)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1]["grade"], "2022级")
        self.assertEqual(rows[0]["grade"], "")


if __name__ == "__main__":
    unittest.main()
