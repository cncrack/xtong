"""pid 归属校验（2026-10-27 盘点 B）：pid 复用不得导致“误判在跑”或“误杀无关进程”。

背景：原来只凭 `OpenProcess` 成功就当作自己的会话/守护；Windows 会复用 pid，
重复利用的空档里会把陌生人进程认成自己的，界面误报在跑、停止时直接 taskkill /F。
现在的判据是“进程创建时间不晚于标记文件 mtime”。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from xxt.core.runmark import (busy_sessions, live_session, marker_path,
                              marker_process_alive, pid_alive, pid_create_time,
                              pid_owns_file, write_marker)


class TestPidOwnership(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_create_time_of_self_is_sane(self):
        t = pid_create_time(os.getpid())
        self.assertGreater(t, 0.0)
        self.assertLess(t, time.time() + 60)  # 不可能在未来

    def test_normal_marker_counts_as_alive(self):
        write_marker(self.data, 1, "测试课", pid=os.getpid())
        p = marker_path(self.data, 1)
        self.assertTrue(pid_owns_file(p, os.getpid()))
        self.assertTrue(marker_process_alive(self.data, 1))
        self.assertIsNotNone(live_session(self.data, 1))

    def test_reused_pid_is_not_owner(self):
        """标记写在一小时前 = 现在的同号进程是复用的 → 不得判活（否则会误杀）。"""
        write_marker(self.data, 1, "测试课", pid=os.getpid())
        p = marker_path(self.data, 1)
        old = time.time() - 3600
        os.utime(p, (old, old))
        self.assertTrue(pid_alive(os.getpid()))  # 进程本身确实活着
        self.assertFalse(pid_owns_file(p, os.getpid()))
        self.assertFalse(marker_process_alive(self.data, 1))
        self.assertIsNone(live_session(self.data, 1))

    def test_dead_pid(self):
        self.assertFalse(pid_alive(999999))
        write_marker(self.data, 2, "测试课", pid=999999)
        self.assertFalse(marker_process_alive(self.data, 2))

    def test_missing_marker(self):
        self.assertFalse(marker_process_alive(self.data, 3))


class TestBusySessionsSelf(unittest.TestCase):
    """防双开不得把“自己”当“别人”（2026-10-27 实测：mlearn 一启动就自杀）。

    面板 `/api/study/start` 先 spawn 并**替子进程**写标记（pid=子进程）防空窗双击；
    子进程启动后自查读到这条标记，pid 活且归属成立 → 旧逻辑判成“已有会话在跑”。
    """

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

    def test_own_pid_is_not_busy(self):
        write_marker(self.data, 2, "测试课", pid=os.getpid())
        self.assertTrue(marker_process_alive(self.data, 2))  # 标记本身有效
        self.assertIsNotNone(live_session(self.data, 2))     # 面板仍要看得到会话
        self.assertEqual(busy_sessions(self.data, [2]), {})  # 自己不算双开

    def test_parent_pid_is_not_busy(self):
        """venv 重定向器：面板 spawn 拿到的是 shim pid，真进程的 ppid 才是它。"""
        write_marker(self.data, 2, "测试课", pid=os.getppid())
        self.assertEqual(busy_sessions(self.data, [2]), {})

    def test_other_process_is_busy(self):
        pid = self._sleepy()
        write_marker(self.data, 2, "测试课", pid=pid)
        self.assertEqual(busy_sessions(self.data, [2]), {2: live_session(self.data, 2)})
        self.assertIn(2, busy_sessions(self.data, [1, 2]))


if __name__ == "__main__":
    unittest.main()
