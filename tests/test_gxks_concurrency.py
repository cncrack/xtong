"""并发闸 + 启动错峰（P2a，2026-11-06）。

## 背景

`PLAN-GXKS.md` §7.3 早就要求「全局并发上限是配置项」「多账号并行要错峰」，
但到 2026-11-06 为止**完全没做**：批量勾几个就同时开几个。

用户选择先做 **P2a（软闸）** 而不是 P2b（完整队列）：

- 软闸：满了就**拒绝**并说清原因（先停一个 / 调高上限），不加常驻调度循环；
- 队列：需要后台循环 + 面板重启后重建队列，留作 P2b。

默认上限取 **2**（不是方案里暂定的 10）：方案的风险页自己写了「写 10、首日先压到 2~3」，
且 D12 明确核实期只能 1 个会话 —— 本站并发面完全未验证。

## 守什么

① 上限从**面板设置**读（可配，默认 2，钳 1~20，**不许 0**）；
② 满了拒绝、不满放行，拒绝回包带 `blocked` + `concurrency_full`；
③ **只数 study 任务** —— 节列表/练习不占学习并发位（它们是短任务）；
④ 批量**按配额**起：`room = 上限 - 已在跑`，超出部分标 blocked 而不是全起；
⑤ 错峰只在**批量 ≥2 个**时加，且传给子进程（面板不许 sleep 卡住 api）。
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from xxt.config import Settings
from xxt.panel_gxks import app as panel_app
from xxt.panel_gxks.app import create_app
from xxt.store import gxks_panel_settings as gp

FAKE_ACCOUNTS = {
    "accounts": [
        {"label": "a1", "display": "甲", "port": 9222},
        {"label": "a2", "display": "乙", "port": 9223},
        {"label": "a3", "display": "丙", "port": 9224},
    ]
}


def _fake_job(label: str, kind: str = "study"):
    proc = mock.Mock(pid=1)
    proc.poll.return_value = None                  # 活着
    return {"proc": proc, "label": label, "kind": kind, "since": "12:00:00",
            "log": Path("nope.log"), "detail": ""}


class TestPanelSettingsConcurrency(unittest.TestCase):
    """配置项本身：默认值、钳制、存取。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "t.db"
        import sqlite3
        conn = sqlite3.connect(str(self.db))
        conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.commit()
        conn.close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_defaults_are_conservative(self):
        s = gp.load(self.db)
        # 默认 2（不是方案里暂定的 10）：并发面未验证，宁严勿松
        self.assertEqual(s["max_concurrency"], 2)
        self.assertEqual(s["start_jitter"], 30)

    def test_save_and_read_back(self):
        gp.save(self.db, {"max_concurrency": 5, "start_jitter": 10})
        s = gp.load(self.db)
        self.assertEqual(s["max_concurrency"], 5)
        self.assertEqual(s["start_jitter"], 10)

    def test_concurrency_never_zero(self):
        """**0 必须被钳成 1**：0 在额度语境里是"不限"，但这一项是"限几个"，
        语义反过来最危险（等于闸门失效）。"""
        gp.save(self.db, {"max_concurrency": 0})
        self.assertEqual(gp.load(self.db)["max_concurrency"], 1)

    def test_concurrency_clamped_high(self):
        gp.save(self.db, {"max_concurrency": 999})
        self.assertEqual(gp.load(self.db)["max_concurrency"], 20)

    def test_jitter_zero_is_allowed(self):
        """错峰 0 是合法的（= 不错峰），与并发上限不同。"""
        gp.save(self.db, {"start_jitter": 0})
        self.assertEqual(gp.load(self.db)["start_jitter"], 0)

    def test_jitter_clamped(self):
        gp.save(self.db, {"start_jitter": 9999})
        self.assertEqual(gp.load(self.db)["start_jitter"], 120)


