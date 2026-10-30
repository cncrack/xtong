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

SECTION_IDS = ("sec-accounts", "sec-browser", "sec-sections", "sec-work", "sec-ops")


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
        for fn in ("function startStudy(", "function stopJob(", "function refreshSectionsFor("):
            self.assertIn(fn, PAGE)

    # ---------------------------------------------------------------- 多选与批量

    def test_multi_select_survives_poll_rebuild(self):
        """选中集合必须记在 JS 里：表格每 4 秒整块重绘，靠 DOM 会丢。"""
        self.assertIn("const SEL = new Set()", PAGE)
        self.assertIn('closest(".acct-pick")', PAGE)
        self.assertIn('id="acct-all"', PAGE)          # 表头全选

    def test_batch_actions_cover_all_five(self):
        """用户要求批量：开始/停止/启用/停用/删除。"""
        for act in ("start", "stop", "enable", "disable", "delete"):
            self.assertIn("batchAction(\\'" + act + "\\')", PAGE)
        self.assertIn("/api/accounts/batch", PAGE)

    def test_batch_delete_asks_twice(self):
        # 批量删除不可逆：前端先 confirm，后端再要 confirm 标记
        self.assertIn("删除选中的 ", PAGE)
        self.assertIn("confirm: true", PAGE)

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

    # ---------------------------------------------------------------- 悬停详情

    def test_hover_detail_panel(self):
        self.assertIn('id="acct-detail"', PAGE)
        self.assertIn("function showAcctDetail(", PAGE)
        self.assertIn("function hideAcctDetail(", PAGE)
        # 浮层不拦鼠标 → 移开一定触发 mouseout，不会卡住
        self.assertIn("pointer-events:none", PAGE)

    def test_hover_uses_event_delegation(self):
        """表格每 4 秒重绘，事件必须委托在容器上，直接绑行会随重绘丢失。"""
        self.assertIn('el("accounts").addEventListener("mouseover"', PAGE)
        self.assertIn('el("accounts").addEventListener("mouseout"', PAGE)


class TestAiPanelUI(unittest.TestCase):
    """AI 模型配置界面（第二批，为 B5 回填做准备）。

    用户要求：可自定义模型厂家 + 可添加到厂家列表 + 可修改 + 每项要有说明。
    2026-10-31：与「账号设置」并成一张卡的两个 TAB（`#pane-ai`），布局重排成左右两栏。
    """

    def test_lives_in_tab_pane(self):
        self.assertIn('<div class="tabpane" id="pane-ai"', PAGE)
        self.assertIn('onclick="showTab(\'ai\')"', PAGE)
        self.assertIn("function showTab(", PAGE)
        # 旧的独立区块已被 TAB 取代
        self.assertNotIn('<section id="sec-ai">', PAGE)

    def test_entry_points(self):
        self.assertIn("function renderAi(", PAGE)
        self.assertIn("function applyProvider(", PAGE)
        self.assertIn("function delProvider(", PAGE)
        self.assertIn("function saveKey(", PAGE)
        self.assertIn("function openProvider(", PAGE)
        self.assertIn("＋ 新增厂商", PAGE)

    def test_layout_is_two_columns(self):
        """左栏厂商+参数、右栏 Key 卡片（用户：AI 模型和选项也要重新规划排列）。"""
        self.assertIn('class="ai-wrap"', PAGE)
        self.assertIn('class="ai-col"', PAGE)
        self.assertIn("ai-col-key", PAGE)
        self.assertIn(".ai-wrap{display:grid;grid-template-columns:1fr 300px", PAGE)

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
        self.assertIn("不入库", PAGE)
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
    def test_settings_card_holds_two_tabs(self):
        self.assertIn('<section id="sec-settings">', PAGE)
        self.assertIn('id="tab-btn-account"', PAGE)
        self.assertIn('id="tab-btn-ai"', PAGE)
        self.assertIn('id="pane-account"', PAGE)
        self.assertIn('id="pane-ai"', PAGE)
        # 切换靠 class，不靠重绘（否则 4 秒轮询会把标签页弹回第一个）
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
        """列宽显式给定，账号列吃余量；操作列压成 1% 自适应。"""
        self.assertIn('<th style="min-width:110px">账号</th>', PAGE)
        self.assertIn('<th style="width:1%">操作</th>', PAGE)
        for w in ("width:76px", "width:82px", "width:130px", "width:104px"):
            self.assertIn(w, PAGE)

    # ③ 右侧浏览器与会话
    def test_browser_card_sits_beside_table(self):
        self.assertIn("#sec-accounts{grid-column:span 8}", PAGE)
        self.assertIn("#sec-browser{grid-column:span 4", PAGE)
        self.assertIn("position:sticky", PAGE)      # 跟随滚动，切账号时一直看得见

    # ④ 搜索高亮
    def test_search_hits_are_highlighted(self):
        self.assertIn("function hl(", PAGE)
        self.assertIn('<mark class=\\"hit\\">', PAGE)
        self.assertIn("mark.hit{background:#fff3bf", PAGE)
        # 名称、副标题（学校/专业）、端口都要用上高亮
        self.assertIn("hl(nameTxt, q)", PAGE)
        self.assertIn("hl(sub, q)", PAGE)
        self.assertIn("hl(c.port, q)", PAGE)

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
        # 账号特别多时在卡内滚动，而不是把整页拉到 2000px+
        self.assertIn("max-height:calc(100vh - 120px)", PAGE)
        self.assertIn("#sec-accounts>#accounts{overflow:auto", PAGE)

    def test_accounts_table_takes_full_width(self):
        # 表格 9 列约 940px，挤到 6 栏（600px）会折行 → 整行独占
        self.assertNotIn("#sec-accounts{grid-column:span 6}", PAGE)
        self.assertIn("#sec-accounts{max-height:calc(100vh - 120px)", PAGE)

    def test_browser_card_is_compact(self):
        # 原来 6 行竖表把浏览器卡撑到 396px；改成短项并排 + 长项单行截断
        self.assertIn(".binfo{display:grid", PAGE)
        self.assertIn(".bi-line{display:flex", PAGE)
        self.assertIn('class="binfo"', PAGE)
        self.assertIn('class="bi-line"', PAGE)


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

    def test_multi_account_totals_row(self):
        self.assertIn("合计 ' + tot.sections", PAGE)
        self.assertIn("cards.length > 1", PAGE)
        self.assertIn("function renderAccounts(", PAGE)


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
