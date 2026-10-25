"""模块边界守护测试（2026-10-25 解耦，devlog 28）。

背景：异步引擎 `astudy.py` 曾经 `from .study import WATCHER_JS, build_watcher_js`——
复用了同步版里的函数，其中 `username_sniffer` 等直接返回同步 Playwright 对象，
在 async 上下文里调用会静默产生"未 await 的协程"，导致账号#2 会话被 TaskGroup 连带取消
（devlog 26）。解耦后靠三条静态断言把这类事故挡在提交前：

1. `astudy.py` 不得 import `study` / `pagesync`
2. `pagesync.py` 不得出现 `async def` / `await`（它是同步专用）
3. `pagesync.py` 的引用方只能来自白名单（study / cli / panel）
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "xxt"
ADAPTERS = SRC / "adapters" / "chaoxing"

# pagesync 允许被谁引用：同步引擎 + 作业链路（cli/panel）。新增即需同步审查。
PAGESYNC_ALLOWED = {
    "adapters/chaoxing/study.py",
    "adapters/chaoxing/pagesync.py",
    "cli.py",
    "panel/app.py",
}


def _text(rel: str) -> str:
    return (SRC / rel).read_text(encoding="utf-8")


def _code(rel: str) -> str:
    """剥掉三引号文档字符串后再判断——注释里提到 `page.xxx()` 不算违规。"""
    return re.sub(r'""".*?"""', "", _text(rel), flags=re.S)


class AsyncEngineIsolationTest(unittest.TestCase):
    def test_astudy_does_not_import_sync_modules(self):
        """异步引擎只能引用 pagejs 这类纯数据模块。"""
        src = _text("adapters/chaoxing/astudy.py")
        bad = re.findall(r"^\s*from \.\s*(study|pagesync)\s+import", src, re.M)
        self.assertEqual(bad, [], f"astudy.py 不得 import 同步模块，命中：{bad}")
        bad2 = re.findall(r"^\s*import .*\bpagesync\b", src, re.M)
        self.assertEqual(bad2, [])

    def test_pagejs_is_pure_data(self):
        """共用模块里不能出现 page/frame 对象操作——那是同步 API 的入口。"""
        src = _code("adapters/chaoxing/pagejs.py")
        self.assertNotIn("async def", src)
        self.assertNotIn("import playwright", src)
        self.assertIsNone(re.search(r"(?<![\w.])(page|frame|fr)\.\w+\(", src),
                          "pagejs.py 出现 page/frame 调用，应移到 pagesync.py")


class PageSyncIsSyncOnlyTest(unittest.TestCase):
    def test_pagesync_has_no_async(self):
        src = _code("adapters/chaoxing/pagesync.py")
        self.assertNotIn("async def", src, "pagesync.py 是同步专用，禁止混入 async 函数")
        self.assertNotIn("await ", src)

    def test_pagesync_importers_are_whitelisted(self):
        """全仓扫描：谁引用了 pagesync，必须在白名单内。"""
        hits: set[str] = set()
        for py in SRC.rglob("*.py"):
            rel = py.relative_to(SRC).as_posix()
            src = py.read_text(encoding="utf-8")
            if re.search(r"(?:^|\s)from \.*pagesync import", src, re.M):
                hits.add(rel)
        unexpected = hits - PAGESYNC_ALLOWED
        self.assertEqual(unexpected, set(),
                         f"未授权模块引用了 pagesync（禁止用于异步引擎）：{unexpected}")

    def test_two_engines_expose_same_core_entrypoints(self):
        """两个引擎都必须有 run 入口且都做时段判定——防止只修一边。"""
        for rel in ("adapters/chaoxing/study.py", "adapters/chaoxing/astudy.py"):
            src = _text(rel)
            self.assertIn("within_daily_window", src, rel)
            self.assertIn("is_plausible_completion", src, rel)


if __name__ == "__main__":
    unittest.main()
