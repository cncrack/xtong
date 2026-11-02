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
/* 账号切换器（2026-11-02）：头部原来的 <select> 账号一多就长得没法用，
   改成「当前账号按钮 + 可搜索弹层」，列表限高滚动。 */
.asw{position:relative;flex:0 0 auto}
.asw-btn{display:flex;align-items:center;gap:7px;max-width:220px;
         border:1px solid var(--line);border-radius:7px;background:#fff;
         padding:5px 10px;font:inherit;font-size:13.5px;cursor:pointer}
.asw-btn:hover{border-color:var(--accent);color:var(--accent);background:#f5f9ff}
.asw-btn #asw-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
                   min-width:0;font-weight:600}
.asw-caret{color:var(--dim);font-size:11px;flex:0 0 auto}
/* 弹层用 fixed 定位（坐标由 JS 按按钮位置算），层级要压过 header 与页面内容 */
.asw-pop{position:fixed;z-index:120;width:250px;max-width:calc(100vw - 20px);
         background:var(--card);border:1px solid var(--line);border-radius:10px;
         box-shadow:0 10px 30px rgba(16,24,40,.22);padding:8px}
.asw-pop[hidden]{display:none}
.asw-pop input{width:100%;box-sizing:border-box;font:inherit;font-size:13px;
               border:1px solid var(--line);border-radius:6px;padding:6px 9px;
               margin-bottom:6px}
.asw-list{max-height:320px;overflow:auto}
.asw-item{display:flex;align-items:center;gap:8px;width:100%;border:0;
          border-radius:6px;background:none;padding:7px 9px;cursor:pointer;
          text-align:left;font:inherit;font-size:13.5px}
.asw-item:hover{background:#f0f6ff}
.asw-item.cur{background:#eaf2ff;font-weight:700;color:var(--accent)}
.asw-item .asw-name{flex:1 1 auto;min-width:0;overflow:hidden;
                    text-overflow:ellipsis;white-space:nowrap}
.asw-item .asw-label{flex:0 0 auto;color:var(--dim);font-size:11.5px}
.asw-empty{color:var(--dim);font-size:12.5px;padding:8px 9px}
@media (max-width:760px){
  .asw-btn{max-width:140px}
  .asw-pop{left:8px;right:8px;width:auto!important}
}
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
  /* 账号视图内部：左表格 9 栏 + 右浏览器 3 栏，**两栏等高**。
     ⚠ 2026-11-02 修重叠：这里必须用**固定 height**，不能只给 max-height。
     原因：内部 #accounts / #acct-side 用的是 flex:1 1 auto，flex 需要一个确定的
     容器高度才能算出"剩余空间"；只给 max-height 时基准是内容高度，
     内容一多就把容器撑开、溢出压到下面的节进度上（用户："重叠在一起了"）。
     height:62vh 是确定的，内部超出部分各自 overflow:auto 在栏内滚动。 */
  #view-accounts .split{display:grid;grid-template-columns:minmax(0,8fr) minmax(0,4fr);
                        gap:14px;align-items:stretch;
                        flex:0 0 auto;height:82vh;min-height:500px;max-height:1000px}
  .split-main,.split-side{display:flex;flex-direction:column;min-width:0;min-height:0;
                          background:var(--card);border:1px solid var(--line);
                          border-radius:12px;padding:16px 18px;box-shadow:var(--shadow);
                          overflow:hidden}
  .split-main>#accounts{overflow:auto;flex:1 1 auto;min-height:0}
  /* 右栏两段（用户 2026-11-02：「右栏底部没看到账号详情，要注意要对齐」）：
     原来 #browser 是 flex:0 0 auto（按内容撑高），内容一多就把 62vh 占满，
     下面的 #acct-side 只拿到几十像素甚至 0 → 看不见。
     现在：上半段（浏览器信息）限高并在内部滚动，下半段（账号详情）保底 180px。 */
  /* 右栏现在是 4fr（更宽），浏览器区能显示更完整的内容（用户：
     「右侧栏宽度要调大些，不然浏览器与会话中有些下面内容看不全」）。
     上半段给到 52%，剩下的留给账号详情。 */
  .split-side>#browser{flex:0 1 auto;overflow:auto;min-height:0;max-height:52%}
  .split-side>#acct-side{flex:1 1 auto;overflow:auto;min-height:180px;
                         margin-top:10px;padding-top:10px;
                         border-top:1px solid var(--line)}
  /* 表格自己不要再套一层滚动：外层 #accounts 已经在滚了，
     两层 max-height 叠起来会让高度计算失真（这是重叠的诱因之一）。 */
  /* 表头吸顶（用户 2026-11-02：「列标题要固定在最上面，像表格冻结一样」）：
   .table-wrap 必须 overflow:visible —— 否则它自己成了滚动容器（又从不滚动），
   thead 的 sticky 就永远不生效。让 #accounts 当唯一的滚动容器。 */
  .split-main>#accounts .table-wrap{overflow:visible;max-height:none;
                                    border:0;border-radius:0}
  /* 节进度的待学/已完成并排 */
  #sec-sections .sec-cols{display:grid;grid-template-columns:1fr 1fr;gap:12px;
                          align-items:start}
}
@media (max-width:999px){
  #view-accounts .split{display:flex;flex-direction:column;gap:14px}
  .split-main,.split-side{background:var(--card);border:1px solid var(--line);
                          border-radius:12px;padding:16px 18px}
  #sec-sections .sec-cols{display:grid;grid-template-columns:1fr;gap:12px}
}
@media (max-width:999px){
  #sec-sections .sec-cols{display:grid;grid-template-columns:1fr;gap:12px}
}
/* ================================================================ 移动端适配
   约定：**任何界面改动都要在 375px 宽下可用**（见 AGENTS.md「移动端可用」）。
   断点：760px 以下按手机处理；1000px 以上才启用左右分栏。 */
@media (max-width:760px){
  body{font-size:14px}
  main{padding:10px 10px 40px;gap:10px}
  header{padding:8px 10px;gap:8px;flex-wrap:wrap}
  h1{font-size:16px}
  h2{font-size:14px}
  section{padding:12px;border-radius:10px}
  /* 窄屏节列表也保持滚动条，只是表格最小宽收窄（仍够横滑看全关键列） */
  details.blk>.body{max-height:50vh}
  details.blk>.body .table-wrap table{min-width:440px}
  /* TAB 条：可横向滑动，不换行 */
  .pagetabs{width:100%;overflow-x:auto;-webkit-overflow-scrolling:touch}
  .pagetabs button{padding:7px 14px;font-size:13px}
  /* 账号视图：取消固定高度，右栏堆到下方 */
  #view-accounts .split{display:flex;flex-direction:column;height:auto;
                        min-height:0;max-height:none;gap:10px}
  .split-main,.split-side{overflow:visible;padding:12px}
  .split-main>#accounts{max-height:62vh}
  /* 账号表：允许横滑，列不被压扁（9 列塞进 375px 必然要滑） */
  table.acct-tbl{min-width:680px}
  table.acct-tbl th,table.acct-tbl td{padding:5px 6px}
  /* 右栏里浏览器信息与详情不再限制高度 */
  .split-side>#browser,.split-side>#acct-side{max-height:none;min-height:0;flex:0 0 auto}
  /* 工具条：搜索独占一行，其余换行 */
  .acct-tools{flex-wrap:wrap;gap:6px}
  .acct-tools input[type=search]{flex:1 1 100%;min-width:0}
  .acct-tools select{flex:1 1 auto;min-width:0}
  .chips{flex-wrap:wrap}
  /* 批量条：允许换行（手机上按钮多） */
  .acct-batch{flex-wrap:wrap}
  /* 表单与 AI：全部单列 */
  .form-cols{grid-template-columns:1fr}
  .ai-wrap{grid-template-columns:1fr}
  .sec-cols{grid-template-columns:1fr}
  /* 按钮放大到手指可点 */
  button{padding:8px 12px}
  button.mini{padding:6px 10px;font-size:13px}
  .acct-batch button{font-size:13px}
  /* 表格类区块可横滑 */
  #sec-sections,#sec-work,#sec-ops{overflow-x:auto}
  #sec-sections .table-wrap,#sec-work .table-wrap,#sec-ops .table-wrap{max-height:none}
}
/* 超窄屏（旧手机/分屏）：再收一档 */
@media (max-width:420px){
  main{padding:8px 8px 32px}
  section{padding:10px}
  .pagetabs button{padding:6px 10px;font-size:12.5px}
  h1{font-size:15px}
}

/* ---------------------------------------------------------------- 整页级 TAB
   用户 2026-11-02：「账号设置与AI模型这两个标签页是要占整个区域的，不是只显示账号与任务
   列表区域的」「页面的设计太低级感了」。所以：
   - TAB 条横跨整宽、做成**分段控件**（segmented control），不是细下划线
   - 每个视图独占整行（.pageview 默认 grid-column:span 12）
   - 选中态用实心白底 + 阴影，未选中是透明，视觉层次清楚 */
.pagetabs{grid-column:span 12;display:flex;gap:4px;padding:4px;
          background:#eceef2;border-radius:10px;width:fit-content;max-width:100%;
          box-shadow:inset 0 1px 2px rgba(16,24,40,.06)}
.pagetabs button{border:0;border-radius:7px;background:none;padding:8px 22px;
                 color:var(--dim);font-size:14px;font-weight:600;cursor:pointer;
                 transition:background .15s,color .15s,box-shadow .15s;
                 white-space:nowrap}
.pagetabs button:hover{color:var(--accent);background:rgba(255,255,255,.6)}
.pagetabs button.on{background:var(--card);color:var(--accent);
                    box-shadow:0 1px 3px rgba(16,24,40,.12)}
