"""G-8 面板侧：待确认清单 + 顶部红点（2026-11-11 用户要求）。

用户原话：
> 「不建议 A 一直停着，也要提示信息，要明确告诉是哪个章节哪里出问题了」
> 「不管是否是人工还是无人，可以考虑跳过继续下一题」
> 「二、新建 gx_pending 表，的作用是什么…或者对以上的功能留有更好的扩展」

本文件是源码级守护（面板 JS 无 DOM 运行环境），配合 `node --check`。

设计要求：
1. 待确认清单放进**现有的「本次作答」区**（用户明确「不是新建区域」）
2. 每行必须能回答**哪个章节、第几题、什么原因**
3. **不阻塞**任务（只展示，不给「挂起」动作）
4. 多账号：`state.pending` 按当前账号过滤，`pending_counts.__total__` 给红点
5. 375px 下要能看（AGENTS 硬性约定）
"""

from __future__ import annotations

import unittest
from pathlib import Path

from xxt.panel_gxks import app
from xxt.panel_gxks.page import PAGE

APP_SRC = Path(app.__file__).read_text(encoding="utf-8")


class TestBackendExposesPending(unittest.TestCase):
    def test_state_returns_pending_and_counts(self):
        self.assertIn('"pending": pending_rows', APP_SRC)
        self.assertIn('"pending_counts": pending_counts', APP_SRC)

    def test_queries_before_closing_db(self):
        """回归：曾在 return 字典里查库 → 连接已关 → ProgrammingError。"""
        anchor = "ops = gxks_ops.recent_ops(db, account_label=label or None"
        seg = APP_SRC.split(anchor, 1)[1][:800]
        self.assertIn("pending_counts = gxks_pending.pending_counts(db)", seg)
        # 查询必须在 finally: db.close() 之前
        self.assertLess(seg.index("pending_counts = gxks_pending"),
                        seg.index("db.close()"))

    def test_pending_imported(self):
        self.assertIn("from ..store import gxks_pending", APP_SRC)


class TestFrontendPendingBlock(unittest.TestCase):
    def test_block_rendered_inside_work_area(self):
        """用户明确「放在本次作答区」，不新建区域。"""
        self.assertIn("function renderPendingBlock(state)", PAGE)
        seg = PAGE.split("function renderWork(state)", 1)[1].split("const ACTION_CN", 1)[0]
        self.assertIn("renderPendingBlock(state)", seg)
        self.assertIn("box.innerHTML = html;", seg)

    def test_shows_section_and_question_number(self):
        """必须能回答「哪个章节、第几题」。"""
        seg = PAGE.split("function renderPendingBlock(state)", 1)[1].split("const ACTION_CN", 1)[0]
        self.assertIn("r.section_name", seg)
        self.assertIn("r.question_ordinal", seg)

    def test_reason_is_translated(self):
        """原因要中文，不能直接甩机器码。"""
        seg = PAGE.split("function renderPendingBlock(state)", 1)[1].split("const ACTION_CN", 1)[0]
        for k in ("ai_used", "no_site_answer", "low_confidence", "subjective"):
            self.assertIn(k, seg)

    def test_total_from_counts_for_badge(self):
        self.assertIn("counts.__total__", PAGE)

    def test_not_blocking(self):
        """不应出现「挂起该节」「暂停任务」这类阻塞动作。"""
        seg = PAGE.split("function renderPendingBlock(state)", 1)[1].split("const ACTION_CN", 1)[0]
        self.assertNotIn("stopJob", seg)
        self.assertNotIn("pause", seg)

    def test_mobile_breakpoint(self):
        self.assertIn(".pending-box{", PAGE)
        self.assertIn(".pending-box .stem{max-width:160px}", PAGE)

    def test_action_words_registered(self):
        for a in ("work-review", "work-verify", "work-skip"):
            self.assertIn(a, PAGE)


if __name__ == "__main__":
    unittest.main()
