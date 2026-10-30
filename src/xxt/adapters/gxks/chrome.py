"""本机 Chrome 的启动与探活（P2，2026-10-30）。

一账号 = 一个 Chrome 实例 + 一个调试端口 + 一个 user-data-dir（见 `accounts.py`）。
本模块只管两件事：**怎么把它正确地拉起来**、**它现在活着吗**，不碰任何页面。

为什么不复用 Playwright 的 `launch()`：本机形态要的是**人看得见、能手动登录**的窗口，
而且必须复用该账号已有的 `user-data-dir`（登录态和续播 cookie 都在里面）。Playwright
自己起的是干净内核，这两样都拿不到。

⚠ 已知坑：如果该 profile 已经被一个**不带调试端口**的 Chrome 打开（用户自己双击的），
再带 `--remote-debugging-port` 启动只会让老进程开个新标签，**端口不会起来**。
所以 `wait_alive()` 超时后一律提示"先把该账号的 Chrome 全部关掉"，不猜。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from . import selectors

# 与 `core/stealth.py::LAUNCH_ARGS` 保持同一口径：只去掉自动化开关，不动别的
#
# `--no-proxy-server` 是 2026-10-30 实测加的：本机系统代理（v2rayN/xray）开着时，
# 目标站点单次请求要 10 秒以上、真实浏览器甚至超时打不开（直连 0.33 秒），
# 而站点每 60 秒要上报一次学习进度 → **学时白刷**。
# 给这个专用 Chrome 加这个参数让它自己绕过代理，**不碰系统代理设置**（别的软件照常用代理）。
_CHROME_ARGS = [
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-blink-features=AutomationControlled",
    "--no-proxy-server",
]

_REG_KEYS = (
    (r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe", "HKLM"),
    (r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe", "HKCU"),
)


def _candidates() -> list[Path]:
    """常见安装位置（按命中概率排序）。"""
    envs = [os.environ.get(k, "") for k in ("ProgramFiles", "ProgramFiles(x86)", "LocalAppData")]
    out = [Path(e) / "Google" / "Chrome" / "Application" / "chrome.exe" for e in envs if e]
    out.append(Path("C:/Program Files/Google/Chrome/Application/chrome.exe"))
    return out


def find_chrome() -> Path | None:
    """找本机 Chrome：环境变量 > 常见路径 > 注册表 App Paths > PATH。

    `XXT_GXKS_CHROME` 优先，方便装在非标准目录的机器（容器/绿色版）指定。
    找不到就返回 None，由调用方给出可照做的提示 —— 不猜一个路径去撞。
    """
    override = os.environ.get("XXT_GXKS_CHROME", "").strip()
    if override:
        path = Path(override)
        return path if path.is_file() else None

    for candidate in _candidates():
        if candidate.is_file():
            return candidate

    if sys.platform == "win32":
        try:
            import winreg

            for key, hive_name in _REG_KEYS:
                hive = getattr(winreg, hive_name)
                try:
                    with winreg.OpenKey(hive, key) as handle:
                        value, _ = winreg.QueryValueEx(handle, None)
                except OSError:
                    continue
                path = Path(str(value))
                if path.is_file():
                    return path
        except Exception:                                  # noqa: BLE001  注册表读不到不是致命错
            pass

    found = shutil.which("chrome") or shutil.which("chrome.exe")
    return Path(found) if found else None


def launch_argv(chrome: str | Path, *, port: int, profile: str | Path,
                url: str | None = None) -> list[str]:
    """组装启动参数。端口与 profile 都是**账号属性**，不信全局配置。"""
    argv = [str(chrome), f"--remote-debugging-port={int(port)}",
            f"--user-data-dir={Path(profile)}", *_CHROME_ARGS]
    if url:
        argv.append(url)
    return argv


def port_alive(port: int, *, timeout: float = 1.0) -> bool:
    """调试端口是否活着。只打 `127.0.0.1`，**不访问平台**（零外网请求）。"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{int(port)}/json/version",
                                    timeout=timeout) as resp:
            return resp.status == 200
    except Exception:                                      # noqa: BLE001  连不上就是没起
        return False


def wait_alive(port: int, seconds: float = 20.0, interval: float = 0.5) -> bool:
    """等端口起来（Chrome 冷启动要几秒）。"""
    deadline = time.monotonic() + max(0.0, float(seconds))
    while time.monotonic() < deadline:
        if port_alive(port):
            return True
        time.sleep(interval)
    return port_alive(port)


def start(chrome: str | Path, *, port: int, profile: str | Path,
          url: str | None = None, wait_seconds: float = 20.0) -> subprocess.Popen:
    """拉起 Chrome（脱离面板进程），返回 Popen 句柄。

    Windows 下用 `CREATE_NEW_PROCESS_GROUP`：面板重启/退出不该把用户正在登录的窗口带走。
    """
    profile = Path(profile)
    profile.mkdir(parents=True, exist_ok=True)
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | \
                getattr(subprocess, "DETACHED_PROCESS", 0)
    proc = subprocess.Popen(launch_argv(chrome, port=port, profile=profile, url=url),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=flags)
    wait_alive(port, seconds=wait_seconds)
    return proc


def default_start_url() -> str:
    """启动时打开哪一页：学习入口（人登录完就能直接点课）。"""
    return selectors.MAIN_PAGE
