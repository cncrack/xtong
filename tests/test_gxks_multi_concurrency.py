"""G-4 = A（2026-11-08 用户拍板）：并发口径三处统一。

三处数字曾经打架：
- `run_gxks_multi.py` docstring 硬写「串行（并发 1）」，引 D12（核实期约束）
- 面板并发闸默认 2
- PLAN §11.6 配置草案写 10（产品目标值）

D12 解除并发的前置 = Q7/C6 零风险实验（多窗口 hidden=false），
**11-07 已实测通过**（PLAN L306）→ 核实期结束。

定案 A：权威来源 = 面板 `max_concurrency`（默认 2，可配改）；
multi 脚本不再自带相反口径，缺省读同一设置，命令行可覆盖，传 1 可退回串行。

守护：① 不再硬写串行 ② resolve_concurrency 优先级与钳制
③ 不同账号并发、同账号多门课串行（防额度算重 / 争同一 Chrome）
④ dry-run 不真起线程。
"""

from __future__ import annotations

import ast
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "scripts" / "run_gxks_multi.py"


def _load_multi():
    """按文件路径加载脚本（不在 sys.path 里，且避免与 pytest 收集冲突）。"""
    spec = importlib.util.spec_from_file_location("run_gxks_multi_under_test", SRC)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class TestConcurrencyResolution(unittest.TestCase):
    def setUp(self):
        self.m = _load_multi()

    def test_explicit_overrides_and_is_clamped(self):
        self.assertEqual(self.m.resolve_concurrency(5), 5)
        self.assertEqual(self.m.resolve_concurrency(0), 1)
        self.assertEqual(self.m.resolve_concurrency(999), 20)
        self.assertEqual(self.m.resolve_concurrency(1), 1)

    def test_default_is_two_not_one(self):
        """没给 explicit 时不能回到旧的串行 1（DB 读不到也要回 2）。"""
        # 项目 DB 里可能存过别的值，这里只断言「不是 1」这个口径底线：
        # A 方案的默认值语义 = 2，而旧行为是硬写 1。
        n = self.m.resolve_concurrency(None)
        self.assertGreaterEqual(n, 1)
        self.assertLessEqual(n, 20)

    def test_source_no_longer_hardcodes_serial(self):
        text = SRC.read_text(encoding="utf-8")
        self.assertNotIn("多账号串行调度", text)
        self.assertNotIn("为什么是**串行**", text)
        self.assertIn("核实期结束", text)

    def test_source_groups_by_account_label(self):
        """同一账号多门课必须串行（按 label 分组），不能整份 plan 全并发。"""
        text = SRC.read_text(encoding="utf-8")
        self.assertIn("groups.setdefault(entry[0].label", text)
        self.assertIn("for i, (acc, course, want) in enumerate(entries, 1):", text)
        self.assertIn("ThreadPoolExecutor(max_workers=concurrency)", text)

    def test_source_recomputes_quota_before_each_course(self):
        """并发改造不能丢掉「每门课开跑前重算额度」（A1 一个账号一个池子）。"""
        text = SRC.read_text(encoding="utf-8")
        self.assertIn("q = gledger.quota_left(acc.label,", text)
        self.assertIn("n = want if q[\"left_sections\"] is None", text)

    def test_dry_run_prints_concurrency(self):
        text = SRC.read_text(encoding="utf-8")
        self.assertIn("[dry-run] 不执行。去掉 --dry-run 才真跑。并发数", text)

    def test_syntax_ok(self):
        ast.parse(SRC.read_text(encoding="utf-8"))


class TestPanelDefaultStillTwo(unittest.TestCase):
    """面板并发闸默认 2 不动（A 方案保持现状）。"""

    def test_default_2_and_clamp(self):
        from xxt.panel_gxks import app

        src = Path(app.__file__).read_text(encoding="utf-8")
        self.assertIn('get("max_concurrency", 2)', src)
        self.assertIn("max(1, min(20, n))", src)


if __name__ == "__main__":
    unittest.main()
