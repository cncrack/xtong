"""第二平台独立面板（`127.0.0.1:8301`）。

与超星面板**不共享进程、路由、表结构**（用户 2026-10-30 定「各自独立」）：
- 超星面板 = `xxt.panel.app`（8300），继续用它自己的 `accounts` / `run_logs`；
- 本模块 = 第二平台（8301），账号来自 `accounts.local.json`，日志落在 `gx_ops`。

面板只做三件事：**看**（节进度、操作记录）、**管**（开始/停止/刷新）、
**核对**（AI 填了什么，一格一格列出来）。所有重活交给 `scripts/` 下的子进程跑——
Playwright 同步 API 会阻塞，跑在面板进程里等于把界面锁死。
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from ..adapters.gxks import accounts as gaccounts
from ..adapters.gxks import browser as gbrowser
from ..adapters.gxks import chrome as gchrome
from ..adapters.gxks import ledger as gledger
from ..adapters.gxks import locks as glocks
from ..adapters.gxks import login as glogin
from ..adapters.gxks import sections_cache
from ..config import Settings
from ..core import runmark
from ..store import gxks_ai_config
from ..store import gxks_ops
from ..store import gxks_panel_settings
from ..store.schema_gxks import open_question_bank

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"

# 每个账号同时只允许一个任务：两个脚本抢同一个 Chrome 会话会互相顶掉页面
#
# ⚠ **键必须是 (label, kind)，不能只用 label**（2026-11-04 修的真 bug）：
# 原来 `_JOBS[label] = {...}` ⇒ **后起的任务会顶掉先起的**；
# 而 `_jobs_snapshot()` 清理死任务时 `_JOBS.pop(label)` ⇒ **把还在跑的那个一起弹了**。
# 实测：11:42 起 `sections` 任务 → 11:50 起 `study` 任务（顶掉前者）→
# 12:03 发现 sections 已死 → `pop("default")` → **study 从面板视野里消失**
# （面板显示「没有任务」，而它其实还在跑）。
#
# 键用 `label + "|" + kind` 就能同时容纳两个任务，且清理时只弹自己那一条。
_JOBS: dict[str, dict[str, Any]] = {}
_JOBS_LOCK = threading.Lock()


def _job_key(label: str, kind: str) -> str:
    """任务在 `_JOBS` 里的键：同一账号的不同任务（study / sections / work）各占一条。"""
    return f"{label}|{kind}"

# 面板自己拉起的浏览器（只记我们起的，用于"停止浏览器"时只杀自己那一只）
_BROWSERS: dict[str, dict[str, Any]] = {}
_BROWSER_LOCK = threading.Lock()
_CHROME_TASK = "xxt-gxks-chrome"   # 交互式计划任务（见 scripts/start_chrome.ps1）

#: 串行化「启动浏览器」：计划任务与 `data/tmp/chrome_start.json` 都是**全局唯一**的，
#: 两个账号同时点「启动并登录」会互相覆盖 spec（后写的赢）→ 起错 profile / 端口对不上，
#: 而且两边的 result 文件也会互相踩（2026-11-07 查出的 P1）。
#: 用**进程内互斥**而不是按 label 分文件：计划任务的动作是注册时写死的
#: `-File start_chrome.ps1`，运行期传不进参数/环境变量，分文件只会让脚本找不到 spec。
#: 代价：两个账号的启动会排队（每个 10~40 秒），这本来就是可接受的 —— 同时开两个
#: Chrome 反而抢资源。
_CHROME_START_LOCK = threading.Lock()

# ------------------------------------------------------------------ 日志推送
#
# ## 为什么要有（2026-11-04 用户：「日志为何要4秒一刷新呢？只要有就更新不是更好吗」）
#
# 原来日志跟 `/api/state` 的 **4 秒轮询**绑在一起：
# ① 刷新慢 —— 新日志最多 4 秒后才显示；
# ② 每次轮询都要把**整个日志文件读一遍**（`_log_tail` 是 `read_text` + 切片），
#    日志越长读得越多，纯浪费 I/O；
# ③ 整块重绘导致滚动位置跳（虽然已用倒序 + `scrollTop=0` 缓解）。
#
# ## 方案：SSE（Server-Sent Events）
#
# 浏览器原生 `EventSource`，无需前端库、无需额外端口。
# 后端起一个**监视线程**：每 0.4 秒看一次当前任务日志文件的 (大小, mtime)，
# 变了就把新内容推给所有订阅者。
#
# 订阅者用 `queue.Queue` 收集，FastAPI 的 `StreamingResponse` 逐条吐出去。
_EVENTS: set[queue.Queue] = set()
_EVENTS_LOCK = threading.Lock()
_WATCH_THREAD: threading.Thread | None = None


def _event_put(payload: dict[str, Any]) -> None:
    """把一条事件推给所有订阅者（满了就丢旧的，保证不卡住发布方）。"""
    with _EVENTS_LOCK:
        targets = list(_EVENTS)
    for q in targets:
        try:
            q.put_nowait(payload)
        except queue.Full:
            # 订阅者太慢（网络卡/标签页挂起）：丢掉它最老的一条再塞新的，
            # 不能阻塞发布线程（否则整个面板卡住）。
            try:
                q.get_nowait()
                q.put_nowait(payload)
            except (queue.Empty, queue.Full):
                pass


def _log_signature() -> dict[str, Any]:
    """当前所有任务日志的「指纹」（大小 + 修改时间）。变了就说明有新内容。

    ⚠ 键用 `label|kind`（同 `_JOBS`）：同一账号可能同时有 study 和 sections 两个任务，
    只按 label 记指纹会让**后一个覆盖前一个** → 推送会漏。
    """
    out: dict[str, Any] = {}
    with _JOBS_LOCK:
        jobs = list(_JOBS.items())
    for key, job in jobs:
        p = Path(job["log"])
        try:
            st = p.stat()
            out[key] = {"label": job["label"], "kind": job["kind"],
                        "size": st.st_size, "mtime": st.st_mtime, "path": str(p)}
        except OSError:
            continue
    return out


def _watch_logs_loop(stop: threading.Event) -> None:
    """监视日志变化 → 推送。**只推变化的部分**，不重发全文。

    ⚠ **路径变了要重置偏移**（2026-11-05 复核发现）：日志文件名带启动时刻
    （`gxks-study-default-20261104-115058.log`），同一账号同一 `kind` 重新起任务时
    会生成**新文件**，但 `_JOBS` 的 key（`label|kind`）不变。
    若只看 `size`，`f.seek(旧文件大小)` 会**跳过新文件的开头**（新文件比旧的短时
    甚至直接读到空）。所以 `last_path[key] != info["path"]` 时必须从 0 开始。
    """
    last: dict[str, Any] = {}
    last_size: dict[str, int] = {}
    last_path: dict[str, str] = {}
    while not stop.is_set():
        try:
            sig = _log_signature()
            for key, info in sig.items():
                # 换文件了（同 key 新任务）→ 偏移归零，从头推
                if last_path.get(key) != info["path"]:
                    last_path[key] = info["path"]
                    last_size[key] = 0
                    last[key] = info
                prev = last_size.get(key)
                if prev is None:
                    last_size[key] = info["size"]
                    last[key] = info
                    continue
                if info["size"] == prev:
                    continue
                # 有新内容：把**新增的部分**推出去
                try:
                    with open(info["path"], "rb") as f:
                        f.seek(prev)
                        chunk = f.read().decode("utf-8", errors="replace")
                except OSError:
                    continue
                last_size[key] = info["size"]
                last[key] = info
                if chunk:
                    _event_put({"type": "log", "label": info["label"],
                                "kind": info["kind"], "text": chunk})
            # 任务结束（从 _JOBS 消失）→ 告诉前端刷新一次状态
            gone = set(last) - set(sig)
            for key in gone:
                _event_put({"type": "job-ended", "label": last[key]["label"],
                            "kind": last[key]["kind"]})
                last.pop(key, None)
                last_size.pop(key, None)
                last_path.pop(key, None)
        except Exception:                                       # noqa: BLE001
            pass
        stop.wait(0.4)


def _ensure_watcher() -> None:
    global _WATCH_THREAD
    with _EVENTS_LOCK:
        if _WATCH_THREAD is not None and _WATCH_THREAD.is_alive():
            return
        stop = threading.Event()
        th = threading.Thread(target=_watch_logs_loop, args=(stop,),
                              daemon=True, name="gxks-log-watch")
        th.start()
        _WATCH_THREAD = th


def _start_chrome_interactive(label: str, port: int) -> dict[str, Any]:
    """用**交互式计划任务**在用户桌面会话里起 Chrome（2026-11-03 实测的必要做法）。

    ## 为什么不能直接 Popen

    面板本身跑在后台计划任务（非交互会话）里，由它 `subprocess.Popen` 起的 Chrome
    **拿不到桌面、直接退出** → 调试端口永远起不来。实测：同一组参数从交互式 shell
    起就 100% 正常，从面板起永远是「Chrome 起来了但端口没响应」。

    ## 做法

    计划任务 `xxt-gxks-chrome`（`-LogonType Interactive`）跑
    `scripts/start_chrome.ps1`（有桌面）。面板只写参数文件 → 触发 → 读结果。
    Chrome 因此**不属于面板的子进程**，面板重启/退出都不会带走它。

    返回 `{"ok": bool, "pid"?: int, "error"?: str}`。

    ## 并发安全（2026-11-07 修的 P1）

    `data/tmp/chrome_start.json` 与 `chrome_start.result.json` 是**全局唯一**的两个文件，
    计划任务的动作也在注册时写死。两个账号同时点「启动并登录」时，后写的 spec 会覆盖
    前一个 → 起错 profile / 端口对不上，result 也会互相踩。
    所以整个「写 spec → 触发 → 等端口」必须**串行**（`_CHROME_START_LOCK`）。
    代价是第二个账号要排队等前一个起完（10~40 秒），这是可接受的：同时开两个 Chrome
    反而抢资源。
    """
    with _CHROME_START_LOCK:
        return _start_chrome_interactive_locked(label, port)


def _start_chrome_interactive_locked(label: str, port: int) -> dict[str, Any]:
    """`_start_chrome_interactive` 的实际实现（调用方必须已持有 `_CHROME_START_LOCK`）。"""
    tmp = ROOT / "data" / "tmp"
    spec = tmp / "chrome_start.json"
    result = tmp / "chrome_start.result.json"
    tmp.mkdir(parents=True, exist_ok=True)
    result.unlink(missing_ok=True)
    # 带上启动 URL（`selectors.MAIN_PAGE`，学习入口）：不给 URL 时 Chrome 停在
    # `chrome://new-tab-page/`，而刷课脚本要求页面里已有课程列表帧（cdp 模式故意
    # 不导航，免得打断人工操作）→ 点「开始学习」会立刻报「找不到平台页面」。
    # ⚠ 这里只能引用 `selectors`，**不能写平台 URL 字面量**（平台细节只属于 adapters/，
    # 由 `tests/test_gxks_boundaries.py` 守）。
    from ..adapters.gxks import selectors
    spec.write_text(json.dumps({"label": label, "port": int(port),
                                "url": selectors.MAIN_PAGE}), encoding="utf-8")
    try:
        done = subprocess.run(["schtasks", "/run", "/tn", _CHROME_TASK],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=20)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "触发计划任务超时"}
    if done.returncode != 0:
        task = ROOT / "scripts" / "start_chrome.ps1"
        return {"ok": False,
                "error": "交互式任务 xxt-gxks-chrome 不存在或起不来。\n"
                         "在**你自己的 PowerShell 窗口**里执行一次（只需一次，之后一直有效）：\n"
                         f'$a=New-ScheduledTaskAction -Execute powershell.exe -Argument '
                         f'"-NoProfile -ExecutionPolicy Bypass -File {task}";'
                         f'$p=New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\\$env:USERNAME" '
                         f'-LogonType Interactive -RunLevel Limited;'
                         f'Register-ScheduledTask -TaskName {_CHROME_TASK} -Action $a '
                         f'-Principal $p -Force'}
    # 等 Chrome 冷启动 + 端口就绪（脚本自己最多等 30 秒，这里留点余量）
    for _ in range(80):
        time.sleep(0.5)
        if gchrome.port_alive(port):
            pid = 0
            try:
                pid = int(json.loads(result.read_text(encoding="utf-8")).get("pid") or 0)
            except Exception:                                   # noqa: BLE001
                pass
            return {"ok": True, "pid": pid}
    err = ""
    try:
        err = str(json.loads(result.read_text(encoding="utf-8")).get("error") or "")
    except Exception:                                           # noqa: BLE001
        pass
    return {"ok": False, "error": err or f"等了 40 秒，端口 {port} 仍未响应"}


# ------------------------------------------------------------------ 子进程

def _log_tail(path: Path, lines: int = 12) -> list[str]:
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return text.strip().splitlines()[-lines:]


def _kill(proc: subprocess.Popen) -> str:
    """停掉子进程**及其子进程**。Windows 下 `terminate()` 只杀本人，Chrome 会留下。"""
    if proc.poll() is not None:
        return "已结束"
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           capture_output=True, timeout=15)
        else:
            proc.terminate()
        proc.wait(timeout=15)
        return "已停止"
    except Exception as exc:                                # noqa: BLE001
        return f"停止失败：{exc}"


def _spawn_script(settings: Settings, label: str, kind: str,
                  script: str, *args: str) -> subprocess.Popen:
    """起一个 `scripts/*.py` 子进程。

    输出**必须落盘**：2026-10-25 的哑故障就是 DEVNULL 吞掉了 traceback，表现为
    "点了没反应"。这里同样不吞，日志名带账号与时刻，面板直接读尾巴。

    ## ⚠ 子进程要**脱离面板的进程树**（2026-11-04 修）

    原来没传 `creationflags` → 子进程默认在父进程的 **Job Object** 里 →
    Windows 上**父进程一死，整棵进程树跟着死**。实测：面板重启（热重载触发、
    或手动重启）会把正在跑的刷课任务一起杀掉 → 日志末尾 `KeyboardInterrupt` →
    任务中断、**不会自动切下一节**（2026-11-04 连续踩到两次）。

    ## ⚠ 用 `CREATE_NO_WINDOW`，**不要用 `DETACHED_PROCESS`**（2026-11-04 实测踩到）

    第一次改用了 `DETACHED_PROCESS`，结果**屏幕上弹出一个黑窗口**
    （标题 `E:\\xxt-gxks\\.venv\\Scripts\\python.exe`）—— 因为
    `DETACHED_PROCESS` 对**控制台程序**（venv 转发器 `Scripts\\python.exe` 就是）
    会**分配一个新控制台**，于是就弹窗了。

    而且 Windows 规定 `DETACHED_PROCESS` 与 `CREATE_NO_WINDOW` **互斥**。

    正确组合：**`CREATE_NO_WINDOW` + `CREATE_NEW_PROCESS_GROUP`**：
    - `CREATE_NO_WINDOW` → **不弹黑窗口**，且同样让子进程脱离父控制台
      （父进程退出不影响它，满足「面板重启不杀刷课」的要求）；
    - `CREATE_NEW_PROCESS_GROUP` → Ctrl+C 不会误杀它。

    实测（2026-11-04）：加上后既无黑窗口，重启面板后刷课任务仍在跑。
    """
    log_dir = settings.data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"gxks-{kind}-{label}-{time.strftime('%Y%m%d-%H%M%S')}.log"
    argv = [sys.executable, "-u", str(SCRIPTS / script), *args]
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1",
               PYTHONUNBUFFERED="1")
    handle = open(log_path, "ab")
    flags = 0
    if os.name == "nt":
        flags = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
                 | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    try:
        proc = subprocess.Popen(argv, cwd=str(ROOT), stdout=handle,
                                stderr=handle, env=env, creationflags=flags)
    finally:
        handle.close()
    with _JOBS_LOCK:
        _JOBS[_job_key(label, kind)] = {
            "proc": proc, "kind": kind, "label": label,
            "since": time.strftime("%H:%M:%S"), "log": log_path,
            "detail": " ".join(args[:4]),
            # 供 `_jobs_snapshot` 在任务结束时清登记表（它拿不到 settings）
            "data_dir": str(settings.data_dir)}
    _write_job_registry(settings, label, kind, proc.pid, log_path,
                        " ".join(args[:4]))
    return proc


# ---------------------------------------------------------------- 任务登记（认领孤儿）

# ---------------------------------------------------------------- 排队（P2b，2026-11-06）

#: 排队中的学习任务：`{label: {...,"queued_at":...}}`（按 label 唯一，同账号不重复排队）
_QUEUE: dict[str, dict[str, Any]] = {}
_QUEUE_LOCK = threading.Lock()
_QUEUE_THREAD: threading.Thread | None = None


def _queue_path(settings: Settings) -> Path:
    """队列落盘文件：面板重启后靠它把「排队中的任务」重建回来。

    与 `gxks_jobs.json`（**已在跑**的任务）刻意分开成两个文件：
    那个回答「谁在跑」，这个回答「谁在等」，语义不同、清理时机也不同
    （跑完要清，排队被打断也要清）。
    """
    return settings.data_dir / "gxks_queue.json"


def _read_queue(settings: Settings) -> dict[str, dict[str, Any]]:
    p = _queue_path(settings)
    try:
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_queue(settings: Settings) -> None:
    """把内存队列写盘（原子替换）。"""
    p = _queue_path(settings)
    with _QUEUE_LOCK:
        snapshot = {k: dict(v) for k, v in _QUEUE.items()}
    try:
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        pass


def _queue_add(settings: Settings, label: str, max_sections: int,
               jitter: float = 0.0) -> None:
    """把一个暂时起不了（并发满）的任务放进队列。"""
    with _QUEUE_LOCK:
        _QUEUE[label] = {"label": label, "max_sections": int(max_sections),
                         "jitter": float(jitter),
                         "queued_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                         "since": time.strftime("%H:%M:%S")}
    _save_queue(settings)


def _queue_drop(settings: Settings, label: str) -> bool:
    """从队列移除一个账号，返回**是否真的移除了**（供停止接口判断要不要多报一句）。"""
    with _QUEUE_LOCK:
        had = _QUEUE.pop(label, None) is not None
    if had:
        _save_queue(settings)
    return had


def _queue_snapshot() -> list[dict[str, Any]]:
    """排队清单（按入队时间先后 —— 先来先跑）。"""
    with _QUEUE_LOCK:
        rows = [dict(v) for v in _QUEUE.values()]
    rows.sort(key=lambda r: str(r.get("queued_at") or ""))
    return rows


def adopt_queue(settings: Settings) -> list[dict[str, Any]]:
    """启动时把落盘的队列读回内存（面板重启不丢排队）。

    与孤儿任务认领的区别：这里**不需要**判 pid —— 排队的任务还没起进程。
    但要**剔除已经在跑或已不存在的账号**（否则会重复起 / 起一个幽灵）。
    """
    data = _read_queue(settings)
    if not data:
        return []
    known = {a.label for a in gaccounts.load_accounts()}
    running = {j["label"] for j in _jobs_snapshot()}
    kept: dict[str, dict[str, Any]] = {}
    dropped: list[str] = []
    for label, info in data.items():
        if (label in known and label not in running
                and isinstance(info, dict)):
            kept[label] = dict(info)
        else:
            dropped.append(label)
    with _QUEUE_LOCK:
        _QUEUE.clear()
        _QUEUE.update(kept)
    if dropped:
        _save_queue(settings)
    return _queue_snapshot()


def _queue_pump(settings: Settings) -> int:
    """把队列里**有空位**的任务起掉，返回本次起了几个。

    只在三种时机调用：① 起任务时被并发闸挡住（进队）② 有任务结束 ③ 后台循环。

    ⚠ 必须**先判空位再起**：并发闸是全局的，队列里排 5 个也只该起 `room` 个。
    """
    started = 0
    while True:
        limit = _concurrency_limit(settings)
        room = limit - _live_study_count()
        if room <= 0:
            break
        rows = _queue_snapshot()
        if not rows:
            break
        # 队头优先（先来先跑）；杀掉一个就跑一个。
        head = rows[0]
        label = str(head.get("label") or "")
        with _QUEUE_LOCK:
            _QUEUE.pop(label, None)
        # 全局日总量闸（P2b）也要判：今天合计学够了就别再起，直接清队列
        if _check_fleet_daily(settings):
            with _QUEUE_LOCK:
                _QUEUE.clear()
            _save_queue(settings)
            break
        got = _study_one(settings, label,
                         max_sections=head.get("max_sections"),
                         jitter=float(head.get("jitter") or 0),
                         check_concurrency=False)
        if got.get("ok"):
            started += 1
        elif not got.get("skipped"):
            # 真起不来（浏览器没开 / 账号没了）：不要留在队列里反复空转
            pass
        _save_queue(settings)
    return started


def _queue_worker(settings: Settings) -> None:
    """后台循环：每 20 秒看一次队列，有空位就起。

    ## 为什么需要它

    队列的意义是「排队等着，有空位自动跑」。没有循环的话，只有用户下一次点击
    才会触发 `_queue_pump` —— 那就等于没排队（还得手动再点一次）。

    ## 间隔为什么是 20 秒

    刷课一节 20~45 分钟，20 秒的调度延迟完全无感；
    太短会白耗 CPU 且每次都要探端口（`port_alive` 最坏吃一个 timeout）。
    """
    while True:
        time.sleep(20)
        try:
            if _queue_snapshot():
                _queue_pump(settings)
        except Exception:                                  # noqa: BLE001
            # 调度线程绝不能死：它死了队列就永远不动了，且用户看不出来。
            time.sleep(60)


def _ensure_queue_worker(settings: Settings) -> None:
    global _QUEUE_THREAD
    if _QUEUE_THREAD is not None and _QUEUE_THREAD.is_alive():
        return
    _QUEUE_THREAD = threading.Thread(target=_queue_worker, args=(settings,),
                                     name="gxks-queue", daemon=True)
    _QUEUE_THREAD.start()


def _start_and_study_one(settings: Settings, label: str, *,
                         run_session: Any, max_sections: object = None,
                         wait_seconds: float = 45.0) -> dict[str, Any]:
    """**一键**：起浏览器 → 等端口就绪 → 起学习任务（2026-11-06 用户要求）。

    ## 为什么需要它

    原来是两步：先点「启动并登录」，等 10~40 秒 Chrome 起来，再点「学习」。
    多账号时这个"等"最烦 —— 用户得盯着端口有没有活。这一键把两步串起来。

    ## 为什么能等（不像别的接口那样怕阻塞）

    `_browser_start_one` 内部已经等端口（`_start_chrome_interactive` 里有
    最多 40 秒的轮询）。这里只是**再多兜一层**：万一端口比它报的更晚就绪。
    批量调用方不调这个函数（批量走 `start-browser` + `start` 分两步，
    避免单个账号卡住整批）。

    返回与 `_study_one` 相同的回包语义（`ok` / `skipped` / `blocked` / `queued`）。
    """
    acc = _account(settings, label)
    # 已经在跑就直接跳过（用户可能连点了两下，或这账号本来就在刷）
    if any(j["label"] == acc.label for j in _jobs_snapshot()):
        return {"ok": False, "skipped": True,
                "error": "该账号已有任务在跑（浏览器与学习都不用再起）"}

    # ① 浏览器：已经在跑就不重复起（`_browser_start_one` 会返回 skipped）
    port_alive = gchrome.port_alive(acc.port)
    if not port_alive:
        got = _browser_start_one(settings, acc.label, run_session=run_session)
        if not got.get("ok"):
            # 浏览器都起不来，就不要再谈学习了 —— 原样把原因透出去
            return got
        # ② 等端口真的能连（`_start_chrome_interactive` 已等过，这里只是兜底）
        deadline = time.monotonic() + max(0.0, wait_seconds)
        while time.monotonic() < deadline:
            if gchrome.port_alive(acc.port):
                port_alive = True
                break
            time.sleep(1.0)
        if not port_alive:
            return {"ok": False,
                    "error": f"Chrome 起来了但端口 {acc.port} 还没就绪"
                             f"（等了 {wait_seconds:.0f} 秒）——稍等片刻再点「学习」即可"}

    # ③ 学习任务：走与单账号完全相同的逻辑（并发闸/日总量闸/错峰都在里面）
    started = _study_one(settings, acc.label, max_sections, port_alive=True)
    if started.get("ok"):
        _ensure_queue_worker(settings)
        started["message"] = ("浏览器就绪 → " + str(started.get("message") or "已开始学习"))
    return started


def _registry_path(settings: Settings) -> Path:
    """任务登记文件：面板重启后靠它把「还在跑但自己不知道」的任务认领回来。

    ## 为什么需要（2026-11-05 用户拍板等 5 节跑完再重启）

    `_JOBS` 是**内存表**。子进程加了 `CREATE_NO_WINDOW` 后确实脱离了面板进程树
    （面板重启不再杀它，这是好事），但代价是：**重启后面板不认得它了** ——
    任务继续跑到自然结束，可面板上看不见、也停不掉（「停止」找不到对象）。

    于是把「我起过哪些任务」落盘。重启后启动时读一遍：
    pid 还活着 + 创建时间对得上（`runmark.pid_owns_file`，防 pid 复用）
    → 重新登记进 `_JOBS`，面板照常显示日志、照常能停。
    """
    return settings.data_dir / "gxks_jobs.json"


def _write_job_registry(settings: Settings, label: str, kind: str,
                        pid: int, log_path: Path, detail: str) -> None:
    """登记一个刚起的任务（失败不影响任务本身 —— 登记只是"备忘"）。"""
    p = _registry_path(settings)
    try:
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        if not isinstance(data, dict):
            data = {}
        data[_job_key(label, kind)] = {
            "label": label, "kind": kind, "pid": int(pid),
            "log": str(log_path), "detail": detail,
            "since": time.strftime("%H:%M:%S"),
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        pass


def _drop_job_registry(data_dir: Path, label: str, kind: str) -> None:
    """任务结束时把它从登记表里删掉（否则下次启动会拿一个死 pid 去认领）。"""
    p = Path(data_dir) / "gxks_jobs.json"
    try:
        if not p.exists():
            return
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return
        if data.pop(_job_key(label, kind), None) is None:
            return
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        pass


class _OrphanProc:
    """认领回来的任务：只有 pid，没有 `subprocess.Popen` 对象。

    只实现 `_JOBS` 用到的两个接口（`poll()` 判活、`pid` 供 taskkill），
    这样 `_jobs_snapshot` / `_stop_one` / `_kill` 一行都不用改就能处理它。
    """

    def __init__(self, pid: int, alive: bool = True):
        self.pid = int(pid)
        self._alive = bool(alive)

    def poll(self) -> int | None:
        if self._alive and not runmark.pid_alive(self.pid):
            self._alive = False
        return None if self._alive else 0

    def wait(self, timeout: float | None = None) -> int:
        return 0


def adopt_orphan_jobs(settings: Settings) -> list[dict[str, Any]]:
    """启动时把「还在跑但面板不知道」的任务认领回 `_JOBS`，返回认领清单。

    ## 判活三道闸（缺一不可）

    1. **pid 活着** —— `runmark.pid_alive`；
    2. **pid 归属对得上** —— `runmark.pid_owns_file` 比进程创建时间与日志 mtime，
       防 Windows **pid 复用**：否则会把一个恰好复用了旧 pid 的无关进程认成刷课，
       更糟的是「停止」时把它 taskkill 掉（2026-10-27 盘点 B 的同一个坑）；
    3. **日志文件还在** —— 没有日志就无法展示，认领了也是空壳。

    登记表里认领失败（进程已死）的条目**顺手清掉**，不留垃圾。
    """
    p = _registry_path(settings)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []

    adopted: list[dict[str, Any]] = []
    stale: list[str] = []
    for key, info in list(data.items()):
        try:
            pid = int(info.get("pid") or 0)
            label = str(info.get("label") or "")
            kind = str(info.get("kind") or "")
            log = Path(str(info.get("log") or ""))
        except (TypeError, ValueError):
            stale.append(key)
            continue
        if not (pid and label and kind and log.exists()
                and runmark.pid_alive(pid)
                and runmark.pid_owns_file(log, pid)):
            stale.append(key)
            continue
        with _JOBS_LOCK:
            if key in _JOBS:                 # 本进程刚起过同名任务，别覆盖
                continue
            _JOBS[key] = {
                "proc": _OrphanProc(pid), "kind": kind, "label": label,
                "since": str(info.get("since") or ""), "log": log,
                "detail": str(info.get("detail") or ""),
                "data_dir": str(settings.data_dir),
                "adopted": True}
        adopted.append({"label": label, "kind": kind, "pid": pid,
                        "log": log.name})
    if stale:
        left = {k: v for k, v in data.items() if k not in stale}
        try:
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(left, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            os.replace(tmp, p)
        except OSError:
            pass
    return adopted


# 刚结束的任务（按 label 留最后一条）：任务跑完 `_JOBS` 会被清掉，
# 但用户最想看的恰恰是「刚才跑了什么、成功没成功」（2026-11-03）。
# 只留**一条**且只在内存里，不占资源、重启即清空。
_LAST_JOBS: dict[str, dict[str, Any]] = {}


def _jobs_snapshot(include_finished: bool = False) -> list[dict[str, Any]]:
    """`include_finished=True` 时把「刚结束的任务」也带上（`alive: false`）。

    ## 为什么要有这个开关（2026-11-03 用户问「运行日志怎么没有了」）

    原来任务一结束就 `_JOBS.pop()` → 页面立刻变成「当前没有运行中的任务」，
    **刚跑完的日志当场消失**。而那正是用户最需要回看的时刻（成功没？哪节没达标？）。

    默认 `False`：`_stop`/`_account_cards` 这类「判断有没有任务在跑」的地方**不能**
    把已结束的当活的（否则停止按钮、额度计算都会错）。
    只有 `/api/state` 的展示路径传 `True`。
    """
    out: list[dict[str, Any]] = []
    finished: list[tuple[str, str, str]] = []      # (data_dir, label, kind) 待清登记
    with _JOBS_LOCK:
        for key, job in list(_JOBS.items()):
            alive = job["proc"].poll() is None
            snap = {"label": job["label"], "kind": job["kind"], "since": job["since"],
                    "alive": alive, "detail": job["detail"],
                    "log_name": Path(job["log"]).name,
                    "tail": _log_tail(Path(job["log"]))}
            if not alive:
                # ⚠ **按 key 删**（不是按 label）：否则会连带删掉同账号**别的**在跑任务
                # （2026-11-04 实测：sections 死 → `pop("default")` → study 被一起弹掉）
                _JOBS.pop(key, None)
                # 留一条给页面回看（带上结束时间，便于和「正在跑」区分）。
                # ⚠ **不要** append 到 `out`：默认返回值必须是"还在跑的任务"，
                # 否则第一次查询就会把已结束的当成活的 → 「停止」去杀死 pid、
                # 新一轮启动被自己挡住（2026-11-03 被测试抓到）。
                snap["ended_at"] = time.strftime("%H:%M:%S")
                _LAST_JOBS[key] = snap
                finished.append((job.get("data_dir", ""), job["label"], job["kind"]))
                continue
            out.append(snap)
    # 自然结束的任务也要清登记（在锁外做，避免文件 IO 占着锁）
    for data_dir, label, kind in finished:
        if data_dir:
            _drop_job_registry(data_dir, label, kind)
    # 有任务自然结束 → 队列里可能有空位了，立刻泵一次（P2b，2026-11-06）。
    # 不能等后台循环（20 秒）才动：用户刚看到一个任务跑完，队列应该马上接上。
    # ⚠ 只在**真有任务结束**时触发，不能每次快照都泵（这个函数被 4 秒轮询高频调用）。
    if finished and len(finished[0]) == 3:
        _on_job_finished(finished[0][0])
    if include_finished:
        for key, snap in _LAST_JOBS.items():
            if not any(j["label"] == snap["label"] and j["kind"] == snap["kind"]
                       for j in out):
                out.append(dict(snap))
    return out


def _on_job_finished(data_dir: str) -> None:
    """有任务自然结束时调（P2b）：队列里可能腾出空位了，立刻泵一次。

    用 `data_dir` 反推 `Settings` 而不是给 `_jobs_snapshot` 加参数 ——
    那个函数被 `_stop_one` / `_account_cards` / `/api/state` 等多处调用，
    加参数会牵连一片；而且它本来就是"只读快照"，不该知道排队的事。

    泵失败只吞：调度出问题不该让 4 秒轮询的接口 500。
    """
    if not data_dir:
        return
    try:
        from ..config import Settings as _S
        s = _S(root=Path(data_dir).parent)
        if _queue_snapshot():
            _queue_pump(s)
    except Exception:                                      # noqa: BLE001
        pass


def _start(settings: Settings, label: str, kind: str,
           script: str, *args: str, message: str) -> dict[str, Any]:
    """起任务并拼好给前端的回包。

    冻结成 exe 后 `scripts/` 不在包内，这条路直接说不支持——比默默报 500
    或“点了没反应”强。

    ## 替子进程写账号会话锁（A1，2026-11-07）

    超星侧 2026-10-27 踩过：面板「先 spawn 后写标记」没问题，但子进程自查时必须
    认出「这条标记是自己人写的」，否则一秒就自杀。这里同理——
    面板用**子进程的 pid** 写标记，`scripts/run_gxks.py` 启动自查时
    `pid ∈ {自己, 父进程}`（venv 重定向器）→ 认作自己人，不重写、不退出。

    写失败不拦任务（锁是红线但不是主流程），只吞。
    """
    if getattr(sys, "frozen", False):
        return {"ok": False,
                "error": "打包版面板暂不支持起脚本（scripts/ 不在 exe 内），请用源码方式启动面板"}
    proc = _spawn_script(settings, label, kind, script, *args)
    try:
        glocks.acquire(settings.data_dir, label, pid=proc.pid)
    except Exception:                                      # noqa: BLE001
        pass
    return {"ok": True, "pid": proc.pid, "message": message}


# ------------------------------------------------------------------ 本地数据

def _safe_label(settings: Settings, label: str) -> str:
    """账号代号只允许本地代号形态，避免被当成路径拼进文件名。"""
    known = {acc.label for acc in gaccounts.load_accounts()}
    if label not in known:
        raise HTTPException(400, f"未知账号：{label}（检查 data/accounts.local.json）")
    return label


def _account(settings: Settings, label: str) -> gaccounts.Account:
    """取账号对象（含它自己的 CDP 端口）。子进程一律显式传 `--port`，不靠它猜。"""
    acc = gaccounts.find_by_label(gaccounts.load_accounts(), _safe_label(settings, label))
    if acc is None:                                       # _safe_label 已拦，兜底防御
        raise HTTPException(400, f"未知账号：{label}")
    return acc


def _account_rows() -> list[dict[str, Any]]:
    """面板展示用账号信息。**绝不返回 name / pw**。"""
    accounts = gaccounts.load_accounts()
    # 序号兜底：老账号（从单账号文件迁移来的 `default`）没有 seq，表格 `#` 列会空一格。
    # 取**尚未被占用的最小正整数**（不能直接用行号：会和用户手填的序号撞车——
    # 实测 `default` 兜底成 1、而 `test` 手填的就是 1，两行都显示 #1）。
    # ⚠ 只算显示值、**不写盘**：写盘会把"看一眼"变成"改数据"，还会覆盖用户手填的序号。
    taken = set()
    for acc in accounts:
        try:
            val = int(acc.extra.get("seq") or 0)
        except (TypeError, ValueError):
            val = 0
        if val > 0:
            taken.add(val)
    free = (n for n in range(1, 10_000) if n not in taken)

    rows = []
    for acc in accounts:
        # 单账号回落时 label 是 `default`，直接显示给人看等于没名字
        display = acc.display or ("主账号" if acc.label == "default" else acc.label)
        profile = {k: acc.extra.get(k, "") for k in gaccounts.ACCOUNT_FIELDS}
        if not profile.get("seq"):
            profile["seq"] = next(free)
        rows.append({
            "label": acc.label,
            "display": display,
            "port": acc.port,
            "profile": Path(acc.profile).name if acc.profile else "",
            "daily_sections": acc.daily_sections,
            "daily_minutes": acc.daily_minutes,
            "enabled": acc.enabled,
            "courses": list(acc.courses),
            # 凭据只回布尔：面板不回显密码，回显即泄露
            "has_credentials": acc.configured,
            # 预置描述字段（序号/学校/专业/年级/学号）+ 用户自定义字段
            "profile_fields": profile,
            "custom_fields": {
                k: v for k, v in acc.extra.items()
                if k not in gaccounts.ACCOUNT_FIELDS
            },
        })
    return rows


def _ledger_path(settings: Settings) -> Path:
    """当日额度账本。跟 `settings.data_dir` 走（测试用临时根目录时不会读到真实账本）。"""
    return settings.data_dir / "gxks_ledger.jsonl"


# tick 心跳每 30 秒一次；连续 3 个周期没刷新 = 任务大概率已中断（手动停/硬停/崩溃）
LEARNING_STALE_SECONDS = 90


def _normalize_stale_learning(sections: dict[str, Any]) -> dict[str, Any]:
    """把「心跳超时」的 learning 行降级为展示状态（done / not_started）。

    背景（2026-11-08 用户：「进度区显示不稳定」）：`sections_cache.update_row`
    的 `playing=True`（tick）会把正在播的节标成 learning 并刷新 `learning_at`。
    任务中断后 tick 不再来，行会一直停在 learning —— 面板永远显示「学习中」
    是误导（原代码靠 progress 回读 not_started 降级，但那个回降本身会把
    正在播的节也打回，见 update_row 的修复说明）。

    规则：`state == "learning"` 且 `learning_at` 距今超过 `LEARNING_STALE_SECONDS`
    → 按 `done_minutes` 推导展示状态（与 update_row 撤回分支同一口径）。

    ⚠ **只影响 /api/state 的展示，不改缓存文件**：文件里的 learning +
    learning_at 是原始证据，任务恢复后 tick 会继续刷新、心跳重新变新鲜。
    没有 `learning_at` 的老行（修复前写的）无法判断 → 保持原样。
    """
    if not sections or not sections.get("rows"):
        return sections
    cutoff = time.time() - LEARNING_STALE_SECONDS
    for r in sections["rows"]:
        if r.get("state") != "learning":
            continue
        try:
            t = datetime.strptime(str(r.get("learning_at") or ""),
                                  "%Y-%m-%d %H:%M:%S").timestamp()
        except ValueError:
            continue                       # 老行没有时间戳 → 无法判断，保持原样
        if t >= cutoff:
            continue                       # 心跳还新鲜
        dm = int(r.get("done_minutes") or 0)
        rm = int(r.get("required_minutes") or 0)
        done = dm >= rm > 0
        r["state"] = "done" if done else "not_started"
        r["state_text"] = "已完成" if done else "已中断（超 90 秒无心跳）"
    return sections


def _pick_sections_cache(debug_dir: Path, label: str,
                         course: str = "") -> tuple[dict[str, Any], str]:
    """选这个账号该读哪一份节列表缓存，返回 `(数据, 实际读到的课程)`。

    **B1 之后**缓存按课程分开（`sections_<代号>_<课程>.json`）。读取顺序：

    ① 面板明确指定了课程 → 精确读那一份（找不到再往下走）；
    ② **带课程的缓存优先于老文件**，同类里取 mtime 最新的一份；
    ③ 最后才回落升级前的老文件 `sections_<代号>.json`。

    ⚠ ② 的顺序**不能**交给 mtime 决定：两个文件可能同一秒写入，谁新谁旧不可靠，
    而"老文件盖住新数据"比"读不到"更难发现。所以先按**文件名是否带课程**分两档，
    档内才比 mtime。

    为什么不做"合并所有课程"：节 ID 在不同课程间可能撞车（见 ledger 的同一教训），
    合并出来的表会把两门课的节混在一屏，比"只显示最近一门"更误导。
    """
    if course:
        exact = sections_cache.path(debug_dir, label, course)
        data = _read_json(exact)
        if data:
            return data, str(data.get("course") or course)

    with_course: list[Path] = []
    legacy: list[Path] = []
    for p in sections_cache.list_all(debug_dir, label):
        (legacy if p.name == f"sections_{label}.json" else with_course).append(p)
    for p in [*with_course, *legacy]:
        data = _read_json(p)
        if data:
            return data, str(data.get("course") or "")
    return {}, ""


def _list_course_caches(debug_dir: Path, label: str) -> list[dict[str, Any]]:
    """列出该账号**所有**节列表缓存（按课程），供前端切换。

    为什么需要（2026-11-03 用户发现）：面板原来只显示**一份**缓存
    （`sections_<代号>.json`，老文件），而刷课/刷新节列表写的是**带课程后缀**的
    `sections_<代号>_<课程>.json` —— 于是面板永远显示旧的那门课，
    用户刷的课「待学/已完成」一点动静都没有。

    节 ID 在不同课程间会撞车（ledger 的同一教训），所以**不做合并**，
    改成列出清单让用户自己选要看的课。
    """
    out: list[dict[str, Any]] = []
    for p in sections_cache.list_all(debug_dir, label):
        data = _read_json(p)
        if not data:
            continue
        rows = data.get("rows") or []
        summary = data.get("summary") or {}
        # 课程 value：文件名里带的那段（老文件没有 → ""）
        m = re.match(rf"sections_{re.escape(label)}_(.+)\.json$", p.name)
        value = m.group(1) if m else ""
        out.append({
            "value": value,
            # 中文课名：从行里的 category 取；没有就退回课程 value
            "name": next((r.get("category") for r in rows if r.get("category")),
                         value or p.stem),
            "sections": int(summary.get("sections") or len(rows)),
            "done": int(summary.get("done") or 0),
            "read_at": str(data.get("read_at") or ""),
            "mtime": time.strftime("%Y-%m-%d %H:%M:%S",
                                   time.localtime(p.stat().st_mtime)),
        })
    return out


def _account_cards(settings: Settings, *,
                   alive: dict[int, bool] | None = None) -> list[dict[str, Any]]:
    """多账号总览：每个账号一张卡的数据。

    只读**本地**东西——账本、`sections_*.json` 缓存、登录态快照、TCP 探活，
    不发任何平台请求（最小访问原则）。凭据只回“有没有”，绝不回值。

    `alive` 允许调用方把**已经探好的**端口结果传进来，避免同一请求里探两遍
    （`/api/state` 既要卡片又要浏览器区，两个地方都要端口状态）。
    """
    accounts = gaccounts.load_accounts()
    jobs = {j["label"]: j for j in _jobs_snapshot()}
    debug_dir = settings.data_dir / "debug"
    ledger_path = _ledger_path(settings)
    # 端口探活**一次并发做完**再进循环：串行时 N 个账号最坏 N × timeout，
    # 实测 5 个账号把 `/api/state` 拖到 6078ms（面板每 4 秒轮询一次，界面一直卡在等）
    if alive is None:
        alive = gchrome.ports_alive([a.port for a in accounts])
    # T4（2026-11-08）：账本**只读一次**、内存按账号聚合。原来每个账号各调
    # `today_totals` + `recent_totals`（两者都各自把整个 jsonl 读进内存解析），
    # 100 账号 × 每 4 秒轮询 = 每 4 秒 200 次全量读 → 面板卡。
    totals = gledger.batch_totals(ledger_path, [a.label for a in accounts])
    out: list[dict[str, Any]] = []
    for acc in accounts:
        today = totals[acc.label]["today"]
        # 近 24 小时（展示用）：刷课常跨零点，用户要看到「这一趟学了几节」（2026-11-03）
        recent = totals[acc.label]["recent"]
        cached, cached_course = _pick_sections_cache(debug_dir, acc.label)
        cached_rows = cached.get("rows") or []
        summary = cached.get("summary") or {}
        course = next((r.get("category") for r in cached_rows
                       if r.get("category")), "")
        # G-11②（2026-11-08 用户拍板）：账号表行内「在跑的迷你卡」。
        # 「正在学哪一节」从节缓存里 state=="learning" 的行取 —— 这是
        # run_gxks.py 的 tick 每 30 秒打的标记（修复 A 后不会被 progress 回降），
        # **零平台请求、零新接口**。没有 learning 行（任务刚起/已停）就留空。
        running = next((r for r in cached_rows if r.get("state") == "learning"), None)
        running_section = {
            "name": str(running.get("name") or ""),
            "done_minutes": int(running.get("done_minutes") or 0),
            "required_minutes": int(running.get("required_minutes") or 0),
            "course": str(running.get("category") or cached_course or ""),
        } if running else None
        # 这个账号一共有几门课的缓存（多课程时卡片要能看出来）
        courses_cached = len(sections_cache.list_all(debug_dir, acc.label))
        # **缓存完整性**（2026-11-08 用户实拍：test 显示「7/8 节」而平台实际 36 节）。
        # 判据：完整读列表的任务（`gxks_sections.py --all`）一定会写 `pager` 字段；
        # 只有 `run_gxks.py` 逐节回写产生的缓存**没有 pager**，行数只等于它经手过的节。
        # 所以 `无 pager` = 这份缓存不完整，前端要提示「点刷新节列表」而不是假装 8 节就是全部。
        sections_partial = bool(cached_rows) and not cached.get("pager")
        snap = gbrowser.state_summary(gbrowser.state_path(settings, acc.label))
        # `left = None` = 该账号把日节数设成 0（不限），卡片要显示“不限”而不是“0 剩”
        left = None if acc.daily_sections <= 0 else max(0, acc.daily_sections - today["sections"])
        out.append({
            "label": acc.label,
            "display": acc.display or ("主账号" if acc.label == "default" else acc.label),
            "enabled": acc.enabled,
            "port": acc.port,
            "port_alive": bool(alive.get(acc.port)),
            # 账号表里该账号自己的 name/pw；旧版全局 account.local.json 在多账号下会全部假 “已配”
            "credentials": bool(acc.configured),
            "limits": {"sections": acc.daily_sections, "minutes": acc.daily_minutes},
            "today": {"sections": today["sections"], "minutes": today["minutes"],
                      "left_sections": left,
                      # 按课程拆分只作展示（A1：额度按账号合计，不按课程分池）
                      "by_course": today.get("courses") or {}},
            # 近 24 小时（展示口径）：跨零点的刷课能看出「这一趟学了几节」。
            # ⚠ **不用于额度判定**（额度必须按墙钟日，见 ledger.recent_totals 的说明）。
            "recent24": {"sections": recent["sections"], "minutes": recent["minutes"],
                         "hours": recent["hours"]},
            "snapshot": snap,
            "course": course,
            "course_value": cached_course,      # 缓存对应的课程 value（多课程时区分用）
            "courses_cached": courses_cached,
            "sections": {"done": summary.get("done", 0),
                         "total": summary.get("sections", 0),
                         "read_at": cached.get("read_at", "")},
            # 缓存不完整（只由刷课逐节回写、没跑过「刷新节列表」）→ 前端提示去刷新
            "sections_partial": sections_partial,
            "running_section": running_section,          # G-11②：行内迷你卡（仅在跑时有值）
            "job": ({"kind": jobs[acc.label]["kind"], "since": jobs[acc.label]["since"]}
                    if acc.label in jobs else None),
        })
    return out


def _record(settings: Settings, label: str, action: str, detail: str,
            result: str = "ok", payload: dict | None = None) -> None:
    """写一条 `gx_ops`。**记日志失败不能把主流程打断**（面板只管展示，不影响干活）。"""
    db = open_question_bank(settings.data_dir / "gxks_questions.db")
    try:
        gxks_ops.record_op(db, account_label=label, action=action, result=result,
                           detail=detail[:200], payload=payload)
    except Exception:                                      # noqa: BLE001
        pass
    finally:
        db.close()


def _kill_pid(pid: int) -> str:
    """按 pid 杀进程树。**只对面板自己拉起的 pid 调用**（否则会误杀日常 Chrome）。"""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(int(pid))],
                           capture_output=True, timeout=20)
        else:
            os.kill(int(pid), 15)
        return "已停止"
    except Exception as exc:                                # noqa: BLE001
        return f"停止失败：{exc}"


# ------------------------------------------------------- 浏览器登记（面板重启后仍认得出）

def _browser_registry_path(settings: Settings) -> Path:
    """浏览器登记文件：面板重启后靠它把「自己起的 Chrome」认回来。

    ## 为什么需要（2026-11-06 用户实拍）

    `_BROWSERS` 是**内存表**。面板一重启就空了 → 自己刚拉起的 Chrome 变成
    「不是面板起的」：界面上「停止」按钮灰着，接口也不敢杀，只能让用户手动关窗口。
    与任务登记（`gxks_jobs.json`）同一思路，落盘即可跨重启。
    """
    return settings.data_dir / "gxks_browsers.json"


def _read_browser_registry(settings: Settings) -> dict[str, Any]:
    p = _browser_registry_path(settings)
    try:
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_browser_registry(settings: Settings, data: dict[str, Any]) -> None:
    p = _browser_registry_path(settings)
    try:
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        pass


def _remember_browser(settings: Settings, label: str, pid: int,
                      port: int, profile: Path | str) -> None:
    """记下「面板为这个账号起了 Chrome」（内存 + 落盘）。"""
    info = {"pid": int(pid), "port": int(port), "profile": str(profile),
            "since": time.strftime("%H:%M:%S"),
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    with _BROWSER_LOCK:
        _BROWSERS[label] = dict(info)
    data = _read_browser_registry(settings)
    data[label] = info
    _save_browser_registry(settings, data)


def _forget_browser(settings: Settings, label: str) -> None:
    data = _read_browser_registry(settings)
    if data.pop(label, None) is not None:
        _save_browser_registry(settings, data)


def _browser_owner_pid(settings: Settings, label: str, port: int) -> int:
    """面板为该账号起的 Chrome 进程 pid；认不出返回 0（调用方不得杀）。

    判据两道，**任一成立即可**：

    ① **登记过**（内存或落盘）+ 现场证据对得上 `gchrome.owner_pid()`
       —— 正常路径，pid 复用也拦得住；
    ② 没登记但**现场证据成立**：`data/profiles/<label>` 是本工具专用目录，
       它的 `lockfile` 正被一个 chrome.exe 占着、且该进程就在听这个调试端口。
       这条专门兜「登记表是后来才加的、旧 Chrome 没有登记」的情况
       （用户 2026-11-06 实拍：09:13:59 起的 Chrome 认不出来）。
     """
    acc = gaccounts.find_by_label(gaccounts.load_accounts(), label)
    with _BROWSER_LOCK:
        own = _BROWSERS.get(label)
    if own is None:
        own = _read_browser_registry(settings).get(label)

    if isinstance(own, dict):
        try:
            profile = Path(str(own.get("profile")
                                or gbrowser.cdp_profile(settings, label, acc)))
        except (gbrowser.SessionError, OSError, ValueError):
            profile = None
        if profile is not None:
            live = gchrome.owner_pid(profile, int(own.get("port") or port))
            if live > 0:
                return live
            # 证据链不完整（端口还没起来 / 锁文件被清）时，退回登记 pid + 镜像名守卫
            pid = int(own.get("pid") or 0)
            if pid and runmark.pid_alive(pid):
                image = gchrome.process_image(pid)
                if image and "chrome" in Path(image).name.lower():
                    return pid

    # ② 没登记也能靠现场证据认定（本工具专用 profile）
    return _owned_pid(settings, label, acc, port)


def _browser_registered_alive(own: dict[str, Any] | None) -> bool:
    """登记过的浏览器是否**还活着**（决定「停止」按钮能不能点）。

    只判 pid，不探端口：端口探测在本机可能因防火墙规则吃满一个 timeout，
    拿它当「活没活」会让按钮每 4 秒闪一次。pid 复用由镜像名兜一道。
    """
    if not isinstance(own, dict):
        return False
    pid = int(own.get("pid") or 0)
    if pid <= 0:
        return True
    if not runmark.pid_alive(pid):
        return False
    image = gchrome.process_image(pid)
    return (not image) or ("chrome" in Path(image).name.lower())


def _owned_pid(settings: Settings, label: str, acc: Any, port: int) -> int:
    """只看现场证据，判断这个账号的专用 profile 是否正被 Chrome 占着该端口。

    `data/profiles/<label>` 是**本工具专用**目录（用户日常 Chrome 用自己的 profile），
    所以「这份 profile 的 lockfile 被占 + 该端口在听 + 听的是 chrome.exe」
    就足以断定是本工具起的，无需依赖内存/落盘登记。
    """
    if not label or not port:
        return 0
    try:
        profile = gbrowser.cdp_profile(settings, label, acc)
    except (gbrowser.SessionError, OSError, ValueError):
        return 0
    return gchrome.owner_pid(profile, port)


def adopt_orphan_browsers(settings: Settings) -> list[dict[str, Any]]:
    """启动时把「面板起过、现在还在跑」的 Chrome 认回 `_BROWSERS`。

    ## 两个来源，缺一不可（2026-11-06）

    ① **落盘登记**：正常路径。面板重启后照登记把 pid 认回来；
    ② **现场证据**：兜底。登记表是这次才加的 —— 之前那个版本起的 Chrome
       没有登记（用户实拍的就是这种：09:13:59 起，面板 09:13:59 也重启了）。
       光靠 ① 会让用户以为「还是修不好」，所以对账号表里每个账号再探一次
       `_owned_pid()`：认得出就补登记，认不出才放弃。

    认定不了的旧条目**顺手清掉**，不留垃圾 —— 否则下次还会拿一个死 pid 去猜。
    """
    data = _read_browser_registry(settings)
    adopted: list[dict[str, Any]] = []
    stale: list[str] = []

    for label, info in list(data.items()):
        if not isinstance(info, dict):
            stale.append(label)
            continue
        try:
            pid = int(info.get("pid") or 0)
            port = int(info.get("port") or 0)
            profile = Path(str(info.get("profile") or ""))
        except (TypeError, ValueError):
            stale.append(label)
            continue
        # 先信登记的 pid（活着且确实是 chrome）；pid 换了（Chrome 自更新重启、
        # 或启动脚本没回报 pid）再用现场证据找真正占着这份 profile 的那个。
        live = pid if _browser_registered_alive({"pid": pid}) else 0
        if live <= 0 and port:
            live = gchrome.owner_pid(profile, port) if profile else 0
        if live <= 0:
            stale.append(label)
            continue
        with _BROWSER_LOCK:
            _BROWSERS[label] = {**info, "pid": live}
        adopted.append({"label": label, "pid": live, "port": port})
    if stale:
        for label in stale:
            data.pop(label, None)
        _save_browser_registry(settings, data)

    # ② 现场证据兜底：没登记、但本工具 profile 的 Chrome 正占着端口的，补登。
    known = set(data) | {a["label"] for a in adopted}
    try:
        rows = _account_rows()
    except Exception:                                      # noqa: BLE001
        rows = []
    # 端口探活必须**并发**：单次探测最坏一个 timeout，串行 7 个账号要把面板
    # 启动拖慢好几秒（同 `chrome.ports_alive` 的理由）。
    alive = gchrome.ports_alive([int(r.get("port") or 0) for r in rows])
    for row in rows:
        label = str(row.get("label") or "")
        port = int(row.get("port") or 0)
        if not label or label in known or not alive.get(port):
            continue
        acc = gaccounts.find_by_label(gaccounts.load_accounts(), label)
        pid = _owned_pid(settings, label, acc, port)
        if pid <= 0:
            continue
        try:
            profile = gbrowser.cdp_profile(settings, label, acc)
            since = time.strftime("%H:%M:%S",
                                  time.localtime((Path(str(profile)) / "lockfile")
                                                 .stat().st_mtime))
        except (OSError, ValueError, gbrowser.SessionError):
            profile = ""
            since = time.strftime("%H:%M:%S")
        _remember_browser(settings, label, pid, port, profile or "")
        with _BROWSER_LOCK:
            if label in _BROWSERS:
                _BROWSERS[label]["since"] = since
        adopted.append({"label": label, "pid": pid, "port": port})

    return [{"label": a["label"], "pid": a["pid"], "port": a["port"]}
            for a in adopted]


def _browser_state(settings: Settings, label: str,
                   alive: bool | None = None) -> dict[str, Any]:
    """浏览器/会话的**本地**状态：只探本地调试端口，**不访问平台**。

    `alive` 允许调用方把已探好的结果传进来（`/api/state` 会一次性并发探活所有端口，
    这里再探一遍就是重复劳动，而且每次都要等一个 timeout）。
    """
    acc = gaccounts.find_by_label(gaccounts.load_accounts(), label)
    backend = gbrowser.backend_of(settings)
    chrome = gchrome.find_chrome()
    try:
        managed_dir = bool(label) and gbrowser.profile_dir(settings, label).exists()
        # cdp 用哪个 user-data-dir：账号表显式配了就用它，否则默认 data/profiles/<代号>
        cdp_dir = gbrowser.cdp_profile(settings, label, acc) if label else None
    except gbrowser.SessionError:
        managed_dir, cdp_dir = False, None
    with _BROWSER_LOCK:
        own = _BROWSERS.get(label)
    if own is None:                      # 面板重启过：落盘登记仍然有效
        own = _read_browser_registry(settings).get(label)
    port_alive = (bool(alive) if alive is not None
                  else (gchrome.port_alive(acc.port) if acc else False))
    return {
        "backend": backend,
        "chrome": str(chrome) if chrome else "",
        "chrome_ok": chrome is not None,
        "port": acc.port if acc else 0,
        "port_alive": port_alive,
        "profile_set": bool(acc and acc.profile),
        "profile": str(cdp_dir) if cdp_dir else "",
        "profile_name": (Path(acc.profile).name if acc and acc.profile
                         else (f"data/{gbrowser.PROFILE_ROOT}/{label}" if cdp_dir else "")),
        "managed_profile": managed_dir,
        # 登记过 **且** 进程还活着才算「面板启动」：Chrome 关掉后登记会残留，
        # 光看 bool(own) 会一直撒谎（用户 2026-11-06 实拍里那句「面板启动 · 09:13:59」）。
        # 只判 pid 不探端口：端口探测会吃满 timeout，按钮会每 4 秒闪一次。
        "started_by_panel": _browser_registered_alive(own),
        "since": (own or {}).get("since", ""),
        "can_start": backend == "cdp" and chrome is not None and cdp_dir is not None,
        # 登录态快照：本站 cookie 全是会话级，关浏览器就没了 → 只能靠这份文件兜底
        "state": gbrowser.state_summary(gbrowser.state_path(settings, label)) if label else {},
        # ⚠ 凭据必须按**账号**判（`acc.configured`），不能读全局 account.local.json。
        # 那个文件是单账号时代的遗留：多账号下所有账号会显示同一个状态，
        # 与表格里按账号算的 `credentials` 自相矛盾（2026-11-02 查出的真 bug）。
        "credentials": bool(acc.configured) if acc else False,
        "session": _read_json(_latest(f"session_{label}.json",
                                      settings.data_dir / "debug")) if label else None,
    }


def _latest(pattern: str, directory: Path) -> Path | None:
    files = sorted(directory.glob(pattern), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def _read_json(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------- 单账号动作（批量复用）
#
# 抽成模块级函数是为了让 `/api/study`、`/api/stop` 与批量的 `/api/accounts/batch`
# 走**同一段逻辑**——否则批量那条路早晚会跟单个那条走偏（比如漏掉"在跑就不重复起"）。


def _require_browser_port(settings: Settings, acc: Any,
                          alive: bool | None = None) -> dict[str, Any] | None:
    """起任务前先确认浏览器还开着；没开就返回一条**可照做**的拒绝回包，否则 None。

    ## 为什么要提前拦（2026-11-06 用户问「多个账号要一个个点吗」时发现）

    `run_gxks.py` 第一步就是 `open_session()` 连 CDP。浏览器没开时它会

    ```
    [FAIL] 连不上 CDP http://127.0.0.1:9222
    ```

    然后**立刻退出**（`return 2`）。面板这边已经 `ok:true` 提示「开始刷课」了，
    用户看到的是「启动了但什么都没发生」，日志里躺着一行失败。

    批量启动把这个坑放大 N 倍：7 个账号里有几个浏览器没开，就是几个白起的进程。

    `refresh_sections` 早就做了这件事（2026-11-03），但**单账号学习和批量学习
    这两条主路反而漏了** —— 说明只在一处手写判断迟早会漏，所以抽成函数共用。

    managed 后端不探测：那种形态下浏览器由脚本自己起，端口本来就不该有人听。

    `alive` 允许批量调用方把**一次并发探好**的结果传进来：串行探 N 个账号
    最坏 N × 0.5 秒（见 `chrome.port_alive` 的 timeout 说明），7 个账号能拖 3.5 秒。
    """
    if gbrowser.backend_of(settings) == "managed":
        return None
    if alive is None:
        try:
            alive = gchrome.port_alive(acc.port)
        except Exception:                                  # noqa: BLE001  探活失败按没开处理
            alive = False
    if alive:
        return None
    name = acc.display or acc.label
    # profile 目录**不能写死** `data/profiles/<label>`：账号表允许自定义 `profile`，
    # 写死的话给的启动命令会开错目录 —— 用户照着敲只会得到一个没有登录态的浏览器。
    try:
        profile = gbrowser.cdp_profile(settings, acc.label, acc)
    except (gbrowser.SessionError, ValueError, OSError):
        profile = Path("data") / "profiles" / acc.label
    return {"ok": False, "need_browser": True,
            "error": f"「{name}」的浏览器没启动（端口 {acc.port} 连不上）。\n"
                     "先在右侧「浏览器与会话」点「启动并登录」，或手动执行：\n"
                     f'"{gchrome.find_chrome() or "chrome.exe"}" '
                     f'--remote-debugging-port={acc.port} '
                     f'--user-data-dir="{profile}"'}


def _live_study_count() -> int:
    """当前**正在跑的 study 任务数**（并发闸的唯一依据）。

    只数 `kind == "study"`：节列表刷新、练习作答虽然也是子进程，但它们是
    **短任务**（1 分钟 / 几分钟），且受「该账号已有任务在跑」那条约束挡住，
    不占学习并发位。若把它们也计入，用户点一次「刷新节列表」就可能把学习挤掉。
    """
    with _JOBS_LOCK:
        live = [j for j in _JOBS.values()
                if j.get("kind") == "study" and j["proc"].poll() is None]
    return max(0, len(live))


def _concurrency_limit(settings: Settings) -> int:
    """读「同时学习上限」（1~20，默认 2）。读不到就回 2 —— 闸宁严勿松。"""
    try:
        n = int(gxks_panel_settings.load(_db_path(settings))
                .get("max_concurrency", 2))
    except Exception:                                      # noqa: BLE001
        return 2
    return max(1, min(20, n))


def _start_jitter(settings: Settings) -> float:
    """读「启动错峰」秒数（0~120，默认 30）。0 = 不错峰。"""
    try:
        n = float(gxks_panel_settings.load(_db_path(settings))
                  .get("start_jitter", 30) or 0)
    except Exception:                                      # noqa: BLE001
        return 30.0
    return max(0.0, min(120.0, n))


def _sections_per_run(settings: Settings) -> int:
    """读「本次跑几节」的默认值（1~12，默认 3）。

    ## 为什么要有这一项（2026-11-07 用户问「默认 3 和面板设置项是否同步」）

    原来「3」在三个地方各写死一份：
    ① 表格行内输入框 `SEC_N` 的初值（`page.py` `Math.min(3, cap)`）；
    ② 批量「启动并学习」不弹框，后端 `_start_and_study_one` 里 `int(max_sections or 3)`；
    ③ 批量 `start` 的队列兜底 `int(body.get("max_sections") or 3)`。

    结果就是**改一处另外两处不变**，用户在面板设置里找不到这一项、也不知道哪里能改。
    现在统一读数据库里的 `sections_per_run`，前端 `/api/state` 带下来。

    ⚠ 与「账号日额度」（`daily_sections`）不同：
      `daily_sections` = 这个账号**一天**最多几节（账号表属性，各账号可不同）；
      `sections_per_run` = 点一次「学习」**这一次**最多跑几节（全局默认，行内可覆盖）。
    两者取小（11-04 定的口径），脚本侧 `run_gxks.py` 再兜一层。
    """
    try:
        n = int(gxks_panel_settings.load(_db_path(settings))
                .get("sections_per_run", 3) or 3)
    except Exception:                                      # noqa: BLE001
        return 3
    return max(1, min(12, n))


def _check_concurrency(settings: Settings) -> dict[str, Any] | None:
    """并发闸：已满就返回拒绝回包，否则 None。

    ## 为什么是「拒绝」而不是「排队」（P2a，2026-11-06 与用户确认）

    完整队列要求常驻调度循环 + 面板重启后重建队列（与孤儿任务认领同类问题），
    改动大。P2a 先做**软闸**：满了就明确告诉用户「已满 X 个，请先停一个或调高上限」，
    行为可预期、不加后台循环。队列留作 P2b。

    ## 为什么默认是 2 而不是方案里的 10

    `PLAN-GXKS.md` §7.3 的风险页自己写了「配置写 10，但首日先手动压到 2~3 跑通，
    确认无 503/验证码再放开」。而 D12 明确核实期只能 1 个会话 —— 并发面在**本站完全未验证**。
    默认保守、由用户在面板上调，比默认激进再出事要正确。
    """
    limit = _concurrency_limit(settings)
    live = _live_study_count()
    if live < limit:
        return None
    return {"ok": False, "blocked": True, "concurrency_full": True,
            "live": live, "limit": limit,
            "error": f"同时学习上限是 {limit} 个，现在已经有 {live} 个在跑。\n"
                     "要么先「停止」一个，要么到「账号设置 → 面板设置」把上限调高。"}


def _fleet_daily_limit(settings: Settings) -> int:
    """读「全部账号合计日时长上限」（分钟，0 = 不限）。"""
    try:
        n = int(gxks_panel_settings.load(_db_path(settings))
                .get("fleet_daily_minutes", 0) or 0)
    except Exception:                                      # noqa: BLE001
        return 0
    return max(0, min(1440, n))


def _fleet_minutes_today(settings: Settings) -> float:
    """今日**所有账号合计**已学分钟数（账本口径，与 `fleet_daily_minutes` 配对展示）。"""
    try:
        total = gledger.today_totals(_ledger_path(settings), account="")
        return round(float(total.get("minutes") or 0), 1)
    except Exception:                                      # noqa: BLE001
        return 0.0


def _check_fleet_daily(settings: Settings) -> dict[str, Any] | None:
    """全局日总量闸：**所有账号今日合计时长**已达上限就拒绝，否则 None。

    ## 与「账号日额度」的区别（`PLAN-GXKS.md` §11.6 / 额度口径表）

    | 闸 | 维度 | 防的是 |
    |---|---|---|
    | 账号 `daily_sections` / `daily_minutes` | **单账号** | 一个号一天学 8 小时很可疑 |
    | `fleet_daily_minutes`（本项） | **全部账号合计** | 这台机器/出口 IP 一天总时长异常 |

    默认 **0 = 不限**（方案标注"建议不开，但要有"）：它是最粗的一刀，
    开得太小会让前几个账号把配额吃光、后面的号一天都跑不了。

    ## 为什么用**账本**而不是"实时计时"

    账本（`data/gxks_ledger.jsonl`）是**唯一权威**（模块 docstring 早就定了）。
    实时计时会漏掉"刚跑完的那节还没记账"与"面板重启前的消耗"，
    而账本在每次任务收尾时写、且跨进程可见。
    """
    limit = _fleet_daily_limit(settings)
    if limit <= 0:
        return None
    # 复用 `_fleet_minutes_today`，别再写一遍账本读取 ——
    # 两处各写一份的话，将来改口径（比如含未记账的当场消耗）必然只改一处。
    used = _fleet_minutes_today(settings)
    if used < limit:
        return None
    return {"ok": False, "blocked": True, "fleet_full": True,
            "used": round(used, 1), "limit": limit,
            "error": f"今天所有账号合计已学 {used:.0f} 分钟，达到全局上限 {limit} 分钟。\n"
                     "要再跑就去「账号设置 → 面板设置」调高「全局日时长上限」，"
                     "或今天到此为止（明天自动清零）。"}


def _study_one(settings: Settings, label: str,
               max_sections: object = None, sections: object = None,
               port_alive: bool | None = None,
               check_concurrency: bool = True,
               jitter: float = 0.0) -> dict[str, Any]:
    """给一个账号起刷课子进程。返回面板回包（`ok`/`error`/`message`）。

    `port_alive` 供批量调用方传入**已经并发探好**的结果（见 `_require_browser_port`）。
    `check_concurrency` 供批量调用方关掉**逐个**判定：批量要先按上限算出「这一批能起几个」，
    否则逐个判会一直看到同一个 `live` 数，起不到闸的作用。
    `jitter` = 开跑前随机等 0~N 秒（**批量错峰**，P2a）。单个账号不传（默认 0，立即开跑）。
    """
    acc = _account(settings, label)
    if any(j["label"] == acc.label for j in _jobs_snapshot()):
        # `skipped` 与「失败」分开：批量启动时「已在跑」是**预期内的跳过**，不是错误。
        # 混在一起会让汇总显示「2 个没做成」，用户以为出问题了（2026-11-06）。
        return {"ok": False, "skipped": True,
                "error": "该账号已有任务在跑"}
    # ---- 账号会话锁（A1，2026-11-07）：跨面板 / 跨命令行防双开 ----
    # `_JOBS` 只是**本进程的内存表**，挡不住「另一个面板」或「手敲 run_gxks.py」。
    # 这里查落盘锁；脚本自己启动时也会再查一次（脚本那边的判据含"自己人"）。
    held = glocks.holder(settings.data_dir, acc.label)
    if held is not None:
        return {"ok": False, "skipped": True, "locked": True,
                "error": f"账号 {acc.label} 已有会话在跑（{glocks.describe(held)}），"
                         "可能来自另一个面板或命令行。先在那边停掉再试"}
    refused = _require_browser_port(settings, acc, port_alive)
    if refused:
        return refused
    if check_concurrency:
        full = _check_concurrency(settings)
        if full:
            return full
        # 全局日总量闸（P2b）：所有账号合计今天已学够 → 谁也别再起。
        # 与并发闸并列放在这里：两者都是"起之前"的拒绝，不该等到跑起来才发现。
        fleet = _check_fleet_daily(settings)
        if fleet:
            return fleet
    # 「本次跑几节」：调用方给了就用，没给就读面板设置里的 `sections_per_run`
    # （原来写死 3，与面板设置、与表格行内默认值三处不同步 —— 2026-11-07 修）。
    n = int(max_sections or _sections_per_run(settings))
    args = ["--label", acc.label, "--account-label", acc.label,
            "--port", str(acc.port),
            "--max-sections", str(max(1, min(n, 12)))]
    if sections:
        args += ["--sections", str(sections)]
    if jitter > 0:
        # 错峰必须**在子进程里**等（`run_gxks.py --start-jitter`），不能在面板里 sleep：
        # 面板阻塞会把整个 api 卡住，批量 5 个就是几分钟无响应。
        args += ["--start-jitter", f"{jitter:g}"]
    return _start(settings, acc.label, "study", "run_gxks.py", *args,
                  message=(f"开始刷课，最多 {n} 节；随时可停"
                           + (f"（错峰：先随机等最多 {jitter:g} 秒）" if jitter > 0 else "")))


def _stop_one(settings: Settings, label: str) -> dict[str, Any]:
    """停一个账号正在跑的任务，并把它从队列里撤掉。"""
    label = _safe_label(settings, label)
    # **排队中的也要撤**（P2b，2026-11-06）：用户点「停止」的意思是"这个账号别跑了"。
    # 如果只杀进程不管队列，排队中的那个正好被后台循环拉起来 —— 会出现
    # 「点了停止，20 秒后它自己又开始了」这种最让人火大的行为。
    queued = _queue_drop(settings, label)
    with _JOBS_LOCK:
        # 该账号可能有**多个**任务（study / sections / work，键是 `label|kind`），
        # 停**每一个**还在跑的（用户点「停止」的意思是「把这个账号的活都停下」）。
        live = [(k, j) for k, j in _JOBS.items()
                if j["label"] == label and j["proc"].poll() is None]
        # 已结束的任务留在 `_JOBS` 里等 `_jobs_snapshot` 收进 `_LAST_JOBS` 回看；
        # 但**不能被当成活的去停**（否则会对一个已死的 pid 报「已停止」，
        # 用户以为停了，实际什么都没发生 —— 2026-11-03 测试抓到）。
    if not live:
        # 没在跑，但**可能是在排队**：撤掉排队也算"停止成功"（用户要的效果达成了）。
        if queued:
            return {"ok": True, "message": "已取消排队（它本来还没开始跑）"}
        # 面板内存表里没有，但**落盘锁**可能被别的入口（另一个面板 / 命令行）持有 ——
        # 面板不该去杀不认识的进程（会误杀），但必须说清楚，否则用户以为「点了停止就好了」。
        held = glocks.holder(settings.data_dir, label)
        if held is not None:
            return {"ok": False, "skipped": True, "locked": True,
                    "error": f"该账号有会话在跑（{glocks.describe(held)}），"
                             "但不是本面板发起的 → 面板不会去杀它。"
                             "请到那个窗口 / 进程里停（或在任务管理器结束它）"}
        # 与「开始学习」遇到在跑账号对称：这是**预期内的跳过**，不是错误。
        # 不区分的话，批量停 5 个账号里有 4 个本来空闲，汇总会报「4 个没做成」弹红条
        # （2026-11-06 用户问「停止/启用/停用是否也能批量」时实测发现）。
        return {"ok": False, "skipped": True,
                "error": "该账号没有在跑的任务"}
    results = []
    for key, job in live:
        results.append(_kill(job["proc"]))
        with _JOBS_LOCK:
            _JOBS.pop(key, None)
        # 停止是**显式终结**：登记表里也要删，否则下次启动会拿这个 pid 去认领
        _drop_job_registry(settings.data_dir, job["label"], job["kind"])
        # 账号会话锁同理（A1，2026-11-07）：只删「这条任务自己写的」那一条。
        # 不校验 pid 的话，杀掉旧任务可能把**新会话**的锁抹掉 → 又能双开。
        try:
            proc_pid = int(getattr(job["proc"], "pid", 0) or 0)
            glocks.release(settings.data_dir, job["label"], pid=proc_pid or None)
        except Exception:                                  # noqa: BLE001
            pass
    result = "、".join(results)
    db = open_question_bank(settings.data_dir / "gxks_questions.db")
    try:
        gxks_ops.record_op(db, account_label=label, action="stop",
                           detail=f"面板手动停止（{result}）")
    finally:
        db.close()
    return {"ok": True, "message": result}


def _db_path(settings: Settings) -> Path:
    """gxks 题库路径（AI 配置也存这里，与超星库完全分开）。

    用 `settings.data_dir` 而不是 `find_project_root()`：测试数据目录是临时目录，
    用 settings 才能隔离；写死项目根会让测试污染真实库。
    """
    return settings.data_dir / "gxks_questions.db"


def app_factory() -> FastAPI:
    """**无参**工厂：给 uvicorn 的 `reload=True` 用（2026-11-03）。

    uvicorn 在 reload 模式下按导入字符串重新 import 并**无参**调用工厂，
    而 `create_app(settings)` 需要参数 → 直接传它会报
    `create_app() missing 1 required positional argument`。
    这里在子进程里重新加载配置，保证热重载后读到的是最新配置。
    """
    from ..config import Settings
    return create_app(Settings())


def _browser_start_one(settings: Settings, label: str, *,
                       run_session: Any) -> dict[str, Any]:
    """为一个账号拉起 Chrome 并弄好登录态。返回面板回包（`ok`/`skipped`/`error`）。

    ## 为什么抽成模块级（2026-11-06 P1）

    单个「启动并登录」与**批量启动**必须走同一段逻辑，否则两条路早晚分家
    （P0 那次 `refresh_sections` 自己手写端口检查、主路反而漏了，就是同一个教训）。

    ## 三类结果要分清（直接决定前端弹什么颜色）

    - `ok=True`：真起来了（或本来就在跑）；
    - `skipped=True`：**预期内跳过** —— 端口已经活着（已在运行）、或 managed 后端
      （容器形态本来就不该在这里起浏览器）；
    - 其余为真失败（Chrome 没找到 / 交互式任务不存在 / 有任务在跑）。

    `run_session` = `create_app` 内的 `_session_script`，以参数传入 —— 本函数在模块级。
    """
    acc = _account(settings, label)
    state = _browser_state(settings, acc.label)
    if state["backend"] != "cdp":
        return {"ok": False, "skipped": True,
                "error": "当前是 managed 后端（容器形态）：面板内不起浏览器。"
                         "本机请把 config/settings.local.toml 里 browser_backend 改回 cdp"}
    if state["port_alive"]:
        # 端口已经活着 = 浏览器已在运行。对单个按钮来说这是"成功"（用户想要的状态已达成），
        # 但对批量来说它是**跳过**（没必要重复起）。这里统一标 skipped，
        # 由调用方决定怎么呈现（单个接口会把 skipped 当成 ok 返回，见 browser_start）。
        return {"ok": False, "skipped": True,
                "message": f"端口 {acc.port} 已在运行"}
    if any(j["label"] == acc.label for j in _jobs_snapshot()):
        return {"ok": False, "blocked": True,
                "error": "该账号有任务在跑，先「停止」再启动浏览器"}
    started = _start_chrome_interactive(acc.label, acc.port)
    if not started.get("ok"):
        return {"ok": False, "error": started.get("error") or "Chrome 没起来"}
    _remember_browser(settings, acc.label, int(started.get("pid") or 0),
                      acc.port, gbrowser.cdp_profile(settings, acc.label, acc))
    _record(settings, acc.label, "browser-start",
            f"面板启动 Chrome（端口 {acc.port}，交互式任务）")
    return {"ok": True,
            "message": _chrome_start_message(settings, acc, acc.port, run_session)}


def _chrome_start_message(settings: Any, acc: Any, port: int,
                          run_session: Any) -> str:
    """Chrome 起来后把**登录态**弄好，拼一句给用户看的结果。

    `run_session` = `create_app` 内的 `_session_script`（子进程跑会话脚本），
    以参数传入 —— 本函数在模块级，拿不到那个闭包。

    登录策略（2026-11-03 用户问「为何不能自动登录」）：
      ① 先注入快照（零密码、最快）；
      ② 票过期是**常态**（cookie 全是 session 级，`expires=-1`，关窗即失效）
         → 自动改用账号密码重登一次；
      ③ 遇验证码立刻停、转人工（绝不试错：试错=撞库特征）。
    """
    msg = f"Chrome 已启动（端口 {port}）"
    # ⚠ 凭据必须**按账号**判（2026-11-07 修 P0）：原来读全局 account.local.json，
    # 多账号下 A 配了凭据、B 没配，B 也会被判成"能自动登录" → 拿 A 的密码登 B（顶号）。
    has_cred = glogin.is_configured_for(acc, settings)
    state = gbrowser.state_summary(gbrowser.state_path(settings, acc.label))

    if state.get("saved"):
        out = run_session(acc, "--restore")
        parsed = out.get("parsed") or {}
        restored = int(parsed.get("restored") or 0)
        if out.get("ok") and parsed.get("logged_in"):
            # restored=0 表示**本来就在登录态**（脚本没走注入）—— 文案不能说"注入 0 条"
            if restored:
                return msg + f"，已注入登录态（{restored} 条 cookie）→ 可以直接刷课"
            return msg + "，当前已是登录状态 → 可以直接刷课"
        if not has_cred:
            return (msg + f"；快照票已失效（注入 {restored} 条仍不认），"
                          "且没配账号密码 —— 请在那台浏览器里手动登录一次")
        msg += f"；快照票已失效（注入 {restored} 条仍不认），改用账号密码自动登录…"

    if not has_cred:
        return msg + "；没有可用的登录态，也没配账号密码 —— 请手动登录一次"

    out = run_session(acc, "--relogin", timeout=180)
    p = out.get("parsed") or {}
    if out.get("ok") and p.get("logged_in"):
        return msg + "\n✅ 已用账号密码登录成功 → 可以直接刷课"
    if p.get("need_human"):
        return msg + "\n⚠ 平台要求输验证码：请在刚打开的浏览器窗口里手动登录一次"
    return msg + (f"\n⚠ 自动登录没成功（{p.get('relogin') or p.get('url') or '未知'}）；"
                  "请手动登录一次")


def create_app(settings: Settings) -> FastAPI:
    from .page import PAGE

    app = FastAPI(title="第二平台控制台", docs_url=None, redoc_url=None)
    debug_dir = settings.data_dir / "debug"

    # **认领孤儿任务**（2026-11-05）：面板重启后 `_JOBS`（内存表）是空的，
    # 但子进程用 `CREATE_NO_WINDOW` 脱离了面板进程树，**它还在跑**。
    # 不认领的话：任务照跑，面板却看不见、停不掉（用户 11-05 因此不敢重启面板）。
    # 认领失败（进程已死 / pid 被复用 / 日志没了）的登记会被顺手清掉。
    try:
        _adopted = adopt_orphan_jobs(settings)
    except Exception:                                      # noqa: BLE001
        _adopted = []

    # **认领自己起过的 Chrome**（2026-11-06 用户实拍「停止」灰着）：同一思路，
    # 否则重启后明明是面板拉起的浏览器，界面却说「不是面板起的」，按钮也不敢杀。
    try:
        adopt_orphan_browsers(settings)
    except Exception:                                      # noqa: BLE001
        pass

    # **恢复队列**（P2b，2026-11-06）：排队中的任务不是进程，重启后靠落盘文件重建。
    # 顺序很重要：必须在孤儿任务认领**之后**，否则会把"已经在跑"的账号又排一遍。
    try:
        adopt_queue(settings)
        if _queue_snapshot():
            _ensure_queue_worker(settings)     # 有排队才起后台循环，空队列不白跑
    except Exception:                                      # noqa: BLE001
        pass

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        # ⚠ 必须显式禁缓存（2026-11-02 用户反复"看不到新功能"的真因）：
        # 面板页面没有静态资源、没有指纹文件名，浏览器会按启发式规则缓存 HTML。
        # 结果每次改完页面，用户不清缓存就一直在看旧版本——排查时极易误判成"没实现"。
        return HTMLResponse(
            content=PAGE,
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )

    @app.get("/api/state")
    def state(label: str = "", course: str = "") -> dict:
        rows = _account_rows()
        if label:
            _safe_label(settings, label)          # 越权代号直接拒，不静默回落
        else:
            label = rows[0]["label"] if rows else ""
        ai = gxks_ai_config.load(_db_path(settings), data_dir=settings.data_dir)
        db = open_question_bank(settings.data_dir / "gxks_questions.db")
        try:
            ops = gxks_ops.recent_ops(db, account_label=label or None, limit=60)
        finally:
            db.close()
        # 端口探活**一次并发做完**，卡片与浏览器区共用同一份结果。
        # 不共用的话这个接口要探两轮，而单次探测最坏就是一个 timeout（见 chrome.port_alive）。
        alive = gchrome.ports_alive([r["port"] for r in rows])
        cur_port = next((r["port"] for r in rows if r["label"] == label), None)
        # 每个账号各算一份浏览器/会话状态，供前端**悬停预览**时直接切换
        # （用户 2026-11-02：「右侧栏上面的浏览器与会话……如果不一样，是不是也要跟随账号显示」）。
        # 数据本来就是按账号的（端口/profile/快照/会话检查），这里一次算全，避免悬停时再发请求。
        browsers = {r["label"]: _browser_state(settings, r["label"],
                                               alive=alive.get(r["port"]))
                    for r in rows}
        # B1：节列表按课程分开存；`course` 给了就读那一份，没给就读该账号最新的一份
        sections, sections_course = (_pick_sections_cache(debug_dir, label, course)
                                     if label else ({}, ""))
        # learning 行超 90 秒无 tick 心跳 → 任务大概率已中断，降级展示
        # （只影响展示，不改缓存文件；见 _normalize_stale_learning）
        sections = _normalize_stale_learning(sections)
        # 该账号**所有**课程的缓存清单（前端切换器用；2026-11-03 加）
        courses_cached = _list_course_caches(debug_dir, label) if label else []
        return {
            "ok": True,
            "accounts": rows,
            "cards": _account_cards(settings, alive=alive),
            "accounts_file": gaccounts.default_path().exists(),
            "label": label,
            "course": sections_course,
            "courses_cached": courses_cached,
            "auto_submit": bool(settings.raw.get("gxks", {}).get("auto_submit", False)),
            # 并发闸状态（P2a，2026-11-06）：批量栏要显示「并发 2/2」并在满时给红标签。
            # `live_study` 只数 study 任务（节列表/练习不占学习并发位，见 `_live_study_count`）。
            "limit_concurrency": _concurrency_limit(settings),
            "live_study": _live_study_count(),
            "start_jitter": _start_jitter(settings),
            # 「本次跑几节」的默认值（2026-11-07）：表格行内输入框与批量「启动并学习」
            # 共用这一个值，改面板设置两处跟着变（用户问过「默认 3 和设置项是否同步」）。
            "sections_per_run": _sections_per_run(settings),
            # 全局日总量闸（P2b）：批量栏要显示「今日合计/上限」，超了给红标签。
            "fleet_minutes_today": _fleet_minutes_today(settings),
            "fleet_limit_minutes": _fleet_daily_limit(settings),
            # 排队中的任务（P2b）：前端要显示「排队中 N 个」，并让用户能撤。
            "queue": _queue_snapshot(),
            # `include_finished=True`：刚结束的任务也带上（`alive: false`），
            # 这样页面能继续显示最后一次的日志尾巴（2026-11-03 用户要求）。
            # 其他接口仍用默认 False，不会把已结束的当"在跑"。
            "jobs": _jobs_snapshot(include_finished=True),
            "sections": sections or None,
            "work": _read_json(_latest(f"work_{label}_*.json", debug_dir)),
            "browser": (_browser_state(settings, label, alive=alive.get(cur_port))
                        if label else None),
            # 全部账号的浏览器/会话状态（悬停预览用；键 = 账号代号）
            "browsers": browsers,
            # AI 配置（厂商列表 + 当前选中 + **是否配了 Key**；Key 本身永不回显）
            "ai": ai,
            "ops": ops,
        }

    @app.post("/api/sections/refresh")
    def refresh_sections(body: dict) -> dict:
        acc = _account(settings, str(body.get("label") or ""))
        label = acc.label
        if any(j["label"] == label for j in _jobs_snapshot()):
            return {"ok": False, "error": "该账号已有任务在跑"}
        # **先探浏览器端口**（2026-11-03 实测的坑）：Chrome 没开时脚本要连 CDP 才报
        # `ECONNREFUSED`，表现是「点了刷新 → 任务跑一下 → 消失」，用户不知道要开浏览器。
        # 这里提前拦下，直接在按钮旁给出可照做的提示，不用白等一分钟。
        # 2026-11-06 抽成 `_require_browser_port`，与单账号/批量学习共用同一份判据。
        refused = _require_browser_port(settings, acc)
        if refused:
            return refused
        args = ["--label", label, "--port", str(acc.port), "--all"]
        course = sections_cache.safe_course(body.get("course"))
        if course:
            args += ["--course", course]
        return _start(settings, label, "sections", "gxks_sections.py", *args,
                      message=("正在读节列表（含翻页），约 1 分钟" if not course
                               else f"正在读课程 {course} 的节列表，约 1 分钟"))

    @app.post("/api/work")
    def do_work(body: dict) -> dict:
        acc = _account(settings, str(body.get("label") or ""))
        label = acc.label
        exercise_id = str(body.get("exercise_id") or "").strip()
        if not exercise_id:
            return {"ok": False, "error": "缺少练习 ID（先刷新节列表）"}
        if any(j["label"] == label for j in _jobs_snapshot()):
            return {"ok": False, "error": "该账号已有任务在跑"}
        args = ["--label", label, "--port", str(acc.port),
                "--exercise-id", exercise_id,
                "--section-id", str(body.get("section_id") or ""),
                "--section-name", str(body.get("section_name") or "")]
        mode = str(body.get("mode") or "").strip()
        if mode:
            args += ["--mode", mode]
        if body.get("no_fill"):
            args.append("--no-fill")
        return _start(settings, label, "work", "gxks_work.py", *args,
                      message="正在开卷并作答；填完会停下等你核对——交卷要你自己点")

    @app.post("/api/study")
    def do_study(body: dict) -> dict:
        """单账号开始学习。

        ## 并发满时**入队**而不是拒绝（P2b，2026-11-06）

        以前 `_study_one` 满了就回 `blocked: true`，用户得自己再点一次。
        现在改成：满了就进队列，等有空位由后台循环自动起 —— 这才是
        `PLAN-GXKS.md` §11.5 说的「否则按并发上限排队」。

        ⚠ 只在**把这个账号加进队列**后返回 `queued`，前端据此显示「排队中」。
        """
        label = str(body.get("label") or "")
        got = _study_one(settings, label, body.get("max_sections"),
                         body.get("sections"))
        if got.get("concurrency_full"):
            n = int(body.get("max_sections") or _sections_per_run(settings))
            _queue_add(settings, _account(settings, label).label,
                       max(1, min(int(n), 12)))
            _ensure_queue_worker(settings)
            return {"ok": True, "queued": True,
                    "message": f"{label} 已排队：现在并发已满（{got.get('live')}/"
                               f"{got.get('limit')}），等有账号跑完就自动开始"}
        if got.get("ok"):
            _ensure_queue_worker(settings)
        return got

    @app.post("/api/study/start-with-browser")
    def do_study_with_browser(body: dict) -> dict:
        """一键「启动并学习」：浏览器没起就先起，等就绪后再开始学习。

        用户 2026-11-06：「开始学习与启动并登录之间的关系是如何的？先登录才能学习吗？
        那如何解决选择的多个账号登陆问题呢？」—— 关系是**必须先有浏览器**，
        所以把两步合成一步，多账号就不用"起一个、等、再点学习"循环 N 遍。
        """
        label = str(body.get("label") or "")
        got = _start_and_study_one(settings, label,
                                   run_session=_session_script,
                                   max_sections=body.get("max_sections"))
        return got

    @app.post("/api/stop")
    def do_stop(body: dict) -> dict:
        return _stop_one(settings, str(body.get("label") or ""))

    @app.get("/api/browser/state")
    def browser_state(label: str = "") -> dict:
        # 空代号 = 看第一个账号（与 /api/state 同一口径），不能直接 400
        if not label:
            rows = _account_rows()
            label = rows[0]["label"] if rows else ""
        return {"ok": True, **_browser_state(settings, _safe_label(settings, label)
                                              if label else "")}

    @app.post("/api/browser/start")
    def browser_start(body: dict) -> dict:
        """拉起该账号自己的 Chrome（带它的调试端口与 user-data-dir）。

        只做本机形态：容器（managed）下浏览器由程序自己起，面板里没有窗口可给人用。
        """
        acc = _account(settings, str(body.get("label") or ""))
        got = _browser_start_one(settings, acc.label, run_session=_session_script)
        # 「端口已活着」对单个按钮是**成功**（用户想要的状态已经在了）：
        # 文案要带上登录态检查结果，与旧行为保持一致。
        if got.get("skipped") and got.get("message", "").startswith(f"端口 {acc.port} 已在运行"):
            return {"ok": True,
                    "message": got["message"] + "。"
                               + _chrome_start_message(settings, acc, acc.port, _session_script)}
        return got

    @app.post("/api/browser/stop")
    def browser_stop(body: dict) -> dict:
        """停掉面板为这个账号拉起的 Chrome。

        ## 归属判定改成「看证据」而不是「看内存」（2026-11-06 用户实拍）

        原来只读内存表 `_BROWSERS`，面板一重启就忘干净：明明是自己刚起的 Chrome，
        界面显示「不是面板起的」、按钮还点不动，只能让用户手动关窗口。
        现在 `gchrome.owner_pid()` 用 profile 里 `DevToolsActivePort` 的 uuid
        与端口 `/json/version` 对撞 —— 这份 profile 的 Chrome 正占着这个调试端口，
        才认；对不上就是别人的浏览器，一律不碰。
        """
        label = _safe_label(settings, str(body.get("label") or ""))
        acc = _account(settings, label)
        pid = _browser_owner_pid(settings, label, acc.port)
        if pid <= 0:
            with _BROWSER_LOCK:
                _BROWSERS.pop(label, None)
            _forget_browser(settings, label)
            return {"ok": False, "error": "这个浏览器不是面板起的，不敢盲杀（会误杀你日常的 "
                                          "Chrome）；请在那个窗口里手动关"}
        result = _kill_pid(pid)
        with _BROWSER_LOCK:
            _BROWSERS.pop(label, None)
        _forget_browser(settings, label)
        _record(settings, label, "browser-stop", f"面板停止 Chrome（{result}）")
        return {"ok": True, "message": result}

    def _session_script(acc: Any, *flags: str, timeout: int = 90) -> dict:
        """跑 `gxks_session_check.py`（check / --restore / --relogin 共用）。

        子进程跑而不是在面板进程里调 Playwright：同步 API 不能和 FastAPI 的事件循环
        同处一线程，而且脚本崩了不该把面板带走。只认**最后一行以 `{` 开头**的输出。
        """
        if getattr(sys, "frozen", False):
            return {"ok": False, "error": "打包版面板不支持起脚本（scripts/ 不在 exe 内）"}
        argv = [sys.executable, "-u", str(SCRIPTS / "gxks_session_check.py"),
                "--label", acc.label, "--port", str(acc.port), *flags]
        try:
            done = subprocess.run(argv, cwd=str(ROOT), capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=timeout)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"会话脚本超时（{timeout} 秒）"}
        parsed: dict | None = None
        for line in reversed((done.stdout or "").strip().splitlines()):
            if line.startswith("{"):
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError:
                    continue
                break
        if parsed is None:
            tail = ((done.stderr or "") + (done.stdout or "")).strip().splitlines()[-3:]
            return {"ok": False, "error": "没拿到结果：" + " / ".join(tail),
                    "exit_code": done.returncode}
        parsed["exit_code"] = done.returncode
        return {"ok": True, "parsed": parsed, "stderr": (done.stderr or "").strip()}

    @app.post("/api/session/check")
    def session_check(body: dict) -> dict:
        """真去看一次页面判登录态（唯一可靠判据）。**会访问平台**，所以只在点按钮时跑。"""
        acc = _account(settings, str(body.get("label") or ""))
        out = _session_script(acc)
        if not out["ok"]:
            return out
        parsed = out["parsed"]
        logged = bool(parsed.get("logged_in"))
        _record(settings, acc.label, "session-check",
                f"会话检查：{'已登录' if logged else '未登录'}（{parsed.get('url', '')[:60]}）",
                result="ok" if logged else "fail")
        return {"ok": True, "message": "已登录" if logged else "未登录（落到登录页）",
                "session": parsed}

    @app.post("/api/session/restore")
    def session_restore(body: dict) -> dict:
        """恢复登录：把快照里的 cookie 注入当前 Chrome，再探一次（零密码）。"""
        acc = _account(settings, str(body.get("label") or ""))
        state = gbrowser.state_summary(gbrowser.state_path(settings, acc.label))
        if not state.get("saved"):
            return {"ok": False, "error": "还没有登录态快照：先「检查会话」或「重新登录」存一份"}
        out = _session_script(acc, "--restore")
        if not out["ok"]:
            return out
        parsed = out["parsed"]
        logged = bool(parsed.get("logged_in"))
        restored = int(parsed.get("restored") or 0)
        # 三种真实情形要分开说：恢复成功 / 本来就登着（没走注入）/ 注入了也没用
        if logged and restored:
            msg = f"已恢复登录（注入 {restored} 条 cookie）"
        elif logged:
            msg = "本来就登录着，没有注入（顺手刷新了快照）"
        elif restored:
            msg = (f"注入了 {restored} 条 cookie 但服务端不认（那张票已作废）"
                   "——点「重新登录」")
        else:
            msg = "快照里没有可注入的平台 cookie——点「重新登录」"
        _record(settings, acc.label, "session-restore",
                f"恢复登录：注入 {restored} 条 cookie → {msg}",
                result="ok" if logged else "fail")
        return {"ok": True, "message": msg, "session": parsed}

    @app.post("/api/session/relogin")
    def session_relogin(body: dict) -> dict:
        """重新登录：用账号密码走真浏览器填表**一次**；遇验证码立即停机转人工。"""
        acc = _account(settings, str(body.get("label") or ""))
        # ⚠ 按账号判（2026-11-07 修 P0）：全局文件在多账号下会让所有账号都"有凭据"
        if not glogin.is_configured_for(acc, settings):
            return {"ok": False,
                    "error": f"账号 {acc.label} 没有可用的账号密码："
                             "请到「✎ 编辑」里给它填上自己的账号密码"
                             "（不要依赖全局 data/account.local.json，多账号会串号）"}
        out = _session_script(acc, "--relogin", timeout=150)
        if not out["ok"]:
            return out
        parsed = out["parsed"]
        logged = bool(parsed.get("logged_in"))
        need_human = bool(parsed.get("need_human"))
        why = str((parsed.get("relogin") or {}).get("result") or "")
        detail = ("需人工输验证码" if need_human else
                  ("已登录" if logged else f"未成功（{why or parsed.get('url', '')[:40]}）"))
        _record(settings, acc.label, "session-relogin", f"重新登录：{detail}",
                result="ok" if logged else "fail")
        if need_human:
            return {"ok": False, "need_human": True,
                    "error": "被推到验证码闸门：请在那台浏览器窗口里手动输验证码并登录"
                             "（脚本不会再试第二次）", "session": parsed}
        return {"ok": True, "message": "已重新登录" if logged else f"没成功：{detail}",
                "session": parsed}

    @app.get("/api/events")
    async def events() -> Any:
        """**SSE 事件流**：日志一写就推给前端（2026-11-04 用户要求「有就更新」）。

        事件类型：
        - `log`：某账号的日志**新增内容**（`text` 字段，可能含多行）；
        - `job-ended`：某账号的任务结束了（前端据此刷新一次整体状态）；
        - `ping`：保活（防止中间代理掐断空闲连接）。

        前端用 `new EventSource("/api/events")` 订阅。
        """
        from fastapi.responses import StreamingResponse

        _ensure_watcher()
        q: queue.Queue = queue.Queue(maxsize=256)
        with _EVENTS_LOCK:
            _EVENTS.add(q)

        async def gen():
            try:
                yield b": connected\n\n"
                while True:
                    # ⚠ **必须 `await asyncio.to_thread`**（2026-11-04 实测踩到）：
                    # 直接 `q.get(timeout=15)` 是**阻塞调用**，会**卡住整个事件循环**
                    # → 面板所有接口（含 `/api/state`）一起超时，表现为「面板卡死」。
                    # 实测就是这样：SSE 连上后 `/api/state` 直接 timeout。
                    # 正确做法是把阻塞读挪到线程池，等价于「不阻塞事件循环地等消息」。
                    try:
                        payload = await asyncio.to_thread(q.get, True, 15.0)
                    except queue.Empty:
                        yield b": ping\n\n"       # 保活
                        continue
                    data = json.dumps(payload, ensure_ascii=False)
                    yield f"event: {payload.get('type', 'log')}\ndata: {data}\n\n".encode()
            except asyncio.CancelledError:              # 客户端断开
                raise
            finally:
                with _EVENTS_LOCK:
                    _EVENTS.discard(q)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "Connection": "keep-alive",
                                          "X-Accel-Buffering": "no"})

    @app.get("/api/ops")
    def ops(label: str = "", limit: int = 100) -> dict:
        if label:
            _safe_label(settings, label)
        db = open_question_bank(settings.data_dir / "gxks_questions.db")
        try:
            return {"ok": True, "rows": gxks_ops.recent_ops(
                db, account_label=label or None, limit=limit)}
        finally:
            db.close()

    # ---------------------------------------------------------------- 账号管理（增删改）
    #
    # 三条硬约束（写进 accounts.save_account 的 docstring）：
    # ① 端口重复一律拒（一账号一端口）；② 越界报错不静默钳制；③ 原子写，不覆盖别人改动。
    # 所有会改真实状态的动作都写 `gx_ops`（用户 2026-10-30 要求“日志也要记录操作记录”）。

    @app.post("/api/accounts/save")
    def accounts_save(body: dict) -> dict:
        original = str(body.get("original_label") or "").strip().lower()
        label = str(body.get("label") or "").strip().lower()
        # 改一个正在跑任务的账号：端口/代号会当场改掉子进程的归属，风险太大
        target = original or label
        if target and any(j["label"] == target for j in _jobs_snapshot()):
            return {"ok": False, "error": "该账号有任务在跑：先「停止」再改账号信息"}
        # 旧值必须在**写之前**取：写完再读到的就是新值，旧值→新值会永远显示“没变”
        before = (gaccounts.find_by_label(gaccounts.load_accounts(), original)
                  if original else None)
        try:
            acc = gaccounts.save_account(body, original_label=original)
        except gaccounts.AccountError as exc:
            return {"ok": False, "error": str(exc)}
        except OSError as exc:
            return {"ok": False, "error": f"写账号表失败：{exc}"}
        action = "account-edit" if original else "account-add"
        # 旧值 → 新值：事后能回答“谁把额度改了”（与超星 Q41/Q42 的留痕要求一致）
        change = ""
        if before is not None:
            diffs = []
            if before.daily_sections != acc.daily_sections:
                diffs.append(f"日节数 {before.daily_sections} → {acc.daily_sections}")
            if before.daily_minutes != acc.daily_minutes:
                diffs.append(f"日时长 {before.daily_minutes} → {acc.daily_minutes}")
            if before.port != acc.port:
                diffs.append(f"端口 {before.port} → {acc.port}")
            if before.enabled != acc.enabled:
                diffs.append(f"启用 {before.enabled} → {acc.enabled}")
            if before.display != acc.display:
                diffs.append("显示名已改")
            if diffs:
                change = "｜" + "，".join(diffs)
        _record(settings, acc.label, action,
                f"{'修改' if original else '新增'}账号 {acc.label}@:{acc.port}"
                f"（日额度 {acc.daily_sections} 节 / {acc.daily_minutes} 分钟）{change}")
        return {"ok": True,
                "message": f"已保存 {acc.display or acc.label}（端口 {acc.port}）",
                "account": {"label": acc.label,
                            "display": acc.display or acc.label,
                            "port": acc.port,
                            "daily_sections": acc.daily_sections,
                            "daily_minutes": acc.daily_minutes,
                            "enabled": acc.enabled,
                            "has_credentials": acc.configured}}

    @app.post("/api/accounts/next-port")
    def accounts_next_port() -> dict:
        """给新账号随机分配一个未占用的调试端口。"""
        try:
            port = gaccounts.next_free_port(gaccounts.load_accounts())
        except gaccounts.AccountError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "port": port,
                "message": f"已分配端口 {port}（可手动修改）"}

    @app.post("/api/accounts/toggle")
    def accounts_toggle(body: dict) -> dict:
        label = _safe_label(settings, str(body.get("label") or ""))
        enabled = bool(body.get("enabled", True))
        if not enabled and any(j["label"] == label for j in _jobs_snapshot()):
            return {"ok": False, "error": "该账号有任务在跑：先「停止」再停用"}
        if not gaccounts.set_enabled(label, enabled):
            return {"ok": False, "error": f"账号表里没有 {label}"}
        _record(settings, label, "account-toggle",
                f"{'启用' if enabled else '停用'}账号 {label}")
        return {"ok": True, "message": f"已{'启用' if enabled else '停用'} {label}"}

    @app.post("/api/accounts/delete")
    def accounts_delete(body: dict) -> dict:
        label = _safe_label(settings, str(body.get("label") or ""))
        if any(j["label"] == label for j in _jobs_snapshot()):
            return {"ok": False, "error": "该账号有任务在跑：先「停止」再删除"}
        # 二次确认：前端要先弹框，后端再要一次 `confirm` 字样，防误点
        if not body.get("confirm"):
            return {"ok": False, "error": "删除需要二次确认（前端会先问一次）"}
        remaining = [a for a in gaccounts.load_accounts() if a.label != label]
        if not remaining:
            return {"ok": False,
                    "error": "这是最后一个账号，删了就什么都不剩；请先加一个新账号再删"}
        if not gaccounts.delete_account(label):
            return {"ok": False, "error": f"账号表里没有 {label}"}
        _record(settings, label, "account-del",
                f"删除账号 {label}（profile 目录与登录态快照保留，没删）")
        return {"ok": True, "message": f"已删除 {label}（登录态快照保留在磁盘上）"}

    @app.post("/api/accounts/batch")
    def accounts_batch(body: dict) -> dict:
        """批量动作：`start` / `stop` / `enable` / `disable` / `delete`。

        语义与单个操作**完全一致**（复用 `_study_one`/`_stop_one`），
        并且**一个失败不影响其它**——批量最怕"第 3 个报错，后面全没做"，
        所以逐账号收集结果，最后统一汇总给人看。

        删除另外多加两道闸：① 必须带 `confirm`；② 一次不许把账号删光。
        """
        action = str(body.get("action") or "").strip().lower()
        labels = body.get("labels")
        if not isinstance(labels, list) or not labels:
            return {"ok": False, "error": "没有选中账号"}
        labels = [str(x).strip().lower() for x in labels if str(x).strip()]
        if action not in ("start", "stop", "enable", "disable", "delete",
                          "start-browser", "start-and-study"):
            return {"ok": False, "error": f"不认识的批量动作：{action}"}

        known = {a.label for a in gaccounts.load_accounts()}
        unknown = [x for x in labels if x not in known]
        if unknown:
            return {"ok": False, "error": f"账号表里没有：{'、'.join(unknown)}"}

        if action == "delete":
            if not body.get("confirm"):
                return {"ok": False, "error": "批量删除需要二次确认"}
            remaining = [a.label for a in gaccounts.load_accounts()
                         if a.label not in set(labels)]
            if not remaining:
                return {"ok": False,
                        "error": "这批会把账号删光；请至少留一个账号"}

        # 端口探活**只对「开始学习」有意义**，且要**一次并发做完**再逐个判定：
        # 串行探 N 个账号最坏 N × timeout（批量 7 个能拖 3.5 秒）；
        # 停止/启用/停用/删除根本不看浏览器，白探一轮纯浪费（2026-11-06）。
        # `start-browser` 自己会探（它要起浏览器），但**先并发探一轮**能省掉
        # 「已经在跑」的那些白起一次计划任务（每个冷启动几秒~几十秒）。
        alive_map: dict[int, bool] = {}
        if action in ("start", "start-browser", "start-and-study"):
            ports = {a.label: a.port for a in gaccounts.load_accounts()}
            alive_map = gchrome.ports_alive(
                [ports[l] for l in labels if l in ports])
        results: list[dict[str, Any]] = []
        # ---- 并发闸（P2a，2026-11-06）：先算「这一批还能起几个」再逐个起 ----
        #
        # 为什么不能靠 `_study_one` 里逐个判：那个 `live` 数是**起之前**的计数，
        # 循环里起完第一个后它没刷新（`_spawn_script` 会写进 `_JOBS`，所以其实会变，
        # 但被测的间隔/竞态太难保证）。这里**显式算配额**更可预期：
        #   room = 上限 - 已在跑；最多个起 room 个，其余的标 blocked 并说清原因。
        start_room: int | None = None
        batch_jitter = 0.0
        fleet_refused: dict[str, Any] | None = None
        if action in ("start", "start-and-study"):
            limit = _concurrency_limit(settings)
            start_room = max(0, limit - _live_study_count())
            started_n = 0
            # 随机错峰（P2a）：同一秒批量起 N 个是"集群行为"最明显的形态。
            # 只在**批量起 ≥2 个**时才加，单个账号不该凭空等 30 秒。
            batch_jitter = _start_jitter(settings)
            if len(labels) < 2:
                batch_jitter = 0.0
            # 全局日总量闸（P2b）：**整批**先判一次。它不按账号细分，
            # 一旦今天合计学够了，这一批一个都不该起。
            fleet_refused = _check_fleet_daily(settings)
        for label in labels:
            if action in ("start", "start-and-study"):
                if fleet_refused:
                    got = dict(fleet_refused)      # 全局日总量已满 → 整批同理由
                elif start_room is not None and started_n >= start_room:
                    # 并发满 → **入队**（P2b），等下有空位自动起。
                    # `PLAN-GXKS.md` §11.5 要的就是这个语义："否则按并发上限排队"。
                    _queue_add(settings, label,
                               max(1, min(int(body.get("max_sections")
                                            or _sections_per_run(settings)), 12)),
                               jitter=batch_jitter)
                    got = {"ok": False, "queued": True,
                           "message": (f"已排队（并发上限 {limit} 个已满，"
                                       "有账号跑完会自动开始）")}
                elif action == "start-and-study":
                    # 一键：起浏览器 → 等就绪 → 开始学习。
                    # ⚠ 这里**逐个等**（每账号最多 45 秒），所以 N 个账号会把这批
                    # 拖到 N×40 秒。批量场景本就该串行起浏览器（同时开会抢资源），
                    # 且前端已提示"每个 10~40 秒、别重复点"。
                    got = _start_and_study_one(settings, label,
                                               run_session=_session_script,
                                               max_sections=body.get("max_sections"))
                    if got.get("ok"):
                        started_n += 1
                else:
                    got = _study_one(settings, label, body.get("max_sections"),
                                     port_alive=alive_map.get(ports.get(label, 0)),
                                     check_concurrency=False,
                                     jitter=batch_jitter)
                    # 真起来了才占一个配额（被浏览器闸/已在跑挡下的不占）
                    if got.get("ok"):
                        started_n += 1
            elif action == "start-browser":
                got = _browser_start_one(settings, label, run_session=_session_script)
            elif action == "stop":
                got = _stop_one(settings, label)
            elif action in ("enable", "disable"):
                want = action == "enable"
                if not want and any(j["label"] == label for j in _jobs_snapshot()):
                    got = {"ok": False, "blocked": True,
                           "error": "有任务在跑：先「停止」再停用"}
                elif gaccounts.set_enabled(label, want):
                    _record(settings, label, "account-toggle",
                            f"{'启用' if want else '停用'}账号 {label}（批量）")
                    got = {"ok": True, "message": "已" + ("启用" if want else "停用")}
                else:
                    got = {"ok": False, "error": "账号表里没有它"}
            else:                                       # delete
                if any(j["label"] == label for j in _jobs_snapshot()):
                    got = {"ok": False, "blocked": True,
                           "error": "有任务在跑：先「停止」再删除"}
                elif gaccounts.delete_account(label):
                    _record(settings, label, "account-del",
                            f"删除账号 {label}（批量；profile 与快照保留）")
                    got = {"ok": True, "message": "已删除"}
                else:
                    got = {"ok": False, "error": "账号表里没有它"}
            results.append({"label": label, "ok": bool(got.get("ok")),
                            "skipped": bool(got.get("skipped")),
                            "blocked": bool(got.get("blocked")),
                            "queued": bool(got.get("queued")),
                            "concurrency_full": bool(got.get("concurrency_full")),
                            "fleet_full": bool(got.get("fleet_full")),
                            "need_browser": bool(got.get("need_browser")),
                            "message": str(got.get("message") or got.get("error") or "")})

        done = [r for r in results if r["ok"]]
        # 三类「没做成」性质不同，必须分开报（2026-11-06）：
        #   skipped = 本来就不需要做（已在跑 / 本来没在跑）→ 正常，绿提示
        #   blocked = 状态前提不满足（有任务在跑，先停再做）→ 用户可自行解决，黄提示
        #   failed  = 真出错（未知代号 / 写盘失败 / 浏览器没起）→ 红提示
        skipped = [r for r in results if not r["ok"] and r["skipped"]]
        queued = [r for r in results if not r["ok"] and r["queued"]]
        blocked = [r for r in results if not r["ok"] and not r["skipped"]
                   and not r["queued"] and r["blocked"]]
        failed = [r for r in results
                  if not r["ok"] and not r["skipped"] and not r["queued"]
                  and not r["blocked"]]
        names = "、".join(r["label"] for r in done) or "（无）"
        note = ""
        if skipped:
            # 跳过的原因各不相同（已在跑 / 没在跑），取各自原文更有用
            note += f"；跳过 {len(skipped)} 个（{ '、'.join(r['label'] for r in skipped) }）"
        if queued:
            # 排队 = **受理由**，不是失败：等有空位自动起（P2b，2026-11-06）
            note += (f"；{len(queued)} 个已排队等空位"
                     f"（{ '、'.join(r['label'] for r in queued) }）")
        if blocked:
            # `blocked` 有三种来源，文案必须分开说 —— 否则「并发已满」会被读成
            # 「有任务在跑要先去停」，用户会白找一圈（2026-11-06）。
            fleet = [r for r in blocked if r.get("fleet_full")]
            full = [r for r in blocked
                    if not r.get("fleet_full") and r.get("concurrency_full")]
            other = [r for r in blocked
                     if not r.get("fleet_full") and not r.get("concurrency_full")]
            if other:
                note += (f"；{len(other)} 个需先停止"
                         f"（{ '、'.join(r['label'] for r in other) }）")
            if full:
                note += (f"；{len(full)} 个因并发已满未启动"
                         f"（{ '、'.join(r['label'] for r in full) }）"
                         "——想都启动就停几个或到「面板设置」调高上限")
            if fleet:
                note += (f"；{len(fleet)} 个因「全局日时长已满」未启动"
                         f"（{ '、'.join(r['label'] for r in fleet) }）"
                         "——要接着跑就到「面板设置」调高「全局日时长上限」")
        if failed:
            why = "；".join(f"{r['label']}：{r['message']}" for r in failed[:5])
            more = "" if len(failed) <= 5 else f" 等 {len(failed)} 个"
            return {"ok": False, "results": results, "done": len(done),
                    "skipped": len(skipped), "queued": len(queued),
                    "blocked": len(blocked), "failed": len(failed),
                    "error": f"{len(failed)} 个没做成{more}——{why}",
                    "message": f"完成 {len(done)} 个（{names}）{note}"}
        # `ok` 只反映**有没有真失败**：全是跳过/排队/需先停止时仍算成功，
        # 否则前端会弹红 toast，像批量挂了。
        return {"ok": True, "results": results, "done": len(done),
                "skipped": len(skipped), "queued": len(queued),
                "blocked": len(blocked), "failed": 0,
                "message": f"完成 {len(done)} 个（{names}）{note}"}

    # ---------------------------------------------------------------- AI 配置（为 B5 做准备）
    #
    # 存 **gxks 自己的库**（`meta` 表），不碰超星的 `xxt.db`/`app_config`
    # —— gxks 库里根本没有 app_config 表，照抄超星那套会得到"能存但读不到 Key"的假功能。
    # Key 全程只以"有没有"的布尔对外，绝不回显。

    @app.get("/api/ai")
    def ai_get() -> dict:
        """厂商列表 + 每个配置项的中文说明 + 当前选中 + 是否已配 Key。"""
        return {"ok": True,
                **gxks_ai_config.load(_db_path(settings), data_dir=settings.data_dir)}

    @app.post("/api/ai/provider")
    def ai_provider(body: dict) -> dict:
        """新增或覆盖一家厂商（按 name 判重）。"""
        item = {k: body.get(k) for k in ("name", "base_url", "model")}
        saved, err = gxks_ai_config.save_provider(_db_path(settings), item)
        if err:
            return {"ok": False, "error": err}
        _record(settings, "", "ai-provider-save",
                f"保存 AI 厂商「{item.get('name')}」")
        return {"ok": True, "message": f"已保存厂商「{item.get('name')}」",
                "custom": saved}

    @app.post("/api/ai/provider/delete")
    def ai_provider_delete(body: dict) -> dict:
        name = str(body.get("name") or "").strip()
        if not name:
            return {"ok": False, "error": "厂商名不能为空"}
        saved = gxks_ai_config.delete_provider(_db_path(settings), name)
        _record(settings, "", "ai-provider-del", f"删除 AI 厂商「{name}」")
        return {"ok": True, "message": f"已删除厂商「{name}」", "custom": saved}

    @app.post("/api/ai/current")
    def ai_current(body: dict) -> dict:
        """切换当前厂商；允许只给名字（地址/模型按列表补）。"""
        try:
            timeout = int(body.get("timeout") or 0) or None
        except (TypeError, ValueError):
            timeout = None
        cur, err = gxks_ai_config.save_current(
            _db_path(settings), str(body.get("name") or ""),
            base_url=str(body.get("base_url") or ""),
            model=str(body.get("model") or ""), timeout=timeout)
        if err:
            return {"ok": False, "error": err}
        _record(settings, "", "ai-current",
                f"切换 AI 厂商 → {cur['name']}（{cur['base_url']}｜{cur['model']}）")
        return {"ok": True, "message": f"已切换到「{cur['name']}」", "current": cur}

    @app.post("/api/ai/key")
    def ai_key(body: dict) -> dict:
        """保存 **指定厂商** 的 API Key（加密存本机库）。空串 = 不修改。

        用户 2026-11-02：「配置了 Key 要能保存，下次在不同厂商切换也能直接调用」
        → 必须带 `provider`，按厂商各存一把。
        """
        provider = str(body.get("provider") or "").strip()
        if not provider:
            return {"ok": False, "error": "要先选一个厂商"}
        ok, err = gxks_ai_config.save_key(
            _db_path(settings), data_dir=settings.data_dir,
            provider=provider, api_key=str(body.get("api_key") or ""))
        if not ok:
            return {"ok": False, "error": err}
        # ⚠ 留痕只记厂商与"已更新"，**绝不写 Key 内容**
        _record(settings, "", "ai-key", f"更新 AI 的 API Key：{provider}（内容不记录）")
        return {"ok": True,
                "message": f"已保存「{provider}」的 API Key（加密存本机）"}

    @app.post("/api/ai/key/delete")
    def ai_key_delete(body: dict) -> dict:
        """清除某厂商的 Key（换 Key 或不再使用时）。"""
        provider = str(body.get("provider") or "").strip()
        if not provider:
            return {"ok": False, "error": "要先选一个厂商"}
        gxks_ai_config.delete_key(_db_path(settings), provider=provider)
        _record(settings, "", "ai-key-del", f"清除 AI 的 API Key：{provider}")
        return {"ok": True, "message": f"已清除「{provider}」的 API Key"}

    @app.get("/api/ai/models")
    def ai_models(provider: str = "") -> dict:
        """从厂商拉模型列表（用户：「模型名称能从对应的厂商获取到」）。"""
        from ..ai import probe

        cfg = gxks_ai_config.load(_db_path(settings), data_dir=settings.data_dir)
        want = provider or cfg["current"]["name"]
        hit = next((p for p in cfg["providers"] if p["name"] == want), None)
        if not hit:
            return {"ok": False, "error": f"没有这个厂商：{want}"}
        key = gxks_ai_config.load_key_for(
            _db_path(settings), data_dir=settings.data_dir, provider=want) or ""
        names, err = probe.fetch_models(hit["base_url"], key, timeout=20.0)
        if err:
            return {"ok": False, "error": err, "provider": want}
        return {"ok": True, "provider": want, "models": names,
                "message": f"取到 {len(names)} 个模型"}

    @app.post("/api/ai/test")
    def ai_test(body: dict) -> dict:
        """测试连通：真发一次最小对话（用户：「测试联通 AI 模型的按钮和提示信息」）。"""
        from ..ai import probe

        cfg = gxks_ai_config.load(_db_path(settings), data_dir=settings.data_dir)
        want = str(body.get("provider") or cfg["current"]["name"]).strip()
        hit = next((p for p in cfg["providers"] if p["name"] == want), None)
        if not hit:
            return {"ok": False, "error": f"没有这个厂商：{want}"}
        # 优先用请求里带的值（用户可能刚改了地址/模型还没保存）
        base_url = str(body.get("base_url") or "").strip() or hit["base_url"]
        model = str(body.get("model") or "").strip() or hit["model"]
        key = gxks_ai_config.load_key_for(
            _db_path(settings), data_dir=settings.data_dir, provider=want) or ""
        ok, msg = probe.test_chat(base_url, key, model, timeout=30.0)
        # 留痕：只记厂商与成败，不记 Key
        _record(settings, "", "ai-test",
                f"测试 AI 连通：{want} → {'成功' if ok else '失败'}")
        return {"ok": ok, "provider": want, "model": model,
                "message": msg, "error": "" if ok else msg}

    # ---------------------------------------------------------- 面板设置
    @app.get("/api/panel/settings")
    def panel_settings_get() -> dict:
        """面板设置（热重载开关、刷新间隔）。

        页面上的「账号设置 → 面板设置」读写的就是这里（2026-11-03 用户要求）。
        """
        return {"ok": True,
                "settings": gxks_panel_settings.load(_db_path(settings))}

    @app.post("/api/panel/settings")
    def panel_settings_save(body: dict) -> dict:
        """保存面板设置。只有 `reload` 需要重启面板才生效（它决定启动方式）。

        ## ⚠ 白名单必须与 `gxks_panel_settings.save()` 支持键**一一对应**
        （2026-11-07 修的真 bug）：这里原来只放行 `reload` / `poll_seconds`，
        而 P2a/P2b 新加的 `max_concurrency` / `start_jitter` / `fleet_daily_minutes`
        **没加进白名单** → 前端提交了、`save()` 也支持，但走到这里就被过滤掉。

        现象正是「面板上把同时学习上限改成 5 → 提示已保存 → 实际还是 2」，
        而且**不报错**（因为过滤后的 items 仍然"保存成功"）。用户没法自助发现，
        只会觉得"改了没效果" —— 这与 11-06 那个 `except: pass` 是同一类骗人 bug。

        诊断实证：`POST {'max_concurrency': 5}` → 回 `ok:true`，回包 settings 里
        `max_concurrency` 仍是 **2**。
        """
        allowed = ("reload", "poll_seconds", "max_concurrency",
                   "start_jitter", "fleet_daily_minutes", "sections_per_run")
        items = {k: body.get(k) for k in allowed if k in body}
        saved = gxks_panel_settings.save(_db_path(settings), items)
        _record(settings, "", "panel-settings",
                "保存面板设置：" + "、".join(f"{k}={v}" for k, v in saved.items()))
        # 把**真正落盘的值**回给前端：前端据此刷新控件，避免"界面显示 5、后端还是 2"
        return {"ok": True, "settings": saved,
                "saved_keys": sorted(items),
                "message": "已保存（热重载开关需重启面板才生效）"}

    return app


def ensure_local_temp(settings: Settings) -> pathlib.Path:
    """把临时目录指向 `data/tmp`（返回该路径）。

    Playwright 会在系统临时目录里 `mkdtemp` 放 artifacts；受限会话下这一点
    会被拒绝（EPERM），表现为「连上了但拿不到结果」。项目内的目录一定可写，
    而且不污染用户临时区。
    """
    import os
    import tempfile

    d = settings.data_dir / "tmp"
    d.mkdir(parents=True, exist_ok=True)
    os.environ["TEMP"] = str(d)
    os.environ["TMP"] = str(d)
    tempfile.tempdir = str(d)
    return d


def _port_in_use(host: str, port: int) -> bool:
    """本机 `host:port` 是否已被**别的进程**监听（纯探测，不绑定、不发包）。

    ## 为什么需要（2026-11-08 查出的总根源）

    实测同一台机器上出现过 **两个 uvicorn worker 同时监听 8301**：

    ```
    26532 (22:32 启动，旧代码) ─┐
    24604 (00:41 启动，新代码) ─┴─ 都 bind 127.0.0.1:8301
    ```

    Windows 下 `SO_REUSEADDR` 允许多个 socket 绑同一地址，请求落到哪个 worker
    不确定 → 用户看到的一直是**旧页面**，而我改的代码全在新进程里。
    表现为「小点还在 / 日志还 4 秒刷新 / 账号徽标没出现」——
    改了三轮都被当成"修复没生效"，其实根本没部署到他连的那个进程。

    所以在 `uvicorn.run` 之前先探一次：端口已被占用就**明确拒绝第二个实例**，
    而不是静默绑上去。第一个实例重启时端口是空的，不受影响。

    ⚠ 只探 `127.0.0.1`（面板默认只本机），用 `connect_ex` 而不是 `bind`：
    `bind` 会真的占用端口，`connect_ex` 只读、不留痕迹。
    """
    import socket

    addr = "127.0.0.1" if host in ("0.0.0.0", "", "localhost") else host
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    except OSError:
        # 连 socket 都建不出来（资源紧张/受限会话）→ 当作"端口空闲"放行，
        # 不能因为探测失败就把正常的第一个实例挡在门外（宁可漏报不可误拒）。
        return False
    s.settimeout(0.3)
    try:
        return s.connect_ex((addr, int(port))) == 0
    except OSError:
        return False
    finally:
        s.close()


def _page_fingerprint() -> str:
    """当前载入内存的 `PAGE` 指纹（长度 + 几个关键串），用于一眼分辨新旧代码。

    2026-11-08 教训：磁盘上是新代码、进程内存里是旧代码，光看文件时间没用。
    启动时把这串打进日志，排查"用户看到的到底是哪个版本"只需 3 秒。
    """
    from .page import PAGE

    keys = ("c-pick", "runlog-who", "LOG_ANCHOR", "start-and-study")
    hits = "".join(k[0].upper() if k in PAGE else "-" for k in keys)
    return f"PAGE len={len(PAGE)} markers={hits}"


def serve(settings: Settings) -> int:
    """启动独立面板（uvicorn 阻塞直到 Ctrl+C）。

    ## 热重载默认开（2026-11-03 用户定）

    用户原话：「为什么会经常出现这个是旧面板新代码的情况，不能一次性解决吗」
    —— 根因是 Python 进程启动时把 `page.PAGE` 等模块载入内存，之后改磁盘文件
    不会刷新已运行的进程，所以每次改代码都得手动重启面板，否则看到的一直是旧页面。

    这里开 uvicorn 的 `reload=True`：改代码自动重启**主进程**，页面永远是最新。
    对挂机刷课**没有影响** —— 刷课是独立子进程（`_spawn_script` 起的），
    面板重启不碰它；日志是追加写文件，重连后尾巴照旧。

    `panel.reload` 可关（默认 `True`）：只在排查"到底是新代码还是旧代码"时
    才值得关掉，日常一律开着。
    """
    import uvicorn

    ensure_local_temp(settings)          # 必须在任何 Playwright 调用之前

    panel = settings.raw.get("panel", {})
    host = str(panel.get("host", "127.0.0.1"))
    port = int(panel.get("port", 8301))
    # 页面上的开关（数据库 meta）优先于 toml：用户能在界面上改，改完重启即生效。
    # toml 的 `panel.reload` 只作默认值来源。
    #
    # ⚠ **必须把"这个值从哪一层来的"打进日志**（2026-11-06 教训）：
    # 当时 DB 里是 `false`、界面/文档都说默认 `true`，我因为「读配置的函数读不到就回默认值」
    # 而误判成 bug（其实是设置被关过）。日志写清来源，下次 3 秒就能判断，不用再查三层。
    reload_on = bool(panel.get("reload", True))
    reload_src = "settings.toml"
    try:
        db_reload = gxks_panel_settings.load(_db_path(settings)).get("reload")
        if db_reload is not None:
            reload_on = bool(db_reload)
            reload_src = "数据库 meta.panel.reload（界面开关）"
    except Exception:                                       # noqa: BLE001
        reload_src = "settings.toml（读 DB 失败，已回退）"
    print(f"[OK] 第二平台面板：http://{host}:{port}（Ctrl+C 停止）")
    print(f"[OK] 热重载：{'开' if reload_on else '关'}（来源：{reload_src}）"
          + ("→ 改代码自动生效，不用手动重启" if reload_on
             else "→ 改了代码必须手动重启面板，否则跑的还是旧代码"))
    # **页面指纹**：磁盘上是新代码 ≠ 进程里是新代码。打出来，新旧一眼可辨。
    print(f"[OK] 页面指纹：{_page_fingerprint()}")
    # **拒绝第二个实例**：端口已被别的进程监听 → 明确报错退出，不静默绑上去。
    # （Windows 允许多 socket 绑同一地址，请求会随机分流到旧 worker。）
    if _port_in_use(host, port):
        print(f"[FAIL] 端口 {host}:{port} 已被别的进程占用 —— 已经有一个面板在跑了。"
              "\n       不要再起第二个：两个 worker 同时监听会让请求随机落到旧进程，"
              "\n       表现为「改了代码却一直看到旧页面」。"
              "\n       请先停掉旧面板（定位监听 pid：Get-NetTCPConnection -LocalPort "
              f"{port}），再启动本实例。")
        return 1
    if reload_on:
        # reload 必须传**导入字符串**，不能传 app 实例；源码目录随 settings 走
        src_dir = str(Path(__file__).resolve().parents[2])
        uvicorn.run("xxt.panel_gxks.app:app_factory", factory=True,
                    host=host, port=port, log_level="warning",
                    reload=True, reload_dirs=[src_dir])
    else:
        uvicorn.run(create_app(settings), host=host, port=port, log_level="warning")
    return 0
