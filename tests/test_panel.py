"""面板接口测试（TestClient + 临时库）。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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

    def test_strategy_settings_roundtrip(self):
        """2026-10-27 用户：守护/节级策略“必为配置项，面板可改”。"""
        body = {
            "auto_max_tries": 3, "auto_retry_minutes": 20, "auto_confirm_seconds": 30,
            "auto_retry_jitter_pct": 55, "auto_retry_max_minutes": 150,
            "auto_hard_tries_cap": 6,
            "learn_enter_retries": 3, "learn_max_skip_chapters": 4,
            "learn_non_video_streak": 5, "learn_partial_retries": 2,
            "learn_partial_retry_ratio_pct": 70, "learn_login_budget_per_day": 3,
            "learn_non_video_marks": "作业,小组任务",
        }
        self.assertEqual(self.client.put("/api/settings", json=body).json(), {"ok": True})
        s = self.client.get("/api/settings").json()
        for k, v in body.items():
            self.assertEqual(s[k], v, k)

    def test_strategy_settings_reject_out_of_range(self):
        for bad in ({"auto_max_tries": 11}, {"auto_max_tries": -1},
                    {"learn_partial_retries": 9}, {"learn_partial_retry_ratio_pct": 10},
                    {"learn_login_budget_per_day": 0}, {"auto_confirm_seconds": 5},
                    {"auto_retry_jitter_pct": 101}, {"auto_retry_max_minutes": 9999},
                    {"auto_hard_tries_cap": 0}, {"auto_hard_tries_cap": 99},
                    {"auto_max_tries": "3"}):
            r = self.client.put("/api/settings", json=bad)
            self.assertFalse(r.json()["ok"], bad)

    def test_float_ratio_and_reconcile_roundtrip(self):
        """额度浮动与对账参数必须能在面板改（2026-10-29 用户：“做成配置项，后期自己调节”）。"""
        body = {"quota_float_lo": 80, "quota_float_hi": 95,
                "reconcile_pre_session": False, "reconcile_demote": True,
                "reconcile_min_coverage_pct": 88, "reconcile_max_demote_ratio_pct": 15}
        self.assertEqual(self.client.put("/api/settings", json=body).json(), {"ok": True})
        s = self.client.get("/api/settings").json()
        for k, v in body.items():
            self.assertEqual(s[k], v, k)
        # 面板提交的是百分数，落库得是比例列表
        conn = connect(self.settings.db_path)
        raw = conn.execute(
            "SELECT value FROM app_config WHERE key='quota.float_ratio'").fetchone()[0]
        conn.close()
        self.assertEqual(json.loads(raw), [0.8, 0.95])

    def test_float_ratio_and_reconcile_reject_bad_values(self):
        """越界与类型错必须当场拒绝，不能默默钳到边界值。"""
        for bad in ({"quota_float_lo": 40},                       # 低于 50
                    {"quota_float_hi": 120},                      # 高于 100
                    {"quota_float_lo": 90, "quota_float_hi": 60},  # 下限>上限
                    {"reconcile_pre_session": "yes"},              # 非布尔
                    {"reconcile_min_coverage_pct": 30},            # 护栏低于允许范围
                    {"reconcile_max_demote_ratio_pct": 0}):
            r = self.client.put("/api/settings", json=bad)
            self.assertFalse(r.json()["ok"], bad)

    def test_settings_writes_audit_trail(self):
        """每次保存都留痕旧值→新值（用户 2026-10-27：方便查阅和调整）。"""
        self.client.put("/api/settings", json={"auto_max_tries": 5})
        self.client.put("/api/settings", json={"auto_max_tries": 6})
        # key 已经是中文（2026-10-28 Q42：以前前端拿下划线名字去匹配点分 key，永远对不上）
        rows = [r for r in self.client.get("/api/settings").json()["recent_changes"]["rows"]
                if r["key"] == "每日自动发起次数"]
        self.assertGreaterEqual(len(rows), 2)
        self.assertEqual(rows[0]["new_value"], "6")
        self.assertEqual(rows[0]["old_value"], "5")
        # 写同值不再刷记录（否则每点一次保存都刷一条，反而没法查）
        n = len(rows)
        self.client.put("/api/settings", json={"auto_max_tries": 6})
        rows2 = [r for r in self.client.get("/api/settings").json()["recent_changes"]["rows"]
                 if r["key"] == "每日自动发起次数"]
        self.assertEqual(len(rows2), n)

    def test_account_quota_change_writes_audit(self):
        """账号级额度改动也要进「最近配置变更」（2026-10-28 Q41 用户实测反馈）。"""
        conn = connect(self.settings.db_path)
        # db 初始化自带 id=1 的 default 账号；这里只把它拉回“未设覆盖”的初始态
        conn.execute("INSERT OR IGNORE INTO accounts(id, label, created_at)"
                     " VALUES(1, '测试号', '2026-01-01T00:00:00')")
        conn.execute("UPDATE accounts SET daily_max_minutes=NULL, daily_max_tasks=NULL"
                     " WHERE id=1")
        conn.commit()
        conn.close()
        label = "账号#1 · 每日时长(分钟)"

        def _rows():
            data = self.client.get("/api/settings").json()["recent_changes"]
            return [r for r in data["rows"] if r["key"] == label]

        r = self.client.put("/api/accounts/1", json={
            "daily_max_minutes": 120, "daily_max_tasks": 3})
        self.assertEqual(r.json(), {"ok": True})
        self.assertEqual(len(_rows()), 1)
        self.assertEqual((_rows()[0]["old_value"], _rows()[0]["new_value"]), ("", "120"))

        # 同值再存不刷记录（与全局项一致）
        self.client.put("/api/accounts/1", json={"daily_max_minutes": 120})
        self.assertEqual(len(_rows()), 1)

        # 改成 100 → 记下旧值 120，方便事后查“谁把它从多少改成多少”
        self.client.put("/api/accounts/1", json={"daily_max_minutes": 100})
        self.assertEqual(len(_rows()), 2)
        self.assertEqual(_rows()[0]["old_value"], "120")
        self.assertEqual(_rows()[0]["new_value"], "100")
        # 任务数同样留痕
        data = self.client.get("/api/settings").json()["recent_changes"]
        keys = [r["key"] for r in data["rows"]]
        self.assertIn("账号#1 · 每日任务数", keys)
        # 卡片有总数字段（前端据它显示“共 N 条 / 只显示最近 8 条”）
        self.assertGreaterEqual(data["total"], len(data["rows"]))

    def test_panel_actions_go_to_run_logs(self):
        """面板上的操作也要进日志页（2026-10-28 Q42：“日志里看不到谁把额度改了”）。"""
        conn = connect(self.settings.db_path)
        conn.execute("INSERT OR IGNORE INTO accounts(id, label, created_at)"
                     " VALUES(2, '面板操作号', '2026-01-01T00:00:00')")
        conn.commit()
        conn.close()
        r = self.client.put("/api/accounts/2", json={"daily_max_minutes": 60,
                                                    "daily_max_tasks": 30})
        self.assertEqual(r.json(), {"ok": True})
        # ① 业务日志里有 account-edit，且写清了 旧→新
        logs = self.client.get("/api/logs?n=50&cat=panel").json()["logs"]
        edits = [x for x in logs if x["event"] == "account-edit" and x["account_id"] == 2]
        self.assertTrue(edits, "面板改额度没写进 run_logs")
        self.assertIn("每日时长(分钟)", edits[0]["detail"])
        self.assertIn("60", edits[0]["detail"])
        # ② 类别筛选：panel 类里不该混进 auto-* 调度事件
        self.assertFalse([x for x in logs if x["event"].startswith("auto-")])
        # ③ 学习类筛选只看会话/节/视频
        learned = self.client.get("/api/logs?n=50&cat=learn").json()["logs"]
        self.assertTrue(all(x["event"].startswith(("session-", "chapter-", "video-"))
                            for x in learned))

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

    def _seed_account_and_event(self, event: str, detail: str,
                                code: str | None = None) -> None:
        import time as _t

        conn = connect(self.settings.db_path)
        if not conn.execute("SELECT 1 FROM accounts LIMIT 1").fetchone():
            conn.execute("INSERT INTO accounts(label, created_at) VALUES('测试号','x')")
        conn.execute(
            "INSERT INTO run_logs(ts, level, event, detail, account_id, code)"
            " VALUES(?,?,?,?,1,?)",
            (_t.strftime("%Y-%m-%d") + "T10:00:00", "WARNING", event, detail, code))
        conn.commit()
        conn.close()

    def _alert(self):
        return self.client.get("/api/accounts").json()["accounts"][0]["alert"]

    def test_alert_surfaces_suspend_with_actions(self):
        """Q20/Q19（2026-10-27）：今日失败要变成卡片上一句话 + 能点的按钮。"""
        import json as _json

        self._seed_account_and_event(
            "auto-suspend",
            "挂起（页面打不开）触发后 1 分钟会话已退出（页面打不开｜今日已试 2/3 次）"
            "｜子进程报：连续 3 节都打不开任务页，30 分钟后自动恢复（约 10:30）")
        # 守护落库的退避时刻表 → 警示条要能回答“什么时候自动重试”
        import time as _t

        _later = _t.strftime("%H:%M", _t.localtime(_t.time() + 3600))
        conn = connect(self.settings.db_path)
        conn.execute("INSERT INTO app_config(key, value) VALUES('auto.retry_after', ?)",
                     (_json.dumps({"date": _t.strftime("%Y-%m-%d"),
                                   "times": {"1": _later}}),))
        conn.commit()
        conn.close()
        al = self.client.get("/api/accounts").json()["accounts"][0]["alert"]
        self.assertEqual(al["label"], "页面打不开")
        self.assertEqual(al["tier"], "suspended")
        self.assertIn("立即补跑", al["actions"])
        # 2026-10-28（A）：改成环形比较后，跨零点（如 23:50 → 00:30）也算“未到”，
        # 不能再用字符串比较把未来的时刻误清成空。
        self.assertEqual(al["resume_at"], _later)

    def test_alert_clears_after_progress_and_flags_hard_stop(self):
        self._seed_account_and_event("chapter-fail", "测试课｜1.2｜点了 2 次仍未进任务页")
        self.assertEqual(self.client.get("/api/accounts").json()["accounts"][0]
                         ["alert"]["tier"], "suspended")
        # 又播完一节 → 自愈，不再示警
        self._seed_account_and_event("video-done", "测试课｜1.2 测试节｜470s")
        self.assertIsNone(self.client.get("/api/accounts").json()["accounts"][0]["alert"])
        # 硬停：守护已判今日不再自动 → 警示条要标硬停（仍然给「立即补跑」入口）
        self._seed_account_and_event("auto-error", "硬停（页面打不开）：短退避与长退避都已用尽")
        al = self.client.get("/api/accounts").json()["accounts"][0]["alert"]
        self.assertEqual(al["tier"], "hard_stop")
        self.assertIn("立即补跑", al["actions"])

    def test_alert_self_heals_misbaked_structure_to_done(self):
        """自愈（会话72）：旧代码把「本地已无待学章节」误判成 structure 存库；
        面板出横幅前按当前规则复核 → 即时纠正为 done 并记一条「已纠正误报」日志，
        不示警、也不等跨天（用户：“纠正要生效，但日志里面要体现”）。"""
        self._seed_account_and_event(
            "auto-suspend",
            "挂起（页面打不开）：会话已退出，今日已播 7 个｜短退避已用 2/3 次，"
            "硬上限 4 轮｜ 下一节按钮不可点；本地已无待学章节｜30 分钟后自动恢复",
            code="structure")
        # ① 横幅即时消失
        self.assertIsNone(self._alert())
        conn = connect(self.settings.db_path)
        # ② 存量码被就地纠正为 done
        row = conn.execute(
            "SELECT code FROM run_logs WHERE event='auto-suspend' "
            "ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(row["code"], "done")
        # ③ 日志里留痕：新增一条 auto-corrected
        fixed = conn.execute(
            "SELECT detail FROM run_logs WHERE event='auto-corrected'").fetchall()
        self.assertTrue(fixed, "纠正没写进 run_logs")
        self.assertIn("已纠正", fixed[0]["detail"])
        conn.close()

    def test_alert_self_heal_is_idempotent(self):
        """复核纠正只发生一次：再次读告警不重复写 auto-corrected 日志（不刷屏）。"""
        self._seed_account_and_event(
            "auto-suspend",
            "挂起（页面打不开）：下一节按钮不可点；本地已无待学章节",
            code="structure")
        self.assertIsNone(self._alert())
        self.assertIsNone(self._alert())  # 第二次读：已纠正为 done，不再命中
        conn = connect(self.settings.db_path)
        n = conn.execute(
            "SELECT COUNT(*) FROM run_logs WHERE event='auto-corrected'").fetchone()[0]
        self.assertEqual(n, 1, "重复纠正会刷屏日志")
        conn.close()

    def test_hardstop_event_marks_card(self):
        """B（2026-10-28）：硬停改成独立事件 `auto-hardstop`，卡片照样标硬停 + 留补跑入口。

        以前硬停写的是 `auto-error`，面板靠“文案里有「硬停」”猜档位——正是 Q30 的老毛病。
        """
        self._seed_account_and_event(
            "auto-hardstop", "硬停（页面打不开）：短退避与长退避都已用尽")
        al = self._alert()
        self.assertEqual(al["tier"], "hard_stop")
        self.assertIn("立即补跑", al["actions"])

    def test_alert_also_reaches_overview_endpoint(self):
        """回归（2026-10-27 实测踩坑）：卡片是由 `/api/overview` 渲染的。

        当初只给 `/api/accounts` 挂了 alert，接口测试全绿，真实页面却一条
        警示条都没出现过。两个下发路径必须同源。
        """
        self._seed_account_and_event("chapter-fail", "测试课｜1.2｜点了 2 次仍未进任务页")
        ov = self.client.get("/api/overview").json()["accounts"][0]
        self.assertIn("alert", ov)
        self.assertEqual(ov["alert"]["label"], "页面打不开")
        self.assertIn("立即补跑", ov["alert"]["actions"])

    def test_video_interrupt_is_not_page_failure(self):
        """Q28（2026-10-28）：播放中断是 B3 自愈事件，不能报“页面打不开”。

        用户截图里第 1 个账号的卡片就是它——课明明在播，警示条却写
        「⚠ 页面打不开·已停手」，人就被引导去点“立即补跑”（反而多开一个会话）。
        """
        self._seed_account_and_event("video-interrupt",
                                     "测试课｜1.2 测试节｜重载任务页续播")
        al = self._alert()
        self.assertEqual(al["label"], "播放中断")
        self.assertEqual(al["code"], "interrupt")
        self.assertEqual(al["tier"], "suspended")

    def test_alert_suppressed_while_session_alive(self):
        """存活总闸：会话还活着（标记文件 pid 成立）→ 卡片不挂任何失败警示。"""
        import json as _json
        import os

        run = self.settings.data_dir / "run"
        run.mkdir(parents=True, exist_ok=True)
        marker = run / "1.json"
        marker.write_text(_json.dumps({"pid": os.getpid(), "course": "测试课",
                                       "since": "10:00"}), encoding="utf-8")
        self._seed_account_and_event("video-interrupt", "测试课｜1.2｜重载续播")
        self.assertIsNone(self._alert())
        self.assertTrue(marker.exists())  # 探活不能把活着的标记清掉

    def test_session_start_clears_old_alert(self):
        """手动 ▶ 重开后旧警示要立刻消失（事件清单以前没有 session-start）。"""
        self._seed_account_and_event("auto-error",
                                     "硬停（页面打不开）：短退避与长退避都已用尽")
        self.assertIsNotNone(self._alert())
        self._seed_account_and_event("session-start", "测试课｜2.1")
        self.assertIsNone(self._alert())

    def test_alert_prefers_machine_code(self):
        """Q30：文案里带“打不开”也不能盖掉守护写入的机器码。"""
        self._seed_account_and_event(
            "auto-suspend", "挂起（网络异常）：子进程报：打不开是因为代理掉线",
            code="network")
        al = self._alert()
        self.assertEqual(al["code"], "network")
        self.assertEqual(al["label"], "网络异常")

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


    def test_study_status_survives_concurrent_jobs_change(self):
        """轮询接口与启停线程并发改 _JOBS 时不得 500（2026-10-27 实报 RuntimeError）。

        前端每 1~2 秒拉一次 /api/study/status，同时点“开始/停止”或作业线程会增删
        `_JOBS` → 直接 items() 遍历会抛 dictionary changed size during iteration。
        """
        import threading

        import xxt.panel.app as appmod

        class _Dead:
            def poll(self):
                return 1

        stop = threading.Event()
        keys = [901, 902, 903, 904, 905]

        def churn():
            i = 0
            while not stop.is_set():
                appmod._JOBS[keys[i % len(keys)]] = {
                    "proc": _Dead(), "course": "测试", "since": "00:00"}
                appmod._JOBS.pop(keys[(i + 2) % len(keys)], None)
                i += 1

        t = threading.Thread(target=churn, daemon=True)
        t.start()
        try:
            for _ in range(40):
                r = self.client.get("/api/study/status")
                self.assertEqual(r.status_code, 200)
        finally:
            stop.set()
            t.join(timeout=5)
            for k in keys:
                appmod._JOBS.pop(k, None)


class NoTreeGatingTest(unittest.TestCase):
    """“有这门课” ≠ “抓过章节树”（2026-10-30 轻档）。

    以前面板只问 courses，0/0 的课也给 ▶：一点就 spawn，子进程在引擎前置检查处
    零访问退出，面板却弹“已开始刷”（假成功），守护还会把这轮记成“今日已触发”。
    现在无树当场拒，并把“抓章节”（不消耗额度）作为显式入口交给人。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(root=Path(self.tmp.name))
        conn = connect(self.settings.db_path)
        conn.execute("INSERT INTO courses(account_id, platform_course_id, name, url) "
                     "VALUES(1,'c1','有树课','http://x/1')")
        conn.execute("INSERT INTO courses(account_id, platform_course_id, name, url) "
                     "VALUES(1,'c2','无树课','http://x/2')")
        conn.commit()
        cid = conn.execute("SELECT id FROM courses WHERE platform_course_id='c1'").fetchone()["id"]
        conn.execute(
            "INSERT INTO chapters(course_id, platform_chapter_id, name, idx, status) "
            "VALUES(?, 'k1', '1.1 有树节', 0, 'pending')", (cid,))
        conn.commit()
        conn.close()
        self.client = TestClient(create_app(self.settings))

    def tearDown(self):
        self.tmp.cleanup()

    def test_study_start_refuses_course_without_tree(self):
        with patch("xxt.panel.app._spawn") as sp:
            d = self.client.post("/api/study/start", json={
                "account_id": 1, "course": "无树课", "course_pid": "c2"}).json()
        self.assertFalse(d["ok"])
        self.assertTrue(d.get("no_tree"), d)   # 调用方靠这个区分“该抓树”与“真失败”
        self.assertIn("抓章节", d["error"])
        sp.assert_not_called()   # 关键：不能 spawn，否则当日机会被记成已触发

    def test_study_start_still_runs_course_with_tree(self):
        with patch("xxt.panel.app._spawn") as sp:
            sp.return_value = SimpleNamespace(pid=4321)
            d = self.client.post("/api/study/start", json={
                "account_id": 1, "course": "有树课", "course_pid": "c1"}).json()
        self.assertTrue(d["ok"], d)
        sp.assert_called_once()

    def test_chapters_fetch_spawns_cli_for_that_course(self):
        """抓章节走现成 `xxt chapters` 子进程（面板不自建第二套入库/对账）。"""
        with patch("xxt.panel.app._spawn") as sp:
            sp.return_value = SimpleNamespace(pid=9001)
            d = self.client.post("/api/chapters/fetch", json={
                "account_id": 1, "course": "无树课", "course_pid": "c2"}).json()
        self.assertTrue(d["ok"], d)
        self.assertFalse(d["had_tree"])
        # argv 必须用库里真实的课名（用户可能传模糊名），且带 --account
        self.assertEqual(sp.call_args[0][1:], ("chapters", "无树课", "--account", "1"))

    def test_chapters_fetch_rejects_unknown_course(self):
        with patch("xxt.panel.app._spawn") as sp:
            d = self.client.post("/api/chapters/fetch",
                                 json={"account_id": 1, "course": "没这门课"}).json()
        self.assertFalse(d["ok"])
        sp.assert_not_called()

    def test_overview_exposes_zero_total_for_no_tree_course(self):
        """前端拿 total=0 判“未抓章节”，所以这个字段不能退成 None。"""
        accs = self.client.get("/api/overview").json()["accounts"]
        courses = {c["name"]: c for c in accs[0]["courses"]}
        self.assertEqual(courses["无树课"]["total"], 0)
        self.assertEqual(courses["有树课"]["total"], 1)

    def test_no_pending_reason_distinguishes_no_tree_from_all_done(self):
        """两引擎共用的收工文案必须能区分成因，且诊断归类跟着分流。"""
        from xxt.adapters.chaoxing.pagejs import no_pending_reason
        from xxt.core.diagnose import classify

        conn = connect(self.settings.db_path)
        try:
            c2 = conn.execute(
                "SELECT id FROM courses WHERE platform_course_id='c2'").fetchone()["id"]
            r_no = no_pending_reason(conn, c2)
            c1 = conn.execute(
                "SELECT id FROM courses WHERE platform_course_id='c1'").fetchone()["id"]
            conn.execute("UPDATE chapters SET status='done' WHERE course_id=?", (c1,))
            conn.commit()
            r_done = no_pending_reason(conn, c1)
        finally:
            conn.close()
        self.assertIn("未抓章节", r_no)
        self.assertIn("1/1 已全部完成", r_done)
        self.assertNotIn("未抓章节", r_done)
        # notree 不能误入 structure（可挂起 → 守护白重试）；done 不能落入 unknown
        self.assertEqual(classify(r_no), "notree")
        self.assertEqual(classify(r_done), "done")

    def test_overview_exposes_pending_for_finished_and_inprogress(self):
        """卡片“已学完”归档判定以后端 `pending` 为准（与 rotation.course_weights 跳过同一把尺）。

        以前 overview 只发 total/done，前端拿 `done>0` 当“下次将继续”，
        导致 100% 学完的课还赖在 C 位标“下次将继续”（与后端永不重刷矛盾）。
        """
        def courses():
            accs = self.client.get("/api/overview").json()["accounts"]
            return {c["name"]: c for c in accs[0]["courses"]}

        # 初始：有树课 1 节 pending（未学完）
        c = courses()["有树课"]
        self.assertEqual(c["pending"], 1)
        self.assertEqual(c["done"], 0)
        # 无树课：total=0 → pending 也必须为 0（不能退 None，前端要参与判据）
        self.assertEqual(courses()["无树课"]["pending"], 0)
        # 把有树课标完→ pending 变 0、done 变 1（这就是“已学完”的信号）
        conn = connect(self.settings.db_path)
        try:
            cid = conn.execute(
                "SELECT id FROM courses WHERE platform_course_id='c1'").fetchone()["id"]
            conn.execute("UPDATE chapters SET status='done' WHERE course_id=?", (cid,))
            conn.commit()
        finally:
            conn.close()
        done_c = courses()["有树课"]
        self.assertEqual(done_c["pending"], 0)
        self.assertEqual(done_c["done"], 1)

    def test_overview_splits_video_from_assignment(self):
        """会话72（D）：overview 把课程分母拆成“视频 vs 作业”，供前端徽章说清
        “视频刷完·x/x｜另 N 项作业待做”，不再用“69/70”这种把作业混进分母的写法。"""
        conn = connect(self.settings.db_path)
        try:
            cid = conn.execute(
                "SELECT id FROM courses WHERE platform_course_id='c1'").fetchone()["id"]
            conn.execute("INSERT INTO chapters(course_id,platform_chapter_id,name,idx,status)"
                         " VALUES(?, 'k2', '1.2 视频二', 1, 'done')", (cid,))
            conn.execute("INSERT INTO chapters(course_id,platform_chapter_id,name,idx,status)"
                         " VALUES(?, 'k3', '第一章｜2.12 作业', 2, 'visited')", (cid,))
            conn.commit()
        finally:
            conn.close()
        c = {x["name"]: x for x in
             self.client.get("/api/overview").json()["accounts"][0]["courses"]}["有树课"]
        # k1 pending 视频 + k2 done 视频 + k3 作业(visited)：total=3，作业 1 节、已完成作业 0
        self.assertEqual(c["total"], 3)
        self.assertEqual(c["tasks"], 1)          # 名称含“作业”→非视频
        self.assertEqual(c["tasks_done"], 0)
        self.assertEqual(c["total"] - c["tasks"], 2)       # 视频总数
        self.assertEqual(c["done"] - c["tasks_done"], 1)   # 视频已学完数


