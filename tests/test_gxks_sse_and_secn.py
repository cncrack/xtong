"""2026-11-04 上午的三处修复（都在实测中被用户抓到）。

## 1. SSE 把整个面板卡死（阻塞事件循环）

`/api/events` 的生成器里直接 `q.get(timeout=15)` —— 这是**阻塞调用**，
在 async 生成器里会**卡住事件循环** → 面板所有接口（含 `/api/state`）一起超时，
表现为「面板卡死、点学习没反应」。

**修法**：`await asyncio.to_thread(q.get, True, 15.0)`。

## 2. 「本次跑几节」改了不生效（重绘覆盖用户输入）

账号表每 4 秒被 `load()` 整块重绘（`renderAccounts` 重建 innerHTML），
输入框里的用户输入被**重置回默认值 3** → 用户填了 5，点了还是 3 节。

**修法**：`SEC_N` Map 记住每个账号填过的值，重绘时读回来；输入框加
`oninput`/`onchange` 回调。

## 3. 面板热重载会杀掉正在跑的刷课任务（**推翻 11-03 的设计假设**）

11-03 devlog 决策 2 写的是：

> 对挂机刷课**零影响**：刷课是独立子进程（`_spawn_script` 起的），**面板重启不碰它**

**实测证明这条假设是错的**：Windows 上父进程被强杀（`taskkill /T`）会
**连带终止整棵进程树** → 刷课脚本被杀 → 日志末尾 `KeyboardInterrupt` →
任务中断、**不会自动切下一节**。

**修法**：默认**关掉**面板热重载（`panel.reload=False`），改代码后手动重启面板。
理由：热重载的收益（改代码自动生效）远小于它的代价（正在跑的刷课被误杀）。
"""

from __future__ import annotations

import inspect
import unittest

from xxt.panel_gxks import app as A
from xxt.panel_gxks.page import PAGE


class TestSseDoesNotBlockEventLoop(unittest.TestCase):
    def test_uses_to_thread(self):
        """阻塞读必须挪到线程池。"""
        src = inspect.getsource(A.create_app)
        self.assertIn("asyncio.to_thread", src,
                      "SSE 生成器里必须用 await asyncio.to_thread(q.get, ...)")

    def test_no_direct_blocking_get(self):
        """不能再有裸的 `q.get(timeout=...)`（会卡事件循环）。

        ⚠ 只看**真实代码行**：注释里为了说明问题会故意写这个字符串。
        """
        src = inspect.getsource(A.create_app)
        code = "\n".join(ln for ln in src.splitlines()
                         if not ln.strip().startswith("#"))
        self.assertNotIn("q.get(timeout=", code,
                         "裸 q.get(timeout=) 会阻塞事件循环 → 整个面板卡死")
        self.assertNotIn("payload = q.get(", code)


class TestPerRunSectionsRemembered(unittest.TestCase):
    """用户填的节数不能被 4 秒重绘冲掉。"""

    def test_has_sec_n_map(self):
        self.assertIn("const SEC_N = new Map()", PAGE)

    def test_input_calls_remember(self):
        self.assertIn("rememberSecN", PAGE)
        self.assertIn('oninput="rememberSecN(this)"', PAGE)

    def test_render_reads_saved_value(self):
        """渲染时要用记住的值，不是写死的默认 3。"""
        self.assertIn("SEC_N.get(c.label)", PAGE)
        self.assertNotIn("const n = Math.min(3, cap);", PAGE,
                         "不能写死 3（用户改的值会被覆盖）")

    def test_remember_clamps(self):
        fn = PAGE.split("function rememberSecN(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("SEC_N.set", fn)
        self.assertIn("max", fn)


class TestHotReloadOffByDefault(unittest.TestCase):
    """面板热重载默认关（避免杀掉正在跑的刷课任务）。"""

    def test_default_is_off(self):
        from xxt.store import gxks_panel_settings as gp
        self.assertFalse(gp.DEFAULTS.get("reload", True),
                         "面板热重载默认必须关：面板重启会连带杀掉刷课任务")

    def test_serve_reads_setting(self):
        src = inspect.getsource(A.serve)
        self.assertIn("reload", src)


if __name__ == "__main__":
    unittest.main()
