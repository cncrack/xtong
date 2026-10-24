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
    return subprocess.Popen(
        [sys.executable, "-m", "xxt.cli", *cmd],
        cwd=root_str, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


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
.wrap{max-width:1180px;margin:0 auto;padding:1.4rem 1.1rem 2.5rem}
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
select,input[type=time],input[type=number],input[type=text],input[type=password],input[type=date]
{border:1px solid var(--line);border-radius:10px;padding:.42rem .6rem;font-size:.86rem;
background:var(--card);color:var(--ink);outline:none;transition:.15s}
select:focus,input:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(91,108,255,.12)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(345px,1fr));gap:1rem}
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
.course .t{display:flex;justify-content:space-between;align-items:center;font-size:.84rem;gap:.5rem}
.course .t .nm{flex:1;min-width:0;word-break:break-all;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.course .t .val{display:flex;gap:.5rem;align-items:center;flex:none;font-size:.8rem}
.course .pct{color:var(--muted);font-size:.72rem;min-width:34px;text-align:right}
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
  <button class="btn" onclick="openAdd()">＋ 账号</button>
  <button class="btn" id="autoBtn" onclick="toggleAuto()">⏱ 定时调度</button>
  <button class="btn" onclick="document.getElementById('dlg').showModal()">⚙ 设置</button>
</div>
<div class="toolbar">
  <select id="psize"><option value="10">每页 10 张</option><option value="20" selected>每页 20 张</option><option value="50">每页 50 张</option></select>
  <span class="muted" id="accInfo"></span>
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
  <div class="frow"><label>每日时长额度（分钟，0 不限）</label><input type="number" id="s_min" style="width:90px"></div>
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

<script>
const esc=s=>String(s??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const badge=(t,c)=>`<span class="badge ${c}">${esc(t)}</span>`;
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
  const ps=+document.getElementById('psize').value, list=filtered();
  const pages=Math.max(1,Math.ceil(list.length/ps)); PAGE=Math.min(PAGE,pages);
  const slice=list.slice((PAGE-1)*ps,PAGE*ps);
  document.getElementById('accN').textContent=ACCOUNTS.length;
  document.getElementById('accInfo').textContent=`匹配 ${list.length} 个账号 · 第 ${PAGE}/${pages} 页`;
  document.getElementById('accounts').innerHTML=slice.map(({a,hits})=>{
    const status=a.status==='active'?'b-ok':'b-warn';
    const sess=a.has_session?badge('已登录','b-info'):badge('未登录','b-warn');
    const chips=[a.window?`窗口 ${esc(a.window)}`:'',a.max_minutes?`时长 ${a.max_minutes}分`:'',
                 a.max_tasks?`任务 ${a.max_tasks}个`:'',a.proxy?'代理':'',
                 a.days_left===null?'':`有效期 ${a.days_left>=0?a.days_left+'天':'已过期'}`,
                 a.start_at?`定时 ${a.start_at}`:(AUTOPLAN.times[a.id]?`今日定时 ${AUTOPLAN.times[a.id]}`:'定时未设'),
                 a.renew_count?`续期${a.renew_count}次`:'',
                 a.last_expired_at?'上次失效 '+esc(a.last_expired_at.slice(5,16)):'' ]
                 .filter(Boolean).map(c=>`<span class="chip">${c}</span>`).join('');
    const courses=a.courses.map(c=>{
      const hit=hits.some(h=>h.name===c.name)?' hit':'';
      const run=RUNNING[a.id];
      const runningThis=run&&run.course===c.name;
      const ctl=runningThis
        ?`<span class="badge b-ok pulse" title="自 ${run.since}">▶ 刷课中</span>`
        :`<button class="minibtn" title="开始刷这门课"
            data-c="${esc(c.name)}" onclick="startGrind(${a.id},this.dataset.c)">▶ 刷</button>`;
      return `<div class="course${hit}"><div class="t"><span class="nm">${esc(c.name)}</span>
      <span class="val"><span class="pct">${c.total?Math.round(c.done/c.total*100):0}%</span>
      <b>${c.done}/${c.total}</b>${ctl}</span></div>
      <div class="bar"><i style="width:${c.total?c.done/c.total*100:0}%"></i></div></div>`;}).join('')
      ||'<div class="muted" style="font-size:.8rem">暂无课程数据</div>';
    const run=RUNNING[a.id];
    const stopBtn=run?`<button class="minibtn danger pulse" onclick="stopGrind(${a.id})">■ 停止</button>`:'';
    const bad=a.days_left!==null&&a.days_left<0;
    const avatar=(a.platform_username||'?').trim().charAt(0);
    return `<div class="card${run?' running':''}${hits.length?' hit':''}">
    <div class="head">
      <div class="avatar">${esc(avatar)}</div>
      <div class="names"><div class="who">${esc(a.platform_username||'—')}</div>
        <div class="sub">#${a.id} ${esc(a.label)} · 优先级 ${a.priority}</div></div>
      <div class="ops">${bad?badge('已过期','b-warn'):badge(a.status,a.status==='active'?'b-ok':'b-warn')}
        ${run?badge('刷课中 '+run.since,'b-ok pulse'):sess}
        <button class="minibtn" onclick="editAccount(${a.id})">✎ 管理</button>${stopBtn}</div>
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
function renderLogs(n=20){
  const acc=document.getElementById('logAcc')?.value||'';
  fetch('/api/logs?n=50'+(acc?`&account=${acc}`:'')).then(r=>r.json()).then(d=>{
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
fetch('/api/overview').then(r=>r.json()).then(d=>{
  document.getElementById('today').textContent=d.today_videos;
  ACCOUNTS=d.accounts;renderAccounts();
});
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
let EDIT=null,RUNNING={},AUTOPLAN={times:{}},AUTOSTATE={};
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
  const btn=document.getElementById('autoBtn');
  const runN=Object.values(RUNNING).filter(j=>j.running).length;
  const el=document.getElementById('runN'); if(el)el.textContent=runN;
  if(AUTOSTATE.running){btn.textContent=`⏱ 调度运行中（${AUTOSTATE.since}）`;
    btn.style.background='rgba(34,160,93,.5)';}
  else{btn.textContent='⏱ 定时调度：点击启动';btn.style.background='';}
  try{AUTOPLAN=await (await fetch('/api/autoplan')).json();}catch(e){AUTOPLAN={times:{}};}
  renderAccounts();}
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
function editAccount(id){EDIT=ACCOUNTS.find(a=>a.id===id);if(!EDIT)return;
  e_title.textContent=`#${EDIT.id}`;e_label.value=EDIT.label;e_status.value=EDIT.status;
  e_pri.value=EDIT.priority;e_ws.value=EDIT.window_start||'';e_we.value=EDIT.window_end||'';
  e_sa.value=EDIT.start_at||'';
  e_min.value=EDIT.max_minutes??0;e_task.value=EDIT.max_tasks??0;e_vu.value=EDIT.valid_until||'';
  e_note.value=EDIT.note||'';
  e_meta.textContent=`续期 ${EDIT.renew_count||0} 次 · 上次失效 ${EDIT.last_expired_at||'—'}`;
  document.getElementById('editDlg').showModal();}
function doSave(){fetch('/api/accounts/'+EDIT.id,{method:'PUT',
  headers:{'Content-Type':'application/json'},body:JSON.stringify({
    label:e_label.value.trim(),status:e_status.value,priority:+e_pri.value||1,
    start_at:e_sa.value||null,
    window_start:e_ws.value||null,window_end:e_we.value||null,
    daily_max_minutes:+e_min.value||0,daily_max_tasks:+e_task.value||0,
    valid_until:e_vu.value||null,note:e_note.value.trim()||null})})
  .then(r=>r.json()).then(d=>{alert(d.ok?'已保存':'失败：'+d.error);
    if(d.ok){document.getElementById('editDlg').close();reload();}});}
function doRenew(){fetch('/api/accounts/'+EDIT.id+'/renew',{method:'POST',
  headers:{'Content-Type':'application/json'},body:JSON.stringify({days:30})})
  .then(r=>r.json()).then(d=>{alert(d.ok?'续期成功，剩余 '+d.days_left+' 天':'失败');
    if(d.ok)reload();});}
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
        """多账号总览：每账号的登录状态、覆盖配置与各自课程进度。"""
        conn = db()
        try:
            accounts = [dict(r) for r in list_accounts(conn)]
            for a in accounts:
                state = settings.data_dir / "accounts" / str(a["id"]) / "state.json"
                a["has_session"] = state.exists()
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
        """启动定时调度守护（面板化管理 xxt auto，无需开命令行）。"""
        global _AUTO
        if _AUTO and _AUTO["proc"].poll() is None:
            return {"ok": False, "error": "定时调度已在运行"}
        proc = _spawn(str(settings.root), "auto")
        _AUTO = {"proc": proc, "since": time.strftime("%H:%M")}
        return {"ok": True}

    @app.post("/api/auto/stop")
    def auto_stop():
        """停止定时调度（已启动的学习会话继续自行跑到额度停机）。"""
        global _AUTO
        if not _AUTO or _AUTO["proc"].poll() is not None:
            _AUTO = None
            return {"ok": False, "error": "定时调度未在运行"}
        _AUTO["proc"].terminate()
        _AUTO = None
        return {"ok": True}

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
        auto_running = bool(_AUTO and _AUTO["proc"].poll() is None)
        if not auto_running:
            globals()["_AUTO"] = None
        return {"jobs": out, "auto": {"running": auto_running,
                                      "since": (_AUTO or {}).get("since")}}

    @app.get("/", response_class=HTMLResponse)
    def index():
        # no-store：面板迭代频繁，避免浏览器缓存旧 HTML 导致"按钮消失"类困惑
        return HTMLResponse(_PAGE, headers={"Cache-Control": "no-store"})

    return app
