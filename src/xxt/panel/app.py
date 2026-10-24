"""FastAPI 应用工厂：只读视图 + 学习会话控制；凭据永不出现在任何响应中。"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
import time

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from ..config import Settings
from ..core.accounts import list_accounts
from ..store.db import connect

# 学习会话进程表：account_id → {proc, course}（面板重启后进程仍活但失联，属已知限制）
_JOBS: dict[int, dict] = {}
_AUTO: dict | None = None  # 定时调度守护进程（面板一键开关）


def _spawn(root_str: str, *cmd: str):
    import sys as _sys

    if getattr(_sys, "frozen", False):  # exe 模式：自身即 CLI 入口
        argv = [_sys.executable, *cmd]
    else:
        argv = [_sys.executable, "-m", "xxt.cli", *cmd]
    return subprocess.Popen(argv, cwd=root_str,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _pid_alive(pid: int) -> bool:
    try:
        import ctypes

        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not h:
            return False
        ctypes.windll.kernel32.CloseHandle(h)
        return True
    except Exception:
        return True


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
            if _pid_alive(int(info["pid"])):
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
.card .head{display:flex;align-items:flex-start;gap:.65rem;margin-bottom:.45rem;flex-wrap:wrap}
.avatar{width:38px;height:38px;border-radius:12px;background:var(--grad);color:#fff;
display:flex;align-items:center;justify-content:center;font-size:1.1rem;font-weight:700;flex:none;
box-shadow:0 3px 8px rgba(70,80,220,.3)}
.head .names{min-width:0;flex:1 1 150px}
.who{font-size:1.05rem;font-weight:800;line-height:1.25;word-break:break-all}
.sub{font-size:.72rem;color:var(--muted);margin-top:.1rem}
.head .ops{display:flex;gap:.35rem;flex:none;align-items:center;flex-wrap:wrap;margin-left:auto}
.minibtn{background:var(--bg);border:1px solid var(--line);border-radius:9px;padding:.22rem .6rem;
font-size:.75rem;cursor:pointer;transition:.15s;color:var(--ink)}
.minibtn:hover{border-color:var(--accent);color:var(--accent)}
.minibtn.danger:hover{border-color:var(--bad);color:var(--bad)}
.badge{font-size:.68rem;border-radius:99px;padding:.14rem .6rem;font-weight:700;white-space:nowrap}
.b-ok{background:#e3f6ec;color:var(--ok)}.b-warn{background:#fdf2df;color:var(--warn)}
.b-info{background:#e8edff;color:#4353d8}
.pulse{animation:pulse 1.6s infinite}@keyframes pulse{50%{opacity:.55}}
.chips{display:flex;gap:.3rem;flex-wrap:wrap;margin:.15rem 0 .6rem}
.chip{font-size:.68rem;color:var(--muted);background:var(--bg);border:1px solid var(--line);
border-radius:7px;padding:.12rem .5rem}
.course{margin:.5rem 0;padding:.45rem .55rem;border-radius:11px;border:1px solid transparent;transition:.15s}
.course:hover{background:var(--bg)}
.course.hit{background:#f0f3ff;border-color:#cdd8ff}
.course .nm{display:block;font-size:.84rem;line-height:1.35;word-break:break-all;
display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.course .t2{display:flex;justify-content:space-between;align-items:center;margin-top:3px;gap:.5rem}
.nums{font-size:.78rem;color:var(--muted);font-weight:700;flex:none}
.nums b{color:var(--ink);font-size:.82rem}
.play{border:1px solid #bfe5d2;background:#eef9f3;color:var(--ok);border-radius:9px;
padding:.2rem .65rem;font-size:.72rem;font-weight:700;cursor:pointer;transition:.15s}
.play:hover{background:var(--ok);color:#fff;border-color:var(--ok);
box-shadow:0 3px 10px rgba(24,160,93,.35);transform:translateY(-1px)}
.stopc{border:1px solid #bfe5d2;background:#e3f6ec;color:var(--ok);border-radius:9px;
padding:.2rem .65rem;font-size:.72rem;font-weight:700;cursor:pointer;transition:.15s}
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
dialog{border:none;border-radius:20px;padding:1.25rem 1.45rem;width:min(460px,92vw);
box-shadow:0 24px 70px rgba(20,30,70,.35)}
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
<div class="toolbar">
  <select id="psize"><option value="10">每页 10 张</option><option value="20" selected>每页 20 张</option><option value="50">每页 50 张</option></select>
  <span class="muted" id="accInfo"></span>
  <span id="synced">自动刷新中…</span>
</div>
<div class="grid" id="accounts"><div class="card muted">加载中…</div></div>
<div class="pager" id="pager"></div>
<section><h2>最近日志 <span class="muted" style="font-weight:400;font-size:.75rem">（全部动态已存档于 data/logs/xxt.log 与数据库）</span></h2>
<div id="logs" class="muted">加载中…</div>
<div style="text-align:center;margin-top:.6rem">
<button class="ghost" id="moreLogs" onclick="renderLogs(100);this.style.display='none'">展开更多（最近 100 条）</button></div></section>
<footer>XXT · 多账号网课助手 · 本地面板（127.0.0.1）· 设置三层：全局 ⚙ &gt; 账号 ✎ &gt; 文件默认</footer>
</div>

<dialog id="dlg"><h3>⚙ 全局设置</h3>
  <div class="frow"><label>弹题 AI 作答（P2 功能，计分项）</label>
    <span class="switch"><input type="checkbox" id="s_quiz"><i></i></span></div>
  <div class="frow"><label>学习窗口开始</label><input type="time" id="s_ws"></div>
  <div class="frow"><label>学习窗口结束</label><input type="time" id="s_we"></div>
  <div class="frow"><label>每日时长额度（分钟，0 不限）<small style="display:block;color:var(--muted);font-size:.72rem">按会话在场时长计（含拟人休息）——限制的是平台视角的总占用，更安全</small></label><input type="number" id="s_min" style="width:90px"></div>
  <div class="frow"><label>每日任务额度（视频数，0 不限）</label><input type="number" id="s_task" style="width:90px"></div>
  <div class="dlgbtns"><button class="ghost" onclick="this.closest('dialog').close()">取消</button>
    <button class="primary" onclick="saveSettings()">保存</button></div>
  <div class="hint">保存写入数据库全局覆盖层（对之后启动的学习会话生效；账号级覆盖仍用 xxt account set）</div>
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
  <div class="frm"><label>作业策略<small>owner=号主自理（默认，只刷视频）；reference=AI出参考答案给你抄；ai=AI代答semi填入</small></label>
    <select id="e_wm"><option value="owner">号主自理</option><option value="reference">AI 参考答案</option><option value="ai">AI 代答（semi）</option></select></div>
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

<dialog id="workDlg"><h3>📝 作业参考答案 <span id="w_ch" class="muted" style="font-size:.85rem"></span></h3>
  <div id="w_body" class="log" style="max-height:52vh;overflow:auto;font-size:.86rem">加载中…</div>
  <div class="dlgbtns"><button class="primary" onclick="this.closest('dialog').close()">知道了</button></div>
</dialog>

<script>
const esc=s=>String(s??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const badge=(t,c)=>`<span class="badge ${c}">${esc(t)}</span>`;
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
    x.hits.length||[x.a.platform_username,x.a.label,x.a.status,String(x.a.id)].some(
      v=>String(v??'').toLowerCase().includes(q)));
}
function renderAccounts(){
  const q=document.getElementById('q').value.trim();  // 高亮用原始大小写（hl 内部不区分）
  const ps=+document.getElementById('psize').value, list=filtered();
  const pages=Math.max(1,Math.ceil(list.length/ps)); PAGE=Math.min(PAGE,pages);
  const slice=list.slice((PAGE-1)*ps,PAGE*ps);
  document.getElementById('accN').textContent=ACCOUNTS.length;
  document.getElementById('accInfo').textContent=`匹配 ${list.length} 个账号 · 第 ${PAGE}/${pages} 页`;
  document.getElementById('accounts').innerHTML=slice.map(({a,hits})=>{
    const status=a.status==='active'?'b-ok':'b-warn';
    const sess=a.has_session?badge('已登录','b-info'):badge('未登录','b-warn');
    const chips=[`今日 ${a.today_videos??0} 个`,a.today_min?`今日 ${a.today_min} 分钟`:'',
                 a.work_mode==='ai'?'作业=AI代答':a.work_mode==='reference'?'作业=参考答案':'作业=号主自理',
                 a.window?`窗口 ${esc(a.window)}`:'',a.max_minutes?`时长 ${a.max_minutes}分`:'',
                 a.max_tasks?`任务 ${a.max_tasks}个`:'',a.proxy?'代理':'',
                 a.days_left===null?'':`有效期 ${a.days_left>=0?a.days_left+'天':'已过期'}`,
                 a.start_at?`定时 ${a.start_at}`:(AUTOPLAN.times[a.id]?`今日定时 ${AUTOPLAN.times[a.id]}`:'定时未设'),
                 a.renew_count?`续期${a.renew_count}次`:'',
                 a.last_expired_at?'上次失效 '+esc(a.last_expired_at.slice(5,16)):'' ]
                 .filter(Boolean).map(c=>`<span class="chip">${c}</span>`).join('');
    const mkCourse=(c)=>{
      const hit=hits.some(h=>h.name===c.name)?' hit':'';
      const run=RUNNING[a.id];
      const runningThis=run&&run.course===c.name;
      const ctl=runningThis
        ?`<button class="stopc" title="正在刷这门课，点击停止（进度保留）"
            onclick="stopGrind(${a.id})">▶ 刷课中 ${run.since} · 点击停止</button>`
        :`<button class="play" title="开始刷这门课（从上次进度继续）"
            data-c="${esc(c.name)}" onclick="startGrind(${a.id},this.dataset.c)">▶ 刷这门课</button>`;
      const q=document.getElementById('q').value.trim();
    return `<div class="course${hit}"><span class="nm">${hl(c.name,q)}</span>
      <div class="t2"><span class="nums">${c.total?Math.round(c.done/c.total*100):0}% · <b>${c.done}/${c.total}</b></span>${ctl}</div>
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
        : w.ai_answered ? badge('AI已答·待核对','b-info') : badge('未完成','b-warn');
      const btn = w.status==='done' ? '' :
        `<button class="minibtn" title="按该账号作业策略处理（✎管理可改：号主自理/参考答案/AI代答）"
          onclick="doWork(${a.id},'${esc(w.name).replace(/'/g,'&#39;')}')">📝 ${a.work_mode==='ai'?'AI代答':a.work_mode==='reference'?'参考答案':'去处理'}</button>`;
      return `<div class="course" style="border:1px dashed var(--line);border-radius:11px">
        <div class="t2"><span class="nm">📄 ${hl(w.name,q)} <span class="muted" style="font-size:.7rem">作业任务</span></span>
        <span class="val" style="display:flex;gap:.45rem;align-items:center">${st}${btn}</span></div></div>`;
    };
    const workHtml = works.length
      ? works.map(mkWork).join('')
      : (mainCourse && workRe.test(mainCourse.name) ? '' : '');
    const searching = document.getElementById('q').value.trim()!=='';
    // 仅当"课程名"命中才自动展开；搜人名/备注时保持折叠（找人不找课）
    const autoOpen = others.some(c=>hits.some(h=>h.name===c.name));
    const open = autoOpen || !!EXPANDED[a.id];
    const mainHtml = mainCourse ? mkCourse(mainCourse) : '';
    const othersHtml = others.map(mkCourse).join('');
    const doneSum = others.reduce((s,c)=>s+c.done,0);
    const courses = (mainCourse ? mainHtml : '<div class="muted" style="font-size:.8rem">暂无课程数据</div>')
      + workHtml
      + (others.length ? `<button class="morebtn" onclick="toggleMore(${a.id})">${open?'▾':'▸'} 其他 ${others.length} 门课 · 已完成 ${doneSum} 个知识点</button>
        <div class="more" style="display:${open?'':'none'}">${othersHtml}</div>` : '');
    const stopBtn=run?`<button class="minibtn danger pulse" title="停止该账号正在刷的课：已完成视频全部保留，下次点▶从断点续播" onclick="stopGrind(${a.id})">■ 停止</button>`:'';
    const bad=a.days_left!==null&&a.days_left<0;
    const avatar=(a.platform_username||'?').trim().charAt(0);
    return `<div class="card${run?' running':''}${hits.length?' hit':''}">
    <div class="head">
      <div class="avatar">${esc(avatar)}</div>
      <div class="names"><div class="who">${hl(a.platform_username||'—',q)}</div>
        <div class="sub">#${a.id} ${hl(a.label,q)} · 优先级 ${a.priority}</div></div>
      <div class="ops">${bad?badge('已过期','b-warn'):badge(a.status,a.status==='active'?'b-ok':'b-warn')}
        ${run?badge('刷课中 '+run.since,'b-ok pulse'):sess}
        <button class="minibtn" title="AI 作业：按该账号的作业策略处理（✎管理可改策略）"
          onclick="doWork(${a.id},'2.12')">📝 作业</button>
        <button class="minibtn" title="账号管理：备注/优先级/专属窗口与额度/有效期/续期/删除" onclick="editAccount(${a.id})">✎ 管理</button>${stopBtn}</div>
    </div>
    ${a.note?`<div class="muted" style="font-size:.75rem;margin-bottom:.3rem">📝 ${esc(a.note)}</div>`:''}
    <div class="chips">${chips}</div>${courses}</div>`;
  }).join('')||'<div class="card muted">无匹配</div>';
  document.getElementById('pager').innerHTML=
    `<button ${PAGE<=1?'disabled':''} onclick="go(${PAGE-1})">‹ 上一页</button>
     <span class="muted">${PAGE} / ${pages}</span>
     <button ${PAGE>=pages?'disabled':''} onclick="go(${PAGE+1})">下一页 ›</button>`;
}
function go(p){PAGE=p;renderAccounts();}
let LOGN=20;
function renderLogs(n){if(n)LOGN=n;
  const acc=document.getElementById('logAcc')?.value||'';
  fetch('/api/logs?n='+LOGN+(acc?`&account=${acc}`:'')).then(r=>r.json()).then(d=>{
    document.getElementById('logs').innerHTML='<table>'+d.logs.map(l=>{
      const cls=l.event==='video-done'?'ev-good':l.event==='session-start'?'ev-info':
        (l.event==='session-stop'||l.level==='WARNING')?'ev-warn':'';
      return `<tr><td class="mono" style="white-space:nowrap">${esc(l.ts)}</td>
        <td class="mono">${esc(l.who)}</td><td class="ev ${cls}">${esc(l.event)}</td>
        <td>${esc(l.detail)}</td></tr>`;}).join('')+'</table>';});
}
function loadSettings(){
  fetch('/api/settings').then(r=>r.json()).then(d=>{
    s_quiz.checked=!!d.quiz_enabled;
    s_ws.value=d.window_start||'08:00'; s_we.value=d.window_end||'23:00';
    s_min.value=d.daily_minutes??0; s_task.value=d.daily_tasks??0;});
}
function saveSettings(){
  fetch('/api/settings',{method:'PUT',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({quiz_enabled:s_quiz.checked,window_start:s_ws.value,
      window_end:s_we.value,daily_minutes:+s_min.value||0,daily_tasks:+s_task.value||0})}
  ).then(r=>r.json()).then(d=>{alert(d.ok?'已保存（新会话生效）':'保存失败：'+d.error);
    if(d.ok)document.getElementById('dlg').close();});
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
let EDIT=null,RUNNING={},AUTOPLAN={times:{}},AUTOSTATE={},EXPANDED={};
function toggleMore(id){EXPANDED[id]=!EXPANDED[id];renderAccounts();}
function startGrind(id,course){
  fetch('/api/study/start',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({account_id:id,course:course})})
  .then(r=>r.json()).then(d=>{alert(d.ok?'已开始刷《'+course+'》（拟人节奏，额度内自动停）':'失败：'+d.error);
    syncRun();});}
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
    ACCOUNTS=d.accounts;}catch(e){}
  const btn=document.getElementById('autoBtn');
  const runN=Object.values(RUNNING).filter(j=>j.running).length;
  const el=document.getElementById('runN'); if(el)el.textContent=runN;
  if(AUTOSTATE.running){btn.textContent=`⏱ 调度运行中（${AUTOSTATE.since}）`;
    btn.style.background='rgba(34,160,93,.5)';}
  else{btn.textContent='⏱ 定时调度：点击启动';btn.style.background='';}
  const sync=document.getElementById('synced');
  if(sync)sync.textContent='自动刷新中 · 更新于 '+new Date().toLocaleTimeString('zh-CN',{hour12:false});
  renderAccounts();renderLogs();}
function toggleAuto(){
  const on=AUTOSTATE.running;
  if(on&&!confirm('停止定时调度？（已开的学习会话会自行跑到当日额度停机）'))return;
  fetch('/api/auto/'+(on?'stop':'start'),{method:'POST'})
    .then(r=>r.json()).then(d=>{if(!d.ok)alert(d.error);syncRun();});}
function doRenew(){fetch('/api/accounts/'+EDIT.id+'/renew',{method:'POST',
  headers:{'Content-Type':'application/json'},body:JSON.stringify({days:30})})
  .then(r=>r.json()).then(d=>{if(!d.ok){alert('失败');return;}
    const until=new Date(Date.now()+d.days_left*864e5).toISOString().slice(0,10);
    e_vu.value=until;  // 弹窗内即时刷新有效期
    e_meta.textContent=`已续期，剩余 ${d.days_left} 天（至 ${until}）`;
    reload();});}
function doWork(id,chapter){
  const a=ACCOUNTS.find(x=>x.id===id); if(!a)return;
  const mode=a.work_mode||'owner';
  if(mode==='reference'){
    document.getElementById('w_ch').textContent=`#${id} ${a.platform_username||a.label}｜${chapter}`;
    document.getElementById('w_body').textContent='AI 解析作业中（约 10~30 秒，含字体解码）…';
    document.getElementById('workDlg').showModal();
    fetch(`/api/accounts/${id}/work`,{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({chapter:chapter})}).then(r=>r.json()).then(d=>{
      const el=document.getElementById('w_body');
      if(!d.ok){el.textContent='失败：'+d.error;return;}
      if(d.action==='owner'){el.textContent=d.message;return;}
      el.innerHTML=(d.answers||[]).map((x,i)=>
        `<div style="margin:.6rem 0"><b>第 ${i+1} 题</b>（${esc(x.stem)}…）<br>
         <span style="color:var(--ok)">参考答案：</span>${esc(x.answer)}</div>`).join('')
        +'<div class="hint">请登录该账号作业页抄写并提交（计分作业建议用自己的语言改写）</div>';});
  } else if(mode==='ai'){
    if(!confirm('AI 代答模式：后台填入该账号作业（semi 不提交）。完成后需到平台作业页核对并提交。继续？'))return;
    fetch(`/api/accounts/${id}/work`,{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({chapter:chapter})}).then(r=>r.json()).then(d=>{
      alert(d.ok?d.message:'失败：'+d.error); if(d.ok)syncRun();});
  } else {
    const who = a.platform_username || a.label;
    alert(`该账号作业策略=号主自理（默认）：作业由 ${who} 自己完成，本工具只负责刷视频。如需 AI 辅助：✎ 管理 → 作业策略 → 改为"AI 参考答案"或"AI 代答"。`);
  }
}
function editAccount(id){EDIT=ACCOUNTS.find(a=>a.id===id);if(!EDIT)return;
  e_title.textContent=`#${EDIT.id}`;e_label.value=EDIT.label;e_status.value=EDIT.status;
  e_pri.value=EDIT.priority;e_ws.value=EDIT.window_start||'';e_we.value=EDIT.window_end||'';
  e_sa.value=EDIT.start_at||'';e_wm.value=EDIT.work_mode||'owner';
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
document.getElementById('q').addEventListener('input',()=>{PAGE=1;renderAccounts();});
document.getElementById('psize').addEventListener('change',()=>{PAGE=1;renderAccounts();});
renderLogs();loadSettings();syncRun();setInterval(syncRun,15000);
</script></body></html>"""


