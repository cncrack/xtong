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


class TestAccountCards(unittest.TestCase):
    """多账号总览卡片（点卡片 = 把下面四个区块切到该账号）。"""

    def test_card_grid_and_current_highlight_styles(self):
        self.assertIn(".cards{display:grid", PAGE)
        self.assertIn(".acct.cur{", PAGE)          # 当前账号要能一眼看出来

    def test_card_renders_into_accounts_slot(self):
        self.assertIn("function acctCard(", PAGE)
        self.assertIn('el("accounts").innerHTML', PAGE)
        self.assertIn("state.cards || []", PAGE)

    def test_card_actions_do_not_also_switch_account(self):
        # 卡片整体 onclick 切账号；卡内按钮若冒泡就会“点停止→顺手切账号”
        self.assertIn('class="acct-actions" onclick="event.stopPropagation()"', PAGE)

    def test_switching_account_forces_sections_rebuild(self):
        # 两个账号的数据签名可能撞车，靠 state.label 参与签名不可能完全区分
        # （read_at/行数/完成数都一样时）→ 切账号时直接清签名强制重建
        self.assertIn("SEC_SIG = \"\";", PAGE)
        self.assertIn("function pick(", PAGE)

    def test_card_buttons_present(self):
        for fn in ("function startStudy(", "function stopJob("):
            self.assertIn(fn, PAGE)


class TestTypography(unittest.TestCase):
    """字号梯度与重点信息突出（用户 2026-10-30：“字体调大、重点突出”）。"""

    def test_base_and_heading_font_sizes(self):
        self.assertIn("font:15px/1.6", PAGE)          # 基础字号从 14 提到 15
        self.assertIn("h1{font-size:18px", PAGE)
        self.assertIn("h2{font-size:15px", PAGE)

    def test_table_font_larger_than_before(self):
        self.assertIn("table{width:100%;border-collapse:collapse;font-size:14px}", PAGE)

    def test_key_numbers_are_emphasized(self):
        self.assertIn(".kv .v b{font-size:16px", PAGE)      # 卡片里的“今日 x 节”
        self.assertIn(".bar .lab b{", PAGE)                # 进度条上的完成数
        self.assertIn("'</span><span><b>'", PAGE)

    def test_account_cards_fill_the_row_when_few(self):
        # auto-fill 会在账号少时留下空轨道，卡片只占左边一小块（实测 1 个账号时只占 1/3 宽）
        self.assertIn("repeat(auto-fit,minmax(300px,1fr))", PAGE)
        self.assertNotIn("repeat(auto-fill,minmax(320px,1fr))", PAGE)

    def test_kv_key_column_is_fixed_so_cards_align(self):
        self.assertIn("grid-template-columns:64px 1fr", PAGE)


class TestAccountAdminUI(unittest.TestCase):
    """面板里的账号增删改（用户 2026-10-30：“多账号管理是怎么规划的，能不能先落地看效果”）。"""

    def test_new_account_button_and_dialog_exist(self):
        self.assertIn('onclick="openAccount()"', PAGE)
        self.assertIn('<dialog id="dlg">', PAGE)
        self.assertIn("function openAccount(", PAGE)
        self.assertIn("function saveAccount(", PAGE)

    def test_card_has_edit_toggle_delete_buttons(self):
        for fn in ("openAccount(", "toggleAccount(", "deleteAccount("):
            self.assertIn(fn, PAGE, f"卡片上缺 {fn} 入口")
        self.assertIn("✎ 编辑", PAGE)
        self.assertIn("✎ 编辑</button>", PAGE)

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
