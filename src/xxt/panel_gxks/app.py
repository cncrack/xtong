"""第二平台独立面板（`127.0.0.1:8301`）。

与超星面板**不共享进程、路由、表结构**（用户 2026-10-30 定「各自独立」）：
- 超星面板 = `xxt.panel.app`（8300），继续用它自己的 `accounts` / `run_logs`；
- 本模块 = 第二平台（8301），账号来自 `accounts.local.json`，日志落在 `gx_ops`。

面板只做三件事：**看**（节进度、操作记录）、**管**（开始/停止/刷新）、
**核对**（AI 填了什么，一格一格列出来）。所有重活交给 `scripts/` 下的子进程跑——
Playwright 同步 API 会阻塞，跑在面板进程里等于把界面锁死。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from ..adapters.gxks import accounts as gaccounts
from ..adapters.gxks import browser as gbrowser
from ..adapters.gxks import chrome as gchrome
from ..adapters.gxks import ledger as gledger
from ..adapters.gxks import login as glogin
from ..adapters.gxks import sections_cache
from ..config import Settings
from ..store import gxks_ai_config
from ..store import gxks_ops
from ..store.schema_gxks import open_question_bank

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"

# 每个账号同时只允许一个任务：两个脚本抢同一个 Chrome 会话会互相顶掉页面
_JOBS: dict[str, dict[str, Any]] = {}
_JOBS_LOCK = threading.Lock()

# 面板自己拉起的浏览器（只记我们起的，用于“停止浏览器”时只杀自己那一只）
_BROWSERS: dict[str, dict[str, Any]] = {}
_BROWSER_LOCK = threading.Lock()


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
    """
    log_dir = settings.data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"gxks-{kind}-{label}-{time.strftime('%Y%m%d-%H%M%S')}.log"
    argv = [sys.executable, "-u", str(SCRIPTS / script), *args]
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1",
               PYTHONUNBUFFERED="1")
    handle = open(log_path, "ab")
    try:
        proc = subprocess.Popen(argv, cwd=str(ROOT), stdout=handle,
                                stderr=handle, env=env)
    finally:
        handle.close()
    with _JOBS_LOCK:
        _JOBS[label] = {"proc": proc, "kind": kind, "label": label,
                        "since": time.strftime("%H:%M:%S"), "log": log_path,
                        "detail": " ".join(args[:4])}
    return proc


def _jobs_snapshot() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with _JOBS_LOCK:
        for label, job in list(_JOBS.items()):
            alive = job["proc"].poll() is None
            if not alive:
                _JOBS.pop(label, None)
            out.append({"label": label, "kind": job["kind"], "since": job["since"],
                        "alive": alive, "detail": job["detail"],
                        "log_name": Path(job["log"]).name,
                        "tail": _log_tail(Path(job["log"]))})
    return out


