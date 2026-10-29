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
from ..config import Settings
from ..store import gxks_ops
from ..store.schema_gxks import open_question_bank

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"

# 每个账号同时只允许一个任务：两个脚本抢同一个 Chrome 会话会互相顶掉页面
_JOBS: dict[str, dict[str, Any]] = {}
_JOBS_LOCK = threading.Lock()


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
                     "daily_minutes": acc.daily_minutes, "enabled": acc.enabled})
    return rows


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
            "accounts_file": gaccounts.default_path().exists(),
            "label": label,
            "auto_submit": bool(settings.raw.get("gxks", {}).get("auto_submit", False)),
            "jobs": _jobs_snapshot(),
            "sections": _read_json(_latest(f"sections_{label}.json", debug_dir)),
            "work": _read_json(_latest(f"work_{label}_*.json", debug_dir)),
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
