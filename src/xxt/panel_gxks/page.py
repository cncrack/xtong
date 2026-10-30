"""第二平台控制台的页面（单页，原生 JS + fetch 轮询，不引前端框架）。

刻意做成一个字符串常量：面板要能塞进单文件 exe，多一个静态目录就多一处打包路径
要维护。轮询 4 秒——比自己写 WebSocket 稳，面板崩了页面也还能显示最后一次数据。
"""

PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>第二平台控制台</title>
<style>
:root{--bg:#f5f6f8;--card:#fff;--line:#e3e6eb;--txt:#1f2328;--dim:#6b7280;
      --ok:#12805c;--warn:#a86200;--bad:#b42318;--accent:#1f6feb}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);
     font:14px/1.55 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
header{display:flex;align-items:center;gap:12px;padding:12px 18px;background:var(--card);
       border-bottom:1px solid var(--line);position:sticky;top:0;z-index:9}
h1{font-size:16px;margin:0;font-weight:600}
h2{font-size:13px;margin:0 0 8px;color:var(--dim);font-weight:600;letter-spacing:.02em}
main{max-width:1180px;margin:0 auto;padding:16px 18px 60px}
section{background:var(--card);border:1px solid var(--line);border-radius:8px;
        padding:14px 16px;margin-bottom:14px}
select,button{font:inherit;border:1px solid var(--line);border-radius:6px;
              background:#fff;padding:4px 10px;cursor:pointer}
button:hover{border-color:var(--accent);color:var(--accent)}
button.primary{background:var(--accent);border-color:var(--accent);color:#fff}
button.primary:hover{opacity:.9;color:#fff}
button.danger{color:var(--bad);border-color:#f0c0bb}
button:disabled{opacity:.45;cursor:not-allowed}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{color:var(--dim);font-weight:600;font-size:12px;white-space:nowrap}
td.num{font-variant-numeric:tabular-nums;white-space:nowrap}
tr:last-child td{border-bottom:0}
.tag{display:inline-block;padding:1px 7px;border-radius:99px;font-size:12px;
     border:1px solid transparent;white-space:nowrap}
.t-done{background:#e6f4ef;color:var(--ok);border-color:#c6e6da}
.t-learn{background:#fff4e5;color:var(--warn);border-color:#f3ddbc}
.t-none{background:#f0f1f3;color:var(--dim);border-color:#e3e6eb}
.t-bank{background:#e6f4ef;color:var(--ok);border-color:#c6e6da}
.t-ai{background:#e8f0fe;color:var(--accent);border-color:#cddffb}
.t-low{background:#fff4e5;color:var(--warn);border-color:#f3ddbc}
.stem{color:var(--txt);max-width:420px;overflow:hidden;text-overflow:ellipsis;
      white-space:nowrap;display:block}
.ans{font-weight:600}
.muted{color:var(--dim);font-size:12px}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.job{display:flex;gap:10px;align-items:center;padding:6px 0;border-bottom:1px dashed var(--line)}
.job:last-child{border-bottom:0}
pre{background:#0f1115;color:#c9d1d9;padding:8px 10px;border-radius:6px;overflow:auto;
    font-size:12px;margin:6px 0 0;max-height:150px}
#toast{position:fixed;right:18px;bottom:18px;display:flex;flex-direction:column;gap:8px;z-index:99}
.toast{background:#1f2328;color:#fff;padding:8px 14px;border-radius:6px;font-size:13px;
       max-width:420px;box-shadow:0 4px 14px rgba(0,0,0,.18)}
.toast.bad{background:var(--bad)}
.empty{color:var(--dim);font-size:13px;padding:8px 0}
</style>
</head>
<body>
<header>
  <h1>第二平台控制台</h1>
  <select id="account"></select>
  <span class="muted" id="pager"></span>
  <span style="flex:1"></span>
  <span class="muted" id="clock"></span>
</header>
<main>
  <section id="sec-accounts"><h2>账号与任务</h2><div id="accounts"></div></section>

  <!-- 浏览器与会话：只做本机 cdp 形态（拉起/探活/检查登录），不碰平台以外的任何东西 -->
  <section>
    <div class="row" style="justify-content:space-between">
      <h2>浏览器与会话 <span class="muted" id="browser-meta"></span></h2>
      <div class="row">
        <button id="btn-bstart" onclick="startBrowser()">启动浏览器</button>
        <button id="btn-bstop" class="danger" onclick="post('/api/browser/stop')">停止浏览器</button>
        <button id="btn-scheck" class="primary" onclick="checkSession()">检查会话</button>
      </div>
    </div>
    <div id="browser"></div>
  </section>

  <section>
    <div class="row" style="justify-content:space-between">
      <h2>节进度 <span class="muted" id="sec-meta"></span></h2>
      <div class="row">
        <button onclick="refresh()">刷新节列表</button>
      </div>
    </div>
    <div id="sections"></div>
  </section>

  <section>
    <h2>本次作答 <span class="muted" id="work-meta"></span></h2>
    <div id="work"></div>
  </section>

  <section><h2>操作记录</h2><div id="ops"></div></section>
</main>
<div id="toast"></div>
<script>
let LABEL = "";
let BUSY = false;

const el = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function toast(msg, bad) {
  const d = document.createElement("div");
  d.className = "toast" + (bad ? " bad" : "");
  d.textContent = msg;
  el("toast").appendChild(d);
  setTimeout(() => d.remove(), bad ? 7000 : 4200);
}

async function post(url, body) {
  if (BUSY) return null;
  BUSY = true;
  try {
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(Object.assign({ label: LABEL }, body || {})) });
    const data = await r.json();
    toast(data.message || data.error || (data.ok ? "已提交" : "失败"), !data.ok);
    return data;
  } catch (e) {
    toast("请求失败：" + e.message, true);
    return null;
  } finally {
    BUSY = false;
    load();
  }
}

function stateTag(row) {
  if (row.state === "done") return '<span class="tag t-done">已完成</span>';
  if (row.state === "learning") return '<span class="tag t-learn">学习中</span>';
  return '<span class="tag t-none">未学习</span>';
}

function practiceCell(row) {
  if (row.practice_ready) {
    return '<span class="tag t-bank">可做</span> <span class="muted">#' +
      esc(row.exam_id) + "</span>";
  }
  return '<span class="tag t-none">未解锁</span>';
}

function renderAccounts(state) {
  const jobs = state.jobs || [];
  const acc = (state.accounts || []).find(a => a.label === state.label) || {};
  const mine = jobs.find(j => j.label === state.label);
  const running = mine ? '<span class="tag t-learn">' + esc(mine.kind) +
    " 运行中 · " + esc(mine.since) + "</span>" : '<span class="tag t-done">空闲</span>';
  let html = '<div class="row" style="margin-bottom:8px">' +
    "<b>" + esc(acc.display || state.label) + "</b>" + running +
    '<span class="muted">端口 ' + esc(acc.port) + " · 日上限 " +
    esc(acc.daily_sections) + " 节 / " + esc(acc.daily_minutes) + " 分钟</span></div>";
  if (!state.accounts_file) {
    html += '<div class="muted" style="margin-bottom:8px">当前是单账号回落' +
      "（data/account.local.json）。要跑多账号，新建 data/accounts.local.json，" +
      "一条账号一个 port / profile，再用右上角下拉切换。</div>";
  }
  html += '<div class="row">' +
    '<button class="primary" onclick="post(\'/api/study\',{max_sections:3})">开始学习（3 节）</button>' +
    '<button class="danger" onclick="post(\'/api/stop\')">停止</button>' +
    "</div>";
  if (mine && mine.tail && mine.tail.length) {
    html += "<pre>" + esc(mine.tail.join("\n")) + "</pre>";
  }
  el("accounts").innerHTML = html;
}

function renderBrowser(state) {
  const b = state.browser || {};
  const s = b.session || null;
  const managed = b.backend === "managed";
  let html = '<div class="row" style="margin-bottom:6px">' +
    '<span class="tag ' + (managed ? "t-ai" : "t-bank") + '">' +
    (managed ? "managed · 程序自起（容器形态）" : "cdp · 连你已开的 Chrome") + "</span>" +
    '<span class="tag ' + (b.port_alive ? "t-done" : "t-none") + '">端口 ' +
    esc(b.port) + (b.port_alive ? " 活着" : " 未监听") + "</span>" +
    (b.started_by_panel ? '<span class="tag t-learn">面板启动 · ' + esc(b.since) + "</span>" : "") +
    (s ? '<span class="tag ' + (s.logged_in ? "t-done" : "t-low") + '">页面上' +
      (s.logged_in ? "已登录" : "未登录") + "</span>" : "") +
    "</div>";
  const rows = [
    ["Chrome", b.chrome_ok ? esc(b.chrome) : "没找到（装 Chrome，或设环境变量 XXT_GXKS_CHROME）"],
    ["user-data-dir", b.profile ? esc(b.profile) + (b.profile_set ? "" : "（默认：账号表未配）")
      : "—"],
    ["managed profile", managed ? (b.managed_profile ? "已建" : "未建（首跑 = 未登录）") : "不适用"],
    ["上次检查", s ? esc(s.checked_at) + " · " + (s.logged_in ? "已登录" : "未登录") +
      (s.navigated ? "（本次导航了）" : "（零新请求）") + " · " + esc(s.url || "") : "没查过"],
  ];
  html += '<table><tbody>' + rows.map(r =>
    "<tr><th>" + esc(r[0]) + "</th><td class=muted>" + (r[1] || "") + "</td></tr>"
  ).join("") + "</tbody></table>";
  if (managed) {
    html += '<div class="muted" style="margin-top:6px">managed 后端不靠外部 Chrome：' +
      "浏览器由脚本自己起。要人工看窗口/点交卷，请把 config/settings.local.toml 的 " +
      "browser_backend 改回 cdp。</div>";
  }
  el("browser").innerHTML = html;
  el("browser-meta").textContent = b.can_start ? "" : (managed ? "" : "缺 Chrome，启动按钮不可用");
  el("btn-bstart").disabled = !!b.port_alive || !b.can_start;
  el("btn-bstop").disabled = !b.started_by_panel;
}

async function startBrowser() {
  toast("正在启动 Chrome（几秒内看新窗口）…");
  await post("/api/browser/start");
}

async function checkSession() {
  toast("正在检查会话（会访问平台一次，稍等）…");
  await post("/api/session/check");
}

function renderSections(state) {
  const box = el("sections");
  const data = state.sections;
  if (!data || !data.rows) {
    box.innerHTML = '<div class="empty">还没有节列表——点右上角「刷新节列表」' +
      "（会读整个课程，约 1 分钟）。</div>";
    el("sec-meta").textContent = "";
    return;
  }
  const s = data.summary || {};
  el("sec-meta").textContent = "读于 " + (data.read_at || "?") + " · 共 " +
    (s.sections || 0) + " 节 · 已完成 " + (s.done || 0) + " · 可做练习 " +
    (s.practice_ready || 0) + " · 学时 " + (s.minutes_done || 0) + "/" +
    (s.minutes_required || 0) + " 分钟";
  let html = "<table><thead><tr><th>节</th><th>学时要求</th><th>已完成</th>" +
    "<th>状态</th><th>练习</th><th>操作</th></tr></thead><tbody>";
  for (const row of data.rows) {
    const canWork = row.practice_ready;
    const workBtn = canWork
      ? '<button onclick="doWork(this)" data-id="' + esc(row.exam_id) +
        '" data-sec="' + esc(row.platform_chapter_id) + '" data-name="' +
        esc(row.name) + '">做练习</button>'
      : "";
    const studyBtn = row.state === "done" ? "" :
      '<button onclick="studyOne(' + esc(row.platform_chapter_id) +
      ')">学这节</button>';
    html += "<tr><td>" + esc(row.name) + " </td><td class=num>" +
      esc(row.required_minutes) + "分</td><td class=num>" + esc(row.done_minutes) +
      "分</td><td>" + stateTag(row) + "</td><td>" + practiceCell(row) +
      "</td><td>" + workBtn + " " + studyBtn + "</td></tr>";
  }
  box.innerHTML = html + "</tbody></table>";
}

function doWork(btn) {
  post("/api/work", { exercise_id: btn.dataset.id, section_id: btn.dataset.sec,
    section_name: btn.dataset.name });
}

function studyOne(sectionId) {
  post("/api/study", { max_sections: 1, sections: String(sectionId) });
}

function renderWork(state) {
  const w = state.work;
  const box = el("work");
  if (!w || !w.questions) {
    box.innerHTML = '<div class="empty">还没有作答记录。点节列表里的「做练习」后，' +
      "这里会逐题列出 AI 填了什么，方便你核对后再去交卷。</div>";
    el("work-meta").textContent = "";
    return;
  }
  const s = w.summary || {};
  el("work-meta").textContent = (w.section_name || w.section_id || "") +
    " · 练习 #" + (w.exercise_id || "") + " · " + (w.filled ? "已回填" : "仅出答案") +
    " · 题库 " + (s.bank || 0) + " / AI " + (s.ai || 0) +
    (state.auto_submit ? " · 自动交卷：开" : " · 自动交卷：关");
  let html = "<table><thead><tr><th>题号</th><th>题干</th><th>答案</th>" +
    "<th>来源</th><th>置信度</th></tr></thead><tbody>";
  for (const q of w.questions) {
    const src = q.source === "bank"
      ? '<span class="tag t-bank">题库</span>'
      : '<span class="tag t-ai">AI</span>';
    const conf = q.source === "bank" ? "—" :
      '<span class="tag ' + (q.confidence < 0.7 ? "t-low" : "t-ai") + '">' +
      Math.round((q.confidence || 0) * 100) + "%</span>";
    html += "<tr><td class=num>" + esc(q.ordinal) + "</td>" +
      '<td><span class="stem" title="' + esc(q.stem) + '">' + esc(q.stem) +
      '</span><span class="muted" title="' +
      esc((q.options || []).join(" / ")) + '">' +
      esc((q.options || []).length) + " 个选项</span></td>" +
      '<td class="ans">' + esc(q.answer_text) + "</td><td>" + src +
      "</td><td>" + conf + "</td></tr>";
  }
  html += "</tbody></table>";
  html += '<div class="muted" style="margin-top:8px">' +
    "答案已经填进浏览器页面（未提交）。请到那个 Chrome 窗口核对，确认无误后" +
    "<b>自己点「交卷」</b>——脚本连着浏览器时点交卷会点不动。</div>";
  box.innerHTML = html;
}

const ACTION_CN = { "study-start": "开始学习", "study-done": "学时达标",
  "study-stop": "停止学习", "work-open": "打开练习", "work-fill": "回填完成",
  "work-submit": "交卷", "sections-read": "刷新节列表", "stop": "停止",
  "browser-start": "启动浏览器", "browser-stop": "停止浏览器",
  "session-check": "检查会话", "error": "出错" };

function renderOps(state) {
  const rows = state.ops || [];
  if (!rows.length) { el("ops").innerHTML = '<div class="empty">暂无记录</div>'; return; }
  let html = "<table><thead><tr><th>时间</th><th>动作</th><th>结果</th>" +
    "<th>详情</th></tr></thead><tbody>";
  for (const r of rows) {
    const bad = r.result !== "ok";
    html += "<tr><td class=num>" + esc((r.at || "").slice(5)) + "</td><td>" +
      esc(ACTION_CN[r.action] || r.action) + "</td><td>" +
      (bad ? '<span class="tag t-low">失败</span>' : '<span class="tag t-done">成功</span>') +
      "</td><td>" + esc(r.detail) +
      (r.code ? ' <span class="muted">[' + esc(r.code) + "]</span>" : "") +
      "</td></tr>";
  }
  el("ops").innerHTML = html + "</tbody></table>";
}

function renderAccountsSelect(state) {
  const sel = el("account");
  const list = state.accounts || [];
  if (sel.dataset.sig !== list.map(a => a.label).join(",")) {
    sel.innerHTML = list.map(a =>
      '<option value="' + esc(a.label) + '">' + esc(a.display || a.label) +
      "</option>").join("");
    sel.dataset.sig = list.map(a => a.label).join(",");
  }
  sel.value = state.label || (list[0] && list[0].label) || "";
}

async function load() {
  try {
    const r = await fetch("/api/state?label=" + encodeURIComponent(LABEL));
    const state = await r.json();
    LABEL = state.label || "";
    renderAccountsSelect(state);
    renderAccounts(state);
    renderBrowser(state);
    renderSections(state);
    renderWork(state);
    renderOps(state);
    el("clock").textContent = new Date().toLocaleTimeString("zh-CN", { hour12: false }) +
      " · 每 4 秒刷新";
  } catch (e) {
    el("clock").textContent = "连接中断：" + e.message;
  }
}

el("account").addEventListener("change", (e) => { LABEL = e.target.value; load(); });
setInterval(load, 4000);
load();
</script>
</body>
</html>
"""
