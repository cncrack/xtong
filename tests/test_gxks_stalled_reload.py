"""`stalled` 必须**重载恢复**，不能干等（2026-11-04 实测白丢 9 分钟学时）。

## 现象（1305「3.2 外国军事思想」，视频 3011 秒 = 50.2 分钟）

| 时刻 | 平台学时 | 说明 |
|---|---|---|
| 01:30:36 | 36/48 | 正常，1:1 记账 |
| **01:33:57** | — | **`stalled`（视频卡住）** |
| 01:36:06 | 39/48 | 之后**再也不涨** |
| 01:45:43 | 39/48 | 视频播完 |

最终：视频播 50.2 分钟，平台只记 **39/48**，差 9 分钟。
视频已播完无法再补 → `escalate` → **整节作废，60 分钟白跑**。

## 根因

`plan_action` 原来给 `stalled` 返回 `"wait"`（干等它自己恢复）。

干等为什么没用：
1. 卡住时**画面不推进**；
2. 平台按**墙钟**计时（`m_time` 每秒 +1），不看 `currentTime`；
3. ⇒ 那段时间服务端**不记账**（与 Q13「媒体重建后服务端停止记账」同源）。

用户在日志里看到的是 `progress 39/48` 连续 9 次不变，以为「停了」。

## 修法

`stalled` 与 `rebuild` **共用同一套恢复路径**：
`reload`（重载播放页开新会话），带冷却 `RELOAD_COOLDOWN_SECONDS`
与次数上限 `MAX_RECOVERIES_PER_SECTION`，避免重载风暴。
"""

from __future__ import annotations

import unittest

from xxt.adapters.gxks.driver import (
    MAX_RECOVERIES_PER_SECTION,
    RELOAD_COOLDOWN_SECONDS,
    plan_action,
)


class TestStalledTriggersReload(unittest.TestCase):
    def test_stalled_returns_reload(self):
        """核心断言：卡住要重载，不是干等。"""
        self.assertEqual(plan_action("stalled"), "reload")

    def test_stalled_respects_cooldown(self):
        """刚重载过就等一会儿，避免重载风暴。"""
        self.assertEqual(
            plan_action("stalled", seconds_since_reload=RELOAD_COOLDOWN_SECONDS / 2),
            "wait")

    def test_stalled_after_cooldown_reloads(self):
        self.assertEqual(
            plan_action("stalled", seconds_since_reload=RELOAD_COOLDOWN_SECONDS + 1),
            "reload")

    def test_stalled_gives_up_after_max_recoveries(self):
        """重载次数用完 → 认命（交给上层切下一节），不能无限重载。"""
        self.assertEqual(
            plan_action("stalled", reloads=MAX_RECOVERIES_PER_SECTION), "escalate")

    def test_other_states_unchanged(self):
        """回归：其他状态不能被这次改动带偏。"""
        self.assertEqual(plan_action("ok"), "none")
        self.assertEqual(plan_action("loading"), "wait")
        self.assertEqual(plan_action("paused"), "play")
        self.assertEqual(plan_action("hidden"), "foreground")
        self.assertEqual(plan_action("ended"), "escalate")
        self.assertEqual(plan_action("unknown"), "wait")


class TestStalledSamePathAsRebuild(unittest.TestCase):
    """stalled 与 rebuild 的恢复策略必须一致（都是「重载开新会话」）。"""

    def test_same_reload_path(self):
        self.assertEqual(plan_action("stalled"), plan_action("rebuild"))
        self.assertEqual(
            plan_action("stalled", reloads=MAX_RECOVERIES_PER_SECTION),
            plan_action("rebuild", reloads=MAX_RECOVERIES_PER_SECTION))


if __name__ == "__main__":
    unittest.main()
