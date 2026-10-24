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
<title>XXT 学习面板</title><style>
body{font-family:system-ui,"Microsoft YaHei";margin:2rem;background:#f6f7f9;color:#222}
.card{background:#fff;border-radius:10px;padding:1rem 1.25rem;margin-bottom:1rem;box-shadow:0 1px 3px rgba(0,0,0,.08)}
h2{font-size:1rem;color:#555;margin:0 0 .75rem}
.bar{height:8px;background:#e8eaf0;border-radius:4px;overflow:hidden;margin-top:4px}
.bar>i{display:block;height:100%;background:#4f8cff}
table{width:100%;border-collapse:collapse;font-size:.9rem}
td{padding:.35rem .4rem;border-bottom:1px solid #f0f1f4}
.log{font-family:Consolas,monospace;font-size:.78rem;white-space:pre-wrap}
.muted{color:#888}
</style></head><body>
<div class="card"><h2>课程进度（今日视频 <b id="today">-</b> 个）</h2><div id="courses" class="muted">加载中…</div></div>
<div class="card"><h2>账号池</h2><div id="accounts" class="muted">加载中…</div></div>
<div class="card"><h2>最近日志</h2><div id="logs" class="log muted">加载中…</div></div>
<script>
const esc = s => String(s??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
fetch('/api/progress').then(r=>r.json()).then(d=>{
  document.getElementById('today').textContent=d.today_videos;
  document.getElementById('courses').innerHTML=d.courses.map(c=>
    `<div>${esc(c.name)}　<b>${c.done}/${c.total}</b>（${c.total?Math.round(c.done/c.total*100):0}%）
     <div class="bar"><i style="width:${c.total?c.done/c.total*100:0}%"></i></div></div>`).join('');
});
fetch('/api/accounts').then(r=>r.json()).then(d=>{
  document.getElementById('accounts').innerHTML=d.accounts.map(a=>
    `<div>#${a.id} [${esc(a.status)}] ${esc(a.label)}（优先级 ${a.priority}）</div>`).join('');
});
fetch('/api/logs?n=30').then(r=>r.json()).then(d=>{
  document.getElementById('logs').innerHTML=d.logs.map(l=>
    `${esc(l.ts)} ${esc(l.event)} ${esc(l.detail)}`).join('<br>');
});
</script></body></html>"""


def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(title="XXT 管理面板", docs_url="/api/docs")

    def db() -> sqlite3.Connection:
        return connect(settings.db_path)

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
    def logs(n: int = 50):
        conn = db()
        try:
            rows = conn.execute(
                "SELECT ts, level, event, detail FROM run_logs ORDER BY id DESC LIMIT ?",
                (max(1, min(n, 500)),),
            ).fetchall()
            return {"logs": [dict(r) for r in rows]}
        finally:
            conn.close()

    @app.get("/", response_class=HTMLResponse)
    def index():
        return _PAGE

    return app