.pageview{display:none;grid-column:span 12}
.pageview.on{display:block}
/* bare = 这个视图自己不要卡片外观：它内部已经是多张卡（.split-main/.split-side
   以及节进度/本次作答/操作记录），再套一层卡片就是"卡套卡"。
   ⚠ 2026-11-02 修 bug：这里**绝不能**无条件写 display:flex ——
   `.pageview.bare`（两个类）比 `.pageview`（一个类）更具体，
   会把 `.pageview{display:none}` 顶掉 → 账号视图永远显示、盖住另外两个页签，
   表现就是"点页签没反应"。display 只在 .on 时才设。 */
.pageview.bare{background:none;border:0;border-radius:0;padding:0;box-shadow:none}
.pageview.bare.on{display:flex;flex-direction:column;gap:14px}
/* 视图内部的后续区块（节进度/本次作答/操作记录）各自是卡片 */
.pageview.bare>section{margin:0}
/* 账号视图内的两栏由 .split 承担；这两个类只在视图里用 */
.split-main>#accounts{min-height:0}
.view-head{display:flex;align-items:center;justify-content:space-between;
           gap:10px;flex-wrap:wrap;margin:0 0 10px}
.view-head h2{margin:0}
/* 表单两列：左列基本信息、右列额度与凭据，窄屏自动单列 */
/* 账号设置：三列卡片分组（2026-11-02 重做）。原来是一个两列表单、用虚线标题分隔，
   字段一多就分不清组；现在每组一张浅底卡片，标题带下边框。 */
.form-cols{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));
           gap:14px;align-items:start}
@media (max-width:1100px){.form-cols{grid-template-columns:1fr 1fr}}
@media (max-width:760px){.form-cols{grid-template-columns:1fr}}
/* ⚠ 必须写 grid-column:auto —— 基础规则里有 section{grid-column:span 12}，
   而 .fcard 是 <section>；不覆盖就会每张卡占满整行 → 三栏退化成上下堆叠。
   这就是「看不到 3 栏效果」的原因。阴影也要清掉（section 自带）。 */
.fcard{grid-column:auto;margin:0;padding:12px 14px;background:#fafbfc;
       border:1px solid var(--line);border-radius:10px;box-shadow:none}rder-radius:10px;box-shadow:none}
.fcard h3{font-size:12.5px;font-weight:700;color:var(--txt);margin:0 0 10px;
          padding-bottom:7px;border-bottom:1px solid var(--line);
          letter-spacing:.04em}
.fcard .fld{grid-template-columns:78px 1fr;gap:3px 8px;margin-bottom:7px;font-size:13px}
/* 说明压到 11.5px / 行高 1.3：7 条说明是卡片偏高的主因（用户："信息长了"） */
.fcard .fld .hint{grid-column:2;font-size:11.5px;line-height:1.3}
.fcard input[type=text],.fcard input[type=number],.fcard input[type=password]{
  padding:5px 8px;font-size:13px}
.fcard .row{gap:5px}
.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:0 26px;align-items:start}
@media (max-width:860px){.form-grid{grid-template-columns:1fr}}
.form-grid .fld{grid-template-columns:110px 1fr}
.form-grid .fld .hint{grid-column:2}
.form-sec{grid-column:1/-1;margin:6px 0 4px;padding-top:10px;border-top:1px dashed var(--line);
          color:var(--dim);font-size:12.5px;font-weight:700;letter-spacing:.04em}
