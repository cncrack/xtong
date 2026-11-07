"""节缓存「正在学」标记（2026-11-07 用户：「正在学习的这节没有显示为正在学习」）。

## 现象

用户截图：1317 那节正在播（日志里 `tick 页面计时 1167 秒`），但学习进度区写「**未学习**」。

## 根因

两层叠加：

1. 平台**每 60 秒**才上报一次学时，列表回读默认**每 5 分钟**一次
   → 刚开播那几分钟，回读到的行仍是 `state = not_started / done_minutes = 0`；
2. `scripts/run_gxks.py` 的 `_on_event` **只处理 `progress`**
   （`if ev.get("kind") != "progress": return`）→ `tick` 心跳不触发回写，
   面板就一直看着那份没更新的缓存。

## 修法

- `sections_cache.update_row(..., playing=True)`：把该节写成 `state = "learning"`。
  **纯展示标记** —— `done_minutes` 仍以平台回读为准，`done_on_platform` 仍看真实 state，
  不伪造任何平台数据。
- `run_gxks.py` 的 `_on_event` 也处理 `tick`（零平台开销的页面内墙钟心跳，30 秒一次），
  传 `playing=True`；切节/结束时传 `playing=False` 撤掉标记。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from xxt.adapters.gxks import sections_cache as gcache


class TestPlayingFlag(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.debug = Path(self.tmp.name)
        # 先造一份缓存：一节未学、一节已完成
        gcache.update_row(self.debug, "a1", "32", "1317",
                          done_minutes=0, state="not_started", state_text="未学习",
                          name="4.6 人工智能重塑未来战场", required_minutes=19)
        gcache.update_row(self.debug, "a1", "32", "1290",
                          done_minutes=48, state="done", state_text="已完成",
                          name="1.2 国防法规", required_minutes=42)

    def tearDown(self):
        self.tmp.cleanup()

    def _row(self, sid: str) -> dict:
        data = json.loads(gcache.path(self.debug, "a1", "32").read_text(encoding="utf-8"))
        return next(r for r in data["rows"]
                    if str(r.get("platform_chapter_id")) == sid)

    def test_playing_marks_learning(self):
        """`playing=True` 把未学的节标成 learning（面板据此显示「学习中」）。"""
        gcache.update_row(self.debug, "a1", "32", "1317", playing=True)
        row = self._row("1317")
        self.assertEqual(row["state"], "learning")
        self.assertEqual(row["state_text"], "学习中")
        # **不伪造学时**：平台没给的仍是 0
        self.assertEqual(row["done_minutes"], 0)

    def test_playing_does_not_downgrade_done(self):
        """已完成的节不能被 `playing` 倒回 learning（否则「已完成」会闪回「学习中」）。"""
        gcache.update_row(self.debug, "a1", "32", "1290", playing=True)
        row = self._row("1290")
        self.assertEqual(row["state"], "done")

    def test_unset_clears_marker(self):
        """`playing=False` 撤掉标记：按 `done_minutes` 回到 done / not_started。"""
        gcache.update_row(self.debug, "a1", "32", "1317", playing=True)
        gcache.update_row(self.debug, "a1", "32", "1317", playing=False)
        row = self._row("1317")
        self.assertEqual(row["state"], "not_started")
        self.assertEqual(row["state_text"], "未学习")

    def test_unset_with_enough_minutes_marks_done(self):
        """撤标记时学时已够 → 直接判 done（不必等下一次 progress 回读）。"""
        gcache.update_row(self.debug, "a1", "32", "1317",
                          done_minutes=19, playing=True)
        gcache.update_row(self.debug, "a1", "32", "1317", playing=False)
        row = self._row("1317")
        self.assertEqual(row["state"], "done")
        self.assertEqual(row["state_text"], "已完成")

    def test_summary_counts_learning_as_not_done(self):
        """learning 不算「已完成」（否则节数会虚高）。"""
        gcache.update_row(self.debug, "a1", "32", "1317", playing=True)
        data = json.loads(gcache.path(self.debug, "a1", "32").read_text(encoding="utf-8"))
        self.assertEqual(data["summary"]["done"], 1)   # 只有 1290
        self.assertEqual(data["summary"]["sections"], 2)


if __name__ == "__main__":
    unittest.main()