def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(title="XXT 管理面板", docs_url="/api/docs")

    def db() -> sqlite3.Connection:
        return connect(settings.db_path)

    @app.get("/api/overview")
    def overview():
        """多账号总览：每账号的登录状态、覆盖配置、课程进度与当日战果。"""
        from datetime import datetime as _dt

        def today_stats(conn: sqlite3.Connection, account_id: int, today: str) -> dict:
            """分钟数 = Σ 每个完成视频的真实时长（video-done 详情尾部"Ns"，纯观看时长）。
            机制上线前的历史记录无时长后缀，不计入（宁少勿虚报）。"""
            import re as _re

            rows = conn.execute(
                "SELECT detail FROM run_logs WHERE event='video-done' "
                "AND account_id=? AND ts LIKE ?", (account_id, f"{today}%"),
            ).fetchall()
            seconds = sum(
                int(m.group(1))
                for r in rows
                if (m := _re.search(r"｜(\d+)s$", r["detail"] or ""))
            )
            return {"today_videos": len(rows), "today_min": int(seconds // 60)}

        conn = db()
        try:
            today = time.strftime("%Y-%m-%d")
            accounts = [dict(r) for r in list_accounts(conn)]
            for a in accounts:
                state = settings.data_dir / "accounts" / str(a["id"]) / "state.json"
                a["has_session"] = state.exists()
                a.update(today_stats(conn, a["id"], today))
                a["window"] = (
                    f"{a['window_start']}–{a['window_end']}"
                    if a["window_start"] and a["window_end"] else None
                )
                a["max_minutes"] = a.pop("daily_max_minutes", None)
                a["max_tasks"] = a.pop("daily_max_tasks", None)
                from ..core.accounts import days_left

                a["days_left"] = days_left(a)
                a["courses"] = [
                    dict(r)
                    for r in conn.execute(
                        """SELECT c.name,
                           (SELECT COUNT(*) FROM chapters h WHERE h.course_id=c.id) AS total,
                           (SELECT COUNT(*) FROM chapters h WHERE h.course_id=c.id
                              AND h.status='done') AS done
                           FROM courses c WHERE c.account_id=? ORDER BY c.id""",
                        (a["id"],),
                    )
                ]
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
                        # AI 已答判定：work-answer 日志详情以章节名开头
                        ai_answered = bool(conn.execute(
                            "SELECT id FROM run_logs WHERE account_id=? AND event='work-answer' "
                            "AND detail LIKE ? LIMIT 1",
                            (a["id"], f"{h['name'][:14]}%")).fetchone())
                        works.append({"course": cname, "name": h["name"],
                                      "status": h["status"], "ai_answered": ai_answered})
                a["works"] = works
            today = time.strftime("%Y-%m-%d")
            n = conn.execute(
                "SELECT COUNT(*) AS c FROM run_logs WHERE event='video-done' AND ts LIKE ?",
                (f"{today}%",),
            ).fetchone()["c"]
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

    @app.get("/api/accounts")
    def accounts():
        conn = db()
        try:
            return {"accounts": [dict(r) for r in list_accounts(conn)]}
        finally:
            conn.close()

    @app.get("/api/logs")
    def logs(n: int = 50, account: int | None = None):
        conn = db()
        try:
            sql = (
                "SELECT l.ts, l.level, l.event, l.detail, l.account_id, "
                "COALESCE(a.platform_username, a.label, '#'||l.account_id) AS who "
                "FROM run_logs l LEFT JOIN accounts a ON a.id = l.account_id"
            )
            params: tuple = ()
            if account:
                sql += " WHERE l.account_id = ?"
                params = (account,)
            sql += " ORDER BY l.id DESC LIMIT ?"
            params += (max(1, min(n, 500)),)
            rows = conn.execute(sql, params).fetchall()
            return {"logs": [dict(r) for r in rows]}
        finally:
            conn.close()

    @app.get("/api/settings")
    def get_settings():
        fresh = Settings(root=settings.root)  # 重新加载（含 app_config 覆盖层）
        return {
            "quiz_enabled": bool(fresh.quiz["enabled"]),
            "window_start": fresh.pace["daily_window"][0],
            "window_end": fresh.pace["daily_window"][1],
            "daily_minutes": int(fresh.quota["daily_minutes"]),
            "daily_tasks": int(fresh.quota["daily_tasks"]),
        }

    @app.put("/api/settings")
    def put_settings(body: dict):
        """面板设置 → app_config 全局覆盖层（白名单键，v0.6 三层配置第 2 层）。"""
        import json as _json
        import re as _re

        def upsert(conn: sqlite3.Connection, key: str, value) -> None:
            conn.execute(
                "INSERT INTO app_config(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, _json.dumps(value)),
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
            fields: dict = {}
            for k in ("label", "note", "valid_until", "window_start", "window_end",
                      "proxy", "start_at"):
                if k in body:
                    fields[k] = body[k] or None
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

    def _log_event(account_id: int, event: str, detail: str) -> None:
        from ..log import get_logger

        get_logger("panel").info("[账号#%s] %s｜%s", account_id, event, detail)
        conn = db()
        try:
            conn.execute(
                "INSERT INTO run_logs(ts, level, event, detail, account_id)"
                " VALUES(?,?,?,?,?)",
                (time.strftime("%Y-%m-%dT%H:%M:%S"), "INFO", event, detail, account_id),
            )
            conn.commit()
        finally:
            conn.close()

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
        finally:
            conn.close()
        if not n:
            return {"ok": False, "error": "该账号还没有课程数据，先为其抓取课程（xxt courses --account）"}
        root = pathlib.Path(settings.root)
        proc = _spawn(str(root), "mlearn", course, "--accounts", str(account_id))
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
            subprocess.run(["taskkill", "/PID", str(ext["pid"]), "/F"],
                           capture_output=True)
            (settings.data_dir / "run" / f"{account_id}.json").unlink(missing_ok=True)
            _log_event(account_id, "session-stop", f"{ext['course']}｜手动停止（进度保留）")
            return {"ok": True}
        return {"ok": False, "error": "该账号没有进行中的会话"}

    @app.post("/api/auto/start")
    def auto_start():
        """启动定时调度守护；PID 落 data/run/auto.json（面板重启后仍可见、可停）。"""
        import json as _json

        global _AUTO
        marker = settings.data_dir / "run" / "auto.json"
        if marker.exists():  # 旧守护还活着（如面板重启前启动的）——先确认
            try:
                old = _json.loads(marker.read_text(encoding="utf-8"))
                if _pid_alive(int(old["pid"])):
                    return {"ok": False, "error": "定时调度已在运行"}
            except Exception:
                pass
        proc = _spawn(str(settings.root), "auto")
        since = time.strftime("%H:%M")
        _AUTO = {"proc": proc, "since": since}
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(_json.dumps({"pid": proc.pid, "since": since}),
                          encoding="utf-8")
        return {"ok": True}

    @app.post("/api/auto/stop")
    def auto_stop():
        """停止定时调度（含面板重启前的旧守护）；学习会话不被动。"""
        global _AUTO
        marker = settings.data_dir / "run" / "auto.json"
        stopped = False
        if _AUTO and _AUTO["proc"].poll() is None:
            _AUTO["proc"].terminate()
            stopped = True
        if marker.exists():  # 孤儿守护（面板重启后失联的）
            try:
                import json as _json

                old = _json.loads(marker.read_text(encoding="utf-8"))
                if _pid_alive(int(old["pid"])):
                    subprocess.run(["taskkill", "/PID", str(old["pid"]), "/F"],
                                   capture_output=True)
                    stopped = True
            except Exception:
                pass
            marker.unlink(missing_ok=True)
        _AUTO = None
        return {"ok": True} if stopped else {"ok": False, "error": "定时调度未在运行"}

    @app.get("/api/study/status")
    def study_status():
        out = {}
        for aid, job in _JOBS.items():
            alive = job["proc"].poll() is None
            out[str(aid)] = {"course": job["course"], "running": alive,
                             "since": job["since"]}
            if not alive:
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
        """作业作答（面板入口，按账号 work_mode 分流）：
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
        mode = row["work_mode"] or "owner"
        chapter = body.get("chapter") or "2.12"
        if mode == "owner":
            return {"ok": True, "action": "owner",
                    "message": f"该账号作业策略=号主自理：请通知 {row['label']} 自己完成作业；"
                               f"如需 AI 参考答案，可在✎管理中把作业策略改为 reference"}
        if mode == "ai":
            if account_id in _JOBS and _JOBS[account_id]["proc"].poll() is None:
                return {"ok": False, "error": "该账号已有进行中的会话/作业"}
            proc = _spawn(str(settings.root), "work", str(account_id), chapter)
            _JOBS[account_id] = {"proc": proc, "course": f"作业{chapter}", "since": time.strftime("%H:%M")}
            _log_event(account_id, "work-start", f"{chapter}｜AI 代答（headless，semi）")
            return {"ok": True, "action": "ai",
                    "message": "AI 已开始作答（headless 填入，semi 不提交）——完成后请到平台作业页核对提交"}
        # reference：只出参考答案（解析+AI，不填表单）
        try:
            from playwright.sync_api import sync_playwright
            from .adapters.chaoxing.study import find_frame_with, open_chapter_frame
            from .adapters.chaoxing.work import parse_work_questions, _page_font_map
            from .ai.llm import LLMClient
            from ...ai.quiz import build_messages as _bm  # noqa: F401  (占位防误用)
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
            answers = []
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(storage_state=str(state))
                page = context.new_page()
                try:
                    tree = open_chapter_frame(page, course["url"])
                    kid = conn2_id = None
                    c2 = db()
                    try:
                        kid_row = c2.execute(
                            "SELECT platform_chapter_id FROM chapters WHERE course_id=? "
                            "AND name LIKE ? LIMIT 1", (course["id"], f"%{chapter}%")).fetchone()
                        kid = kid_row["platform_chapter_id"] if kid_row else None
                    finally:
                        c2.close()
                    if kid is None:
                        return {"ok": False, "error": f"未找到章节 {chapter}"}
                    tree.locator(f"#cur{kid}").first.click(timeout=15_000)
                    wf = find_frame_with(page, "div.singleQuesId", timeout_s=25)
                    if wf is None:
                        return {"ok": False, "error": "该章节不是作业任务点"}
                    qs = parse_work_questions(wf.content(), dynamic_map=_page_font_map(wf))
                    llm = LLMClient(settings.llm)
                    for q in qs:
                        try:
                            raw = llm.chat(build_messages(
                                q["stem"], q["qtype"], q["options"]))
                            ans = raw.strip()[:500]
                        except Exception as exc:
                            ans = f"（AI 调用失败：{exc}）"
                        answers.append({"stem": q["stem"][:80], "answer": ans})
                    context.storage_state(path=str(state))
                finally:
                    browser.close()
            return {"ok": True, "action": "reference", "answers": answers}
        except Exception as exc:
            return {"ok": False, "error": repr(exc)}

    @app.get("/", response_class=HTMLResponse)
    def index():
        # no-store：面板迭代频繁，避免浏览器缓存旧 HTML 导致"按钮消失"类困惑
        return HTMLResponse(_PAGE, headers={"Cache-Control": "no-store"})

    return app
