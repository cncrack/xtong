"""2026-11-05 复核审查发现的 3 个问题（都是「昨天改对了、但细节没兜住」）。

## 问题 1：`tick` 心跳**永远不会发出**

昨天的实现：

```python
if last_clock is None or clock != last_clock:
    last_clock = clock
    last_tick_at = now        # ← 每次采样都刷新
elif now - last_tick_at >= 30:
    emit("tick", ...)         # ← 永远到不了
```

`m_time` **每秒都在涨** → 每次采样都命中「变了」分支 → `last_tick_at` 一直刷新
→ `elif` 永不成立 → **心跳一次都发不出来**（用户看不到 `tick` 行）。

**修法**：只按**时间间隔**发；`clock` 仅用于判断「是不是真的在走」。

## 问题 2：SSE 换文件后**跳过开头**

日志文件名带启动时刻（`gxks-study-default-20261104-115058.log`），
同一账号同一 kind 重新起任务会生成**新文件**，但 `_JOBS` 的 key（`label|kind`）不变。
只看 `size` 的话，`f.seek(旧文件大小)` 会**跳过新文件的开头**（新文件更短时读到空）。

**修法**：指纹里带 `path`，路径变了就把偏移归零。

## 问题 3：前端日志**按 label 分流，后端按 label|kind**

后端 SSE 推 `{"label": ..., "kind": ...}`，前端 `appendLogLines(label, text)`
把 `kind` 丢了 → 同账号的 `study` 与 `sections` 两个任务日志**混在一起**。

**修法**：前端也用 `label|kind` 做键（`logKey()`），并新增 `pickLogJob()`
按「在跑优先、study > work > sections」挑当前显示哪个。
"""

from __future__ import annotations

import inspect
import unittest

from xxt.adapters.gxks import driver as gdriver
from xxt.panel_gxks import app as A
from xxt.panel_gxks.page import PAGE


class TestTickActuallyFires(unittest.TestCase):
    def test_tick_not_keyed_on_clock_change(self):
        """不能用「clock 变了」当触发条件（那样永远不发）。"""
        src = inspect.getsource(gdriver.GxksDriver.watch)
        seg = src.split("clock = sample.get(\"clock\")", 1)[1].split("# 上报节奏", 1)[0]
        # 旧写法：`if last_clock is None or clock != last_clock:` 然后刷新基准
        self.assertNotIn("last_clock is None or clock != last_clock", seg,
                         "旧写法会让 tick 永不触发（m_time 每秒都变）")
        # 新写法：按时间间隔
        self.assertIn("now - last_tick_at >= 30", seg)
        self.assertIn('emit("tick"', seg)

    def test_tick_stops_when_clock_frozen(self):
        """计时器不动（视频停了）就不发心跳 —— 否则看起来像还在学。"""
        src = inspect.getsource(gdriver.GxksDriver.watch)
        seg = src.split("clock = sample.get(\"clock\")", 1)[1].split("# 上报节奏", 1)[0]
        self.assertIn("clock == last_clock", seg)


class TestWatcherResetsOnNewFile(unittest.TestCase):
    def test_tracks_path(self):
        src = inspect.getsource(A._watch_logs_loop)
        self.assertIn("last_path", src, "要记住上次的日志路径")
        self.assertIn('last_path.get(key) != info["path"]', src,
                      "路径变了必须把偏移归零（否则跳过新文件开头）")

    def test_resets_size_to_zero(self):
        src = inspect.getsource(A._watch_logs_loop)
        self.assertIn("last_size[key] = 0", src)

    def test_cleans_path_on_job_end(self):
        src = inspect.getsource(A._watch_logs_loop)
        self.assertIn("last_path.pop(key, None)", src)


class TestFrontendKeysByLabelAndKind(unittest.TestCase):
    def test_has_log_key_helper(self):
        self.assertIn("const logKey = (label, kind)", PAGE)

    def test_append_takes_kind(self):
        self.assertIn("function appendLogLines(label, kind, text)", PAGE)

    def test_sse_passes_kind(self):
        self.assertIn('appendLogLines(d.label, d.kind || "study"', PAGE)

    def test_pick_log_job_prefers_running(self):
        """当前显示哪个任务：在跑优先，study > work > sections。"""
        self.assertIn("function pickLogJob(", PAGE)
        body = PAGE.split("function pickLogJob(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("j.alive", body)
        self.assertIn("order", body)

    def test_maps_use_composite_key(self):
        self.assertIn('LOG_LINES.get(curKey)', PAGE)
        self.assertIn("LOG_JOBS.set(curKey, cur)", PAGE)


if __name__ == "__main__":
    unittest.main()
