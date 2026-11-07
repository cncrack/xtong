"""`scripts/run_gxks.py` 的**变量定义顺序**回归（2026-11-07 我改出来的真 bug）。

## 事故

修「凭据按账号取」时，我把 `acc = find_by_label(...)` 写在额度判定**之后**，
而额度判定第 207 行就要用 `acc.daily_sections`：

```
UnboundLocalError: cannot access local variable 'acc' where it is not associated with a value
```

现象非常像"功能没实现"：面板点「学习」→ 提示"浏览器就绪 → 开始刷课" →
**4 秒后进程死了** → 界面上的「学习」按钮又变回可点，用户以为"点了没反应"。
日志里只有一行 traceback，而且**只在真正跑起来时才出现**（单测全部绿）。

## 为什么单测没抓到

`pytest` 里没有任何用例真的**跑一遍 `main()` 的完整流程** —— 全是打桩调子函数。
所以变量顺序这种"只在真跑时才炸"的问题，靠单测抓不到。

## 这次守两层

1. **静态**：AST 扫描 `main()`，确认所有"被使用前未被赋值"的模块级局部变量；
   具体到本案：`acc` 第一次出现必须是**赋值**，不能是读取。
2. **动态**：真跑 `main()`（网络自检、浏览器、账本全打桩），断言它走到
   "起浏览器"这一步而不是抛 `UnboundLocalError`。
"""

from __future__ import annotations

import ast
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_gxks.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("run_gxks_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class TestNoUseBeforeAssign(unittest.TestCase):
    """静态守卫：`main()` 里不能出现"先读后赋值"的变量。"""

    def test_acc_is_assigned_before_any_use(self):
        """`acc` 的第一次出现必须是**赋值**（Store），不能是读取（Load）。

        这就是本案的直接修复点。它在第 214 行被 `acc.daily_sections` 读，
        而赋值在第 209 行 —— 顺序一颠倒就 `UnboundLocalError`。
        """
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
        main = next(n for n in tree.body
                    if isinstance(n, ast.FunctionDef) and n.name == "main")
        events = []
        for node in ast.walk(main):
            if isinstance(node, ast.Name) and node.id == "acc":
                events.append((node.lineno,
                               isinstance(node.ctx, ast.Store)))
        self.assertTrue(events, "main() 里居然没有 acc 的引用？脚本被改坏了")
        events.sort()
        first_line, first_is_store = events[0]
        self.assertTrue(
            first_is_store,
            f"main() 第 {first_line} 行**第一次出现 acc 就是读取** → 运行到那里必然 "
            "UnboundLocalError（现象：面板提示已开始、4 秒后进程就死、按钮又变回可点）")

    def test_no_other_use_before_assign_in_main(self):
        """扫全函数：任何变量在"赋值前被读"都报出来。

        只查模块内真正常见的坑（`acc` / `cred` / `q` 这类在多个分支里赋值的），
        不做完整的控制流分析 —— 那会误报 for 循环变量、comprehension、
        `with ... as` 等一大堆合法写法。
        """
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
        main = next(n for n in tree.body
                    if isinstance(n, ast.FunctionDef) and n.name == "main")
        stores: set[str] = set()
        bad: list[tuple[int, str]] = []
        # 按行号顺序遍历**直接语句**（不含嵌套函数体，避免把闭包里的名字算进来）
        for node in sorted(ast.walk(main), key=lambda n: getattr(n, "lineno", 0)):
            if isinstance(node, ast.Name):
                if isinstance(node.ctx, ast.Store):
                    stores.add(node.id)
                elif isinstance(node.ctx, ast.Load) and node.id not in stores:
                    # 函数参数、推导式目标、except 变量等由 AST 其它节点产生，跳过
                    bad.append((node.lineno, node.id))
        # 只关心"读之前先赋值"的顶层顺序问题；参数与推导式会产生误报，这里只挑已知关键名
        suspicious = [(ln, n) for ln, n in bad if n in ("acc", "cred", "q")]
        self.assertEqual(suspicious, [], f"这些变量在被赋值前就被读了：{suspicious}")


class TestMainRunsPastQuotaSection(unittest.TestCase):
    """动态守卫：真跑 main()，确认它**跨过额度判定那一段**而不抛 UnboundLocalError。"""

    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / "data").mkdir(parents=True)
        import json

        (self.root / "data" / "accounts.local.json").write_text(
            json.dumps({"accounts": [{"label": "a1", "port": 9222}]}),
            encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_reaches_open_session(self):
        """跑到 `open_session` 就算过了额度那一段；不许出现 UnboundLocalError。

        `open_session` 打桩成抛 `SessionError` → main 会打印 `[FAIL]` 并 return 2。
        只要返回码是 2（而不是抛异常），就证明额度判定那一段跑通了。
        """
        import os

        mod = _load_module()
        old_argv, old_root = sys.argv, os.environ.get("XXT_ROOT")
        os.environ["XXT_ROOT"] = str(self.root)
        sys.argv = ["run_gxks.py", "--label", "a1", "--account-label", "a1",
                    "--no-netcheck", "--no-relogin"]
        try:
            with mock.patch.object(mod, "gbrowser") as gb, \
                 mock.patch.object(mod, "gledger") as gl, \
                 mock.patch.object(mod, "gaccounts"):

                class _SessionError(Exception):
                    """真 `SessionError` 有 `.hint` 属性，打桩必须带上。"""
                    hint = ""

                gb.SessionError = _SessionError
                gb.open_session.side_effect = _SessionError("打桩：不真连浏览器")
                gb.state_path.return_value = self.root / "data" / "nope.json"
                gb.state_summary.return_value = {"saved": False}
                gl.quota_left.return_value = {
                    "sections": 0, "minutes": 0.0, "exhausted": False,
                    "left_sections": None, "left_minutes": None}
                # find_by_label 必须返回一个带 daily_sections 的对象（这段用到它）
                acc = mock.Mock(daily_sections=10, daily_minutes=600,
                                configured=True, label="a1")
                mod.gaccounts.find_by_label.return_value = acc
                mod.gaccounts.load_accounts.return_value = [acc]
                try:
                    code = mod.main()
                except UnboundLocalError as exc:
                    self.fail(f"额度判定那段又踩了「先读后赋值」：{exc}")
                except Exception as exc:                       # noqa: BLE001
                    # 打桩抛的 Exception 会被 except SessionError 捕到；
                    # 其它异常说明流程走偏，也要暴露
                    self.fail(f"意外异常：{type(exc).__name__}: {exc}")
            self.assertEqual(code, 2, "应当因 SessionError 返回 2")
        finally:
            sys.argv = old_argv
            if old_root is None:
                os.environ.pop("XXT_ROOT", None)
            else:
                os.environ["XXT_ROOT"] = old_root


if __name__ == "__main__":
    unittest.main()
