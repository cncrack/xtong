"""FastAPI 应用工厂：所有接口只读，凭据永不出现在任何响应中。"""

from __future__ import annotations

import sqlite3
import time

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from ..config import Settings
from ..core.accounts import list_accounts
from ..store.db import connect

_PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>XXT 学习面板</title><style>
:root{--bg:#eef1f7;--card:#fff;--ink:#1e2434;--muted:#7b8398;--line:#e6e9f2;
--accent:#5b6cff;--accent2:#8f7bff;--ok:#18a05d;--warn:#e8930c;--bad:#d64545}
*{box-sizing:border-box}body{font-family:system-ui,"Microsoft YaHei",sans-serif;margin:0;
background:var(--bg);color:var(--ink)}
.wrap{max-width:1140px;margin:0 auto;padding:1.25rem 1rem 3rem}
.hero{background:linear-gradient(120deg,#4756d7,#7a5bff 70%,#9d64ff);color:#fff;
border-radius:18px;padding:1.1rem 1.3rem;display:flex;align-items:center;gap:1rem;
flex-wrap:wrap;box-shadow:0 6px 24px rgba(70,80,220,.25)}
.hero h1{font-size:1.3rem;margin:0;letter-spacing:.5px}
.hero small{opacity:.75;font-weight:400}
.pill{background:rgba(255,255,255,.16);border:1px solid rgba(255,255,255,.25);
border-radius:99px;padding:.25rem .8rem;font-size:.85rem}
.spacer{flex:1}
#q{border:none;border-radius:12px;padding:.5rem .9rem;font-size:.9rem;min-width:230px;outline:none}
.btn{border:none;border-radius:12px;padding:.5rem 1rem;font-size:.88rem;cursor:pointer;
background:rgba(255,255,255,.2);color:#fff;border:1px solid rgba(255,255,255,.35);transition:.15s}
.btn:hover{background:rgba(255,255,255,.32)}
.toolbar{display:flex;align-items:center;gap:.6rem;margin:1rem 0 .8rem;flex-wrap:wrap}
.toolbar .muted{font-size:.85rem}
select,input[type=time],input[type=number],input[type=text]{border:1px solid var(--line);
border-radius:10px;padding:.4rem .55rem;font-size:.86rem;background:var(--card);color:var(--ink);outline:none}
select:focus,input:focus{border-color:var(--accent)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:.9rem}
.card{background:var(--card);border-radius:16px;padding:1rem 1.1rem;border:1px solid var(--line);
box-shadow:0 1px 4px rgba(25,35,70,.06);transition:.18s;cursor:default}
.card:hover{transform:translateY(-2px);box-shadow:0 8px 22px rgba(25,35,70,.12)}
.card.hit{outline:2px solid var(--accent);outline-offset:1px}
.card h3{margin:0 0 .45rem;font-size:1.02rem;display:flex;align-items:center;gap:.45rem;flex-wrap:wrap}
.who{font-size:1.15rem;font-weight:700;background:linear-gradient(90deg,#4756d7,#9d64ff);
-webkit-background-clip:text;background-clip:text;color:transparent}
.badge{font-size:.7rem;border-radius:99px;padding:.12rem .58rem;font-weight:600}
.b-ok{background:#e3f6ec;color:var(--ok)}.b-warn{background:#fdf2df;color:var(--warn)}
.b-info{background:#e8edff;color:#4353d8}
.chips{display:flex;gap:.32rem;flex-wrap:wrap;margin:.3rem 0 .55rem}
.chip{font-size:.7rem;color:var(--muted);background:var(--bg);border:1px solid var(--line);
border-radius:7px;padding:.1rem .45rem}
.course{margin:.4rem 0}.course.hit{background:#f0f3ff;border-radius:8px;padding:.25rem .4rem;margin-left:-.4rem}
.course .t{display:flex;justify-content:space-between;font-size:.84rem;gap:.5rem}
.course .t span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bar{height:7px;background:var(--bg);border-radius:4px;overflow:hidden;margin-top:3px}
.bar>i{display:block;height:100%;width:0;border-radius:4px;
background:linear-gradient(90deg,#5b6cff,#9d64ff);transition:width .5s}
.pager{display:flex;align-items:center;gap:.5rem;justify-content:center;margin-top:1rem;font-size:.86rem}
.pager button{border:1px solid var(--line);background:var(--card);border-radius:9px;
padding:.3rem .75rem;cursor:pointer}.pager button:disabled{opacity:.4;cursor:default}
section{margin-top:1.6rem}section>h2{font-size:.92rem;color:var(--muted);margin:0 0 .6rem}
table{width:100%;border-collapse:collapse;font-size:.82rem;background:var(--card);
border:1px solid var(--line);border-radius:14px;overflow:hidden}
td{padding:.45rem .7rem;border-bottom:1px solid var(--line)}tr:last-child td{border-bottom:none}
.ev{font-weight:600}.ev-good{color:var(--ok)}.ev-warn{color:var(--warn)}
.mono{font-family:Consolas,monospace;color:var(--muted)}.muted{color:var(--muted)}
dialog{border:none;border-radius:18px;padding:1.2rem 1.4rem;width:min(430px,92vw);
box-shadow:0 20px 60px rgba(20,30,70,.3)}
dialog::backdrop{background:rgba(20,25,45,.45);backdrop-filter:blur(2px)}
dialog h3{margin:0 0 .9rem}
.frow{display:flex;align-items:center;justify-content:space-between;gap:1rem;margin:.7rem 0}
.frow label{font-size:.9rem}
.switch{position:relative;width:42px;height:23px;flex:none}
.switch input{opacity:0;width:0;height:0}
.switch i{position:absolute;inset:0;background:#cdd3e4;border-radius:99px;transition:.2s;cursor:pointer}
.switch i:before{content:"";position:absolute;width:17px;height:17px;border-radius:50%;
background:#fff;top:3px;left:3px;transition:.2s;box-shadow:0 1px 3px rgba(0,0,0,.25)}
.switch input:checked+i{background:var(--ok)}
.switch input:checked+i:before{transform:translateX(19px)}
.dlgbtns{display:flex;gap:.6rem;justify-content:flex-end;margin-top:1.1rem}
.primary{background:linear-gradient(120deg,#4756d7,#7a5bff);color:#fff;border:none;
border-radius:11px;padding:.55rem 1.2rem;cursor:pointer;font-size:.9rem}
.ghost{background:var(--bg);border:1px solid var(--line);border-radius:11px;
padding:.55rem 1rem;cursor:pointer;font-size:.9rem}
.hint{font-size:.78rem;color:var(--muted);margin-top:.6rem}
</style></head><body><div class="wrap">
<div class="hero">
  <h1>XXT 学习面板 <small>多账号网课助手</small></h1>
  <span class="pill">👤 账号 <b id="accN">-</b></span>
  <span class="pill">🎬 今日视频 <b id="today">-</b></span>
  <span class="spacer"></span>
  <input id="q" placeholder="🔍 搜索账号或课程名…">
  <button class="btn" onclick="document.getElementById('dlg').showModal()">⚙ 设置</button>
</div>
<div class="toolbar">
  <select id="psize"><option value="10">每页 10 张</option><option value="20" selected>每页 20 张</option><option value="50">每页 50 张</option></select>
  <span class="muted" id="accInfo"></span>
</div>
<div class="grid" id="accounts"><div class="card muted">加载中…</div></div>
<div class="pager" id="pager"></div>
<section><h2>最近日志</h2><div id="logs" class="muted">加载中…</div></section>
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
                 a.max_tasks?`任务 ${a.max_tasks}个`:'',a.proxy?'代理':''].filter(Boolean)
                 .map(c=>`<span class="chip">${c}</span>`).join('');
    const courses=a.courses.map(c=>{
      const hit=hits.some(h=>h.name===c.name)?' hit':'';
      return `<div class="course${hit}"><div class="t"><span>${esc(c.name)}</span><b>${c.done}/${c.total}</b></div>
      <div class="bar"><i style="width:${c.total?c.done/c.total*100:0}%"></i></div></div>`;}).join('')
      ||'<div class="muted" style="font-size:.8rem">暂无课程数据</div>';
    return `<div class="card${hits.length?' hit':''}"><h3>
      <span class="who">${esc(a.platform_username||'—')}</span>
      <span class="muted" style="font-size:.75rem">#${a.id} ${esc(a.label)}</span>
      ${badge(a.status,status)} ${sess}<span class="chip">优先级 ${a.priority}</span></h3>
      <div class="chips">${chips}</div>${courses}</div>`;
  }).join('')||'<div class="card muted">无匹配</div>';
  document.getElementById('pager').innerHTML=
    `<button ${PAGE<=1?'disabled':''} onclick="go(${PAGE-1})">‹ 上一页</button>
     <span class="muted">${PAGE} / ${pages}</span>
     <button ${PAGE>=pages?'disabled':''} onclick="go(${PAGE+1})">下一页 ›</button>`;
}
function go(p){PAGE=p;renderAccounts();}
function renderLogs(){
  const acc=document.getElementById('logAcc')?.value||'';
  fetch('/api/logs?n=50'+(acc?`&account=${acc}`:'')).then(r=>r.json()).then(d=>{
    document.getElementById('logs').innerHTML='<table>'+d.logs.map(l=>{
      const cls=l.event==='video-done'?'ev-good':(l.level==='WARNING'?'ev-warn':'');
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
document.getElementById('q').addEventListener('input',()=>{PAGE=1;renderAccounts();});
document.getElementById('psize').addEventListener('change',()=>{PAGE=1;renderAccounts();});
renderLogs();loadSettings();
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

    @app.get("/", response_class=HTMLResponse)
    def index():
        return _PAGE

    return app
