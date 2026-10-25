"""学习统计（数据可视页）测试：画像增改、按维度聚合、口径与边界。"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from xxt.core.stats import (aggregate, get_profile, list_profiles,
                            merge_platform_profile, set_profile, today_summary)
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

    def test_student_no_roundtrip(self):
        r = set_profile(self.conn, 1, student_no="2024123456", grade="2024级")
        self.assertTrue(r["ok"])
        self.assertEqual(r["student_no"], "2024123456")
        self.assertEqual(get_profile(self.conn, 1)["student_no"], "2024123456")
        rows = list_profiles(self.conn)
        self.assertEqual(rows[0]["student_no"], "2024123456")
        # 学号是独立字段：清空年级不影响学号
        set_profile(self.conn, 1, student_no="2024123456", grade="")
        self.assertEqual(get_profile(self.conn, 1)["student_no"], "2024123456")
        self.assertEqual(get_profile(self.conn, 1)["grade"], "")

    def test_aggregate_by_course(self):
        # video-done 详情的第一段就是课程名（课程｜知识点｜秒数）
        self._log(1, "video-done", "数据结构｜栈｜120s")
        self._log(2, "video-done", "数据结构｜队列｜60s")
        self._log(1, "video-done", "操作系统｜进程｜300s")
        self._log(1, "work-start", "作业1｜AI 代答")  # 作业日志没有课程名 → 不归组
        d = aggregate(self.conn, by="course", days=0)
        rows = {r["key"]: r for r in d["rows"]}
        self.assertEqual(rows["数据结构"]["videos"], 2)
        self.assertEqual(rows["数据结构"]["accounts"], 2)
        self.assertEqual(rows["数据结构"]["minutes"], 3.0)
        self.assertEqual(rows["操作系统"]["videos"], 1)
        self.assertNotIn("作业1", rows)
        self.assertEqual(d["total"]["works"], 0)

    def test_keyword_search(self):
        set_profile(self.conn, 1, student_no="2024123456", major="计算机")
        set_profile(self.conn, 2, student_no="2024654321", major="软件工程")
        self._log(1, "video-done", "数据结构｜栈｜120s")
        self._log(2, "video-done", "操作系统｜进程｜60s")
        # 按学号搜 → 只剩 1 号账号的记录
        d = aggregate(self.conn, by="account", days=0, q="2024123456")
        self.assertEqual([r["key"] for r in d["rows"]], ["#1 甲"])
        self.assertEqual(d["total"]["videos"], 1)
        # 按课程名搜（维度=课程时命中 key）
        c = aggregate(self.conn, by="course", days=0, q="操作")
        self.assertEqual([r["key"] for r in c["rows"]], ["操作系统"])
        # 搜不到 → 空结果但不是错误
        self.assertTrue(aggregate(self.conn, by="account", days=0, q="不存在")["ok"])
        self.assertEqual(aggregate(self.conn, by="account", days=0, q="不存在")["rows"], [])

    def test_trend_by_day(self):
        self._log(1, "video-done", "课A｜一｜120s", ts="2026-10-24T10:00:00")
        self._log(1, "video-done", "课A｜二｜60s", ts="2026-10-24T11:00:00")
        self._log(2, "video-done", "课A｜三｜60s", ts="2026-10-25T09:00:00")
        self._log(2, "work-start", "作业1", ts="2026-10-25T12:00:00")
        d = aggregate(self.conn, by="grade", days=0)
        trend = {t["date"]: t for t in d["trend"]}
        self.assertEqual(trend["2026-10-24"]["videos"], 2)
        self.assertEqual(trend["2026-10-24"]["minutes"], 3.0)
        self.assertEqual(trend["2026-10-25"]["videos"], 1)
        self.assertEqual(trend["2026-10-25"]["works"], 1)
        # 趋势按日期升序（前端直接画柱）
        self.assertEqual([t["date"] for t in d["trend"]],
                         ["2026-10-24", "2026-10-25"])

    # ---- 姓名画像（2026-10-26：平台昵称升级为可编辑字段）----

    def test_name_profile_roundtrip(self):
        r = set_profile(self.conn, 1, name="林锦阳", student_no="2024123456")
        self.assertTrue(r["ok"])
        p = get_profile(self.conn, 1)
        self.assertEqual(p["name"], "林锦阳")
        self.assertEqual(p["display_name"], "林锦阳")  # 有手填姓名 → 用它
        rows = {x["account_id"]: x for x in list_profiles(self.conn)}
        self.assertEqual(rows[1]["name"], "林锦阳")
        self.assertEqual(rows[1]["display_name"], "林锦阳")
        # 清空姓名 → display_name 回退（本账号无平台昵称 → 空）
        set_profile(self.conn, 1, name="", student_no="2024123456")
        self.assertEqual(get_profile(self.conn, 1)["display_name"], "")

    def test_display_name_falls_back_to_platform(self):
        self.conn.execute("UPDATE accounts SET platform_username='林鑫阳' WHERE id=1")
        self.conn.commit()
        self.assertEqual(get_profile(self.conn, 1)["display_name"], "林鑫阳")
        self.assertEqual(get_profile(self.conn, 1)["platform_name"], "林鑫阳")
        # 手填姓名优先于平台昵称
        set_profile(self.conn, 1, name="林锦阳")
        p = get_profile(self.conn, 1)
        self.assertEqual(p["display_name"], "林锦阳")
        self.assertEqual(p["platform_name"], "林鑫阳")

    def test_merge_platform_only_fills_empty(self):
        set_profile(self.conn, 1, name="手填名", grade="2024级")  # 手填值不许被覆盖
        res = merge_platform_profile(
            self.conn, 1,
            {"name": "平台名", "student_no": "2024800000",
             "grade": "平台级", "major": "平台专业"})
        self.assertTrue(res["ok"])
        self.assertIn("student_no", res["filled"])
        self.assertIn("major", res["filled"])
        self.assertIn("name", res["skipped"])   # 手填姓名 → 跳过
        self.assertIn("grade", res["skipped"])  # 手填年级 → 跳过
        p = get_profile(self.conn, 1)
        self.assertEqual(p["name"], "手填名")
        self.assertEqual(p["grade"], "2024级")
        self.assertEqual(p["student_no"], "2024800000")
        self.assertEqual(p["major"], "平台专业")
        self.assertEqual(p["source"], "platform")  # 有回填 → 标注来源平台

    def test_merge_platform_nothing_new(self):
        set_profile(self.conn, 1, name="甲")
        res = merge_platform_profile(self.conn, 1, {"name": "平台甲"})
        self.assertTrue(res["ok"])
        self.assertEqual(res["filled"], [])
        self.assertEqual(get_profile(self.conn, 1)["name"], "甲")
        self.assertEqual(get_profile(self.conn, 1)["source"], "manual")  # 未回填不标平台

    def test_keyword_search_hits_name(self):
        set_profile(self.conn, 1, name="林锦阳")
        self._log(1, "video-done", "课A｜一｜60s")
        self._log(2, "video-done", "课B｜二｜60s")
        d = aggregate(self.conn, by="account", days=0, q="林锦阳")
        self.assertEqual([r["key"] for r in d["rows"]], ["#1 甲"])
        self.assertEqual(d["total"]["videos"], 1)


if __name__ == "__main__":
    unittest.main()
