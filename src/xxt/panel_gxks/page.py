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
      --ok:#12805c;--warn:#a86200;--bad:#b42318;--accent:#1f6feb;
      --shadow:0 1px 2px rgba(16,24,40,.04),0 1px 3px rgba(16,24,40,.06)}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);
     font:14px/1.55 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
header{display:flex;align-items:center;gap:12px;padding:10px 18px;background:var(--card);
       border-bottom:1px solid var(--line);position:sticky;top:0;z-index:9}
h1{font-size:16px;margin:0;font-weight:600}
h2{font-size:13px;margin:0 0 10px;color:var(--dim);font-weight:600;letter-spacing:.02em;
   display:flex;align-items:center;gap:8px;flex-wrap:wrap}
h2::before{content:"";width:3px;height:13px;border-radius:2px;background:var(--accent);
           flex:0 0 auto}
/* 12 栏网格：宽屏下只让"账号/浏览器"两张短信息卡并排；节进度/作答/记录三张表都吃宽度，
   并排会把「详情」列挤到只剩 45px（实测折行），所以一律独占整行。窄屏自动堆叠。 */
main{max-width:1280px;margin:0 auto;padding:16px 18px 60px;
     display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:14px;align-items:start}
section{grid-column:span 12;background:var(--card);border:1px solid var(--line);
        border-radius:10px;padding:14px 16px;box-shadow:var(--shadow)}
@media(min-width:1000px){
  #sec-accounts,#sec-browser{grid-column:span 6}
}
select,button{font:inherit;border:1px solid var(--line);border-radius:6px;
              background:#fff;padding:4px 10px;cursor:pointer;
              transition:background .12s,border-color .12s}
