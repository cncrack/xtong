"""FastAPI 应用工厂：只读视图 + 学习会话控制；凭据永不出现在任何响应中。"""

from __future__ import annotations

import base64
import os as _os
import re
import sqlite3
import subprocess
import sys
import time
from collections import deque

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

from ..config import Settings
from ..core.accounts import list_accounts
from ..store.db import connect

# 学习会话进程表：account_id → {proc, course}（面板重启后进程仍活但失联，属已知限制）
_JOBS: dict[int, dict] = {}
_AUTO: dict | None = None  # 定时调度守护进程（面板一键开关）


# 脚本/命令行客户端的 UA 特征：命中即视为“非真人浏览器”
_SCRIPT_UA_HINTS = ("httpx", "requests", "aiohttp", "urllib", "curl", "wget",
                    "python-", "go-http-client", "okhttp", "node-fetch", "testclient")


def _looks_script_ua(ua: str) -> bool:
    """UA 像脚本/命令行（空 UA 也算）——真人浏览器一定带 Mozilla。"""
    low = (ua or "").strip().lower()
    return not low or any(k in low for k in _SCRIPT_UA_HINTS)


def _auto_who(request, source: str) -> str:
    """标识“谁发起的启停”，让「📜 日志」能追溯（2026-10-27 加固，同日纠偏）。

    背景：10-26 有四次来源不明的守护启动，靠历史文件与日志才排到是诊断脚本。
    首版只判 `source`，但诊断脚本为模拟真实点击同样传 `source=ui` → 脚本与真人
    点击在 run_logs 里一模一样（2026-10-27 实测 8 次全为脚本却显示“面板按钮”）。
    现改为三重证据：
      ① `X-Diag: <脚本名>` 头或 `source=diag` → 诊断脚本（脚本应主动声明）；
      ② 自称 ui 但 UA 不像真浏览器（httpx/requests/curl/空 UA…）→ 记“（自称 UI）”；
      ③ 自定义 UI 且 UA 含 Mozilla → 才记「面板按钮」。
    """
    diag, ua = "", ""
    if request is not None:
        try:
            diag = (request.headers.get("x-diag") or "").strip()[:40]
            ua = (request.headers.get("user-agent") or "")[:60]
        except Exception:
            diag, ua = "", ""
    if diag:
        return f"诊断脚本({diag})"
    if source == "diag":
        return "诊断脚本"
    low = ua.lower()
    if source == "ui":
        if "mozilla" in low and not _looks_script_ua(ua):
            return "面板按钮"
        return f"API({ua or '无UA'})（自称 UI）"
    if "httpx" in low:
        return "API(httpx)"
    if "requests" in low:
        return "API(requests)"
    if "mozilla" in low:
        return "API(浏览器)"
    return f"API({ua or '无UA'})"


def _who_level(who: str) -> str:
    """“自称 UI 但不像浏览器”要黄字标出来，否则日志里看不出伪装。"""
    return "WARNING" if "自称 UI" in who else "INFO"


def _spawn(root_str: str, *cmd: str):
    """拉起子进程（会话/自动调度）。子进程输出必须落盘，不许进 DEVNULL。

    2026-10-25 教训：DEVNULL 吞掉了 #1 会话的 traceback，表现为"点了开始、
    日志只有 session-start、界面不显示刷课中、进程已死"的哑故障，无法定位。
    现在每次拉起都留一份 data/logs/sub-<命令>-<HHMMSS>.log。
    """
    import pathlib as _pl
    import sys as _sys

    from ..log import get_logger  # 模块级未导入（同文件其他位置均在函数内取）

    if getattr(_sys, "frozen", False):  # exe 模式：自身即 CLI 入口
        argv = [_sys.executable, *cmd]
    else:
        # -u：不缓冲 stdout。不加时守护/会话的 print（计划时刻、跳过原因、触发结果）
        # 全卡在缓冲区，进程一被杀就丢 → 日志只剩一行“守护启动”，无法核查决策
        # （2026-10-26 实测：只剩 logging 那条，print 全没）
        argv = [_sys.executable, "-u", "-m", "xxt.cli", *cmd]
    log_dir = _pl.Path(root_str) / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"sub-{cmd[0] if cmd else 'cli'}-{time.strftime('%Y%m%d-%H%M%S')}.log"
    fh = open(log_path, "ab")
    try:
        # 子进程 stdout 默认按系统码页（cp936）编码，中文会写成乱码——强制 UTF-8
        env = dict(_os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1",
                   PYTHONUNBUFFERED="1")
        proc = subprocess.Popen(argv, cwd=root_str, stdout=fh, stderr=fh, env=env)
    finally:
        fh.close()  # 句柄已由子进程继承
    get_logger("panel").info("子进程 %s 已启动 pid=%s，输出 → %s",
                             " ".join(cmd[:2]), proc.pid, log_path.name)
    return proc


def _pid_alive(pid: int, marker=None) -> bool:
    """进程是否存活；给了标记文件时再核对 pid 归属（防 pid 复用误判/误杀）。

    2026-10-27：此前只用 OpenProcess 判“存在”，pid 被系统复用后会误报“守护已在运行”
    （点不起来）或在停止时 taskkill 掉无关进程。归属判定见 core/runmark.pid_owns_file，
    已覆盖 Linux（/proc），Docker 部署同样适用。
    """
    from ..core.runmark import pid_alive, pid_owns_file

    if pid <= 0 or not pid_alive(pid):
        return False
    return True if marker is None else pid_owns_file(marker, pid)


def _marker_jobs(settings: Settings) -> dict[int, dict]:
    """扫 data/run/<id>.json 会话标记（跨面板重启仍可见的运行中会话）。"""
    import json as _json

    run_dir = settings.data_dir / "run"
    out: dict[int, dict] = {}
    if not run_dir.exists():
        return out
    for f in run_dir.glob("*.json"):
        try:
            info = _json.loads(f.read_text(encoding="utf-8"))
            if _pid_alive(int(info["pid"]), f):
                out[int(f.stem)] = {"course": info.get("course", "?"),
                                    "since": info.get("since", ""), "pid": info["pid"],
                                    "external": True}
            else:
                f.unlink(missing_ok=True)  # 进程已死，清标记
        except Exception:
            continue
    return out

_PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>XXT 学习面板</title><style>
:root{--bg:#eef1f7;--card:#fff;--ink:#1e2434;--muted:#7b8398;--line:#e6e9f2;
--accent:#5b6cff;--accent2:#8f7bff;--ok:#18a05d;--warn:#e8930c;--bad:#d64545;
--grad:linear-gradient(120deg,#4756d7,#7a5bff 60%,#9d64ff)}
*{box-sizing:border-box}
body{font-family:system-ui,"Microsoft YaHei",sans-serif;margin:0;background:var(--bg);color:var(--ink)}
::-webkit-scrollbar{width:9px;height:9px}::-webkit-scrollbar-thumb{background:#c9cfe0;border-radius:9px}
::selection{background:#cdd8ff}
.wrap{max-width:1440px;margin:0 auto;padding:1.4rem 1.1rem 2.5rem}
.hero{background:var(--grad);color:#fff;border-radius:20px;padding:1.15rem 1.4rem;
display:flex;align-items:center;gap:1.1rem;flex-wrap:wrap;
box-shadow:0 10px 30px rgba(70,80,220,.28),inset 0 1px 0 rgba(255,255,255,.25)}
.hero h1{font-size:1.32rem;margin:0;letter-spacing:.5px}
.hero small{opacity:.8;font-weight:400;display:block;font-size:.78rem;margin-top:.15rem}
.metrics{display:flex;gap:1.5rem;margin-left:.4rem}
.metric{text-align:center}
.metric b{font-size:1.45rem;font-weight:800;display:block;line-height:1.1}
.metric span{font-size:.72rem;opacity:.8}
.spacer{flex:1}
#q{border:none;border-radius:13px;padding:.55rem 1rem;font-size:.9rem;min-width:225px;outline:none;
box-shadow:inset 0 1px 3px rgba(0,0,0,.12)}
.btn{border:none;border-radius:13px;padding:.55rem 1.05rem;font-size:.88rem;cursor:pointer;
background:rgba(255,255,255,.18);color:#fff;border:1px solid rgba(255,255,255,.35);transition:.15s}
.btn:hover{background:rgba(255,255,255,.34);transform:translateY(-1px)}
.toolbar{display:flex;align-items:center;gap:.7rem;margin:1.05rem 0 .85rem;flex-wrap:wrap}
.viewbar{display:flex;align-items:center;gap:.6rem;margin:1.05rem 0 .85rem;flex-wrap:wrap}
.vtabs{display:flex;gap:.25rem;background:rgba(255,255,255,.5);border:1px solid var(--line);border-radius:12px;padding:.22rem}
.vtab{background:transparent;border:0;border-radius:9px;padding:.36rem .85rem;font:inherit;font-size:.86rem;
 color:var(--muted);cursor:pointer;transition:.15s}
.vtab:hover{color:var(--ink)}
.vtab.active{background:var(--card);color:var(--ink);font-weight:700;box-shadow:0 1px 4px rgba(30,36,52,.12)}
.vtab b{font-weight:600;color:var(--accent);margin-left:.25rem;font-size:.78rem}
.batch{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:.65rem .85rem;margin-bottom:.9rem}
.bhead{display:flex;align-items:center;gap:.6rem;flex-wrap:wrap;font-size:.85rem}
.bhead .spacer{flex:1}
.bhead input{width:190px;padding:.22rem .5rem;border:1px solid var(--line);border-radius:9px;
 font:inherit;font-size:.78rem;background:var(--bg)}
.toast{position:fixed;left:50%;bottom:28px;transform:translate(-50%,20px);opacity:0;
 background:rgba(38,150,90,.96);color:#fff;padding:.5rem 1rem;border-radius:11px;
 font-size:.85rem;box-shadow:0 6px 22px rgba(0,0,0,.18);pointer-events:none;z-index:99;
 transition:opacity .18s,transform .18s}
.toast.show{opacity:1;transform:translate(-50%,0)}
.toast.bad{background:rgba(190,60,60,.96)}
.picked{display:flex;flex-direction:column;gap:.2rem;max-height:150px;overflow:auto;
 margin:.3rem 0;font-size:.8rem}
.picked .pkrow{display:flex;align-items:center;gap:.4rem;background:var(--bg);
 border:1px solid var(--line);border-radius:9px;padding:.2rem .45rem}
.picked .pkrow b{font-weight:600}
.picked .pkrow .x{margin-left:auto;cursor:pointer;color:var(--muted);font-weight:700;
 padding:0 .25rem}
.picked .pkrow .x:hover{color:#c0392b}
.pbar select{padding:.2rem .4rem;border:1px solid var(--line);border-radius:8px;
 font:inherit;font-size:.78rem;background:var(--bg)}
.prow{display:flex;align-items:center;gap:.5rem;flex-wrap:wrap;padding:.35rem 0;border-top:1px dashed var(--line);font-size:.8rem}
.prow:first-child{border-top:0}
.paccs{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:.3rem .8rem;max-height:220px;overflow:auto;margin-top:.3rem}
.paccs label{font-size:.8rem;display:flex;gap:.35rem;align-items:center}
.paccs label.off{opacity:.45}
.pbar{display:flex;gap:.5rem;align-items:center;flex-wrap:wrap;margin-top:.3rem;font-size:.78rem}
.pbar input[type=text],.pbar input:not([type]){width:180px;padding:.2rem .45rem;border:1px solid var(--line);border-radius:8px;font:inherit;font-size:.78rem}
.pbar .chk{display:flex;gap:.25rem;align-items:center;color:var(--muted);cursor:pointer}
.hbar{display:flex;flex-direction:column;gap:.3rem;margin:.25rem 0 .35rem}
.hrow{display:flex;align-items:center;gap:.55rem;font-size:.76rem}
.hd{flex:0 0 46px;color:var(--muted);white-space:nowrap}
.htrack{flex:1;height:15px;background:var(--bg);border:1px solid var(--line);border-radius:8px;overflow:hidden}
.htrack i{display:block;height:100%;background:var(--grad);border-radius:8px;transition:width .3s}
.hv{flex:0 0 120px;text-align:right;color:var(--muted);white-space:nowrap}
.seg{display:inline-flex;gap:.2rem;background:var(--bg);border:1px solid var(--line);border-radius:10px;padding:.15rem}
.segb{background:transparent;border:0;border-radius:8px;padding:.24rem .6rem;font:inherit;font-size:.78rem;
 color:var(--muted);cursor:pointer}
.segb.on{background:var(--card);color:var(--ink);font-weight:700}
.bar{height:8px;border-radius:6px;background:var(--grad);min-width:2px}
.statrow{display:grid;grid-template-columns:minmax(90px,1.2fr) 4fr 46px 60px 60px;gap:.5rem;
 align-items:center;padding:.3rem 0;border-bottom:1px dashed var(--line);font-size:.8rem}
.statrow b{font-weight:600}
.statrow .flag{font-size:.65rem;color:var(--warn,#b8860b);margin-left:.25rem;cursor:pointer;white-space:nowrap}
#profBody{overflow-x:auto;margin-top:.3rem}
/* 画像表：列宽按内容收紧（2026-10-27）—— 不再 width:100% 强行铺满，窄列不再大片空白 */
#profBody .ptable{width:max-content;max-width:100%;min-width:0}
#profBody .ptable th,#profBody .ptable td{padding:.26rem .35rem}
#profBody .ptable .pf{width:auto;min-width:0}
#profBody .ptable td:nth-child(3) .pf{width:8em}
#profBody .ptable td:nth-child(5) .pf{width:9em}
#profBody .ptable td:nth-child(6) .pf,#profBody .ptable td:nth-child(8) .pf{width:6em}
#profBody .ptable td:nth-child(7) .pf{width:9em}
#profBody .ptable td:nth-child(9) .pf{width:11em}
#profBody .ptable td:nth-child(10) .pf{width:8em}
#profBody .ptable td:nth-child(11) .pf{width:8em}
#profBody .ptable .dirty{color:#c0560b;font-size:.68rem;margin-right:.25rem;white-space:nowrap}
.ptable{width:100%;border-collapse:collapse;font-size:.75rem;background:var(--card);
 border:1px solid var(--line);border-radius:12px;overflow:hidden;min-width:1050px}
.ptable th,.ptable td{border-bottom:1px solid var(--line);padding:.3rem .4rem;text-align:left;
 vertical-align:middle;white-space:nowrap}
.ptable thead th{background:#f7f9fd;color:var(--muted);font-weight:600;font-size:.72rem}
.ptable tr:last-child td{border-bottom:none}
.ptable tbody tr:hover td{background:#f7f9fd}
.ptable .pf{width:100%;min-width:70px;padding:.16rem .35rem;border:1px solid var(--line);
 border-radius:7px;font:inherit;font-size:.74rem;background:var(--bg)}
.ptable .ro{color:var(--muted)}
.ptable .cellops{display:flex;gap:.3rem}
.ptable.pick label{display:flex;align-items:center;gap:.35rem;cursor:pointer}
.ptable.pick tr.off td{opacity:.45}
.ptable.pick tr.off label{cursor:not-allowed}
/* 选择账号弹窗：容器改为块级+横向滚动（原先 grid 会被 13 列表格撑破），并调窄各列 */
#pk_list{display:block;overflow:auto;max-height:60vh;margin-top:.3rem}
.ptable.pick{min-width:980px}
.ptable.pick th:nth-child(1),.ptable.pick td:nth-child(1){width:32px;text-align:center}
.ptable.pick th:nth-child(2),.ptable.pick td:nth-child(2){width:46px}
.ptable.pick th:nth-child(3),.ptable.pick td:nth-child(3){width:82px}
.ptable.pick th:nth-child(4),.ptable.pick td:nth-child(4){width:72px}
.ptable.pick th:nth-child(5),.ptable.pick td:nth-child(5){width:82px}
.ptable.pick th:nth-child(6),.ptable.pick td:nth-child(6){width:100px}
.ptable.pick th:nth-child(7),.ptable.pick td:nth-child(7){width:60px}
.ptable.pick th:nth-child(8),.ptable.pick td:nth-child(8){width:92px}
.ptable.pick th:nth-child(9),.ptable.pick td:nth-child(9){width:72px}
.ptable.pick th:nth-child(10),.ptable.pick td:nth-child(10){width:108px}
.ptable.pick th:nth-child(11),.ptable.pick td:nth-child(11){width:92px}
.ptable.pick th:nth-child(12),.ptable.pick td:nth-child(12){width:68px}
.ptable.pick th:nth-child(13),.ptable.pick td:nth-child(13){width:92px}
.toolbar .muted{font-size:.85rem}
#synced{margin-left:auto;font-size:.75rem;color:#a3aac0}
.flash{animation:flash 1.2s ease}
@keyframes flash{0%{color:#fff;text-shadow:0 0 14px #7dffb0}100%{}}
#today.flash{animation:flashg 1.6s ease}
@keyframes flashg{0%,40%{text-shadow:0 0 16px #9fffc6;transform:scale(1.25)}100%{transform:scale(1)}}
select,input[type=time],input[type=number],input[type=text],input[type=password],input[type=date]
{border:1px solid var(--line);border-radius:10px;padding:.42rem .6rem;font-size:.86rem;
background:var(--card);color:var(--ink);outline:none;transition:.15s}
select:focus,input:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(91,108,255,.12)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(410px,1fr));gap:1rem}
.card{background:var(--card);border-radius:18px;padding:1.05rem 1.15rem;border:1px solid var(--line);
box-shadow:0 1px 4px rgba(25,35,70,.05);transition:.2s;position:relative;overflow:hidden}
.card:hover{transform:translateY(-3px);box-shadow:0 12px 28px rgba(25,35,70,.13)}
.card.running{border-color:#9fe3c0}
.card.running:before{content:"";position:absolute;left:0;top:0;bottom:0;width:4px;background:var(--ok)}
.card.hit{outline:2px solid var(--accent);outline-offset:1px}
.card .head{display:flex;align-items:flex-start;gap:.65rem;margin-bottom:.35rem;flex-wrap:wrap}
.avatar{width:38px;height:38px;border-radius:12px;background:var(--grad);color:#fff;
display:flex;align-items:center;justify-content:center;font-size:1.1rem;font-weight:700;flex:none;
box-shadow:0 3px 8px rgba(70,80,220,.3)}
.head .names{min-width:0;flex:1 1 130px}
.who{font-size:1.05rem;font-weight:800;line-height:1.25;word-break:break-all}
.who .nicktag{font-size:.6rem;font-weight:600;color:var(--muted);border:1px solid var(--line);
 border-radius:6px;padding:0 .25rem;margin-left:.35rem;vertical-align:2px;white-space:nowrap}
.sub{font-size:.72rem;color:var(--muted);margin-top:.1rem}
.head .state{display:flex;gap:.3rem;align-items:center;flex-wrap:wrap;flex:none;margin-left:auto;
padding-top:.12rem}
/* 操作按钮单独一行、右对齐：2026-10-27 修复“■ 停止被卡片右边界裁掉”
   （此前放在 .head 里且 flex:none，宽度被 6 个按钮撑破，outer1 又 overflow:hidden） */
.card .ops{display:flex;gap:.35rem;align-items:center;justify-content:flex-end;flex-wrap:wrap;
margin:.05rem 0 .4rem}
/* 失败警示条（2026-10-27 Q20 + Q26 布局约束）：只占一行、超长省略、按钮最多 3 个，
   且不往 .head / .ops 里塞东西，避免把会话 51 调好的卡片布局再挤爆。 */
.alertbar{display:flex;align-items:center;gap:.4rem;background:#fff8e9;border:1px solid #f0dcb4;
border-radius:10px;padding:.24rem .5rem;margin:-.2rem 0 .45rem;font-size:.75rem;min-width:0}
.alertbar .ab-tag{flex:none;font-weight:800;color:#96661a;white-space:nowrap}
.alertbar .ab-txt{flex:1 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis;
white-space:nowrap;color:#7d6027}
.alertbar .ab-acts{flex:none;display:flex;gap:.3rem}
.alertbar .ab-acts .minibtn{padding:.1rem .45rem;font-size:.7rem}
.minibtn{background:var(--bg);border:1px solid var(--line);border-radius:9px;padding:.22rem .6rem;
font-size:.75rem;cursor:pointer;transition:.15s;color:var(--ink);white-space:nowrap}
.minibtn:hover{border-color:var(--accent);color:var(--accent)}
.minibtn.danger:hover{border-color:var(--bad);color:var(--bad)}
.minibtn.ghost{opacity:.55;font-weight:400}
.minibtn.ghost:hover{opacity:1}
.minibtn.primary{border-color:var(--accent);color:var(--accent);font-weight:700}
.badge{font-size:.68rem;border-radius:99px;padding:.14rem .6rem;font-weight:700;white-space:nowrap}
.b-ok{background:#e3f6ec;color:var(--ok)}.b-warn{background:#fdf2df;color:var(--warn)}
.b-info{background:#e8edff;color:#4353d8}
.pulse{animation:pulse 1.6s infinite}@keyframes pulse{50%{opacity:.55}}
.chips{display:flex;gap:.3rem;flex-wrap:wrap;margin:.05rem 0 .55rem}
.chip{font-size:.68rem;color:var(--muted);background:var(--bg);border:1px solid var(--line);
border-radius:7px;padding:.12rem .5rem}
.course{margin:.5rem 0;padding:.45rem .55rem;border-radius:11px;border:1px solid transparent;transition:.15s}
.course:hover{background:var(--bg)}
.course.hit{background:#f0f3ff;border-color:#cdd8ff}
.course .nm{display:block;font-size:.84rem;line-height:1.35;word-break:break-all;
display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.course .t2{display:flex;justify-content:space-between;align-items:center;margin-top:3px;gap:.5rem}
.nums{font-size:.78rem;color:var(--muted);font-weight:700;flex:1 1 auto;min-width:0;
overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.nums b{color:var(--ink);font-size:.82rem}
/* “已学/总约”单独一行：2026-10-27 它与百分比挤在同一行时，文字长的卡片会把
   右侧按钮挤到换行 → 两张卡高度不一样（用户截图指出） */
.spent{font-size:.7rem;color:var(--muted);margin-top:.15rem}
.play{border:1px solid #bfe5d2;background:#eef9f3;color:var(--ok);border-radius:9px;
padding:.2rem .65rem;font-size:.72rem;font-weight:700;cursor:pointer;transition:.15s;white-space:nowrap;
min-width:88px;text-align:center}
.play:hover{background:var(--ok);color:#fff;border-color:var(--ok);
box-shadow:0 3px 10px rgba(24,160,93,.35);transform:translateY(-1px)}
.stopc{border:1px solid #bfe5d2;background:#e3f6ec;color:var(--ok);border-radius:9px;
padding:.2rem .65rem;font-size:.72rem;font-weight:700;cursor:pointer;transition:.15s;white-space:nowrap;
min-width:88px;text-align:center}
.stopc:hover{background:var(--bad);border-color:var(--bad);color:#fff;
box-shadow:0 3px 10px rgba(214,69,69,.35);transform:translateY(-1px)}
.morebtn{width:100%;margin-top:.5rem;background:none;border:1px dashed var(--line);
border-radius:10px;padding:.4rem .5rem;font-size:.76rem;color:var(--muted);
cursor:pointer;transition:.15s;text-align:center}
.morebtn:hover{border-color:var(--accent);color:var(--accent);border-style:solid}
.more{margin-top:.15rem}
.bar{height:6px;background:var(--bg);border-radius:4px;overflow:hidden;margin-top:5px}
.bar>i{display:block;height:100%;width:0;border-radius:4px;background:var(--grad);transition:width .6s ease}
.pager{display:flex;align-items:center;gap:.55rem;justify-content:center;margin-top:1.1rem;font-size:.86rem}
.pager button{border:1px solid var(--line);background:var(--card);border-radius:10px;
padding:.32rem .8rem;cursor:pointer;transition:.15s}
.pager button:hover:not(:disabled){border-color:var(--accent)}
.pager button:disabled{opacity:.35;cursor:default}
section{margin-top:1.7rem}section>h2{font-size:.9rem;color:var(--muted);margin:0 0 .65rem;
display:flex;align-items:center;gap:.4rem}
section>h2:before{content:"";width:4px;height:14px;border-radius:2px;background:var(--grad)}
table{width:100%;border-collapse:collapse;font-size:.8rem;background:var(--card);
border:1px solid var(--line);border-radius:14px;overflow:hidden}
td{padding:.5rem .75rem;border-bottom:1px solid var(--line)}tr:last-child td{border-bottom:none}
tr:hover td{background:#f7f9fd}
.ev{font-weight:700}.ev-good{color:var(--ok)}.ev-warn{color:var(--warn)}.ev-info{color:#4353d8}
.dot{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:5px;vertical-align:1px}
.mono{font-family:Consolas,monospace;color:var(--muted);font-size:.76rem}.muted{color:var(--muted)}
mark{background:#ffe17d;border-radius:3px;padding:0 2px;color:inherit}
footer{margin-top:2.2rem;text-align:center;font-size:.74rem;color:#a5acbf}
dialog{border:none;border-radius:20px;padding:1.25rem 1.45rem;width:min(720px,95vw);
max-width:96vw;max-height:88vh;overflow:auto;box-shadow:0 24px 70px rgba(20,30,70,.35);
resize:both;min-width:340px;min-height:220px}
#planDlg{width:min(840px,96vw)}
#pickDlg{width:min(1180px,96vw)}
dialog::backdrop{background:rgba(20,25,45,.45);backdrop-filter:blur(3px)}
dialog h3{margin:0 0 .95rem}
.frow{display:flex;align-items:center;justify-content:space-between;gap:1rem;margin:.7rem 0}
.frow label{font-size:.9rem}
.switch{position:relative;width:42px;height:23px;flex:none}
.switch input{opacity:0;width:0;height:0}
.switch i{position:absolute;inset:0;background:#cdd3e4;border-radius:99px;transition:.2s;cursor:pointer}
.switch i:before{content:"";position:absolute;width:17px;height:17px;border-radius:50%;
background:#fff;top:3px;left:3px;transition:.2s;box-shadow:0 1px 3px rgba(0,0,0,.25)}
.switch input:checked+i{background:var(--ok)}
.switch input:checked+i:before{transform:translateX(19px)}
.dlgbtns{display:flex;gap:.6rem;justify-content:flex-end;margin-top:1.15rem}
.frm{display:grid;grid-template-columns:155px 1fr;gap:.15rem .9rem;align-items:center;margin:.6rem 0}
.frm label{font-size:.88rem}
.frm small{display:block;color:var(--muted);font-size:.72rem;margin-top:.1rem}
.llmbox{background:var(--bg);border:1px solid var(--line);border-radius:14px;padding:.8rem .9rem}
.llmgrid{display:grid;grid-template-columns:1fr 1fr;gap:.55rem .7rem}
.llmgrid label{display:flex;flex-direction:column;gap:.25rem;font-size:.78rem;color:var(--muted)}
.llmgrid .span2{grid-column:span 2}
.llmgrid input{width:100%;font-size:.86rem}
.tabs{display:flex;gap:.4rem;margin:.6rem 0 .8rem;border-bottom:1px solid var(--line);padding-bottom:.55rem}
.qa{margin:.7rem 0;border:1px solid var(--line);border-radius:12px;overflow:hidden}
.qa .q{background:var(--bg);padding:.55rem .75rem;font-size:.84rem;border-left:3px solid var(--accent)}
.qa .a{padding:.55rem .75rem;font-size:.87rem;line-height:1.55;background:#fff}
.qa .a .chip{margin-right:.4rem}
.tab{border:none;background:none;padding:.35rem .8rem;font-size:.85rem;cursor:pointer;
color:var(--muted);border-radius:9px;transition:.15s}
.tab:hover{color:var(--accent)}
.tab.active{background:#e8edff;color:#4353d8;font-weight:700}
.primary{background:var(--grad);color:#fff;border:none;border-radius:12px;
padding:.58rem 1.25rem;cursor:pointer;font-size:.9rem;transition:.15s}
.primary:hover{transform:translateY(-1px);box-shadow:0 5px 14px rgba(70,80,220,.35)}
.ghost{background:var(--bg);border:1px solid var(--line);border-radius:12px;
padding:.58rem 1.05rem;cursor:pointer;font-size:.9rem;transition:.15s}
.ghost:hover{border-color:var(--accent)}
.hint{font-size:.76rem;color:var(--muted);margin-top:.65rem}
</style></head><body><div class="wrap">
<div class="hero">
  <div><h1>XXT 学习面板</h1><small>多账号网课助手</small></div>
  <div class="metrics">
    <div class="metric"><b id="accN">-</b><span>账号</span></div>
    <div class="metric"><b id="today">-</b><span>今日视频</span></div>
    <div class="metric"><b id="runN">-</b><span>刷课中</span></div>
  </div>
  <span class="spacer"></span>
  <input id="q" placeholder="🔍 搜索账号或课程名…">
  <button class="btn" title="添加一个学习通账号（凭据加密存储）" onclick="openAdd()">＋ 账号</button>
  <button class="btn" id="autoBtn" title="定时调度：各账号每天在设定/随机的时刻自动开刷（额度内自停）" onclick="toggleAuto()">⏱ 定时调度</button>
  <button class="btn" title="全局设置：弹题开关/学习窗口/每日双额度（账号可在✎管理里单独覆盖）" onclick="document.getElementById('dlg').showModal()">⚙ 设置</button>
</div>
<div class="viewbar">
  <div class="vtabs">
    <button class="vtab active" data-view="accounts" onclick="switchView('accounts')"
      title="每个账号一张卡：额度、课程、作业与刷课控制">📇 账号卡片<b id="vtabAccN"></b></button>
    <button class="vtab" data-view="plans" onclick="switchView('plans')"
      title="可选：把某门课排给多个账号（不设也能刷：手动 ▶ 指定课，定时则按进度挑一门）；开跑时间仍按各账号定时/窗口错峰">📚 学习安排<b id="vtabPlanN"></b></button>
    <button class="vtab" data-view="stats" onclick="switchView('stats')"
      title="按年级/专业/学期/课程/账号统计刷课战果（数据来源：本地日志，不访问平台）">📈 统计</button>
    <button class="vtab" data-view="logs" onclick="switchView('logs')"
      title="全局时间线：谁在什么时候学了什么（可按账号筛选）">📜 日志<b id="vtabLogN"></b></button>
  </div>
  <span style="flex:1"></span>
  <span id="synced">自动刷新中…</span>
</div>
<div id="viewAccounts">
  <div class="toolbar">
    <select id="psize"><option value="10">每页 10 张</option><option value="20" selected>每页 20 张</option><option value="50">每页 50 张</option></select>
    <span class="muted" id="accInfo"></span>
  </div>
  <div class="grid" id="accounts"><div class="card muted">加载中…</div></div>
  <div class="pager" id="pager"></div>
</div>
<div id="viewPlans" style="display:none">
  <section class="batch">
    <div class="bhead"><b>📚 学习安排</b>
      <span class="muted">统一指定课程；开跑时间仍按各账号自己的定时/窗口错峰（不会同时开刷，也不会重复多开）</span>
      <span class="spacer"></span>
      <input id="planQ" placeholder="🔍 搜学习安排名/课程/账号" style="width:190px"
        oninput="renderPlans()">
      <button class="minibtn" title="可选：把某门课统一排给多个账号" onclick="openPlan()">＋ 新建学习安排</button>
    </div>
    <div id="planList" class="muted" style="font-size:.8rem">加载中…</div>
  </section>
</div>
<div id="viewStats" style="display:none">
  <section class="batch">
    <div class="bhead"><b>📈 学习统计</b>
      <span class="muted">数据全部来自本地日志（不访问平台）；口径与额度一致：视频数 / 观看分钟</span>
      <span class="spacer"></span>
      <span class="seg" id="statBy">
        <button class="segb on" data-by="grade" onclick="setStatBy('grade')">年级</button>
        <button class="segb" data-by="major" onclick="setStatBy('major')">专业</button>
        <button class="segb" data-by="term" onclick="setStatBy('term')">学期</button>
        <button class="segb" data-by="course" onclick="setStatBy('course')">课程</button>
        <button class="segb" data-by="account" onclick="setStatBy('account')">账号</button>
      </span>
      <select id="statDays" onchange="loadStats()">
        <option value="1">今日</option><option value="7" selected>最近 7 天</option>
        <option value="30">最近 30 天</option><option value="0">全部</option>
      </select>
      <input id="statQ" placeholder="🔍 关键字（课程/账号/学号/专业…）" style="width:210px"
        oninput="clearTimeout(window._sqT);window._sqT=setTimeout(loadStats,300)">
    </div>
    <div id="statTrend" class="muted" style="font-size:.82rem"></div>
    <div id="statBody" class="muted" style="font-size:.82rem">加载中…</div>
  </section>
  <section class="batch">
    <div class="bhead"><b>🏷 画像设置</b>
      <span class="muted">年级/专业/学期参与统计分组；姓名/学号用于识别与搜索；学校/院系/班级/平台昵称只展示。平台读取只补空，不足的手补</span>
      <span class="spacer"></span>
      <input id="profQ" placeholder="🔍 搜 账号/备注名/姓名/平台昵称/学号/年级/专业/学期/学校/院系/班级" style="width:200px"
        oninput="renderProfiles()">
    </div>
    <div id="profBody" class="muted" style="font-size:.82rem">加载中…</div>
  </section>
</div>
<div id="viewLogs" style="display:none">
  <section class="batch">
    <div class="bhead"><b>📜 日志</b>
      <span class="muted">业务事件存数据库（含定时调度的计划/触发/跳过）；系统日志读 data/logs/xxt.log 与 sub-*.log。只读本地，不访问平台</span>
      <span class="spacer"></span>
      <select id="logSrc" onchange="renderLogs()" title="业务事件=账号做了什么事；系统日志=面板与子进程的技术日志">
        <option value="event" selected>业务事件</option>
        <option value="sys-warn">系统日志（警告+）</option>
        <option value="sys-info">系统日志（全部）</option>
        <option value="sys-sub">含子进程输出</option>
      </select>
      <input id="logAcc" list="logAccList" style="width:190px"
        placeholder="全部账号（输 #1 或姓名）" title="可输入账号编号（如 #2）或姓名/备注/平台昵称模糊匹配；清空=全部"
        oninput="LOGACC=this.value;renderLogs()">
      <datalist id="logAccList"></datalist>
      <select id="logN" onchange="renderLogs(+this.value)">
        <option value="20" selected>最近 20 条</option>
        <option value="50">最近 50 条</option>
        <option value="100">最近 100 条</option>
        <option value="300">最近 300 条</option>
      </select>
      <label class="muted" style="display:flex;align-items:center;gap:4px;font-size:.78rem"
        title="关掉后本页不再自动刷新（账号卡片/统计仍每 15 秒刷新），点「⟳ 刷一次」手动更新">
        <input type="checkbox" id="logAuto" onchange="LOG_AUTO=this.checked;localStorage.setItem('logAuto',this.checked?'1':'0');renderLogs()"> 自动刷新</label>
      <span id="logAt" class="muted" style="font-size:.72rem"></span>
      <button class="minibtn" onclick="renderLogs()">⟳ 刷一次</button>
    </div>
    <div id="logs" class="muted">加载中…</div>
  </section>
</div>
<footer>XXT · 多账号网课助手 · 本地面板（127.0.0.1）· 设置三层：全局 ⚙ &gt; 账号 ✎ &gt; 文件默认</footer>
</div>

<dialog id="dlg"><h3>⚙ 全局设置</h3>
<div class="tabs">
  <button class="tab active" onclick="switchTab(0,this)">⏱ 守护与节级策略</button>
  <button class="tab" onclick="switchTab(1,this)">📚 学习与作业</button>
  <button class="tab" onclick="switchTab(2,this)">🤖 AI 模型</button>
</div>
<div id="tab0">
  <div class="llmgrid">
    <label>每日最多自动发起<small>0=不限，1~10（安全默认 2）</small><input type="number" id="s_mt" min="0" max="10"></label>
    <label>开跑回读（秒）<small>15~300；确认会话真在跑</small><input type="number" id="s_cs" min="15" max="300"></label>
  </div>
  <h3 style="margin:.6rem 0 .4rem;font-size:.86rem">失败怎么退避 <span class="muted" style="font-size:.72rem;font-weight:400">指数递增 + 随机浮动，不像机器人</span></h3>
  <div class="llmgrid">
    <label>退避基数（分钟）<small>1~240；第一次失败等多久</small><input type="number" id="s_rm" min="1" max="240"></label>
    <label>随机浮动（%）<small>0~100；±这个比例内随机，不固定节奏</small><input type="number" id="s_rj" min="0" max="100"></label>
    <label>退避封顶（分钟）<small>15~480；再糟也不会超这个间隔</small><input type="number" id="s_rx" min="15" max="480"></label>
    <label>单日硬上限（次）<small>1~20；打不开/网络这类可恢复失败长退避再试的轮数，用尽才真停</small><input type="number" id="s_hc" min="1" max="20"></label>
  </div>
  <h3 style="margin:.6rem 0 .4rem;font-size:.86rem">单节怎么处理 <span class="muted" style="font-size:.72rem;font-weight:400">失败的节保持待学，不会被当成“已学”</span></h3>
  <div class="llmgrid">
    <label>单节点最多点几次<small>1~5；点不开算这节失败</small><input type="number" id="s_er" min="1" max="5"></label>
    <label>连续几节打不开收工<small>1~10；失败的节保持待学</small><input type="number" id="s_ms" min="1" max="10"></label>
    <label>连续非视频收工<small>1~20；作业/阅读穿过去</small><input type="number" id="s_ns" min="1" max="20"></label>
    <label>中断续播次数<small>0=关闭，最大 3</small><input type="number" id="s_pr" min="0" max="3"></label>
    <label>续播门槛（%）<small>30~95；已播够这个比例才值得续</small><input type="number" id="s_prr" min="30" max="95"></label>
    <label>每日登录预算<small>1~5 次；失败尝试也算</small><input type="number" id="s_lb" min="1" max="5"></label>
    <label class="span2">非视频节点关键词<small>逗号分隔；名字含这些词的节不会被当成“播放失败”</small><input type="text" id="s_nvm" placeholder="作业,测验,讨论,阅读,考试,练习,公告"></label>
  </div>
  <div class="hint" style="margin-top:.55rem">守护类改动需在面板「■ 停调度 → ▶ 启调度」后生效（守护只在启动时读一次）；节级改动对下一次学习会话生效。</div>
  <div style="border-top:1px solid var(--line);margin:.8rem 0"></div>
  <h3 style="margin:.2rem 0 .4rem;font-size:.86rem">🕘 最近配置变更 <span class="muted" style="font-size:.72rem;font-weight:400">每次保存都留痕旧值→新值（溯源用）</span></h3>
  <div id="auditList" style="font-size:.76rem;line-height:1.55"></div>
</div>
<div id="tab1" style="display:none">
  <div class="frow"><label>弹题 AI 作答（P2 功能，计分项）</label>
    <span class="switch"><input type="checkbox" id="s_quiz"><i></i></span></div>
  <div class="frow"><label>学习窗口开始</label><input type="time" id="s_ws"></div>
  <div class="frow"><label>学习窗口结束</label><input type="time" id="s_we"></div>
  <div class="frow"><label>每日时长额度（分钟，0 不限）<small style="display:block;color:var(--muted);font-size:.72rem">按本会话“真播完并记账”的视频时长计（拟人间隔/加载不算）；240 是上限，实际会乘一个随机系数</small></label><input type="number" id="s_min" style="width:90px"></div>
  <div class="frow"><label>每日任务额度（视频数，0 不限）</label><input type="number" id="s_task" style="width:90px"></div>
  <div class="frow"><label>作业策略全局默认<small>账号✎管理里单独设置的优先于此项</small></label>
    <select id="s_wm"><option value="owner">号主自理</option><option value="reference">AI 参考答案</option><option value="ai">AI 代答（semi）</option></select></div>
</div>
<div id="tab2" style="display:none">
  <h3 style="margin:.2rem 0 .5rem;font-size:.92rem">🤖 AI 模型 <span class="muted" style="font-size:.75rem;font-weight:400">全局，所有账号共用</span></h3>
  <div class="llmbox">
    <div style="margin-bottom:.5rem"><span class="muted" style="font-size:.75rem">提供商（点选载入；自定义项⨯删除）</span>
      <div id="provList" style="display:flex;gap:.35rem;flex-wrap:wrap;margin-top:.3rem"></div></div>
    <div class="llmgrid">
      <label>提供商名称<input type="text" id="s_lname" placeholder="如 MyProvider"></label>
      <label>模型名称<input type="text" id="s_lmodel" placeholder="如 agnes-2.5-flash"></label>
      <label class="span2">Base URL（OpenAI 兼容端点）<input type="text" id="s_lurl" placeholder="https://api.example.com/v1"></label>
      <label class="span2">API Key（加密存本机；<b>留空=沿用已存 Key</b>）<input type="password" id="s_lkeyv" placeholder="粘贴 Key（保存后不回显）"></label>
    </div>
    <div style="display:flex;gap:.5rem;align-items:center;flex-wrap:wrap;margin-top:.55rem">
      <button class="ghost" onclick="testLLM()">🔌 测试连通</button>
      <button class="ghost" onclick="saveProvider()">💾 保存为当前厂商</button>
      <button class="ghost" onclick="delProvider()">🗑 删除自定义</button>
      <span id="s_lhint" style="font-size:.76rem;color:var(--muted);flex:1;min-width:140px"></span></div>
    <div class="hint" id="s_lhint2" style="margin-top:.45rem"></div>
  </div>
</div>
  <div class="dlgbtns"><button class="ghost" onclick="this.closest('dialog').close()">取消</button>
    <button class="primary" onclick="saveSettings()">保存</button></div>
  <div class="hint">保存写入数据库全局覆盖层（对之后启动的学习会话生效；账号级覆盖仍用 xxt account set）。⚠ 一旦在这里保存过某键，该键就以数据库为准，再改 config/settings.toml 不会生效。</div>
</dialog>

<dialog id="planDlg"><h3 id="planTitle">📚 新建学习安排</h3>
  <div class="frow"><label>学习安排名</label><input type="text" id="pl_name" style="width:200px" placeholder="如：十月数据结构"></div>
  <div class="frm"><label>① 账号<small>先勾人；课程列表随后只显示这些账号的课</small></label>
    <span style="flex:1"></span>
    <button class="minibtn" title="打开账号选择器：可按 账号/备注名/姓名/平台昵称/学号/年级/专业/学期/学校/院系/班级 搜索，并按年级/专业筛选" onclick="openPicker()">＋ 选择账号</button>
    <button class="minibtn" onclick="clearPicked()">清空</button>
    <span class="muted" id="pl_count">已选 0 个</span>
  </div>
  <div id="pl_picked" class="picked muted">还没有选择账号 —— 点「＋ 选择账号」从列表里挑</div>
  <div class="frow"><label>② 课程<small>按平台课程ID锚定 · 同名不同班会分开列</small></label>
      <select id="pl_course" style="width:520px" onchange="updateCourseCov()"></select></div>
    <div id="pl_cov" class="hint" style="margin:.1rem 0 .35rem"></div>
  <div class="dlgbtns"><button class="ghost" onclick="this.closest('dialog').close()">取消</button>
    <button class="primary" id="planOk" onclick="doPlan()">创建（默认停用）</button></div>
  <div class="hint">学习安排锁定的是「平台课程ID + 你勾的账号」：以后<b>新增账号不用重建学习安排</b>，点「✎ 编辑」把新账号勾上即可；缺这门课的账号会被自动跳过（上方会列出）。开跑仍按各账号定时/学习窗口错峰，新建后默认「已停用」。</div>
</dialog>

<dialog id="pickDlg"><h3>选择账号</h3>
  <div class="pbar">
    <input id="pk_q" placeholder="🔍 搜 账号/备注名/姓名/平台昵称/学号/年级/专业/学期/学校/院系/班级" oninput="renderPick()">
    <label class="chk"><input type="checkbox" id="pk_onlyfree" checked onchange="renderPick()">只看未排</label>
    <select id="pk_school" onchange="renderPick()"><option value="">全部学校</option></select>
    <select id="pk_dept" onchange="renderPick()"><option value="">全部院系</option></select>
    <select id="pk_cls" onchange="renderPick()"><option value="">全部班级</option></select>
    <select id="pk_grade" onchange="renderPick()"><option value="">全部年级</option></select>
    <select id="pk_major" onchange="renderPick()"><option value="">全部专业</option></select>
    <button class="minibtn" onclick="pickAll(true)">全选可见</button>
    <button class="minibtn" onclick="pickAll(false)">清空</button>
    <span class="muted" id="pk_count">已选 0 个</span>
  </div>
  <div id="pk_list" class="paccs"></div>
  <div class="dlgbtns"><button class="ghost" onclick="document.getElementById('pickDlg').close()">取消</button>
    <button class="primary" onclick="pickApply()">确定添加</button></div>
  <div class="hint">勾选只是"加进学习安排"，不会立刻开刷；开跑仍按各账号定时/窗口错峰。</div>
</dialog>

<dialog id="addDlg"><h3>＋ 添加账号</h3>
  <div class="frow"><label>备注名（唯一）</label><input type="text" id="a_label" style="width:180px"></div>
  <div class="frow"><label>学习通账号/手机号</label><input type="text" id="a_user" style="width:180px"></div>
  <div class="frow"><label>密码</label><input type="password" id="a_pw" style="width:180px"></div>
  <div class="frow"><label>优先级（默认 1.0）</label><input type="number" step="0.5" id="a_pri" style="width:90px" value="1"></div>
  <div class="dlgbtns"><button class="ghost" onclick="this.closest('dialog').close()">取消</button>
    <button class="primary" onclick="doAdd()">加密录入</button></div>
  <div class="hint">凭据 AES-GCM 加密存储，仅本机；添加后需为其登录一次</div>
</dialog>

<dialog id="editDlg"><h3>✎ 账号管理 <span id="e_title" class="muted" style="font-size:.85rem"></span></h3>
  <div class="frm"><label>备注名<small>面板显示的名字，如"同学的号"</small></label><input type="text" id="e_label"></div>
  <div class="frm"><label>状态<small>paused=暂停调度（不自动学）</small></label><select id="e_status"><option value="active">active（启用）</option><option value="paused">paused（暂停）</option></select></div>
  <div class="frm"><label>优先级<small>课程轮换权重，越大越优先学</small></label><input type="number" step="0.5" id="e_pri"></div>
  <div class="frm"><label>定时开刷时刻<small>auto 守护的启动时间；空=系统在学习窗口开始后 0~120 分钟随机分配（面板与日志会显示分配结果）</small></label><input type="time" id="e_sa"></div>
  <div class="frm"><label>作业策略<small>空=跟随⚙全局默认（当前全局见⚙设置）；显式设置优先于全局。生效值以卡片"作业="标签为准</small></label>
    <select id="e_wm"><option value="">跟随全局默认（⚙设置）</option><option value="owner">号主自理（显式）</option><option value="reference">AI 参考答案（显式）</option><option value="ai">AI 代答 semi（显式）</option></select></div>
  <div class="frm"><label>学习窗口<small>该账号允许学习的时段（空=用全局 08:00–23:00）</small></label><span><input type="time" id="e_ws"> – <input type="time" id="e_we"></span></div>
  <div class="frm"><label>每日额度<small>时长(分)/任务(个)，0=用全局默认</small></label><span><input type="number" id="e_min" style="width:70px"> 分 / <input type="number" id="e_task" style="width:60px"> 个</span></div>
  <div class="frm"><label>有效期至<small>租用账号的到期日；过期自动停刷，空=永久</small></label><input type="date" id="e_vu"></div>
  <div class="frm"><label>备注<small>自由文本，如归还日期/号主</small></label><input type="text" id="e_note"></div>
  <div class="dlgbtns" style="justify-content:space-between">
    <span><button class="ghost" style="color:var(--bad)" onclick="doDel()">删除账号</button>
    <button class="ghost" onclick="doRenew()">续期 +30 天</button></span>
    <span><button class="ghost" onclick="this.closest('dialog').close()">取消</button>
    <button class="primary" onclick="doSave()">保存</button></span></div>
  <div class="hint" id="e_meta"></div>
</dialog>

<dialog id="workDlg"><h3>📝 作业参考答案 <span id="w_ch" class="muted" style="font-size:.85rem"></span>
  <button class="minibtn" style="margin-left:auto" onclick="doWork(WORK_ACC,WORK_CH,true)">🔄 重新生成</button></h3>
  <div class="frow"><label>对 AI 的要求<small>可空；如"300字以内""结合大学生视角""分点作答"</small></label>
    <input type="text" id="w_req" placeholder="无附加要求" style="flex:1"></div>
  <div id="w_body" class="log" style="max-height:52vh;overflow:auto;font-size:.86rem">加载中…</div>
  <div class="dlgbtns" style="justify-content:space-between">
    <button class="primary" title="把上述答案填入该账号作业页并点暂存（草稿，不提交）——随后到平台核对满意再提交"
      onclick="fillFromRef()">📝 回填到平台（填入+暂存，不提交）</button>
    <button class="ghost" onclick="this.closest('dialog').close()">关闭</button></div>
</dialog>

<script>
const esc=s=>String(s??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const badge=(t,c,ti)=>`<span class="badge ${c}"${ti?` title="${esc(ti)}"`:''}>${esc(t)}</span>`;
const fmtDur=m=>{m=m||0;return m>=60?`${Math.floor(m/60)}小时${m%60?m%60+'分':''}`:`${m}分钟`;};
const hl=(text,q)=>{ // 转义后对命中关键词加黄底高亮
  const safe=esc(text);
  if(!q)return safe;
  const i=safe.toLowerCase().indexOf(q.toLowerCase());
  return i<0?safe:safe.slice(0,i)+'<mark>'+safe.slice(i,i+q.length)+'</mark>'+safe.slice(i+q.length);
};
let ACCOUNTS=[],PAGE=1;
function matchCourses(a,q){return a.courses.filter(c=>String(c.name).toLowerCase().includes(q));}
function filtered(){
  const q=document.getElementById('q').value.trim().toLowerCase();
  if(!q)return ACCOUNTS.map(a=>({a,hits:[]}));
  return ACCOUNTS.map(a=>({a,hits:matchCourses(a,q)})).filter(x=>
    x.hits.length||[x.a.platform_username,x.a.display_name,(x.a.profile&&x.a.profile.name),
      x.a.label,x.a.status,String(x.a.id),x.a.student_no,x.a.note].some(
      v=>String(v??'').toLowerCase().includes(q)));
}
function renderAccounts(){
  const q=document.getElementById('q').value.trim();  // 高亮用原始大小写（hl 内部不区分）
  const ps=+document.getElementById('psize').value, list=filtered();
  const pages=Math.max(1,Math.ceil(list.length/ps)); PAGE=Math.min(PAGE,pages);
  const slice=list.slice((PAGE-1)*ps,PAGE*ps);
  document.getElementById('accN').textContent=ACCOUNTS.length;
  document.getElementById('vtabAccN').textContent=ACCOUNTS.length?String(ACCOUNTS.length):'';
  document.getElementById('accInfo').textContent=`匹配 ${list.length} 个账号 · 第 ${PAGE}/${pages} 页`;
  document.getElementById('accounts').innerHTML=slice.map(({a,hits})=>{
    const status=a.status==='active'?'b-ok':'b-warn';
    // 徽标三选一（2026-10-27 用户确认保留现状 + hover 提示）：
    // 刷课中时不再重复显示“已登录”（同一行里两个绿灯看着像重复），
    // 会话是否有效靠悬停提示与右侧「🔑 会话/登录」按钮区分。
    const sess=a.has_session
      ?badge('已登录','b-info','会话文件有效：点右侧「🔑 会话」可查看/刷新')
      :badge('未登录','b-warn','本机没存到会话，点右侧「🔑 登录」扫码');
    const wm=a.work_effective||'owner';
    const wmName={ai:'AI代答',reference:'参考答案',owner:'号主自理'};
    // 额度：今日已完成 / 上限（0 即不限）；上限来源（全局 or 账号单独）标在任务数后
    const qt=a.quota_tasks||0, qm=a.quota_minutes||0;
    const qsrc=a.quota_from_global?'上限跟随全局（账号级填 0 即跟随）':'上限按本账号单独设置';
    // 把“（全局）/（账号）/0=不限”这类解释移进 title：卡头上少两截文字，chips 一行就能放下
    const mkChip=(v,t)=>`<span class="chip"${t?` title="${esc(t)}"`:''}>${v}</span>`;
    const chips=[mkChip(`今日 ${a.today_videos??0}/${qt?qt+'个':'不限'}`, qsrc+'｜任务数'),
                 mkChip(`今日 ${a.today_min??0}/${qm?qm+'分':'不限'}`, qsrc+'｜时长'),
                 mkChip(`作业 ${wmName[wm]||'号主自理'}`, a.work_from_global?'跟随全局默认策略':'本账号单独设置'),
                 a.window?mkChip(`窗口 ${esc(a.window)}`):'',a.proxy?mkChip('代理'):'',
                 a.days_left===null?'':mkChip(`有效期 ${a.days_left>=0?a.days_left+'天':'已过期'}`),
                 a.start_at?mkChip('⏱ '+a.start_at,'计划时刻；到点由「⏱ 定时调度」守护开刷')
                   :(AUTOPLAN.times[a.id]?mkChip('⏱ '+AUTOPLAN.times[a.id],'今日计划时刻'):mkChip('定时未设')),
                 // 定时只是“计划时刻”：真正去开刷的是「⏱ 定时调度」守护进程；
                 // 守护没在跑时显式标红，避免误以为到点会自动开刷（2026-10-26 用户疑问）
                 (a.start_at&&!AUTOSTATE.running)
                   ? `<span class="chip" style="color:#c0392b" title="这只是计划时刻：需点顶部「⏱ 定时调度」启动守护进程，到点才会自动开刷（不启动则今天不会自己跑）">⏱ 调度未启动</span>`
                   : '',
                 a.renew_count?mkChip('续期 '+a.renew_count+' 次'):'',
                 a.last_expired_at?mkChip('上次失效 '+esc(a.last_expired_at.slice(5,16))):'' ]
                 .filter(Boolean).join('');
    const mkCourse=(c)=>{
      const hit=hits.some(h=>h.name===c.name)?' hit':'';
      const run=RUNNING[a.id];
      const runningThis=run&&run.course===c.name;
      const ctl=runningThis
        ?`<button class="stopc" title="正在刷这门课（${run.since} 开始），点击停止；已看的进度全部保留"
            onclick="stopGrind(${a.id})">■ 停止刷课</button>`
        :`<button class="play" title="开始刷这门课（从上次进度继续）"
            data-c="${esc(c.name)}" data-p="${esc(c.platform_course_id||'')}"
            onclick="startGrind(${a.id},this.dataset.c,this.dataset.p)">▶ 刷这门课</button>`;
      const q=document.getElementById('q').value.trim();
      const now=runningThis?` style="border-left:3px solid var(--ok);padding-left:.5rem"`:'';
      const nowTag=runningThis?` <span class="badge b-ok pulse" style="margin-left:.3rem">当前正在学</span>`:'';
      const bold=runningThis?'font-weight:800;':'';
    return `<div class="course${hit}"${now}><span class="nm" style="${bold}">${hl(c.name,q)}${nowTag}</span>
      <div class="t2"><span class="nums">${c.total?Math.round(c.done/c.total*100):0}% · <b>${c.done}/${c.total}</b></span>${ctl}</div>
      <div class="spent">已学 ${fmtDur(c.total_min)}${c.est_total_min>c.total_min?` / 总${c.est_is_exact?'':'约'}${fmtDur(c.est_total_min)}`:''}</div>
      <div class="bar"><i style="width:${c.total?c.done/c.total*100:0}%"></i></div></div>`;};
    const run=RUNNING[a.id];
    // 主显课程：正在刷的 > 最有进度的 > 第一门；其余默认折叠（点击展开；搜索时自动展开）
    const mainCourse = (run && a.courses.find(c=>c.name===run.course))
      || a.courses.find(c=>c.done>0) || a.courses[0];
    const others = a.courses.filter(c=>!mainCourse || c.name!==mainCourse.name);
    // 作业任务点（名称含"作业/测验/考试"）：挂课程行下展示真实状态
    const workRe = /作业|测验|考试/;
    const works = a.works || [];
    const mkWork = w => {
      const st = w.status==='done' ? badge('已完成','b-ok')
        : w.ai_filled ? badge('AI已填入·未提交','b-info')
        : w.ai_answered ? badge('AI参考已出·未填写','b-info')
        : badge('未完成','b-warn');
      const btns = w.status==='done' ? '' :
        `<button class="minibtn" title="按该账号作业策略处理（✎管理可改：号主自理/参考答案/AI代答）"
          onclick="doWork(${a.id},'${esc(w.name).replace(/'/g,'&#39;')}')">${wm==='ai'?'📝 AI代答':wm==='reference'?'📝 参考答案':'📝 去处理'}</button>`;
      const review = w.ai_answered
        ? `<button class="minibtn" title="已提交？读回每题得分：答对转正题库、答错清空重学"
            onclick="doReview(${a.id},'${esc(w.name).replace(/'/g,'&#39;')}')">✔ 读判分</button>` : '';
      return `<div class="course" style="border:1px dashed var(--line);border-radius:11px">
        <div class="t2" style="margin-bottom:.3rem"><span class="nm">📄 ${hl(w.name,q)}</span>${st}</div>
        ${btns||review?`<div style="display:flex;gap:.45rem;justify-content:flex-end">${btns}${review}</div>`:''}
      </div>`;
    };
    const workHtml = works.length
      ? works.map(mkWork).join('')
      : (mainCourse && workRe.test(mainCourse.name) ? '' : '');
    const searching = document.getElementById('q').value.trim()!=='';
    // 仅当"课程名"命中才自动展开；搜人名/备注时保持折叠（找人不找课）
    const autoOpen = others.some(c=>hits.some(h=>h.name===c.name));
    const open = autoOpen || !!EXPANDED[a.id];
    // 课程区标题：在刷="正在学习的课程"（绿），未刷="下次将继续的课程"（灰）
    const runningNow = run && mainCourse && run.course===mainCourse.name;
    const zoneTitle = mainCourse
      ? `<div style="font-size:.78rem;font-weight:700;margin:-.15rem 0 .35rem;color:${runningNow?'var(--ok)':'var(--muted)'}">
           ${runningNow?'▶ 正在学习的课程':'⏭ 下次将继续的课程'}</div>` : '';
    const mainHtml = mainCourse ? zoneTitle + mkCourse(mainCourse) : '';
    const othersHtml = others.map(mkCourse).join('');
    const doneSum = others.reduce((s,c)=>s+c.done,0);
    const courses = (mainCourse ? mainHtml : '<div class="muted" style="font-size:.8rem">暂无课程数据</div>')
      + workHtml
      + (others.length ? `<button class="morebtn" onclick="toggleMore(${a.id})">${open?'▾':'▸'} 其他 ${others.length} 门课 · 已完成 ${doneSum} 个知识点</button>
        <div class="more" style="display:${open?'':'none'}">${othersHtml}</div>` : '');
    const stopBtn=run?`<button class="minibtn danger pulse" title="停止该账号正在刷的课：已完成视频全部保留，下次点▶从断点续播" onclick="stopGrind(${a.id})">■ 停止</button>`:'';
    // 失败警示（Q20）：今日最近一次失败 → 归类成一句话 + 最多 3 个真能点的按钮。
    // 数据库为空的号不显示任何东西；已经重新跑起来的号也不显示（后端已判）。
    const al=a.alert;
    const ACT={'看日志':`gotoLogs(${a.id})`,'重新登录':`doLogin(${a.id})`,
      '立即补跑':`resumeStudy(${a.id},'${esc(mainCourse?mainCourse.name:'').replace(/'/g,'&#39;')}')`};
    const alertHtml = al ? `<div class="alertbar" title="${esc(al.hint||'')}${al.detail?'｜原文：'+esc(al.detail):''}">
      <span class="ab-tag">⚠ ${esc(al.label||'异常')}${al.tier==='hard_stop'?'·已停手'
        :al.tier==='needs_you'?'·等你操作':'·会自动重试'}</span>
      <span class="ab-txt">${esc(al.hint||'')}${al.resume_at?'｜约 '+esc(al.resume_at)+' 自动重试':''}</span>
      <span class="ab-acts">${(al.actions||[]).slice(0,3)
        .map(x=>`<button class="minibtn" onclick="${ACT[x]||''}">${esc(x)}</button>`).join('')}</span></div>` : '';
    const bad=a.days_left!==null&&a.days_left<0;
    const avatar=String(a.display_name||a.platform_username||'?').trim().charAt(0)||'?';
    return `<div class="card${run?' running':''}${hits.length?' hit':''}">
    <div class="head">
      <div class="avatar">${esc(avatar)}</div>
      <div class="names"><div class="who">${hl(a.display_name||a.platform_username||'—',q)}${(a.platform_username&&!(a.profile&&a.profile.name))?'<span class="nicktag" title="当前显示的是平台昵称；在「📈 统计 → 画像设置」手填姓名后自动换成姓名">昵称</span>':''}</div>
        <div class="sub">#${a.id} ${hl(a.label,q)} · 优先级 ${a.priority}${(a.profile&&a.profile.name&&a.platform_username&&a.profile.name!==a.platform_username)?' · 平台昵称 '+esc(a.platform_username):''}</div>
        ${(a.student_no||(a.profile&&(a.profile.grade||a.profile.major||a.profile.term)))?`<div class="sub">
          🎓 ${a.student_no?`学号 ${hl(a.student_no,q)}`:''}${a.student_no&&a.profile&&(a.profile.grade||a.profile.major||a.profile.term)?' · ':''}${a.profile?[a.profile.grade,a.profile.major,a.profile.term].filter(Boolean).map(x=>esc(x)).join(' / '):''}
          <span class="muted" title="在「📈 统计 → 画像设置」里修改" style="cursor:pointer" onclick="switchView('stats')">✎</span></div>`:''}</div>
      <div class="state">${bad?badge('已过期','b-warn'):badge(a.status,a.status==='active'?'b-ok':'b-warn')}
        ${run?badge('刷课中 '+run.since,'b-ok pulse',
          '正在刷课（'+run.course+'）——会话有效；下课后恢复显示“已登录”'):sess}</div>
    </div>
    <div class="ops">${a.has_session
              ? `<button class="minibtn ghost" title="本账号已登录（无需重新登录）。仅当会话失效时才点此刷新：先无头自动，失败可转手动扫码" onclick="doLogin(${a.id})">🔑 会话</button>`
              : `<button class="minibtn primary" title="为该账号登录：先试无头自动（用已存密码），失败可转手动扫码/过验证码" onclick="doLogin(${a.id})">🔑 登录</button>`}
        <button class="minibtn" title="批量作业：遍历该账号全部未完成作业按策略执行（单门作业在下方课程列表的作业行里单独处理）"
          onclick="doWorkAll(${a.id})">📝 批量作业</button>
        <button class="minibtn" title="账号管理：备注/优先级/专属窗口与额度/有效期/续期/删除" onclick="editAccount(${a.id})">✎ 管理</button>${stopBtn}</div>
    ${a.note?`<div class="muted" style="font-size:.75rem;margin-bottom:.3rem">📝 ${esc(a.note)}</div>`:''}
    ${alertHtml}
    ${(()=>{const bp=(PLANS||[]).find(p=>p.enabled&&p.account_ids.includes(a.id));
      return bp?`<div class="muted" style="font-size:.75rem;margin-bottom:.3rem">📚 学习安排《${esc(bp.name)}》→ 课程《${esc(bp.course_name)}》（开跑时间仍按本账号定时/窗口）</div>`:'';})()}
    <div class="chips">${chips}</div>${courses}</div>`;
  }).join('')||'<div class="card muted">无匹配</div>';
  document.getElementById('pager').innerHTML=
    `<button ${PAGE<=1?'disabled':''} onclick="go(${PAGE-1})">‹ 上一页</button>
     <span class="muted">${PAGE} / ${pages}</span>
     <button ${PAGE>=pages?'disabled':''} onclick="go(${PAGE+1})">下一页 ›</button>`;
}
function go(p){PAGE=p;renderAccounts();}
let LOGN=20, LOGACC='', LOG_AUTO=true;  // 日志页：条数 / 账号筛选输入 / 自动刷新开关
// 日志页账号候选：只灌 datalist（不覆盖用户正在输入的值），账号再多也不会变成超长下拉
function fillLogAccounts(){
  const dl=document.getElementById('logAccList'); if(!dl)return;
  dl.innerHTML=(ACCOUNTS||[]).map(a=>
    `<option value="#${a.id} ${esc(a.display_name||a.label)}"></option>`).join('');
}
// 输入解析：`#2` / `2` → 该账号；“张” → 姓名/备注/平台昵称模糊命中（可能多账号）
function logAccIds(){
  const raw=(LOGACC||'').trim(); if(!raw)return '';
  const head=raw.split(/\\s+/)[0].replace(/^#/,'');
  if(/^\\d+$/.test(head))return head;
  const q=raw.toLowerCase();
  return (ACCOUNTS||[]).filter(a=>[a.display_name,a.label,a.platform_username]
    .some(v=>v&&String(v).toLowerCase().includes(q))).map(a=>a.id).join(',');
}
function logSourceQs(){
  const v=document.getElementById('logSrc')?.value||'event';
  if(v==='sys-sub')return 'source=sys&min_level=INFO&sub=1';
  if(v==='sys-info')return 'source=sys&min_level=INFO';
  if(v==='sys-warn')return 'source=sys&min_level=WARNING';
  return 'source=event';
}
// 事件名 → 中文短标签（2026-10-27 用户反馈：日志全是英文事件名，看不懂在跑什么）
const EVNAME={'chapter-start':'开始一节','video-done':'看完一节','video-interrupt':'播放中断',
  'chapter-fail':'打不开','session-start':'会话开始','session-end':'会话结束',
  'session-stop':'手动停止','auto-fire':'已开跑','auto-try':'发起尝试',
  'auto-revoke':'撤回今日已学','auto-suspend':'已挂起','auto-done':'今日已达成',
  'auto-error':'硬停/失败','auto-skip':'今日跳过','auto-plan':'今日计划',
  'auto-start':'守护启动','auto-stop':'守护停止',
  'auto-request':'启动请求','auto-replan':'计划变更','auto-plan-stale':'计划陈旧',
  'auto-exit':'守护异常退出','diag-visited-fix':'数据修复'};
function renderLogs(n){if(n)LOGN=n;
  const box=document.getElementById('logs'); if(!box)return;
  const ids=logAccIds();
  if((LOGACC||'').trim()&&!ids){  // 输入了但没命中：不发请求，免得误以为“没日志”
    box.innerHTML='<div class="muted">未匹配到账号：“'+esc(LOGACC)+'”（清空即看全部）</div>';return;}
  fetch('/api/logs?n='+LOGN+'&'+logSourceQs()+(ids?`&accounts=${ids}`:'')).then(r=>r.json()).then(d=>{
    if(!d.logs.length){box.innerHTML='<div class="muted">暂无记录</div>';}
    else box.innerHTML='<table>'+d.logs.map(l=>{
      const cls=l.event==='video-done'||l.event==='auto-fire'||l.event==='auto-done'?'ev-good':
        (l.event==='chapter-start'||l.event==='session-start'||l.event==='auto-start'||
         l.event==='auto-plan'||l.event==='auto-request'||l.event==='auto-replan'||
         l.event==='auto-try')?'ev-info':
        (l.event==='video-interrupt'||l.event==='chapter-fail'||l.event==='session-stop'||
         l.event==='auto-skip'||l.event==='auto-stop'||l.event==='auto-error'||
         l.event==='auto-revoke'||l.event==='auto-suspend'||l.event==='auto-plan-stale'||
         l.event==='auto-exit'||
         l.level==='WARNING'||l.level==='ERROR')?'ev-warn':'';
      const nm=EVNAME[l.event]||'';
      // 账号列必须 nowrap：中文名会被挤成“林鑫/阳”两行（用户 2026-10-27 截图反馈）
      return `<tr><td class="mono" style="white-space:nowrap">${esc(l.ts)}</td>
        <td class="mono" style="white-space:nowrap">${esc(l.who)}</td>
        <td class="ev ${cls}" style="white-space:nowrap" title="${esc(l.event)}">${esc(l.event)}
          ${nm?`<span class="muted" style="display:block;font-size:.72em;line-height:1.1">${esc(nm)}</span>`:''}</td>
        <td>${esc(l.detail)}</td></tr>`;}).join('')+'</table>';
    const at=document.getElementById('logAt');
    if(at)at.textContent='更新于 '+new Date().toLocaleTimeString('zh-CN',{hour12:false});
  }).catch(e=>{box.innerHTML='<div class="muted">读取日志失败：'+esc(String(e))+'</div>';});
}
function loadSettings(){
  fetch('/api/settings').then(r=>r.json()).then(d=>{
    s_quiz.checked=!!d.quiz_enabled;
    s_ws.value=d.window_start||'08:00'; s_we.value=d.window_end||'23:00';
    s_min.value=d.daily_minutes??0; s_task.value=d.daily_tasks??0;
    s_wm.value=d.work_mode_default||'owner';
    s_mt.value=d.auto_max_tries??2; s_rm.value=d.auto_retry_minutes??15;
    s_cs.value=d.auto_confirm_seconds??25;
    s_rj.value=d.auto_retry_jitter_pct??40; s_rx.value=d.auto_retry_max_minutes??90;
    s_hc.value=d.auto_hard_tries_cap??4;
    s_er.value=d.learn_enter_retries??2; s_ms.value=d.learn_max_skip_chapters??3;
    s_ns.value=d.learn_non_video_streak??4; s_pr.value=d.learn_partial_retries??1;
    s_prr.value=d.learn_partial_retry_ratio_pct??60; s_lb.value=d.learn_login_budget_per_day??2;
    s_nvm.value=d.learn_non_video_marks||'';
    renderAudit(d.recent_changes||[]);
    s_lurl.value=d.llm_base_url||''; s_lmodel.value=d.llm_model||'';
    s_lkeyv.value='';s_lkeyv.dataset.hasKey=d.llm_key_stored?'1':'0';
    window.LLM_NAME=d.llm_provider_name||'Agnès';
    window.LLM_KEY_STORED=!!d.llm_key_stored;
    const hint=d.llm_key_stored?'✓ Key 已加密保存，可直接使用':'⚠ 未配置 Key——下方粘贴后点保存';
    const h2=document.getElementById('s_lhint2'); if(h2)h2.textContent=hint;
    renderProviders();
    window.QUIZ=d;});
}
function switchTab(i,btn){
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  btn.classList.add('active');
  [0,1,2].forEach(k=>{const el=document.getElementById('tab'+k);
    if(el)el.style.display=(k===i?'':'none');});}
function renderAudit(rows){
  const el=document.getElementById('auditList'); if(!el)return;
  const NAME={auto_max_tries:'每日最多自动发起',auto_retry_minutes:'退避基数(分)',
    auto_retry_jitter_pct:'退避随机浮动(%)',auto_retry_max_minutes:'退避封顶(分)',
    auto_hard_tries_cap:'单日硬上限(次)',
    auto_confirm_seconds:'开跑回读(秒)',learn_enter_retries:'单节点点几次',
    learn_max_skip_chapters:'连续打不开收工',learn_non_video_streak:'连续非视频收工',
    learn_partial_retries:'中断续播次数',learn_partial_retry_ratio_pct:'续播门槛(%)',
    learn_login_budget_per_day:'每日登录预算',learn_non_video_marks:'非视频关键词',
    daily_minutes:'时长额度',daily_tasks:'任务额度',window_start:'窗口开始',
    window_end:'窗口结束',quiz_enabled:'弹题AI',work_mode_default:'作业策略'};
  if(!rows.length){el.innerHTML='<span class="muted">暂无变更记录</span>';return;}
  el.innerHTML=rows.map(r=>{
    const t=(r.ts||'').replace('T',' ').slice(5,16);
    const nm=NAME[r.key]||r.key;
    return `<div style="padding:.12rem 0;border-bottom:1px dashed var(--line)">
      <span class="muted">${esc(t)}</span>　<b>${esc(nm)}</b>
      <span style="color:var(--muted)">${esc(String(r.old_value??'—'))}</span>
      <span style="color:var(--accent)">→ ${esc(String(r.new_value??'—'))}</span></div>`;
  }).join('');
}
function presetLLM(name,url,model){s_lname.value=name;s_lurl.value=url;s_lmodel.value=model;
  s_lhint.textContent=`已填入 ${name} 预设——点"保存到厂商列表"生效（可改模型名）`;}
let PROV={builtin:[],custom:[],current:''};
function renderProviders(){
  const el=document.getElementById('provList'); if(!el)return;
  const all=[...PROV.builtin,...PROV.custom];
  el.innerHTML=all.map(p=>{
    const cur=p.name===PROV.current?'border-color:var(--accent);color:var(--accent);font-weight:700':'';
    const del=PROV.custom.some(c=>c.name===p.name)
      ?`<a style="margin-left:3px;color:var(--bad);cursor:pointer" onclick="delProvider('${esc(p.name)}')">⨯</a>`:'';
    return `<span class="chip" style="${cur};cursor:pointer"
      onclick="useProvider('${esc(p.name)}')">${esc(p.name)}${del}</span>`;}).join('');
  const s=document.getElementById('s_lhint2');
  const cur=[...PROV.builtin,...PROV.custom].find(p=>p.name===PROV.current);
  if(s){
    const keyStat=window.LLM_KEY_STORED
      ?'✓ Key 已加密保存，可直接使用'
      :'⚠ 尚无可用 Key——下方粘贴 Key 后点保存';
    s.innerHTML=`当前使用：<b style="color:var(--accent)">${esc(PROV.current)}</b>`+
      (cur?`（${esc(cur.base_url)} / ${esc(cur.model)}）`:'')+`<br>${keyStat}`+
      (PROV.current===document.getElementById('s_lname').value.trim()
        ?'　<span style="color:var(--ok)">= 正在编辑的就是当前厂商</span>':'');
  }
}
function useProvider(name){
  const p=[...PROV.builtin,...PROV.custom].find(x=>x.name===name); if(!p)return;
  s_lname.value=p.name;s_lurl.value=p.base_url;s_lmodel.value=p.model;
  s_lhint.textContent=`已载入 ${name} 参数——改完点"保存到厂商列表"切换`;
}
function saveProvider(){
  fetch('/api/llm/providers',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name:s_lname.value.trim(),base_url:s_lurl.value.trim(),
      model:s_lmodel.value.trim(),key:s_lkeyv.value.trim()})})
  .then(r=>r.json()).then(d=>{
    if(!d.ok){alert('保存失败：'+d.error);return;}
    s_lkeyv.value='';s_lkeyv.dataset.hasKey='1';
    s_lhint.textContent='✓ 已保存并切换到 '+s_lname.value.trim()+'（Key 已加密存本机，可点测试连通验证）';
    loadSettings();loadProviders();});}
function delProvider(name){
  if(!confirm('删除自定义提供商 '+name+'？'))return;
  fetch('/api/llm/providers/del',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name:name})}).then(r=>r.json()).then(d=>{
    if(!d.ok)alert('失败：'+d.error); loadProviders();});}
function loadProviders(){
  fetch('/api/llm/providers').then(r=>r.json()).then(d=>{
    if(d.ok){PROV=d;renderProviders();}});}
function testLLM(){
  const el=document.getElementById('s_lhint');
  el.textContent='🔌 测试中…（约 10 秒）';
  const ctl=new AbortController(); const t=setTimeout(()=>ctl.abort(),15000);
  fetch('/api/llm/test',{method:'POST',headers:{'Content-Type':'application/json'},
    signal:ctl.signal,
    body:JSON.stringify({base_url:s_lurl.value.trim(),model:s_lmodel.value.trim(),
      key:s_lkeyv.value.trim()})})
  .then(r=>r.json()).then(d=>{clearTimeout(t);el.textContent=d.ok
    ?`✓ 连通成功，模型回复：${d.reply}`:'✗ 失败：'+d.error;})
  .catch(e=>{clearTimeout(t);el.textContent='✗ 测试失败：'+(e.name==='AbortError'?'超时（15秒）':e);});}
function saveSettings(){
  const body={quiz_enabled:s_quiz.checked,window_start:s_ws.value,
    window_end:s_we.value,daily_minutes:+s_min.value||0,daily_tasks:+s_task.value||0,
    work_mode_default:s_wm.value};
  for(const [k,el] of [['auto_max_tries',s_mt],['auto_retry_minutes',s_rm],
      ['auto_retry_jitter_pct',s_rj],['auto_retry_max_minutes',s_rx],
      ['auto_hard_tries_cap',s_hc],['auto_confirm_seconds',s_cs],['learn_enter_retries',s_er],
      ['learn_max_skip_chapters',s_ms],['learn_non_video_streak',s_ns],
      ['learn_partial_retries',s_pr],['learn_partial_retry_ratio_pct',s_prr],
      ['learn_login_budget_per_day',s_lb]]){
    if(el&&el.value!=='')body[k]=Math.round(+el.value);
  }
  if(s_nvm)body.learn_non_video_marks=s_nvm.value.trim();
  fetch('/api/settings',{method:'PUT',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(body)}
  ).then(r=>r.json()).then(d=>{alert(d.ok?'已保存（新会话生效）':'保存失败：'+d.error);
    if(d.ok){window.QUIZ.work_mode_default=s_wm.value;document.getElementById('dlg').close();syncRun();}});
}
function reload(){fetch('/api/overview').then(r=>r.json()).then(d=>{
  document.getElementById('today').textContent=d.today_videos;
  ACCOUNTS=d.accounts;renderAccounts();});}
function openAdd(){a_label.value=a_user.value=a_pw.value='';a_pri.value=1;
  document.getElementById('addDlg').showModal();}
function doAdd(){
  fetch('/api/accounts',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({label:a_label.value.trim(),username:a_user.value.trim(),
      password:a_pw.value,priority:+a_pri.value||1})})
  .then(r=>r.json()).then(d=>{alert(d.ok?'已添加（记得为其登录）':'失败：'+d.error);
    if(d.ok){document.getElementById('addDlg').close();reload();}});}
let PLANS=[];
// 视图切换（2026-10-26：学习安排原先压在账号卡片上方，占位又挡视线 → 改成标签页；
// 选择记在 localStorage，刷新/重开面板后停在上次看的页）
let VIEW=(()=>{try{return localStorage.getItem('xxt.view')||'accounts';}catch(e){return 'accounts';}})();
function switchView(v){
  VIEW=v; try{localStorage.setItem('xxt.view',v);}catch(e){}
  const a=document.getElementById('viewAccounts'), p=document.getElementById('viewPlans'),
        s=document.getElementById('viewStats');
  if(a)a.style.display=(v==='accounts'?'':'none');
  if(p)p.style.display=(v==='plans'?'':'none');
  if(s)s.style.display=(v==='stats'?'':'none');
  const g=document.getElementById('viewLogs');
  if(g)g.style.display=(v==='logs'?'':'none');
  document.querySelectorAll('.vtab').forEach(b=>b.classList.toggle('active',b.dataset.view===v));
  if(v==='plans')renderPlans();
  if(v==='stats'){loadStats();loadProfiles();}
  if(v==='logs'){fillLogAccounts();renderLogs();}
}
// ---- 统计（数据可视页）----
let STATBY='grade';
function setStatBy(b){
  STATBY=b;
  document.querySelectorAll('#statBy .segb').forEach(x=>x.classList.toggle('on',x.dataset.by===b));
  loadStats();
}
function loadStats(){
  const days=+document.getElementById('statDays').value;
  const q=document.getElementById('statQ').value.trim();
  fetch(`/api/stats?by=${STATBY}&days=${days}&q=${encodeURIComponent(q)}`).then(r=>r.json()).then(d=>{
    const el=document.getElementById('statBody'), tr=document.getElementById('statTrend');
    if(!d.ok){el.innerHTML='<span class="muted">统计失败：'+esc(d.error||'')+'</span>';tr.innerHTML='';return;}
    renderTrend(d.trend||[]);
    if(!d.rows.length){
      el.innerHTML='<span class="muted">'+(q?'没有匹配「'+esc(q)+'」的记录':'这段时间还没有刷课记录（'+esc(d.since)+' 起）')+'</span>';
      return;
    }
    const max=Math.max(...d.rows.map(r=>r.minutes),1);
    el.innerHTML=
      `<div class="muted" style="margin:.35rem 0 .2rem">按「${esc(d.by_name)}」分组 · 范围 ${esc(d.since)} 起${q?' · 关键字「'+esc(q)+'」':''} · 合计 ${d.total.accounts} 个账号 / ${d.total.videos} 个视频 / ${d.total.minutes} 分钟 / 作业 ${d.total.works} 次</div>`
      + d.rows.map(r=>`<div class="statrow"><b title="${esc(r.key)}">${esc(r.key)}${r.key==='未标注'
          ?'<span class="flag" onclick="goProf()" title="这一行是没填画像的账号；点这里去画像设置补齐">⚑ 待补画像</span>':''}</b>
          <div class="bar" style="width:${Math.max(2,Math.round(r.minutes/max*100))}%"></div>
          <span>${r.accounts} 号</span><span>${r.videos} 视频</span><span>${r.minutes} 分</span></div>`).join('')
      + `<div class="muted" style="margin-top:.35rem;font-size:.75rem">条长 = 观看时长占比${d.by==='course'?'；按课程聚合只统计视频（作业日志里没有课程名）':'（作业次数不在条上）'}</div>`;
  }).catch(e=>{document.getElementById('statBody').textContent='统计加载失败：'+e;});
}
function goProf(){
  const el=document.getElementById('profBody');
  if(el)el.scrollIntoView({behavior:'smooth',block:'center'});
  const q=document.getElementById('profQ'); if(q)q.focus();
}
// 趋势：每天一行横向条（条长=当天观看分钟），右侧标分钟/视频数；天数多少都好读
function renderTrend(trend){
  const el=document.getElementById('statTrend'); if(!el)return;
  if(!trend.length){el.innerHTML='<div class="muted" style="margin:.4rem 0 .2rem">📅 每日趋势：所选范围内还没有数据（换个时间范围，或先刷几节课再看）</div>';return;}
  const r1=x=>Math.round(x*10)/10;
  const max=Math.max(...trend.map(t=>t.minutes),1);
  const total=trend.reduce((s,t)=>s+(t.minutes||0),0);
  const sumV=trend.reduce((s,t)=>s+(t.videos||0),0);
  el.innerHTML=`<div class="muted" style="margin:.4rem 0 .1rem">📅 每日趋势 · 峰值 ${r1(max)} 分 · 合计 ${r1(total)} 分 / ${sumV} 个视频</div>`
    + '<div class="hbar">' + trend.map(t=>`<div class="hrow" title="${esc(t.date)}｜${t.videos} 视频｜${t.minutes} 分｜作业 ${t.works} 次">
        <span class="hd">${esc(t.date.slice(5))}</span>
        <div class="htrack"><i style="width:${t.minutes?Math.max(2,Math.round(t.minutes/max*100)):0}%"></i></div>
        <span class="hv">${r1(t.minutes)} 分 · ${t.videos} 视频</span></div>`).join('') + '</div>';
}
let PROFILES=[];
function loadProfiles(){
  fetch('/api/profiles').then(r=>r.json()).then(d=>{
    if(!d.ok){document.getElementById('profBody').textContent='画像加载失败';return;}
    PROFILES=d.profiles; renderProfiles();
  });
}
function renderProfiles(){
  const el=document.getElementById('profBody'); if(!el)return;
  if(!PROFILES.length){el.innerHTML='<span class="muted">还没有账号</span>';return;}
  const q=(document.getElementById('profQ')?.value||'').trim().toLowerCase();
  const rows=PROFILES.filter(p=>!q||[p.account_id,p.label,p.name,p.display_name,p.platform_name,
      p.student_no,p.grade,p.major,p.term,p.school,p.dept,p.cls,p.source]
      .join(' ').toLowerCase().includes(q));
  if(!rows.length){el.innerHTML='<span class="muted">没有匹配「'+esc(q)+'」的账号</span>';return;}
  const inp=(id,k,v,ph)=>`<input id="pf_${k}_${id}" class="pf" placeholder="${esc(ph)}" value="${esc(v||'')}" oninput="pfDirty(${id})">`;
  el.innerHTML='<table class="ptable"><thead><tr>'
    + '<th>账号</th><th>备注</th><th>姓名</th><th>昵称</th><th>学号</th>'
    + '<th>年级</th><th>专业</th><th>学期</th><th>学校</th><th>院系</th><th>班级</th>'
    + '<th>来源</th><th>操作</th></tr></thead><tbody>'
    + rows.map(p=>{const id=p.account_id;return `<tr>
        <td class="ro" title="账号 #${id}">#${id}</td>
        <td class="ro" title="账号备注名（面板显示名）">${esc(p.label||'')}</td>
        <td>${inp(id,'n',p.name,p.platform_name?'留空用昵称':'姓名')}</td>
        <td class="ro" title="平台昵称（只读，登录后自动带出）">${esc(p.platform_name||'—')}</td>
        <td>${inp(id,'s',p.student_no,'学号')}</td>
        <td>${inp(id,'g',p.grade,'年级')}</td>
        <td>${inp(id,'m',p.major,'专业')}</td>
        <td>${inp(id,'t',p.term,'学期')}</td>
        <td>${inp(id,'sc',p.school,'学校')}</td>
        <td>${inp(id,'dp',p.dept,'院系')}</td>
        <td>${inp(id,'cl',p.cls,'班级')}</td>
        <td class="ro">${p.source==='platform'?'<span class="b-ok">平台</span>':(p.source==='manual'?'<span class="b-info">手动</span>':'—')}</td>
        <td class="ro"><div class="cellops">
          <span class="dirty" id="pf_dirty_${id}"></span>
          <button class="minibtn" onclick="saveProfile(${id})">保存</button>
          <button class="minibtn" title="只读访问一次个人中心，尝试抓取平台上的姓名/学号/学校/院系/班级并回填（不覆盖你已手填的内容）"
            onclick="readProfileFromPlatform(${id})">⬇ 平台读取</button></div></td></tr>`;}).join('')
    + '</tbody></table>'
    + '<div class="muted" style="margin-top:.35rem;font-size:.75rem">姓名/学号/年级/专业/学期/学校/院系/班级可编辑，留空即清空；姓名留空时卡片显示平台昵称（不会把昵称写进姓名）；平台读取只补空、不覆盖手填。学号只用于本地面板识别，不上传</div>';
}
function pfDirty(id){
  const e=document.getElementById('pf_dirty_'+id);
  if(e&&!e.textContent)e.textContent='● 未保存';
}
function saveProfile(id){
  const v=k=>{const el=document.getElementById(`pf_${k}_${id}`);return el?el.value.trim():'';};
  const cur=PROFILES.find(p=>p.account_id===id)||{};
  const vals={name:v('n'),student_no:v('s'),grade:v('g'),major:v('m'),term:v('t'),
    school:v('sc'),dept:v('dp'),cls:v('cl')};
  // 一个字都没改就不动来源（否则点一下“保存”就把平台→手动，来源标记就失真了）
  const same=Object.keys(vals).every(k=>vals[k]===(cur[k]||'').trim());
  fetch('/api/profiles',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({account_id:id,...vals,source:same?null:'manual'})})
  .then(r=>r.json()).then(d=>{
    if(!d.ok){toast('保存失败：'+d.error,1);return;}
    toast(same?'✔ 无改动（来源保持不变）':'✔ 画像已保存（姓名/学号/年级/专业/学期/学校/院系/班级）');
    const e=document.getElementById('pf_dirty_'+id);if(e)e.textContent='';
    syncRun();loadStats();loadProfiles();});
}
const FIELD_CN={name:'姓名',student_no:'学号',grade:'年级',major:'专业',term:'学期',
  school:'学校',dept:'院系',cls:'班级'};
function readProfileFromPlatform(id){
  if(!confirm('将从平台只读访问一次个人中心，尝试抓取 姓名/学号/学校/院系/班级 并回填（只补空、不覆盖手填；需该账号已登录）。继续？'))return;
  toast('⏳ 正在从平台读取（约 5–15 秒）…');
  fetch(`/api/accounts/${id}/profile_from_platform`,{method:'POST'})
  .then(r=>r.json()).then(d=>{
    if(!d.ok){toast('平台读取失败：'+(d.error||'未知'),1);return;}
    const f=(d.merged&&d.merged.filled)||[], s=(d.merged&&d.merged.skipped)||[];
    const nn=(d.merged&&d.merged.nickname_only)||[];
    const fp=f.length?'已回填 '+f.map(k=>FIELD_CN[k]||k).join('/'):'无新字段可回填';
    const sp=s.length?'；保留手填 '+s.map(k=>FIELD_CN[k]||k).join('/'):'';
    const np=nn.length?'；平台只给了昵称，姓名未填（请手填真名）':'';
    const fl=(d.fields&&d.fields.length)?'。平台返回字段：'+d.fields.join('/'):'';
    toast('✔ 平台读取完成：'+fp+sp+np+fl);
    syncRun();loadProfiles();
  }).catch(e=>toast('平台读取失败：'+e,1));
}
function loadPlans(){
  fetch('/api/plans').then(r=>r.json()).then(d=>{PLANS=d.plans||[];renderPlans();});}
function renderPlans(){
  const el=document.getElementById('planList'); if(!el)return;
  const on=PLANS.filter(p=>p.enabled).length;
  const cnt=document.getElementById('vtabPlanN');
  if(cnt)cnt.textContent=PLANS.length?`${on}/${PLANS.length}`:'';
  if(!PLANS.length){el.innerHTML='<span class="muted">暂无学习安排（可选功能）—— 想把同一门课统一排给多个账号，点右上「＋ 新建学习安排」；<b>不建也能刷</b>：手动 ▶ 指定课，定时则按进度随机挑一门</span>';return;}
  const q=(document.getElementById('planQ')?.value||'').trim().toLowerCase();
  const shown=PLANS.filter(p=>{
    if(!q)return true;
    const names=p.account_ids.map(id=>{const a=(ACCOUNTS||[]).find(x=>x.id===id);
      return a?(a.label+' '+(a.platform_username||'')+' '+(a.student_no||'')):'';}).join(' ');
    return [p.name,p.course_name,names].join(' ').toLowerCase().includes(q);
  });
  if(!shown.length){el.innerHTML='<span class="muted">没有匹配「'+esc(q)+'」的学习安排</span>';return;}
  el.innerHTML=shown.map(p=>{
    const accs=p.account_ids.map(id=>{const a=(ACCOUNTS||[]).find(x=>x.id===id);
      return '#'+id+(a?' '+esc(a.label):'');}).join('、')||'—';
    const st=p.enabled?badge('启用中','b-ok'):badge('已停用','b-warn');
    const pidNote=p.course_pid?'':` <span class="b-warn" style="font-size:.68rem"
      title="老学习安排只存了课名（无平台课程ID），同名课程可能歧义——点「✎ 编辑」重新选一次课程即可补上">建议补选课程ID</span>`;
    // 已抓课程里没这门课（按 pid）的账号：运行时会被跳过，卡片上直说，不让人猜
    const lackN=p.course_pid?p.account_ids.filter(id=>!accountHasCourse(id,p.course_pid)):[];
    const lackNote=lackN.length?` <span class="b-warn" style="font-size:.68rem"
      title="这些账号的课程列表里没有本学习安排这门课（按平台课程ID匹配）：${esc(lackN.map(i=>'#'+i).join('、'))}——运行时会被跳过。让对方先抓取课程，或点「✎ 编辑」核对">${lackN.length} 个账号缺此课</span>`:'';
    return `<div class="prow"><b>${esc(p.name)}</b>${st}
      <span>课程《${esc(p.course_name)}》${pidNote}${lackNote}</span>
      <span class="muted">${p.account_ids.length} 个账号：${esc(accs)}</span>
      <span style="flex:1"></span>
      <button class="minibtn" title="改名/换课程/增删账号（不影响已完成的刷课）" onclick="openPlan(${p.id})">✎ 编辑</button>
      <button class="minibtn" onclick="togglePlan(${p.id},${p.enabled?0:1})">${p.enabled?'⏸ 停用':'▶ 启用'}</button>
      <button class="minibtn danger" onclick="delPlan(${p.id})">🗑 删除</button></div>`;}).join('');
}
let PLSEL=new Set();     // 主弹窗当前已选账号（新建/编辑共用）
let PKSEL=new Set();     // 选择器弹窗内的临时勾选
let PLBUSY={};           // 已在**其他**启用学习安排里的账号 → 学习安排名
let PLANMODE=null;       // null=新建；否则=正在编辑的学习安排 id
function toast(msg,bad){
  let el=document.getElementById('toast');
  if(!el){el=document.createElement('div');el.id='toast';document.body.appendChild(el);}
  el.textContent=msg;el.className='toast show'+(bad?' bad':'');
  clearTimeout(window._tT);window._tT=setTimeout(()=>{el.className='toast';},3000);
}
function accName(a){return (a.profile&&a.profile.name)||a.platform_username||a.label||('#'+a.id);}
function accDesc(a){
  const p=a.profile||{}, bits=[];
  if(a.platform_username)bits.push(a.platform_username);
  if(p.student_no)bits.push('学号 '+p.student_no);
  if(p.school)bits.push(p.school);   // 学校靠前：同名课跨校时先看这一位
  [p.grade,p.major,p.term].filter(Boolean).forEach(v=>bits.push(v));
  if(a.note)bits.push('📝'+a.note);
  return bits.join(' · ');
}
function loadPlanBusy(){
  PLBUSY={};
  PLANS.filter(p=>p.enabled&&p.id!==PLANMODE).forEach(p=>p.account_ids.forEach(i=>PLBUSY[i]=p.name));
}
function openPlan(planId){
  PLANMODE=planId||null;
  const p=planId?PLANS.find(x=>x.id===planId):null;
  document.getElementById('pl_name').value=p?p.name:'';
  document.getElementById('planTitle').textContent=p?`✎ 编辑学习安排 #${p.id}`:'📚 新建学习安排';
  document.getElementById('planOk').textContent=p?'保存修改':'创建（默认停用）';
  PLSEL=new Set(p?p.account_ids:[]);   // 先定人：课程下拉按这批账号过滤（2026-10-26 改）
  loadPlanBusy();
  renderPicked();                      // 内含 buildCourseOptions + updateCourseCov
  const sel=document.getElementById('pl_course');
  if(p){  // 编辑：按 pid 回选；老学习安排无 pid 则按课名回选
    let key=p.course_pid?('pid:'+p.course_pid):('name:'+p.course_name);
    if(![...sel.options].some(o=>o.value===key)){
      key='name:'+p.course_name;
      if(![...sel.options].some(o=>o.value===key)){  // 课名已不在任何账号课程里→补占位项
        sel.insertAdjacentHTML('beforeend',
          `<option value="${esc(key)}" data-name="${esc(p.course_name)}" data-pid="${esc(p.course_pid||'')}">${esc(p.course_name)}（原学习安排课名，未在账号课程中）</option>`);
      }
    }
    sel.value=key;
  }else{ sel.value=''; }
  updateCourseCov();
  document.getElementById('planDlg').showModal();
}
let PLCOURSE={};   // 课程下拉 key → {pid,name,ids:Set,schools:Set}（按平台课程ID聚合）
// 全库聚合：pid → 课程。同名不同班会分开（pid 不同）；同一 pid 覆盖多个账号时合并
function courseMap(){
  const map=new Map();
  (ACCOUNTS||[]).forEach(a=>(a.courses||[]).forEach(c=>{
    const key=c.platform_course_id?('pid:'+c.platform_course_id):('name:'+c.name);
    if(!map.has(key))map.set(key,{pid:c.platform_course_id||'',name:c.name,
      ids:new Set(),schools:new Set()});
    const v=map.get(key);
    v.ids.add(a.id);
    const sch=((a.profile||{}).school||'').trim();
    if(sch)v.schools.add(sch);
  }));
  return map;
}
// 课程归属学校的可读文本（画像 school 为空时明确提示，而不是留白让人猜）
function courseSchool(v){
  const s=[...(v&&v.schools)||[]];
  if(!s.length)return '学校未填';
  return s.slice(0,2).join('/')+(s.length>2?'等':'');
}
function accountHasCourse(id,pid){   // 该账号有没有抓到这门课（按平台课程ID）
  if(!pid)return true;               // 老学习安排无 pid：不做覆盖判断，避免误报
  const a=(ACCOUNTS||[]).find(x=>x.id===id);
  if(!a)return false;
  return (a.courses||[]).some(c=>c.platform_course_id===pid);
}
// 课程下拉：先勾账号 → 只列这些账号抓到的课；未勾账号则列全部（便于先看有什么课）
function buildCourseOptions(){
  const sel=document.getElementById('pl_course');
  if(!sel)return;
  const keep=sel.value;
  const map=courseMap(), ids=[...PLSEL];
  PLCOURSE={};
  const rows=[...map.entries()].filter(([k,v])=>!ids.length||ids.some(i=>v.ids.has(i)));
  rows.sort((x,y)=>y[1].ids.size-x[1].ids.size);
  const opts=rows.map(([key,v])=>{
    PLCOURSE[key]=v;
    const cov=ids.length?(' · 你选的 '+ids.filter(i=>v.ids.has(i)).length+'/'+ids.length+' 有'):'';
    return `<option value="${esc(key)}" data-name="${esc(v.name)}" data-pid="${esc(v.pid)}">`
      +`《${esc(v.name)}》 · ${esc(courseSchool(v))}${cov}</option>`;
  });
  sel.innerHTML='<option value="">（请选择课程）</option>'+opts.join('');
  if(keep&&[...sel.options].some(o=>o.value===keep))sel.value=keep;
}
function updateCourseCov(){
  const sel=document.getElementById('pl_course');
  const el=document.getElementById('pl_cov');
  if(!sel||!el)return;
  const ids=[...PLSEL], key=sel.value, v=PLCOURSE[key];
  const name=(sel.selectedOptions[0]?.dataset.name)||'';
  el.style.color='';
  if(!ids.length){
    el.style.color='var(--muted)';
    el.textContent=key?`课程《${name}》：还没勾账号 —— 勾账号后才会核对覆盖`
      :'① 先勾账号 → ② 课程列表只显示这些账号抓到的课';
    return;
  }
  if(!key||!name){
    el.style.color='var(--muted)';
    el.textContent=`已勾 ${ids.length} 个账号；请在上面选课程`;return;
  }
  const who=i=>{const a=(ACCOUNTS||[]).find(x=>x.id===i);
    return '#'+i+(a?' '+esc(accName(a)):'');};
  const have=ids.filter(i=>v&&v.ids.has(i));
  const lack=ids.filter(i=>!(v&&v.ids.has(i)));
  const cross=!!(v&&v.schools.size>1);   // 同一 pid 冒出多所学校 = 异常，值得警示
  if(lack.length){
    el.style.color='#c0392b';
    el.innerHTML=`⚠ 已勾 ${ids.length} 个账号，只有 <b>${have.length}</b> 个有《${esc(name)}》；`
      +`缺课：<b>${lack.map(who).join('、')}</b>（运行时会被跳过）`
      +(cross?' · <b>该课程ID跨校出现，请核对</b>':'');
  }else{
    el.style.color='var(--ok)';
    el.innerHTML=`✔ 已勾 ${ids.length} 个账号都有《${esc(name)}》 · ${esc(courseSchool(v))}`
      +(cross?' <span style="color:#c0392b">⚠ 该课程ID跨校出现，请核对</span>':'');
  }
}
function renderPicked(){
  const box=document.getElementById('pl_picked'); if(!box)return;
  const ids=[...PLSEL];
  document.getElementById('pl_count').textContent=`已选 ${ids.length} 个`;
  box.className='picked'+(ids.length?'':' muted');
  box.innerHTML=ids.length?ids.map(id=>{
    const a=(ACCOUNTS||[]).find(x=>x.id===id);
    const b=PLBUSY[id];
    return `<div class="pkrow"><b>#${id} ${esc(a?accName(a):'（账号已删）')}</b>
      <span class="muted">${esc(a?accDesc(a):'')}</span>
      ${b?`<span class="b-warn" style="font-size:.7rem">已在《${esc(b)}》</span>`:''}
      <span class="x" title="从本学习安排移除" onclick="pickedRemove(${id})">✕</span></div>`;}).join('')
    :'还没有选择账号 —— 点「＋ 选择账号」从列表里挑';
  buildCourseOptions();   // 账号变了 → 课程候选随之收窄（先定人再定课）
  updateCourseCov();
}
function pickedRemove(id){PLSEL.delete(+id);renderPicked();}
function clearPicked(){PLSEL=new Set();renderPicked();}
// ---- 账号选择器（多条件 + 多选）----
function openPicker(){
  PKSEL=new Set(PLSEL);
  loadPlanBusy();
  fillPickFilters();
  document.getElementById('pk_q').value='';
  document.getElementById('pickDlg').showModal();
  renderPick();
}
// 选择器筛选维度（2026-10-26：加学校/院系/班级——都是平台 stu-base-info 里能拿到、且已入库的字段；
// 选项全部由本地画像动态生成，**不产生任何平台访问**）
const PICKFILTERS=[['pk_school','school','学校'],['pk_dept','dept','院系'],
  ['pk_cls','cls','班级'],['pk_grade','grade','年级'],['pk_major','major','专业']];
function fillPickFilters(){
  for(const [id,k,label] of PICKFILTERS){
    const sel=document.getElementById(id); if(!sel)continue;
    const cur=sel.value;
    const vals=[...new Set((ACCOUNTS||[]).map(a=>((a.profile||{})[k]||'').trim()).filter(Boolean))].sort();
    sel.innerHTML=`<option value="">全部${label}</option>`
      +vals.map(v=>`<option value="${esc(v)}">${esc(v)}</option>`).join('')
      +`<option value="__empty__">（未填${label}）</option>`;  // 找“画像不全”的号去补
    sel.value=cur;
  }
}
function pickVisible(){
  const q=(document.getElementById('pk_q').value||'').trim().toLowerCase();
  const onlyFree=document.getElementById('pk_onlyfree').checked;
  // __empty__ = 该字段未填；其余为精确相等
  const conds=PICKFILTERS.map(([id,k])=>{const el=document.getElementById(id);
    return el&&el.value?[k,el.value]:null;}).filter(Boolean);
  return (ACCOUNTS||[]).filter(a=>{
    const p=a.profile||{};
    if(onlyFree&&PLBUSY[a.id])return false;
    for(const [k,v] of conds){
      const cur=(p[k]||'').trim();
      if(v==='__empty__'?cur!=='':cur!==v)return false;
    }
    if(!q)return true;
    return [a.id,a.label,a.platform_username,p.name,p.student_no,p.grade,p.major,p.term,
      p.school,p.dept,p.cls,a.note].filter(Boolean).join(' ').toLowerCase().includes(q);
  });
}
function renderPick(){
  const box=document.getElementById('pk_list'); if(!box)return;
  const rows=pickVisible();
  if(!rows.length){box.innerHTML=`<span class="muted">${(ACCOUNTS||[]).length?'没有匹配的账号（试试清空搜索或取消筛选）':'还没有账号'}</span>`;return;}
  const dash='<span class="muted">—</span>';
  box.innerHTML='<table class="ptable pick"><thead><tr>'
    + '<th>选</th><th>账号</th><th>备注</th><th>姓名</th><th>昵称</th><th>学号</th>'
    + '<th>年级</th><th>专业</th><th>学期</th><th>学校</th><th>院系</th><th>班级</th><th>状态</th>'
    + '</tr></thead><tbody>'
    + rows.map(a=>{const b=PLBUSY[a.id], p=a.profile||{};
      return `<tr class="${b?'off':''}" title="${b?'已在《'+esc(b)+'》里，需先停用原学习安排':'勾选后点「确定添加」加入学习安排'}">
        <td><input type="checkbox" value="${a.id}" ${b?'disabled':''} ${PKSEL.has(a.id)?'checked':''}
          onchange="pickToggle(${a.id},this.checked)"></td>
        <td class="ro">#${a.id}</td>
        <td class="ro">${esc(a.label||'')||dash}</td>
        <td>${esc(accName(a))}</td>
        <td class="ro">${esc(a.platform_username||'')||dash}</td>
        <td class="ro">${esc(p.student_no||'')||dash}</td>
        <td class="ro">${esc(p.grade||'')||dash}</td>
        <td class="ro">${esc(p.major||'')||dash}</td>
        <td class="ro">${esc(p.term||'')||dash}</td>
        <td class="ro">${esc(p.school||'')||dash}</td>
        <td class="ro">${esc(p.dept||'')||dash}</td>
        <td class="ro">${esc(p.cls||'')||dash}</td>
        <td class="ro">${b?`<span class="b-warn" style="font-size:.7rem">已在《${esc(b)}》</span>`:'<span class="muted">可选</span>'}</td>
      </tr>`;}).join('')+'</tbody></table>';
  document.getElementById('pk_count').textContent=`已选 ${PKSEL.size} 个`;
}
function pickToggle(id,on){if(on)PKSEL.add(+id);else PKSEL.delete(+id);
  document.getElementById('pk_count').textContent=`已选 ${PKSEL.size} 个`;}
function pickAll(on){
  pickVisible().filter(a=>!PLBUSY[a.id]).forEach(a=>{if(on)PKSEL.add(a.id);else PKSEL.delete(a.id);});
  renderPick();
}
function pickApply(){
  PLSEL=new Set(PKSEL);
  document.getElementById('pickDlg').close();
  renderPicked();
}
function doPlan(){
  const name=document.getElementById('pl_name').value.trim();
  const sel=document.getElementById('pl_course');
  const key=sel.value;
  const course=(sel.selectedOptions[0]?.dataset.name)||'';
  // 占位项（原学习安排课名已不在任何账号课程里）也带 data-pid，避免保存时把 pid 洗掉
  let pid=(sel.selectedOptions[0]?.dataset.pid)||'';
  if(key.startsWith('pid:'))pid=key.slice(4);
  else if(PLCOURSE[key]&&PLCOURSE[key].pid)pid=PLCOURSE[key].pid;
  const ids=[...PLSEL];
  if(!name||!course){toast('学习安排名与课程都不能为空（课程请从下拉里选）',1);return;}
  if(!ids.length){toast('至少选择一个账号',1);return;}
  const url=PLANMODE?`/api/plans/${PLANMODE}/update`:'/api/plans';
  fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name,course_name:course,course_pid:pid||null,account_ids:ids})})
  .then(r=>r.json()).then(d=>{
    if(!d.ok){toast('保存失败：'+d.error,1);return;}
    document.getElementById('planDlg').close();loadPlans();
    toast(PLANMODE?`✔ 学习安排 #${PLANMODE} 已更新（${ids.length} 个账号）`
                 :`✔ 已创建（默认为“已停用”）——点该学习安排「▶ 启用」后才会到点开刷（${ids.length} 个账号）`);
    PLANMODE=null;});
}
function togglePlan(id,en){
  fetch(`/api/plans/${id}/toggle`,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({enabled:!!en})}).then(r=>r.json()).then(d=>{
    if(!d.ok){toast('操作失败：'+d.error,1);loadPlans();return;}
    toast(en?'✔ 学习安排已启用（到点按各账号时间错峰开刷）':'✔ 学习安排已停用');
    loadPlans();});}
function delPlan(id){
  if(!confirm('删除该学习安排？（不影响已完成的刷课与作业）'))return;
  fetch(`/api/plans/${id}`,{method:'DELETE'}).then(r=>r.json()).then(()=>{
    toast('✔ 学习安排已删除');loadPlans();});}
let EDIT=null,RUNNING={},AUTOPLAN={times:{}},AUTOSTATE={},EXPANDED={};
function toggleMore(id){EXPANDED[id]=!EXPANDED[id];renderAccounts();}
function startGrind(id,course,pid){
  fetch('/api/study/start',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({account_id:id,course:course,course_pid:pid||null})})
  .then(r=>r.json()).then(d=>{alert(d.ok?'已开始刷《'+course+'》（拟人节奏，额度内自动停）':'失败：'+d.error);
    syncRun();});}
// 警示条「看日志」：切到日志页并把账号筛好，不用用户再手输
function gotoLogs(id){
  switchView('logs');
  LOGACC='#'+id;
  const el=document.getElementById('logAcc'); if(el)el.value=LOGACC;
  renderLogs(LOGN);
}
// 警示条「立即补跑」：等同卡片 ▶（走同一个防双开接口，不占守护次数）
function resumeStudy(id,course){
  if(!course){toast('该账号当前没有可开始的课程（先抓课程/章节）',1);return;}
  startGrind(id,course,'');
}
function stopGrind(id){
  if(!confirm('停止该账号当前刷课会话？（已完成视频保留）'))return;
  fetch('/api/study/stop',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({account_id:id})}).then(r=>r.json()).then(()=>syncRun());}
async function syncRun(){
  try{const d=await (await fetch('/api/study/status')).json();
    RUNNING=d.jobs||{};AUTOSTATE=d.auto||{};}catch(e){RUNNING={};AUTOSTATE={};}
  try{AUTOPLAN=await (await fetch('/api/autoplan')).json();}catch(e){AUTOPLAN={times:{}};}
  try{const d=await (await fetch('/api/overview')).json();
    const prev=document.getElementById('today').textContent;
    document.getElementById('today').textContent=d.today_videos;
    if(prev!=='-'&&prev!==String(d.today_videos)){  // 有新战果→指标闪烁
      const m=document.getElementById('today');m.classList.remove('flash');void m.offsetWidth;m.classList.add('flash');}
    ACCOUNTS=d.accounts;fillLogAccounts();}catch(e){}
  const btn=document.getElementById('autoBtn');
  const runN=Object.values(RUNNING).filter(j=>j.running).length;
  const el=document.getElementById('runN'); if(el)el.textContent=runN;
  if(AUTOSTATE.running){btn.textContent=`⏱ 调度运行中（${AUTOSTATE.since}）`;
    btn.style.background='rgba(34,160,93,.5)';}
  else{btn.textContent='⏱ 定时调度：点击启动';btn.style.background='';}
  const sync=document.getElementById('synced');
  if(sync)sync.textContent='自动刷新中 · 更新于 '+new Date().toLocaleTimeString('zh-CN',{hour12:false});
  renderAccounts();if(LOG_AUTO)renderLogs();}
function toggleAuto(){
  const on=AUTOSTATE.running;
  if(on&&!confirm('停止定时调度？（已开的学习会话会自行跑到当日额度停机）'))return;
  fetch('/api/auto/'+(on?'stop':'start')+'?source=ui',{method:'POST'})
    .then(r=>r.json()).then(d=>{if(!d.ok)alert(d.error);syncRun();});}
function doRenew(){fetch('/api/accounts/'+EDIT.id+'/renew',{method:'POST',
  headers:{'Content-Type':'application/json'},body:JSON.stringify({days:30})})
  .then(r=>r.json()).then(d=>{if(!d.ok){alert('失败');return;}
    const until=new Date(Date.now()+d.days_left*864e5).toISOString().slice(0,10);
    e_vu.value=until;  // 弹窗内即时刷新有效期
    e_meta.textContent=`已续期，剩余 ${d.days_left} 天（至 ${until}）`;
    reload();});}
function doLogin(id){
  const a=ACCOUNTS.find(x=>x.id===id);
  const who=a?(a.display_name||a.platform_username||a.label):('#'+id);
  toast('⏳ 正在尝试无头自动登录 '+who+'…');
  fetch(`/api/accounts/${id}/login`,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({manual:false})})
  .then(r=>r.json()).then(d=>{
    if(d.ok){toast('✔ 登录成功：'+who+'（会话已保存）');syncRun();loadProfiles();return;}
    const hint=d.hint?('建议：'+d.hint+'。'):'';
    if(!confirm('自动登录未成功：'+(d.error||'未知')+'。'+hint+'是否打开浏览器窗口手动完成登录（扫码 / 验证码 / 短信）？'))return;
    fetch(`/api/accounts/${id}/login`,{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({manual:true})})
    .then(r=>r.json()).then(m=>{
      if(!m.ok){toast('手动登录启动失败：'+(m.error||''),1);return;}
      toast('🖥 已打开浏览器窗口，请在窗口中完成登录…（完成后自动刷新）');
      pollLogin(id,Date.now()+180000);
    }).catch(e=>toast('手动登录启动失败：'+e,1));
  }).catch(e=>toast('登录请求失败：'+e,1));
}
async function pollLogin(id,deadline){
  while(Date.now()<deadline){
    await new Promise(r=>setTimeout(r,3000));
    await syncRun();
    const a=ACCOUNTS.find(x=>x.id===id);
    if(a&&a.has_session){
      toast('✔ 检测到已登录：'+(a.display_name||a.platform_username||a.label)+'（可点『⬇ 平台读取』补画像）');
      loadProfiles();return;
    }
  }
  toast('未检测到登录成功（窗口是否已关闭 / 超时），请稍后刷新或重试',1);
}
let WORK_ACC=null,WORK_CH='';
function doWork(id,chapter,force=false,qidx=null){
  const a=ACCOUNTS.find(x=>x.id===id); if(!a)return;
  const mode=(a.work_mode||'').trim()||( (typeof QUIZ!=='undefined'&&QUIZ.work_mode_default) || 'owner');
  const named=a.works? a.works.find(w=>w.name===chapter) : null;
  if(mode==='reference'){
    WORK_ACC=id;WORK_CH=chapter;
    document.getElementById('w_ch').textContent=`#${id} ${a.platform_username||a.label}｜${chapter}`;
    document.getElementById('w_body').textContent='正在打开作业页解析题目…';
    document.getElementById('workDlg').showModal();
    // 渐进式第一步：只取题目（快）→ 立即渲染题干 + 生成中占位
    fetch(`/api/accounts/${id}/work`,{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({chapter:chapter,questions_only:true})}).then(r=>r.json()).then(d0=>{
      if(!d0.ok||d0.action!=='questions'){document.getElementById('w_body').textContent='题目解析失败：'+(d0.error||'');return;}
      document.getElementById('w_body').innerHTML=d0.questions.map(x=>
        `<div class="qa"><div class="q"><b>第 ${x.idx+1} 题（${esc(x.qtype_cn)}）</b><br>${esc(x.stem)}</div>
         <div class="a" id="ans_${x.idx}"><span class="muted">⏳ 答案生成中…</span></div></div>`).join('');
      // 第二步：取答案（题库优先/LLM 并行）逐题填充
      fetch(`/api/accounts/${id}/work`,{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({chapter:chapter,force:force,question_idx:qidx,
          extra_requirements:(document.getElementById('w_req')?.value||'').trim()})}).then(r=>r.json()).then(d=>{
        if(!d.ok){document.getElementById('w_body').textContent='失败：'+d.error;return;}
        (d.answers||[]).forEach(x=>{
          const cell=document.getElementById('ans_'+(x.idx??0));
          if(!cell)return;
          const src=x.source==='bank'?'<span class="chip">题库答案</span>':'<span class="chip">AI 新生成</span>';
          const rb=`<button class="minibtn" style="float:right" title="仅重生成本题（带附加要求）"
            onclick="doWork(${id},'${esc(chapter).replace(/'/g,'&#39;')}',false,${x.idx??0})">🔄</button>`;
          cell.innerHTML=`${rb}${src}<br>${esc(x.answer)||'<span class="muted">（空）</span>'}`;});
      });});
  } else if(mode==='ai'){
    if(!confirm('AI 代答模式：后台填入该账号作业（semi 不提交）。完成后需到平台作业页核对并提交。继续？'))return;
    fetch(`/api/accounts/${id}/work`,{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({chapter:chapter})}).then(r=>r.json()).then(d=>{
      alert(d.ok?d.message:'失败：'+d.error); if(d.ok)syncRun();});
  } else {
    const who = a.platform_username || a.label;
    alert(`该账号作业策略=号主自理（默认）：作业由 ${who} 自己完成，本工具只负责刷视频。如需 AI 辅助：✎ 管理 → 作业策略 → 改为"AI 参考答案"或"AI 代答"，或⚙设置里改全局默认。`);
  }
}
function doReview(id,chapter){
  fetch(`/api/accounts/${id}/work/review`,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({chapter:chapter})}).then(r=>r.json()).then(d=>{
    if(!d.ok){alert('读判分失败：'+d.error);return;}
    const lines=(d.results||[]).map(r=>`[${r.status}] ${r.stem}（得分 ${r.score??'待批阅'}）`).join('\\n');
    alert(`判分回读完成：\\n${lines}\\n\\n题库已自动更新（答对转正/答错清空）。`);
    syncRun();});
}
function fillFromRef(){
  if(!WORK_ACC){alert('未选择账号');return;}
  if(!confirm('将把参考答案填入该账号作业页并暂存（草稿，不提交）。约 1 分钟完成，之后请登录平台核对满意再提交。继续？'))return;
  fetch(`/api/accounts/${WORK_ACC}/work`,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({chapter:WORK_CH,action:'fill'})}).then(r=>r.json()).then(d=>{
    alert(d.ok?d.message:'失败：'+d.error);
    if(d.ok){document.getElementById('workDlg').close();syncRun();reload();}});
}
async function doWorkAll(id){
  const a=ACCOUNTS.find(x=>x.id===id); if(!a)return;
  const mode=((a.work_mode||'').trim()||(QUIZ&&QUIZ.work_mode_default)||'owner');
  if(mode==='owner'){alert(`该账号作业策略=号主自理，批量作业跳过。\n如需 AI 处理：✎管理改策略，或⚙设置改全局默认。`);return;}
  const pend=(a.works||[]).filter(w=>w.status!=='done');
  if(!pend.length){alert('该账号没有未完成的作业任务');return;}
  for(const w of pend){ await doWork(id,w.name); await new Promise(r=>setTimeout(r,800)); }
}
function editAccount(id){EDIT=ACCOUNTS.find(a=>a.id===id);if(!EDIT)return;
  e_title.textContent=`#${EDIT.id}`;e_label.value=EDIT.label;e_status.value=EDIT.status;
  e_pri.value=EDIT.priority;e_ws.value=EDIT.window_start||'';e_we.value=EDIT.window_end||'';
  e_sa.value=EDIT.start_at||'';e_wm.value=EDIT.work_mode||'';  // 空=跟随全局
  e_min.value=EDIT.max_minutes??0;e_task.value=EDIT.max_tasks??0;e_vu.value=EDIT.valid_until||'';
  e_note.value=EDIT.note||'';
  e_meta.textContent=`续期 ${EDIT.renew_count||0} 次 · 上次失效 ${EDIT.last_expired_at||'—'}`;
  document.getElementById('editDlg').showModal();}
function doSave(){fetch('/api/accounts/'+EDIT.id,{method:'PUT',
  headers:{'Content-Type':'application/json'},body:JSON.stringify({
    label:e_label.value.trim(),status:e_status.value,priority:+e_pri.value||1,
    start_at:e_sa.value||null,work_mode:e_wm.value,
    window_start:e_ws.value||null,window_end:e_we.value||null,
    daily_max_minutes:+e_min.value||0,daily_max_tasks:+e_task.value||0,
    valid_until:e_vu.value||null,note:e_note.value.trim()||null})})
  .then(r=>r.json()).then(d=>{alert(d.ok?'已保存':'失败：'+d.error);
    if(d.ok){document.getElementById('editDlg').close();reload();}});}
function doDel(){if(!confirm('确定删除账号 '+EDIT.label+'？课程/进度数据保留'))return;
  fetch('/api/accounts/'+EDIT.id,{method:'DELETE'}).then(r=>r.json()).then(d=>{
    if(d.ok){document.getElementById('editDlg').close();reload();}else alert('失败');});}
document.getElementById('q').addEventListener('input',()=>{PAGE=1;
  if(VIEW!=='accounts')switchView('accounts');  // 搜索只作用于账号卡片，自动切回
  renderAccounts();});
document.getElementById('psize').addEventListener('change',()=>{PAGE=1;renderAccounts();});
LOG_AUTO=localStorage.getItem('logAuto')!=='0';
{const _la=document.getElementById('logAuto'); if(_la)_la.checked=LOG_AUTO;}
renderLogs();loadSettings();loadProviders();loadPlans();switchView(VIEW);syncRun();setInterval(()=>{syncRun();loadPlans();},15000);
</script></body></html>"""


def _startup_cleanup(settings: Settings) -> dict:
    """面板启动自检（用户要求"改后重启即干净"）：清孤儿运行标记、归空旧默认残留。

    只清理可再生成的状态（run 标记、显式 owner 残留），绝不动数据（库/会话/题库）。
    """
    report: dict[str, int] = {"run_markers": 0, "owner_residue": 0}
    run_dir = settings.data_dir / "run"
    if run_dir.exists():
        for f in run_dir.glob("*.json"):
            # 只清"进程已死"的孤儿标记：会话子进程由 subprocess 独立拉起，
            # 面板重启不影响它。无条件删除会让面板误判账号空闲 → 同账号双开（红线）。
            keep = False
            try:
                import json as _j

                keep = _pid_alive(
                    int(_j.loads(f.read_text(encoding="utf-8")).get("pid") or 0), f)
            except Exception:
                keep = False
            if keep:
                continue
            f.unlink(missing_ok=True)
            report["run_markers"] += 1
    try:
        conn = sqlite3.connect(settings.db_path)
        cur = conn.execute(
            "UPDATE accounts SET work_mode=NULL WHERE work_mode='owner'")
        report["owner_residue"] = cur.rowcount
        conn.commit()
        conn.close()
    except Exception:
        pass
    return report


def conn2_has_key(settings: Settings) -> bool:
    """本机是否已存加密 Key（llm.key_enc）。"""
    import sqlite3 as _s

    try:
        c = _s.connect(settings.db_path)
        try:
            return c.execute(
                "SELECT 1 FROM app_config WHERE key='llm.key_enc'").fetchone() is not None
        finally:
            c.close()
    except Exception:
        return False


def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(title="XXT 管理面板", docs_url="/api/docs")

    cleanup = _startup_cleanup(settings)  # 启动即清（FastAPI 单实例语义）
    if any(cleanup.values()):
        from ..log import get_logger

        get_logger("panel").info("启动自检清理：%s", cleanup)
    if not conn2_has_key(settings):
        from ..log import get_logger

        get_logger("panel").warning(
            "⚠ 未配置 LLM Key（环境变量与加密存储均无）——AI 功能将不可用；"
            "面板⚙设置中粘贴 Key 保存即可")

    def db() -> sqlite3.Connection:
        return connect(settings.db_path)

    @app.get("/api/overview")
    def overview():
        """多账号总览：每账号的登录状态、覆盖配置、课程进度与当日战果。"""
        from datetime import datetime as _dt

        def today_stats(conn: sqlite3.Connection, account_id: int, today: str) -> dict:
            """分钟数 = Σ 每个完成视频的真实时长（video-done 详情尾部"Ns"，纯观看时长）。
            机制上线前的历史记录无时长后缀，不计入（宁少勿虚报）。
            解析复用于 runner 的额度判定（core.quota.sum_done_seconds），口径只有一处。"""
            from ..core.quota import sum_done_seconds

            rows = conn.execute(
                "SELECT detail FROM run_logs WHERE event='video-done' "
                "AND account_id=? AND ts LIKE ?", (account_id, f"{today}%"),
            ).fetchall()
            seconds = sum_done_seconds(r["detail"] or "" for r in rows)
            return {"today_videos": len(rows), "today_min": int(seconds // 60)}

        conn = db()
        try:
            today = time.strftime("%Y-%m-%d")
            accounts = [dict(r) for r in list_accounts(conn)]
            from ..core.accounts import days_left, effective_quota
            from ..core.stats import get_profile

            fresh = Settings(root=settings.root)  # 每次请求重读 DB：改全局额度后无需重启面板
            global_default = fresh.quiz.get(
                "work_mode_default", "owner")  # 生效策略计算用（账号显式优先）
            for a in accounts:
                state = settings.data_dir / "accounts" / str(a["id"]) / "state.json"
                a["has_session"] = state.exists()
                # 有效额度（账号级 > 全局；0/NULL = 跟随全局）——卡片显示"今日 x / 上限 y"
                _q = effective_quota(fresh, a)
                a["quota_minutes"] = _q["daily_minutes"]
                a["quota_tasks"] = _q["daily_tasks"]
                a["quota_from_global"] = not (a.get("daily_max_minutes")
                                             or a.get("daily_max_tasks"))
                # 生效策略 = 账号显式 > 全局默认（卡片显示"实际生效值"并标注来源）
                eff = a.get("work_mode") or global_default
                a["work_effective"] = eff
                a["work_from_global"] = not a.get("work_mode")
                a.update(today_stats(conn, a["id"], today))
                a["window"] = (
                    f"{a['window_start']}–{a['window_end']}"
                    if a["window_start"] and a["window_end"] else None
                )
                a["max_minutes"] = a.pop("daily_max_minutes", None)
                a["max_tasks"] = a.pop("daily_max_tasks", None)
                a["days_left"] = days_left(a)
                # 画像（姓名/学号/年级/专业/学期）：卡片与学习安排弹窗都要显示，一并下发
                # 学校/院系/班级一并下发：学习安排弹窗的课程下拉要用它标注「这门课是哪所学校的」
                a["profile"] = {k: (get_profile(conn, a["id"]).get(k) or "") for k in
                                ("name", "student_no", "grade", "major", "term",
                                 "school", "dept", "cls")}
                a["student_no"] = a["profile"]["student_no"]
                a["display_name"] = a["profile"]["name"] or (a.get("platform_username") or "")
                a["courses"] = [
                    dict(r)
                    for r in conn.execute(
                        """SELECT c.name, c.platform_course_id, c.clazz_id,
                           (SELECT COUNT(*) FROM chapters h WHERE h.course_id=c.id) AS total,
                           (SELECT COUNT(*) FROM chapters h WHERE h.course_id=c.id
                              AND h.status='done') AS done
                           FROM courses c WHERE c.account_id=? ORDER BY c.id""",
                        (a["id"],),
                    )
                ]
                # 每门课程：已学时长（按标题去重的真实时长和）+ 预计总时长（未看的按平均估）
                import re as _re_dur

                for c in a["courses"]:
                    durations: dict[str, int] = {}  # 标题 → 时长（同名视频去重取最大）
                    for (detail,) in conn.execute(
                            "SELECT detail FROM run_logs WHERE account_id=? "
                            "AND event='video-done' AND detail LIKE ?",
                            (a["id"], f"{c['name']}｜%")):
                        m = _re_dur.match(rf"{_re_dur.escape(c['name'])}｜(.+)｜(\d+)s$",
                                          detail or "")
                        if m:
                            t, n = m.group(1), int(m.group(2))
                            durations[t] = max(durations.get(t, 0), n)
                    known_secs = sum(durations.values())
                    c["total_min"] = int(known_secs // 60)
                    # 视频类知识点数（排除作业/测验/考试）；未看的按已知平均时长估算
                    video_points = conn.execute(
                        "SELECT COUNT(*) FROM chapters WHERE course_id="
                        "(SELECT id FROM courses WHERE account_id=? AND name=?) "
                        "AND name NOT LIKE '%作业%' AND name NOT LIKE '%测验%' "
                        "AND name NOT LIKE '%考试%'", (a["id"], c["name"]),
                    ).fetchone()[0]
                    if durations and video_points > len(durations):
                        avg = known_secs / len(durations)
                        c["est_total_min"] = int((known_secs + avg * (video_points - len(durations))) // 60)
                    else:
                        c["est_total_min"] = c["total_min"]  # 全部已知或无数据
                    c["est_is_exact"] = video_points <= len(durations)
                # 作业任务点挂课程（名称含作业/测验/考试；状态=章节状态；AI 已答标记）
                cids = [c2["id"] for c2 in conn.execute(
                    "SELECT id FROM courses WHERE account_id=?", (a["id"],))]
                works = []
                for cid in cids:
                    cname = conn.execute("SELECT name FROM courses WHERE id=?", (cid,)).fetchone()["name"]
                    for h in conn.execute(
                            "SELECT name, status FROM chapters WHERE course_id=? "
                            "AND (name LIKE '%作业%' OR name LIKE '%测验%' OR name LIKE '%考试%') "
                            "ORDER BY idx", (cid,)):
                        # AI 处理判定：work-answer 日志以章节名开头；
                        # 状态区分——含"参考答案"=仅出答案未填，否则=真实填入（均未提交）
                        row2 = conn.execute(
                            "SELECT detail FROM run_logs WHERE account_id=? AND event='work-answer' "
                            "AND detail LIKE ? ORDER BY id DESC LIMIT 1",
                            (a["id"], f"{h['name'][:14]}%")).fetchone()
                        ai_answered = bool(row2)
                        ai_filled = bool(row2) and "参考答案" not in (row2["detail"] or "")
                        works.append({"course": cname, "name": h["name"],
                                      "status": h["status"],
                                      "ai_answered": ai_answered,
                                      "ai_filled": ai_filled})
                a["works"] = works
            today = time.strftime("%Y-%m-%d")
            n = conn.execute(
                "SELECT COUNT(*) AS c FROM run_logs WHERE event='video-done' AND ts LIKE ?",
                (f"{today}%",),
            ).fetchone()["c"]
            # 警示条数据（Q20）：**必须挂在 /api/overview 上**——卡片是这里渲染出来的。
            # 2026-10-27 实测踩过：只给 /api/accounts 挂了 alert，接口测试全绿，
            # 真实页面却一条警示条都没出现过。两边都留下同一份数据才作数。
            alerts = _account_alerts(conn, today)
            for a in accounts:
                a["alert"] = alerts.get(int(a["id"]))
            return {"today_videos": n, "accounts": accounts}
        finally:
            conn.close()

    @app.get("/api/progress")
    def progress():
        conn = db()
        try:
            rows = conn.execute(
                """SELECT c.name,
                   (SELECT COUNT(*) FROM chapters h WHERE h.course_id=c.id) AS total,
                   (SELECT COUNT(*) FROM chapters h WHERE h.course_id=c.id
                      AND h.status='done') AS done
                   FROM courses c WHERE c.account_id=1 ORDER BY c.id"""
            ).fetchall()
            today = time.strftime("%Y-%m-%d")
            n = conn.execute(
                "SELECT COUNT(*) AS c FROM run_logs WHERE event='video-done' AND ts LIKE ?",
                (f"{today}%",),
            ).fetchone()["c"]
            return {"today_videos": n, "courses": [dict(r) for r in rows]}
        finally:
            conn.close()

    def _account_alerts(conn, day: str) -> dict[int, dict]:
        """今日每个账号最近一次失败 → 归类后的警示（2026-10-27 用户 Q20）。

        用户原话：「打不开要反馈原因，要有提示，不能就收工，要让人家能够有对应的方法
        和入口」。规则（纯本地、零平台访问）：
        - 取该账号今日最后一条章节/会话/守护事件；
        - 已经又播完一节、正在学、或已达成（auto-done）→ 不显示；
        - 最后是 session-end：只有含「打不开/异常/失败/中断」才警示（正常达标收工不打扰）；
        - 其余失败类交给 `core/diagnose` 归类；auto-error 带「硬停」按硬停展示。
        """
        from ..core.diagnose import actions_for, classify, describe, tier_for

        # 挂起中的账号要回答“多久才轮回来”：退避时刻表由守护落进 app_config
        # （2026-10-27 同一个表也用于守护重启续跑，两边看到的是同一个事实）。
        import json as _json

        resume: dict[int, str] = {}
        try:
            row_ra = conn.execute(
                "SELECT value FROM app_config WHERE key='auto.retry_after'").fetchone()
            if row_ra:
                info = _json.loads(row_ra["value"])
                if info.get("date") == day:
                    resume = {int(k): v for k, v in (info.get("times") or {}).items() if v}
        except Exception:
            resume = {}

        try:
            rows = conn.execute(
                "SELECT account_id, ts, event, detail FROM run_logs"
                " WHERE ts LIKE ? AND account_id > 0 AND event IN"
                " ('auto-error','auto-revoke','auto-suspend','auto-done',"
                "  'video-interrupt','chapter-fail',"
                "  'chapter-start','video-done','session-end')"
                " ORDER BY id DESC", (day + "%",)).fetchall()
        except Exception:
            return {}
        last: dict[int, object] = {}
        for r in rows:
            last.setdefault(int(r["account_id"]), r)
        out: dict[int, dict] = {}
        for aid, r in last.items():
            ev, detail = str(r["event"]), str(r["detail"] or "")
            if ev in ("chapter-start", "video-done", "auto-done"):
                continue  # 又跑起来了 / 今日已达成 → 不示警
            if ev == "session-end":
                if not any(w in detail for w in ("打不开", "异常", "失败", "中断")):
                    continue  # 正常收工（达标/完成/无待学章节）
                code = classify(detail)
            elif ev in ("chapter-fail", "video-interrupt"):
                code = "structure"
            else:
                code = classify(detail)
            tier = tier_for(code)
            if tier == "achieved":
                continue  # 达成不算异常，不占卡片
            if ev == "auto-error" and "硬停" in detail:
                tier = "hard_stop"   # 守护已判今日不再自动 → 警示条标硬停
            label, hint = describe(code)
            out[aid] = {"code": code, "tier": tier, "label": label, "hint": hint,
                        "detail": detail[:160], "ts": str(r["ts"]),
                        "resume_at": resume.get(aid, ""),
                        "actions": actions_for(code)}
        return out

    @app.get("/api/accounts")
    def accounts():
        conn = db()
        try:
            rows = [dict(r) for r in list_accounts(conn)]
            alerts = _account_alerts(conn, time.strftime("%Y-%m-%d"))
            for r in rows:
                r["alert"] = alerts.get(int(r["id"]))
            return {"accounts": rows}
        finally:
            conn.close()

    @app.get("/api/logs")
    def logs(n: int = 50, account: int | None = None, accounts: str | None = None,
             source: str = "event", min_level: str = "WARNING", sub: int = 0):
        """日志页数据。source=event：业务事件（run_logs）；source=sys：技术日志文件。

        业务事件与系统日志是两条道（2026-10-26 用户疑问“页面为何看不到守护动作”）：
        run_logs 只记业务事件；面板/守护/会话的 INFO（含 traceback）在 data/logs/ 下，
        过去只能翻文件。现在把两者都接到本页——
        - event：run_logs（account_id、accounts=1,2 可筛）
        - sys：   data/logs/xxt.log，min_level=WARNING|INFO，sub=1 时并入当天 sub-*.log
        """
        limit = max(1, min(n, 500))
        if source == "sys":
            rank = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
            floor = rank.get(min_level.upper(), 30)
            pat = re.compile(
                r"^(\d{4}-\d\d-\d\d) (\d\d:\d\d:\d\d),\d+ (\w+) \[([^\]]+)\] (.*)$")

            def tail(path):
                try:
                    with path.open("r", encoding="utf-8", errors="replace") as fh:
                        return deque(fh, maxlen=2000)
                except OSError:
                    return deque()

            logdir = settings.data_dir / "logs"
            files = [logdir / "xxt.log"]
            if sub:  # 只合今天的子进程日志（最多 4 个），历史文件别扰当前排查
                day0 = time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d"))
                cand = [p for p in logdir.glob("sub-*.log") if p.stat().st_mtime >= day0]
                files += sorted(cand)[-4:]
            out = []
            for fp in files:
                is_sub = fp.name != "xxt.log"
                last_ts = ""
                for line in tail(fp):
                    line = line.rstrip("\n")
                    if not line.strip():
                        continue
                    m = pat.match(line)
                    if m:
                        d, t, lv, logger, msg = m.groups()
                        last_ts = f"{d}T{t}"
                        if is_sub:
                            continue  # 子进程文件里的 logging 行 xxt.log 已有，避免重复
                    elif sub:  # 子进程的 print / traceback 无时间戳：整行保留，时间兑上一条
                        lv, logger, msg = "INFO", "sub:" + fp.stem, line
                    else:
                        continue
                    if rank.get(lv, 20) < floor:
                        continue
                    out.append({"ts": last_ts, "level": lv, "event": logger,
                                "detail": msg, "account_id": 0, "who": "系统"})
            out.sort(key=lambda x: x["ts"], reverse=True)
            return {"logs": out[:limit]}
        conn = db()
        try:
            sql = (
                "SELECT l.ts, l.level, l.event, l.detail, l.account_id, "
                "COALESCE(a.platform_username, a.label, '#'||l.account_id) AS who "
                "FROM run_logs l LEFT JOIN accounts a ON a.id = l.account_id"
            )
            where, params = [], []
            if account:
                where.append("l.account_id = ?")
                params.append(account)
            if accounts:  # 可搜索筛选的多命中（逗号分隔），与 account 互斥
                ids = [int(x) for x in str(accounts).split(",") if str(x).strip().isdigit()]
                if ids:
                    where.append("l.account_id IN (%s)" % ",".join("?" * len(ids)))
                    params += ids
            if where:
                sql += " WHERE " + " AND ".join(where)
            sql += " ORDER BY l.id DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(sql, tuple(params)).fetchall()
            return {"logs": [dict(r) for r in rows]}
        finally:
            conn.close()

    def _providers_load() -> dict:
        """厂商列表（app_config: llm.providers JSON）+ 当前选中。内置五家作为基线。"""
        builtin = [
            {"name": "GLM", "base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4-flash"},
            {"name": "DeepSeek", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat"},
            {"name": "Qwen", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus"},
            {"name": "Kimi", "base_url": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k"},
            {"name": "Agnès", "base_url": "https://api.agnes-ai.cn/v1", "model": "agnes-2.5-flash"},
        ]
        fresh = Settings(root=settings.root)
        saved = fresh.raw.get("llm", {}).get("providers") or []
        current = fresh.llm.get("provider_name") or "Agnès"
        names = {p["name"] for p in builtin}
        custom = [p for p in saved if p.get("name") not in names]
        return {"builtin": builtin, "custom": custom, "current": current}

    @app.get("/api/llm/providers")
    def providers_get():
        return {"ok": True, **_providers_load()}

    @app.post("/api/llm/providers")
    def providers_save(body: dict):
        """保存厂商（自定义项进列表；同名覆盖）并把该厂商设为当前——含加密 Key。"""
        import json as _json

        name = str(body.get("name") or "").strip()
        base_url = str(body.get("base_url") or "").strip()
        model = str(body.get("model") or "").strip()
        key = str(body.get("key") or "").strip()
        if not (name and base_url and model):
            return {"ok": False, "error": "名称/BaseURL/模型 均必填"}
        fresh = Settings(root=settings.root)
        saved = list(fresh.raw.get("llm", {}).get("providers") or [])
        names = {p["name"] for p in builtin_list}
        entry = {"name": name, "base_url": base_url, "model": model}
        saved = [p for p in saved if p["name"] != name]
        if name not in {b["name"] for b in builtin_list}:
            saved.append(entry)
        conn = db()
        try:
            upsert = ("INSERT INTO app_config(key,value) VALUES(?,?) "
                      "ON CONFLICT(key) DO UPDATE SET value=excluded.value")
            conn.execute(upsert, ("llm.providers", _json.dumps(saved, ensure_ascii=False)))
            conn.execute(upsert, ("llm.provider_name", _json.dumps(name, ensure_ascii=False)))
            conn.execute(upsert, ("llm.base_url", _json.dumps(base_url)))
            conn.execute(upsert, ("llm.model", _json.dumps(model)))
            if key:  # 方案B：Key 加密落 data/（AES-GCM，主密钥同账号凭据体系）
                from ..core.crypto import encrypt, master_key

                # 注意：key_enc 故意存裸 base64（不 json.dumps）——ai/llm.py 直接读该行做
                # b64decode，加引号会解不开。它是独立字段，不进配置树。
                conn.execute(upsert, ("llm.key_enc", base64.b64encode(
                    encrypt(master_key(settings.data_dir), key)).decode()))
            conn.commit()
        finally:
            conn.close()
        return {"ok": True}

    @app.post("/api/llm/test")
    def llm_test(body: dict):
        """连通性测试：真实发一条 1-token 请求。"""
        import httpx as _httpx

        fresh = Settings(root=settings.root)
        base_url = str(body.get("base_url") or fresh.llm["base_url"]).rstrip("/")
        model = str(body.get("model") or fresh.llm["model"])
        key = str(body.get("key") or "").strip()
        if not key:  # 未粘贴 → 只用已存加密 Key（不读环境变量：分发版数据封闭原则）
            try:
                from ..core.crypto import decrypt, master_key

                conn = db()
                try:
                    row = conn.execute(
                        "SELECT value FROM app_config WHERE key='llm.key_enc'").fetchone()
                finally:
                    conn.close()
                if row:
                    key = decrypt(master_key(settings.data_dir), base64.b64decode(row[0]))
            except Exception:
                pass
        if not key:
            return {"ok": False, "error": "未配置 Key——请在上方 Key 框粘贴后点『保存到厂商列表』（加密存本机）"}
        try:
            r = _httpx.post(f"{base_url}/chat/completions",
                            headers={"Authorization": f"Bearer {key}"},
                            json={"model": model, "messages": [
                                {"role": "user", "content": "回复：ok"}],
                                "max_tokens": 5},
                            timeout=30)
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"]
            return {"ok": True, "reply": text.strip()[:40]}
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:180]}

    builtin_list = [  # 供 providers_save 判断内置项
        {"name": "GLM"}, {"name": "DeepSeek"}, {"name": "Qwen"},
        {"name": "Kimi"}, {"name": "Agnès"},
    ]

    @app.post("/api/llm/providers/del")
    def providers_del(body: dict):
        import json as _json

        name = str(body.get("name") or "").strip()
        fresh = Settings(root=settings.root)
        saved = [p for p in (fresh.raw.get("llm", {}).get("providers") or [])
                 if p["name"] != name]
        conn = db()
        try:
            conn.execute(
                "INSERT INTO app_config(key,value) VALUES('llm.providers',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (_json.dumps(saved, ensure_ascii=False),))
            if fresh.llm.get("provider_name") == name:
                # 删掉的是当前 Provider：名字、地址、模型要一起回落到内置默认，
                # 否则会留下"名字是 Agnès、地址还是已删除那家"的错配（JSON 化写入）。
                from ..config import DEFAULTS

                _d = DEFAULTS["llm"]
                for _k, _v in (("llm.provider_name", "Agnès"),
                               ("llm.base_url", _d["base_url"]),
                               ("llm.model", _d["model"])):
                    conn.execute(
                        "INSERT INTO app_config(key,value) VALUES(?,?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (_k, _json.dumps(_v, ensure_ascii=False)))
            conn.commit()
        finally:
            conn.close()
        return {"ok": True}

    def _recent_changes(limit: int = 12) -> list[dict]:
        """最近配置变更（config_audit）→ 「守护与节级策略」页底部展示。

        2026-10-27 用户要求：“每次调整都要记录清楚，方便查阅和调整”。
        读失败绝不能阻塞设置页本身。
        """
        try:
            c = db()
            try:
                return [{"ts": r["ts"], "key": r["key"],
                         "old_value": r["old_value"], "new_value": r["new_value"]}
                        for r in c.execute(
                            "SELECT ts, key, old_value, new_value FROM config_audit"
                            " ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]
            finally:
                c.close()
        except Exception:
            return []

    @app.get("/api/settings")
    def get_settings():
        fresh = Settings(root=settings.root)  # 重新加载（含 app_config 覆盖层）

        def _i(cfg: dict | None, key: str, default: int) -> int:
            try:
                return int((cfg or {}).get(key, default))
            except (TypeError, ValueError):
                return default

        auto_cfg = fresh.raw.get("auto") or {}
        learn_cfg = fresh.learn or {}
        return {
            "quiz_enabled": bool(fresh.quiz["enabled"]),
            "window_start": fresh.pace["daily_window"][0],
            "window_end": fresh.pace["daily_window"][1],
            "daily_minutes": int(fresh.quota["daily_minutes"]),
            "daily_tasks": int(fresh.quota["daily_tasks"]),
            "work_mode_default": fresh.quiz.get("work_mode_default", "owner"),
            # 守护与节级策略（2026-10-27 用户：“必为配置项，面板可改”）
            "auto_max_tries": _i(auto_cfg, "max_tries", 2),
            "auto_retry_minutes": _i(auto_cfg, "retry_minutes", 15),
            "auto_retry_jitter_pct": _i(auto_cfg, "retry_jitter_pct", 40),
            "auto_retry_max_minutes": _i(auto_cfg, "retry_max_minutes", 90),
            "auto_hard_tries_cap": _i(auto_cfg, "hard_tries_cap", 4),
            "auto_confirm_seconds": _i(auto_cfg, "confirm_seconds", 25),
            "learn_enter_retries": _i(learn_cfg, "enter_retries", 2),
            "learn_max_skip_chapters": _i(learn_cfg, "max_skip_chapters", 3),
            "learn_non_video_streak": _i(learn_cfg, "non_video_streak", 4),
            "learn_partial_retries": _i(learn_cfg, "partial_retries", 1),
            "learn_partial_retry_ratio_pct": _i(learn_cfg, "partial_retry_ratio_pct", 60),
            "learn_login_budget_per_day": _i(learn_cfg, "login_budget_per_day", 2),
            "learn_non_video_marks": str(learn_cfg.get("non_video_marks", "") or ""),
            "llm_base_url": fresh.llm["base_url"],
            "llm_model": fresh.llm["model"],
            "llm_provider_name": fresh.llm.get("provider_name", ""),
            "llm_key_stored": conn2_has_key(settings),
            "recent_changes": _recent_changes(),
        }

    @app.put("/api/settings")
    def put_settings(body: dict):
        """面板设置 → app_config 全局覆盖层（白名单键，v0.6 三层配置第 2 层）。"""
        import json as _json
        import re as _re

        def upsert(conn: sqlite3.Connection, key: str, value) -> None:
            """写覆盖层 + 变更审计（2026-10-27 用户：“每次调整都要记录清楚”）。

            值没变就不写——否则用户每点一次保存都刷一条无意义记录，反而没法查。
            """
            row = conn.execute(
                "SELECT value FROM app_config WHERE key=?", (key,)).fetchone()
            old = row["value"] if row else None
            new_txt = _json.dumps(value)
            if old == new_txt:
                return
            conn.execute(
                "INSERT INTO app_config(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, new_txt),
            )
            conn.execute(
                "INSERT INTO config_audit(ts, source, key, old_value, new_value)"
                " VALUES(?,?,?,?,?)",
                (time.strftime("%Y-%m-%dT%H:%M:%S"), "panel", key,
                 "" if old is None else str(old).strip('"'), str(value)),
            )

        conn = db()
        try:
            if "quiz_enabled" in body:
                if not isinstance(body["quiz_enabled"], bool):
                    return {"ok": False, "error": "quiz_enabled 需为布尔"}
                upsert(conn, "quiz.enabled", body["quiz_enabled"])
            ws, we = body.get("window_start"), body.get("window_end")
            if ws or we:
                if not (_re.fullmatch(r"\d{2}:\d{2}", ws or "") and _re.fullmatch(r"\d{2}:\d{2}", we or "")):
                    return {"ok": False, "error": "窗口格式应为 HH:MM"}
                upsert(conn, "pace.daily_window", [ws, we])
            for field, key in (("daily_minutes", "quota.daily_minutes"),
                               ("daily_tasks", "quota.daily_tasks")):
                if field in body:
                    v = body[field]
                    if not isinstance(v, int) or v < 0:
                        return {"ok": False, "error": f"{field} 需为非负整数"}
                    upsert(conn, key, v)
            if "work_mode_default" in body:
                if body["work_mode_default"] not in ("owner", "reference", "ai"):
                    return {"ok": False, "error": "作业策略仅 owner/reference/ai"}
                upsert(conn, "quiz.work_mode_default", body["work_mode_default"])
            # 守护与节级策略（2026-10-27）：全部配置项 + 范围钳制，与代码里的默认行为一致。
            # 钳制而非拒绝越界以外的值：这里直接拒绝，让用户当场看到写错，而不是默默被改。
            int_rules = {
                "auto_max_tries": ("auto.max_tries", 0, 10),
                "auto_retry_minutes": ("auto.retry_minutes", 1, 240),
                "auto_retry_jitter_pct": ("auto.retry_jitter_pct", 0, 100),
                "auto_retry_max_minutes": ("auto.retry_max_minutes", 15, 480),
                "auto_hard_tries_cap": ("auto.hard_tries_cap", 1, 20),
                "auto_confirm_seconds": ("auto.confirm_seconds", 15, 300),
                "learn_enter_retries": ("learn.enter_retries", 1, 5),
                "learn_max_skip_chapters": ("learn.max_skip_chapters", 1, 10),
                "learn_non_video_streak": ("learn.non_video_streak", 1, 20),
                "learn_partial_retries": ("learn.partial_retries", 0, 3),
                "learn_partial_retry_ratio_pct": ("learn.partial_retry_ratio_pct", 30, 95),
                "learn_login_budget_per_day": ("learn.login_budget_per_day", 1, 5),
            }
            for field, (key, lo, hi) in int_rules.items():
                if field not in body:
                    continue
                val = body[field]
                if isinstance(val, bool) or not isinstance(val, int):
                    return {"ok": False, "error": f"{field} 需为整数"}
                if not (lo <= val <= hi):
                    return {"ok": False, "error": f"{field} 需在 {lo}~{hi} 之间"}
                upsert(conn, key, val)
            if "learn_non_video_marks" in body:
                marks = str(body["learn_non_video_marks"]).strip()
                upsert(conn, "learn.non_video_marks", marks)  # 空串=回落内置默认
            for field, key in (("llm_base_url", "llm.base_url"),
                               ("llm_model", "llm.model")):
                if field in body and str(body[field]).strip():
                    upsert(conn, key, str(body[field]).strip())
            conn.commit()
        finally:
            conn.close()
        return {"ok": True}

    @app.post("/api/accounts")
    def add_account_api(body: dict):
        """添加账号（凭据只写不读；面板仅本机监听）。"""
        from ..core import accounts as acc
        from ..core.crypto import master_key

        label, user, pw = body.get("label"), body.get("username"), body.get("password")
        if not (label and user and pw):
            return {"ok": False, "error": "备注/账号/密码均必填"}
        conn = db()
        try:
            aid = acc.add_account(
                conn, master_key(settings.data_dir), label, user, pw,
                priority=float(body.get("priority") or 1.0),
            )
            return {"ok": True, "id": aid}
        finally:
            conn.close()

    @app.put("/api/accounts/{account_id}")
    def update_account_api(account_id: int, body: dict):
        from ..core import accounts as acc

        if not acc.find_account(conn := db(), str(account_id)):
            conn.close()
            return {"ok": False, "error": "账号不存在"}
        try:
            cur = acc.find_account(conn, str(account_id))
            fields: dict = {}
            for k in ("label", "note", "valid_until", "window_start", "window_end",
                      "proxy", "start_at", "work_mode"):
                if k in body:
                    v = body[k]
                    if k == "work_mode" and v not in (None, "", "ai", "owner", "reference"):
                        return {"ok": False, "error": "作业策略仅 ai/owner/reference"}
                    fields[k] = v or None  # 空串→None（跟随全局/清除覆盖）
            # 学习窗口必须成对：core.effective_pace 只在两个都填时覆盖全局，
            # 只填一侧会**静默**回退全局（2026-10-25 用户反馈"以为生效了"）→ 直接拦。
            ws = fields.get("window_start", cur["window_start"])
            we = fields.get("window_end", cur["window_end"])
            if bool(ws) != bool(we):
                bad = "开始" if not ws else "结束"
                return {"ok": False,
                        "error": f"学习窗口需同时填写开始与结束：{bad}时间缺失"
                                 "（都留空=跟随全局设置）"}
            for k in ("daily_max_minutes", "daily_max_tasks"):
                if k in body:
                    v = body[k]
                    if v is not None and (not isinstance(v, int) or v < 0):
                        return {"ok": False, "error": f"{k} 需为非负整数"}
                    fields[k] = v
            if "priority" in body:
                fields["priority"] = float(body["priority"] or 1.0)
            if "status" in body:
                if body["status"] not in ("active", "paused"):
                    return {"ok": False, "error": "status 仅 active/paused"}
                conn.execute("UPDATE accounts SET status=? WHERE id=?", (body["status"], account_id))
            if fields:
                acc.set_overrides(conn, str(account_id), **fields)
            conn.commit()
            return {"ok": True}
        finally:
            conn.close()

    @app.delete("/api/accounts/{account_id}")
    def delete_account_api(account_id: int):
        from ..core import accounts as acc

        conn = db()
        try:
            ok = acc.remove_account(conn, str(account_id))
            return {"ok": ok} if ok else {"ok": False, "error": "账号不存在"}
        finally:
            conn.close()

    @app.post("/api/accounts/{account_id}/renew")
    def renew_account_api(account_id: int, body: dict):
        from ..core import accounts as acc

        conn = db()
        try:
            days = acc.renew_account(conn, str(account_id), days=int(body.get("days") or 30))
            return ({"ok": True, "days_left": days} if days is not None
                    else {"ok": False, "error": "账号不存在"})
        finally:
            conn.close()

    @app.get("/api/autoplan")
    def autoplan():
        """auto 守护的今日计划（守护分配时写入 app_config；未运行时为空）。"""
        conn = db()
        try:
            row = conn.execute(
                "SELECT value FROM app_config WHERE key='auto.plan'"
            ).fetchone()
            if not row:
                return {"date": None, "times": {}}
            import json as _json

            plan = _json.loads(row[0])
            if plan.get("date") != time.strftime("%Y-%m-%d"):
                return {"date": None, "times": {}}
            return plan
        finally:
            conn.close()

    def _log_event(account_id: int, event: str, detail: str, level: str = "INFO") -> None:
        from ..log import get_logger

        get_logger("panel").info("[账号#%s] %s｜%s", account_id, event, detail)
        conn = db()
        try:
            conn.execute(
                "INSERT INTO run_logs(ts, level, event, detail, account_id)"
                " VALUES(?,?,?,?,?)",
                (time.strftime("%Y-%m-%dT%H:%M:%S"), level, event, detail, account_id),
            )
            conn.commit()
        finally:
            conn.close()

    # ---------- 学习安排（统一排课，开跑时间仍按各账号定时/窗口错峰） ----------

    @app.get("/api/plans")
    def plans_api():
        from ..core import plans as planmod

        conn = db()
        try:
            return {"ok": True, "plans": planmod.list_plans(conn)}
        finally:
            conn.close()

    @app.post("/api/plans")
    def plans_create_api(body: dict):
        from ..core import plans as planmod

        conn = db()
        try:
            r = planmod.create_plan(conn, str(body.get("name") or ""),
                                    str(body.get("course_name") or ""),
                                    [int(a) for a in (body.get("account_ids") or [])],
                                    enabled=bool(body.get("enabled", False)),
                                    course_pid=body.get("course_pid"))
            if r.get("ok"):
                _log_event(0, "plan-create",
                           f"{body.get('name')}｜{body.get('course_name')}｜"
                           f"{len(body.get('account_ids') or [])} 个账号")
            return r
        finally:
            conn.close()

    @app.post("/api/plans/{plan_id}/toggle")
    def plans_toggle_api(plan_id: int, body: dict):
        from ..core import plans as planmod

        conn = db()
        try:
            enabled = bool(body.get("enabled"))
            r = planmod.set_enabled(conn, plan_id, enabled)
            if r.get("ok"):
                _log_event(0, "plan-toggle", f"#{plan_id}→{'启用' if enabled else '停用'}")
            return r
        finally:
            conn.close()

    @app.post("/api/plans/{plan_id}/update")
    def plans_update_api(plan_id: int, body: dict):
        """编辑已建学习安排：改名 / 换课程 / 增删账号（None = 不变）。"""
        from ..core import plans as planmod

        ids = body.get("account_ids")
        conn = db()
        try:
            r = planmod.update_plan(
                conn, plan_id,
                name=None if body.get("name") is None else str(body.get("name")),
                course_name=None if body.get("course_name") is None
                else str(body.get("course_name")),
                course_pid=None if body.get("course_pid") is None
                else str(body.get("course_pid")),
                account_ids=None if ids is None else [int(a) for a in ids],
                enabled=None if body.get("enabled") is None else bool(body.get("enabled")))
            if r.get("ok"):
                _log_event(0, "plan-update",
                           f"#{plan_id}｜{body.get('name')}｜{body.get('course_name')}｜"
                           f"{len(ids) if ids is not None else '账号未改'} 个账号")
            return r
        finally:
            conn.close()

    @app.delete("/api/plans/{plan_id}")
    def plans_delete_api(plan_id: int):
        from ..core import plans as planmod

        conn = db()
        try:
            planmod.delete_plan(conn, plan_id)
            _log_event(0, "plan-delete", f"#{plan_id}")
            return {"ok": True}
        finally:
            conn.close()

    # ---------- 统计（数据可视页：按年级/专业/学期/账号聚合） ----------

    @app.get("/api/stats")
    def stats_api(by: str = "grade", days: int = 7, q: str = ""):
        from ..core import stats as statsmod

        conn = db()
        try:
            return statsmod.aggregate(conn, by=by, days=days, q=q)
        finally:
            conn.close()

    @app.get("/api/profiles")
    def profiles_api():
        from ..core import stats as statsmod

        conn = db()
        try:
            return {"ok": True, "profiles": statsmod.list_profiles(conn)}
        finally:
            conn.close()

    @app.post("/api/profiles")
    def profiles_set_api(body: dict):
        from ..core import stats as statsmod

        conn = db()
        try:
            r = statsmod.set_profile(conn, int(body.get("account_id") or 0),
                                     name=str(body.get("name") or ""),
                                     student_no=str(body.get("student_no") or ""),
                                     grade=str(body.get("grade") or ""),
                                     major=str(body.get("major") or ""),
                                     term=str(body.get("term") or ""),
                                     school=str(body.get("school") or ""),
                                     dept=str(body.get("dept") or ""),
                                     cls=str(body.get("cls") or ""),
                                     # null = 前端确认“一个字没改”，保留原来源
                                     source=body.get("source", "manual"))
            return r
        finally:
            conn.close()

    @app.post("/api/accounts/{account_id}/profile_from_platform")
    def profile_from_platform(account_id: int):
        """从平台读一次画像（姓名/学号/学校/院系），**只补空不覆盖手填**。

        走 `adapters/chaoxing/profile_read.py`（平台细节只在那）；一次访问，
        复用页面自身的 stu-base-info 响应。抓不到就如实报错，绝不猜。
        """
        from ..adapters.chaoxing.profile_read import read_profile
        from ..core import stats as statsmod

        state_path = settings.data_dir / "accounts" / str(account_id) / "state.json"
        got = read_profile(state_path, settings.platform["space_url"])
        conn = db()
        try:
            if not got.get("ok"):
                return {**got, "profile": statsmod.get_profile(conn, account_id)}
            merged = statsmod.merge_platform_profile(conn, account_id, got)
            # 学校/院系/班级平台给了也一并留档（面板只展示，不进统计维度）
            note = "，姓名=昵称已忽略" if "name" in (merged.get("nickname_only") or []) else ""
            _log_event(account_id, "profile-read",
                       f"平台画像：字段 {len(got.get('fields') or [])} 个，回填 "
                       f"{merged.get('filled') or '无'}，保留手填 {merged.get('skipped') or '无'}{note}")
            return {**got, "merged": merged, "profile": statsmod.get_profile(conn, account_id)}
        finally:
            conn.close()

    # 无头自动登录失败的转人工原因（面板据此提示"为什么不行 + 怎么办"）
    _LOGIN_REASON = {
        "captcha": ("平台要求验证码（无头浏览器无法自动通过）",
                    "请改用手动登录：会打开一个可见浏览器窗口，你在窗口中过验证码/扫码"),
        "wrong-password": ("账号或密码错误（平台返回密码错误）",
                           "请在『✎ 管理』核对/重存账号密码，或先手动登录一次确认"),
        "budget": ("今日无头自动登录尝试已达上限（2 次/天，安全底线）",
                   "请改用手动登录，或明天再试"),
        "no-cred": ("该账号未保存密码，无法无头自动登录",
                    "请改用手动登录（或用『✎ 管理』重存账号密码后再试自动）"),
        "unknown": ("自动登录异常或超时（可能网络/风控/登录页选择器变更）",
                    "请改用手动登录；详情见『📜 日志』页"),
    }

    @app.post("/api/accounts/{account_id}/login")
    def account_login_api(account_id: int, body: dict):
        """账号登录：默认先试无头自动（用已存密码），失败可转手动（可见浏览器）。

        - manual=false：headless 代填登录；返回 ok，或"失败原因 + 建议转人工"。
        - manual=true：拉起子进程 `xxt login --account N`（有头浏览器），人工扫码/过验证码。
        成功后会话落 accounts/<id>/state.json；面板再点『⬇ 平台读取』即可补画像。
        """
        manual = bool(body.get("manual"))
        conn = db()
        try:
            # 凭据列不在公开列里，单独取（绝不外泄，只在本函数内用于自动登录）
            creds = conn.execute(
                "SELECT username_enc, password_enc FROM accounts WHERE id=?", (account_id,)
            ).fetchone()
        finally:
            conn.close()
        if creds is None:
            return {"ok": False, "error": "账号不存在"}

        if manual:
            proc = _spawn(str(settings.root), "login", "--account", str(account_id))
            _log_event(account_id, "login-manual", "已打开浏览器窗口等待人工登录")
            return {"ok": True, "started": True, "pid": proc.pid}

        if not creds["username_enc"]:
            _log_event(account_id, "login-auto", "无凭据，转人工")
            reason, hint = _LOGIN_REASON["no-cred"]
            return {"ok": False, "outcome": "no-cred", "error": reason, "hint": hint}
        from ..core.accounts import account_state_path
        from ..core.crypto import master_key
        from ..session.relogin import auto_relogin, login_budget_per_day

        conn = db()
        try:
            outcome = auto_relogin(
                conn, master_key(settings.data_dir), settings.platform["login_url"],
                account_state_path(settings, account_id), account_id,
                creds["username_enc"], creds["password_enc"],
                per_day=login_budget_per_day(settings))
        finally:
            conn.close()
        if outcome == "ok":
            _log_event(account_id, "login-auto", "无头自动登录成功")
            return {"ok": True}
        reason, hint = _LOGIN_REASON.get(outcome, (f"自动登录未成功（{outcome}）", "请改用手动登录"))
        return {"ok": False, "outcome": outcome, "error": reason, "hint": hint}

    @app.post("/api/study/start")
    def study_start(body: dict):
        """为账号启动一门课的学习会话（子进程 xxt mlearn --accounts <id>）。"""
        import pathlib

        account_id, course = body.get("account_id"), body.get("course")
        if not (account_id and course):
            return {"ok": False, "error": "缺少 account_id / course"}
        job = _JOBS.get(account_id)
        if job and job["proc"].poll() is None:
            return {"ok": False, "error": f"该账号已有进行中的会话（{job['course']}）"}
        if account_id in _marker_jobs(settings):
            return {"ok": False, "error": "该账号已有进行中的会话（定时/外部启动）"}
        conn = db()
        try:
            n = conn.execute(
                "SELECT COUNT(*) AS c FROM courses WHERE account_id=?", (account_id,)
            ).fetchone()["c"]
            # 缺课保护（2026-10-26）：该号没抓过这门课就直接拒——spawn 了也会 `[FAIL] 退出`，
            # 但调用方（定时守护）会收到 ok=True 并记为“今日已触发”，当日机会白白消耗。
            from ..core import plans as planmod  # 本地查库，零平台访问

            cpid0 = str(body.get("course_pid") or "").strip()
            if n and not planmod.account_has_course(conn, account_id, pid=cpid0, name=course):
                return {"ok": False,
                        "error": f"该账号没有这门课（{('课程ID ' + cpid0) if cpid0 else '课名'}），已跳过"}
        finally:
            conn.close()
        if not n:
            return {"ok": False, "error": "该账号还没有课程数据，先为其抓取课程（xxt courses --account）"}
        root = pathlib.Path(settings.root)
        # 学习安排带平台课程ID时透传（运行按 pid 精确匹配，避免同名歧义）
        cpid = str(body.get("course_pid") or "").strip()
        argv = ["mlearn", course]
        if cpid:
            argv += ["--course-pid", cpid]
        argv += ["--accounts", str(account_id)]
        proc = _spawn(str(root), *argv)
        # 立刻写运行标记（子进程自己写前有 1~2 秒空窗，期间再点一次就会双开）。
        # 标记内容与子进程一致：pid=子进程 pid，退出时由子进程清理。
        from ..core.runmark import write_marker

        write_marker(settings.data_dir, account_id, course, pid=proc.pid)
        _JOBS[account_id] = {"proc": proc, "course": course, "since": time.strftime("%H:%M")}
        _log_event(account_id, "session-start", f"{course}｜{body.get('source', '手动')}启动")
        return {"ok": True}

    @app.post("/api/study/stop")
    def study_stop(body: dict):
        account_id = body.get("account_id")
        job = _JOBS.get(account_id)
        if job and job["proc"].poll() is None:
            job["proc"].terminate()
            _JOBS.pop(account_id, None)
            _log_event(account_id, "session-stop", f"{job['course']}｜手动停止（进度保留）")
            return {"ok": True}
        ext = _marker_jobs(settings).get(account_id)  # 定时/外部启动的会话
        if ext:
            mpath = settings.data_dir / "run" / f"{account_id}.json"
            # 强杀前再核一次 pid 归属：期间可能已自行退出、pid 又被别的进程占用
            owned = _pid_alive(int(ext["pid"]), mpath)
            if owned:
                subprocess.run(["taskkill", "/PID", str(ext["pid"]), "/F"],
                               capture_output=True)
            mpath.unlink(missing_ok=True)
            _log_event(account_id, "session-stop",
                       f"{ext['course']}｜手动停止（进度保留）" if owned
                       else f"{ext['course']}｜标记 pid 已变（可能已退出），仅清标记")
            return {"ok": True}
        return {"ok": False, "error": "该账号没有进行中的会话"}

    @app.post("/api/auto/start")
    def auto_start(request: Request, source: str = "api"):
        """启动定时调度守护；PID 落 data/run/auto.json（面板重启后仍可见、可停）。

        `source=ui` 由前端按钮传入（其余记 API + UA），启动/拒绝都入库 → 日志页能答
        “这个守护是谁开的”（2026-10-27 加固）。
        """
        import json as _json

        global _AUTO
        who = _auto_who(request, source)
        marker = settings.data_dir / "run" / "auto.json"
        if marker.exists():  # 旧守护还活着（如面板重启前启动的）——先确认
            try:
                old = _json.loads(marker.read_text(encoding="utf-8"))
                if _pid_alive(int(old["pid"]), marker):
                    _log_event(0, "auto-request",
                               f"启动请求被拒（已在运行 pid={old['pid']}）｜来源={who}",
                               "WARNING")  # 被拒本身就该黄字，别看来源
                    return {"ok": False, "error": "定时调度已在运行"}
            except Exception:
                pass
        proc = _spawn(str(settings.root), "auto")
        since = time.strftime("%H:%M")
        _AUTO = {"proc": proc, "since": since}
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(_json.dumps({"pid": proc.pid, "since": since}),
                          encoding="utf-8")
        _log_event(0, "auto-request", f"启动请求已受理（pid={proc.pid}）｜来源={who}",
                   _who_level(who))
        return {"ok": True}

    @app.post("/api/auto/stop")
    def auto_stop(request: Request, source: str = "api"):
        """停止定时调度（含面板重启前的旧守护）；学习会话不被动。"""
        global _AUTO
        who = _auto_who(request, source)
        marker = settings.data_dir / "run" / "auto.json"
        stopped = False
        if _AUTO and _AUTO["proc"].poll() is None:
            _AUTO["proc"].terminate()
            stopped = True
        if marker.exists():  # 孤儿守护（面板重启后失联的）
            try:
                import json as _json

                old = _json.loads(marker.read_text(encoding="utf-8"))
                old_pid = int(old["pid"])
                if _pid_alive(old_pid, marker):
                    subprocess.run(["taskkill", "/PID", str(old_pid), "/F"],
                                   capture_output=True)
                    stopped = True
                elif _pid_alive(old_pid):
                    # pid 被别的进程复用：绝不强杀，只清标记并留警告（2026-10-27）
                    _log_event(0, "auto-stop",
                               f"标记 pid={old_pid} 存活但不像守护进程"
                               "（pid 可能已被复用），未强杀，仅清标记", "WARNING")
            except Exception:
                pass
            marker.unlink(missing_ok=True)
        _AUTO = None
        if stopped:  # 面板侧停止也要在「📜 日志」留一笔（守护被强杀时自己写不了）
            _log_event(0, "auto-stop",
                       f"面板停止定时调度（来源={who}；已在跑的学习会话不受影响）",
                       _who_level(who))
        return {"ok": True} if stopped else {"ok": False, "error": "定时调度未在运行"}

    @app.get("/api/study/status")
    def study_status():
        out = {}
        # 快照遍历：本接口被前端每 1~2 秒轮询，同时在跑的学习/作业线程会增删 _JOBS
        # → 直接 `for ... in _JOBS.items()` 会抛 RuntimeError: dictionary changed size
        # during iteration（2026-10-27 实测，ASGI 500）。
        for aid, job in list(_JOBS.items()):
            alive = job["proc"].poll() is None
            out[str(aid)] = {"course": job["course"], "running": alive,
                             "since": job["since"]}
            # 只清“自己刚看到的那条”：期间可能已被新会话替换，删了界面就丢会话
            if not alive and _JOBS.get(aid) is job:
                _JOBS.pop(aid, None)
        for aid, info in _marker_jobs(settings).items():  # 定时/外部（含面板重启前的）
            out.setdefault(str(aid), {"course": info["course"], "running": True,
                                      "since": info["since"], "external": True})
        # 守护状态以磁盘标记为准（面板重启后仍真实反映，杜绝"看不见的孤儿守护"）
        auto_running, auto_since = False, None
        marker = settings.data_dir / "run" / "auto.json"
        if marker.exists():
            try:
                import json as _json

                info = _json.loads(marker.read_text(encoding="utf-8"))
                if _pid_alive(int(info["pid"])):
                    auto_running, auto_since = True, info.get("since")
                else:
                    marker.unlink(missing_ok=True)
            except Exception:
                pass
        if not auto_running and _AUTO and _AUTO["proc"].poll() is None:
            auto_running, auto_since = True, _AUTO["since"]
        if not auto_running:
            globals()["_AUTO"] = None
        return {"jobs": out, "auto": {"running": auto_running, "since": auto_since}}

    @app.post("/api/accounts/{account_id}/work")
    def work_api(account_id: int, body: dict):
        """作业作答（面板入口，策略优先级：账号显式 > 全局默认 > owner）。
        ai → 后台执行 xxt work（headless 填入，结果看日志）；
        reference → 返回 AI 参考答案（只解析题目+AI 作答，不碰作业页表单）；
        owner → 仅返回操作指引（号主自理）。"""
        import json as _json

        conn = db()
        try:
            row = conn.execute(
                "SELECT work_mode, label FROM accounts WHERE id=?", (account_id,)
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return {"ok": False, "error": "账号不存在"}
        # 策略三层：账号显式（work_mode 非空）> 全局默认（quiz.work_mode_default）> owner
        global_default = Settings(root=settings.root).quiz.get("work_mode_default", "owner")
        mode = row["work_mode"] or global_default or "owner"
        chapter = body.get("chapter") or "2.12"
        if mode == "owner":
            return {"ok": True, "action": "owner",
                    "message": f"该账号作业策略=号主自理：请通知 {row['label']} 自己完成作业；"
                               f"如需 AI 参考答案，可在✎管理中把作业策略改为 reference"}
        if mode == "ai" or body.get("action") == "fill":
            job = _JOBS.get(account_id)  # 一次取值：避免两次查表之间被轮询线程 pop 掉
            if job and job["proc"].poll() is None:
                return {"ok": False, "error": "该账号已有进行中的会话/作业"}
            proc = _spawn(str(settings.root), "work", str(account_id), chapter, "--force")
            _JOBS[account_id] = {"proc": proc, "course": f"作业{chapter}", "since": time.strftime("%H:%M")}
            mode_name = "AI 回填" if body.get("action") == "fill" else "AI 代答"
            _log_event(account_id, "work-start", f"{chapter}｜{mode_name}")
            return {"ok": True, "action": "ai",
                    "message": "AI 已开始填入并暂存（不提交）——约 1 分钟后到平台作业页核对，满意再提交"}
        # reference：只出参考答案（解析+AI，不填表单）
        try:
            from playwright.sync_api import sync_playwright
            from ..adapters.chaoxing.pagesync import find_frame_with, open_chapter_frame
            from ..adapters.chaoxing.work import parse_work_questions, _page_font_map
            from ..ai.llm import LLMClient
            from ..ai.quiz import build_messages

            state = settings.data_dir / "accounts" / str(account_id) / "state.json"
            conn2 = db()
            try:
                course = conn2.execute(
                    "SELECT * FROM courses WHERE account_id=? ORDER BY id LIMIT 1",
                    (account_id,)).fetchone()
            finally:
                conn2.close()
            if course is None:
                return {"ok": False, "error": "账号无课程数据"}
            cache_key = f"{account_id}|{course['id']}|{chapter}"
            wq = db()
            try:
                ch_row = wq.execute(
                    "SELECT name FROM chapters WHERE course_id=? AND name LIKE ? LIMIT 1",
                    (course["id"], f"%{chapter}%")).fetchone()
                chapter_full = ch_row["name"] if ch_row else chapter
            finally:
                wq.close()
            import json as _json5

            # 题目缓存优先（题目静态：解析+字体解码只付一次成本，之后全程无浏览器）
            qs: list = []
            if not body.get("force_questions"):
                wq = db()
                try:
                    rows = wq.execute(
                        "SELECT idx, qid, qtype, qtype_cn, stem, options FROM work_questions "
                        "WHERE cache_key=? ORDER BY idx", (cache_key,)).fetchall()
                finally:
                    wq.close()
                qs = [{"qid": r["qid"], "qtype": r["qtype"], "qtype_cn": r["qtype_cn"],
                       "stem": r["stem"],
                       "options": _json5.loads(r["options"] or "[]")} for r in rows]
            answers = []
            if qs:  # 缓存命中：不开浏览器
                if body.get("questions_only"):
                    return {"ok": True, "action": "questions",
                            "questions": [{"idx": i, "stem": q["stem"][:120],
                                           "qtype_cn": q["qtype_cn"]}
                                          for i, q in enumerate(qs)]}
            else:  # 未缓存：浏览器解析一次 → 存库（后续永不再开浏览器取题）
                with sync_playwright() as p:
                    from ..core.stealth import LAUNCH_ARGS, hardened_context
                    browser = p.chromium.launch(headless=True, args=LAUNCH_ARGS)

                    context = hardened_context(browser, storage_state=str(state))
                    page = context.new_page()
                    try:
                        tree = open_chapter_frame(page, course["url"])
                        c2 = db()
                        try:
                            kid_row = c2.execute(
                                "SELECT platform_chapter_id FROM chapters WHERE course_id=? "
                                "AND name LIKE ? LIMIT 1",
                                (course["id"], f"%{chapter}%")).fetchone()
                        finally:
                            c2.close()
                        if kid_row is None:
                            return {"ok": False, "error": f"未找到章节 {chapter}"}
                        tree.locator(f"#cur{kid_row['platform_chapter_id']}").first.click(timeout=15_000)
                        wf = find_frame_with(page, "div.singleQuesId", timeout_s=25)
                        if wf is None:
                            return {"ok": False, "error": "该章节不是作业任务点"}
                        qs = parse_work_questions(wf.content(), dynamic_map=_page_font_map(wf))
                        context.storage_state(path=str(state))
                        wq = db()
                        try:
                            wq.execute("DELETE FROM work_questions WHERE cache_key=?", (cache_key,))
                            for i, q in enumerate(qs):
                                wq.execute(
                                    "INSERT INTO work_questions(cache_key, idx, qid, qtype, qtype_cn,"
                                    " stem, options, updated_at) VALUES(?,?,?,?,?,?,?,datetime('now'))",
                                    (cache_key, i, q.get("qid", ""), q["qtype"], q["qtype_cn"],
                                     q["stem"], _json5.dumps(q.get("options") or [], ensure_ascii=False)))
                            wq.commit()
                        finally:
                            wq.close()
                    finally:
                        browser.close()
                if body.get("questions_only"):
                    return {"ok": True, "action": "questions",
                            "questions": [{"idx": i, "stem": q["stem"][:120],
                                           "qtype_cn": q["qtype_cn"]}
                                          for i, q in enumerate(qs)]}

            # ===== 答案生成（题目已在手，全程无浏览器）=====
            llm = LLMClient(settings.llm, data_dir=settings.data_dir)
            from ..ai.quiz import (bank_lookup, bank_record, build_messages,
                                   parse_llm_json, question_hash)
            extra = str(body.get("extra_requirements") or "").strip()
            only_idx = body.get("question_idx")
            wconn = db()
            bank_hit: dict[int, str] = {}

            def answer_one(idx_q):
                """纯网络函数（线程安全）：题库已由主线程预查，这里只调 LLM。"""
                idx, q, need_llm = idx_q
                if not need_llm:
                    return idx, None, "bank", None
                try:
                    raw = llm.chat(build_messages(
                        q["stem"], q["qtype"], q["options"],
                        course=course["name"], chapter=chapter,
                        extra_requirements=extra))
                    from ..ai.quiz import clean_markdown

                    try:
                        parsed = parse_llm_json(raw)
                        ans = clean_markdown("；".join(parsed["answer"]))
                    except Exception:
                        ans = clean_markdown(raw.strip()[:500])
                    return idx, ans, "llm", None
                except Exception as exc:
                    return idx, "", "err", str(exc)

            # ① 主线程：题库预查（命中=不调 LLM）
            need = []
            for idx, q in enumerate(qs):
                h = question_hash(q["stem"], q["options"])
                hit = None
                if only_idx is None and not body.get("force"):
                    hit = bank_lookup(wconn, h, account_id=account_id)
                if hit and hit.get("answer"):
                    from ..ai.quiz import clean_markdown as _cm

                    bank_hit[idx] = _cm("；".join(hit["answer"]))
                    need.append((idx, q, False))
                elif only_idx is not None and idx != int(only_idx):
                    need.append((idx, q, False))  # 非刷新目标跳过
                else:
                    need.append((idx, q, True))
            # ② LLM 并行（多题时提速）
            results: dict[int, tuple] = {}
            llm_tasks = [t for t in need if t[2]]
            if len(llm_tasks) > 1:
                from concurrent.futures import ThreadPoolExecutor

                with ThreadPoolExecutor(max_workers=min(4, len(llm_tasks))) as ex:
                    for r in ex.map(answer_one, llm_tasks):
                        results[r[0]] = r
            else:
                for t in llm_tasks:
                    r = answer_one(t)
                    results[r[0]] = r
            # ③ 主线程：结果回写题库 + 组装
            for idx, q, do_llm in need:
                if not do_llm:
                    results[idx] = (idx, bank_hit.get(idx, ""), "bank", None)
                    continue
                _, ans, src, err = results.get(idx, (idx, "", "err", "未生成"))
                if ans:
                    bank_record(wconn, question_hash(q["stem"], q["options"]),
                                q["stem"], q["qtype"], q["options"],
                                account_id=account_id, answer=[ans])
            for idx, q in enumerate(qs):
                _, ans, src, err = results.get(idx, (idx, "", "err", "未生成"))
                wconn.execute(
                    "INSERT INTO run_logs(ts, level, event, detail, account_id)"
                    " VALUES(?,?,?,?,?)",
                    (time.strftime("%Y-%m-%dT%H:%M:%S"),
                     "WARNING" if err else "INFO", "work-answer",
                     f"{chapter_full}｜{q['qtype_cn']}｜{q['stem'][:30]}…｜参考答案｜{src}", account_id))
                wconn.commit()
                answers.append({"idx": idx, "stem": q["stem"][:80], "source": src,
                                "answer": f"（AI 调用失败：{err}）" if err else ans})
            wconn.close()
            return {"ok": True, "action": "reference", "answers": answers}
        except Exception as exc:
            return {"ok": False, "error": repr(exc)}

    @app.post("/api/accounts/{account_id}/work/review")
    def work_review_api(account_id: int, body: dict):
        """判题回读（面板化 --review）：读已提交作业的每题得分 → 题库转正/清空。"""
        from playwright.sync_api import sync_playwright

        # ⚠️ 2026-10-25 修复：原为 `.adapters...`（少一个点）→ 实际解析成
        # xxt.panel.adapters（不存在），点"✔ 读判分"会直接 ImportError 500。
        from ..adapters.chaoxing.pagesync import find_frame_with, open_chapter_frame
        from ..adapters.chaoxing.work import review_work
        from ..core.accounts import account_state_path

        chapter = body.get("chapter") or "2.12"
        state = account_state_path(settings, account_id)
        if not state.exists():
            return {"ok": False, "error": "该账号未登录"}
        conn = db()
        try:
            course = conn.execute(
                "SELECT * FROM courses WHERE account_id=? ORDER BY id LIMIT 1",
                (account_id,)).fetchone()
            ch = conn.execute(
                "SELECT * FROM chapters WHERE course_id=? AND name LIKE ? LIMIT 1",
                (course["id"], f"%{chapter}%")).fetchone() if course else None
        finally:
            conn.close()
        if course is None or ch is None:
            return {"ok": False, "error": "未找到课程/章节"}
        try:
            with sync_playwright() as p:
                from ..core.stealth import LAUNCH_ARGS, hardened_context
                browser = p.chromium.launch(headless=True, args=LAUNCH_ARGS)
                context = hardened_context(browser, storage_state=str(state))
                page = context.new_page()
                try:
                    tree = open_chapter_frame(page, course["url"])
                    tree.locator(f"#cur{ch['platform_chapter_id']}").first.click(timeout=15_000)
                    wf = find_frame_with(page, "div.singleQuesId", timeout_s=25)
                    if wf is None:
                        return {"ok": False, "error": "未找到作业页（可能尚未提交）"}
                    results = review_work(wf, conn, account_id)
                    context.storage_state(path=str(state))
                finally:
                    browser.close()
            return {"ok": True, "results": results}
        except Exception as exc:
            return {"ok": False, "error": repr(exc)}

    @app.get("/", response_class=HTMLResponse)
    def index():
        # no-store：面板迭代频繁，避免浏览器缓存旧 HTML 导致"按钮消失"类困惑
        return HTMLResponse(_PAGE, headers={"Cache-Control": "no-store"})

    return app
