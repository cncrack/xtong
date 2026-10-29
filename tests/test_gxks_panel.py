"""第二平台面板接口测试（TestClient + 临时根目录）。

重点不是“路由能通”，而是三条容易出事故的边界：
① 账号代号非法/未知一律拒，不静默回落（否则会拿别人的账号去跑）；
② 账号的 CDP 端口必须由账号表决定（一账号一端口），不能两边各猜一个；
③ 接口回包里**不得出现凭据**。
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from xxt.adapters.gxks import accounts as gaccounts
from xxt.config import Settings
from xxt.panel_gxks.app import create_app

FAKE_ACCOUNTS = {
    "accounts": [
        {"label": "jia", "display": "甲同学", "name": "s1001", "pw": "pw-A-123",
         "port": 9222, "profile": "D:/profiles/jia", "daily_sections": 8},
        {"label": "yi", "display": "乙同学", "port": 9223},
    ]
}


class TestGxksPanel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "data").mkdir(parents=True)
        (self.root / "data" / "accounts.local.json").write_text(
            json.dumps(FAKE_ACCOUNTS, ensure_ascii=False), encoding="utf-8")
        # `accounts.default_path()` 跟着项目根走 → 用 XXT_ROOT 把临时目录顶上，
        # 否则测试会读到开发机上真实的账号表
        self._old_root = os.environ.get("XXT_ROOT")
        os.environ["XXT_ROOT"] = str(self.root)
        self.settings = Settings(root=self.root)
        self.client = TestClient(create_app(self.settings))

    def tearDown(self):
        if self._old_root is None:
            os.environ.pop("XXT_ROOT", None)
        else:
            os.environ["XXT_ROOT"] = self._old_root
        self.tmp.cleanup()

    # ---------------------------------------------------------------- 页面/状态

    def test_index_renders(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("第二平台控制台", r.text)

    def test_state_lists_accounts_without_credentials(self):
        r = self.client.get("/api/state")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["ok"])
        self.assertEqual([a["label"] for a in body["accounts"]], ["jia", "yi"])
        self.assertEqual(body["label"], "jia")           # 缺省取第一个
        self.assertTrue(body["accounts_file"])
        # 凭据绝不出现在回包里（连字段名都不该有）
        raw = r.text
        for secret in ("pw-A-123", "s1001", '"pw"', '"name"'):
            self.assertNotIn(secret, raw)

    def test_state_rejects_unknown_label(self):
        r = self.client.get("/api/state?label=nobody")
        self.assertEqual(r.status_code, 400)

    def test_state_auto_submit_defaults_off(self):
        self.assertFalse(self.client.get("/api/state").json()["auto_submit"])

    # ---------------------------------------------------------------- 起任务

    def test_work_requires_exercise_id(self):
        r = self.client.post("/api/work", json={"label": "jia", "exercise_id": ""})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["ok"])

    def test_work_rejects_unknown_account(self):
        r = self.client.post("/api/work", json={"label": "../etc", "exercise_id": "1"})
        self.assertEqual(r.status_code, 400)

    def test_work_spawns_with_account_port(self):
        with mock.patch("xxt.panel_gxks.app._spawn_script") as spawn:
            spawn.return_value = mock.Mock(pid=4321)
            r = self.client.post("/api/work", json={
                "label": "yi", "exercise_id": "14670",
                "section_id": "1299", "section_name": "2.2"})
            body = r.json()
            self.assertTrue(body["ok"])
            args = spawn.call_args.args
        self.assertEqual(args[1], "yi")
        self.assertEqual(args[3], "gxks_work.py")
        argv = list(args[4:])
        self.assertEqual(argv[argv.index("--port") + 1], "9223")   # 乙同学是 9223
        self.assertIn("14670", argv)

    def test_study_spawns_with_account_port(self):
        with mock.patch("xxt.panel_gxks.app._spawn_script") as spawn:
            spawn.return_value = mock.Mock(pid=1)
            self.client.post("/api/study", json={"label": "jia", "max_sections": 99})
            argv = list(spawn.call_args.args[4:])
        self.assertEqual(argv[argv.index("--port") + 1], "9222")
        # 上限收紧到 12：面板不给“一次刷一整天”的入口
        self.assertEqual(argv[argv.index("--max-sections") + 1], "12")

    def test_sections_refresh_spawns_all_pages(self):
        with mock.patch("xxt.panel_gxks.app._spawn_script") as spawn:
            spawn.return_value = mock.Mock(pid=1)
            self.client.post("/api/sections/refresh", json={"label": "jia"})
            argv = list(spawn.call_args.args[4:])
        self.assertIn("--all", argv)
        self.assertEqual(argv[argv.index("--port") + 1], "9222")

    def test_stop_without_job(self):
        r = self.client.post("/api/stop", json={"label": "jia"})
        self.assertFalse(r.json()["ok"])


class TestResolvePort(unittest.TestCase):
    def test_port_is_account_property(self):
        accs = gaccounts.parse_accounts(FAKE_ACCOUNTS)
        self.assertEqual(gaccounts.resolve_port("jia", accounts=accs), 9222)
        self.assertEqual(gaccounts.resolve_port("yi", accounts=accs), 9223)
        # 表里没有 → 用调用方给的兜底（config 的 port_base），不是 0
        self.assertEqual(gaccounts.resolve_port("nobody", accounts=accs, fallback=9300), 9300)


if __name__ == "__main__":
    unittest.main()
