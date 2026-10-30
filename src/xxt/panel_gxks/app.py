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
from ..config import Settings
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
    rows = []
    for acc in gaccounts.load_accounts():
        # 单账号回落时 label 是 `default`，直接显示给人看等于没名字
        display = acc.display or ("主账号" if acc.label == "default" else acc.label)
        rows.append({"label": acc.label, "display": display,
                     "port": acc.port, "profile": Path(acc.profile).name if acc.profile else "",
                     "daily_sections": acc.daily_sections,
                     "daily_minutes": acc.daily_minutes, "enabled": acc.enabled,
                     "courses": list(acc.courses),
                     # 凭据只回布尔：面板不回显密码，回显即泄露
                     "has_credentials": acc.configured})
    return rows


def _ledger_path(settings: Settings) -> Path:
    """当日额度账本。跟 `settings.data_dir` 走（测试用临时根目录时不会读到真实账本）。"""
    return settings.data_dir / "gxks_ledger.jsonl"


def _account_cards(settings: Settings) -> list[dict[str, Any]]:
    """多账号总览：每个账号一张卡的数据。

    只读**本地**东西——账本、`sections_*.json` 缓存、登录态快照、TCP 探活，
    不发任何平台请求（最小访问原则）。凭据只回“有没有”，绝不回值。
    """
    accounts = gaccounts.load_accounts()
    jobs = {j["label"]: j for j in _jobs_snapshot()}
    debug_dir = settings.data_dir / "debug"
    ledger_path = _ledger_path(settings)
    out: list[dict[str, Any]] = []
    for acc in accounts:
        today = gledger.today_totals(ledger_path, acc.label)
        cached = _read_json(_latest(f"sections_{acc.label}.json", debug_dir)) or {}
        summary = cached.get("summary") or {}
        course = next((r.get("category") for r in cached.get("rows") or []
                       if r.get("category")), "")
        snap = gbrowser.state_summary(gbrowser.state_path(settings, acc.label))
        # `left = None` = 该账号把日节数设成 0（不限），卡片要显示“不限”而不是“0 剩”
        left = None if acc.daily_sections <= 0 else max(0, acc.daily_sections - today["sections"])
        out.append({
            "label": acc.label,
            "display": acc.display or ("主账号" if acc.label == "default" else acc.label),
            "enabled": acc.enabled,
            "port": acc.port,
            "port_alive": gchrome.port_alive(acc.port),
            # 账号表里该账号自己的 name/pw；旧版全局 account.local.json 在多账号下会全部假 “已配”
            "credentials": bool(acc.configured),
            "limits": {"sections": acc.daily_sections, "minutes": acc.daily_minutes},
            "today": {"sections": today["sections"], "minutes": today["minutes"],
                      "left_sections": left,
                      # 按课程拆分只作展示（A1：额度按账号合计，不按课程分池）
                      "by_course": today.get("courses") or {}},
            "snapshot": snap,
            "course": course,
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


def _browser_state(settings: Settings, label: str) -> dict[str, Any]:
    """浏览器/会话的**本地**状态：只探本地调试端口，**不访问平台**。"""
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
        "port_alive": gchrome.port_alive(acc.port) if acc else False,
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


def create_app(settings: Settings) -> FastAPI:
    from .page import PAGE

    app = FastAPI(title="第二平台控制台", docs_url=None, redoc_url=None)
    debug_dir = settings.data_dir / "debug"

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return PAGE

    @app.get("/api/state")
    def state(label: str = "") -> dict:
        rows = _account_rows()
        if label:
            _safe_label(settings, label)          # 越权代号直接拒，不静默回落
        else:
            label = rows[0]["label"] if rows else ""
        db = open_question_bank(settings.data_dir / "gxks_questions.db")
        try:
            ops = gxks_ops.recent_ops(db, account_label=label or None, limit=60)
        finally:
            db.close()
        return {
            "ok": True,
            "accounts": rows,
            "cards": _account_cards(settings),
            "accounts_file": gaccounts.default_path().exists(),
            "label": label,
            "auto_submit": bool(settings.raw.get("gxks", {}).get("auto_submit", False)),
            "jobs": _jobs_snapshot(),
            "sections": _read_json(_latest(f"sections_{label}.json", debug_dir)),
            "work": _read_json(_latest(f"work_{label}_*.json", debug_dir)),
            "browser": _browser_state(settings, label) if label else None,
            "ops": ops,
        }

    @app.post("/api/sections/refresh")
    def refresh_sections(body: dict) -> dict:
        acc = _account(settings, str(body.get("label") or ""))
        label = acc.label
        if any(j["label"] == label for j in _jobs_snapshot()):
            return {"ok": False, "error": "该账号已有任务在跑"}
        return _start(settings, label, "sections", "gxks_sections.py",
                      "--label", label, "--port", str(acc.port), "--all",
                      message="正在读节列表（含翻页），约 1 分钟")

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
        acc = _account(settings, str(body.get("label") or ""))
        label = acc.label
        if any(j["label"] == label for j in _jobs_snapshot()):
            return {"ok": False, "error": "该账号已有任务在跑"}
        max_sections = int(body.get("max_sections") or 3)
        args = ["--label", label, "--account-label", label,
                "--port", str(acc.port),
                "--max-sections", str(max(1, min(max_sections, 12)))]
        if body.get("sections"):
            args += ["--sections", str(body["sections"])]
        return _start(settings, label, "study", "run_gxks.py", *args,
                      message=f"开始刷课，最多 {max_sections} 节；随时可停")

    @app.post("/api/stop")
    def do_stop(body: dict) -> dict:
        label = _safe_label(settings, str(body.get("label") or ""))
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
