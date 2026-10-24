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
:root{--bg:#f4f6fa;--card:#fff;--ink:#1c2333;--muted:#7a8299;--line:#e7eaf1;
--accent:#4f8cff;--ok:#22a06b;--warn:#e8930c;--bad:#d64545}
*{box-sizing:border-box}body{font-family:system-ui,"Microsoft YaHei",sans-serif;margin:0;
background:var(--bg);color:var(--ink)}
.wrap{max-width:1080px;margin:0 auto;padding:1.5rem 1rem 3rem}
header{display:flex;align-items:baseline;gap:1rem;margin-bottom:1.25rem;flex-wrap:wrap}
h1{font-size:1.35rem;margin:0}h1 small{color:var(--muted);font-weight:400;font-size:.85rem}
.stat{background:var(--accent);color:#fff;border-radius:10px;padding:.3rem .8rem;font-size:.9rem}
#q{flex:1;min-width:200px;max-width:340px;border:1px solid var(--line);border-radius:10px;
padding:.45rem .8rem;font-size:.9rem;background:var(--card);color:var(--ink);outline:none}
#q:focus{border-color:var(--accent)}
select{border:1px solid var(--line);border-radius:10px;padding:.4rem .5rem;font-size:.85rem;
background:var(--card);color:var(--ink)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:1rem}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:1rem 1.1rem;
box-shadow:0 1px 4px rgba(20,30,60,.06)}
.card h3{margin:0 0 .5rem;font-size:1rem;display:flex;align-items:center;gap:.5rem;flex-wrap:wrap}
.badge{font-size:.72rem;border-radius:99px;padding:.1rem .55rem;font-weight:600}
.b-ok{background:#e5f5ec;color:var(--ok)}.b-warn{background:#fdf2e2;color:var(--warn)}
.b-bad{background:#fbe9e9;color:var(--bad)}.b-info{background:#e9f0fe;color:var(--accent)}
.chips{display:flex;gap:.35rem;flex-wrap:wrap;margin:.35rem 0 .6rem}
.chip{font-size:.72rem;color:var(--muted);background:var(--bg);border:1px solid var(--line);
border-radius:6px;padding:.1rem .45rem}
.course{margin:.45rem 0}
.course .t{display:flex;justify-content:space-between;font-size:.85rem}
.bar{height:7px;background:var(--bg);border-radius:4px;overflow:hidden;margin-top:3px}
.bar>i{display:block;height:100%;background:linear-gradient(90deg,#4f8cff,#7bb0ff);border-radius:4px}
section{margin-top:1.5rem}
section>h2{font-size:.95rem;color:var(--muted);margin:0 0 .6rem}
table{width:100%;border-collapse:collapse;font-size:.82rem;background:var(--card);
border:1px solid var(--line);border-radius:12px;overflow:hidden}
td{padding:.45rem .7rem;border-bottom:1px solid var(--line)}
tr:last-child td{border-bottom:none}
.ev{font-weight:600}.ev-good{color:var(--ok)}.ev-warn{color:var(--warn)}
.mono{font-family:Consolas,monospace;color:var(--muted)}
.muted{color:var(--muted)}
</style></head><body><div class="wrap">
<header><h1>XXT 学习面板 <small>多账号网课助手</small></h1>
<span class="stat" id="today">今日 - 个视频</span>
<input id="q" placeholder="🔍 搜索账号（昵称 / 备注 / 状态）…">
<select id="logAcc"><option value="">全部账号日志</option></select></header>
<div class="grid" id="accounts"><div class="card muted">加载中…</div></div>
<section><h2>最近日志</h2><div id="logs" class="muted">加载中…</div></section>
</div>
<script>
const esc=s=>String(s??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const badge=(txt,cls)=>`<span class="badge ${cls}">${esc(txt)}</span>`;
let ACCOUNTS=[];
function renderAccounts(){
  const q=document.getElementById('q').value.trim().toLowerCase();
  document.getElementById('accounts').innerHTML=ACCOUNTS.filter(a=>{
    if(!q)return true;
    return [a.platform_username,a.label,a.status,String(a.id)].some(
      x=>String(x??'').toLowerCase().includes(q));
  }).map(a=>{
    const who=a.platform_username||'—';
    const status=a.status==='active'?'b-ok':'b-warn';
    const sess=a.has_session?badge('已登录','b-info'):badge('未登录','b-warn');
    const chips=[a.window?`窗口 ${esc(a.window)}`:'',a.max_minutes?`时长 ${a.max_minutes}分`:'',
                 a.max_tasks?`任务 ${a.max_tasks}个`:'',a.proxy?'绑定代理':'']
                 .filter(Boolean).map(c=>`<span class="chip">${c}</span>`).join('');
    const courses=a.courses.map(c=>`<div class="course">
      <div class="t"><span>${esc(c.name)}</span><b>${c.done}/${c.total}</b></div>
      <div class="bar"><i style="width:${c.total?c.done/c.total*100:0}%"></i></div></div>`).join('')
      ||'<div class="muted" style="font-size:.82rem">暂无课程数据</div>';
    return `<div class="card"><h3>${esc(who)} <span class="muted" style="font-size:.8rem">#${a.id} ${esc(a.label)}</span>
      ${badge(a.status,status)} ${sess}<span class="chip">优先级 ${a.priority}</span></h3>
      <div class="chips">${chips}</div>${courses}</div>`;
  }).join('')||'<div class="card muted">无匹配账号</div>';
}
function renderLogs(){
  const acc=document.getElementById('logAcc').value;
  fetch('/api/logs?n=50'+(acc?`&account=${acc}`:'')).then(r=>r.json()).then(d=>{
    document.getElementById('logs').innerHTML='<table>'+d.logs.map(l=>{
      const cls=l.event==='video-done'?'ev-good':(l.level==='WARNING'?'ev-warn':'');
      return `<tr><td class="mono" style="white-space:nowrap">${esc(l.ts)}</td>
        <td class="mono">${esc(l.who)}</td>
        <td class="ev ${cls}">${esc(l.event)}</td><td>${esc(l.detail)}</td></tr>`;
    }).join('')+'</table>';
  });
}
fetch('/api/overview').then(r=>r.json()).then(d=>{
  document.getElementById('today').textContent=`今日 ${d.today_videos} 个视频`;
  ACCOUNTS=d.accounts;
  renderAccounts();
  const sel=document.getElementById('logAcc');
  d.accounts.forEach(a=>{
    const o=document.createElement('option');
    o.value=a.id;o.textContent=a.platform_username?`${a.platform_username}（${a.label}）`:a.label;
    sel.appendChild(o);
  });
});
document.getElementById('q').addEventListener('input',renderAccounts);
document.getElementById('logAcc').addEventListener('change',renderLogs);
renderLogs();
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

    @app.get("/", response_class=HTMLResponse)
    def index():
        return _PAGE

    return app
