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


def pid_create_time(pid: int) -> float:
    """进程创建时间（epoch 秒）；取不到返回 0.0。

    为什么需要：pid 会被系统复用，仅凭 `pid_alive` 成功就把陌生进程当成自己的
    会话/守护 → 界面误报“在跑”，停止时更会 taskkill 掉无关进程（2026-10-27 盘点 B）。
    """
    if pid <= 0:
        return 0.0
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class _FileTime(ctypes.Structure):
                _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

            k32 = ctypes.windll.kernel32
            # QUERY_INFORMATION | LIMITED：GetProcessTimes 需要前者
            h = k32.OpenProcess(0x0400 | 0x1000, False, pid)
            if not h:
                return 0.0
            try:
                c, e, k, u = _FileTime(), _FileTime(), _FileTime(), _FileTime()
                if not k32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e),
                                           ctypes.byref(k), ctypes.byref(u)):
                    return 0.0
                ticks = (c.high << 32) | c.low  # 1601-01-01 起的 100ns 数
                return ticks / 1e7 - 11644473600.0
            finally:
                k32.CloseHandle(h)
        except Exception:
            return 0.0
    try:  # Linux：/proc/<pid>/stat 第 22 字段（开机后 tick）+ /proc/stat btime
        with open(f"/proc/{pid}/stat", "rb") as f:
            raw = f.read()
        fields = raw[raw.rfind(b")") + 2:].split()  # 跳过含空格的 comm
        ticks = float(fields[19])  # fields[0]=第3字段(state) → 第22字段=fields[19]
        with open("/proc/stat", "rb") as f:
            for line in f:
                if line.startswith(b"btime "):
                    return float(line.split()[1]) + ticks / os.sysconf("SC_CLK_TCK")
    except Exception:
        pass
    return 0.0


def pid_owns_file(path: Path, pid: int) -> bool:
    """标记文件里的 pid 是否**就是**写下这条标记的那个进程。

    正常流程是“先 spawn 后写标记”，故进程创建时间必然不晚于文件 mtime；
    pid 被复用时新进程创建时间会晚于 mtime → 判否，调用方不得据此强杀。
    取不到创建时间时返回 True（宁可误拒启动，不可放过双开）。
    """
    created = pid_create_time(pid)
    if created <= 0:
        return True
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return True
    return created <= mtime + 5  # 5 秒容忍：时间戳精度 / 时钟抖动


def marker_process_alive(data_dir: Path, account_id: int) -> bool:
    """会话标记指向的进程是否真实存活（含 pid 归属校验）。"""
    info = read_marker(data_dir, account_id)
    if not info:
        return False
    pid = int(info.get("pid") or 0)
    return pid_alive(pid) and pid_owns_file(marker_path(data_dir, account_id), pid)


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
    if info and marker_process_alive(data_dir, int(account_id)):
        return info
    return None


def clear_marker(data_dir: Path, account_id: int) -> None:
    marker_path(data_dir, account_id).unlink(missing_ok=True)


def busy_sessions(data_dir: Path, account_ids) -> dict[int, dict[str, Any]]:
    """传入的账号里，哪些已有**活着的**会话 → {account_id: 标记内容}。

    启动前统一查这一下（面板与 CLI 共用），防同账号双开。

    2026-10-27 修：面板 `/api/study/start` 是“先 spawn、立刻写标记（pid=proc.pid）”的，
    子进程（mlearn）启动后自查时会读到这条**自己**的标记，`pid_alive`/`pid_owns_file`
    全部成立 → 被判成“账号已有会话在跑”而 [FAIL] 秒退（守护随后记 auto-error，
    表现为“定时触发了但学不了”）。

    判据要包括 ppid：`.venv/Scripts/python.exe` 是 venv 的重定向器，`Popen` 得到的
    只是 shim 的 pid，真正跑代码的是它再拉起的孙进程（实测 shim 5908 / 真进程 22640），
    而孙进程的 ppid 正是那个 shim——所以“标记 pid == 自 pid 或父 pid”都是自己人。
    """
    me, parent = os.getpid(), os.getppid()
    out: dict[int, dict[str, Any]] = {}
    for aid in account_ids:
        info = live_session(data_dir, int(aid))
        if not info:
            continue
        if int(info.get("pid") or 0) in (me, parent):
            continue
        out[int(aid)] = info
    return out
