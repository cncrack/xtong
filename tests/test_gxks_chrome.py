"""本机 Chrome 启动/探活（`adapters/gxks/chrome.py`）的测试（P2）。

这些测试**不碰真 Chrome、不出本机**：探活用临时起的一个 `http.server`，启动用
`Popen` 替身。要验的点只有三个——参数对不对、端口判得准不准、失败会不会给出
可照做的提示（而不是瞎猜一个 chrome.exe 路径）。
"""

from __future__ import annotations

import http.server
import os
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from xxt.adapters.gxks import chrome as gchrome
from xxt.adapters.gxks import selectors


def _free_port() -> int:
    """取一个刚释放的端口：此刻没人监听，用来断言 `port_alive` 为 False。"""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _Quiet(http.server.BaseHTTPRequestHandler):
    def do_GET(self):                                      # noqa: N802  标准库命名
        body = b'{"Browser":"Chrome/156.0.0.0"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):                          # 测试里不要刷屏
        pass


class TestFindChrome(unittest.TestCase):
    def test_env_override_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "chrome.exe"
            fake.write_bytes(b"")
            with mock.patch.dict(os.environ, {"XXT_GXKS_CHROME": str(fake)}):
                self.assertEqual(gchrome.find_chrome(), fake)

    def test_bad_override_returns_none_instead_of_guessing(self):
        # 显式指定了却不存在：宁可返回 None 让人去改，也不要偷偷换成别的 Chrome
        with mock.patch.dict(os.environ, {"XXT_GXKS_CHROME": "D:/nope/chrome.exe"}):
            self.assertIsNone(gchrome.find_chrome())


class TestLaunchArgv(unittest.TestCase):
    def test_port_and_profile_are_account_properties(self):
        argv = gchrome.launch_argv("C:/c/chrome.exe", port=9333, profile="D:/p/yi",
                                   url=selectors.MAIN_PAGE)
        self.assertEqual(argv[0], "C:/c/chrome.exe")
        self.assertIn("--remote-debugging-port=9333", argv)
        self.assertTrue(any(a.startswith("--user-data-dir=") and a.endswith("yi")
                            for a in argv))
        self.assertIn("--disable-blink-features=AutomationControlled", argv)
        # 地址**必须在最后**：Chrome 把裸参数当要打开的页面
        self.assertEqual(argv[-1], selectors.MAIN_PAGE)

    def test_no_url_means_no_trailing_argument(self):
        argv = gchrome.launch_argv("C:/c/chrome.exe", port=9333, profile="D:/p")
        self.assertFalse(any(a.startswith("http") for a in argv))


class TestPortAlive(unittest.TestCase):
    def test_false_when_nothing_listens(self):
        self.assertFalse(gchrome.port_alive(_free_port(), timeout=0.5))

    def test_true_when_debug_endpoint_answers(self):
        server = http.server.HTTPServer(("127.0.0.1", 0), _Quiet)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            self.assertTrue(gchrome.port_alive(server.server_port, timeout=2))
        finally:
            server.shutdown()
            server.server_close()

    def test_wait_alive_gives_up_quickly(self):
        self.assertFalse(gchrome.wait_alive(_free_port(), seconds=0, interval=0.01))


class TestStart(unittest.TestCase):
    def test_creates_profile_dir_and_detaches(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp) / "profiles" / "jia"
            proc = mock.Mock(pid=4242)
            with mock.patch.object(gchrome.subprocess, "Popen",
                                   return_value=proc) as popen, \
                 mock.patch.object(gchrome, "wait_alive", return_value=True) as wa:
                got = gchrome.start("C:/c/chrome.exe", port=9333, profile=profile,
                                    url=selectors.MAIN_PAGE)
            self.assertIs(got, proc)
            self.assertTrue(profile.is_dir())              # 不存在就建：Chrome 不会自己建
            self.assertEqual(wa.call_args.args[0], 9333)
            argv = popen.call_args.args[0]
            self.assertIn("--remote-debugging-port=9333", argv)
            # 面板退出不该带走用户正在登录的窗口（非 Windows 上 flags=0）
            if os.name == "nt":
                self.assertTrue(popen.call_args.kwargs["creationflags"])

    def test_default_start_url_is_main_page(self):
        self.assertEqual(gchrome.default_start_url(), selectors.MAIN_PAGE)


if __name__ == "__main__":
    unittest.main()
