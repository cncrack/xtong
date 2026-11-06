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
import socket
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from ...core import runmark
from . import selectors

#: **绕过系统代理**的 opener：本机 `127.0.0.1` 永远不该走代理。
#: 不加这个的话，系统代理（v2rayN/xray）开着时 `urllib` 会把本机探活也发给代理，
#: 每次都卡满 timeout（2026-10-30 实测：`/api/state` 从 ~30ms 变成 6078ms）。
_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))

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


def port_alive(port: int, *, timeout: float = 0.5) -> bool:
    """调试端口是否活着。只打 `127.0.0.1`，**不访问平台**（零外网请求）。

    两件实测出来的事：

    ① **必须显式绕过系统代理**：系统代理（v2rayN/xray）开着时，`urllib` 默认会把
       `http://127.0.0.1:<port>/...` **也发给代理**。本机地址永远不该走代理。
    ② **不能假设"已关闭端口会被立刻拒绝"**：本机实测 `connect(127.0.0.1, 已关闭端口)`
       是 **TimeoutError（约 1 秒）**，不是 ConnectionRefused —— 回环包被防火墙规则
       （`codex_sandbox_offline_block_loopback_tcp` 等全局 Block）丢掉了。
       所以单次探测最坏就是**一个 timeout**，默认取 0.5 秒
       （活着的 CDP 端口在本机是 0.0ms 握手 + 几十 ms 响应，0.5s 足够宽松）。
    """
    try:
        with _DIRECT.open(f"http://127.0.0.1:{int(port)}/json/version",
                          timeout=timeout) as resp:
            return resp.status == 200
    except Exception:                                      # noqa: BLE001  连不上就是没起
        return False


def ports_alive(ports: list[int], *, timeout: float = 0.5) -> dict[int, bool]:
    """**并发**探活多个端口，返回 `{端口: 是否活着}`。

    为什么必须并发：单次探测最坏是一个 timeout（见 `port_alive` 的 ②），
    串行时 N 个账号就是 N × timeout —— 实测 5 个账号把 `/api/state` 拖到 **6078ms**，
    而面板每 4 秒轮询一次，界面等于一直在等。
    并发后总耗时约等于**一次** timeout，账号再多也不会线性变慢。
    """
    uniq = sorted({int(p) for p in ports})
    if not uniq:
        return {}
    if len(uniq) == 1:
        return {uniq[0]: port_alive(uniq[0], timeout=timeout)}
    out: dict[int, bool] = {}
    with ThreadPoolExecutor(max_workers=min(8, len(uniq))) as pool:
        futures = {pool.submit(port_alive, p, timeout=timeout): p for p in uniq}
        for fut in as_completed(futures):
            port = futures[fut]
            try:
                out[port] = bool(fut.result())
            except Exception:                              # noqa: BLE001
                out[port] = False
    return out


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


# ------------------------------------------------------------ 归属判定（谁起的这个 Chrome）

#: Chrome 在 `--user-data-dir` 里放的**单实例锁**：一个 profile 同时只能被一个
#: Chrome 实例持有。它的 mtime ≈ 该实例启动时刻，因此能配 `runmark.pid_owns_file`
#: 判断「占着这份 profile 的，是不是监听这个端口的那个进程」。
#:
#: ⚠ **不要**改用 `DevToolsActivePort`：本机实测它只在
#: `--remote-debugging-port=0`（随机端口）时才写；面板用的是显式端口，
#: 那个文件根本不存在（2026-11-06 实测踩到，原方案当场失效）。
_PROFILE_LOCK = "lockfile"


