"""批量账号操作的接口测试（`POST /api/accounts/batch`）。

用户 2026-10-30 要求：多选后能**统一操作**（开始/停止/启用/停用/删除）。

守四条（批量最怕"第 N 个报错，后面全没做"）：

① **逐个收集结果**，一个失败不拖累其它；汇总里说清谁成了、谁没成、为什么；
② **与单个操作同语义**（复用 `_study_one`/`_stop_one`），不许走偏
   —— 比如批量启动也得"已有任务在跑就跳过"；
③ 删除两道闸：**必须 `confirm`** + **不许删光**；
④ 未知代号**整批拒绝**（宁可什么都不做，也不要静默按存在的那些跑）。
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

FAKE_ACCOUNTS = {
    "accounts": [
        {"label": "a1", "display": "甲", "port": 9222, "name": "u1", "pw": "p1"},
        {"label": "a2", "display": "乙", "port": 9223},
        {"label": "a3", "display": "丙", "port": 9224, "enabled": False},
    ]
}


class TestAccountsBatch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "data").mkdir(parents=True)
        self.path = self.root / "data" / "accounts.local.json"
        self.path.write_text(json.dumps(FAKE_ACCOUNTS, ensure_ascii=False), encoding="utf-8")
        self._old_root = os.environ.get("XXT_ROOT")
        os.environ["XXT_ROOT"] = str(self.root)
        self.client = TestClient(create_app(Settings(root=self.root)))
        self._p_alive = mock.patch.object(panel_app.gchrome, "port_alive",
                                          return_value=False)
        self._p_alive.start()

    def tearDown(self):
        mock.patch.stopall()
        panel_app._JOBS.clear()
        if self._old_root is None:
            os.environ.pop("XXT_ROOT", None)
        else:
            os.environ["XXT_ROOT"] = self._old_root
        self.tmp.cleanup()

    def _rows(self) -> list[dict]:
        return json.loads(self.path.read_text(encoding="utf-8"))["accounts"]

    def _batch(self, labels, action, **kw) -> dict:
        return self.client.post("/api/accounts/batch",
                                json={"labels": labels, "action": action, **kw}).json()

    # ---------------------------------------------------------------- 入参校验

    def test_empty_selection_rejected(self):
        body = self._batch([], "enable")
        self.assertFalse(body["ok"])
        self.assertIn("没有选中", body["error"])

    def test_unknown_action_rejected(self):
        body = self._batch(["a1"], "explode")
        self.assertFalse(body["ok"])
        self.assertIn("不认识的批量动作", body["error"])

    def test_unknown_label_rejects_whole_batch(self):
        """宁可什么都不做，也不要静默只跑存在的那几个。"""
        body = self._batch(["a1", "ghost"], "enable")
        self.assertFalse(body["ok"])
        self.assertIn("ghost", body["error"])
        # a1 一个都没动（夹具里 a3 本来就是停用的，不能拿它当"没动"的证据）
        rows = {r["label"]: r for r in self._rows()}
        self.assertTrue(rows["a1"].get("enabled", True))
        self.assertTrue(rows["a2"].get("enabled", True))

    # ---------------------------------------------------------------- 启用/停用

    def test_enable_and_disable(self):
        body = self._batch(["a1", "a2"], "disable")
        self.assertTrue(body["ok"])
        self.assertEqual(body["done"], 2)
        rows = {r["label"]: r for r in self._rows()}
        self.assertFalse(rows["a1"]["enabled"])
        self.assertFalse(rows["a2"]["enabled"])
        body = self._batch(["a1", "a2"], "enable")
        self.assertTrue(body["ok"])
        rows = {r["label"]: r for r in self._rows()}
        self.assertTrue(rows["a1"]["enabled"])

    def test_disable_writes_batch_note_in_ops(self):
        self._batch(["a1"], "disable")
        rows = self.client.get("/api/ops").json()["rows"]
        self.assertEqual(rows[0]["action"], "account-toggle")
        self.assertIn("批量", rows[0]["detail"])

    # ---------------------------------------------------------------- 开始（走与单个相同的逻辑）

    def _start(self, labels, **kw) -> dict:
        """批量启动的统一夹具：端口探活替身成活（否则 2026-11-06 加的端口闸会全拦下）。"""
        with mock.patch.object(panel_app, "_spawn_script") as spawn, \
             mock.patch.object(panel_app.gchrome, "port_alive", return_value=True):
            spawn.return_value = mock.Mock(pid=4321)
            body = self._batch(labels, "start", **kw)
        self.spawn = spawn
        return body

    def test_start_spawns_each_account(self):
        body = self._start(["a1", "a2"], max_sections=5)
        self.assertTrue(body["ok"])
        self.assertEqual(self.spawn.call_count, 2)
        argv = list(self.spawn.call_args.args[4:])
        # 端口按各自的账号表走，不猜
        self.assertIn("--port", argv)

    def test_start_skips_account_already_running(self):
        """批量启动与单个启动同语义：已有任务在跑的**跳过**，其余照跑。

        「已在跑」是**预期内的跳过**，不是失败：汇总里要分开数，
        否则用户看到「1 个没做成」会以为出错了（2026-11-06）。
        """
        proc = mock.Mock(pid=1)
        proc.poll.return_value = None
        panel_app._JOBS["a1"] = {"proc": proc, "label": "a1", "kind": "study",
                                 "since": "12:00:00", "log": Path("nope.log"),
                                 "detail": ""}
        body = self._start(["a1", "a2"])
        self.assertEqual(self.spawn.call_count, 1)     # 只起了 a2
        self.assertEqual(body["done"], 1)
        self.assertEqual(body["skipped"], 1)           # 跳过 ≠ 失败
        self.assertEqual(body["failed"], 0)
        self.assertTrue(body["ok"])                    # 没有真失败 → 不该弹红提示
        self.assertIn("跳过 1 个", body["message"])
        self.assertIn("a1", body["message"])

    def test_start_reports_accounts_without_browser(self):
        """浏览器没开的账号：不起进程，回包里带 `need_browser` 供前端摊开提示。"""
        with mock.patch.object(panel_app, "_spawn_script") as spawn, \
             mock.patch.object(panel_app.gchrome, "port_alive", return_value=False):
            body = self._batch(["a1", "a2"], "start")
        spawn.assert_not_called()
        self.assertFalse(body["ok"])
        self.assertEqual(body["failed"], 2)
        for r in body["results"]:
            self.assertTrue(r["need_browser"], f"{r['label']} 应标记 need_browser")
            self.assertIn("9222" if r["label"] == "a1" else "9223", r["message"])

    def test_non_start_actions_do_not_probe_ports(self):
        """停止/启用/停用/删除不看浏览器 —— 白探一轮最坏吃满 timeout（2026-11-06）。"""
        with mock.patch.object(panel_app.gchrome, "ports_alive") as ports_alive, \
             mock.patch.object(panel_app.gchrome, "port_alive") as port_alive:
            self._batch(["a1", "a2"], "enable")
        ports_alive.assert_not_called()
        port_alive.assert_not_called()

    def test_start_probes_ports_concurrently(self):
        """批量启动必须**并发**探活：否则 N 个账号最坏 N × 0.5 秒。"""
        with mock.patch.object(panel_app.gchrome, "ports_alive",
                               return_value={9222: True, 9223: True}) as ports_alive, \
             mock.patch.object(panel_app, "_spawn_script") as spawn:
            spawn.return_value = mock.Mock(pid=1)
            body = self._batch(["a1", "a2"], "start")
        ports_alive.assert_called_once()
        self.assertEqual(sorted(ports_alive.call_args.args[0]), [9222, 9223])
        self.assertEqual(body["done"], 2)

    # ---------------------------------------------------------------- 停止

    def test_stop_reports_accounts_without_job(self):
        proc = mock.Mock(pid=1)
        proc.poll.return_value = None
        panel_app._JOBS["a2"] = {"proc": proc, "label": "a2", "kind": "study",
                                 "since": "12:00:00", "log": Path("nope.log"),
                                 "detail": ""}
        with mock.patch.object(panel_app, "_kill", return_value="已停止"):
            body = self._batch(["a1", "a2"], "stop")
        self.assertEqual(body["done"], 1)              # a2 停了
        self.assertEqual(body["failed"], 1)            # a1 本来没跑
        self.assertIn("没有在跑", body["error"])

    # ---------------------------------------------------------------- 删除

    def test_delete_requires_confirm(self):
        body = self._batch(["a1"], "delete")
        self.assertFalse(body["ok"])
        self.assertIn("二次确认", body["error"])
        self.assertEqual(len(self._rows()), 3)

    def test_delete_refuses_to_wipe_all(self):
        body = self._batch(["a1", "a2", "a3"], "delete", confirm=True)
        self.assertFalse(body["ok"])
        self.assertIn("删光", body["error"])
        self.assertEqual(len(self._rows()), 3)

    def test_delete_partial_ok(self):
        body = self._batch(["a1", "a2"], "delete", confirm=True)
        self.assertTrue(body["ok"])
        self.assertEqual([r["label"] for r in self._rows()], ["a3"])

    def test_delete_refuses_running_account(self):
        proc = mock.Mock(pid=1)
        proc.poll.return_value = None
        panel_app._JOBS["a1"] = {"proc": proc, "label": "a1", "kind": "study",
                                 "since": "12:00:00", "log": Path("nope.log"),
                                 "detail": ""}
        body = self._batch(["a1", "a2"], "delete", confirm=True)
        # a2 删掉、a1 留下
        self.assertEqual([r["label"] for r in self._rows()], ["a1", "a3"])
        self.assertEqual(body["failed"], 1)


if __name__ == "__main__":
    unittest.main()