class TestConcurrencyGate(unittest.TestCase):
    """闸门逻辑：满则拒、不满放行、只数 study。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # ⚠ `Settings(root=...)` 会把相对路径解析成**相对 CWD** 的路径，
        # 临时目录若传相对路径会和真实 data_dir 分叉 → 测试写进一个、读另一个。
        self.root = Path(self.tmp.name).resolve()
        (self.root / "data").mkdir(parents=True)
        (self.root / "data" / "accounts.local.json").write_text(
            json.dumps(FAKE_ACCOUNTS, ensure_ascii=False), encoding="utf-8")
        self._old_root = os.environ.get("XXT_ROOT")
        os.environ["XXT_ROOT"] = str(self.root)
        self.settings = Settings(root=self.root)
        self.client = TestClient(create_app(self.settings))
        panel_app._JOBS.clear()
        self._p_alive = mock.patch.object(panel_app.gchrome, "port_alive",
                                          return_value=True)
        self._p_alive.start()

    def tearDown(self):
        mock.patch.stopall()
        panel_app._JOBS.clear()
        if self._old_root is None:
            os.environ.pop("XXT_ROOT", None)
        else:
            os.environ["XXT_ROOT"] = self._old_root
        self.tmp.cleanup()

    def _set(self, **kw) -> None:
        gp.save(panel_app._db_path(self.settings), kw)

    def test_live_count_only_study(self):
        """只数 study：sections/work 是短任务，不该吃掉学习并发位。"""
        panel_app._JOBS["s1"] = _fake_job("a1", "study")
        panel_app._JOBS["x1"] = _fake_job("a2", "sections")
        panel_app._JOBS["x2"] = _fake_job("a3", "work")
        self.assertEqual(panel_app._live_study_count(), 1)

    def test_dead_jobs_not_counted(self):
        dead = _fake_job("a1")
        dead["proc"].poll.return_value = 0            # 已结束
        panel_app._JOBS["s1"] = dead
        self.assertEqual(panel_app._live_study_count(), 0)

    def test_gate_allows_below_limit(self):
        self._set(max_concurrency=2)
        panel_app._JOBS["s1"] = _fake_job("a1")
        self.assertIsNone(panel_app._check_concurrency(self.settings))

    def test_gate_refuses_at_limit(self):
        self._set(max_concurrency=2)
        panel_app._JOBS["s1"] = _fake_job("a1")
        panel_app._JOBS["s2"] = _fake_job("a2")
        got = panel_app._check_concurrency(self.settings)
        self.assertIsNotNone(got)
        self.assertTrue(got["blocked"])
        self.assertTrue(got["concurrency_full"])
        self.assertEqual(got["live"], 2)
        self.assertEqual(got["limit"], 2)
        # 必须告诉用户怎么解决，而不只是"不行"
        self.assertIn("面板设置", got["error"])

    def test_single_start_refused_when_full(self):
        """单账号「学习」也受限：否则用户绕开批量就能突破上限。"""
        self._set(max_concurrency=1)
        panel_app._JOBS["s1"] = _fake_job("a1")
        with mock.patch.object(panel_app, "_spawn_script") as spawn:
            body = self.client.post("/api/study",
                                    json={"label": "a2", "max_sections": 1}).json()
        self.assertFalse(body["ok"])
        self.assertTrue(body["concurrency_full"])
        spawn.assert_not_called()

    def test_state_reports_concurrency_status(self):
        """批量栏要显示「并发 N/M」，所以 /api/state 必须带上这两个数。"""
        self._set(max_concurrency=3)
        panel_app._JOBS["s1"] = _fake_job("a1")
        body = self.client.get("/api/state").json()
        self.assertEqual(body["limit_concurrency"], 3)
        self.assertEqual(body["live_study"], 1)


class TestBatchQuotaAndJitter(unittest.TestCase):
    """批量按配额起、超出标 blocked、错峰只在批量时加。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / "data").mkdir(parents=True)
        (self.root / "data" / "accounts.local.json").write_text(
            json.dumps(FAKE_ACCOUNTS, ensure_ascii=False), encoding="utf-8")
        self._old_root = os.environ.get("XXT_ROOT")
        os.environ["XXT_ROOT"] = str(self.root)
        self.settings = Settings(root=self.root)
        self.client = TestClient(create_app(self.settings))
        panel_app._JOBS.clear()
        self._p_alive = mock.patch.object(panel_app.gchrome, "port_alive",
                                          return_value=True)
        self._p_alive.start()
        self._p_ports = mock.patch.object(panel_app.gchrome, "ports_alive",
                                          return_value={9222: True, 9223: True,
                                                        9224: True})
        self._p_ports.start()

    def tearDown(self):
        mock.patch.stopall()
        panel_app._JOBS.clear()
        if self._old_root is None:
            os.environ.pop("XXT_ROOT", None)
        else:
            os.environ["XXT_ROOT"] = self._old_root
        self.tmp.cleanup()

    def _set(self, **kw) -> None:
        gp.save(panel_app._db_path(self.settings), kw)

    def _batch(self, labels, action="start", **kw) -> dict:
        return self.client.post("/api/accounts/batch",
                                json={"labels": labels, "action": action, **kw}).json()

    def test_batch_respects_quota(self):
        """上限 2、勾 3 个 → 只起 2 个，第 3 个 blocked（不是全起也不是全不起）。"""
        self._set(max_concurrency=2, start_jitter=0)
        with mock.patch.object(panel_app, "_spawn_script") as spawn:
            spawn.return_value = mock.Mock(pid=1)
            body = self._batch(["a1", "a2", "a3"])
        self.assertEqual(spawn.call_count, 2)
        self.assertEqual(body["done"], 2)
        self.assertEqual(body["blocked"], 1)
        self.assertEqual(body["failed"], 0)          # 配额挡住不是"失败"
        # 文案要**说清是并发满**，不能说成"需先停止"（那是另一码事）
        self.assertIn("并发已满", body["message"])
        self.assertIn("调高上限", body["message"])
        self.assertIn("a3", body["message"])

    def test_batch_quota_counts_already_running(self):
        """已在跑的占配额：上限 2、已跑 1 个 → 这批只能再起 1 个。"""
        self._set(max_concurrency=2, start_jitter=0)
        panel_app._JOBS["s1"] = _fake_job("a1")
        with mock.patch.object(panel_app, "_spawn_script") as spawn:
            spawn.return_value = mock.Mock(pid=1)
            body = self._batch(["a2", "a3"])
        self.assertEqual(spawn.call_count, 1)
        self.assertEqual(body["done"], 1)
        self.assertEqual(body["blocked"], 1)

    def test_batch_full_refuses_all(self):
        """已经满了 → 一个都不起，全部 blocked，并说清怎么解决。"""
        self._set(max_concurrency=2, start_jitter=0)
        panel_app._JOBS["s0"] = _fake_job("a3")
        panel_app._JOBS["s1"] = _fake_job("a1")
        with mock.patch.object(panel_app, "_spawn_script") as spawn:
            body = self._batch(["a2"])
        spawn.assert_not_called()
        self.assertEqual(body["blocked"], 1)
        # 「一个都没起」时 message 里没有 done 项，原因在 results/error 里
        blob = body["message"] + str(body.get("error")) + str(
            [r["message"] for r in body["results"]])
        self.assertIn("面板设置", blob)

    def test_jitter_passed_only_for_multi_batch(self):
        """批量 ≥2 个才传 `--start-jitter`；单个账号不该凭空等 30 秒。"""
        self._set(max_concurrency=5, start_jitter=30)
        with mock.patch.object(panel_app, "_spawn_script") as spawn:
            spawn.return_value = mock.Mock(pid=1)
            self._batch(["a1", "a2"])
            argv2 = list(spawn.call_args.args[4:])
            self.assertIn("--start-jitter", argv2)
            self.assertEqual(argv2[argv2.index("--start-jitter") + 1], "30")

        with mock.patch.object(panel_app, "_spawn_script") as spawn:
            spawn.return_value = mock.Mock(pid=1)
            self._batch(["a1"])
            argv1 = list(spawn.call_args.args[4:])
            self.assertNotIn("--start-jitter", argv1)

    def test_zero_jitter_means_no_sleep(self):
        """错峰设 0（用户主动关掉）时不许传这个参数，否则每条命令都多一个无用项。"""
        self._set(max_concurrency=5, start_jitter=0)
        with mock.patch.object(panel_app, "_spawn_script") as spawn:
            spawn.return_value = mock.Mock(pid=1)
            self._batch(["a1", "a2"])
            argv = list(spawn.call_args.args[4:])
        self.assertNotIn("--start-jitter", argv)


