"""账号级会话锁（A1，2026-11-07）：同账号不得双开。

## 背景

`PLAN-GXKS.md` §7.3 硬约束第 1 条「一账号一进程一会话」欠了一个多月。到 2026-11-07
为止防双开只有面板内存表 `_JOBS`，挡不住：

- 另一个面板（`E:\\xxt` 那个 worktree 换个端口就能起）；
- 手敲 `scripts/run_gxks.py --label 同一号`；
- 面板重启后旧任务还活着、认领失败。

同账号两路心跳是最强的机器指纹。

## 守什么

① 别人持有 → 拒；② 自己人（自己/父进程）持有 → 放行且不重写；
③ 进程已死的陈旧标记 → 可接管；④ 不同 label 互不影响；
⑤ 释放要校验归属（不能把别人刚抢到的锁删掉）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from xxt.adapters.gxks import locks as glocks


class TestLockBasics(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.procs: list[subprocess.Popen] = []

    def tearDown(self):
        for p in self.procs:
            p.kill()
            p.wait(timeout=10)
        self.tmp.cleanup()

    def _sleepy(self) -> int:
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.procs.append(p)
        return p.pid

    def test_lock_path_is_per_label(self):
        """一账号一文件：两个账号绝不能共用一个锁文件（共用 = 互相挡）。"""
        a = glocks.lock_path(self.data, "jia")
        b = glocks.lock_path(self.data, "yi")
        self.assertNotEqual(a, b)
        self.assertEqual(a.name, "gxks_jia.json")
        self.assertEqual(a.parent.name, "run")

    def test_invalid_label_rejected(self):
        """非法代号不得回落成某个默认目录（否则两个账号共用一份锁）。"""
        for bad in ("", "甲", "../x", "A B"):
            with self.assertRaises(ValueError):
                glocks.lock_path(self.data, bad)

    def test_no_lock_means_free(self):
        self.assertIsNone(glocks.live(self.data, "jia"))
        self.assertIsNone(glocks.holder(self.data, "jia"))

    def test_acquire_then_holder_sees_other(self):
        """换一个"别的进程"的 pid 写进锁 → 本进程应判成"别人占着"。"""
        pid = self._sleepy()
        glocks.acquire(self.data, "jia", pid=pid)
        info = glocks.holder(self.data, "jia")
        self.assertIsNotNone(info, "别的活进程持有 → 必须判成被占")
        self.assertEqual(info["pid"], pid)
        ok, cur = glocks.acquire(self.data, "jia")
        self.assertFalse(ok)
        self.assertEqual(cur["pid"], pid)

    def test_self_pid_is_not_busy(self):
        """面板替子进程写标记后，子进程自查必须认「自己人」（超星侧 2026-10-27 的坑）。"""
        glocks.acquire(self.data, "jia", pid=os.getpid())
        self.assertIsNotNone(glocks.live(self.data, "jia"))   # 标记本身有效
        self.assertIsNone(glocks.holder(self.data, "jia"))    # 但不算别人占

    def test_parent_pid_is_not_busy(self):
        """venv 重定向器：面板 spawn 拿到的是 shim pid，真进程的 ppid 才是它。"""
        glocks.acquire(self.data, "jia", pid=os.getppid())
        self.assertIsNone(glocks.holder(self.data, "jia"))

    def test_self_acquire_does_not_rewrite(self):
        """自己人持有时不重写文件：面板收尾要按它 spawn 时的 pid 释放。"""
        glocks.acquire(self.data, "jia", pid=os.getppid())
        before = glocks.lock_path(self.data, "jia").read_text(encoding="utf-8")
        ok, cur = glocks.acquire(self.data, "jia", pid=os.getpid())
        self.assertTrue(ok)
        self.assertIsNone(cur)
        after = glocks.lock_path(self.data, "jia").read_text(encoding="utf-8")
        self.assertEqual(before, after)

    def test_dead_pid_lock_can_be_taken_over(self):
        """进程死了留下的陈旧标记必须能接管 —— 否则一次崩溃就永久锁死该账号。"""
        glocks.acquire(self.data, "jia", pid=999999)
        self.assertIsNone(glocks.live(self.data, "jia"))
        ok, cur = glocks.acquire(self.data, "jia", pid=os.getpid())
        self.assertTrue(ok)
        self.assertIsNone(cur)
        self.assertEqual(glocks.read_lock(self.data, "jia")["pid"], os.getpid())

    def test_reused_pid_is_not_owner(self):
        """pid 复用：标记写在一小时前，同号进程现在是复用的 → 不得判活。

        判活错的后果不是"少拦一次"，而是**停止时 taskkill 掉一个无关进程**。
        """
        glocks.acquire(self.data, "jia", pid=os.getpid())
        p = glocks.lock_path(self.data, "jia")
        old = time.time() - 3600
        os.utime(p, (old, old))
        self.assertIsNone(glocks.live(self.data, "jia"))
        self.assertIsNone(glocks.holder(self.data, "jia"))

    def test_labels_are_independent(self):
        """一个账号在跑不影响另一个账号 —— 多账号同时刷的前提。"""
        pid = self._sleepy()
        glocks.acquire(self.data, "jia", pid=pid)
        self.assertIsNotNone(glocks.holder(self.data, "jia"))
        self.assertIsNone(glocks.holder(self.data, "yi"))
        ok, _ = glocks.acquire(self.data, "yi", pid=os.getpid())
        self.assertTrue(ok)

    def test_release_without_pid_always_removes(self):
        """脚本自己的收尾走无参 release（它写的就是自己的 pid）。"""
        glocks.acquire(self.data, "jia", pid=os.getpid())
        self.assertTrue(glocks.release(self.data, "jia"))
        self.assertIsNone(glocks.live(self.data, "jia"))
        self.assertFalse(glocks.release(self.data, "jia"))   # 已经没了

    def test_release_with_pid_checks_ownership(self):
        """按 pid 释放时必须校验：不能把**别人刚抢到**的锁删掉（那会放行双开）。"""
        other = self._sleepy()
        glocks.acquire(self.data, "jia", pid=other)
        self.assertFalse(glocks.release(self.data, "jia", pid=os.getpid() + 1),
                         "pid 对不上就不该删")
        self.assertIsNotNone(glocks.holder(self.data, "jia"))
        self.assertTrue(glocks.release(self.data, "jia", pid=other))
        self.assertIsNone(glocks.live(self.data, "jia"))

    def test_corrupt_lock_file_is_treated_as_free(self):
        """坏文件不能永久锁死账号（与 runmark 的宽容口径一致）。"""
        p = glocks.lock_path(self.data, "jia")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{not json", encoding="utf-8")
        self.assertIsNone(glocks.live(self.data, "jia"))
        ok, _ = glocks.acquire(self.data, "jia", pid=os.getpid())
        self.assertTrue(ok)

    def test_describe_has_no_credentials(self):
        """给人看的那句话只含 pid/时刻/课程，绝不带凭据。"""
        glocks.acquire(self.data, "jia", course="32", pid=os.getpid())
        text = glocks.describe(glocks.read_lock(self.data, "jia") or {})
        self.assertIn("pid", text)
        self.assertIn("32", text)


class TestPanelLockIntegration(unittest.TestCase):
    """面板侧：`_JOBS` 只能挡本进程，落盘锁才能挡「另一个面板 / 命令行」。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / "data").mkdir(parents=True)
        (self.root / "data" / "accounts.local.json").write_text(
            json.dumps({"accounts": [
                {"label": "jia", "port": 9222},
                {"label": "yi", "port": 9223},
            ]}, ensure_ascii=False), encoding="utf-8")
        self._old_root = os.environ.get("XXT_ROOT")
        os.environ["XXT_ROOT"] = str(self.root)
        self.data = self.root / "data"
        self.procs: list[subprocess.Popen] = []

        from xxt.config import Settings
        from xxt.panel_gxks import app as panel_app

        self.panel_app = panel_app
        self.settings = Settings(root=self.root)
        panel_app._JOBS.clear()
        panel_app._BROWSERS.clear()
        self._patches = [
            mock.patch.object(panel_app.gchrome, "port_alive", return_value=True),
            mock.patch.object(panel_app.gchrome, "ports_alive",
                              return_value={9222: True, 9223: True}),
            mock.patch.object(panel_app.gchrome, "find_chrome", return_value=None),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        for proc in self.procs:
            proc.kill()
            proc.wait(timeout=10)
        self.panel_app._JOBS.clear()
        self.panel_app._BROWSERS.clear()
        if self._old_root is None:
            os.environ.pop("XXT_ROOT", None)
        else:
            os.environ["XXT_ROOT"] = self._old_root
        self.tmp.cleanup()

    def _sleepy(self) -> int:
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.procs.append(p)
        return p.pid

    def test_study_refused_when_another_entry_holds_lock(self):
        """另一个面板 / 命令行在跑 → 面板点「学习」必须拒绝，且**不起子进程**。"""
        glocks.acquire(self.data, "jia", pid=self._sleepy())
        with mock.patch.object(self.panel_app, "_spawn_script") as spawn:
            got = self.panel_app._study_one(self.settings, "jia", 1)
        spawn.assert_not_called()
        self.assertFalse(got["ok"])
        self.assertTrue(got.get("skipped"))
        self.assertTrue(got.get("locked"))
        self.assertIn("已有会话在跑", got["error"])

    def test_study_still_works_for_other_account(self):
        """一个账号被占不影响另一个 —— 多账号同时刷的前提。"""
        glocks.acquire(self.data, "jia", pid=self._sleepy())
        with mock.patch.object(self.panel_app, "_spawn_script") as spawn:
            spawn.return_value = mock.Mock(pid=os.getpid())
            got = self.panel_app._study_one(self.settings, "yi", 1)
        self.assertTrue(got["ok"], got)
        spawn.assert_called_once()

    def test_panel_writes_lock_for_child(self):
        """面板 spawn 后必须**替子进程写锁**，否则脚本自己也不知道跑起来了。"""
        with mock.patch.object(self.panel_app, "_spawn_script") as spawn:
            spawn.return_value = mock.Mock(pid=os.getpid())
            self.panel_app._study_one(self.settings, "jia", 1)
        info = glocks.read_lock(self.data, "jia")
        self.assertIsNotNone(info, "面板起完任务却没写锁 → 另一个面板能双开")
        self.assertEqual(info["pid"], os.getpid())

    def test_stop_releases_lock(self):
        """停止要清锁，否则该账号在面板重启前一直被判成「在跑」。"""
        glocks.acquire(self.data, "jia", pid=os.getpid())
        job = mock.Mock(pid=os.getpid())
        job.poll.return_value = None                     # 活着
        self.panel_app._JOBS["jia|study"] = {
            "proc": job, "label": "jia", "kind": "study", "since": "12:00:00",
            "log": Path(self.root / "nope.log"), "detail": "",
            "data_dir": str(self.data)}
        with mock.patch.object(self.panel_app, "_kill", return_value="已停止"):
            got = self.panel_app._stop_one(self.settings, "jia")
        self.assertTrue(got["ok"])
        self.assertIsNone(glocks.live(self.data, "jia"))

    def test_stop_does_not_kill_unknown_lock_holder(self):
        """锁被别的入口持有 → 面板**不去杀**（会误杀），但要说清楚。"""
        glocks.acquire(self.data, "jia", pid=self._sleepy())
        got = self.panel_app._stop_one(self.settings, "jia")
        self.assertFalse(got["ok"])
        self.assertTrue(got.get("skipped"))
        self.assertTrue(got.get("locked"))
        self.assertIn("不是本面板发起", got["error"])


class TestScriptLockIntegration(unittest.TestCase):
    """`run_gxks.py` 必须在**访问平台之前**就因锁而退出（最小访问原则）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # ⚠ 锁在 `Settings(root=<项目根>).data_dir` = `<根>/data`，不是根目录本身。
        # 传根目录会让测试写一个锁、脚本读另一个 → 假绿（脚本照旧去连了 CDP）。
        self.root = Path(self.tmp.name).resolve()
        self.data = self.root / "data"
        self.data.mkdir(parents=True)
        self.procs: list[subprocess.Popen] = []

    def tearDown(self):
        for p in self.procs:
            p.kill()
            p.wait(timeout=10)
        self.tmp.cleanup()

    def _sleepy(self) -> int:
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.procs.append(p)
        return p.pid

    def test_run_gxks_exits_5_when_other_holds_lock(self):
        """别的活进程持有锁 → 秒退返回 5，且**不去连浏览器/不访问平台**。

        用 `--no-netcheck` 把不确定性隔开，断言只看"是否因锁退出"。
        """
        pid = self._sleepy()
        glocks.acquire(self.data, "jia", pid=pid)
        env = dict(os.environ, XXT_ROOT=str(self.root), PYTHONIOENCODING="utf-8")
        root = Path(__file__).resolve().parents[1]
        done = subprocess.run(
            [sys.executable, str(root / "scripts" / "run_gxks.py"),
             "--label", "jia", "--account-label", "jia", "--no-netcheck"],
            cwd=str(root), env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=120)
        self.assertEqual(done.returncode, 5, done.stderr[-800:])
        self.assertIn("已有会话在跑", done.stdout)
        # 关键：没走到连浏览器那一步（否则会报 CDP 连不上 = 访问过本机端口）
        self.assertNotIn("连不上 CDP", done.stdout)

    def test_run_gxks_does_not_leave_lock_when_netcheck_fails(self):
        """异常退出必须清锁，否则该账号被永久锁死（一次网络抖动就废掉一个号）。

        在**进程内**调 `main()`：网络自检打桩成失败，断言返回码 4 且锁不残留。
        不走子进程 —— 那样会真的请求一次站点（测试不该访问平台）。
        """
        import importlib.util
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location(
            "run_gxks_under_test", root / "scripts" / "run_gxks.py")
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)

        old_argv = sys.argv
        old_root = os.environ.get("XXT_ROOT")
        os.environ["XXT_ROOT"] = str(self.root)
        sys.argv = ["run_gxks.py", "--label", "jia", "--account-label", "jia"]
        try:
            with mock.patch.object(mod.netcheck, "preflight",
                                   return_value={"ok": False, "secs": 9.9,
                                                 "content_ok": False, "advice": "",
                                                 "url": "http://example.invalid",
                                                 "threshold_secs": 3.0}), \
                 mock.patch.object(mod.netcheck, "format_report", return_value=""):
                code = mod.main()
        finally:
            sys.argv = old_argv
            if old_root is None:
                os.environ.pop("XXT_ROOT", None)
            else:
                os.environ["XXT_ROOT"] = old_root

        self.assertEqual(code, 4)
        self.assertIsNone(glocks.live(self.data, "jia"),
                          "退出后不得留下锁（否则该账号被永久判成双开）")


if __name__ == "__main__":
    unittest.main()
