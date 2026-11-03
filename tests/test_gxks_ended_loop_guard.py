"""`ended` 死循环的总闸（2026-11-04 用户「又停了」的真因）。

## 现象

1305「3.2 外国军事思想」：视频 **3011 秒（50.2 分钟）播完**，但平台列表**只记 39/48**。
日志每 70 秒刷一遍：

```
[01:45:13] progress 39/48 learning
[01:45:43] ended
[01:45:43] ended_short 视频播完但列表只 39/48（快照可能过期）→ 立即 fresh 回读，仍差再等 240s
[01:46:13] progress 39/48 learning
[01:46:13] ended
...
[01:49:43] replay_short 视频播完但只 39/48 → 第 1/1 次补播
[01:50:19] ended
[01:50:19] ended_short ...
```

**永不退出、也不切下一节**。用户体感是「又停了」，实际是**原地打转**。

## 根因

补播成功后代码把 `ended_at = None`（重置等待期）。但补播**没真播起来**（又立刻 ended），
于是 `if ended_at is None: ended_at = now` 又触发 → 240 秒等待期**无限重来**。

`max_replays` 管不住它：`should_replay` 虽然返回 False（补播次数用完），
但代码路径是 `continue`（继续等），不是 `return`。

## 修法

加一个**累计总闸**：`ended_since` 记录「第一次进入 ended 的时刻」，
`ended_total = now - ended_since` 累计呆在 ended 状态的总时长；
超过 `ENDED_TOTAL_MAX_SECONDS`（660 秒）就 `return escalate` 认命退出。

- 补播时**不重置** `ended_since`（否则死循环照旧）；
- 视频**真的重新走起来**（`state == "ok"`）才重置。

为什么 660 秒：一次完整「等 240s + 补播窗口（REPLAY_WINDOW_SECONDS）」
≈ 240 + 300 = 540 秒，再留一次回读与抖动的余量。
"""

from __future__ import annotations

import inspect
import unittest

from xxt.adapters.gxks import driver as gdriver


class TestEndedTotalCap(unittest.TestCase):
    def test_constant_exists_and_reasonable(self):
        cap = gdriver.ENDED_TOTAL_MAX_SECONDS
        # 必须大于一次完整「等待期 + 补播窗口」，否则补播还没跑完就被砍
        settle = gdriver.ENDED_SETTLE_SECONDS
        window = gdriver.REPLAY_WINDOW_SECONDS
        self.assertGreater(cap, settle + window,
                           f"总闸 {cap}s 必须 > 等待期 {settle}s + 补播窗口 {window}s")
        # 也不能太离谱（单节不该被无限拖住）
        self.assertLess(cap, 1800, "总闸超过半小时就不合理了")

    def test_watch_has_ended_total_guard(self):
        src = inspect.getsource(gdriver.GxksDriver.watch)
        self.assertIn("ended_since", src, "要有 ended 起始时刻的记录")
        self.assertIn("ended_total", src, "要累计 ended 时长")
        self.assertIn("ENDED_TOTAL_MAX_SECONDS", src)
        self.assertIn("ended_too_long", src, "退出时要给出明确原因")

    def test_guard_returns_escalate(self):
        """超时必须是 `escalate`（让上层切下一节），不能继续 `continue`。"""
        src = inspect.getsource(gdriver.GxksDriver.watch)
        seg = src.split("ENDED_TOTAL_MAX_SECONDS", 1)[1].split("emit(", 1)[1]
        self.assertIn("escalate", seg, "超总闸后要 return escalate")

    def test_replay_does_not_reset_ended_since(self):
        """补播那段**不能**重置 ended_since（否则死循环照旧）。"""
        src = inspect.getsource(gdriver.GxksDriver.watch)
        # 找到 replay_short 那段
        seg = src.split("replay_short", 1)[1].split("continue", 1)[0]
        self.assertNotIn("ended_since = None", seg,
                         "补播不该重置 ended_since（只有 state==ok 才重置）")
        self.assertIn("ended_at = None", seg, "补播要清 ended_at（等待期重来是对的）")

    def test_ok_state_resets_guard(self):
        """视频真走起来了才重置累计，否则补播播几秒就被旧累计判超时。"""
        src = inspect.getsource(gdriver.GxksDriver.watch)
        seg = src.split('if state == "ok":', 1)[1].split("self.mute()", 1)[0]
        self.assertIn("ended_since = None", seg)


class TestEndedGuardOrdering(unittest.TestCase):
    """总闸必须**先于**立即回读判定，否则永远走不到。"""

    def test_guard_before_fresh_read(self):
        src = inspect.getsource(gdriver.GxksDriver.watch)
        seg = src.split('if state == "ended":', 1)[1]
        guard_pos = seg.find("ENDED_TOTAL_MAX_SECONDS")
        read_pos = seg.find("fresh = self.read_row")
        self.assertGreater(guard_pos, -1, "找不到总闸")
        self.assertGreater(read_pos, -1, "找不到立即回读")
        self.assertLess(guard_pos, read_pos, "总闸要在回读之前判，否则永远轮不到")


if __name__ == "__main__":
    unittest.main()