class TestConcurrencyPageControls(unittest.TestCase):
    """面板设置里必须有这两项的输入框 + 读写接线。"""

    def test_controls_exist(self):
        from xxt.panel_gxks.page import PAGE
        self.assertIn('id="f-concurrency"', PAGE)
        self.assertIn('id="f-jitter"', PAGE)

    def test_load_and_save_wired(self):
        from xxt.panel_gxks.page import PAGE
        load = PAGE.split("async function loadPanelSettings(", 1)[1].split("\n}\n", 1)[0]
        save = PAGE.split("async function savePanelSettings(", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("f-concurrency", load)
        self.assertIn("f-jitter", load)
        self.assertIn("max_concurrency:", save)
        self.assertIn("start_jitter:", save)

    def test_batch_bar_shows_concurrency_status(self):
        """批量栏要显示「并发 N/M」，满了给红标签 —— 用户点之前就知道。"""
        from xxt.panel_gxks.page import PAGE
        bar = PAGE.split("function renderBatchBar(", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("limit_concurrency", bar)
        self.assertIn("live_study", bar)
        self.assertIn("并发 ", bar)

    def test_concurrency_input_not_zero_allowed_in_ui(self):
        """UI 的 min 必须是 1：与后端"0 钳成 1"一致，别让用户以为 0 = 不限。"""
        from xxt.panel_gxks.page import PAGE
        seg = PAGE.split('id="f-concurrency"', 1)[1].split(">", 1)[0]
        self.assertIn('min="1"', seg)


if __name__ == "__main__":
    unittest.main()
