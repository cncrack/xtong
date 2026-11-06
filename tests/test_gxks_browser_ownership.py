"""浏览器归属判定（2026-11-06 用户问「两处停止有什么区别」后加固的那条链）。

## 守什么

「停止浏览器」原来只认**内存**登记表（`_BROWSERS`），面板一重启就忘干净：
自己刚拉起的 Chrome 变成「不是面板起的」，按钮灰着、接口也不敢杀，
只能让用户手动关窗口。改成：

① **落盘登记**（`data/gxks_browsers.json`）→ 重启后仍认得出；
② **现场证据**（`chrome.owner_pid`）→ 用户自己双击开的 Chrome 永远认不出来，绝不动它；
③ **pid 复用守卫**（可执行文件名必须是 chrome）→ 不误杀无关进程。

## ⚠ 第一版方案在这里翻过车（2026-11-06 实测）

原打算用 profile 里的 `DevToolsActivePort` 存 uuid 跟端口 `/json/version` 对撞，
看起来最"不可伪造"。**实机一跑全空** —— 那个文件只在
`--remote-debugging-port=0`（随机端口）时才写，面板用的是显式端口，文件根本不存在。
改成 `lockfile`（profile 单实例锁，mtime ≈ 启动时刻）+ `runmark.pid_owns_file`，
实测 9222 的 owner_pid 正确识别为 33852。

全程替身：不起真 Chrome、不探真端口。
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from xxt.adapters.gxks import chrome as gchrome
from xxt.config import Settings
from xxt.panel_gxks import app as panel_app
from xxt.panel_gxks.app import adopt_orphan_browsers, create_app

FAKE_ACCOUNTS = {
    "accounts": [
        {"label": "jia", "display": "甲同学", "port": 9222},
        {"label": "yi", "display": "乙同学", "port": 9223},
    ]
}

CHROME_EXE = r"C:\Program Files\Google\Chrome\Application\chrome.exe"


class TestChromeOwnerPidEvidence(unittest.TestCase):
    """`owner_pid()` 是三道证据的合取，缺一不可。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.profile = Path(self.tmp.name) / "profile"
        self.profile.mkdir()
        self.lock = self.profile / "lockfile"
        self.lock.write_text("", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_no_listener_is_not_owned(self):
        """端口没人听 = 浏览器没开。"""
        with mock.patch.object(gchrome, "listening_pid", return_value=0):
            self.assertEqual(gchrome.owner_pid(self.profile, 9222), 0)

    def test_listener_not_chrome_is_refused(self):
        """pid 复用的典型形态：端口确实在听，但听的是别的程序。"""
        with mock.patch.object(gchrome, "listening_pid", return_value=4242), \
             mock.patch.object(gchrome, "process_image",
                               return_value="C:/Windows/System32/python.exe"):
            self.assertEqual(gchrome.owner_pid(self.profile, 9222), 0)

    def test_missing_profile_lockfile_is_not_owned(self):
        """profile 里没有 lockfile → 不是这份 profile 的实例（可能压根没起过）。"""
        self.lock.unlink()
        with mock.patch.object(gchrome, "listening_pid", return_value=4242), \
             mock.patch.object(gchrome, "process_image", return_value=CHROME_EXE):
            self.assertEqual(gchrome.owner_pid(self.profile, 9222), 0)

    def test_pid_created_after_profile_is_not_owned(self):
        """pid 是后来才出现的（复用了），晚于 profile 锁 → 不是它占的。"""
        old = time.time() - 3600
        os.utime(self.lock, (old, old))
        with mock.patch.object(gchrome, "listening_pid", return_value=4242), \
             mock.patch.object(gchrome, "process_image", return_value=CHROME_EXE), \
             mock.patch.object(gchrome.runmark, "pid_create_time",
                               return_value=time.time()):
            self.assertEqual(gchrome.owner_pid(self.profile, 9222), 0)

    def test_matching_evidence_returns_listener_pid(self):
        """三道全过：端口在听 + 是 chrome + 它占着这份 profile。"""
        with mock.patch.object(gchrome, "listening_pid", return_value=4242), \
             mock.patch.object(gchrome, "process_image", return_value=CHROME_EXE), \
             mock.patch.object(gchrome.runmark, "pid_create_time",
                               return_value=time.time() - 600):
            self.assertEqual(gchrome.owner_pid(self.profile, 9222), 4242)

    def test_unknown_create_time_still_trusts_lockfile(self):
        """取不到进程创建时间时不得因此拒绝（否则权限受限的机器上全废）。"""
        with mock.patch.object(gchrome, "listening_pid", return_value=4242), \
             mock.patch.object(gchrome, "process_image", return_value=CHROME_EXE), \
             mock.patch.object(gchrome.runmark, "pid_create_time", return_value=0.0):
            self.assertEqual(gchrome.owner_pid(self.profile, 9222), 4242)


class TestBrowserRegistrySurvivesRestart(unittest.TestCase):
    """面板重启后，「自己起的 Chrome」要能认回来（用户实拍的那个坑）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "data").mkdir(parents=True)
        (self.root / "data" / "accounts.local.json").write_text(
            json.dumps(FAKE_ACCOUNTS, ensure_ascii=False), encoding="utf-8")
        # `_safe_label` 读的是**全局**账号表路径（`XXT_ROOT` 决定），
        # 不设这个变量它会去读真实仓库的 data/ → 账号对不上直接 400。
        self._old_root = os.environ.get("XXT_ROOT")
        os.environ["XXT_ROOT"] = str(self.root)
        self.settings = Settings(root=self.root)
        panel_app._BROWSERS.clear()
        self._patch = mock.patch.object(panel_app.gchrome, "port_alive",
                                        return_value=False)
        self._patch.start()

    def tearDown(self):
        mock.patch.stopall()
        panel_app._BROWSERS.clear()
        if self._old_root is None:
            os.environ.pop("XXT_ROOT", None)
        else:
            os.environ["XXT_ROOT"] = self._old_root
        self.tmp.cleanup()

    def _registry(self) -> dict:
        p = self.root / "data" / "gxks_browsers.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    def _start(self, client: TestClient) -> dict:
        with mock.patch.object(panel_app, "_start_chrome_interactive",
                               return_value={"ok": True, "pid": 4242}), \
             mock.patch.object(panel_app, "_chrome_start_message", return_value="ok"), \
             mock.patch.object(panel_app.runmark, "pid_alive", return_value=True), \
             mock.patch.object(panel_app.gchrome, "process_image",
                               return_value=CHROME_EXE):
            return client.post("/api/browser/start", json={"label": "jia"}).json()

    def test_start_writes_registry_and_stop_clears_it(self):
        client = TestClient(create_app(self.settings))
        self.assertTrue(self._start(client)["ok"])
        self.assertEqual(self._registry()["jia"]["pid"], 4242)

        with mock.patch.object(panel_app, "_kill_pid", return_value="已停止"), \
             mock.patch.object(panel_app.runmark, "pid_alive", return_value=True), \
             mock.patch.object(panel_app.gchrome, "process_image",
                               return_value=CHROME_EXE), \
             mock.patch.object(panel_app.gchrome, "owner_pid", return_value=4242):
            stopped = client.post("/api/browser/stop", json={"label": "jia"}).json()
        self.assertTrue(stopped["ok"])
        self.assertEqual(self._registry(), {}, "停完必须清登记，否则下次拿死 pid 去猜")

    def test_adopt_after_restart_recognises_own_chrome(self):
        """模拟「面板重启」：只留落盘登记，内存表清空。"""
        (self.root / "data" / "gxks_browsers.json").write_text(json.dumps({
            "jia": {"pid": 4242, "port": 9222,
                    "profile": str(self.root / "data" / "profiles" / "jia"),
                    "since": "09:13:59"}}), encoding="utf-8")
        # 替身要一直挂着：`create_app` 里也会再认领一次、`/api/browser/state`
        # 还要判 pid 存活，出了 with 块这些就都走真实 Windows 调用（4242 不存在）。
        with mock.patch.object(panel_app.runmark, "pid_alive", return_value=True), \
             mock.patch.object(panel_app.gchrome, "process_image",
                               return_value=CHROME_EXE):
            adopted = adopt_orphan_browsers(self.settings)
            self.assertEqual([a["label"] for a in adopted], ["jia"])
            self.assertEqual(panel_app._BROWSERS["jia"]["pid"], 4242)

            # 重启后的面板：界面应显示「面板启动」，「停止」可用
            client = TestClient(create_app(self.settings))
            state = client.get("/api/browser/state?label=jia").json()
        self.assertTrue(state["started_by_panel"])
        self.assertEqual(state["since"], "09:13:59")

    def test_adopt_falls_back_to_live_owner_when_pid_changed(self):
        """Chrome 自更新重启换了 pid：登记 pid 已死，但现场证据仍指得着新 pid。"""
        (self.root / "data" / "gxks_browsers.json").write_text(json.dumps({
            "jia": {"pid": 1111, "port": 9222, "profile": "/p/jia"}}),
            encoding="utf-8")
        with mock.patch.object(panel_app.runmark, "pid_alive", return_value=False), \
             mock.patch.object(panel_app.gchrome, "owner_pid", return_value=2222):
            adopted = adopt_orphan_browsers(self.settings)
        self.assertEqual(adopted[0]["pid"], 2222)

    def test_adopt_drops_dead_registry(self):
        """Chrome 已经关了 → 登记必须清掉，不能一直谎报「面板启动」。"""
        (self.root / "data" / "gxks_browsers.json").write_text(json.dumps({
            "jia": {"pid": 4242, "port": 9222, "profile": "/p/jia"}}),
            encoding="utf-8")
        with mock.patch.object(panel_app.runmark, "pid_alive", return_value=False), \
             mock.patch.object(panel_app.gchrome, "owner_pid", return_value=0):
            adopted = adopt_orphan_browsers(self.settings)
        self.assertEqual(adopted, [])
        self.assertEqual(self._registry(), {})

    def test_state_does_not_claim_panel_started_when_chrome_gone(self):
        """登记残留但 Chrome 已关：`started_by_panel` 必须为假（用户实拍的那句慌话）。"""
        (self.root / "data" / "gxks_browsers.json").write_text(json.dumps({
            "jia": {"pid": 4242, "port": 9222, "profile": "/p/jia",
                    "since": "09:13:59"}}), encoding="utf-8")
        with mock.patch.object(panel_app.runmark, "pid_alive", return_value=False), \
             mock.patch.object(panel_app.gchrome, "owner_pid", return_value=0), \
             mock.patch.object(panel_app, "adopt_orphan_browsers", return_value=[]):
            client = TestClient(create_app(self.settings))
            state = client.get("/api/browser/state?label=jia").json()
        self.assertFalse(state["started_by_panel"])

    def test_adopt_recognises_unregistered_chrome_by_evidence(self):
        """登记表是后来才加的：**没登记**的旧 Chrome 也要凭现场证据认回来。

        对应用户 2026-11-06 实拍：Chrome 09:13:59 起，面板同刻重启，
        落盘登记里没有它 → 旧实现会一直说「不是面板起的」。
        """
        with mock.patch.object(panel_app.gchrome, "ports_alive",
                               return_value={9222: True, 9223: False}), \
             mock.patch.object(panel_app.gchrome, "owner_pid", return_value=4242), \
             mock.patch.object(panel_app.gbrowser, "cdp_profile",
                               return_value=Path("/p/jia")):
            adopted = adopt_orphan_browsers(self.settings)
        self.assertEqual([a["label"] for a in adopted], ["jia"])
        self.assertEqual(self._registry()["jia"]["pid"], 4242)
        self.assertTrue(self._registry()["jia"]["started_at"])

    def test_adopt_skips_port_that_is_not_listening(self):
        """端口没监听就别探了（探活本身会吃 timeout），也不能凭证据瞎认。"""
        with mock.patch.object(panel_app.gchrome, "ports_alive",
                               return_value={9222: False, 9223: False}), \
             mock.patch.object(panel_app.gchrome, "owner_pid") as owner:
            adopted = adopt_orphan_browsers(self.settings)
        self.assertEqual(adopted, [])
        owner.assert_not_called()

    def test_stop_refuses_when_registry_says_foreign(self):
        """登记表里没这个账号 → 一律不杀（用户自己开的 Chrome 绝不动）。"""
        client = TestClient(create_app(self.settings))
        with mock.patch.object(panel_app, "_kill_pid") as kill:
            body = client.post("/api/browser/stop", json={"label": "jia"}).json()
        self.assertFalse(body["ok"])
        self.assertIn("不是面板起的", body["error"])
        kill.assert_not_called()

    def test_stop_refuses_when_pid_reused_by_other_program(self):
        """登记过、pid 也活着，但镜像不是 chrome → 认不出，不杀。"""
        client = TestClient(create_app(self.settings))
        self._start(client)
        with mock.patch.object(panel_app.runmark, "pid_alive", return_value=True), \
             mock.patch.object(panel_app.gchrome, "process_image",
                               return_value="C:/Windows/System32/notepad.exe"), \
             mock.patch.object(panel_app.gchrome, "owner_pid", return_value=0), \
             mock.patch.object(panel_app, "_kill_pid") as kill:
            body = client.post("/api/browser/stop", json={"label": "jia"}).json()
        self.assertFalse(body["ok"])
        kill.assert_not_called()


class TestBrowserStopGuardsRunningJob(unittest.TestCase):
    """前端在「学习任务跑着」时点「停浏览器」必须先确认（这是两件不同的事）。"""

    def test_page_warns_before_killing_chrome_during_study(self):
        from xxt.panel_gxks.page import PAGE
        # 按钮必须走 stopBrowser()，不能裸 post —— 否则跳过确认直接杀 Chrome
        self.assertIn('onclick="stopBrowser()"', PAGE)
        self.assertNotIn("post('/api/browser/stop', { label: browserLabel() })", PAGE)
        body = PAGE.split("async function stopBrowser()", 1)[1].split("\n}", 1)[0]
        self.assertIn("card.job", body)
        self.assertIn("confirm(", body)

    def test_page_explains_the_difference(self):
        from xxt.panel_gxks.page import PAGE
        self.assertIn("杀 Chrome 进程", PAGE)


if __name__ == "__main__":
    unittest.main()
