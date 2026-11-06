"""控制台页面（`panel_gxks/page.py`）的静态断言：只读字符串，不起服务、不连浏览器。

守四件事：
① 节进度必须分成「待学 / 已完成」两块，且折叠状态要能在 4 秒轮询重绘后保住；
② 每个 `onclick="fn(...)"` 调用的 `fn` 在页面里真有定义——2026-10-30 实测踩过：
   「刷新节列表」调的是一个从未定义过的 `refresh()`，点了就 ReferenceError；
③ 进度条的学时口径取 min(已学, 要求)，否则 543/340 这种会把进度顶过 100%；
④ 单文件打包约束：不引任何外部 CDN / 静态资源。
"""

from __future__ import annotations

import re
import unittest

from xxt.panel_gxks.page import PAGE

FUNC_DEF = re.compile(r"(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(")
ONCLICK = re.compile(r'onclick="([A-Za-z_$][\w$]*)\s*\(')

SECTION_IDS = ("view-accounts", "view-account", "view-ai",
               "sec-sections", "sec-work", "sec-ops")


class TestHandlers(unittest.TestCase):
    def test_every_onclick_handler_is_defined(self):
        used = set(ONCLICK.findall(PAGE))
        defined = set(FUNC_DEF.findall(PAGE))
        self.assertEqual(used - defined, set(), "有 onclick 指向了未定义的函数")

    def test_refresh_button_calls_defined_handler(self):
        # 曾经写成 onclick="refresh()"，而 refresh 根本不存在
        self.assertIn('onclick="refreshSections()"', PAGE)

    def test_no_stray_format_placeholder(self):
        # 手写字符串拼接时漏过 %s，页面直接显示字面量
        self.assertNotIn("%s", PAGE)


class TestSectionsLayout(unittest.TestCase):
    def test_split_into_todo_and_done_blocks(self):
        self.assertIn('data-blk="todo"', PAGE)
        self.assertIn('data-blk="done"', PAGE)

    def test_open_state_survives_poll_rebuild(self):
        # 4 秒轮询会整块重绘：折叠状态得记在 JS 里，并在重建时按变量还原
        self.assertIn("SEC_OPEN[d.dataset.blk] = d.open", PAGE)
        self.assertIn('(SEC_OPEN.todo ? " open" : "")', PAGE)
        self.assertIn("if (sig === SEC_SIG) return;", PAGE)

    def test_hours_use_clamped_sum(self):
        self.assertIn("Math.min(numv(r.done_minutes), numv(r.required_minutes))", PAGE)

    def test_progressbar_exposes_aria(self):
        self.assertIn('role="progressbar"', PAGE)
        self.assertIn("aria-valuenow=", PAGE)

    def test_empty_states_when_a_block_has_no_rows(self):
        self.assertIn("待学区已清空", PAGE)
        self.assertIn("还没有学完的节", PAGE)


    def test_numeric_cells_are_right_aligned(self):
        # 实测踩过：th.num 右对齐而 td.num 没补 → 表头贴右、数字靠左，同列不对齐
        self.assertIn("td.num{font-variant-numeric:tabular-nums;white-space:nowrap;"
                      "text-align:right}", PAGE)

    def test_ops_first_columns_do_not_wrap(self):
        # 并排布局下「动作」列曾被挤到 45px，四个字被拓成两行
        self.assertIn("#ops td:nth-child(-n+3){white-space:nowrap}", PAGE)


