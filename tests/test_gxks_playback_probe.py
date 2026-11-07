"""播放状态探针（2026-11-08）：日志必须能**自证**视频到底播没播。

## 背景（用户定的纪律）

用户原话：「以后有关视频播放问题请你查看日志后再去查问题」。

此前 `tick` 心跳只带 `clock / t / updates`。而 `clock` 是**页面墙钟**
（`m_time` 每秒 +1），**视频卡住它也照涨** —— 所以「日志里有 tick」
根本不能证明画面在播。11-07 那次 1317 争议就卡在这里：日志显示
clock 涨到 1167、上报 19 次，但用户在浏览器里看到窗口没动，
两边结论冲突、只能靠猜。

## 修法

`tick` 事件补上采样里的 `d`（视频总长）、`paused`、`ended`、`hidden`；
`watch_gxks.make_printer` 把它们摊开打印成一行。这样一行日志就能判断：

- `视频 95/1166s · 播放中 · 上报 1 次 · 页面计时 95 秒` → 真在播且入账
- `视频 1166/1166s · 已播完 · 上报 19 次` → 播完了
- `视频 300/1166s · 已暂停 · 上报 5 次` → 卡住/被暂停

**只加事件字段，不改 `classify` / `plan_action`** —— 播放行为零变化。

## 守什么

① `emit("tick", ...)` 必须带那几个字段（回退了就红）；
② `make_printer` 必须打印它们（否则事件里有、日志里还是看不见）；
③ 用 1317 历史 jsonl 的格式回放，确认新打印不会因缺字段崩。
"""

from __future__ import annotations

import inspect
import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from xxt.adapters.gxks import driver as gdriver


class TestTickCarriesPlaybackState(unittest.TestCase):
    def test_emit_includes_all_probe_fields(self):
        """`emit("tick")` 必须带完整播放状态，不能再只有 clock/t/updates。"""
        src = inspect.getsource(gdriver.GxksDriver.watch)
        # 取 `emit("tick"` 到 `last_clock = clock` 之间那一整段（不能用 `split(")")`，
        # 因为 `sample.get("t")` 里就有右括号，会提前截断）
        seg = src.split('emit("tick"', 1)[1].split("last_clock = clock", 1)[0]
        for field in ("clock=", "t=", "d=", "paused=", "ended=",
                      "hidden=", "updates="):
            self.assertIn(field, seg,
                          f"tick 事件缺 {field} → 日志无法自证视频是否真在播")

    def test_does_not_change_classification(self):
        """只加事件字段，不能碰判定逻辑（否则播放行为会变）。"""
        src = inspect.getsource(gdriver.GxksDriver.watch)
        # emit 那一段里不能出现 classify/plan_action 的调用
        seg = src.split('emit("tick"', 1)[1].split("last_clock", 1)[0]
        self.assertNotIn("classify(", seg)
        self.assertNotIn("plan_action(", seg)


class TestPrinterShowsPlaybackState(unittest.TestCase):
    def _load_watch(self):
        """以模块方式加载 scripts/watch_gxks.py（它不是包）。"""
        import importlib.util

        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location(
            "watch_gxks_under_test", root / "scripts" / "watch_gxks.py")
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)
        return mod

    def test_prints_video_progress_and_paused(self):
        """一行日志要同时看得出：视频进度 / 播放中还是暂停 / 上报次数。"""
        mod = self._load_watch()
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            on_event, close = mod.make_printer(Path(tmp) / "x.jsonl")
            buf = io.StringIO()
            with redirect_stdout(buf):
                on_event({"kind": "tick", "clock": 95, "t": 95, "d": 1166,
                          "paused": False, "ended": False, "hidden": False,
                          "updates": 1})
            close()
        out = buf.getvalue()
        self.assertIn("视频 95/1166s", out)
        self.assertIn("播放中", out)
        self.assertIn("上报 1 次", out)
        self.assertIn("页面计时 95 秒", out)

    def test_prints_paused_and_ended(self):
        mod = self._load_watch()
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            on_event, close = mod.make_printer(Path(tmp) / "x.jsonl")
            buf = io.StringIO()
            with redirect_stdout(buf):
                on_event({"kind": "tick", "clock": 1167, "t": 1166, "d": 1166,
                          "paused": True, "ended": True, "updates": 19})
            close()
        out = buf.getvalue()
        self.assertIn("已暂停", out)
        self.assertIn("已播完", out)

    def test_old_events_without_new_fields_still_work(self):
        """兼容老日志（没有 d/paused/ended/hidden）：不能因缺字段抛异常。"""
        mod = self._load_watch()
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            on_event, close = mod.make_printer(Path(tmp) / "x.jsonl")
            buf = io.StringIO()
            with redirect_stdout(buf):
                on_event({"kind": "tick", "clock": 305, "t": 305,
                          "updates": 5})           # 11-07 的老格式
            close()
        self.assertIn("页面计时 305 秒", buf.getvalue())
        self.assertIn("视频 305s", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
