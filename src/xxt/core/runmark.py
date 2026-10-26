"""会话运行标记（`data/run/<account_id>.json`）——"同账号只允许一个进程"的唯一凭据。

为什么单独成模块：面板靠它判断账号是否已在跑（跨面板重启仍有效），CLI 也要写它。
2026-10-25 发现 `xxt learn` 完全不写标记，面板因此看不见命令行会话，
用户再点"开始"就会同账号双开（红线）。两边共用一份实现，避免再次不一致。
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any


def marker_path(data_dir: Path, account_id: int) -> Path:
    return data_dir / "run" / f"{account_id}.json"


def pid_alive(pid: int) -> bool:
    """进程是否存活。判不准时偏向"活着"（宁可误拒启动，不可放过双开）。"""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes

            h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if not h:
                return False
            ctypes.windll.kernel32.CloseHandle(h)
            return True
        except Exception:
            return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def write_marker(data_dir: Path, account_id: int, course: str,
                 pid: int | None = None) -> None:
    p = marker_path(data_dir, account_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "pid": pid or os.getpid(),
        "course": course,
        "since": time.strftime("%H:%M"),
    }, ensure_ascii=False), encoding="utf-8")


def read_marker(data_dir: Path, account_id: int) -> dict[str, Any] | None:
    p = marker_path(data_dir, account_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def live_session(data_dir: Path, account_id: int) -> dict[str, Any] | None:
    """标记存在且进程还活着 → 返回标记内容（该账号已有会话在跑）。"""
    info = read_marker(data_dir, account_id)
    if info and pid_alive(int(info.get("pid") or 0)):
        return info
    return None


def clear_marker(data_dir: Path, account_id: int) -> None:
    marker_path(data_dir, account_id).unlink(missing_ok=True)


def busy_sessions(data_dir: Path, account_ids) -> dict[int, dict[str, Any]]:
    """传入的账号里，哪些已有**活着的**会话 → {account_id: 标记内容}。

    启动前统一查这一下（面板与 CLI 共用），防同账号双开。
    """
    out: dict[int, dict[str, Any]] = {}
    for aid in account_ids:
        info = live_session(data_dir, int(aid))
        if info:
            out[int(aid)] = info
    return out
