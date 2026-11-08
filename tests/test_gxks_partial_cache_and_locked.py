"""两个账号「待学区不一样」的两个真 bug（2026-11-08 用户第三次截图反馈）。

## 用户看到的现象

| 甲同学（default） | 乙同学（test） |
|---|---|
| 33/36 节，待学 3 节全「未学习 / 未解锁」 | **7/8 节**，待学 1 节「学习中 / **未解锁**」 |

## 查证结论（代码 + 缓存 + 日志）

**大部分差异是真实的**：两个账号是不同学生，同一门课（value=32）进度不同 ——
default 第 2.1 节 `done 62/60`，test 第 2.1 节才 `learning 0/60`。这是对的。

但混着**两个真 bug**：

### Bug A：test 的「8 节」不是课程总节数

`gxks-study-test-*.log` 里从没有 `sections` 任务记录 —— test **从没跑过「刷新节列表」**。
它的缓存 `sections_test_32.json` 只由 `run_gxks.py` 的**逐节回写**产生
（`find_section` 只找它经手的那一节），所以行数 = 它学过的节数，不是课程全量
（同一门课在 default 那份是 36 行）。
→ 面板却把它当「7/8 节」显示，用户会以为这门课只有 8 节、快学完了。

**修**：后端加 `sections_partial`（判据 = 缓存**没有 `pager` 字段** ——
完整读列表的 `gxks_sections.py --all` 一定会写 pager，逐节回写不会），
前端在标签行显示「⚠ 缓存不全，请刷新」。

### Bug B：同一行「学习中 + 未解锁」自相矛盾

`practiceCell()` 只看 `practice_ready`，learning 行也是 false → 显示「未解锁」，
与旁边「学习中」并排像 bug。
**修**：learning 行改显示「学完解锁」+ title 说明（语义准确，不与「学习中」冲突）。
"""

from __future__ import annotations

import unittest
from pathlib import Path

from xxt.panel_gxks import app
from xxt.panel_gxks.page import PAGE

APP_SRC = Path(app.__file__).read_text(encoding="utf-8")


class TestPartialCacheFlag(unittest.TestCase):
    def test_backend_computes_partial_by_missing_pager(self):
        """判据必须用 pager 是否存在，不能用行数猜。"""
        self.assertIn('sections_partial = bool(cached_rows) and not cached.get("pager")',
                      APP_SRC)

    def test_backend_exposes_flag_in_cards(self):
        self.assertIn('"sections_partial": sections_partial', APP_SRC)

    def test_frontend_warns_when_partial(self):
        self.assertIn("card.sections_partial", PAGE)
        self.assertIn("缓存不全，请刷新", PAGE)
        self.assertIn("刷新节列表", PAGE)

    def test_full_cache_has_no_warning(self):
        """完整缓存（有 pager）不该出现提示 —— 条件必须是 card.sections_partial。"""
        self.assertIn("(card.sections_partial", PAGE)
        self.assertNotIn("(data.pager ? 0 : 1)", PAGE)


class TestLearningPracticeCell(unittest.TestCase):
    def test_learning_row_not_labelled_unlocked(self):
        """learning 行不能显示「未解锁」（会与「学习中」矛盾）。"""
        seg = PAGE.split("function practiceCell(row)", 1)[1]
        seg = seg.split("// ---------------------------------------------------------------- 账号表格", 1)[0]
        self.assertIn('row.state === "learning"', seg)
        self.assertIn("学完解锁", seg)
        # 「未解锁」只能出现在最后那条兜底 return 里（注释里出现不算）
        code_lines = [ln for ln in seg.split("\n") if not ln.strip().startswith("//")]
        self.assertEqual("\n".join(code_lines).count("未解锁"), 1)

    def test_ready_still_wins(self):
        """已解锁的节（practice_ready）不受影响，仍显示「可做 #id」。"""
        seg = PAGE.split("function practiceCell(row)", 1)[1]
        seg = seg.split("// ---------------------------------------------------------------- 账号表格", 1)[0]
        self.assertIn("if (row.practice_ready)", seg)
        self.assertIn('class="tag t-bank">可做', seg)

    def test_state_tag_unchanged(self):
        """状态标签本身不动：learning 仍是「学习中」。"""
        seg = PAGE.split("function stateTag(row)", 1)[1][:400]
        self.assertIn('row.state === "learning"', seg)
        self.assertIn("学习中", seg)


class TestRealDifferenceIsPreserved(unittest.TestCase):
    """真实进度差异必须照实显示 —— 不能为了「看起来一样」去改数据。"""

    def test_detail_view_still_single_account(self):
        self.assertIn("const data = state.sections;", PAGE)
        self.assertIn("function renderSections(state)", PAGE)

    def test_no_hardcoded_section_count(self):
        """不许把节数写死成某个数字来「对齐」两个账号。"""
        seg = PAGE.split("function renderSections(state)", 1)[1][:6000]
        self.assertNotIn("rows.length = 36", seg)
        self.assertIn("rows.length", seg)


if __name__ == "__main__":
    unittest.main()