class TestAccountTable(unittest.TestCase):
    """账号表格（用户 2026-10-30 提议：表格 + 多选批量 + 搜索筛选，替代卡片）。

    为什么改：卡片每张 ~222px 高，20 个账号要 2300px；表格每行 ~44px、20 个约 1040px，
    能在固定区滚动，而且列对齐才能横向比较。
    """

    def test_table_present_and_current_row_highlighted(self):
        self.assertIn("table.acct-tbl", PAGE)
        self.assertIn("table.acct-tbl tbody tr.cur{", PAGE)   # 当前账号一眼看出
        self.assertIn("function acctTr(", PAGE)
        self.assertIn('el("accounts").innerHTML', PAGE)
        self.assertIn("state.cards || []", PAGE)

    def test_row_actions_do_not_also_switch_account(self):
        # 行点击（切账号）与行内按钮/复选框都在**同一个委托**里分流：
        # 按钮先判 `closest("button")` 直接 return，复选框走勾选分支，剩下才 pick。
        # ⚠ 早期版本在 <td> 上写 stopPropagation 挡冒泡，结果把委托自己也挡掉了（多选失灵）。
        self.assertIn('class="c-ops"', PAGE)
        self.assertIn('class="acct-pick"', PAGE)
        self.assertIn('if (e.target.closest("button")) return;', PAGE)
        self.assertNotIn('class="c-ops" onclick="event.stopPropagation()"', PAGE)

    def test_switching_account_forces_sections_rebuild(self):
        # 两个账号的数据签名可能撞车 → 切账号时直接清签名强制重建
        self.assertIn("SEC_SIG = \"\";", PAGE)
        self.assertIn("function pick(", PAGE)

    def test_row_buttons_present(self):
        for fn in ("function startStudySafe(", "function stopJob(",
                   "function refreshSectionsFor("):
            self.assertIn(fn, PAGE)

    # ---------------------------------------------------------------- 多选与批量

    def test_multi_select_survives_poll_rebuild(self):
        """选中集合必须记在 JS 里：表格每 4 秒整块重绘，靠 DOM 会丢。"""
        self.assertIn("const SEL = new Set()", PAGE)
        self.assertIn('closest(".acct-pick")', PAGE)
        self.assertIn('id="acct-all"', PAGE)          # 表头全选

    def test_batch_actions_cover_all_five(self):
        """用户要求批量：开始/停止/启用/停用/删除（2026-11-06 又加「启动浏览器」）。"""
        for act in ("start", "stop", "enable", "disable", "delete", "start-browser"):
            self.assertIn("batchAction(\\'" + act + "\\')", PAGE)
        self.assertIn("/api/accounts/batch", PAGE)

    def test_batch_start_browser_button_only_when_needed(self):
        """批量「启动浏览器」（P1）：只在选中里**真有浏览器没开**时才出现。

        全开着时显示这个按钮点了也没意义，只是添乱。
        """
        bar = PAGE.split("function renderBatchBar(", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("deadSel", bar)
        self.assertIn("port_alive", bar)
        self.assertIn("启动浏览器", bar)
        # 按钮要带上「有几个没开」，让用户知道点下去要等多久
        self.assertIn("个没开", bar)

    def test_batch_start_browser_warns_about_duration(self):
        """串行起 Chrome 每个 10~40 秒，必须先告诉用户要等，否则会重复点。"""
        body = PAGE.split("async function batchAction(", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("start-browser", body)
        self.assertIn("10~40 秒", body)
        self.assertIn("不要重复点", body)

    def test_batch_disable_and_delete_warn_about_running(self):
        """停用/删除遇到运行中的账号会在后端被拒 → 点之前就要说清（2026-11-06）。"""
        body = PAGE.split("async function batchAction(", 1)[1].split("\n}\n", 1)[0]
        self.assertIn('action === "disable"', body)
        self.assertIn("请先「停止」", body)

    def test_batch_delete_asks_twice(self):
        # 批量删除不可逆：前端先 confirm，后端再要 confirm 标记
        self.assertIn("删除选中的 ", PAGE)
        self.assertIn("confirm: true", PAGE)

    def test_running_accounts_stay_selectable_but_say_why(self):
        """运行中的账号**仍可勾选**，但必须说清后果（2026-11-06 用户提问）。

        不能直接禁用复选框：批量「停止」恰恰需要选中正在跑的账号。
        所以只在 title / 按钮文案里解释「开始学习会跳过它」。
        """
        tr = PAGE.split("function acctTr(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("acct-pick", tr)
        self.assertNotIn("disabled", tr.split("acct-pick")[1].split(">")[0],
                         "运行中不许直接禁用复选框——批量停止要用到它")
        self.assertIn("批量「开始学习」会自动跳过它", tr)

    def test_batch_bar_warns_about_running_selection(self):
        """批量栏要提前算出「选中的里有几个在跑」，并写进按钮文案。"""
        bar = PAGE.split("function renderBatchBar(", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("runningSel", bar)
        self.assertIn("跳过 ", bar)
        self.assertIn("其中 ", bar)          # 「其中 N 个运行中」标签

    def test_batch_start_confirm_explains_skipping(self):
        body = PAGE.split("async function batchAction(", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("会被自动跳过", body)
        self.assertIn("不会重复开", body)

    def test_start_study_surfaces_browser_error(self):
        """单账号学习也要把「浏览器没起」的原文摊开（不能一闪而过的 toast）。"""
        self.assertIn("async function startStudySafe(", PAGE)
        body = PAGE.split("async function startStudySafe(", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("need_browser", body)
        self.assertIn("showNotice", body)

    # ---------------------------------------------------------------- 搜索与筛选

    def test_search_and_filters_present(self):
        for eid in ("acct-q", "acct-status", "acct-port", "acct-cred"):
            self.assertIn('id="' + eid + '"', PAGE)
        self.assertIn("function visibleCards(", PAGE)
        self.assertIn("function readFilter(", PAGE)

    def test_search_covers_profile_and_custom_fields(self):
        """搜索要能搜学校/专业/年级/学号/自定义字段，不只搜名字。"""
        self.assertIn("function acctSearchText(", PAGE)
        for key in ("pf.school", "pf.major", "pf.grade", "pf.student_id"):
            self.assertIn(key, PAGE)
        self.assertIn("Object.values(cf).join", PAGE)

    def test_quick_filter_chips_and_clear(self):
        self.assertIn("function quickFilter(", PAGE)
        self.assertIn("function clearFilter(", PAGE)
        self.assertIn("quickFilter('running')", PAGE)
        self.assertIn("quickFilter('noconf')", PAGE)

    def test_toolbar_is_static_so_search_keeps_focus(self):
        """工具栏放在轮询重绘的容器**外面**，否则每 4 秒搜索框掉一次焦点。"""
        i_tools = PAGE.index('class="acct-tools"')
        i_slot = PAGE.index('<div id="accounts"></div>')
        self.assertLess(i_tools, i_slot)

    # ---------------------------------------------------------------- 右列账号详情

    def test_side_detail_panel_is_persistent(self):
        """账号详情：2026-11-02 从「悬停浮层」改成「右列常驻卡」。

        用户原话：「这个自动弹出的卡片区能否与浏览器与会话在同一列显示，
        不跟随，但会随着我选择账号的不同而跟随变化」。
        """
        self.assertIn('id="acct-detail"', PAGE)
        self.assertIn("function renderAcctSide(", PAGE)
        # 跟随选中账号，不再跟随鼠标：悬停相关函数应消失
        self.assertNotIn("function showAcctDetail(", PAGE)
        self.assertNotIn("function hideAcctDetail(", PAGE)

    def test_hover_previews_and_selection_persists(self):
        """2026-11-02 最终形态：悬停预览 + 移开回到选中账号。

        用户先要求"改成常驻不跟鼠标"，随后补充"鼠标移到某个账号时右侧栏要跟随变化"
        —— 两者结合：位置固定不飘，内容随悬停变，移开回落到选中账号。
        """
        self.assertIn("function renderAcctSide(", PAGE)
        self.assertIn("function hoverAcctSide(", PAGE)
        # 悬停必须走事件委托（表格每 4 秒重绘，直接绑行会丢）
        self.assertIn('el("accounts").addEventListener("mouseover"', PAGE)
        self.assertIn('el("accounts").addEventListener("mouseout"', PAGE)
        # 移开时要回落到选中账号（传空 label），不是清空
        self.assertIn('hoverAcctSide("")', PAGE)
        # 悬停态要能看出来，避免误以为选中变了
        self.assertIn("悬停预览", PAGE)

    def test_detail_sits_in_right_column(self):
        """详情卡必须在账号视图的右栏（.split-side）里，而不是页面末尾的独立浮层。"""
        side = PAGE.split('class="split-side"', 1)[1].split("</aside>", 1)[0]
        self.assertIn('id="acct-detail"', side)
        self.assertIn('class="acct-side"', side)
        # 浏览器卡也在同一栏
        self.assertIn('id="browser"', side)


class TestAiPanelUI(unittest.TestCase):
    """AI 模型配置界面（第二批，为 B5 回填做准备）。

    用户要求：可自定义模型厂家 + 可添加到厂家列表 + 可修改 + 每项要有说明。
    2026-10-31：与「账号设置」并成一张卡的两个 TAB（`#pane-ai`），布局重排成左右两栏。
    """

    def test_lives_in_full_page_view(self):
        """2026-11-02：AI 页签必须占**整个区域**，不是只占账号列表那几栏。"""
        self.assertIn('<section id="view-ai" class="pageview"', PAGE)
        self.assertIn('onclick="showTab(\'ai\')"', PAGE)
        self.assertIn("function showTab(", PAGE)
        # 整页视图：默认 span 12（占满整行）
        self.assertIn(".pageview{display:none;grid-column:span 12}", PAGE)
        self.assertNotIn('<section id="sec-ai">', PAGE)

    def test_entry_points(self):
        self.assertIn("function renderAi(", PAGE)
        self.assertIn("function applyProvider(", PAGE)
        self.assertIn("function delProvider(", PAGE)
        self.assertIn("function saveKey(", PAGE)
        self.assertIn("function openProvider(", PAGE)
        self.assertIn("＋ 新增厂商", PAGE)

    def test_layout_is_two_columns(self):
        """左栏「厂商与参数」、右栏「Key + 测试」（2026-11-02 重做）。"""
        self.assertIn('class="ai-wrap"', PAGE)
        self.assertIn('class="ai-panel"', PAGE)
        self.assertIn("ai-panel-key", PAGE)
        self.assertIn(".ai-wrap{display:grid;grid-template-columns:1fr 340px", PAGE)

    def test_has_test_connection_button(self):
        """用户：「增加测试联通 AI 模型的按钮和成功或者错误提示信息」。"""
        self.assertIn("function testAi(", PAGE)
        self.assertIn("测试连通", PAGE)
        self.assertIn('/api/ai/test', PAGE)
        # 结果提示区：成功绿、失败红
        self.assertIn('id="ai-test-msg"', PAGE)
        self.assertIn(".ai-testmsg.ok{", PAGE)
        self.assertIn(".ai-testmsg.bad{", PAGE)

    def test_has_fetch_models_button(self):
        """用户：「模型名称能从对应的厂商获取到」。"""
        self.assertIn("function fetchModels(", PAGE)
        self.assertIn("/api/ai/models", PAGE)
        self.assertIn('list="ai-models-dl"', PAGE)
        self.assertIn('id="ai-models-dl"', PAGE)

    def test_key_is_per_provider_in_ui(self):
        """Key 按厂商分开存：界面要显示当前是哪家、并支持清除。"""
        self.assertIn("function clearKey(", PAGE)
        self.assertIn('/api/ai/key/delete', PAGE)
        self.assertIn("provider: name", PAGE)
        self.assertIn("每家的 Key 分开保存", PAGE)

    def test_provider_select_marks_configured(self):
        self.assertIn('(p2.has_key ? " ●" : "")', PAGE)

    def test_each_config_item_renders_its_description(self):
        """每个配置项都把后端给的 `desc` 渲染出来——用户明确要求"做好说明"。"""
        self.assertIn("function aiField(", PAGE)
        self.assertIn("ai-hint", PAGE)          # 说明用这个类
        self.assertIn("esc(desc)", PAGE)

    def test_key_is_password_and_never_prefilled(self):
        """Key 只提交、不回显：type=password 且没有 value 回填。"""
        self.assertIn('id="ai-key" type="password"', PAGE)
        self.assertNotIn('id="ai-key" value=', PAGE)

    def test_ai_endpoints_used(self):
        for path in ("/api/ai/current", "/api/ai/provider/delete",
                     "/api/ai/key", "/api/ai/provider"):
            self.assertIn(path, PAGE)

    def test_key_note_mentions_local_encrypted(self):
        """Key 说明要讲清「存本机、不回显」。2026-11-02 文案收紧后仍保留这两层意思。"""
        self.assertIn("加密存本机", PAGE)
        self.assertIn("不回显", PAGE)


class TestLayout20261031(unittest.TestCase):
    """2026-10-31 这轮布局改造（用户逐条提的要求）。

    每条都对应一个具体诉求，钉死实现，防止后续改样式时被悄悄改回去：
    ① 「AI 模型配置和新建账号弄成一块，TAB 标签页」
    ② 「账号表格每列列宽是否需要调一下」
    ③ 「右边留出区域放每个账号对应的浏览和会话」
    ④ 「搜索中也要能匹配到账号，匹配到突出显示」
    ⑤ 「节进度待学区与完成区是否考虑并排排列」
    """

    # ① TAB 化
    def test_three_page_level_tabs(self):
        """三个**整页级** TAB（用户 2026-11-02：「要占整个区域」）。"""
        self.assertIn('<nav class="pagetabs"', PAGE)
        for t in ("accounts", "account", "ai"):
            self.assertIn(f'id="tab-btn-{t}"', PAGE)
            self.assertIn(f'id="view-{t}"', PAGE)
        # 旧的独立区块已并入视图
        self.assertNotIn('<section id="sec-accounts">', PAGE)
        self.assertNotIn('<section id="sec-settings">', PAGE)
        # 切换靠 class，不靠重绘
        self.assertIn('classList.toggle("on"', PAGE)

    def test_tab_switch_does_not_get_reset_by_polling(self):
        """renderAi 只重画 #ai，绝不能动 .tabs/.tabpane 的 class。"""
        # 取 renderAi 函数体，确认里面没有操作 tab 元素
        body = PAGE.split("function renderAi(", 1)[1].split("\nfunction aiField", 1)[0]
        self.assertNotIn("tab-btn", body)
        self.assertNotIn("tabpane", body)
        self.assertIn('box.innerHTML = html', body)

    def test_load_ai_only_repaints_ai_block(self):
        body = PAGE.split("async function loadAi(", 1)[1].split("async function load(", 1)[0]
        self.assertIn('renderAi({ ai: data })', body)
        self.assertNotIn("renderAccounts", body)

    # ② 列宽
    def test_table_columns_have_explicit_widths(self):
        """列宽用百分比 + table-layout:fixed。

        2026-11-02：原来 auto 布局下「账号」列（只写了 min-width）会吸走全部余量，
        名字靠左、右边一大片空白；改 fixed + 百分比后余量按比例分给各列。
        """
        head = PAGE.split('table class="acct-tbl"', 1)[1].split("</thead>", 1)[0]
        self.assertIn('>账号</th>', head)
        self.assertIn('>操作</th>', head)
        # 2026-11-02 二次调整：操作列 20%→26%（原来放不下「未启动」+4 个图标，
        # 被 ellipsis 截断；余量从今日/课程/登录等比让出）
        for w in ("width:18%", "width:22%", "width:11%", "width:26%"):
            self.assertIn(w, head)
        self.assertIn("table.acct-tbl td.c-ops{white-space:nowrap;"
                      "text-align:right;overflow:visible}", PAGE)
        self.assertIn("table.acct-tbl{table-layout:fixed}", PAGE)
        # ⚠ 只能看账号表的表头：`>状态</th>` 在「节进度」表里也有
        self.assertNotIn(">端口</th>", head)
        self.assertNotIn(">状态</th>", head)
        self.assertIn(">今日</th>", head)
        self.assertIn(">课程</th>", head)

    def test_headers_are_centered(self):
        """用户 2026-11-02：「账号列表的标题要居中」。"""
        self.assertIn("table.acct-tbl thead th{text-align:center}", PAGE)

    def test_today_column_shows_units(self):
        """「今日」列要带单位，否则只看到 0/10 ▬ 0 不知道是什么。"""
        body = PAGE.split("function acctTr(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn('" 节"', body)        # 节数带「节」
        self.assertIn(" 分</span>", body)   # 分钟带「分」

    def test_column_budget_leaves_room_for_name(self):
        """2026-11-02 实测修正：8+4 时账号列只剩 255px，改 9+3 后 414px。

        固定列合计必须留够空间，否则账号名被压到下限、表格出横向滚动。
        """
        # 2026-11-02：改用百分比，核对「账号」列在 8fr 宽度下够不够放名字
        inner = 1280 - 36                               # main 内宽
        col = (inner - 14 * 11) / 12
        table = col * 8 + 14 * 7 - 40                   # 账号表可用宽度
        account_col = table * 0.18                      # 账号列 18%
        self.assertGreaterEqual(account_col, 120,        # 放得下「林鑫阳」+ 状态圆点
                                f"账号列只有 {account_col:.0f}px，会挤")
        today_col = table * 0.22
        self.assertGreaterEqual(today_col, 150,
                                f"今日列只有 {today_col:.0f}px，单位可能被截断")

    # ③ 右侧浏览器与会话
    def test_table_sits_beside_browser_side(self):
        """账号视图内部：左表格 + 右栏（浏览器 + 详情）。"""
        self.assertIn("#view-accounts .split{display:grid;"
                      "grid-template-columns:minmax(0,8fr) minmax(0,4fr)", PAGE)
        self.assertIn('class="split-main"', PAGE)
        self.assertIn('class="split-side"', PAGE)

    # ④ 搜索高亮
    def test_search_hits_are_highlighted(self):
        self.assertIn("function hl(", PAGE)
        self.assertIn('<mark class=\\"hit\\">', PAGE)
        self.assertIn("mark.hit{background:#fff3bf", PAGE)
        # 名称用上高亮（副标题小字、端口列均在 2026-11-02 移除）
        self.assertIn("hl(nameTxt, q)", PAGE)

    def test_highlight_escapes_before_wrapping(self):
        """先转义再包 <mark>：不能把用户的查询当 HTML 注入。"""
        body = PAGE.split("function hl(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("esc(s.slice(", body)

    def test_empty_query_does_not_wrap(self):
        body = PAGE.split("function hl(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("if (!q) return esc(s);", body)

    # ⑤ 节进度并排
    def test_todo_and_done_are_side_by_side(self):
        self.assertIn('class="sec-cols"', PAGE)
        self.assertIn(".sec-cols{display:grid;grid-template-columns:1fr 1fr", PAGE)
        # 窄屏要能堆叠，不能硬挤两列
        self.assertIn("@media (max-width:999px){", PAGE)


class TestLayout20261102(unittest.TestCase):
    """2026-11-02 这轮（用户逐条提的四点）。

    ① 账号区与右侧浏览器卡对齐
    ② 浏览器卡不随滚动条移动（去掉 sticky）
    ③ 悬停弹出的详情卡 → 右列常驻、跟随选中账号
    ④ 区块顺序：账号 → 节进度 → 本次作答 → 账号设置/AI → 操作记录
    """

    def test_browser_side_no_longer_sticky(self):
        """用户原话：「浏览器与会话这个区域会随滚动条移动」→ 要固定住。"""
        self.assertNotIn("#sec-browser", PAGE)
        # 右栏不能有 sticky
        split_rule = PAGE.split(".split-main,.split-side{", 1)[1].split("}", 1)[0]
        self.assertNotIn("sticky", split_rule)

    def test_cards_are_equal_height(self):
        """用户原话：「账号与任务这块下右侧栏下面没等高呀」。

        等高靠三件事：两卡都 flex column、栅格行默认 stretch、内部可滚动区 flex:1。
        """
        # 两栏必须用**同一把尺子**：.split 上给统一 height，内部各自滚动。
        # 2026-11-02 改 62vh：calc(100vh-172px) 几乎占满整屏，
        # 把节进度/本次作答/操作记录全挤到一屏之外（用户："它们去哪里了"）。
        self.assertIn("height:82vh;min-height:500px;max-height:1000px}", PAGE)
        self.assertIn(".split-main,.split-side{display:flex;flex-direction:column", PAGE)
        self.assertIn(".split-main>#accounts{overflow:auto;flex:1 1 auto", PAGE)
        self.assertIn(".split-side>#acct-side{flex:1 1 auto", PAGE)

    def test_section_order(self):
        """三个视图在 main 下平级；节进度等三块**在 view-accounts 内部**。

        用户 2026-11-02：「三个标签页里面都有把节进度、本次作答、操作记录都包进了它里面，
        只需要在账号与任务标签页下面有就行」。
        """
        # sec-* 现在嵌在 view-accounts 里，用 view-* 单独判序
        views = re.findall(r'<section id="(view-[a-z]+)"', PAGE)
        self.assertEqual(views, ["view-accounts", "view-account", "view-ai"],
                         f"三个视图顺序不对：{views}")
        view = PAGE.split('id="view-accounts"', 1)[1]
        for blk in ("sec-sections", "sec-work", "sec-ops"):
            self.assertIn(blk, view, f"{blk} 应属于账号视图")

    def test_settings_is_full_page_view(self):
        """账号设置是**整页视图**，不是挤在列表旁边的小面板。

        用户 2026-11-02：「账号设置与AI模型这两个标签页是要占整个区域的，
        不是只显示账号与任务列表区域的」。
        """
        self.assertNotIn('id="sec-settings"', PAGE)
        view = PAGE.split('id="view-account"', 1)[1].split("</section>", 1)[0]
        self.assertIn('id="acct-form"', view)
        # 表单必须直接属于整页视图，不能被 .split 包成半宽
        self.assertNotIn('class="split"', view)


class TestInfoShownOnlyOnce(unittest.TestCase):
    """一处信息只在一个地方显示（用户 2026-11-02）。

    用户原话：「在列表中有的，在这里面是否要显示，还是说在这显示的，
    在列表中不要显示，有些不要两个都显示」。

    划分：
      列表 = 横向对比（账号名/端口/状态/今日/课程/快照时间）
      详情卡 = 列表装不下的细项（学校/专业/年级/学号/自定义/凭据/运行态）
    """

    def test_row_has_no_school_subtitle(self):
        """账号格子里不要再有「学校·专业·年级」小字（会把列宽撑开）。"""
        body = PAGE.split("function acctTr(", 1)[1].split("\nfunction ", 1)[0]
        self.assertNotIn('class="sub"', body)
        self.assertNotIn("const sub =", body)

    def test_detail_keeps_profile_fields(self):
        """小字去掉后，学校/专业/年级/学号必须由详情卡承担。"""
        body = PAGE.split("function acctDetailHtml(", 1)[1].split("\nfunction ", 1)[0]
        for k in ("学校", "专业", "年级", "学号"):
            self.assertIn(k, body, f"详情卡缺 {k}")

    def test_detail_drops_list_duplicates(self):
        """今日节数/时长/课程进度列表列已有 → 详情卡不再重复。"""
        body = PAGE.split("function acctDetailHtml(", 1)[1].split("\nfunction ", 1)[0]
        for k in ("今日节数", "今日时长", "这门课", "凭据文件"):
            self.assertNotIn(k, body, f"详情卡不该再显示 {k}（列表已有或恒定值）")

    def test_detail_keeps_credential_and_job(self):
        body = PAGE.split("function acctDetailHtml(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("自动重登", body)
        self.assertIn("正在跑", body)


class TestAccountSwitcher(unittest.TestCase):
    """头部账号切换器（用户 2026-11-02 截图：「账号多的时候这个列表就很长」）。

    方案：不用 <select>（账号一多就长得没法用），改成
    「当前账号按钮 + 可搜索弹层 + 限高滚动」。
    保留切换入口，同时不把表格的搜索搬上来（两者目的不同：
    头部 = 切到某个账号；表格搜索 = 筛选正在看的这张表）。
    """

    def test_no_long_select_anymore(self):
        self.assertNotIn('<select id="account"></select>', PAGE)

    def test_switcher_markup(self):
        for eid in ("asw", "asw-btn", "asw-name", "asw-pop", "asw-q", "asw-list"):
            self.assertIn(f'id="{eid}"', PAGE)

    def test_has_search_box(self):
        self.assertIn('id="asw-q"', PAGE)
        self.assertIn('oninput="renderAswList()"', PAGE)
        self.assertIn("function renderAswList(", PAGE)

    def test_list_is_height_capped_and_scrolls(self):
        """关键：列表必须限高滚动，否则账号多时又把弹层撑得很长。"""
        body = PAGE.split(".asw-list{", 1)[1].split("}", 1)[0]
        self.assertIn("max-height:320px", body)
        self.assertIn("overflow:auto", body)

    def test_filters_by_label_and_display(self):
        body = PAGE.split("function renderAswList(", 1)[1].split("\n}", 1)[0]
        self.assertIn("a.label", body)
        self.assertIn("a.display", body)

    def test_closes_on_outside_click_and_esc(self):
        self.assertIn("function closeAsw(", PAGE)
        self.assertIn("if (box && !box.contains(e.target)) closeAsw();", PAGE)
        self.assertIn('if (e.key === "Escape") closeAsw();', PAGE)

    def test_picking_switches_account(self):
        body = PAGE.split("function pickFromAsw(", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("closeAsw()", body)
        self.assertIn("pick(label)", body)

    def test_popover_uses_fixed_positioning(self):
        """弹层必须 fixed 定位（2026-11-02 用户反馈看不到效果；
        absolute 会被 sticky header 的层叠上下文影响）。"""
        body = PAGE.split(".asw-pop{", 1)[1].split("}", 1)[0]
        self.assertIn("position:fixed", body)
        self.assertIn("z-index:120", body)
        # hidden 时明确隐藏
        self.assertIn(".asw-pop[hidden]{display:none}", PAGE)

    def test_ignores_the_opening_click(self):
        """点开它的那次 click 冒泡到 document 时不能立刻关掉。"""
        self.assertIn("ASW_OPENED_AT", PAGE)
        self.assertIn("Date.now() - ASW_OPENED_AT < 200", PAGE)


class TestLoginTagOnSessionRow(unittest.TestCase):
    """「已登录」放在会话那一行（用户 2026-11-02：「可以把已登录放在这一行」）。

    语义上也更合理：登录状态属于「会话」，与检查/恢复/重登同类。
    """

    def test_placeholder_in_session_row(self):
        # 会话行的 bgrp 里要有 b-login 占位
        m = PAGE.split('class="bgrp-lab">会话', 1)
        self.assertEqual(len(m), 2, "找不到会话行")
        seg = m[1].split("</div>", 1)[0]
        self.assertIn('id="b-login"', seg)

    def test_login_rendered_into_placeholder(self):
        body = PAGE.split("function renderBrowser(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn('el("b-login")', body)
        self.assertIn('s.logged_in ? "已登录" : "未登录"', body)

    def test_not_in_tags_row_anymore(self):
        """登录标签不应再渲染进 .b-tags 那一行。"""
        seg = PAGE.split("let html = '<div class=\"b-tags\">'", 1)
        self.assertEqual(len(seg), 2, "找不到 b-tags 渲染")
        head = seg[1].split(";", 1)[0]
        self.assertNotIn("logged_in", head)

    def test_tag_text_is_short(self):
        self.assertNotIn('"页面上"', PAGE)


class TestBrowserTagsOneLine(unittest.TestCase):
    """浏览器状态标签排一行（用户 2026-11-02 截图：「这三个一行显示」）。"""

    def test_tags_use_dedicated_nowrap_row(self):
        self.assertIn('class="b-tags"', PAGE)
        body = PAGE.split(".b-tags{", 1)[1].split("}", 1)[0]
        self.assertIn("flex-wrap:nowrap", body)
        self.assertIn("overflow:hidden", body)

    def test_first_tag_can_shrink(self):
        """第一枚（cdp/managed）允许省略，保证后面「端口」「登录」始终可见。"""
        self.assertIn(".b-tags .tag:first-child{flex:0 1 auto", PAGE)

    def test_login_text_shortened(self):
        """用户：「把页面上已登录，直接改为已登录」。"""
        self.assertNotIn('"页面上"', PAGE)
        self.assertIn('(s.logged_in ? "已登录" : "未登录")', PAGE)


class TestIdHasMatchingClass(unittest.TestCase):
    """通用防护：id 元素若被 class 选择器样式化，就必须带那个 class。

    2026-11-02 教训：`<div id="acct-detail">` 漏了 `class="acct-detail"`，
    导致 `.acct-detail ...` 共 12 条规则全部失效（冒号/加粗/网格/分组条都没了），
    而结构测试仍全绿 —— 只查 id 存在是不够的。
    """

    # 静态标记（<script> 之前）里的元素开标签
    MARKUP = PAGE.split("<script", 1)[0]

    def _ids_and_tags(self):
        out = {}
        for m in re.finditer(r"<[a-zA-Z][^>]*?\bid=\"([\w-]+)\"[^>]*>", self.MARKUP):
            out[m.group(1)] = m.group(0)
        return out

    # 合法的同名不同对象（不是 bug，别硬套）：
    #   toast     → #toast 是容器，.toast 是 JS 创建的每条消息
    #   asw-name  → #asw-name 是按钮里的当前账号名，.asw-name 是弹层列表项里的名字
    ALLOW = {"toast", "asw-name"}

    def test_no_class_style_without_class_attribute(self):
        """找出「CSS 定义了 .X 但 id=X 的元素没写 class=X」的全部情况。"""
        missing = []
        for eid, tag in self._ids_and_tags().items():
            if eid in self.ALLOW:
                continue
            # CSS 里是否有针对 .X 的规则？
            if not re.search(r"\.\b" + re.escape(eid) + r"\s*\{", PAGE):
                continue
            # 元素是否带 class=X？
            m = re.search(r'class="([^"]*)"', tag)
            classes = (m.group(1) if m else "").split()
            if eid not in classes:
                missing.append((eid, tag[:80]))
        self.assertEqual(missing, [], "以下元素的 class 与 CSS 选择器不匹配，样式会失效："
                         + repr(missing))

    def test_detail_panel_has_class(self):
        """单独钉死这次踩过的坑。"""
        m = re.search(r"<div[^>]*id=\"acct-detail\"[^>]*>", self.MARKUP)
        self.assertIsNotNone(m)
        self.assertIn('class="acct-detail"', m.group(0))

    def test_switcher_parts_have_class(self):
        for cid in ("asw-pop", "asw-list", "asw-btn"):
            m = re.search(r'<[^>]*id="' + cid + r'"[^>]*>', self.MARKUP)
            self.assertIsNotNone(m, f"找不到 {cid}")
            self.assertIn(f'class="{cid}"', m.group(0), f"{cid} 缺同名 class")


class TestStickyTableHeader(unittest.TestCase):
    """账号列表的表头要吸顶（用户 2026-11-02：「列标题固定在最上面，像表格冻结」）。

    踩过的坑：thead 的 sticky 只相对**自己的滚动容器**生效。
    原来 .table-wrap 有 overflow:auto（它自己成了滚动容器却又从不滚动），
    真正滚动的是外层 #accounts → 表头完全不吸顶。
    """

    def test_thead_is_sticky(self):
        self.assertIn("position:sticky", PAGE.split(".table-wrap thead th{", 1)[1][:80])

    def test_table_wrap_does_not_create_scroll_container(self):
        """关键回归点：账号表里的 .table-wrap 必须 overflow:visible。"""
        body = PAGE.split(".split-main>#accounts .table-wrap{", 1)[1].split("}", 1)[0]
        self.assertIn("overflow:visible", body,
                      ".table-wrap 若 overflow:auto 会截断 sticky，表头不吸顶")

    def test_accounts_is_the_scroll_container(self):
        body = PAGE.split(".split-main>#accounts{", 1)[1].split("}", 1)[0]
        self.assertIn("overflow:auto", body)

    def test_sticky_header_has_opaque_background(self):
        """吸顶后必须有实底，否则数据行会从表头下面透出来。"""
        body = PAGE.split(".table-wrap thead th{", 1)[1].split("}", 1)[0]
        self.assertIn("background:", body)


class TestMobileResponsive(unittest.TestCase):
    """移动端可用（用户 2026-11-02 定成长期约定，已写入 AGENTS.md）。

    约定要点：375px 宽可用；窄屏不压扁表格（给 min-width 让容器横滑）；
    工具条换行；断点 ≤760px 按手机处理。
    """

    def test_viewport_meta(self):
        self.assertIn('name="viewport"', PAGE)
        self.assertIn("width=device-width", PAGE)

    def test_has_phone_breakpoint(self):
        self.assertIn("@media (max-width:760px)", PAGE)
        self.assertIn("@media (max-width:420px)", PAGE)

    def test_table_scrolls_instead_of_squashing(self):
        """窄屏给表格 min-width，让容器横滑，而不是把 9 列挤成竖条。"""
        block = PAGE.split("@media (max-width:760px){", 1)[1].split("@media (max-width:420px)", 1)[0]
        self.assertIn("table.acct-tbl{min-width", block)

    def test_toolbar_wraps_on_phone(self):
        block = PAGE.split("@media (max-width:760px){", 1)[1].split("@media (max-width:420px)", 1)[0]
        self.assertIn(".acct-tools{flex-wrap:wrap", block)

    def test_form_and_ai_single_column_on_phone(self):
        block = PAGE.split("@media (max-width:760px){", 1)[1].split("@media (max-width:420px)", 1)[0]
        self.assertIn(".form-cols{grid-template-columns:1fr}", block)
        self.assertIn(".ai-wrap{grid-template-columns:1fr}", block)

    def test_split_stacks_on_phone(self):
        block = PAGE.split("@media (max-width:760px){", 1)[1].split("@media (max-width:420px)", 1)[0]
        self.assertIn("flex-direction:column", block)

    def test_touch_targets_enlarged(self):
        block = PAGE.split("@media (max-width:760px){", 1)[1].split("@media (max-width:420px)", 1)[0]
        self.assertIn("button{padding:8px 12px}", block)


class TestAccountFormColumns(unittest.TestCase):
    """账号设置：三栏卡片（用户 2026-11-02：「把它弄成3栏并列显示是否可行」）。

    踩过的坑：.fcard 是 <section>，而基础规则有 section{grid-column:span 12}，
    不显式覆盖就会每张卡占满整行 → 三栏退化成上下堆叠（用户：「没有看到 3 栏效果」）。
    """

    def test_three_cards(self):
        view = PAGE.split('id="view-account"', 1)[1]
        self.assertEqual(view.count('class="fcard"'), 3)
        for title in ("基本信息", "额度与端口", "登录凭据"):
            self.assertIn(title, view)

    def test_grid_is_three_columns(self):
        self.assertIn(".form-cols{display:grid;"
                      "grid-template-columns:repeat(3,minmax(0,1fr))", PAGE)

    def test_cards_override_section_span(self):
        """核心回归点：.fcard 必须写 grid-column:auto，否则又变回单列。"""
        body = PAGE.split(".fcard{", 1)[1].split("}", 1)[0]
        self.assertIn("grid-column:auto", body,
                      ".fcard 没覆盖 section 的 grid-column:span 12 → 会退化成单列")

    def test_cards_reset_section_shadow(self):
        body = PAGE.split(".fcard{", 1)[1].split("}", 1)[0]
        self.assertIn("box-shadow:none", body)


class TestFullPageTabs(unittest.TestCase):
    """整页级 TAB（用户 2026-11-02 最核心的批评）。

    原话：「账号设置与AI模型这两个标签页是要占整个区域的，
    不是只显示账号与任务列表区域的，你基本的TAB标签页不会设计吗」
    """

    def test_tabs_span_full_width(self):
        """TAB 条横跨整宽，三个视图各占整行。"""
        self.assertIn('<nav class="pagetabs" role="tablist">', PAGE)
        # 视图默认 span 12 → 占满整行，不受账号列表 9 栏的限制
        self.assertIn(".pageview{display:none;grid-column:span 12}", PAGE)
        self.assertIn(".pagetabs{grid-column:span 12", PAGE)

    def test_settings_view_is_not_split(self):
        """账号设置视图不能被 .split 包成半宽（那正是之前的毛病）。"""
        view = PAGE.split('id="view-account"', 1)[1].split("</section>", 1)[0]
        self.assertIn('id="acct-form"', view)
        self.assertNotIn('class="split"', view)

    def test_ai_view_is_not_split(self):
        view = PAGE.split('id="view-ai"', 1)[1].split("</section>", 1)[0]
        self.assertIn('id="ai"', view)
        self.assertNotIn('class="split"', view)

    def test_only_accounts_view_has_split(self):
        """只有「账号与任务」需要左右分栏，另外两个视图整宽。"""
        self.assertEqual(PAGE.count('class="split"'), 1)

    def test_tab_bar_uses_segmented_control_style(self):
        """视觉升级：分段控件（有底色/圆角/选中态阴影），不是细下划线。"""
        self.assertIn(".pagetabs{grid-column:span 12;display:flex", PAGE)
        self.assertIn("background:#eceef2;border-radius:10px", PAGE)
        self.assertIn(".pagetabs button.on{background:var(--card)", PAGE)
        self.assertIn("box-shadow:0 1px 3px rgba(16,24,40,.12)", PAGE)

    def test_tab_switch_uses_views_not_panes(self):
        body = PAGE.split("function showTab(", 1)[1].split("\n}", 1)[0]
        self.assertIn('el("view-" + t)', body)
        self.assertNotIn('el("pane-" + t)', body)


class TestNoDuplicateNewAccountButton(unittest.TestCase):
    """TAB 条右边那个「＋ 新增账号」已移除（用户：「是不是重复了」）。

    它与「账号设置」页签语义重复：点它其实就是切到账号设置页。
    """

    def test_tab_bar_has_no_new_account_button(self):
        nav = PAGE.split('class="pagetabs"', 1)[1].split("</nav>", 1)[0]
        self.assertNotIn("newAccount()", nav)

    def test_new_account_button_lives_in_accounts_view(self):
        """按钮留在账号视图的标题行里，语义清楚（点它 = 去填新账号）。"""
        head = PAGE.split('class="view-head"', 1)[1].split("</div>", 1)[0]
        self.assertIn("newAccount()", head)
        self.assertIn("＋ 新增账号", head)

    def test_only_one_new_account_button(self):
        """全页只应有一个「＋ 新增账号」按钮。"""
        self.assertEqual(PAGE.count('onclick="newAccount()"'), 1)


class TestCancelButtonWorks(unittest.TestCase):
    """「取消」必须真的有效果（用户：「新增账号的取消功能也不能实现」）。

    根因：原来 resetAccountForm() 只把表单重置成新增态、人还停在表单页，
    点完屏幕几乎没变化 → 看起来"点了没反应"。
    现在：清空字段 + **回到账号列表**。
    """

    def test_cancel_returns_to_accounts_list(self):
        body = PAGE.split("function resetAccountForm(", 1)[1].split("\n}", 1)[0]
        self.assertIn('showTab("accounts")', body)

    def test_cancel_clears_fields(self):
        self.assertIn("function clearAccountFields(", PAGE)
        body = PAGE.split("function clearAccountFields(", 1)[1].split("\n}", 1)[0]
        # 关键字段都要清
        for fld in ("f-label", "f-display", "f-port", "f-name", "f-pw"):
            self.assertIn(f'el("{fld}").value = ""', body)

    def test_cancel_re_enables_label_field(self):
        """编辑态代号是 disabled 的，取消后必须恢复可编辑，否则下次新增填不进去。"""
        body = PAGE.split("function clearAccountFields(", 1)[1].split("\n}", 1)[0]
        self.assertIn('el("f-label").disabled = false', body)

    def test_new_account_requests_random_port(self):
        """新增时自动要一个未占用端口，省得用户自己填。"""
        body = PAGE.split("function newAccount(", 1)[1].split("\n}", 1)[0]
        self.assertIn("genPort()", body)

    def test_new_account_does_not_call_reset_which_switches_back(self):
        """newAccount 不能调 resetAccountForm（后者会切回列表，方向相反）。"""
        body = PAGE.split("function newAccount(", 1)[1].split("\n}", 1)[0]
        self.assertNotIn("resetAccountForm()", body)


class TestRunLogBlock(unittest.TestCase):
    """运行日志独立成块（用户 2026-11-02：「黑色的刷课进程记录，
    要不要一起放在操作记录这块区域中单独一块」+「操作记录改为日志记录」）。"""

    def test_ops_section_renamed_to_log(self):
        ops = PAGE.split('<section id="sec-ops">', 1)[1].split("</section>", 1)[0]
        self.assertIn("<h2>日志记录</h2>", PAGE.split('id="sec-ops"', 1)[1][:120])
        # 两个子块：运行日志 + 操作记录
        self.assertIn('id="runlog"', ops)
        self.assertIn('id="ops"', ops)
        self.assertIn("运行日志", ops)
        self.assertIn("操作记录", ops)

    def test_runlog_rendered_by_own_function(self):
        self.assertIn("function renderRunLog(", PAGE)
        # 必须进 load() 的渲染链，否则 4 秒轮询不会刷新
        body = PAGE.split("async function load(", 1)[1].split("\n}", 1)[0]
        self.assertIn("renderRunLog(state)", body)

    def test_tail_no_longer_inside_account_table(self):
        """黑色 pre 不再塞在账号表下面（用户要求单独一块）。"""
        body = PAGE.split("function renderAccounts(", 1)[1].split("\nfunction ", 1)[0]
        self.assertNotIn("mine.tail", body)

    def test_empty_state_when_no_job(self):
        body = PAGE.split("function renderRunLog(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("当前没有运行中的任务", body)

    def test_sec_lists_scroll_inside_body(self):
        """待学/已完成列表太长用内部滚动条，不分页（2026-11-03 用户问题）。

        为什么滚动不是分页：这页 4 秒轮询整块重绘，分页需要维护页码状态，
        轮询会重置页码 → 体验很差；滚动条零状态、桌面/移动都能用。

        ⚠ **滚动容器是 .table-wrap，不是 .body**（2026-11-03 用户实拍表头被盖）：
        thead 的 sticky 只在最近的滚动祖先是表格容器时生效。
        """
        self.assertIn("max-height:min(52vh,420px)", PAGE)
        self.assertIn("overflow:auto", PAGE)
        # 滚动条方案不依赖页码状态；折叠态还原仍由 SEC_OPEN 负责，没被破坏
        self.assertIn('SEC_OPEN[d.dataset.blk]', PAGE)
        # 窄屏也配了（>=440px 横滑，列不压扁）
        self.assertIn("table{min-width:440px}", PAGE)

    def test_table_wrap_is_the_scroll_container(self):
        """.body 不滚、.table-wrap 滚 —— 否则表头 sticky 失效被首行盖住。

        ⚠ 检查**所有**匹配规则（页面里窄屏断点还有一条 .table-wrap 规则），
        不能只看第一条 —— 否则窄屏那条会漏检。
        """
        body_rules = [r.split("}", 1)[0] for r in
                      PAGE.split("details.blk>.body{")[1:]]
        for rule in body_rules:
            self.assertNotIn("overflow", rule,
                             ".body 不能是滚动容器（会压掉表头 sticky）：" + rule)
        wrap_rules = [r.split("}", 1)[0] for r in
                      PAGE.split("details.blk>.body .table-wrap{")[1:]]
        self.assertTrue(any("overflow:auto" in r for r in wrap_rules),
                        ".table-wrap 必须是滚动容器")
        self.assertTrue(any("max-height:min(52vh,420px)" in r for r in wrap_rules),
                        ".table-wrap 要限高，长列表才有滚动条")

    def test_buttons_do_not_wrap(self):
        """「学这节」按钮曾被挤成两行，把行距撑得参差不齐。"""
        self.assertIn("details.blk>.body td button{white-space:nowrap}", PAGE)

    def test_cells_vertically_centered(self):
        """垂直居中 + 统一 padding → 行高整齐。"""
        self.assertIn("details.blk>.body th,details.blk>.body td{vertical-align:middle",
                      PAGE)


class TestBrowserCardPerAccount(unittest.TestCase):
    """浏览器与会话**按账号区分**（用户 2026-11-02 提问后核实并落地）。

    用户原话：「右侧栏上面的浏览器与会话是指针对每一个账号不一样的，还是一样的，
    如果不一样，是不是也要跟随账号显示呢？」

    核实结论：**确实按账号不同** —— 端口、profile 目录、登录态快照、会话检查结果
    全部按 label 取（见 `app.py::_browser_state`）。所以它必须跟随账号显示。
    """

    def test_backend_returns_every_accounts_browser_state(self):
        """后端一次算全所有账号的浏览器状态，悬停时前端可直接切、无需再发请求。"""
        import inspect

        from xxt.panel_gxks import app as panel_app
        src = inspect.getsource(panel_app.create_app)
        self.assertIn('"browsers": browsers', src)
        self.assertIn('browsers = {r["label"]: _browser_state(', src)

    def test_browser_state_is_keyed_by_label(self):
        """`_browser_state` 的端口/profile/快照都按 label 取，不是全局一份。"""
        import inspect

        from xxt.panel_gxks import app as panel_app
        src = inspect.getsource(panel_app._browser_state)
        self.assertIn("acc.port if acc else 0", src)
        self.assertIn("state_path(settings, label)", src)
        self.assertIn('f"session_{label}.json"', src)

    def test_credentials_are_per_account(self):
        """凭据必须按账号判，不能读全局 account.local.json（那是单账号遗留）。

        这是 2026-11-02 查出的真 bug：全局文件会让所有账号显示同一个凭据状态，
        与表格里按账号算的 credentials 自相矛盾。
        """
        import inspect

        from xxt.panel_gxks import app as panel_app
        src = inspect.getsource(panel_app._browser_state)
        self.assertIn("acc.configured", src)
        self.assertNotIn("glogin.is_configured", src)

    def test_hover_switches_browser_too(self):
        """悬停某行时，浏览器区也要跟着切到那个账号。"""
        body = PAGE.split("function hoverAcctSide(", 1)[1].split("\n}", 1)[0]
        self.assertIn("renderBrowserFor", body)

    def test_browser_renders_for_given_label(self):
        """renderBrowser 接受 (state, browser, label)，按传入账号渲染。"""
        self.assertIn("function renderBrowserFor(state, label)", PAGE)
        self.assertIn("function renderBrowser(state, browser, label)", PAGE)
        # 标题要写清是谁的浏览器，否则切账号后数字变了但不知道看的是谁
        self.assertIn('"（端口 " + (b.port || "?") + "）"', PAGE)

    def test_polling_keeps_hover_preview(self):
        """4 秒轮询不能把右列弹回选中账号（鼠标还停在行上却跳走）。"""
        body = PAGE.split("async function load(", 1)[1].split("\n}", 1)[0]
        self.assertIn("HOVER_LABEL", body)
        self.assertIn("renderBrowserFor(state, HOVER_LABEL)", body)


class TestBrowserButtonsLayout(unittest.TestCase):
    """浏览器卡按钮重排（用户 2026-11-02：「这几个按钮要重新排列一下」）。

    原来 5 个按钮平铺一行、宽度随文字长短不一、主次不分。
    现在分两组：浏览器（启动/停止）+ 会话（检查/恢复/重登），组内等宽。
    """

    def test_buttons_are_grouped(self):
        self.assertIn('class="bgrp"', PAGE)
        self.assertIn('class="bgrp-lab">浏览器', PAGE)
        self.assertIn('class="bgrp-lab">会话', PAGE)
        self.assertEqual(PAGE.count('class="bgrp-btn'), 5, "五个按钮都要用组内样式")

    def test_group_css_makes_them_equal_width(self):
        self.assertIn(".bgrp{display:flex;align-items:center;gap:6px", PAGE)
        self.assertIn(".bgrp-btn{flex:1 1 0", PAGE)

    def test_lifecycle_buttons_keep_semantics(self):
        """启动是主操作、停止是危险操作，语义不能因为重排丢掉。"""
        self.assertIn('id="btn-bstart" class="bgrp-btn primary"', PAGE)
        self.assertIn('id="btn-bstop" class="bgrp-btn danger"', PAGE)

    def test_all_five_buttons_still_exist(self):
        for bid in ("btn-bstart", "btn-bstop", "btn-scheck",
                    "btn-srestore", "btn-rlogin"):
            self.assertIn(f'id="{bid}"', PAGE, f"按钮 {bid} 丢了")


class TestTypography(unittest.TestCase):
    """字号梯度与重点信息突出（用户 2026-10-30：“字体调大、重点突出”）。"""
    def test_base_and_heading_font_sizes(self):
        self.assertIn("font:15px/1.6", PAGE)          # 基础字号从 14 提到 15
        self.assertIn("h1{font-size:18px", PAGE)
        self.assertIn("h2{font-size:15px", PAGE)

    def test_table_font_larger_than_before(self):
        self.assertIn("table{width:100%;border-collapse:collapse;font-size:14px}", PAGE)

    def test_key_numbers_are_emphasized(self):
        self.assertIn("table.acct-tbl td.c-today b{font-size:15px", PAGE)  # 今日节数
        self.assertIn(".bar .lab b{", PAGE)                # 进度条上的完成数
        self.assertIn("'</span><span><b>'", PAGE)

    def test_table_columns_are_aligned(self):
        # 表格的价值就在列对齐：今日数字用等宽数字、操作列右对齐不折行
        self.assertIn("table.acct-tbl td.c-today b{font-size:15px", PAGE)
        self.assertIn("table.acct-tbl td.c-ops{white-space:nowrap", PAGE)


class TestLayoutBalance(unittest.TestCase):
    """账号区的空间占用（用户 2026-10-30 两次追问）。

    第一轮问题：卡片视图下账号多了左边一直变长、右边浏览器区不动 → 实测 6 个账号差 1116px。
    第二轮方案（用户提议）：**账号区改成表格**——表格行矮且能滚动，问题从根上消失，
    所以原来那套"两列等高"的补丁不再需要（`align-items:stretch` 与 6+6 已撤掉）。
    """

    def test_accounts_area_capped_so_page_does_not_grow_forever(self):
        # 账号特别多时在栏内滚动，而不是把整页拉到 2000px+
        self.assertIn("height:82vh", PAGE)
        self.assertIn(".split-main>#accounts{overflow:auto", PAGE)
        self.assertIn("min-height:500px", PAGE)

    def test_accounts_table_gets_nine_columns(self):
        # 表格 9 列，挤到 6 栏会折行 → 9fr + 3fr 并排
        self.assertNotIn("grid-column:span 6", PAGE)
        self.assertIn("grid-template-columns:minmax(0,8fr) minmax(0,4fr)", PAGE)
        self.assertIn("height:82vh", PAGE)

    def test_browser_card_is_compact(self):
        """浏览器信息用「路径整行 + 其余单行」的紧凑排法（2026-11-02 重排）。

        账号密码已移到「浏览器」那一行当标签，所以 .binfo 栅格被移除了。
        """
        self.assertIn(".bi-line{display:flex", PAGE)
        self.assertIn('class="bi-line"', PAGE)
        self.assertIn('class="bi-line path"', PAGE)   # 路径整行、可换行
        self.assertIn('id="b-cred"', PAGE)            # 账号密码标签占位


class TestAccountAdminUI(unittest.TestCase):
    """面板里的账号增删改（用户 2026-10-30：“多账号管理是怎么规划的，能不能先落地看效果”）。

    2026-10-31 改造：新增/编辑从**弹窗**改成卡片内嵌表单，并与「AI 模型」并成两个 TAB。
    """

    def test_new_account_button_and_form_exist(self):
        self.assertIn('onclick="newAccount()"', PAGE)
        self.assertIn('<form id="acct-form"', PAGE)          # 内嵌表单，不再是弹窗
        self.assertNotIn('<dialog id="dlg">', PAGE)          # 旧账号弹窗已移除
        self.assertIn("function openAccount(", PAGE)
        self.assertIn("function newAccount(", PAGE)
        self.assertIn("function saveAccount(", PAGE)

    def test_row_has_edit_toggle_delete_buttons(self):
        for fn in ("openAccount(", "toggleAccount(", "deleteAccount("):
            self.assertIn(fn, PAGE, f"账号行缺 {fn} 入口")
        # 表格变窄（右侧让给浏览器卡），按钮压成图标 + title
        self.assertIn('title="编辑：端口 / 日额度 / 学校 / 凭据">✎</button>', PAGE)
        self.assertIn('"停用（不参与调度）"', PAGE)
        self.assertIn(">✕</button>", PAGE)                   # 删除
        self.assertIn(">↻</button>", PAGE)                   # 刷新节列表

    def test_password_is_never_prefilled(self):
        # 面板拿不到密码（后端只回布尔），回填等于自欺；这里钉死“永远清空”
        self.assertIn('el("f-pw").value = "";', PAGE)
        self.assertIn('el("f-name").value = "";', PAGE)

    def test_delete_asks_twice(self):
        self.assertIn('confirm("删除账号「"', PAGE)      # 前端先问一次
        self.assertIn("confirm: true", PAGE)             # 后端还要 confirm 标记

    def test_label_is_readonly_when_editing(self):
        # 改代号会让登录态快照 / profile 目录对不上，先不让改
        self.assertIn('el("f-label").disabled = !!label;', PAGE)

    def test_blank_number_input_is_rejected(self):
        # 空输入框 `Number("")` 是 0，会被当成“不限”悄悄存进去 —— 必须当成非法
        self.assertIn("不能为空（要“不限”请显式填 0）", PAGE)
        self.assertIn('el(id).value.trim() === ""', PAGE)

    def test_unlimited_quota_renders_as_text_not_zero(self):
        # 日节数 0 = 不限：显示成“0 节”会让人以为额度用完了
        self.assertIn("left_sections === null", PAGE)
        self.assertIn('"不限"', PAGE)

    def test_actions_use_actions_cn_dictionary(self):
        for action in ("account-add", "account-edit", "account-toggle", "account-del"):
            self.assertIn(f'"{action}"', PAGE, f"操作记录里 {action} 没有中文名")

    def test_card_has_sections_refresh(self):
        # 台账 B8：以前只有节进度区块头有「刷新节列表」，多账号时要先切账号再点
        self.assertIn("function refreshSectionsFor(", PAGE)
        self.assertIn("refreshSectionsFor(", PAGE)
        self.assertIn("刷新节列表</button>", PAGE)

    def test_backend_400_is_not_shown_as_success(self):
        # FastAPI 的 400 回 `{"detail": ...}`，没有 `ok` 字段；不处理会把失败当成功
        self.assertIn("async function apiPost(", PAGE)
        self.assertIn('data.detail || data.error || ("HTTP " + r.status)', PAGE)
        self.assertIn("await apiPost(", PAGE)

    def test_multi_account_totals_in_batch_bar(self):
        """统计与批量操作**同一行**（用户 2026-11-02：
        「合计…运行中…空闲… 要放在这一行：已选 N 个 / 开始学习 / …」）。

        原来统计是表格上方独立一行（head 变量），已并入 #acct-batch。
        """
        self.assertIn("合计 ' +", PAGE)
        # 统计必须在 renderBatchBar 里，而不是 renderAccounts 的 head
        bar = PAGE.split("function renderBatchBar(", 1)[1].split("\n}", 1)[0]
        self.assertIn("合计", bar)
        self.assertIn("运行中", bar)
        self.assertIn("空闲", bar)
        self.assertIn("bb-sep", bar)          # 统计与按钮之间的竖分隔
        # 旧实现里的独立 head 行必须已经删掉
        self.assertNotIn('let head = "";', PAGE)


class TestPageShell(unittest.TestCase):
    def test_grid_layout_targets_exist(self):
        self.assertIn("grid-template-columns:repeat(12", PAGE)
        for sec in SECTION_IDS:
            self.assertIn(f'id="{sec}"', PAGE, f"网格样式指向的 #{sec} 在页面上不存在")

    def test_no_external_assets(self):
        # 单文件 exe：页面必须自包含，离线也不能少东西
        for bad in ('src="http', 'href="http', "@import", "<link "):
            self.assertNotIn(bad, PAGE)


if __name__ == "__main__":
    unittest.main()