def _start(settings: Settings, label: str, kind: str,
           script: str, *args: str, message: str) -> dict[str, Any]:
    """起任务并拼好给前端的回包。

    冻结成 exe 后 `scripts/` 不在包内，这条路直接说不支持——比默默报 500
    或“点了没反应”强。
    """
    if getattr(sys, "frozen", False):
        return {"ok": False,
                "error": "打包版面板暂不支持起脚本（scripts/ 不在 exe 内），请用源码方式启动面板"}
    proc = _spawn_script(settings, label, kind, script, *args)
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
    out: list[dict[str, Any]] = []
    for acc in accounts:
        today = gledger.today_totals(ledger_path, acc.label)
        cached, cached_course = _pick_sections_cache(debug_dir, acc.label)
        summary = cached.get("summary") or {}
        course = next((r.get("category") for r in cached.get("rows") or []
                       if r.get("category")), "")
        # 这个账号一共有几门课的缓存（多课程时卡片要能看出来）
        courses_cached = len(sections_cache.list_all(debug_dir, acc.label))
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
            "snapshot": snap,
            "course": course,
            "course_value": cached_course,      # 缓存对应的课程 value（多课程时区分用）
            "courses_cached": courses_cached,
            "sections": {"done": summary.get("done", 0),
                         "total": summary.get("sections", 0),
                         "read_at": cached.get("read_at", "")},
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
    return {
        "backend": backend,
        "chrome": str(chrome) if chrome else "",
        "chrome_ok": chrome is not None,
        "port": acc.port if acc else 0,
        "port_alive": (bool(alive) if alive is not None
                       else (gchrome.port_alive(acc.port) if acc else False)),
        "profile_set": bool(acc and acc.profile),
        "profile": str(cdp_dir) if cdp_dir else "",
        "profile_name": (Path(acc.profile).name if acc and acc.profile
                         else (f"data/{gbrowser.PROFILE_ROOT}/{label}" if cdp_dir else "")),
        "managed_profile": managed_dir,
        "started_by_panel": bool(own),
        "since": own["since"] if own else "",
        "can_start": backend == "cdp" and chrome is not None and cdp_dir is not None,
        # 登录态快照：本站 cookie 全是会话级，关浏览器就没了 → 只能靠这份文件兜底
        "state": gbrowser.state_summary(gbrowser.state_path(settings, label)) if label else {},
        "credentials": glogin.is_configured(glogin.credentials_path(settings)),
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


def _study_one(settings: Settings, label: str,
               max_sections: object = None, sections: object = None) -> dict[str, Any]:
    """给一个账号起刷课子进程。返回面板回包（`ok`/`error`/`message`）。"""
    acc = _account(settings, label)
    if any(j["label"] == acc.label for j in _jobs_snapshot()):
        return {"ok": False, "error": "该账号已有任务在跑"}
    n = int(max_sections or 3)
    args = ["--label", acc.label, "--account-label", acc.label,
            "--port", str(acc.port),
            "--max-sections", str(max(1, min(n, 12)))]
    if sections:
        args += ["--sections", str(sections)]
    return _start(settings, acc.label, "study", "run_gxks.py", *args,
                  message=f"开始刷课，最多 {n} 节；随时可停")


def _stop_one(settings: Settings, label: str) -> dict[str, Any]:
    """停一个账号正在跑的任务。"""
    label = _safe_label(settings, label)
    with _JOBS_LOCK:
        job = _JOBS.get(label)
    if job is None:
        return {"ok": False, "error": "该账号没有在跑的任务"}
    result = _kill(job["proc"])
    with _JOBS_LOCK:
        _JOBS.pop(label, None)
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


def create_app(settings: Settings) -> FastAPI:
    from .page import PAGE

    app = FastAPI(title="第二平台控制台", docs_url=None, redoc_url=None)
    debug_dir = settings.data_dir / "debug"

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return PAGE

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
        # B1：节列表按课程分开存；`course` 给了就读那一份，没给就读该账号最新的一份
        sections, sections_course = (_pick_sections_cache(debug_dir, label, course)
                                     if label else ({}, ""))
        return {
            "ok": True,
            "accounts": rows,
            "cards": _account_cards(settings, alive=alive),
            "accounts_file": gaccounts.default_path().exists(),
            "label": label,
            "course": sections_course,
            "auto_submit": bool(settings.raw.get("gxks", {}).get("auto_submit", False)),
            "jobs": _jobs_snapshot(),
            "sections": sections or None,
            "work": _read_json(_latest(f"work_{label}_*.json", debug_dir)),
            "browser": (_browser_state(settings, label, alive=alive.get(cur_port))
                        if label else None),
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
        return _study_one(settings, str(body.get("label") or ""),
                          body.get("max_sections"), body.get("sections"))

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
        state = _browser_state(settings, acc.label)
        if state["backend"] != "cdp":
            return {"ok": False,
                    "error": "当前是 managed 后端（容器形态）：面板内不起浏览器。"
                             "本机请把 config/settings.local.toml 里 browser_backend 改回 cdp"}
        if state["port_alive"]:
            return {"ok": True, "message": f"端口 {acc.port} 已在运行，不用重复启动"}
        profile = gbrowser.cdp_profile(settings, acc.label, acc)
        chrome = gchrome.find_chrome()
        if chrome is None:
            return {"ok": False, "error": "没找到 Chrome。请安装，或设环境变量 "
                                          "XXT_GXKS_CHROME 指向 chrome.exe"}
        if any(j["label"] == acc.label for j in _jobs_snapshot()):
            return {"ok": False, "error": "该账号有任务在跑，先「停止」再启动浏览器"}
        proc = gchrome.start(chrome, port=acc.port, profile=profile,
                             url=gchrome.default_start_url())
        if not gchrome.port_alive(acc.port):
            return {"ok": False,
                    "error": f"Chrome 起来了但调试端口 {acc.port} 没响应：这个 profile 可能已被"
                             "另一个不带调试端口的 Chrome 打开——先把该账号的 Chrome 全部关掉再点一次"}
        with _BROWSER_LOCK:
            _BROWSERS[acc.label] = {"pid": proc.pid, "since": time.strftime("%H:%M:%S"),
                                    "port": acc.port, "profile": str(profile)}
        _record(settings, acc.label, "browser-start",
                f"面板启动 Chrome（端口 {acc.port}，profile {profile.name}）")
        return {"ok": True, "message": f"Chrome 已启动（端口 {acc.port}）；"
                                        "登录后点「检查会话」确认"}

    @app.post("/api/browser/stop")
    def browser_stop(body: dict) -> dict:
        label = _safe_label(settings, str(body.get("label") or ""))
        with _BROWSER_LOCK:
            own = _BROWSERS.pop(label, None)
        if own is None:
            return {"ok": False, "error": "这个浏览器不是面板起的，不敢盲杀（会误杀你日常的 "
                                          "Chrome）；请在那个窗口里手动关"}
        result = _kill_pid(int(own["pid"]))
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
        if not glogin.is_configured(glogin.credentials_path(settings)):
            return {"ok": False, "error": "data/account.local.json 里没有可用的账号密码"}
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
        if action not in ("start", "stop", "enable", "disable", "delete"):
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

        results: list[dict[str, Any]] = []
        for label in labels:
            if action == "start":
                got = _study_one(settings, label, body.get("max_sections"))
            elif action == "stop":
                got = _stop_one(settings, label)
            elif action in ("enable", "disable"):
                want = action == "enable"
                if not want and any(j["label"] == label for j in _jobs_snapshot()):
                    got = {"ok": False, "error": "有任务在跑：先「停止」再停用"}
                elif gaccounts.set_enabled(label, want):
                    _record(settings, label, "account-toggle",
                            f"{'启用' if want else '停用'}账号 {label}（批量）")
                    got = {"ok": True, "message": "已" + ("启用" if want else "停用")}
                else:
                    got = {"ok": False, "error": "账号表里没有它"}
            else:                                       # delete
                if any(j["label"] == label for j in _jobs_snapshot()):
                    got = {"ok": False, "error": "有任务在跑：先「停止」再删除"}
                elif gaccounts.delete_account(label):
                    _record(settings, label, "account-del",
                            f"删除账号 {label}（批量；profile 与快照保留）")
                    got = {"ok": True, "message": "已删除"}
                else:
                    got = {"ok": False, "error": "账号表里没有它"}
            results.append({"label": label, "ok": bool(got.get("ok")),
                            "message": str(got.get("message") or got.get("error") or "")})

        done = [r for r in results if r["ok"]]
        failed = [r for r in results if not r["ok"]]
        names = "、".join(r["label"] for r in done) or "（无）"
        if failed:
            why = "；".join(f"{r['label']}：{r['message']}" for r in failed[:5])
            return {"ok": bool(done), "results": results, "done": len(done),
                    "failed": len(failed),
                    "error": f"{len(failed)} 个没做成——{why}",
                    "message": f"完成 {len(done)} 个（{names}）"}
        return {"ok": True, "results": results, "done": len(done), "failed": 0,
                "message": f"完成 {len(done)} 个（{names}）"}

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
        """保存 API Key（加密存本机库）。空串 = 不修改。"""
        ok, err = gxks_ai_config.save_key(
            _db_path(settings), data_dir=settings.data_dir,
            api_key=str(body.get("api_key") or ""))
        if not ok:
            return {"ok": False, "error": err}
        # ⚠ 留痕只记"已更新"，**绝不写 Key 内容**
        _record(settings, "", "ai-key", "更新 AI 的 API Key（内容不记录）")
        return {"ok": True, "message": "已保存 API Key（加密存本机）"}

    return app


def serve(settings: Settings) -> int:
    """启动独立面板（uvicorn 阻塞直到 Ctrl+C）。"""
    import uvicorn

    panel = settings.raw.get("panel", {})
    host = str(panel.get("host", "127.0.0.1"))
    port = int(panel.get("port", 8301))
    print(f"[OK] 第二平台面板：http://{host}:{port}（Ctrl+C 停止）")
    uvicorn.run(create_app(settings), host=host, port=port, log_level="warning")
    return 0