button:hover:not(:disabled){border-color:var(--accent);color:var(--accent);background:#f5f9ff}
/* 按钮三档：primary 主操作 / 默认 ghost / danger 弱化为白底红字 */
button.primary{background:var(--accent);border-color:var(--accent);color:#fff}
button.primary:hover:not(:disabled){background:#1a5fd0;color:#fff}
button.danger{color:var(--bad);border-color:#f0c0bb}
button.danger:hover:not(:disabled){border-color:var(--bad);color:var(--bad);
                                 background:#fff5f4}
button:disabled{opacity:.45;cursor:not-allowed}
.card-head{display:flex;align-items:flex-start;justify-content:space-between;
           gap:10px;flex-wrap:wrap;margin-bottom:2px}
.toolbar{display:flex;gap:6px;align-items:center;flex-wrap:wrap;justify-content:flex-end}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{color:var(--dim);font-weight:600;font-size:12px;white-space:nowrap}
th.num{text-align:right}
td.num{font-variant-numeric:tabular-nums;white-space:nowrap;text-align:right}
tr:last-child td{border-bottom:0}
/* 表头吸顶：表格裹一层 .table-wrap 才生效，长表格滚动时列名不丢 */
.table-wrap{overflow:auto;max-height:520px;border:1px solid var(--line);border-radius:8px}
.table-wrap thead th{position:sticky;top:0;z-index:1;background:#fafbfc;
                     box-shadow:inset 0 -1px 0 var(--line)}
tbody tr:hover{background:#f7f9fc}
/* 行首 3px 状态色条：已完成绿 / 学习中琥珀 / 未学灰 */
tr.s-done td:first-child{box-shadow:inset 3px 0 0 #86c9ac}
tr.s-learn td:first-child{box-shadow:inset 3px 0 0 #f2c272}
tr.s-none td:first-child{box-shadow:inset 3px 0 0 #d9dce1}
/* 多课程/多章节时的分组小标题行 */
tr.grp td{background:#f7f8fa;color:var(--dim);font-size:12px;font-weight:600}
/* 操作记录的前三列（时间/动作/结果）不折行，把余量全留给详情 */
#ops td:nth-child(-n+3){white-space:nowrap}
/* 两条进度条（节数 / 学时）：纯 div，不引图表库 */
.bars{display:flex;flex-wrap:wrap;gap:16px;margin:0 0 12px}
.bar{flex:1 1 240px;min-width:200px}
.bar .lab{display:flex;justify-content:space-between;gap:8px;font-size:12px;color:var(--dim);
          margin-bottom:4px}
.bar .track{height:8px;background:#eef0f3;border-radius:99px;overflow:hidden}
.bar .fill{height:100%;background:linear-gradient(90deg,#3fb27f,#12805c);border-radius:99px;
           transition:width .3s}
.bar .fill.warn{background:linear-gradient(90deg,#f2b45c,#a86200)}
/* 两个分区：待学（默认展开）/ 已完成（默认折叠）；折叠状态记在 JS 里，轮询重绘能还原 */
details.blk{border:1px solid var(--line);border-radius:8px;margin-top:10px;overflow:hidden}
details.blk>summary{cursor:pointer;padding:8px 10px;background:#fafbfc;font-size:13px;
                    display:flex;align-items:center;gap:8px;list-style:none;user-select:none}
details.blk>summary::-webkit-details-marker{display:none}
details.blk>summary::before{content:"\25B8";color:var(--dim);font-size:11px}
details.blk[open]>summary::before{content:"\25BE"}
details.blk.todo>summary{background:#fffaf2;color:var(--warn)}
details.blk>summary .sum{margin-left:auto;color:var(--dim);font-size:12px}
details.blk>.body{padding:8px 10px 10px}
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
  <section id="sec-browser">
    <div class="card-head">
      <h2>浏览器与会话 <span class="muted" id="browser-meta"></span></h2>
      <div class="toolbar">
        <button id="btn-bstart" onclick="startBrowser()">启动浏览器</button>
        <button id="btn-bstop" class="danger" onclick="post('/api/browser/stop')">停止浏览器</button>
        <button id="btn-scheck" class="primary" onclick="checkSession()">检查会话</button>
        <button id="btn-srestore" onclick="restoreSession()"
          title="把登录态快照里的 cookie 注入当前 Chrome（零密码）">恢复登录</button>
        <button id="btn-rlogin" onclick="reloginSession()"
          title="用账号密码填表登录一次；遇验证码立即停、转人工">重新登录</button>
      </div>
    </div>
    <div id="browser"></div>
  </section>

  <!-- 节进度：待学 / 已完成两个分区 + 两条进度条，见 renderSections -->
  <section id="sec-sections">
    <div class="card-head">
      <h2>节进度 <span class="muted" id="sec-meta"></span></h2>
      <div class="toolbar">
        <button id="btn-secrefresh" onclick="refreshSections()"
          title="重新读整个课程（含翻页），约 1 分钟">刷新节列表</button>
      </div>
    </div>
    <div id="sections"></div>
  </section>

  <section id="sec-work">
    <h2>本次作答 <span class="muted" id="work-meta"></span></h2>
    <div id="work"></div>
  </section>

  <section id="sec-ops"><h2>操作记录</h2><div id="ops"></div></section>
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
    ["登录态快照", (b.state && b.state.saved)
      ? esc(b.state.at) + " · " + esc(b.state.cookies) + " 条 cookie" +
        (b.state.session_level && b.state.session_level.length
          ? "（全是会话级，关浏览器即失 → 靠这份文件恢复）" : "")
      : "未存（先点「检查会话」或「重新登录」）"],
    ["账号密码", b.credentials ? "已配置（可自动重登）"
      : "未配置（data/account.local.json 里没有可用 name/pw）"],
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
  // 恢复/重登必须先有活着的浏览器（注入与填表都得占着那个页面）
  el("btn-srestore").disabled = !b.port_alive || !(b.state && b.state.saved);
  el("btn-rlogin").disabled = !b.port_alive || !b.credentials;
}

async function startBrowser() {
  toast("正在启动 Chrome（几秒内看新窗口）…");
  await post("/api/browser/start");
}

async function checkSession() {
  toast("正在检查会话（会访问平台一次，稍等）…");
  await post("/api/session/check");
}

async function restoreSession() {
  toast("正在注入登录态快照（不发请求，稍等）…");
  await post("/api/session/restore");
}

async function reloginSession() {
  toast("正在用账号密码重登一次（若弹验证码，请在那台浏览器窗口里手输）…");
  await post("/api/session/relogin");
}

const SEC_OPEN = { todo: true, done: false };  // 折叠状态记这里：4 秒轮询会整块重绘，不能只靠 DOM
let SEC_SIG = "";                              // 数据签名没变就不重建 → 折叠状态、选中文字都不被打扰

const numv = (v) => { const n = Number(v); return Number.isFinite(n) ? n : 0; };

function barHtml(label, done, total, note) {
  const pct = total > 0 ? Math.min(100, Math.round(done * 100 / total)) : 0;
  return '<div class="bar"><div class="lab"><span>' + esc(label) + '</span><span>' +
    esc(done) + " / " + esc(total) + (note ? " · " + esc(note) : "") +
    '</span></div><div class="track" role="progressbar" aria-valuemin="0" ' +
    'aria-valuemax="100" aria-valuenow="' + pct + '"><div class="fill' +
    (pct >= 100 || total === 0 ? "" : " warn") + '" style="width:' + pct +
    '%"></div></div></div>';
}

function secRowClass(row) {
  if (row.state === "done") return "s-done";
  return row.state === "learning" ? "s-learn" : "s-none";
}

// 一个分区内的表；已完成区不再重复"状态"列，也不给"学这节"
function secTableHtml(rows, withState) {
  const cols = withState ? 5 : 4;
  const multi = new Set(rows.map(r => r.category || "")).size > 1;
  let lastCat = null;
  let html = '<div class="table-wrap"><table><thead><tr><th>节</th>' +
    '<th class="num">学时（已学/要求）</th>' + (withState ? "<th>状态</th>" : "") +
    "<th>练习</th><th>操作</th></tr></thead><tbody>";
  for (const row of rows) {
    const cat = row.category || "";
    if (multi && cat !== lastCat) {          // 多课程/多章节才插分组小标题
      html += '<tr class="grp"><td colspan="' + cols + '">' + esc(cat) + "</td></tr>";
      lastCat = cat;
    }
    const workBtn = row.practice_ready
      ? '<button onclick="doWork(this)" data-id="' + esc(row.exam_id) +
        '" data-sec="' + esc(row.platform_chapter_id) + '" data-name="' +
        esc(row.name) + '">做练习</button>'
      : "";
    const studyBtn = row.state === "done" ? "" :
      '<button onclick="studyOne(' + esc(row.platform_chapter_id) + ')">学这节</button>';
    const ops = (workBtn + " " + studyBtn).trim();
    html += '<tr class="' + secRowClass(row) + '"><td>' + esc(row.name) +
      '</td><td class="num">' + esc(row.done_minutes) + " / " +
      esc(row.required_minutes) + "分</td>" +
      (withState ? "<td>" + stateTag(row) + "</td>" : "") +
      "<td>" + practiceCell(row) + "</td><td>" +
      (ops || '<span class="muted">—</span>') + "</td></tr>";
  }
  return html + "</tbody></table></div>";
}

function renderSections(state) {
  const box = el("sections");
  const data = state.sections;
  if (!data || !data.rows) {
    box.innerHTML = '<div class="empty">还没有节列表——点右上角「刷新节列表」' +
      "（会读整个课程，约 1 分钟）。</div>";
    el("sec-meta").textContent = "";
    SEC_SIG = "";
    return;
  }
  const rows = data.rows || [];
  const s = data.summary || {};
  el("sec-meta").textContent = "读于 " + (data.read_at || "?");

  const sig = [data.read_at, rows.length, s.done, s.practice_ready].join("|");
  if (sig === SEC_SIG) return;    // 数据没变就别重建，否则折叠区会每 4 秒弹回
  SEC_SIG = sig;

  // 学时口径：每节算 min(已学, 要求)。1.1 要求 45 而实际播了 236，
  // 直接求和会把总进度顶到 165%（543/340），看着像 bug
  const eff = rows.reduce((a, r) =>
    a + Math.min(numv(r.done_minutes), numv(r.required_minutes)), 0);
  const need = rows.reduce((a, r) => a + numv(r.required_minutes), 0);
  const played = rows.reduce((a, r) => a + numv(r.done_minutes), 0);
  const doneRows = rows.filter(r => r.state === "done");
  const todoRows = rows.filter(r => r.state !== "done")
    .sort((a, b) => (b.state === "learning") - (a.state === "learning") ||
                    numv(a.idx) - numv(b.idx));
  const todoLeft = todoRows.reduce((a, r) =>
    a + Math.max(0, numv(r.required_minutes) - numv(r.done_minutes)), 0);

  let html = '<div class="bars">' +
    barHtml("节数", doneRows.length, rows.length) +
    barHtml("学时", eff, need, played > eff ? "实际播放 " + played + " 分" : "") +
    "</div>";
  html += '<div class="row" style="margin-bottom:2px">' +
    '<span class="tag t-done">已完成 ' + doneRows.length + " 节</span>" +
    '<span class="tag ' + (todoRows.length ? "t-learn" : "t-none") + '">待学 ' +
    todoRows.length + " 节</span>" +
    '<span class="tag t-bank">可做练习 ' + (s.practice_ready || 0) + "</span>" +
    (data.pager ? '<span class="muted">平台分页 ' + esc(data.pager.current) + " / " +
      esc(data.pager.total) + "</span>" : "") +
    "</div>";

  html += '<details class="blk todo" data-blk="todo"' +
    (SEC_OPEN.todo ? " open" : "") + "><summary>待学" +
    '<span class="sum">' + todoRows.length + " 节 · 还差 " + todoLeft +
    ' 分钟</span></summary><div class="body">' +
    (todoRows.length ? secTableHtml(todoRows, true)
      : '<div class="empty">待学区已清空——这门课全部学完了。</div>') +
    "</div></details>";
  html += '<details class="blk" data-blk="done"' +
    (SEC_OPEN.done ? " open" : "") + "><summary>已完成" +
    '<span class="sum">' + doneRows.length + " 节 · " +
    doneRows.reduce((a, r) => a + numv(r.required_minutes), 0) +
    ' 分钟</span></summary><div class="body">' +
    (doneRows.length ? secTableHtml(doneRows, false)
      : '<div class="empty">还没有学完的节。</div>') +
    "</div></details>";
  box.innerHTML = html;
}

function refreshSections() {
  post("/api/sections/refresh");
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
  let html = '<div class="table-wrap"><table><thead><tr><th class="num">题号</th>' +
    "<th>题干</th><th>答案</th><th>来源</th><th>置信度</th></tr></thead><tbody>";
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
  html += "</tbody></table></div>";
  html += '<div class="muted" style="margin-top:8px">' +
    "答案已经填进浏览器页面（未提交）。请到那个 Chrome 窗口核对，确认无误后" +
    "<b>自己点「交卷」</b>——脚本连着浏览器时点交卷会点不动。</div>";
  box.innerHTML = html;
}

const ACTION_CN = { "study-start": "开始学习", "study-done": "学时达标",
  "study-stop": "停止学习", "work-open": "打开练习", "work-fill": "回填完成",
  "work-submit": "交卷", "sections-read": "刷新节列表", "stop": "停止",
  "browser-start": "启动浏览器", "browser-stop": "停止浏览器",
  "session-check": "检查会话", "session-restore": "恢复登录",
  "session-relogin": "重新登录", "error": "出错" };

function renderOps(state) {
  const rows = state.ops || [];
  if (!rows.length) { el("ops").innerHTML = '<div class="empty">暂无记录</div>'; return; }
  let html = '<div class="table-wrap"><table><thead><tr><th>时间</th><th>动作</th><th>结果</th>' +
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
  el("ops").innerHTML = html + "</tbody></table></div>";
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

// details 的 toggle 不冒泡，用捕获阶段统一记一次：轮询重绘后才能原样还原折叠状态
el("sections").addEventListener("toggle", (e) => {
  const d = e.target.closest("details[data-blk]");
  if (d) SEC_OPEN[d.dataset.blk] = d.open;
}, true);
el("account").addEventListener("change", (e) => { LABEL = e.target.value; load(); });
setInterval(load, 4000);
load();
</script>
</body>
</html>
"""
