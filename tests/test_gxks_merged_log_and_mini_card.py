"""T5 日志合并流 + G-11② 行内迷你卡（2026-11-08 用户确认/拍板）。

T5（devlog 2026-11-08-01 §三）：默认「全部账号合并流」，每行前缀
`[账号·课程]`，下拉可过滤；页签只作 ≤5 账号可选视图。N 大时页签必然失效，
合并流是唯一不随账号数退化的形态。
实现约束：**保留 LOG_LINES 分桶不动**，合并流只是新渲染器 → 单账号视图零成本回退。

G-11②：账号表行内显示「正在学第几节 x/y」+ 迷你条，只在 job 在跑时显示；
详情区仍单账号。数据来源 = 节缓存里 `state=="learning"` 的行（tick 每 30 秒打的
标记，零平台请求、零新接口）。

本文件是**源码级守护**（面板 JS 无 DOM 运行环境），配合 `node --check` 语法检查。
"""

from __future__ import annotations

import unittest
from pathlib import Path

from xxt.panel_gxks import app
from xxt.panel_gxks.page import PAGE

APP_SRC = Path(app.__file__).read_text(encoding="utf-8")


class TestMergedLogFlow(unittest.TestCase):
    def test_markup_has_mode_and_filter_selects(self):
        self.assertIn('id="log-mode"', PAGE)
        self.assertIn('id="log-filter"', PAGE)
        self.assertIn("全部账号（合并流）", PAGE)
        self.assertIn("单账号", PAGE)

    def test_default_mode_is_merged(self):
        self.assertIn('let LOG_MODE = "merged"', PAGE)

    def test_merged_rows_have_account_course_prefix(self):
        self.assertIn("function mergedLogRows()", PAGE)
        self.assertIn('"[" + logWho(r.label)', PAGE)
        self.assertIn('(course ? "·" + course : "")', PAGE)

    def test_append_feeds_both_views(self):
        """SSE 增量必须同时喂分桶（单账号回退用）和合并流。"""
        self.assertIn("LOG_MERGED.push({ seq: ++LOG_SEQ", PAGE)
        self.assertIn('if (LOG_MODE === "merged") { renderLogBox(); return; }', PAGE)

    def test_filter_uses_full_account_list(self):
        self.assertIn("function renderLogFilter()", PAGE)
        self.assertIn("(STATE && STATE.cards) || []", PAGE)

    def test_runlog_polling_does_not_overwrite_merged(self):
        """4 秒轮询的 renderRunLog 兜底不能整块覆盖合并流。"""
        self.assertIn('if (LOG_MODE === "merged") {', PAGE)
        # 合并模式分支里必须 return，不能继续走单账号 innerHTML 重建
        seg = PAGE.split("function renderRunLog(state)", 1)[1][:1400]
        self.assertIn("renderLogBox();", seg)
        self.assertIn("return;", seg)

    def test_merged_renderer_keeps_scroll_anchor(self):
        """11-07 修的「翻历史不被挤走」在合并流下同样成立（按全局行号）。"""
        self.assertIn("function renderMergedLogBox()", PAGE)
        self.assertIn("const added = Math.max(0, rows.length - prevCount)", PAGE)
        self.assertIn("(anchorLine + added) * lh", PAGE)

    def test_single_view_still_uses_log_lines_buckets(self):
        """回退路径必须完好：LOG_LINES 分桶 + pickLogJob 都没被删。"""
        self.assertIn("const LOG_LINES = new Map()", PAGE)
        self.assertIn("function pickLogJob(", PAGE)
        self.assertIn("function setLogMode(v)", PAGE)

    def test_mobile_breakpoint(self):
        """375px 约定：工具条窄屏换行，不靠横向滚动。"""
        self.assertIn(".log-tools", PAGE)
        self.assertIn("@media (max-width:760px)", PAGE)


class TestMiniCard(unittest.TestCase):
    def test_backend_exposes_running_section(self):
        self.assertIn('"running_section": running_section', APP_SRC)
        self.assertIn('r.get("state") == "learning"', APP_SRC)

    def test_running_section_fields(self):
        self.assertIn('"name": str(running.get("name") or "")', APP_SRC)
        self.assertIn('"done_minutes": int(running.get("done_minutes") or 0)', APP_SRC)
        self.assertIn('"required_minutes": int(running.get("required_minutes") or 0)', APP_SRC)

    def test_frontend_only_shows_when_job_alive(self):
        self.assertIn("const rs = c.running_section;", PAGE)
        self.assertIn("(c.job && rs && rs.name)", PAGE)
        self.assertIn('class="run-mini"', PAGE)

    def test_mini_card_shows_section_progress(self):
        self.assertIn("esc(rs.name)", PAGE)
        self.assertIn("miniBar(Number(rs.done_minutes)", PAGE)

    def test_mini_card_mobile_breakpoint(self):
        self.assertIn(".run-mini{", PAGE)
        # 断点块内要有字号放大
        self.assertIn(".run-mini{font-size:12px;max-width:150px}", PAGE)

    def test_detail_view_unchanged(self):
        """详情区仍是单账号 renderSections（设计原则不破）。"""
        self.assertIn("function renderSections(state)", PAGE)
        self.assertIn("const data = state.sections;", PAGE)


if __name__ == "__main__":
    unittest.main()
