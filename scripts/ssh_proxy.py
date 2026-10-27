# -*- coding: utf-8 -*-
"""SSH ProxyCommand 转发器：经本机代理建立到目标端口的 TCP 隧道。

背景：本机 `git push` 走 `ssh.github.com:443`（`~/.ssh/config` 的 `github.com-work`），
TCP 能连上但 SSH 握手超时/被 reset（典型干扰）。本脚本把 SSH 流量塞进本地
HTTP 代理的 CONNECT 隧道，绕开干扰。

只影响当次命令，不改任何全局配置。用法：

    $env:GIT_SSH_COMMAND = "ssh -o ProxyCommand='.venv/Scripts/python.exe scripts/ssh_proxy.py %h %p'"
    git push
    Remove-Item Env:GIT_SSH_COMMAND      # 用完清掉

代理地址取环境变量 `XXT_PROXY`，默认 `127.0.0.1:10808`（同时支持 HTTP CONNECT 与 SOCKS5 的混合入站）。
"""

from __future__ import annotations

import os
import socket
import sys
import threading

DEFAULT_PROXY = "127.0.0.1:10808"


def connect_via_http_proxy(host: str, port: int, proxy: str) -> socket.socket:
    ph, _, pp = proxy.partition(":")
    sock = socket.create_connection((ph, int(pp or 80)), timeout=20)
    sock.sendall(("CONNECT %s:%d HTTP/1.1\r\nHost: %s:%d\r\n\r\n"
                  % (host, port, host, port)).encode())
    # 读到空行为止，首行判断是否 200
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf += chunk
    first = buf.split(b"\r\n", 1)[0].decode("latin1", "replace")
    if " 200 " not in first:
        raise RuntimeError("代理拒绝 CONNECT：%s" % first)
    sock.settimeout(None)
    return sock


def pump(src, dst_write) -> None:
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst_write(data)
    except Exception:
        pass


def pump_file(inp, dst) -> None:
    try:
        while True:
            data = inp.read(65536)
            if not data:
                break
            dst.sendall(data)
    except Exception:
        pass


def main() -> int:
    if len(sys.argv) < 3:
        sys.stderr.write("usage: ssh_proxy.py <host> <port>\n")
        return 2
    host, port = sys.argv[1], int(sys.argv[2])
    proxy = os.environ.get("XXT_PROXY", DEFAULT_PROXY)

    try:
        sock = connect_via_http_proxy(host, port, proxy)
    except Exception as exc:
        sys.stderr.write("ssh_proxy: %s\n" % exc)
        return 1

    out = sys.stdout.buffer

    def write_out(data: bytes) -> None:
        out.write(data)
        out.flush()

    down = threading.Thread(target=pump, args=(sock, write_out), daemon=True)
    down.start()
    pump_file(sys.stdin.buffer, sock)   # 上行：SSH → 隧道
    down.join(timeout=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