def listening_pid(port: int) -> int:
    """谁在监听这个 TCP 端口 → pid；查不到返回 0。

    用 `GetExtendedTcpTable`（ctypes，毫秒级）而不是 `netstat -ano` 子进程：
    `/api/state` 每 4 秒轮询一次，起子进程会把界面拖慢。
    """
    if sys.platform != "win32":
        return 0
    try:
        import ctypes
        from ctypes import wintypes

        class _Row(ctypes.Structure):
            _fields_ = [("state", wintypes.DWORD),
                        ("local_addr", wintypes.DWORD),
                        ("local_port", wintypes.DWORD),
                        ("remote_addr", wintypes.DWORD),
                        ("remote_port", wintypes.DWORD),
                        ("pid", wintypes.DWORD)]

        af_inet, table_listener = 2, 3
        iphlpapi = ctypes.windll.iphlpapi
        size = wintypes.DWORD(0)
        iphlpapi.GetExtendedTcpTable(None, ctypes.byref(size), False,
                                    af_inet, table_listener, 0)
        if not size.value:
            return 0
        buf = ctypes.create_string_buffer(size.value)
        if iphlpapi.GetExtendedTcpTable(buf, ctypes.byref(size), False,
                                        af_inet, table_listener, 0) != 0:
            return 0
        count = ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD))[0]
        rows = ctypes.cast(ctypes.addressof(buf) + ctypes.sizeof(wintypes.DWORD),
                           ctypes.POINTER(_Row))
        for i in range(int(count)):
            # dwLocalPort 低 16 位是**网络字节序**，要转回主机序才能比
            if socket.ntohs(rows[i].local_port & 0xFFFF) == int(port):
                return int(rows[i].pid)
    except Exception:                                      # noqa: BLE001  拿不到就当不知道
        return 0
    return 0


def process_image(pid: int) -> str:
    """进程的完整可执行文件路径；查不到返回 ""。

    用来兜最后一道：pid 复用时它可能是个完全无关的进程，绝不能 taskkill。
    """
    if sys.platform != "win32" or int(pid) <= 0:
        return ""
    try:
        import ctypes
        from ctypes import wintypes

        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))  # QUERY_LIMITED
        if not h:
            return ""
        try:
            size = wintypes.DWORD(32768)
            path = ctypes.create_unicode_buffer(size.value)
            if not ctypes.windll.kernel32.QueryFullProcessImageNameW(
                    h, 0, path, ctypes.byref(size)):
                return ""
            return path.value
        finally:
            ctypes.windll.kernel32.CloseHandle(h)
    except Exception:                                      # noqa: BLE001
        return ""


def owner_pid(profile: str | Path, port: int) -> int:
    """**这份 profile 的 Chrome 是否正占着该调试端口**；是则返回它的 pid，否则 0。

    ## 为什么需要（2026-11-06 用户实拍「停止浏览器」灰着 / 点了不敢杀）

    「停止浏览器」原来只认面板**内存**里的登记表（`_BROWSERS`）。面板一重启表就空了
    → 明明是面板自己刚拉起的 Chrome，界面却显示「不是面板起的」，按钮点了也不敢杀，
    只能让用户手动关窗口。

    这里改成看**现场证据**，重启前后都成立（三道全过才返回 pid）：

    ① 端口确实有人在听 —— `listening_pid`；
    ② 听的人**是 Chrome** —— `process_image` 末段是 `chrome.exe`
       （防 pid 复用把无关进程当成浏览器）；
    ③ 它**占着这份 profile** —— profile 的 `lockfile` 存在，且进程创建时间
       不晚于该文件 mtime（`runmark.pid_owns_file`，与任务认领同一套判据）。

    用户日常的 Chrome 用的是**自己的** profile，第 ③ 条过不去 —— 动不到它。
    """
    pid = listening_pid(port)
    if pid <= 0:
        return 0
    image = process_image(pid)
    if image and "chrome" not in Path(image).name.lower():
        return 0
    lock = Path(profile) / _PROFILE_LOCK
    try:
        if not lock.is_file():
            return 0
        mtime = lock.stat().st_mtime
    except OSError:
        return 0
    created = runmark.pid_create_time(pid)
    if created > 0 and created > mtime + 5:     # 5 秒容忍：时间戳精度 / 时钟抖动
        return 0                                # 这个 pid 是后来才有的 → 不是它占的 profile
    return pid
