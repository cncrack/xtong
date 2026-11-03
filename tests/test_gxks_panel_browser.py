"""第二平台面板的「浏览器 / 会话」接口测试（P2）。

守四条边界：
① 容器（managed）形态下**不许**在面板里拉本机 Chrome —— 那里根本没有窗口；
② 「停止浏览器」只杀面板自己起的 pid，别人的 Chrome 一概不碰；
③ `/api/session/check` 的结果只认脚本最后一行 JSON，脚本崩了要给原文尾巴；
④ 回包里不出现凭据。

全程替身：不找真 Chrome、不探真端口、不起真子进程。
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
        {"label": "jia", "display": "甲同学", "name": "s1001", "pw": "pw-A-123",
         "port": 9222, "profile": "D:/profiles/jia"},
        {"label": "yi", "display": "乙同学", "port": 9223},   # 故意不配 profile
    ]
}


class TestGxksPanelBrowser(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "data").mkdir(parents=True)
        (self.root / "data" / "accounts.local.json").write_text(
            json.dumps(FAKE_ACCOUNTS, ensure_ascii=False), encoding="utf-8")
        self._old_root = os.environ.get("XXT_ROOT")
        os.environ["XXT_ROOT"] = str(self.root)
        self.settings = Settings(root=self.root)
        self.client = TestClient(create_app(self.settings))
        panel_app._BROWSERS.clear()               # 模块级登记表，测试间必须清

        # 默认替身：找不到 Chrome、端口不活（避免碰到开发机真实环境）
        self._p_chrome = mock.patch.object(panel_app.gchrome, "find_chrome",
                                           return_value=None)
        self._p_alive = mock.patch.object(panel_app.gchrome, "port_alive",
                                          return_value=False)
        self._p_chrome.start()
        self._p_alive.start()

    def tearDown(self):
        mock.patch.stopall()
        panel_app._BROWSERS.clear()
        if self._old_root is None:
            os.environ.pop("XXT_ROOT", None)
        else:
            os.environ["XXT_ROOT"] = self._old_root
        self.tmp.cleanup()

    def _set_backend(self, name: str) -> None:
        self.settings.raw.setdefault("gxks", {})["browser_backend"] = name

    # ---------------------------------------------------------------- 只读状态

    def test_state_embeds_browser_block(self):
        body = self.client.get("/api/state").json()
        browser = body["browser"]
        self.assertEqual(browser["backend"], "cdp")
        self.assertEqual(browser["port"], 9222)          # 端口来自账号表
        self.assertFalse(browser["chrome_ok"])           # 替身：没找到
        self.assertFalse(browser["can_start"])
        self.assertIsNone(browser["session"])            # 还没检查过

    def test_state_never_leaks_credentials(self):
        """回包里不得出现凭据。

        用**精确检查**而不是子串黑名单：面板后来加了 AI 配置，其字段说明里本就有
        `{"key":"name"...}`、`credentials` 也是卡片上的布尔（不是凭据值）。
        子串匹配 `"name"`／`"pw"` 会误报，掩盖真正的泄露。

        这里检查两件事：
        ① 账号对象里没有 name/pw 字段；② 整个回包里没有真实凭据值。
        """
        r = self.client.get("/api/state")
        body = r.json()
        for acc in body["accounts"]:
            self.assertNotIn("name", acc)
            self.assertNotIn("pw", acc)
        for real in ("pw-A-123", "s1001"):
            self.assertNotIn(real, r.text)

    def test_browser_state_defaults_to_first_account(self):
        body = self.client.get("/api/browser/state").json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["port"], 9222)

    def test_browser_state_reads_last_session_file(self):
        debug = self.root / "data" / "debug"
        debug.mkdir(parents=True)
        (debug / "session_jia.json").write_text(json.dumps(
            {"logged_in": True, "checked_at": "2026-10-30 12:00:00"}),
            encoding="utf-8")
        body = self.client.get("/api/browser/state?label=jia").json()
        self.assertTrue(body["session"]["logged_in"])

    def test_browser_state_rejects_unknown_label(self):
        self.assertEqual(self.client.get("/api/browser/state?label=nobody").status_code, 400)

    # ---------------------------------------------------------------- 启动 / 停止

    def test_start_refused_on_managed_backend(self):
        self._set_backend("managed")
        body = self.client.post("/api/browser/start", json={"label": "jia"}).json()
        self.assertFalse(body["ok"])
        self.assertIn("managed", body["error"])

    def test_start_skips_when_port_already_alive(self):
        self._p_alive.stop()
        with mock.patch.object(panel_app.gchrome, "port_alive", return_value=True), \
             mock.patch.object(panel_app, "_start_chrome_interactive") as start, \
             mock.patch.object(panel_app, "_chrome_start_message",
                               return_value="已在运行"):
            body = self.client.post("/api/browser/start", json={"label": "jia"}).json()
            start.assert_not_called()
        self.assertTrue(body["ok"])
        self.assertIn("已在运行", body["message"])

    def test_start_without_profile_uses_default_dir(self):
        # 单账号回落表（account.local.json）本来就没有 profile 字段：
        # 默认落到 data/profiles/<代号>，否则这个按钮永远是灰的
        with mock.patch.object(panel_app, "_start_chrome_interactive",
                               return_value={"ok": True, "pid": 777}) as start, \
             mock.patch.object(panel_app, "_chrome_start_message",
                               return_value="ok"):
            self.client.post("/api/browser/start", json={"label": "yi"})
        # 端口取自账号表（乙同学 9223），不是全局默认
        self.assertEqual(start.call_args.args, ("yi", 9223))
        # profile 落到 data/profiles/yi
        own = panel_app._BROWSERS.get("yi") or {}
        self.assertEqual(Path(own["profile"]).name, "yi")

    def test_start_refused_without_chrome(self):
        """交互式任务报错（含找不到 Chrome）时，错误原样透出，不静默失败。"""
        with mock.patch.object(panel_app, "_start_chrome_interactive",
                               return_value={"ok": False,
                                             "error": "没找到 Chrome：C:/c/chrome.exe"}):
            body = self.client.post("/api/browser/start", json={"label": "jia"}).json()
        self.assertFalse(body["ok"])
        self.assertIn("没找到 Chrome", body["error"])

    def test_start_success_registers_pid_then_stop_kills_it(self):
        with mock.patch.object(panel_app, "_start_chrome_interactive",
                               return_value={"ok": True, "pid": 777}) as start, \
             mock.patch.object(panel_app, "_chrome_start_message",
                               return_value="已登录"), \
             mock.patch.object(panel_app, "_kill_pid", return_value="已停止") as kill:
            body = self.client.post("/api/browser/start", json={"label": "jia"}).json()
            self.assertTrue(body["ok"])
            # 端口与账号都取自账号表，不信全局配置
            self.assertEqual(start.call_args.args, ("jia", 9222))

            state = self.client.get("/api/browser/state?label=jia").json()
            self.assertTrue(state["started_by_panel"])
            self.assertEqual(panel_app._BROWSERS["jia"]["pid"], 777)

            stopped = self.client.post("/api/browser/stop", json={"label": "jia"}).json()
            self.assertTrue(stopped["ok"])
            kill.assert_called_once_with(777)
        self.assertNotIn("jia", panel_app._BROWSERS)
        self.assertEqual(self.client.post("/api/browser/stop",
                                          json={"label": "jia"}).json()["ok"], False)

    def test_start_reports_port_not_responding(self):
        with mock.patch.object(panel_app, "_start_chrome_interactive",
                               return_value={"ok": False,
                                             "error": "等了 40 秒，端口 9222 仍未响应"}):
            body = self.client.post("/api/browser/start", json={"label": "jia"}).json()
        self.assertFalse(body["ok"])
        self.assertIn("仍未响应", body["error"])

    def test_stop_refuses_foreign_browser(self):
        body = self.client.post("/api/browser/stop", json={"label": "jia"}).json()
        self.assertFalse(body["ok"])
        self.assertIn("不是面板起的", body["error"])
    def test_start_refused_while_job_running(self):
        proc = mock.Mock(pid=1)
        proc.poll.return_value = None                     # 活着：snapshot 才留得住这条
        panel_app._JOBS["jia"] = {"proc": proc, "label": "jia", "kind": "study",
                                  "since": "12:00:00", "log": Path("nope.log"),
                                  "detail": "--max-sections 3"}
        try:
            body = self.client.post("/api/browser/start", json={"label": "jia"}).json()
        finally:
            panel_app._JOBS.pop("jia", None)
        self.assertFalse(body["ok"])
        self.assertIn("任务在跑", body["error"])

    # ---------------------------------------------------------------- 会话检查

    def _run_check(self, **result) -> dict:
        done = mock.Mock(returncode=0, **result)
        with mock.patch.object(panel_app.subprocess, "run", return_value=done) as run:
            body = self.client.post("/api/session/check", json={"label": "jia"}).json()
        self.run_call = run
        return body

    def test_session_check_parses_last_json_line(self):
        payload = {"ok": True, "logged_in": True, "url": "http://example/main",
                   "backend": "cdp", "ticket": True}
        body = self._run_check(stdout="[OK] 已登录\n" + json.dumps(payload) + "\n",
                               stderr="")
        self.assertTrue(body["ok"])
        self.assertEqual(body["message"], "已登录")
        self.assertEqual(body["session"]["url"], payload["url"])
        argv = self.run_call.call_args.args[0]
        self.assertEqual(argv[argv.index("--port") + 1], "9222")

    def test_session_check_reports_not_logged_in(self):
        payload = {"ok": True, "logged_in": False, "url": "http://example/login"}
        body = self._run_check(stdout=json.dumps(payload), stderr="[WARN] 未登录")
        self.assertTrue(body["ok"])
        self.assertIn("未登录", body["message"])

    def test_session_check_without_json_returns_tail(self):
        body = self._run_check(stdout="", stderr="[FAIL] 连不上 CDP http://127.0.0.1:9222")
        self.assertFalse(body["ok"])
        self.assertIn("连不上 CDP", body["error"])

    def test_session_check_timeout_is_reported(self):
        with mock.patch.object(panel_app.subprocess, "run",
                               side_effect=panel_app.subprocess.TimeoutExpired("x", 90)):
            body = self.client.post("/api/session/check", json={"label": "jia"}).json()
        self.assertFalse(body["ok"])
        self.assertIn("超时", body["error"])

    def test_session_check_rejects_unknown_label(self):
        r = self.client.post("/api/session/check", json={"label": "../etc"})
        self.assertEqual(r.status_code, 400)

    # ---------------------------------------------------------------- 恢复登录

    def _write_state(self, label: str = "jia") -> Path:
        p = self.root / "data" / f"storage_state_gxks_{label}.json"
        p.write_text(json.dumps({"cookies": [
            {"name": ".ASPXAUTH", "value": "SECRET", "domain": "www.gaoxiaokaoshi.com",
             "path": "/", "expires": -1}], "origins": []}), encoding="utf-8")
        return p

    def test_restore_without_snapshot_is_refused(self):
        body = self.client.post("/api/session/restore", json={"label": "jia"}).json()
        self.assertFalse(body["ok"])
        self.assertIn("快照", body["error"])

    def test_restore_injects_and_reports_logged_in(self):
        self._write_state()
        payload = {"ok": True, "logged_in": True, "restored": 4,
                   "url": "http://example/main", "state": {"saved": True}}
        done = mock.Mock(returncode=0, stdout=json.dumps(payload), stderr="")
        with mock.patch.object(panel_app.subprocess, "run", return_value=done) as run:
            body = self.client.post("/api/session/restore", json={"label": "jia"}).json()
        self.assertTrue(body["ok"])
        self.assertIn("恢复登录", body["message"])
        self.assertIn("--restore", run.call_args.args[0])

    def test_restore_reports_ticket_rejected(self):
        self._write_state()
        payload = {"ok": True, "logged_in": False, "restored": 4}
        done = mock.Mock(returncode=0, stdout=json.dumps(payload), stderr="")
        with mock.patch.object(panel_app.subprocess, "run", return_value=done):
            body = self.client.post("/api/session/restore", json={"label": "jia"}).json()
        self.assertTrue(body["ok"])
        self.assertIn("不认", body["message"])

    def test_restore_when_already_logged_in(self):
        """本来就登着时脚本**不会走注入**（restored=0），文案不能谎称“已恢复”。"""
        self._write_state()
        payload = {"ok": True, "logged_in": True, "restored": 0}
        done = mock.Mock(returncode=0, stdout=json.dumps(payload), stderr="")
        with mock.patch.object(panel_app.subprocess, "run", return_value=done):
            body = self.client.post("/api/session/restore", json={"label": "jia"}).json()
        self.assertTrue(body["ok"])
        self.assertIn("本来就登录着", body["message"])
        self.assertNotIn("已恢复登录", body["message"])

    # ---------------------------------------------------------------- 重新登录

    def test_relogin_needs_credentials(self):
        """没配账号密码时门都不开（不白跑一次登录）。"""
        (self.root / "data" / "account.local.json").unlink(missing_ok=True)
        body = self.client.post("/api/session/relogin", json={"label": "jia"}).json()
        self.assertFalse(body["ok"])
        self.assertIn("账号密码", body["error"])

    def test_relogin_success(self):
        (self.root / "data" / "account.local.json").write_text(
            json.dumps({"name": "s1001", "pw": "pw"}), encoding="utf-8")
        payload = {"ok": True, "logged_in": True, "relogin": {"result": "ok"},
                   "url": "http://example/main"}
        done = mock.Mock(returncode=0, stdout=json.dumps(payload), stderr="")
        with mock.patch.object(panel_app.subprocess, "run", return_value=done) as run:
            body = self.client.post("/api/session/relogin", json={"label": "jia"}).json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["message"], "已重新登录")
        self.assertIn("--relogin", run.call_args.args[0])

    def test_relogin_verify_code_stops_and_sends_human(self):
        (self.root / "data" / "account.local.json").write_text(
            json.dumps({"name": "s1001", "pw": "pw"}), encoding="utf-8")
        payload = {"ok": True, "logged_in": False, "need_human": True,
                   "relogin": {"result": "need_verify"}}
        done = mock.Mock(returncode=3, stdout=json.dumps(payload), stderr="[WARN] 验证码")
        with mock.patch.object(panel_app.subprocess, "run", return_value=done) as run:
            body = self.client.post("/api/session/relogin", json={"label": "jia"}).json()
        self.assertFalse(body["ok"])
        self.assertTrue(body["need_human"])
        self.assertIn("验证码", body["error"])
        argv = run.call_args.args[0]
        self.assertEqual(argv[argv.index("--port") + 1], "9222")   # 端口按账号表，不猜






class TestChromeStartLogins(unittest.TestCase):
    """`_chrome_start_message` 的登录策略（2026-11-03 用户问「为何不能自动登录」）。

    三层：① 注入快照；② 快照票过期 → 自动用账号密码重登；③ 验证码 → 转人工。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "data").mkdir(parents=True)
        self.settings = Settings(root=self.root)
        self.acc = mock.Mock(label="jia", port=9222)

    def tearDown(self):
        self.tmp.cleanup()

    def _snapshot(self, saved=True):
        p = self.root / "data" / "storage_state_gxks_jia.json"
        p.write_text(json.dumps({"cookies": [{"name": ".ASPXAUTH", "value": "X",
                                              "domain": "www.gaoxiaokaoshi.com",
                                              "path": "/", "expires": -1}]}),
                     encoding="utf-8")
        return saved

    def _cred(self, configured=True):
        p = self.root / "data" / "account.local.json"
        if configured:
            p.write_text(json.dumps({"name": "s1", "pw": "p"}), encoding="utf-8")
        else:
            p.unlink(missing_ok=True)

    def _run(self, results: dict[str, dict]) -> tuple[str, list[str]]:
        """`results` 按 flag（--restore / --relogin）给回包。"""
        calls: list[str] = []

        def _run_session(_acc, *flags, **_kw):
            flag = flags[0] if flags else ""
            calls.append(flag)
            return {"ok": True, "parsed": results.get(flag, {})}

        msg = panel_app._chrome_start_message(self.settings, self.acc, 9222, _run_session)
        return msg, calls

    def test_restore_works_no_relogin(self):
        self._snapshot()
        self._cred()
        msg, calls = self._run({"--restore": {"logged_in": True, "restored": 9}})
        self.assertIn("已注入登录态（9 条 cookie）", msg)
        self.assertIn("可以直接刷课", msg)
        self.assertEqual(calls, ["--restore"], "注入成功就不该再跑重登")

    def test_already_logged_in_does_not_say_injected_zero(self):
        """`restored=0` = 本来就登着（脚本没走注入）—— 不能说「注入 0 条 cookie」。"""
        self._snapshot()
        self._cred()
        msg, _ = self._run({"--restore": {"logged_in": True, "restored": 0}})
        self.assertIn("已是登录状态", msg)
        self.assertNotIn("0 条 cookie", msg)

    def test_expired_ticket_falls_back_to_password(self):
        """快照票过期是常态（cookie 是 session 级）→ 必须自动重登。"""
        self._snapshot()
        self._cred()
        msg, calls = self._run({
            "--restore": {"logged_in": False, "restored": 9},
            "--relogin": {"logged_in": True, "relogin": {"result": "ok"}},
        })
        self.assertIn("快照票已失效", msg)
        self.assertIn("账号密码登录成功", msg)
        self.assertEqual(calls, ["--restore", "--relogin"])

    def test_verify_code_sends_human(self):
        self._snapshot()
        self._cred()
        msg, _ = self._run({
            "--restore": {"logged_in": False, "restored": 9},
            "--relogin": {"logged_in": False, "need_human": True,
                          "relogin": {"result": "need_verify"}},
        })
        self.assertIn("验证码", msg)
        self.assertIn("手动登录", msg)

    def test_no_snapshot_goes_straight_to_password(self):
        self._cred()
        msg, calls = self._run({
            "--relogin": {"logged_in": True, "relogin": {"result": "ok"}},
        })
        self.assertIn("账号密码登录成功", msg)
        self.assertEqual(calls, ["--relogin"], "没有快照就别白跑注入")

    def test_no_snapshot_no_cred_asks_manual(self):
        self._cred(configured=False)
        msg, calls = self._run({})
        self.assertIn("手动登录", msg)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
