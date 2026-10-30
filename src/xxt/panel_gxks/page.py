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
     font:15px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
header{display:flex;align-items:center;gap:12px;padding:12px 20px;background:var(--card);
       border-bottom:1px solid var(--line);position:sticky;top:0;z-index:9;
       box-shadow:0 1px 3px rgba(16,24,40,.05)}
h1{font-size:18px;margin:0;font-weight:700;letter-spacing:.01em}
h2{font-size:15px;margin:0 0 12px;color:var(--txt);font-weight:700;letter-spacing:.02em;
   display:flex;align-items:center;gap:8px;flex-wrap:wrap}
h2::before{content:"";width:4px;height:15px;border-radius:2px;background:var(--accent);
           flex:0 0 auto}
/* 12 栏网格：宽屏下「账号与任务」+「浏览器与会话」两张短信息卡各占 6 栏并排；
   节进度/作答/记录三张表一律独占整行（并排会把「详情」列挤到 45px 折行）。
   窄屏（<1000px）自动堆叠成单列。 */
main{max-width:1280px;margin:0 auto;padding:16px 18px 60px;
     display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:14px;align-items:start}
section{grid-column:span 12;background:var(--card);border:1px solid var(--line);
        border-radius:12px;padding:16px 20px;box-shadow:var(--shadow)}
/* 宽屏：账号表格需要宽度（9 列约 940px），所以**整行独占**；
   浏览器卡是短信息，跟在全宽表格下面当一条状态带。
   （原来的 6+6 并排在换成表格后不再合适：表格挤到 600px 会折行。） */
@media (min-width:1000px){
  /* 账号行多时在卡内滚动，而不是把整页拉到 2000px+ */
  #sec-accounts{max-height:calc(100vh - 120px);display:flex;
                flex-direction:column;min-height:320px}
  #sec-accounts>#accounts{overflow:auto;flex:1 1 auto}
}
/* 账号表格（替代原卡片视图）：见下方 .acct-tbl 一组规则。
   卡片每张 ~222px 高、20 个账号要 2300px；表格每行 ~44px、20 个约 1040px 且能在固定区滚动。 */
/* ---------------------------------------------------------------- 账号表格
   为什么从卡片改成表格（用户 2026-10-30 提议）：卡片每张 ~222px 高，6 个账号 ~700px、
   20 个账号 ~2300px；表格每行 ~44px，6 个 ~310px、20 个 ~1040px（固定区内滚动）。
   同样空间能多放 2~3 倍账号，而且**列对齐才能横向比较**（谁的额度用完了、谁端口没开）。 */
.acct-tools{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:0 0 10px}
.acct-tools input[type=search]{font:inherit;flex:1 1 260px;min-width:180px;
  border:1px solid var(--line);border-radius:6px;padding:6px 10px}