class AutoGrindToggleTest(unittest.TestCase):
    """课程级「纳入自动刷」opt-in（v10 会话72）：抓树不再自动进轮转池。

    根治“刚抓到需要的课、没说要刷，守护就自动开刷”：
    • 默认 auto_grind=0 → course_weights 不选它（守护不会自动刷）；
    • 面板开关 POST /api/course/auto-grind 开→纳入，关→移出；
    • 开关写 config_audit 留痕，同值重复设置幂等。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(root=Path(self.tmp.name))
        conn = connect(self.settings.db_path)
        # 一门有树、全 pending 的课（旧行为下会被轮转自动开刷）
        conn.execute("INSERT INTO courses(account_id, platform_course_id, name, url) "
                     "VALUES(1,'c1','待刷课','http://x/1')")
        conn.commit()
        cid = conn.execute("SELECT id FROM courses WHERE platform_course_id='c1'").fetchone()["id"]
        for i in range(3):
            conn.execute(
                "INSERT INTO chapters(course_id, platform_chapter_id, name, idx, status) "
                "VALUES(?,?,?,?,'pending')", (cid, f"k{i}", f"1.{i} 节", i))
        conn.commit()
        conn.close()
        self.client = TestClient(create_app(self.settings))

    def tearDown(self):
        self.tmp.cleanup()

    def _weights(self):
        from xxt.core.rotation import course_weights
        conn = connect(self.settings.db_path)
        try:
            return {r["name"] for _, r in course_weights(conn, 1, {})}
        finally:
            conn.close()

    def test_not_in_rotation_pool_until_switched_on(self):
        # 默认关：有树有 pending 也不进池（这就是修复点）
        self.assertEqual(self._weights(), set())
        ov = self.client.get("/api/overview").json()["accounts"][0]["courses"][0]
        self.assertEqual(ov["auto_grind"], 0)
        # 开启后立刻进轮转池
        d = self.client.post("/api/course/auto-grind", json={
            "account_id": 1, "course": "待刷课", "course_pid": "c1", "on": True}).json()
        self.assertTrue(d["ok"], d)
        self.assertEqual(d["auto_grind"], 1)
        self.assertEqual(self._weights(), {"待刷课"})
        self.assertEqual(
            self.client.get("/api/overview").json()["accounts"][0]["courses"][0]["auto_grind"], 1)

    def test_switch_off_removes_and_writes_audit(self):
        self.client.post("/api/course/auto-grind", json={
            "account_id": 1, "course": "待刷课", "course_pid": "c1", "on": True})
        conn = connect(self.settings.db_path)
        try:
            n_audit = conn.execute(
                "SELECT COUNT(*) c FROM config_audit WHERE key LIKE 'course.%.auto_grind'"
            ).fetchone()["c"]
        finally:
            conn.close()
        self.assertEqual(n_audit, 1)   # 0→1 写一行审计
        self.client.post("/api/course/auto-grind", json={
            "account_id": 1, "course": "待刷课", "course_pid": "c1", "on": False})
        self.assertEqual(self._weights(), set())   # 1→0 移出池
        conn = connect(self.settings.db_path)
        try:
            n_audit = conn.execute(
                "SELECT COUNT(*) c FROM config_audit WHERE key LIKE 'course.%.auto_grind'"
            ).fetchone()["c"]
            n_dup = conn.execute(
                "SELECT COUNT(*) c FROM config_audit WHERE new_value='1'"
                " AND old_value='1' AND key LIKE 'course.%'").fetchone()["c"]
        finally:
            conn.close()
        self.assertEqual(n_audit, 2)   # 1→0 再写一行
        self.assertEqual(n_dup, 0)     # 同值不写审计

    def test_switch_unknown_course_errors(self):
        d = self.client.post("/api/course/auto-grind", json={
            "account_id": 1, "course": "没这门课", "on": True}).json()
        self.assertFalse(d["ok"])


class PanelPageJSSyntaxTest(unittest.TestCase):
    """面板内嵌 JS 语法守卫（2026-10-30 新增）。

    真实事故：轻档给 confirm 文案写了单 `\n`，而 `_PAGE` 是 Python 非原始字符串
    → `\n` 变成真换行弄断 JS 字符串 → 整个 <script> 块不执行 → 页面永久“加载中…”。
    pytest 只测接口不跑 JS，完全没拦住。这条用 `node --check` 守住同类错误。
    """

    def test_page_script_block_is_valid_js(self):
        import re
        import shutil
        import subprocess
        import tempfile
        from xxt.panel import app

        if shutil.which("node") is None:
            self.skipTest("本机无 node，跳过 JS 语法检查")
        js = "\n".join(re.findall(r"<script>(.*?)</script>", app._PAGE, re.S))
        self.assertTrue(js.strip(), "没抽到 <script> 块，守卫失效")
        with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8",
                                         delete=False) as f:
            f.write(js)
            path = f.name
        try:
            r = subprocess.run(["node", "--check", path],
                               capture_output=True, text=True)
        finally:
            import os
            os.unlink(path)
        self.assertEqual(r.returncode, 0, f"内嵌 JS 语法错误：\n{r.stderr[:800]}")


if __name__ == "__main__":
    unittest.main()
