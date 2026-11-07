"""面板单实例守卫 + 页面指纹（2026-11-08）。

## 事故（用户连续三轮反馈「改了没效果」）

同一台机器上出现过 **两个 uvicorn worker 同时监听 8301**：

```
26532 (22:32 启动，旧代码) ─┐
24604 (00:41 启动，新代码) ─┴─ 都 bind 127.0.0.1:8301
```

Windows 下 `SO_REUSEADDR` 允许多个 socket 绑同一地址，请求落到哪个 worker
不确定 → 用户看到的一直是**旧页面**：

- 「复选框旁边的小点还在」（`.c-pick` 修复没部署过去）
- 「日志还是 4 秒刷新」（`LOG_ANCHOR` 修复没部署过去）
- 「日志看不出是谁的」（`runlog-who` 修复没部署过去）

而磁盘上这些修复**全都在**、pytest 全绿 —— 查了三轮才意识到是"没部署到他连的进程"。
实测：直接请求 8301 拿到的 HTML 里 `c-pick=False / runlog-who=False / LOG_ANCHOR=False`，
但 `sections_per_run=True / start-and-study=True`（更早那批改动），
说明连的是 22:32 那个旧 worker。

## 守什么

1. `_port_in_use()` 是**纯探测**（`connect_ex`，不 bind、不留端口占用）；
2. `serve()` 在 `uvicorn.run` **之前**检查，端口已占用就 `return 1` 并打印可照做的提示，
   **绝不静默绑上去**；
3. `_page_fingerprint()` 打印内存里 `PAGE` 的长度 + 关键串命中情况 ——
   "磁盘新 vs 进程旧"这类问题以后看启动日志 3 秒就能定；
4. 第一个正常实例（端口空闲）行为**完全不变**。
"""

from __future__ import annotations

import inspect
import unittest
from unittest import mock

from xxt.panel_gxks import app as A


class TestPortProbeIsReadOnly(unittest.TestCase):
    def test_uses_connect_ex_not_bind(self):
        """必须只读探测：不能 `bind`（那会真的占用端口、影响第一个实例）。"""
        src = inspect.getsource(A._port_in_use)
        self.assertIn("connect_ex", src)
        self.assertNotIn(".bind(", src)
        self.assertIn("finally", src, "socket 必须关闭")
        self.assertIn("s.close()", src)

    def test_returns_false_on_socket_error(self):
        """探测本身出错时不能误判成"端口被占"，否则正常启动会被挡。"""
        import socket

        # `socket` 是 `_port_in_use` 里的局部 import，patch 标准库模块才生效
        with mock.patch.object(socket, "socket", side_effect=OSError("boom")):
            self.assertFalse(A._port_in_use("127.0.0.1", 8301))

    def test_detects_listening_port(self):
        """真的有进程在听 → True（用临时端口自建监听验证，测完即关）。"""
        import socket

        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        try:
            self.assertTrue(A._port_in_use("127.0.0.1", port))
        finally:
            srv.close()
        # 关掉后应为 False（端口确实空了）
        self.assertFalse(A._port_in_use("127.0.0.1", port))


class TestServeRefusesSecondInstance(unittest.TestCase):
    def _serve_src(self) -> str:
        return inspect.getsource(A.serve)

    def test_checks_before_uvicorn_run(self):
        """检测必须在 `uvicorn.run` 之前，否则第二个实例已经绑上去了。"""
        src = self._serve_src()
        check = src.find("_port_in_use(host, port)")
        run = src.find("uvicorn.run(")
        self.assertGreater(check, -1, "serve 里没有端口检测")
        self.assertGreater(run, -1)
        self.assertLess(check, run, "检测必须在 uvicorn.run 之前")

    def test_returns_1_when_busy(self):
        """被占用要 return 1（不是 return 0，也不是继续跑）。"""
        src = self._serve_src()
        self.assertIn("return 1", src)
        self.assertIn("已被别的进程占用", src)

    def test_prints_page_fingerprint(self):
        """启动日志要能一眼分辨新旧代码（这是这次排查的关键线索）。"""
        src = self._serve_src()
        self.assertIn("_page_fingerprint()", src)
        fp = A._page_fingerprint()
        self.assertIn("PAGE len=", fp)
        self.assertIn("markers=", fp)

    def test_fingerprint_marks_present_features(self):
        """当前 PAGE 里这些新特性都在 → 指纹应全命中（不是 ----）。"""
        fp = A._page_fingerprint()
        markers = fp.split("markers=", 1)[1]
        self.assertNotIn("-", markers,
                         "PAGE 里少了某个新特性 → 说明磁盘代码被回退了，需查")
        self.assertEqual(len(markers), 4)


if __name__ == "__main__":
    unittest.main()