.acct-tools select{font:inherit;border:1px solid var(--line);border-radius:6px;
  background:#fff;padding:5px 8px;cursor:pointer}
.chips{display:flex;gap:4px;flex-wrap:wrap}
.acct-batch{display:flex;gap:6px;align-items:center;flex-wrap:wrap;
            min-height:0;margin:0 0 8px}
.acct-batch:not(:empty){padding:8px 10px;background:#f7f9fc;border:1px solid var(--line);
                        border-radius:8px}
table.acct-tbl{font-size:13.5px}
table.acct-tbl th{font-size:12.5px;padding:6px 8px}
table.acct-tbl td{padding:6px 8px;vertical-align:middle}
table.acct-tbl tbody tr{cursor:pointer}
table.acct-tbl tbody tr.cur{background:#f0f6ff;
  box-shadow:inset 3px 0 0 var(--accent)}
table.acct-tbl tbody tr.off{opacity:.55}
table.acct-tbl td.c-name b{font-size:14.5px}
table.acct-tbl td.c-name .sub{color:var(--dim);font-size:12px;display:block;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:200px}
table.acct-tbl td.c-today b{font-size:15px;color:var(--accent);
  font-variant-numeric:tabular-nums}
table.acct-tbl td.c-ops{white-space:nowrap;text-align:right}
table.acct-tbl td.c-ops button{margin-left:4px}
/* 行内小进度条：30px 宽足够看出"快满了" */
.mini-bar{display:inline-block;width:34px;height:6px;border-radius:99px;
  background:#eef0f3;overflow:hidden;vertical-align:middle;margin-left:5px}
.mini-bar i{display:block;height:100%;background:#3fb27f}
.mini-bar i.warn{background:#f2b45c}
/* 悬停详情浮层：固定定位、不挡鼠标（pointer-events:none），所以移开一定自动隐藏 */
.acct-detail{position:fixed;z-index:60;max-width:330px;background:#1f2328;color:#fff;
  border-radius:8px;padding:10px 12px;font-size:12.5px;line-height:1.6;
  box-shadow:0 8px 24px rgba(0,0,0,.24);pointer-events:none}
.acct-detail .dk{color:#9aa4b2;margin-right:6px}
.acct-detail .row2{display:flex;gap:0;flex-wrap:wrap}
.acct-detail b{color:#fff}
.acct-detail .sep{border-top:1px solid #3a4048;margin:6px 0}
/* 浏览器卡的紧凑信息区：短项并排三列，长项各占一行并截断。
   原来 6 行竖表把整张卡撑到 396px，右侧一长条空白（实测 6 个账号时差 1116px）。 */
.binfo{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
       gap:8px 14px;margin:2px 0 8px}
.binfo .bi{display:flex;flex-direction:column;gap:2px;min-width:0}
.binfo .k{color:var(--dim);font-size:12.5px;white-space:nowrap}
.binfo .v{font-size:13.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bi-line{display:flex;gap:10px;font-size:13.5px;padding:4px 0;
         border-top:1px dashed var(--line);min-width:0}
.bi-line .k{color:var(--dim);white-space:nowrap;flex:0 0 84px}
.bi-line .v{flex:1 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis;
            white-space:nowrap}
select,button{font:inherit;border:1px solid var(--line);border-radius:6px;
              background:#fff;padding:5px 12px;cursor:pointer;
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
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{color:var(--dim);font-weight:600;font-size:13px;white-space:nowrap}
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
.bar .lab{display:flex;justify-content:space-between;gap:8px;font-size:13px;color:var(--dim);
          margin-bottom:5px}
.bar .lab b{color:var(--txt);font-size:15px;font-variant-numeric:tabular-nums}
.bar .track{height:10px;background:#eef0f3;border-radius:99px;overflow:hidden}
.bar .fill{height:100%;background:linear-gradient(90deg,#3fb27f,#12805c);border-radius:99px;
           transition:width .3s}
.bar .fill.warn{background:linear-gradient(90deg,#f2b45c,#a86200)}
/* 两个分区：待学（默认展开）/ 已完成（默认折叠）；折叠状态记在 JS 里，轮询重绘能还原 */
details.blk{border:1px solid var(--line);border-radius:8px;margin-top:10px;overflow:hidden}
details.blk>summary{cursor:pointer;padding:10px 12px;background:#fafbfc;font-size:14px;
                    display:flex;align-items:center;gap:8px;list-style:none;user-select:none}
details.blk>summary::-webkit-details-marker{display:none}
details.blk>summary::before{content:"\25B8";color:var(--dim);font-size:11px}
details.blk[open]>summary::before{content:"\25BE"}
details.blk.todo>summary{background:#fffaf2;color:var(--warn)}
details.blk>summary .sum{margin-left:auto;color:var(--dim);font-size:13px}
details.blk>.body{padding:8px 10px 10px}
.tag{display:inline-block;padding:2px 8px;border-radius:99px;font-size:12.5px;
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
.muted{color:var(--dim);font-size:12.5px}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.job{display:flex;gap:10px;align-items:center;padding:6px 0;border-bottom:1px dashed var(--line)}
.job:last-child{border-bottom:0}
pre{background:#0f1115;color:#c9d1d9;padding:10px 12px;border-radius:6px;overflow:auto;
    font-size:12.5px;margin:8px 0 0;max-height:150px}
#toast{position:fixed;right:18px;bottom:18px;display:flex;flex-direction:column;gap:8px;z-index:99}
.toast{background:#1f2328;color:#fff;padding:8px 14px;border-radius:6px;font-size:13px;
       max-width:420px;box-shadow:0 4px 14px rgba(0,0,0,.18)}
.toast.bad{background:var(--bad)}
.empty{color:var(--dim);font-size:14px;padding:10px 0}
/* 账号编辑对话框：原生 <dialog>，不引任何 UI 库（单文件 exe 不能带静态资源） */
dialog{border:1px solid var(--line);border-radius:12px;padding:0;max-width:520px;width:92%;
       box-shadow:0 12px 40px rgba(16,24,40,.18)}
dialog::backdrop{background:rgba(16,24,40,.32)}
.dlg-head{display:flex;align-items:center;justify-content:space-between;gap:10px;
          padding:14px 18px;border-bottom:1px solid var(--line)}
.dlg-head b{font-size:16px}
.dlg-body{padding:14px 18px}
.dlg-foot{display:flex;gap:8px;justify-content:flex-end;padding:12px 18px;
          border-top:1px solid var(--line);background:#fafbfc}
.fld{display:grid;grid-template-columns:96px 1fr;gap:6px 12px;align-items:center;
     margin-bottom:10px;font-size:14px}
.fld>label{color:var(--dim);text-align:right;white-space:nowrap}
.fld input[type=text],.fld input[type=number],.fld input[type=password]{
  font:inherit;border:1px solid var(--line);border-radius:6px;padding:6px 10px;width:100%}
.fld .hint{grid-column:2;color:var(--dim);font-size:12.5px;line-height:1.45}
button.mini{padding:3px 9px;font-size:13px}
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
  <!-- 账号列表：表格（一行一个账号），鼠标悬停行右侧浮出详情，点行 = 切换下方四个区块。
       工具栏**不随 4 秒轮询重绘**（否则搜索框会掉焦点），只重绘 #accounts。 -->
  <section id="sec-accounts">
    <div class="card-head">
      <h2>账号与任务 <span class="muted" id="accounts-meta"></span></h2>
      <div class="toolbar">
        <button class="primary" onclick="openAccount()"
          title="新增一个账号：代号 + 端口 + 日额度（凭据可留空，之后再填）">＋ 新增账号</button>
      </div>
    </div>
    <div class="acct-tools">
      <input id="acct-q" type="search" placeholder="搜索：名称 / 代号 / 端口 / 学校 / 学号 / 自定义字段"
             autocomplete="off">
      <select id="acct-status" title="按状态筛选">
        <option value="">状态：全部</option>
        <option value="running">运行中</option>
        <option value="idle">空闲</option>
        <option value="off">已停用</option>
        <option value="full">额度用完</option>
      </select>
      <select id="acct-port" title="按端口是否监听筛选">
        <option value="">端口：全部</option>
        <option value="alive">活着</option>
        <option value="dead">未监听</option>
      </select>
      <select id="acct-cred" title="按是否配置账号密码筛选">
        <option value="">凭据：全部</option>
        <option value="yes">已配置</option>
        <option value="no">未配置</option>
      </select>
      <span class="chips">
        <button class="mini" type="button" onclick="quickFilter('running')">运行中</button>
        <button class="mini" type="button" onclick="quickFilter('full')">额度用完</button>
        <button class="mini" type="button" onclick="quickFilter('dead')">端口没开</button>
        <button class="mini" type="button" onclick="quickFilter('noconf')">无凭据</button>
        <button class="mini" type="button" onclick="clearFilter()">清空</button>
      </span>
    </div>
    <div class="acct-batch" id="acct-batch"></div>
    <div id="accounts"></div>
  </section>

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
<!-- 悬停详情浮层：鼠标划到某一行时浮在行右侧，移开自动隐藏（纯展示，无可点元素） -->
<div id="acct-detail" class="acct-detail" hidden></div>

<!-- 账号编辑对话框：一个表单同时用于新增与修改（修改时回填，密码框永远留空） -->
<dialog id="dlg">
  <form method="dialog" onsubmit="return false">
    <div class="dlg-head">
      <b id="dlg-title">新增账号</b>
      <button class="mini" onclick="closeAccount()" type="button">关闭</button>
    </div>
    <div class="dlg-body">
      <div class="fld">
        <label for="f-label">代号</label>
        <input id="f-label" type="text" placeholder="jia" autocomplete="off">
        <div class="hint">只允许 a-z 0-9 _ -，用作文件名与日志标签；真名请填「显示名」</div>
      </div>
      <div class="fld">
        <label for="f-display">显示名</label>
        <input id="f-display" type="text" placeholder="甲同学" autocomplete="off">
        <div class="hint">只用于界面展示，不会写进文件名</div>
      </div>
      <div class="fld">
        <label for="f-seq">序号</label>
        <input id="f-seq" type="number" min="1" placeholder="自动生成">
        <div class="hint">自动生成（1/2/3…），可改；只作展示与排序</div>
      </div>
      <div class="fld">
        <label for="f-school">学校</label>
        <input id="f-school" type="text" placeholder="示范学院" autocomplete="off">
        <div class="hint">非必填；真名一律用代号，见 AGENTS</div>
      </div>
      <div class="fld">
        <label for="f-major">专业</label>
        <input id="f-major" type="text" placeholder="计算机科学与技术" autocomplete="off">
        <div class="hint">非必填</div>
      </div>
      <div class="fld">
        <label for="f-grade">年级</label>
        <input id="f-grade" type="text" placeholder="2024 级" autocomplete="off">
        <div class="hint">非必填</div>
      </div>
      <div class="fld">
        <label for="f-student">学号</label>
        <input id="f-student" type="text" placeholder="2602220131" autocomplete="off">
        <div class="hint">非必填；只存本地，不入库</div>
      </div>
      <div class="fld">
        <label for="f-port">调试端口</label>
        <div class="row" style="gap:6px">
          <input id="f-port" type="number" min="1024" max="65535" placeholder="9222">
          <button class="mini" type="button" onclick="genPort()">随机</button>
        </div>
        <div class="hint">一账号一端口，不能重复。点「随机」自动分配一个未占用的</div>
      </div>
      <div class="fld">
        <label for="f-sections">日节数上限</label>
        <input id="f-sections" type="number" min="0" max="40" placeholder="10">
        <div class="hint">0 = 不限。<b>按账号合计</b>：多门课共用这一个池子</div>
      </div>
      <div class="fld">
        <label for="f-minutes">日时长上限</label>
        <input id="f-minutes" type="number" min="0" max="1440" placeholder="600">
        <div class="hint">分钟；0 = 不限。建议 ≥ 日节数 × 60，否则时长先卡住</div>
      </div>
      <div class="fld">
        <label for="f-name">账号</label>
        <input id="f-name" type="text" autocomplete="off" placeholder="留空 = 不自动重登">
        <div class="hint" id="hint-name">留空则不写入；已有凭据时留空 = 保持原样</div>
      </div>
      <div class="fld">
        <label for="f-pw">密码</label>
        <input id="f-pw" type="password" autocomplete="new-password" placeholder="留空 = 不改">
        <div class="hint">密码只写进 data/accounts.local.json（不入库），面板永不回显</div>
      </div>
      <div class="fld">
        <label for="f-enabled">启用</label>
        <span><input id="f-enabled" type="checkbox" checked> 参与调度</span>
      </div>
      <div class="fld">
        <label>自定义字段</label>
        <div id="f-custom-box" class="custom-box"></div>
      </div>
      <div class="muted" id="dlg-err" style="color:var(--bad)"></div>
    </div>
    <div class="dlg-foot">
      <button type="button" onclick="closeAccount()">取消</button>
      <button class="primary" type="button" onclick="saveAccount()">保存</button>
    </div>
  </form>
</dialog>
<script>
let LABEL = "";
let BUSY = false;
let STATE = null;              // 最近一次 /api/state 的回包（编辑对话框回填用）

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

// ---------------------------------------------------------------- 账号表格
// 为什么是表格：卡片每张 ~222px 高，20 个账号要 2300px；表格每行 ~44px，20 个约 1040px
// 且能在固定区里滚动。列对齐才能横向比较（谁的额度用完了、谁端口没开）。
//
// 选中集合记在 JS 里：4 秒轮询会整块重绘表格，状态不能只靠 DOM（与 SEC_OPEN 同理）。
const SEL = new Set();
const FILTER = { q: "", status: "", port: "", cred: "" };

function acctStatus(c) {
  if (!c.enabled) return "off";
  if (c.job) return "running";
  const t = c.today || {}, lim = c.limits || {};
  if (Number(lim.sections) > 0 && t.left_sections === 0) return "full";
  return "idle";
}

function acctStatusTag(c) {
  const s = acctStatus(c);
  if (s === "off") return '<span class="tag t-none">已停用</span>';
  if (s === "running") return '<span class="tag t-learn">运行中</span>';
  if (s === "full") return '<span class="tag t-low">额度用完</span>';
  return '<span class="tag t-done">空闲</span>';
}

// 搜索：名称/代号/端口/课程/序号/学校/专业/年级/学号/自定义字段 全都能搜
function acctSearchText(c, accRow) {
  const pf = (accRow && accRow.profile_fields) || {};
  const cf = (accRow && accRow.custom_fields) || {};
  return [c.label, c.display, c.port, c.course,
          pf.seq, pf.school, pf.major, pf.grade, pf.student_id,
          Object.keys(cf).join(" "), Object.values(cf).join(" ")]
    .map((v) => String(v === undefined || v === null ? "" : v)).join(" ").toLowerCase();
}

function visibleCards(state) {
  const cards = state.cards || [];
  const accounts = state.accounts || [];
  const q = (FILTER.q || "").trim().toLowerCase();
  return cards.filter((c) => {
    const accRow = accounts.find((a) => a.label === c.label) || null;
    if (q && acctSearchText(c, accRow).indexOf(q) < 0) return false;
    if (FILTER.status && acctStatus(c) !== FILTER.status) return false;
    if (FILTER.port === "alive" && !c.port_alive) return false;
    if (FILTER.port === "dead" && c.port_alive) return false;
    if (FILTER.cred === "yes" && !c.credentials) return false;
    if (FILTER.cred === "no" && c.credentials) return false;
    return true;
  });
}

function miniBar(done, total) {
  const pct = total > 0 ? Math.min(100, Math.round(done * 100 / total)) : 0;
  return '<span class="mini-bar"><i class="' + (pct >= 100 ? "" : "warn") +
    '" style="width:' + pct + '%"></i></span>';
}

// 一行 = 一个账号。学校/专业放名称下面那行小字（用户要的"一行+展开"里的静态摘要）。
function acctTr(c, accRow, cur) {
  const t = c.today || {}, lim = c.limits || {}, sec = c.sections || {};
  const snap = c.snapshot || {};
  const pf = (accRow && accRow.profile_fields) || {};
  const active = c.label === cur;
  const st = acctStatus(c);
  const secLim = Number(lim.sections) || 0;
  const left = t.left_sections === null || t.left_sections === undefined
    ? Infinity : Number(t.left_sections) || 0;
  const sub = [pf.school, pf.major, pf.grade].map((v) => String(v || "").trim())
    .filter(Boolean).join(" · ");
  let html = '<tr class="' + (active ? "cur " : "") + (c.enabled ? "" : "off") +
    '" data-label="' + esc(c.label) + '">';
  html += '<td><input type="checkbox" class="acct-pick"' +
    (SEL.has(c.label) ? " checked" : "") + ' data-label="' + esc(c.label) + '"></td>';
  html += '<td class="num muted">' + esc(pf.seq || "") + "</td>";
  html += '<td class="c-name"><b>' + esc(c.display || c.label) + "</b>" +
    (sub ? '<span class="sub" title="' + esc(sub) + '">' + esc(sub) + "</span>" : "") +
    "</td>";
  html += '<td>' + esc(c.port) + " " + (c.port_alive
    ? '<span class="tag t-done">活</span>' : '<span class="tag t-none">停</span>') + "</td>";
  html += "<td>" + acctStatusTag(c) + "</td>";
  html += '<td class="c-today"><b>' + esc(t.sections || 0) + "</b>/" +
    (secLim > 0 ? esc(secLim) : "不限") + " 节 " +
    miniBar(Number(t.sections) || 0, secLim > 0 ? secLim : Math.max(1, Number(t.sections) || 1)) +
    '<span class="muted"> ' + esc(Math.round(Number(t.minutes) || 0)) + " 分</span></td>";
  html += '<td class="muted">' + (sec.total
    ? esc(sec.done) + "/" + esc(sec.total) + " 节"
    : "—") + "</td>";
  html += '<td class="muted">' + (snap.saved ? esc((snap.at || "").slice(5, 16)) : "无快照") +
    (c.credentials ? "" : ' <span class="tag t-low">无凭据</span>') + "</td>";
  // 操作列：按钮自己处理点击；行点击由下面的委托统一管（见 #accounts 的 click 监听）
  html += '<td class="c-ops">';
  if (c.job) {
    html += '<button class="mini danger" onclick="stopJob(\'' + esc(c.label) +
      '\')">停止</button>';
  } else if (!c.enabled) {
    html += '<button class="mini" disabled title="账号已停用；先点「启用」">已停用</button>';
  } else if (!c.port_alive) {
    html += '<button class="mini" disabled title="先点上方「启动浏览器」">浏览器没开</button>';
  } else if (left <= 0) {
    html += '<button class="mini" disabled title="今日额度已用完（账本按账号统计）">额度用完</button>';
  } else {
    const n = left === Infinity ? 3 : Math.min(3, left);
    html += '<button class="mini primary" onclick="startStudy(\'' + esc(c.label) + "'," + n +
      ')" title="今日还剩 ' + (left === Infinity ? "不限" : left) + ' 节">学习 ' + n +
      " 节</button>";
  }
  html += '<button class="mini" onclick="refreshSectionsFor(\'' + esc(c.label) + "','" +
    esc(c.course_value || "") + '\')" title="重读这门课的节列表（约 1 分钟）">刷新</button>';
  html += '<button class="mini" onclick="openAccount(\'' + esc(c.label) +
    '\')" title="改端口 / 日额度 / 学校等 / 凭据">✎</button>';
  html += '<button class="mini" onclick="toggleAccount(\'' + esc(c.label) + "'," +
    (c.enabled ? "false" : "true") + ')" title="' +
    (c.enabled ? "停用（不参与调度）" : "重新启用") + '">' +
    (c.enabled ? "停用" : "启用") + "</button>";
  html += '<button class="mini danger" onclick="deleteAccount(\'' + esc(c.label) +
    '\')" title="只删账号表里这一条，快照与 profile 保留">删除</button>';
  html += "</td></tr>";
  return html;
}

function renderBatchBar(shown) {
  const box = el("acct-batch");
  if (!box) return;
  const n = SEL.size;
  if (!n) {
    box.innerHTML = '<span class="muted">勾选左侧复选框可批量操作（' +
      shown.length + " 个账号可见）</span>";
    return;
  }
  box.innerHTML = '<b>已选 ' + n + ' 个</b>' +
    '<button class="mini primary" onclick="batchAction(\'start\')">开始学习</button>' +
    '<button class="mini" onclick="batchAction(\'stop\')">停止</button>' +
    '<button class="mini" onclick="batchAction(\'enable\')">启用</button>' +
    '<button class="mini" onclick="batchAction(\'disable\')">停用</button>' +
    '<button class="mini danger" onclick="batchAction(\'delete\')">删除</button>' +
    '<button class="mini" onclick="clearSel()">取消选择</button>';
}

function renderAccounts(state) {
  const cards = state.cards || [];
  const accounts = state.accounts || [];
  const shown = visibleCards(state);
  el("accounts-meta").textContent = cards.length
    ? (shown.length === cards.length ? cards.length + " 个账号"
       : shown.length + " / " + cards.length + " 个账号")
    : "";
  renderBatchBar(shown);

  // 选中集合里已不存在的账号（被删了）要清掉，否则批量会带着幽灵代号去请求
  for (const lab of [...SEL]) {
    if (!cards.some((c) => c.label === lab)) SEL.delete(lab);
  }

  if (!cards.length) {
    el("accounts").innerHTML = '<div class="empty">还没有账号——点右上角「＋ 新增账号」，' +
      "或检查 data/accounts.local.json / data/account.local.json。</div>";
    return;
  }
  if (!shown.length) {
    el("accounts").innerHTML = '<div class="empty">没有符合筛选条件的账号——' +
      "点「清空」恢复全部。</div>";
    return;
  }

  // 多账号时给一行「合计」：单看每行无法回答"今天总共学了多少"
  const tot = shown.reduce((a, c) => {
    const t = c.today || {}, lim = c.limits || {};
    a.sections += Number(t.sections) || 0;
    a.minutes += Number(t.minutes) || 0;
    a[acctStatus(c)] = (a[acctStatus(c)] || 0) + 1;
    return a;
  }, { sections: 0, minutes: 0, off: 0, running: 0, full: 0, idle: 0 });
  let head = "";
  if (shown.length > 1 || cards.length > 1) {
    head = '<div class="row" style="margin-bottom:8px">' +
      '<span class="tag t-ai">合计 ' + tot.sections + " 节 · " +
      Math.round(tot.minutes) + " 分钟</span>" +
      '<span class="tag t-learn">运行中 ' + (tot.running || 0) + "</span>" +
      '<span class="tag t-none">空闲 ' + (tot.idle || 0) + "</span>" +
      (tot.full ? '<span class="tag t-low">额度用完 ' + tot.full + "</span>" : "") +
      (tot.off ? '<span class="tag t-none">停用 ' + tot.off + "</span>" : "") +
      "</div>";
  }
  const allChecked = shown.length > 0 && shown.every((c) => SEL.has(c.label));
  const rows = shown.map((c) => acctTr(
    c, accounts.find((a) => a.label === c.label) || null, state.label)).join("");
  let html = head + '<div class="table-wrap" style="max-height:none">' +
    '<table class="acct-tbl"><thead><tr>' +
    '<th style="width:30px"><input type="checkbox" id="acct-all"' +
    (allChecked ? " checked" : "") + ' title="全选当前可见"></th>' +
    '<th class="num" style="width:38px">#</th>' +
    "<th>账号</th>" +
    '<th style="width:84px">端口</th>' +
    '<th style="width:92px">状态</th>' +
    '<th style="width:150px">今日</th>' +
    '<th style="width:92px">课程</th>' +
    '<th style="width:120px">登录</th>' +
    '<th style="width:260px">操作</th>' +
    "</tr></thead><tbody>" + rows + "</tbody></table></div>";
  if (!state.accounts_file) {
    html += '<div class="muted" style="margin-top:8px">当前是单账号回落' +
      "（data/account.local.json）。点「＋ 新增账号」会自动建 data/accounts.local.json，" +
      "一条账号一个 port / profile。</div>";
  }
  const mine = (state.jobs || []).find((j) => j.label === state.label);
  if (mine && mine.tail && mine.tail.length) {
    html += "<pre>" + esc(mine.tail.join("\n")) + "</pre>";
  }
  el("accounts").innerHTML = html;
}

// ---------------------------------------------------------------- 悬停详情
// 鼠标划到行上 → 浮层出现在行右侧（空间不够就放左侧）；移开自动隐藏。
// 浮层 `pointer-events:none`：它不拦鼠标，所以"移开行"一定触发 mouseout，不会卡住。
function acctDetailHtml(c, accRow) {
  const t = c.today || {}, lim = c.limits || {}, sec = c.sections || {};
  const snap = c.snapshot || {};
  const pf = (accRow && accRow.profile_fields) || {};
  const cf = (accRow && accRow.custom_fields) || {};
  const L = (k, v) => v === undefined || v === null || String(v).trim() === "" ? ""
    : '<div><span class="dk">' + esc(k) + "</span>" + esc(v) + "</div>";
  let h = '<div><b>' + esc(c.display || c.label) + "</b>" +
    '<span class="dk" style="margin-left:8px">' + esc(c.label) + "@" + esc(c.port) +
    "</span></div>";
  h += '<div class="sep"></div>';
  h += L("序号", pf.seq) + L("学校", pf.school) + L("专业", pf.major) +
    L("年级", pf.grade) + L("学号", pf.student_id);
  const cfKeys = Object.keys(cf).filter((k) => String(cf[k] || "").trim() !== "");
  if (cfKeys.length) {
    h += '<div class="sep"></div>' +
      cfKeys.map((k) => L(k, cf[k])).join("");
  }
  h += '<div class="sep"></div>';
  h += L("今日", (t.sections || 0) + " / " + (Number(lim.sections) > 0 ? lim.sections : "不限") +
    " 节 · " + Math.round(Number(t.minutes) || 0) + " / " +
    (Number(lim.minutes) > 0 ? lim.minutes : "不限") + " 分");
  h += L("课程", sec.total ? (c.course || "未命名") + " " + sec.done + "/" + sec.total +
    " 节（读于 " + (sec.read_at || "?") + "）" : "还没读过节列表");
  h += L("登录快照", snap.saved ? (snap.at || "") + " · " + (snap.cookies || 0) + " 条"
    : "无");
  h += L("账号密码", c.credentials ? "已配置（可自动重登）" : "未配置（掉线即停）");
  h += L("凭据文件", "data/accounts.local.json");
  if (c.job) h += L("正在跑", c.job.kind + " · 起于 " + c.job.since);
  return h;
}

function showAcctDetail(label) {
  const box = el("acct-detail");
  const row = document.querySelector('tr[data-label="' + label + '"]');
  if (!box || !row) return;
  const c = ((STATE && STATE.cards) || []).find((x) => x.label === label);
  if (!c) return;
  const accRow = ((STATE && STATE.accounts) || []).find((a) => a.label === label) || null;
  box.innerHTML = acctDetailHtml(c, accRow);
  box.hidden = false;
  const r = row.getBoundingClientRect();
  const bw = box.offsetWidth, bh = box.offsetHeight;
  let x = r.right + 10;
  if (x + bw > window.innerWidth - 8) x = Math.max(8, r.left - bw - 10);
  let y = r.top;
  if (y + bh > window.innerHeight - 8) y = Math.max(8, window.innerHeight - bh - 8);
  box.style.left = x + "px";
  box.style.top = y + "px";
}

function hideAcctDetail() {
  const box = el("acct-detail");
  if (box) box.hidden = true;
}

// ---------------------------------------------------------------- 选择 / 筛选 / 批量
function syncSel() {
  const shown = visibleCards(STATE || {});
  renderBatchBar(shown);
  const all = el("acct-all");
  if (all) all.checked = shown.length > 0 && shown.every((c) => SEL.has(c.label));
}

function clearSel() {
  SEL.clear();
  load();
}

async function batchAction(action) {
  const labels = [...SEL];
  if (!labels.length) return;
  const CN = { start: "开始学习", stop: "停止", enable: "启用",
               disable: "停用", delete: "删除" };
  if (action === "delete") {
    if (!confirm("删除选中的 " + labels.length + " 个账号？\n\n" +
      labels.join("、") + "\n\n只删账号表里这些条目；登录态快照与 profile 目录会保留。")) {
      return;
    }
  } else if (!confirm("对选中的 " + labels.length + " 个账号执行「" +
      CN[action] + "」？")) {
    return;
  }
  const body = { labels: labels, action: action, confirm: true };
  if (action === "start") body.max_sections = 3;
  const data = await apiPost("/api/accounts/batch", body);
  toast(data.message || data.error || "已提交", !data.ok);
  if (data.ok && action === "delete") SEL.clear();
  load();
}

function quickFilter(kind) {
  const s = el("acct-status"), p = el("acct-port"), c = el("acct-cred");
  if (s) s.value = ""; if (p) p.value = ""; if (c) c.value = "";
  if (kind === "running" && s) s.value = "running";
  else if (kind === "full" && s) s.value = "full";
  else if (kind === "dead" && p) p.value = "dead";
  else if (kind === "noconf" && c) c.value = "no";
  readFilter();
  load();
}

function clearFilter() {
  const q = el("acct-q");
  if (q) q.value = "";
  for (const id of ["acct-status", "acct-port", "acct-cred"]) {
    const e = el(id);
    if (e) e.value = "";
  }
  readFilter();
  load();
}

function readFilter() {
  FILTER.q = (el("acct-q") || {}).value || "";
  FILTER.status = (el("acct-status") || {}).value || "";
  FILTER.port = (el("acct-port") || {}).value || "";
  FILTER.cred = (el("acct-cred") || {}).value || "";
}


function pick(label) {
  if (!label || label === LABEL) return;
  LABEL = label;
  el("account").value = label;
  SEC_SIG = "";          // 换账号必须重建节进度（不同账号的签名可能撞车）
  load();
}

function startStudy(label, n) {
  post("/api/study", { label: label, max_sections: n });
}

function stopJob(label) {
  post("/api/stop", { label: label });
}

// ---------------------------------------------------------------- 账号管理
// 新增与修改共用一个对话框：`EDITING` 为空 = 新增，否则是被改的那个代号。
// 密码框永远不回填——面板拿不到密码（后端只回布尔），回填等于自欺。
let EDITING = "";

function openAccount(label) {
  EDITING = label || "";
  const c = (STATE && (STATE.cards || []).find(x => x.label === label)) || null;
  // 从 /api/state 的 accounts 里取 profile_fields / custom_fields
  const accRow = (STATE && (STATE.accounts || []).find(x => x.label === label)) || null;
  const pf = (accRow && accRow.profile_fields) || {};
  const cf = (accRow && accRow.custom_fields) || {};
  el("dlg-title").textContent = label ? ("编辑账号 · " + (c ? (c.display || label) : label))
    : "新增账号";
  el("dlg-err").textContent = "";
  el("f-label").value = label || "";
  el("f-label").disabled = !!label;          // 改代号 = 重命名，会让登录态快照对不上，先不让改
  el("f-display").value = c ? (c.display || "") : "";
  el("f-seq").value = pf.seq || (label ? "" : "");
  el("f-school").value = pf.school || "";
  el("f-major").value = pf.major || "";
  el("f-grade").value = pf.grade || "";
  el("f-student").value = pf.student_id || "";
  el("f-port").value = c ? c.port : "";
  const lim = (c && c.limits) || {};
  // 用 `=== undefined` 判断而不是 `??`：`0`（不限）必须原样回填，不能被 `||` 吃掉
  el("f-sections").value = c ? (lim.sections === undefined ? 10 : lim.sections) : 10;
  el("f-minutes").value = c ? (lim.minutes === undefined ? 600 : lim.minutes) : 600;
  el("f-name").value = "";
  el("f-pw").value = "";
  el("f-enabled").checked = c ? !!c.enabled : true;
  el("hint-name").textContent = c && c.credentials
    ? "这个账号已有凭据；留空 = 保持原样（面板不回显密码）"
    : "留空 = 不自动重登，掉线即停（比乱试安全）";
  // 自定义字段：渲染成 key-value 行（可删）
  renderCustomFields(cf);
  if (!label) {
    // 新增时自动请求一个随机端口
    el("f-port").value = "";
    genPort();
  }
  el("dlg").showModal();
}

function renderCustomFields(cf) {
  const box = el("f-custom-box");
  if (!box) return;
  const keys = Object.keys(cf || {});
  let html = "";
  for (const k of keys) {
    html += '<div class="fld"><label>自定义</label>' +
      '<div class="row" style="gap:6px;flex:1">' +
      '<input type="text" class="cf-key" value="' + esc(k) + '" placeholder="字段名">' +
      '<input type="text" class="cf-val" value="' + esc(cf[k]) + '" placeholder="值">' +
      '<button class="mini danger" type="button" onclick="this.closest(\'.fld\').remove()">删</button>' +
      '</div></div>';
  }
  html += '<div class="fld"><label>自定义</label>' +
    '<div class="row" style="gap:6px;flex:1">' +
    '<input type="text" class="cf-key" placeholder="字段名">' +
    '<input type="text" class="cf-val" placeholder="值">' +
    '<button class="mini" type="button" onclick="addCustomRow()">加</button>' +
    '</div><div class="hint">自定义字段随账号保存，不参与业务判定</div></div>';
  box.innerHTML = html;
}

function addCustomRow() {
  const box = el("f-custom-box");
  if (!box) return;
  const row = document.createElement("div");
  row.className = "fld";
  row.innerHTML = '<label>自定义</label>' +
    '<div class="row" style="gap:6px;flex:1">' +
    '<input type="text" class="cf-key" placeholder="字段名">' +
    '<input type="text" class="cf-val" placeholder="值">' +
    '<button class="mini danger" type="button" onclick="this.closest(\'.fld\').remove()">删</button>' +
    '</div>';
  box.appendChild(row);
}

async function genPort() {
  const r = await fetch("/api/accounts/next-port", { method: "POST" });
  const d = await r.json();
  if (d.ok) {
    el("f-port").value = d.port;
  } else {
    el("f-port").value = "";
  }
}

function closeAccount() {
  if (el("dlg").open) el("dlg").close();
  EDITING = "";
}

// 统一读回包：后端 400 走 FastAPI 的 `{"detail": "..."}`，不是 `{"ok": false}`。
// 不处理的话前端会把失败当成功（`data.ok` 是 undefined → falsy → 显示“已提交”）。
async function apiPost(url, body) {
  const r = await fetch(url, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  let data = {};
  try { data = await r.json(); } catch (e) { data = {}; }
  if (!r.ok) {
    return { ok: false, error: data.detail || data.error || ("HTTP " + r.status) };
  }
  return data;
}

async function saveAccount() {
  const body = {
    label: el("f-label").value.trim().toLowerCase(),
    display: el("f-display").value.trim(),
    port: Number(el("f-port").value),
    daily_sections: Number(el("f-sections").value),
    daily_minutes: Number(el("f-minutes").value),
    enabled: el("f-enabled").checked,
    // 预置描述字段（全部非必填，只作展示/筛选）
    seq: el("f-seq").value.trim() ? Number(el("f-seq").value) : undefined,
    school: el("f-school").value.trim(),
    major: el("f-major").value.trim(),
    grade: el("f-grade").value.trim(),
    student_id: el("f-student").value.trim(),
  };
  if (EDITING) body.original_label = EDITING;
  const name = el("f-name").value.trim();
  const pw = el("f-pw").value;
  if (name) body.name = name;                // 空 = 不改（后端只认显式给值）
  if (pw) body.pw = pw;
  // 自定义字段：收集所有 cf-key / cf-val 对
  const custom = {};
  document.querySelectorAll("#f-custom-box .cf-key").forEach((k) => {
    const key = k.value.trim();
    if (key) {
      const v = k.parentElement.querySelector(".cf-val");
      custom[key] = v ? v.value : "";
    }
  });
  if (Object.keys(custom).length) body.custom = custom;
  if (!body.label) { el("dlg-err").textContent = "代号不能为空"; return; }
  // 空输入框 `Number("")` 是 0，会被当成“不限”悄悄存进去 —— 必须当成非法
  for (const [id, cn] of [["f-port", "端口"], ["f-sections", "日节数上限"],
                          ["f-minutes", "日时长上限"]]) {
    if (el(id).value.trim() === "") {
      el("dlg-err").textContent = cn + "不能为空（要“不限”请显式填 0）";
      return;
    }
  }
  for (const [key, cn] of [["port", "端口"], ["daily_sections", "日节数上限"],
                           ["daily_minutes", "日时长上限"]]) {
    if (!Number.isFinite(body[key])) {
      el("dlg-err").textContent = cn + "必须是数字";
      return;
    }
  }
  el("dlg-err").textContent = "";
  const data = await apiPost("/api/accounts/save", body);
  if (!data.ok) { el("dlg-err").textContent = data.error || "保存失败"; return; }
  const wasNew = !EDITING;                   // closeAccount 会清空 EDITING，先记下来
  closeAccount();
  toast(data.message || "已保存");
  if (wasNew && data.account) LABEL = data.account.label;
  load();
}

// 统一读回包：后端 400 走 FastAPI 的 `{"detail": "..."}`，不是 `{"ok": false}`。
// 不处理的话前端会把失败当成功（`data.ok` 是 undefined → falsy → 显示“已提交”）。
async function toggleAccount(label, enabled) {
  const data = await apiPost("/api/accounts/toggle",
    { label: label, enabled: enabled });
  toast(data.message || data.error || "已提交", !data.ok);
  load();
}

async function deleteAccount(label) {
  if (!confirm("删除账号「" + label + "」？\n\n只删账号表里这一条；"
    + "登录态快照与 profile 目录会保留在磁盘上。")) return;
  const data = await apiPost("/api/accounts/delete", { label: label, confirm: true });
  toast(data.message || data.error || "已提交", !data.ok);
  load();
}

function renderBrowser(state) {
  const b = state.browser || {};
  const s = b.session || null;
  const managed = b.backend === "managed";
  let html = '<div class="row" style="margin-bottom:8px">' +
    '<span class="tag ' + (managed ? "t-ai" : "t-bank") + '">' +
    (managed ? "managed · 程序自起（容器形态）" : "cdp · 连你已开的 Chrome") + "</span>" +
    '<span class="tag ' + (b.port_alive ? "t-done" : "t-none") + '">端口 ' +
    esc(b.port) + (b.port_alive ? " 活着" : " 未监听") + "</span>" +
    (b.started_by_panel ? '<span class="tag t-learn">面板启动 · ' + esc(b.since) + "</span>" : "") +
    (s ? '<span class="tag ' + (s.logged_in ? "t-done" : "t-low") + '">页面上' +
      (s.logged_in ? "已登录" : "未登录") + "</span>" : "") +
    "</div>";
  // 紧凑双列：原来 6 行竖表占了整张卡，右边一长条空白。
  // 现在短项并排（Chrome / profile / 凭据），长项（快照、上次检查）独占一行。
  const short = [
    ["Chrome", b.chrome_ok ? esc(b.chrome) : "没找到（设 XXT_GXKS_CHROME）"],
    ["profile", b.profile ? esc(b.profile) + (b.profile_set ? "" : "（默认）") : "—"],
    ["账号密码", b.credentials ? "已配置" : "未配置（掉线即停）"],
  ];
  let grid = '<div class="binfo">';
  for (const [k, v] of short) {
    grid += '<div class="bi"><span class="k">' + esc(k) + '</span>' +
      '<span class="v">' + (v || "—") + "</span></div>";
  }
  grid += "</div>";
  html += grid;
  // 长项：各自一行，但值截断 + title 看全文（避免把卡撑高）
  const long = [];
  if (b.state && b.state.saved) {
    long.push(["登录态快照", esc(b.state.at) + " · " + esc(b.state.cookies) + " 条" +
      (b.state.session_level && b.state.session_level.length ? "（会话级）" : "")]);
  } else {
    long.push(["登录态快照", "未存（先点「检查会话」）"]);
  }
  long.push(["上次检查", s
    ? esc(s.checked_at) + " · " + (s.logged_in ? "已登录" : "未登录") +
      (s.navigated ? "（导航了）" : "（零请求）")
    : "没查过"]);
  if (managed) {
    long.push(["managed", b.managed_profile ? "profile 已建" : "profile 未建（首跑 = 未登录）"]);
  }
  for (const [k, v] of long) {
    html += '<div class="bi-line"><span class="k">' + esc(k) + '</span>' +
      '<span class="v" title="' + v + '">' + v + "</span></div>";
  }
  if (managed) {
    html += '<div class="muted" style="margin-top:8px;line-height:1.5">managed 后端不靠外部 Chrome：' +
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
  return '<div class="bar"><div class="lab"><span>' + esc(label) + '</span><span><b>' +
    esc(done) + "</b> / " + esc(total) + (note ? " · " + esc(note) : "") +
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
  const card = (state.cards || []).find(c => c.label === state.label) || {};
  const course = (rows[0] && rows[0].category) || "";
  // 标题写清「谁的哪门课」：多账号多课程时不写就只剩一个日期，容易看错人
  el("sec-meta").textContent = [card.display || state.label, course,
    "读于 " + (data.read_at || "?")].filter(Boolean).join(" · ");

  const sig = [state.label, data.read_at, rows.length, s.done, s.practice_ready].join("|");
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
  // 带上当前显示的课程 value：B1 之后缓存按课程分开，不带就会写到“无课程”那份去
  post("/api/sections/refresh", { course: (STATE && STATE.course) || "" });
}

function refreshSectionsFor(label, course) {
  post("/api/sections/refresh", { label: label, course: course || "" });
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
  "session-relogin": "重新登录", "account-add": "新增账号",
  "account-edit": "修改账号", "account-toggle": "启停账号",
  "account-del": "删除账号", "error": "出错" };

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
    let r = await fetch("/api/state?label=" + encodeURIComponent(LABEL));
    // 选中的账号被删掉 / 改了代号：后端会 400。此时回落到第一个账号，
    // 不能一直报“连接中断”——那看起来像面板坏了。
    if (!r.ok && LABEL) {
      LABEL = "";
      r = await fetch("/api/state");
    }
    const state = await r.json();
    STATE = state;
    LABEL = state.label || "";
    readFilter();                 // 工具栏是静态的，每次重绘前把筛选条件读回来
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

// ---------------------------------------------------------------- 账号表：事件绑定
// 全部用**事件委托**挂在 #accounts 上：表格每 4 秒整块重绘，直接绑到行/按钮上会随重绘丢失。
// ⚠ 行点击也走这里（不在 <tr> 上写 onclick）——否则行内复选框的点击会先冒泡到 <tr>
//    触发"切账号"，或者为了挡它而在 <td> 上加 stopPropagation，反过来把这里的委托也挡掉。
el("accounts").addEventListener("click", (e) => {
  const all = e.target.closest("#acct-all");
  if (all) {                                     // 全选 / 全不选（只影响"当前可见"）
    const shown = visibleCards(STATE || {});
    if (all.checked) shown.forEach((c) => SEL.add(c.label));
    else shown.forEach((c) => SEL.delete(c.label));
    renderAccounts(STATE || {});
    return;
  }
  const cb = e.target.closest(".acct-pick");
  if (cb) {                                      // 单行勾选
    const lab = cb.dataset.label;
    if (cb.checked) SEL.add(lab); else SEL.delete(lab);
    renderBatchBar(visibleCards(STATE || {}));
    const all2 = el("acct-all");
    const shown = visibleCards(STATE || {});
    if (all2) all2.checked = shown.length > 0 && shown.every((c) => SEL.has(c.label));
    return;
  }
  if (e.target.closest("button")) return;        // 行内按钮（学习/停止/✎/删除…）自己处理
  const tr = e.target.closest("tr[data-label]");
  if (tr) pick(tr.dataset.label);                // 点行的空白处 = 切换账号
});
el("accounts").addEventListener("mouseover", (e) => {
  const tr = e.target.closest("tr[data-label]");
  if (tr) showAcctDetail(tr.dataset.label);
});
el("accounts").addEventListener("mouseout", (e) => {
  const tr = e.target.closest("tr[data-label]");
  if (tr) hideAcctDetail();
});
// 表格本身滚走 / 页面切走时把浮层收掉，免得留在原地
el("accounts").addEventListener("scroll", hideAcctDetail, true);
window.addEventListener("scroll", hideAcctDetail, true);

// 工具栏（静态 DOM，不随轮询重绘，所以搜索框不会掉焦点）
el("acct-q").addEventListener("input", () => { readFilter(); renderAccounts(STATE || {}); });
for (const id of ["acct-status", "acct-port", "acct-cred"]) {
  el(id).addEventListener("change", () => { readFilter(); renderAccounts(STATE || {}); });
}

setInterval(load, 4000);
load();
</script>
</body>
</html>
"""