.form-sec:first-child{margin-top:0;padding-top:0;border-top:0}
/* 搜索命中高亮：命中片段黄底加粗，扫一眼就知道为什么这行被筛出来 */
mark.hit{background:#fff3bf;color:inherit;padding:0 1px;border-radius:2px;
         font-weight:700;box-shadow:inset 0 -1px 0 #f0c000}
/* 表单底部动作条：贴在卡片内，不再用弹窗 */
.form-actions{display:flex;gap:8px;justify-content:flex-end;align-items:center;
              margin-top:16px;padding-top:14px;border-top:1px solid var(--line)}
.form-actions .sp{flex:1}
/* 账号表格（替代原卡片视图）：见下方 .acct-tbl 一组规则。
   卡片每张 ~222px 高、20 个账号要 2300px；表格每行 ~44px、20 个约 1040px 且能在固定区滚动。 */
/* ---------------------------------------------------------------- 账号表格
   为什么从卡片改成表格（用户 2026-10-30 提议）：卡片每张 ~222px 高，6 个账号 ~700px、
   20 个账号 ~2300px；表格每行 ~44px，6 个 ~310px、20 个 ~1040px（固定区内滚动）。
   同样空间能多放 2~3 倍账号，而且**列对齐才能横向比较**（谁的额度用完了、谁端口没开）。 */
/* 筛选工具条（用户 2026-11-02 两次要求）：
   ① 「是否可安排在一行显示」→ 正常宽度下排一行
   ② 「右边都出现滚动条」→ **不能用 overflow-x:auto**
   所以：尺寸整体收紧（搜索框 120px、下拉 12.5px 字号、快捷按钮 12px），
   并留 flex-wrap:wrap 兜底 —— 窗口特别窄时换行，而不是出滚动条。 */
.acct-tools{display:flex;gap:5px;align-items:center;flex-wrap:wrap;
            margin:0 0 8px}
.acct-tools input[type=search]{font:inherit;font-size:13px;flex:0 1 120px;
  min-width:96px;border:1px solid var(--line);border-radius:6px;padding:5px 8px}
.acct-tools select{font:inherit;border:1px solid var(--line);border-radius:6px;
  background:#fff;padding:4px 5px;cursor:pointer;flex:0 0 auto;font-size:12.5px}
.chips{display:flex;gap:3px;flex-wrap:nowrap;flex:0 0 auto}
.chips button{padding:3px 7px;font-size:12px;white-space:nowrap}
/* 统计 + 批量操作**同一行**（用户 2026-11-02 要求），
   同样不能出横向滚动条（用户截图反馈），所以去掉 overflow-x 并收紧按钮。 */
.acct-batch{display:flex;gap:5px;align-items:center;flex-wrap:wrap;
            min-height:0;margin:0 0 8px}
.acct-batch:not(:empty){padding:6px 9px;background:#f7f9fc;border:1px solid var(--line);
                        border-radius:8px}
.acct-batch .tag{flex:0 0 auto;font-size:12px;padding:1px 7px}
.acct-batch button{flex:0 0 auto;white-space:nowrap;padding:3px 9px;font-size:12.5px}
.acct-batch b{font-size:12.5px;white-space:nowrap}
/* 统计与批量操作之间的竖分隔线 */
.bb-sep{flex:0 0 auto;width:1px;height:16px;background:var(--line);margin:0 3px}
/* 列宽策略（2026-11-02 修）：
   原来 table-layout:auto，没设 width 的「账号」列会吸走全部余量 →
   名字靠左、右边留一大片空白。改成 fixed + 百分比，余量按比例分给各列。 */
table.acct-tbl{table-layout:fixed}
table.acct-tbl th,table.acct-tbl td{overflow:hidden;text-overflow:ellipsis}
/* 表头居中（用户 2026-11-02 要求） */
table.acct-tbl thead th{text-align:center}
table.acct-tbl thead th.num{text-align:center}
table.acct-tbl{font-size:13.5px}
table.acct-tbl th{font-size:12.5px;padding:6px 8px}
table.acct-tbl td{padding:6px 8px;vertical-align:middle}
table.acct-tbl tbody tr{cursor:pointer}
table.acct-tbl tbody tr.cur{background:#f0f6ff;
  box-shadow:inset 3px 0 0 var(--accent)}
table.acct-tbl tbody tr.off{opacity:.55}
table.acct-tbl td.c-name b{font-size:14.5px}
table.acct-tbl td.c-today b{font-size:15px;color:var(--accent);
  font-variant-numeric:tabular-nums}
/* 操作列：图标按钮组，单行不折行（折行会让行高翻倍）。
   9 栏下操作列约 200px，4 个图标 + 间距 ≈ 150px，放得下；
   若将来加按钮，优先缩图标间距而不是允许折行。 */
/* 操作列不裁剪：按钮被 ellipsis 截掉会露出半个、且悬停会误激活（用户 2026-11-02） */
table.acct-tbl td.c-ops{white-space:nowrap;text-align:right;overflow:visible}
table.acct-tbl th.c-ops{overflow:visible}
table.acct-tbl td.c-ops button{margin-left:3px;padding:2px 7px}
/* 端口列单行：端口号 + 活/停 标签不折行（用户 2026-11-02 明确要求） */
table.acct-tbl td.c-port{white-space:nowrap}
/* 今日列的数字与迷你条必须同行：折行会让整行高度从 44px 涨到 66px */
table.acct-tbl td.c-today{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
/* 行内小进度条：30px 宽足够看出"快满了" */
.mini-bar{display:inline-block;width:34px;height:6px;border-radius:99px;
  background:#eef0f3;overflow:hidden;vertical-align:middle;margin-left:5px}
.mini-bar i{display:block;height:100%;background:#3fb27f}
.mini-bar i.warn{background:#f2b45c}
/* 浏览器卡的按钮组（2026-11-02 重排）：两组各占一行，组内等宽、标签在左。
   原来 5 个按钮平铺一行，宽度随文字长短不一、主次不分。 */
.bgrp{display:flex;align-items:center;gap:6px;margin:0 0 8px}
.bgrp-lab{flex:0 0 42px;color:var(--dim);font-size:12px;white-space:nowrap}
.bgrp-btn{flex:1 1 0;min-width:0;padding:5px 6px;font-size:12.5px;
          white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
/* 右列「账号详情」：常驻卡片（用户 2026-11-02 要求）。
   原来它是 position:fixed 的悬停浮层，跟着鼠标跑、会挡住表格；
   现在固定在浏览器卡下方，随**选中账号**变化，不跟鼠标、不跟滚动。 */
/* 账号详情：上下间距与分隔线由媒体查询里的 .split-side>#acct-side 负责
   （那里还要控 flex 与 min-height），这里只留窄屏也能用的基础值。 */
.acct-side{margin-top:10px;padding-top:10px;border-top:1px solid var(--line)}
.acct-side-head{display:flex;align-items:center;gap:8px;margin:0 0 8px;
                font-size:13px;font-weight:700;color:var(--txt)}
.acct-detail{background:#fafbfc;border:1px solid var(--line);border-radius:8px;
  padding:10px 12px;font-size:12.5px;line-height:1.65;color:var(--txt)}
/* 账号详情（2026-11-02 重做）：只放列表看不到的信息，按小节分组。
   每行「标签左、值右」用 flex 撑开，长值自动换行不撑破右栏。 */
/* 分组标题（用户 2026-11-02：「分级组的标题文字要加组」）：
   加浅底色 + 左侧色条，让「档案 / 自定义字段 / 凭据与运行 / 额度口径」一眼分得开。 */
.acct-detail .dsec{font-size:11.5px;font-weight:700;color:var(--txt);
  letter-spacing:.05em;margin:10px 0 5px;padding:3px 8px;
  background:#eef2f7;border-left:3px solid var(--accent);border-radius:3px}
.acct-detail .dsec:first-child{margin-top:0}
/* 「标签：值」网格：(标签宽自适应|值占其余)，同一组内标签左对齐成一列 */
.acct-detail .dgrid{display:grid;grid-template-columns:max-content minmax(0,1fr);
                    gap:3px 6px;padding:2px 0}
/* 两个短字段一行（年级/学号、自动重登/正在跑） */
.acct-detail .dgrid2{display:grid;
                     grid-template-columns:max-content minmax(0,1fr) max-content minmax(0,1fr);
                     gap:3px 6px;padding:2px 0}
@media (max-width:620px){
  .acct-detail .dgrid2{grid-template-columns:max-content minmax(0,1fr)}
}
/* 标签：加粗、暗色、自动补冒号（用户：「字段要加粗，比如 学校：泉州信息工程学院」） */
.acct-detail .dk{color:var(--dim);font-weight:700;white-space:nowrap}
.acct-detail .dk::after{content:"：";font-weight:400}
.acct-detail .dv{min-width:0;word-break:break-all;color:var(--txt)}
.acct-detail .row2{display:flex;gap:0;flex-wrap:wrap}
.acct-detail b{color:var(--txt)}
.acct-detail .sep{border-top:1px dashed var(--line);margin:6px 0}
/* AI 模型页（2026-11-02 重做）：左栏「厂商与参数」、右栏「该厂商的 Key + 测试」。
   两栏各自是浅底卡片，窄屏自动堆叠。 */
.ai-wrap{display:grid;grid-template-columns:1fr 340px;gap:16px;align-items:start}
@media (max-width:900px){.ai-wrap{grid-template-columns:1fr}}
.ai-panel{min-width:0;padding:14px 16px;background:#fafbfc;
          border:1px solid var(--line);border-radius:10px}
.ai-panel-key{background:#f7f9fc}
.ai-ptitle{display:flex;align-items:center;gap:8px;margin:0 0 12px;
           font-size:13px;font-weight:700;color:var(--txt);
           padding-bottom:7px;border-bottom:1px solid var(--line)}
.ai-row{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:0 0 12px}
.ai-row select{font:inherit;border:1px solid var(--line);border-radius:6px;
  background:#fff;padding:6px 10px;cursor:pointer;min-width:150px;flex:1 1 150px}
.ai-grid{display:grid;grid-template-columns:1fr;gap:10px}
.ai-f{display:flex;flex-direction:column;gap:3px;min-width:0}
.ai-lab{color:var(--dim);font-size:12.5px;white-space:nowrap;font-weight:600}
.ai-f input,.ai-panel-key input{font:inherit;border:1px solid var(--line);
  border-radius:6px;padding:7px 10px;width:100%;box-sizing:border-box}
/* 输入框 + 小按钮同一行（模型「拉取」用） */
.ai-inline{display:flex;gap:6px;align-items:center}
.ai-inline input{flex:1 1 auto;min-width:0}
.ai-inline button{flex:0 0 auto}
/* Key 卡片里的按钮组 */
.ai-btns{display:flex;gap:6px;margin-top:8px}
.ai-btns button{flex:1 1 auto}
/* 测试连通区块 */
.ai-test{margin-top:14px;padding-top:12px;border-top:1px dashed var(--line)}
.ai-testmsg{margin-top:8px;font-size:12.5px;line-height:1.5;min-height:1em;
            padding:0;border-radius:6px;word-break:break-all}
.ai-testmsg.ok{color:var(--ok);background:#eef8f3;padding:7px 9px;
               border:1px solid #c6e6da}
.ai-testmsg.bad{color:var(--bad);background:#fdf2f1;padding:7px 9px;
                border:1px solid #f0c0bb}
.ai-hint{color:var(--dim);font-size:12px;line-height:1.5;margin-top:8px}
/* 状态圆点（替代被删掉的「状态」列）：★不占列宽，放在账号名前 */
table.acct-tbl .sdot{display:inline-block;width:7px;height:7px;border-radius:50%;
  margin-right:5px;vertical-align:middle;background:#d9dce1}
table.acct-tbl .sdot.running{background:#3fb27f;box-shadow:0 0 0 2px #e3f4ec}
table.acct-tbl .sdot.full{background:#f2b45c;box-shadow:0 0 0 2px #fdf1e0}
table.acct-tbl .sdot.off{background:#c3c8d0}
/* 会话行末尾的登录状态（用户 2026-11-02：放到「会话」那一行）：
   不参与等宽分配，靠右紧贴按钮。 */
.b-login{flex:0 0 auto;display:flex;align-items:center;margin-left:2px;
          min-width:0;overflow:hidden}
.b-login .tag{font-size:12px;padding:2px 8px;white-space:nowrap;
              overflow:hidden;text-overflow:ellipsis;max-width:110px}
/* 标签真的放不下时（极窄），按钮先收缩，标签最后才省略 */
.bgrp .bgrp-btn{min-width:56px}

/* 浏览器状态标签：三枚排成一行不换行（用户 2026-11-02 截图要求）。
   右栏内容约 365px，三枚约 300px，够放；真的不够时横向裁切而不是折行。 */
.b-tags{display:flex;gap:5px;align-items:center;flex-wrap:nowrap;
        overflow:hidden;margin:0 0 8px}
.b-tags .tag{flex:0 0 auto;font-size:12px;padding:2px 8px;white-space:nowrap}
/* 第一枚（cdp / managed）允许收缩并省略，保证后两枚始终可见 */
.b-tags .tag:first-child{flex:0 1 auto;overflow:hidden;text-overflow:ellipsis}
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
/* 路径行（Chrome / profile）：整行 + 换行显示，避免被截成 "...\Google\C..." */
.bi-line.path{align-items:flex-start}
.bi-line.path .v{white-space:normal;overflow:visible;text-overflow:clip;
                 word-break:break-all;line-height:1.4}
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
/* 日志记录区的子标题（运行日志 / 操作记录） */
.ops-sub{font-size:12.5px;font-weight:700;color:var(--txt);margin:12px 0 6px;
         padding-bottom:4px;border-bottom:1px dashed var(--line);
         letter-spacing:.04em}
.ops-sub:first-of-type{margin-top:2px}
#runlog pre{margin:0}
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
/* 两个列表很长时的纵向滚动（2026-11-03 用户问「待学/已完成太长怎么办」）。
   加滚动条、不分页：分页要维护页码状态，而这页 4 秒轮询重绘会重置翻页。
   高度取 min 一屏一半高度——并排两列时各占一半，窄屏堆叠时也一致不拉长整页。
   表格 min-width 保证列不压扁 → 纵向条 + 窄屏横向条可并存。 */
details.blk>.body{padding:8px 10px 10px;max-height:min(52vh,420px);overflow-y:auto;
                  scrollbar-width:thin}
details.blk>.body .table-wrap{max-height:none;overflow:visible;border:0;border-radius:0}
details.blk>.body .table-wrap table{min-width:520px}
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
  <h1>第二平台控制台 <span class="muted" style="font-weight:400;font-size:12px">v2026-11-02Z4</span></h1>
  <!-- 账号切换器（2026-11-02 替换原来的 <select>）：账号多时原下拉会很长，
       改成「当前账号按钮 + 可搜索弹层」。 -->
  <div class="asw" id="asw">
    <button type="button" class="asw-btn" id="asw-btn" onclick="toggleAsw()"
            aria-haspopup="listbox" aria-expanded="false" title="切换账号（可搜索）">
      <span id="asw-name">—</span><span class="asw-caret">▾</span>
    </button>
    <div class="asw-pop" id="asw-pop" hidden>
      <input id="asw-q" type="search" placeholder="搜索代号 / 显示名…"
             autocomplete="off" oninput="renderAswList()">
      <div class="asw-list" id="asw-list" role="listbox"></div>
    </div>
  </div>
  <span class="muted" id="pager"></span>
  <span style="flex:1"></span>
  <span class="muted" id="clock"></span>
</header>
<main>
  <!-- ============================================================ 整页级 TAB
       用户 2026-11-02 明确要求：TAB 必须占**整个区域**，不是只占账号列表那 9 栏。
       所以 TAB 条跨整宽，三个面板各自独占整行。
       原来 TAB 条右边的「＋ 新增账号」已移除——它与「账号设置」页签语义重复。 -->
  <nav class="pagetabs" role="tablist">
    <button type="button" id="tab-btn-accounts" class="on" role="tab"
      aria-controls="view-accounts" onclick="showTab('accounts')">账号与任务</button>
    <button type="button" id="tab-btn-account" role="tab"
      aria-controls="view-account" onclick="showTab('account')">账号设置</button>
    <button type="button" id="tab-btn-ai" role="tab"
      aria-controls="view-ai" onclick="showTab('ai')">AI 模型</button>
  </nav>

  <!-- ------------------------------------------------ 视图 1：账号与任务（整行） -->
  <section id="view-accounts" class="pageview on bare" role="tabpanel">
    <div class="split">
      <div class="split-main">
        <div class="view-head">
          <h2>账号与任务 <span class="muted" id="accounts-meta"></span></h2>
          <button class="primary mini" type="button" onclick="newAccount()"
            title="切到「账号设置」并清空表单，填写新账号">＋ 新增账号</button>
        </div>


    
    <div class="acct-tools">
      <input id="acct-q" type="search" placeholder="搜索账号…"
             title="可搜：名称 / 代号 / 端口 / 学校 / 学号 / 自定义字段（命中会高亮）"
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
      </div><!-- /.split-main -->
      <aside class="split-side">

    <div class="card-head">
      <h2>浏览器与会话 <span class="muted" id="browser-meta"></span></h2>
    </div>
    <!-- 按钮重排（用户 2026-11-02：「这几个按钮要重新排列一下」）：
         原来 5 个平铺一行、宽度不一、主次不分。现在分两组：
           ① 生命周期：启动 / 停止（互斥，主操作）
           ② 会话：检查 / 恢复 / 重登（按"先看有没有登录 → 没登录才恢复或重登"的顺序）
         每组内等宽，组间留空隙，一眼能看出哪些是一类。 -->
    <div class="bgrp">
      <span class="bgrp-lab">浏览器</span>
      <button id="btn-bstart" class="bgrp-btn primary" onclick="startBrowser()"
        title="用本账号的端口与 profile 目录拉起 Chrome">启动</button>
      <button id="btn-bstop" class="bgrp-btn danger" onclick="post('/api/browser/stop')"
        title="只停面板自己拉起的那个 Chrome">停止</button>
      <button id="btn-bcopy" class="mini" type="button" onclick="copyChromeCmd()"
        title="复制该账号的 Chrome 启动命令（面板在非交互会话里无法启动 GUI 程序时用）">复制命令</button>
      <!-- 账号密码状态放在浏览器这一行（用户 2026-11-02 建议），与「已登录」在会话行对称 -->
      <span class="b-login" id="b-cred"></span>
    </div>
    <div class="bgrp">
      <span class="bgrp-lab">会话</span>
      <button id="btn-scheck" class="bgrp-btn" onclick="checkSession()"
        title="访问平台一次，确认当前账号的登录态是否有效">检查</button>
      <button id="btn-srestore" class="bgrp-btn" onclick="restoreSession()"
        title="把本账号的登录态快照注入当前 Chrome（零密码）">恢复登录</button>
      <button id="btn-rlogin" class="bgrp-btn" onclick="reloginSession()"
        title="用本账号的账号密码填表登录一次；遇验证码立即停、转人工">重新登录</button>
      <!-- 登录状态放在会话这一行（用户 2026-11-02 要求，语义也属于"会话"） -->
      <span class="b-login" id="b-login"></span>
    </div>
    <div id="browser"></div>

    <!-- 当前账号详情：随**选中账号**变化，鼠标悬停某行时临时预览那一行 -->
    <div class="acct-side" id="acct-side">
      <div class="acct-side-head">账号详情 <span class="muted" id="acct-side-meta"></span></div>
      <div class="acct-detail" id="acct-detail"></div>
    </div>
      </aside>
    </div>
  

    <!-- 下面三块属于「账号与任务」，所以放在这个视图内部：
         切到别的 TAB 时一起隐藏（用户 2026-11-02 要求）。 -->
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

  <!-- 日志记录（用户 2026-11-02：「操作记录是不是要改为日志记录」+
       「黑色的刷课进程记录要不要放在这块区域单独一块」）：
       上半 = 当前账号任务的运行日志（4 秒轮询实时尾巴）；下半 = 操作记录表。 -->
  <section id="sec-ops">
    <h2>日志记录</h2>
    <div class="ops-sub">运行日志 <span class="muted" id="runlog-meta"></span></div>
    <div id="runlog"></div>
    <div class="ops-sub">操作记录</div>
    <div id="ops"></div>
  </section>
  </section>

  <!-- ------------------------------------------------ 视图 2：账号设置（整行全宽） -->
  <section id="view-account" class="pageview" role="tabpanel">

      <div class="card-head">
        <h2 id="acct-form-title">新增账号 <span class="muted" id="acct-form-meta"></span></h2>
        <div class="toolbar">
          <button type="button" class="mini" onclick="resetAccountForm()"
            title="清空表单，回到「新增账号」状态">清空</button>
        </div>
      </div>
      <form id="acct-form" onsubmit="return false">
        <div class="form-cols">
          <section class="fcard">
            <h3>基本信息</h3>
            <div class="fld">
            <label for="f-label">代号</label>
            <input id="f-label" type="text" placeholder="jia" autocomplete="off">
            <div class="hint">小写字母 / 数字 / _ / -，当文件名用</div>
          </div>
          <div class="fld">
            <label for="f-display">显示名</label>
            <input id="f-display" type="text" placeholder="甲同学" autocomplete="off">
            <div class="hint">只用于界面展示</div>
          </div>
          <div class="fld">
            <label for="f-seq">序号</label>
            <input id="f-seq" type="number" min="1" placeholder="自动生成">
            <div class="hint">自动生成，可改</div>
          </div>
          <div class="fld">
            <label for="f-school">学校</label>
            <input id="f-school" type="text" placeholder="示范学院" autocomplete="off">
            <div class="hint">非必填</div>
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
            <div class="hint">非必填，只存本地</div>
          </div>
          </section>
          <section class="fcard">
            <h3>额度与端口</h3>
            <div class="fld">
            <label for="f-port">调试端口</label>
            <div class="row" style="gap:6px">
              <input id="f-port" type="number" min="1024" max="65535" placeholder="9222">
              <button class="mini" type="button" onclick="genPort()">随机</button>
            </div>
            <div class="hint">一账号一端口，点「随机」自动分配</div>
          </div>
          <div class="fld">
            <label for="f-sections">日节数上限</label>
            <input id="f-sections" type="number" min="0" max="40" placeholder="10">
            <div class="hint">0 = 不限；<b>按账号合计</b></div>
          </div>
          <div class="fld">
            <label for="f-minutes">日时长上限</label>
            <input id="f-minutes" type="number" min="0" max="1440" placeholder="600">
            <div class="hint">0 = 不限；建议 ≥ 节数×60</div>
          </div>
          <div class="fld">
            <label for="f-enabled">启用</label>
            <span><input id="f-enabled" type="checkbox" checked> 参与调度</span>
            <div class="hint">停用后不参与调度，数据保留</div>
          </div>
          </section>
          <section class="fcard">
            <h3>登录凭据（只存本地，永不回显）</h3>
            <div class="fld">
            <label for="f-name">账号</label>
            <input id="f-name" type="text" autocomplete="off" placeholder="留空 = 不自动重登">
            <div class="hint" id="hint-name">留空 = 不写入 / 保持原样</div>
          </div>
          <div class="fld">
            <label for="f-pw">密码</label>
            <input id="f-pw" type="password" autocomplete="new-password" placeholder="留空 = 不改">
            <div class="hint">只存 data/accounts.local.json，永不回显</div>
          </div>
            <div class="fld" style="grid-template-columns:110px 1fr">
            <label>自定义</label>
            <div id="f-custom-box" class="custom-box"></div>
          </div>
          </section>
        </div>
        <div class="muted" id="dlg-err" style="color:var(--bad);margin-top:8px"></div>
        <div class="form-actions">
          <span class="sp"></span>
          <button type="button" onclick="resetAccountForm()">取消</button>
          <button class="primary" type="button" onclick="saveAccount()">保存</button>
        </div>
      </form>
  </section>

  <!-- ------------------------------------------------ 视图 3：AI 模型（整行全宽） -->
  <section id="view-ai" class="pageview" role="tabpanel">

      <div class="card-head">
        <h2>AI 模型 <span class="muted" id="ai-meta"></span></h2>
        <div class="toolbar">
          <button class="primary" type="button" onclick="openProvider()"
            title="新增一个厂商：名字 + 接口地址 + 模型">＋ 新增厂商</button>
        </div>
      </div>
      <div id="ai"></div>
  </section>



  

</main>
<div id="toast"></div>

<!-- 新增厂商对话框：只填 name/base_url/model 三件套，Key 另有入口（页面里那个 password 框） -->
<dialog id="dlg-provider">
  <form method="dialog" onsubmit="return false">
    <div class="dlg-head">
      <b>新增厂商</b>
      <button class="mini" onclick="closeProvider()" type="button">关闭</button>
    </div>
    <div class="dlg-body">
      <div class="fld">
        <label for="pf-name">厂商名</label>
        <input id="pf-name" type="text" placeholder="我的接入" autocomplete="off">
        <div class="hint">只作展示；同名会覆盖老的</div>
      </div>
      <div class="fld">
        <label for="pf-url">接口地址</label>
        <input id="pf-url" type="text" placeholder="https://api.example.com/v1" autocomplete="off">
        <div class="hint">OpenAI 兼容地址，结尾不要带 /</div>
      </div>
      <div class="fld">
        <label for="pf-model">模型</label>
        <input id="pf-model" type="text" placeholder="my-model" autocomplete="off">
        <div class="hint">模型名，按厂商文档填</div>
      </div>
      <div class="dlg-err" id="pf-err"></div>
    </div>
    <div class="dlg-foot">
      <button type="button" onclick="closeProvider()">取消</button>
      <button class="primary" type="button" onclick="saveProvider()">保存</button>
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

// 搜索命中高亮：把文本里匹配 query 的片段包成 <mark>。
// 先 esc 再按转义后的串匹配——这样不会把用户的查询当成 HTML 注入。
// query 为空时原样返回（不做任何包裹，避免白加标签）。
function hl(text, query) {
  const s = String(text === undefined || text === null ? "" : text);
  const q = String(query || "").trim();
  if (!q) return esc(s);
  const low = s.toLowerCase(), ql = q.toLowerCase();
  let out = "", from = 0, at = low.indexOf(ql);
  while (at >= 0) {
    out += esc(s.slice(from, at)) + "<mark class=\"hit\">" +
      esc(s.slice(at, at + q.length)) + "</mark>";
    from = at + q.length;
    at = low.indexOf(ql, from);
  }
  return out + esc(s.slice(from));
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
  let html = '<tr class="' + (active ? "cur " : "") + (c.enabled ? "" : "off") +
    '" data-label="' + esc(c.label) + '">';
  html += '<td><input type="checkbox" class="acct-pick"' +
    (SEL.has(c.label) ? " checked" : "") + ' data-label="' + esc(c.label) + '"></td>';
  html += '<td class="num muted">' + esc(pf.seq || "") + "</td>";
  // 搜索命中处高亮：用户搜"甲"时能直接看出这行为什么被筛出来
  const q = (FILTER.q || "").trim();
  const nameTxt = c.display || c.label;
  // 账号格子只放名字（用户 2026-11-02：「这些小字是否可以不要，会撑开列宽」）。
  // 端口/状态两列已删（与右侧「浏览器与会话」重复）；
  // 但「谁在跑」必须一眼看得出，所以用名字前的彩色圆点保留，不占列宽。
  // 复用函数开头已算好的 st（acctStatus(c)），不要重复声明
  const dotTitle = { running: "运行中", full: "额度用完", off: "已停用",
                     idle: "空闲" }[st] || st;
  html += '<td class="c-name"><span class="sdot ' + st + '" title="' + dotTitle +
    '"></span><b>' + hl(nameTxt, q) + "</b></td>";
  // 端口列强制单行（用户 2026-11-02：「端口里面的内容显示一行显示」）：
  // 端口号 + 活/停 标签本来在 66px 里会折成两行，现在列宽加到 84 且 nowrap。
  // 今日列：118px 里塞「12/10 节 ▮▮ + 480 分」会折行 → 分改成只有数字，
  // 单位「分」进 title（悬停可看全），保住单行。
  // 今日 = 「已完成 / 日上限 节」+ 迷你进度条 + 「今日分钟数 分」
  // 补上单位，避免只看到 0/10 ▬ 0 不知道是什么（用户 2026-11-02 问过）
  html += '<td class="c-today" title="今日已学 ' + esc(t.sections || 0) + " 节 / 上限 " +
    (secLim > 0 ? esc(secLim) + " 节" : "不限") + " · 共 " +
    esc(Math.round(Number(t.minutes) || 0)) + ' 分钟">' +
    "<b>" + esc(t.sections || 0) + "</b>/" +
    (secLim > 0 ? esc(secLim) : "不限") + " 节" +
    miniBar(Number(t.sections) || 0, secLim > 0 ? secLim : Math.max(1, Number(t.sections) || 1)) +
    '<span class="muted">' + esc(Math.round(Number(t.minutes) || 0)) + " 分</span></td>";
  html += '<td class="muted">' + (sec.total
    ? esc(sec.done) + "/" + esc(sec.total) + " 节"
    : "—") + "</td>";
  // 登录列只留「快照时间」，把「无凭据」压成一个 ⚠ 图标（全称进 title）：
  // 92px 放不下「无快照 无凭据」两段文字，之前会把行高撑成两行。
  html += '<td class="muted">' + (snap.saved ? esc((snap.at || "").slice(5, 16))
    : "无快照") +
    (c.credentials ? ""
      : ' <span class="tag t-low" title="未配置账号密码：掉线即停，不能自动重登">⚠</span>') +
    "</td>";
  // 操作列：8 栏宽度下放不下 4 个全称按钮，改成「主按钮 + 图标按钮」，
  // 全称放 title 里（悬停可看）。删除仍用文字色区分危险操作。
  html += '<td class="c-ops">';
  if (c.job) {
    html += '<button class="mini danger" onclick="stopJob(\'' + esc(c.label) +
      '\')" title="停止这个账号的学习任务">停止</button>';
  } else if (!c.enabled) {
    html += '<button class="mini" disabled title="账号已停用；先点「启用」">停用中</button>';
  } else if (!c.port_alive) {
    html += '<button class="mini" disabled title="先点上方「启动浏览器」">未启动</button>';
  } else if (left <= 0) {
    html += '<button class="mini" disabled title="今日额度已用完（账本按账号统计）">额度满</button>';
  } else {
    const n = left === Infinity ? 3 : Math.min(3, left);
    html += '<button class="mini primary" onclick="startStudy(\'' + esc(c.label) + "'," + n +
      ')" title="今日还剩 ' + (left === Infinity ? "不限" : left) + ' 节">学习 ' + n +
      " 节</button>";
  }
  html += '<button class="mini" onclick="refreshSectionsFor(\'' + esc(c.label) + "','" +
    esc(c.course_value || "") + '\')" title="重读这门课的节列表（约 1 分钟）">↻</button>';
  html += '<button class="mini" onclick="openAccount(\'' + esc(c.label) +
    '\')" title="编辑：端口 / 日额度 / 学校 / 凭据">✎</button>';
  html += '<button class="mini" onclick="toggleAccount(\'' + esc(c.label) + "'," +
    (c.enabled ? "false" : "true") + ')" title="' +
    (c.enabled ? "停用（不参与调度）" : "重新启用") + '">' +
    (c.enabled ? "⊘" : "✓") + "</button>";
  html += '<button class="mini danger" onclick="deleteAccount(\'' + esc(c.label) +
    '\')" title="只删账号表里这一条，快照与 profile 保留">✕</button>';
  html += "</td></tr>";
  return html;
}

function renderBatchBar(shown) {
  const box = el("acct-batch");
  if (!box) return;
  // 统计（原来单独占表格上方一行，用户要求并进这一行）
  const tot = shown.reduce((a, c) => {
    const t = c.today || {};
    a.sections += Number(t.sections) || 0;
    a.minutes += Number(t.minutes) || 0;
    const s = acctStatus(c);
    a[s] = (a[s] || 0) + 1;
    return a;
  }, { sections: 0, minutes: 0, off: 0, running: 0, full: 0, idle: 0 });

  let html = '<span class="tag t-ai">合计 ' + tot.sections + " 节 · " +
    Math.round(tot.minutes) + " 分钟</span>" +
    '<span class="tag t-learn">运行中 ' + (tot.running || 0) + "</span>" +
    '<span class="tag t-none">空闲 ' + (tot.idle || 0) + "</span>" +
    (tot.full ? '<span class="tag t-low">额度用完 ' + tot.full + "</span>" : "") +
    (tot.off ? '<span class="tag t-none">停用 ' + tot.off + "</span>" : "");

  // 分隔符：统计与批量操作之间
  html += '<span class="bb-sep"></span>';

  const n = SEL.size;
  if (!n) {
    html += '<span class="muted">勾选左侧复选框可批量操作（' +
      shown.length + " 个账号可见）</span>";
    box.innerHTML = html;
    return;
  }
  html += '<b>已选 ' + n + ' 个</b>' +
    '<button class="mini primary" onclick="batchAction(\'start\')">开始学习</button>' +
    '<button class="mini" onclick="batchAction(\'stop\')">停止</button>' +
    '<button class="mini" onclick="batchAction(\'enable\')">启用</button>' +
    '<button class="mini" onclick="batchAction(\'disable\')">停用</button>' +
    '<button class="mini danger" onclick="batchAction(\'delete\')">删除</button>' +
    '<button class="mini" onclick="clearSel()">取消选择</button>';
  box.innerHTML = html;
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
  const allChecked = shown.length > 0 && shown.every((c) => SEL.has(c.label));
  const rows = shown.map((c) => acctTr(
    c, accounts.find((a) => a.label === c.label) || null, state.label)).join("");
  // 列宽：账号列吃余量（min-width:0 + 省略号），其余各列给足不折行的最小宽度。
  // 2026-11-02 按实测预算收紧：固定列 530 → 456px，把省下的 74px 全给账号列。
  //   ☐28 / #30 / 端口 66 / 状态 76 / 今日 118 / 课程 66 / 登录 92 = 476
  let html = '<div class="table-wrap" style="max-height:none">' +
    '<table class="acct-tbl"><thead><tr>' +
    '<th style="width:28px"><input type="checkbox" id="acct-all"' +
    (allChecked ? " checked" : "") + ' title="全选当前可见"></th>' +
    '<th style="width:46px">序号</th>' +
    '<th style="width:18%">账号</th>' +
    '<th style="width:22%">今日</th>' +
    '<th style="width:11%">课程</th>' +
    '<th style="width:11%">登录</th>' +
    '<th style="width:26%">操作</th>' +
    "</tr></thead><tbody>" + rows + "</tbody></table></div>";
  if (!state.accounts_file) {
    html += '<div class="muted" style="margin-top:8px">当前是单账号回落' +
      "（data/account.local.json）。点「＋ 新增账号」会自动建 data/accounts.local.json，" +
      "一条账号一个 port / profile。</div>";
  }
  // 运行日志不再塞在这里（用户 2026-11-02：「黑色的刷课进程记录，
  // 要不要放在操作记录这块区域中单独一块」）→ 见 renderRunLog，
  // 已移到底部「日志记录」区，账号表保持干净。
  el("accounts").innerHTML = html;
}

// ---------------------------------------------------------------- 悬停详情
// 鼠标划到行上 → 浮层出现在行右侧（空间不够就放左侧）；移开自动隐藏。
// 浮层 `pointer-events:none`：它不拦鼠标，所以"移开行"一定触发 mouseout，不会卡住。
// 账号详情：**只显示列表里看不到的信息**（用户 2026-11-02：
// 「账号详情里面的内容如果在账号列表中有的就不需要显示」）。
// 列表已有 → 详情卡不再重复：显示名、端口、状态、今日 N/M 节、课程 done/total、快照时间。
// 这里只留列表装不下的：学校/专业/年级/学号、自定义字段、能否自动重登、正在跑。
// 账号详情：**只显示列表里看不到的信息**（用户 2026-11-02：
// 「在列表中有的，在这里面是否要显示……有些不要两个都显示」）。
// 格式（同日第二次要求）：`标签：值`，标签加粗+暗色，短字段两个一行。
function acctDetailHtml(c, accRow) {
  const pf = (accRow && accRow.profile_fields) || {};
  const cf = (accRow && accRow.custom_fields) || {};
  const has = (v) => v !== undefined && v !== null && String(v).trim() !== "";
  // 单元格：标签 + 值（CSS 用网格对齐，标签自带冒号）
  const cell = (k, v) => '<span class="dk">' + esc(k) + '</span>'
    + '<span class="dv">' + esc(v) + "</span>";
  const row = (k, v) => (has(v)
    ? '<div class="dgrid">' + cell(k, v) + "</div>" : "");
  let h = "";

  // ① 档案：长字段各占一行，年级/学号短、并成一行
  // 用户 2026-11-02：「学校与专业能放在一行吗，能尽量放一行的放一行」
  // → 学校+专业配对、年级+学号配对；太窄时 CSS 自动退回一行一个。
  let prof = "";
  if (has(pf.school) || has(pf.major)) {
    prof += '<div class="dgrid2">'
      + (has(pf.school) ? cell("学校", pf.school) : "")
      + (has(pf.major) ? cell("专业", pf.major) : "")
      + "</div>";
  }
  if (has(pf.grade) || has(pf.student_id)) {
    prof += '<div class="dgrid2">'
      + (has(pf.grade) ? cell("年级", pf.grade) : "")
      + (has(pf.student_id) ? cell("学号", pf.student_id) : "")
      + "</div>";
  }
  if (prof) h += '<div class="dsec">档案</div>' + prof;

  // ② 自定义字段：列表完全看不到
  const cfKeys = Object.keys(cf).filter((k) => has(cf[k]));
  if (cfKeys.length) {
    h += '<div class="dsec">自定义字段</div><div class="dgrid">'
      + cfKeys.map((k) => cell(k, cf[k])).join("") + "</div>";
  }

  // ③ 凭据与运行态：两个短项并一行
  h += '<div class="dsec">凭据与运行</div><div class="dgrid2">'
    + cell("自动重登", c.credentials ? "可以" : "不能（掉线即停）")
    + cell("正在跑", c.job ? (c.job.kind || "运行中") : "空闲")
    + "</div>";
  return h;
}

// 右列「账号详情」：**悬停预览 + 选中常驻**（用户 2026-11-02 两次要求合起来的效果）。
//   - 默认显示当前选中账号（state.label）
//   - 鼠标移到某一行 → 立刻换成那一行的详情（HOVER_LABEL）
//   - 移开 → 回到选中账号
// 与最初的浮层区别：位置固定在这一列，不再跟着鼠标飘、不遮挡表格。
let HOVER_LABEL = "";

function renderAcctSide(state, label) {
  const box = el("acct-detail");
  const meta = el("acct-side-meta");
  if (!box) return;
  const st = state || STATE || {};
  // 优先用显式传入的 label（悬停），否则用当前选中账号
  const want = String(label === undefined ? (st.label || "") : label);
  const c = ((st.cards) || []).find((x) => x.label === want) || null;
  if (!c) {
    box.innerHTML = '<div class="empty" style="padding:6px 0">还没有选中账号。</div>';
    if (meta) meta.textContent = "";
    return;
  }
  const accRow = ((st.accounts) || []).find((a) => a.label === want) || null;
  box.innerHTML = acctDetailHtml(c, accRow);
  // 悬停预览时标出来，避免用户误以为选中变了
  const isHover = want !== (st.label || "");
  if (meta) {
    meta.textContent = "· " + (c.display || c.label) + (isHover ? "（悬停预览）" : "");
  }
}

// 悬停某行 → 右侧详情跟着变；移开 → 回到选中账号
function hoverAcctSide(label) {
  HOVER_LABEL = label || "";
  renderAcctSide(STATE || {}, HOVER_LABEL);
  // 浏览器区也按账号区分（端口/profile/登录快照/会话检查都不同），
  // 所以悬停时一起切过去；移开回落到选中账号（用户 2026-11-02 明确问过这点）。
  renderBrowserFor(STATE || {}, HOVER_LABEL);
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
  // 切换器由 renderAccountsSelect 刷新，这里不用手动改 select
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
// 新增与修改共用同一张内嵌表单（原来是弹窗，用户 2026-10-31 要求并进 TAB）：
// `EDITING` 为空 = 新增，否则是被改的那个代号。
// 密码框永远不回填——面板拿不到密码（后端只回布尔），回填等于自欺。
let EDITING = "";

// 整页级 TAB 切换：账号与任务 / 账号设置 / AI 模型。
// 三个视图（#view-*）各自独占整行，切换只改 class；
// renderAi 只重画 #ai、renderAccounts 只重画 #accounts，都不碰 TAB 条，所以不会被轮询弹回。
const TABS = ["accounts", "account", "ai"];
function showTab(which) {
  const want = TABS.includes(which) ? which : "accounts";
  for (const t of TABS) {
    const btn = el("tab-btn-" + t), view = el("view-" + t);
    if (btn) {
      btn.classList.toggle("on", t === want);
      btn.setAttribute("aria-selected", t === want ? "true" : "false");
    }
    if (view) view.classList.toggle("on", t === want);
  }
  if (want === "ai") loadAi();          // 切到 AI 页时拉一次，保证看到最新
}

// 「＋ 新增账号」：切到账号 TAB 并清空表单
// 「＋ 新增账号」：清空表单 → 切到账号设置页 → 要一个随机端口 → 聚焦代号框
function newAccount() {
  EDITING = "";
  clearAccountFields();
  showTab("account");
  genPort();                    // 自动分配一个未占用端口，省得用户自己填
  const f = el("f-label");
  if (f) f.focus();
}

// 「取消 / 清空」：清空表单并**回到账号列表**（用户 2026-11-02：「取消功能也不能实现」）。
// 原来它调 openAccount("") —— 那只是把表单重置成新增态、人还停在表单页，
// 点完屏幕上几乎没变化，看起来就是"点了没反应"。
function resetAccountForm() {
  EDITING = "";
  clearAccountFields();
  showTab("accounts");
}

// 只清字段，不切页签（内部用）
function clearAccountFields() {
  el("dlg-err").textContent = "";
  el("f-label").value = "";
  el("f-label").disabled = false;
  el("f-display").value = "";
  el("f-seq").value = "";
  el("f-school").value = "";
  el("f-major").value = "";
  el("f-grade").value = "";
  el("f-student").value = "";
  el("f-port").value = "";
  el("f-sections").value = 10;
  el("f-minutes").value = 600;
  el("f-name").value = "";
  el("f-pw").value = "";
  el("f-enabled").checked = true;
  renderCustomFields({});
  const title = el("acct-form-title");
  if (title) title.textContent = "新增账号";
  const meta = el("acct-form-meta");
  if (meta) meta.textContent = "";
  const hint = el("hint-name");
  if (hint) hint.textContent = "留空 = 不自动重登，掉线即停（比乱试安全）";
}

function openAccount(label) {
  EDITING = label || "";
  const c = (STATE && (STATE.cards || []).find(x => x.label === label)) || null;
  // 从 /api/state 的 accounts 里取 profile_fields / custom_fields
  const accRow = (STATE && (STATE.accounts || []).find(x => x.label === label)) || null;
  const pf = (accRow && accRow.profile_fields) || {};
  const cf = (accRow && accRow.custom_fields) || {};
  const title = el("acct-form-title");
  if (title) {
    title.innerHTML = (label ? ("编辑账号 · " + esc(c ? (c.display || label) : label))
      : "新增账号") + ' <span class="muted" id="acct-form-meta"></span>';
  }
  const meta = el("acct-form-meta");
  if (meta) meta.textContent = label ? "（代号不可改）" : "（保存后出现在上方表格）";
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
  showTab("account");
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
    '</div><div class="hint">随账号保存，不参与业务判定</div></div>';
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
  // 表单已内嵌进卡片，没有"关闭"这回事；保留函数名是为了不打断既有调用点，
  // 语义改成"清空并回到新增态"。
  resetAccountForm();
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

// 复制该账号的 Chrome 启动命令（用户 2026-11-02：面板在非交互会话里
// 无法启动 GUI 程序 —— Python 里 Popen 会被沙箱终止、经 PowerShell 也不行。
// 所以给一个一键复制的命令行，用户在自己的 PowerShell 窗口粘一下即可。）
async function copyChromeCmd() {
  const b = (STATE && STATE.browser) || {};
  const chrome = b.chrome || "chrome.exe";
  const port = b.port || "";
  const profile = b.profile || "";
  if (!port || !profile) {
    toast("还没拿到端口 / profile，稍等刷新后再试", true);
    return;
  }
  const cmd = '"' + chrome + '" --remote-debugging-port=' + port
    + ' --user-data-dir="' + profile + '"'
    + " --no-first-run --no-default-browser-check --no-proxy-server";
  try {
    await navigator.clipboard.writeText(cmd);
    toast("已复制启动命令，粘到你的 PowerShell 窗口执行即可");
  } catch (e) {
    // 非 https / 无剪贴板权限时退化为弹窗手动复制
    window.prompt("复制下面这条命令到 PowerShell 执行：", cmd);
  }
}

// 按**指定账号**渲染浏览器区（悬停预览用）；不传 label 就用当前选中账号。
// 数据来源：state.browsers[label]（后端一次算全），没有就退回 state.browser。
function renderBrowserFor(state, label) {
  const want = String(label === undefined ? "" : label) || (state && state.label) || "";
  const all = (state && state.browsers) || {};
  const b = all[want] || (want === (state && state.label) ? (state && state.browser) : null);
  renderBrowser(state, b, want);
}

function renderBrowser(state, browser, label) {
  const b = browser || (state && state.browser) || {};
  const who = String(label || (state && state.label) || "");
  const s = b.session || null;
  const managed = b.backend === "managed";
  // 这段数据是**按账号**的（端口 / profile 目录 / 登录态快照 / 会话检查结果都按 label 取），
  // 所以标题上要写清是谁的浏览器，否则切账号时数字变了用户不知道看的是谁（2026-11-02）。
  const card = ((state && state.cards) || []).find((x) => x.label === who) || null;
  el("browser-meta").textContent = card
    ? "· " + (card.display || card.label) + "（端口 " + (b.port || "?") + "）"
    : "";
  // 三枚标签排一行（用户 2026-11-02 截图要求），见 .b-tags
  let html = '<div class="b-tags">' +
    '<span class="tag ' + (managed ? "t-ai" : "t-bank") + '">' +
    (managed ? "managed · 程序自起（容器形态）" : "cdp · 连你已开的 Chrome") + "</span>" +
    '<span class="tag ' + (b.port_alive ? "t-done" : "t-none") + '">端口 ' +
    esc(b.port) + (b.port_alive ? " 活着" : " 未监听") + "</span>" +
    (b.started_by_panel ? '<span class="tag t-learn">面板启动 · ' + esc(b.since) + "</span>" : "") +
    "</div>";
  // 登录状态写到「会话」行末尾的占位里（用户 2026-11-02：「可以把已登录放在这一行」）
  const loginBox = el("b-login");
  if (loginBox) {
    loginBox.innerHTML = s
      ? '<span class="tag ' + (s.logged_in ? "t-done" : "t-low") + '">' +
        (s.logged_in ? "已登录" : "未登录") + "</span>"
      : "";
  }
  // 紧凑双列：原来 6 行竖表占了整张卡，右边一长条空白。
  // 现在短项并排（Chrome / profile / 凭据），长项（快照、上次检查）独占一行。
  // 「账号密码」改为浏览器行的标签（见下面写 b-cred 占位）；
  // 这里只剩路径等长项，不再有并排短项。
  const credBox = el("b-cred");
  if (credBox) {
    credBox.innerHTML = '<span class="tag ' +
      (b.credentials ? "t-done" : "t-low") + '" title="' +
      (b.credentials ? "已配账号密码：掉线可自动重登" : "未配账号密码：掉线即停") +
      '">' + (b.credentials ? "已配密码" : "无密码") + "</span>";
  }
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
  // 路径类：整行 + 允许换行，不再被 ellipsis 截断
  const paths = [
    ["Chrome", b.chrome_ok ? esc(b.chrome) : "没找到（设 XXT_GXKS_CHROME）"],
    ["profile", b.profile ? esc(b.profile) + (b.profile_set ? "" : "（默认）") : "—"],
  ];
  for (const [k, v] of paths) {
    html += '<div class="bi-line path"><span class="k">' + esc(k) + '</span>' +
      '<span class="v" title="' + v + '">' + v + "</span></div>";
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

// ---------------------------------------------------------------- AI 模型配置
// 厂商列表可增删改；每个配置项都把后端给的说明渲染出来（用户要求"做好对应每个配置项的说明"）。
// ⚠ Key **只提交、不回显**：界面只显示"已配置 / 未配置"，输入框留空表示不修改。
function openProvider() {
  el("dlg-provider").showModal();
  el("pf-name").value = "";
  el("pf-url").value = "";
  el("pf-model").value = "";
  el("pf-err").textContent = "";
}
function closeProvider() { el("dlg-provider").close(); }
async function saveProvider() {
  const name = el("pf-name").value.trim();
  const base_url = el("pf-url").value.trim();
  const model = el("pf-model").value.trim();
  const data = await apiPost("/api/ai/provider",
    { name: name, base_url: base_url, model: model });
  if (!data.ok) {
    el("pf-err").textContent = data.error || "保存失败";
    return;
  }
  closeProvider();
  toast(data.message || "已保存");
  load();
}

function renderAi(state) {
  const a = state.ai || null;
  const box = el("ai");
  if (!a) {
    box.innerHTML = '<div class="empty">AI 配置还没有加载。</div>';
    return;
  }
  const cur = a.current || {};
  const hasKey = !!cur.has_key;
  el("ai-meta").textContent = cur.name
    ? "当前：" + cur.name + (cur.model ? " · " + cur.model : "") +
      (hasKey ? "" : "（未配 Key）")
    : "";

  // 布局（2026-11-02 重做）：左栏 = 厂商与参数，右栏 = 该厂商的 Key + 测试。
  // Key 按厂商分开存，所以右栏标题要写明是哪家的。
  let html = '<div class="ai-wrap">';

  // ---------- 左栏：厂商 + 参数
  html += '<div class="ai-panel">';
  html += '<div class="ai-ptitle">厂商与参数</div>';

  html += '<div class="ai-row">';
  html += '<span class="ai-lab">厂商</span>';
  html += '<select id="ai-provider" title="选一个厂商；名字后面带 ● 表示它已配 Key">';
  for (const p2 of a.providers || []) {
    const sel = (p2.name === cur.name) ? " selected" : "";
    html += '<option value="' + esc(p2.name) + '"' + sel + '>' +
      esc(p2.name) + (p2.has_key ? " ●" : "") + "</option>";
  }
  html += "</select>";
  html += '<button class="mini primary" type="button" onclick="applyProvider()"'
    + ' title="把选中的厂商设为当前使用">应用</button>';
  html += '<button class="mini danger" type="button" onclick="delProvider()"'
    + ' title="删除当前厂商及其 Key（内置的删不掉，只会回落到默认）">删除</button>';
  html += "</div>";

  // 参数：接口地址 / 模型（带拉取）/ 超时
  html += '<div class="ai-grid">';
  html += aiField(a, "base_url", "接口地址", cur.base_url || "", "text");
  // 模型：输入框 + 「拉取」按钮（从厂商 /models 取）
  const mf = (a.fields || []).find((x) => x.key === "model");
  html += '<div class="ai-f">'
    + '<label class="ai-lab" for="ai-model">模型</label>'
    + '<div class="ai-inline">'
    + '<input id="ai-model" type="text" list="ai-models-dl" value="'
    + esc(cur.model || "") + '" placeholder="模型名">'
    + '<button class="mini" type="button" onclick="fetchModels()"'
    + ' title="从该厂商的 /models 接口拉取可用模型">拉取</button>'
    + '</div>'
    + '<datalist id="ai-models-dl"></datalist>'
    + '<div class="ai-hint">' + esc(mf ? mf.desc : "") + "</div></div>";
  html += aiField(a, "timeout", "超时（秒）", String(a.timeout || 60), "number");
  html += "</div>";
  html += "</div>";

  // ---------- 右栏：该厂商的 Key + 测试
  html += '<div class="ai-panel ai-panel-key">';
  html += '<div class="ai-ptitle">API Key'
    + '<span class="tag ' + (hasKey ? "t-done" : "t-low") + '">'
    + (hasKey ? "已配置" : "未配置") + "</span></div>";
  html += '<div class="ai-hint" style="margin:0 0 6px">当前厂商：<b>'
    + esc(cur.name || "") + "</b>（每家的 Key 分开保存，切换不用重填）</div>";
  html += '<input id="ai-key" type="password" autocomplete="new-password"'
    + ' placeholder="' + (hasKey ? "已配置（留空 = 不改）" : "粘贴 Key") + '">';
  html += '<div class="ai-btns">'
    + '<button class="mini primary" type="button" onclick="saveKey()"'
    + ' title="加密保存到本机 data/ 下的库，只属于当前厂商">保存 Key</button>'
    + (hasKey ? '<button class="mini danger" type="button" onclick="clearKey()"'
        + ' title="删掉当前厂商的 Key">清除</button>' : "")
    + "</div>";
  html += '<div class="ai-hint" style="margin-top:6px">'
    + "加密存本机 data/ 下的库，不入库、不回显。" + "</div>";
  if (a.legacy_key) {
    html += '<div class="ai-hint" style="margin-top:6px">'
      + "当前用的是旧版全局 Key；保存一次本厂商的 Key 后会改为按厂商存。</div>";
  }

  // 测试连通
  html += '<div class="ai-test">';
  html += '<button class="mini primary" type="button" onclick="testAi()"'
    + ' style="width:100%" title="真发一次最小对话，验证地址+Key+模型">测试连通</button>';
  html += '<div id="ai-test-msg" class="ai-testmsg"></div>';
  html += "</div>";
  html += '</div>';

  html += "</div>";
  box.innerHTML = html;
}

// 显示测试/拉取的结果：ok=true 绿、false 红
function aiResult(text, ok) {
  const box = el("ai-test-msg");
  if (!box) { toast(text, !ok); return; }
  box.className = "ai-testmsg " + (ok ? "ok" : "bad");
  box.textContent = text;
}

async function fetchModels() {
  const name = (el("ai-provider") || {}).value || "";
  aiResult("正在从「" + name + "」拉取模型列表…", true);
  let data = {};
  try {
    const r = await fetch("/api/ai/models?provider=" + encodeURIComponent(name));
    data = await r.json();
    if (!r.ok) data = { ok: false, error: (data && data.detail) || ("HTTP " + r.status) };
  } catch (e) {
    data = { ok: false, error: "请求失败：" + e.message };
  }
  if (!data.ok) {
    aiResult("拉取失败：" + (data.error || "未知错误"), false);
    return;
  }
  const dl = el("ai-models-dl");
  if (dl) {
    dl.innerHTML = (data.models || []).map((m) =>
      '<option value="' + esc(m) + '"></option>').join("");
  }
  aiResult("取到 " + (data.models || []).length + " 个模型 —— "
    + "点输入框右侧下拉箭头选一个", true);
}

async function testAi() {
  const name = (el("ai-provider") || {}).value || "";
  aiResult("正在测试「" + name + "」…（最多 30 秒）", true);
  const data = await apiPost("/api/ai/test", {
    provider: name,
    base_url: (el("ai-base_url") || {}).value || "",
    model: (el("ai-model") || {}).value || "",
  });
  aiResult(data.message || data.error || "没有返回信息", !!data.ok);
  load();
}

async function clearKey() {
  const name = (el("ai-provider") || {}).value || "";
  if (!confirm("清除「" + name + "」的 API Key？")) return;
  const data = await apiPost("/api/ai/key/delete", { provider: name });
  toast(data.message || data.error || "已清除", !data.ok);
  load();
}

// 一个配置项：标签 + 输入框 + 后端给的说明（用户要求每项都有说明）
function aiField(a, key, cn, val, kind) {
  const f = (a.fields || []).find((x) => x.key === key);
  const desc = f ? f.desc : "";
  return '<div class="ai-f">'
    + '<label class="ai-lab" for="ai-' + key + '">' + esc(cn) + "</label>"
    + '<input id="ai-' + key + '" type="' + kind + '" value="' + esc(val) + '">'
    + '<div class="ai-hint">' + esc(desc) + "</div></div>";
}

async function applyProvider() {
  const name = (el("ai-provider") || {}).value || "";
  const data = await apiPost("/api/ai/current", {
    name: name,
    base_url: (el("ai-base_url") || {}).value || "",
    model: (el("ai-model") || {}).value || "",
    timeout: Number((el("ai-timeout") || {}).value || 0),
  });
  toast(data.message || data.error || "已提交", !data.ok);
  load();
}

async function delProvider() {
  const name = (el("ai-provider") || {}).value || "";
  if (!confirm("删除厂商「" + name + "」？")) return;
  const data = await apiPost("/api/ai/provider/delete", { name: name });
  toast(data.message || data.error || "已提交", !data.ok);
  load();
}

async function saveKey() {
  const name = (el("ai-provider") || {}).value || "";
  const val = (el("ai-key") || {}).value || "";
  const data = await apiPost("/api/ai/key", { provider: name, api_key: val });
  toast(data.message || data.error || "已提交", !data.ok);
  load();
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

  // 待学 / 已完成并排（宽屏两列、窄屏自动堆叠），见 CSS .sec-cols。
  // 上下堆叠时两区各要滚动一次；并排后一屏能同时看到"还差什么"和"已做完什么"。
  html += '<div class="sec-cols">';
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
  html += "</div>";
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

// 运行日志（用户 2026-11-02：「黑色的刷课进程记录……单独一块」）。
// 数据来自 state.jobs[].tail —— 后端已经在读日志文件的尾巴，这里只负责展示。
// 当前选中账号的任务优先显示；其他账号的任务折叠在后面，避免多任务时刷屏。
function renderRunLog(state) {
  const box = el("runlog"), meta = el("runlog-meta");
  if (!box) return;
  const jobs = (state && state.jobs) || [];
  if (!jobs.length) {
    box.innerHTML = '<div class="empty">当前没有运行中的任务（刷课 / 做练习 / 刷新节列表的实时输出会显示在这里）。</div>';
    if (meta) meta.textContent = "";
    return;
  }
  const cur = jobs.find((j) => j.label === state.label) || jobs[0];
  if (meta) {
    meta.textContent = "· " + (cur.label || "") + "（" + (cur.kind || "") +
      " 起于 " + (cur.since || "?") + "，每 4 秒刷新）";
  }
  const tail = (cur.tail || []);
  box.innerHTML = "<pre>" + (tail.length ? esc(tail.join("\n")) : "（日志还没写出内容）") +
    "</pre>";
  if (jobs.length > 1) {
    const others = jobs.filter((j) => j !== cur)
      .map((j) => j.label + "·" + (j.kind || "")).join("、");
    box.innerHTML += '<div class="muted" style="margin-top:4px">还有其他任务在跑：' +
      esc(others) + "（点对应账号行可看它的日志）</div>";
  }
}

// 账号切换器：缓存账号列表，供弹层过滤用
let ASW_LIST = [];

function renderAccountsSelect(state) {
  ASW_LIST = state.accounts || [];
  const cur = ASW_LIST.find((a) => a.label === state.label) || null;
  const nameEl = el("asw-name");
  if (nameEl) nameEl.textContent = cur ? (cur.display || cur.label) : "—";
  const btn = el("asw-btn");
  if (btn) btn.title = "当前：" + (cur ? (cur.display || cur.label) : "—") + "（可搜索切换）";
  // 弹层开着时同步刷新列表（轮询会更新卡片状态）
  if (!el("asw-pop") || !el("asw-pop").hidden) renderAswList();
}

function toggleAsw() {
  const pop = el("asw-pop");
  const btn = el("asw-btn");
  if (!pop || !btn) return;
  const willOpen = pop.hidden;
  if (willOpen) {
    // 用 fixed + 按钮的视口坐标定位：不受 header(sticky) 的层叠上下文影响，
    // 也不会被祖先的 overflow 裁掉（用户反馈过"看不到效果"）。
    const r = btn.getBoundingClientRect();
    const w = Math.max(230, Math.round(r.width));
    pop.style.position = "fixed";
    pop.style.width = w + "px";
    pop.style.left = Math.max(8, Math.round(r.left)) + "px";
    pop.style.top = Math.round(r.bottom + 6) + "px";
  }
  pop.hidden = !willOpen;
  btn.setAttribute("aria-expanded", willOpen ? "true" : "false");
  if (willOpen) {
    ASW_OPENED_AT = Date.now();
    const q = el("asw-q");
    if (q) { q.value = ""; q.focus(); }
    renderAswList();
  }
}

function closeAsw() {
  const pop = el("asw-pop");
  if (pop) pop.hidden = true;
  const btn = el("asw-btn");
  if (btn) btn.setAttribute("aria-expanded", "false");
}

function renderAswList() {
  const box = el("asw-list");
  if (!box) return;
  const q = ((el("asw-q") || {}).value || "").trim().toLowerCase();
  const rows = ASW_LIST.filter((a) => {
    if (!q) return true;
    return (a.label || "").toLowerCase().includes(q)
      || String(a.display || "").toLowerCase().includes(q);
  });
  if (!rows.length) {
    box.innerHTML = '<div class="asw-empty">没有匹配的账号</div>';
    return;
  }
  box.innerHTML = rows.map((a) => {
    const cur = a.label === LABEL ? " cur" : "";
    return '<button type="button" class="asw-item' + cur + '" role="option"'
      + ' aria-selected="' + (cur ? "true" : "false") + '"'
      + ' onclick="pickFromAsw(\'' + esc(a.label) + '\')">'
      + '<span class="asw-name">' + esc(a.display || a.label) + "</span>"
      + '<span class="asw-label">' + esc(a.label) + "</span></button>";
  }).join("");
}

function pickFromAsw(label) {
  closeAsw();
  pick(label);
}

async function loadAi() {
  // 只拉 AI 配置、只重画 #ai：不碰 .tabs/.tabpane，所以切走的标签页不会被弹回来，
  // 也不会打断正在填的账号表单。
  try {
    const r = await fetch("/api/ai");
    const data = await r.json();
    if (data && data.ok !== false) renderAi({ ai: data });
  } catch (e) { /* 轮询会兜底，这里静默 */ }
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
    // 有悬停预览时保持预览（否则 4 秒轮询会把右列弹回选中账号，鼠标还停在行上却跳走）
    if (HOVER_LABEL && (state.cards || []).some((c) => c.label === HOVER_LABEL)) {
      renderBrowserFor(state, HOVER_LABEL);
      renderAcctSide(state, HOVER_LABEL);
    } else {
      HOVER_LABEL = "";
      renderBrowserFor(state, state.label);
      renderAcctSide(state);
    }
    renderAi(state);
    renderSections(state);
    renderWork(state);
    renderRunLog(state);
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
// 点弹层外面 / 按 Esc 关闭账号切换器
// ⚠ 忽略「打开它的那次点击」：onclick 先执行、同一事件继续冒泡到 document，
// 若不加这个判断，会出现"点开就立刻关"的假象（看起来像没反应）。
let ASW_OPENED_AT = 0;
document.addEventListener("click", (e) => {
  if (Date.now() - ASW_OPENED_AT < 200) return;
  const box = el("asw");
  if (box && !box.contains(e.target)) closeAsw();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeAsw();
});

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
// 悬停某行 → 右列详情跟着变；移开 → 回到选中账号（用户 2026-11-02 要求）。
// 用事件委托挂在 #accounts 上：表格每 4 秒整块重绘，直接绑行会随重绘丢失。
el("accounts").addEventListener("mouseover", (e) => {
  const tr = e.target.closest("tr[data-label]");
  if (tr) hoverAcctSide(tr.dataset.label);
});
el("accounts").addEventListener("mouseout", (e) => {
  const tr = e.target.closest("tr[data-label]");
  if (!tr) return;
  // 移到行内子元素不算离开：只有真正离开这一行才回落到选中账号
  const to = e.relatedTarget;
  if (to && tr.contains(to)) return;
  hoverAcctSide("");
});

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
