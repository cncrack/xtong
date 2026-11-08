"""节缓存状态一致性（2026-11-08 用户：「两个账号的进度区不一样显示」）。

## 现象

- 账号甲（default）：进度区「还是原来的显示内容」，4.6 平台早学完仍在待学区
  显示「0/19 分 未学习 未解锁」；
- 账号乙（test）：正在播的节显示「学习中」，但实测在 learning↔not_started
  间震荡（35 秒两次快照：learning/0分 → not_started/5分）。

## 根因（日志 + 缓存 + 代码三重证据）

1. **progress 回读会回降 learning**：`update_row` 原来无条件照写平台 `state`。
   平台列表页的 state 翻转比学时滞后（学时 60 秒上报、列表缓存更久），
   progress 每 5 分钟回读到 `not_started` 是常态 → 把 tick 刚标的 learning
   覆盖掉，面板「学习中」一闪一闪。
2. **SKIP 不回写**：driver 用 `find_section` 知道历史节已 done 并跳过，但
   「只有正在播的节」才有人回写缓存 → 4.6（1317）这类节永远停在几天前的
   not_started/未解锁；而面板「刷新节列表」对任务在跑的账号是拒绝的
   （防列表页抢播放页 frame），人工也救不回来。
3. **中断卡 learning**：任务死了 tick 不再来，行永久停在 learning。

## 修法

- `update_row`：平台只有明确 `done`（终态）才允许覆盖 learning；
  `not_started` 不回降（progress 事件本身证明 driver 还在播这一节）；
  learning 记 `learning_at` 心跳时间戳。
- 读方 `app._normalize_stale_learning`：learning 超 90 秒无心跳 →
  按 `done_minutes` 推导降级展示（只影响展示，不改缓存文件）。
- `run_gxks.py` SKIP 分支：`find_section` 刚读过该行，**零额外平台请求**
  把真值回写缓存。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from xxt.adapters.gxks import sections_cache as gcache


class TestProgressNoDowngrade(unittest.TestCase):
    """修复 A：progress 回读（playing=False）不许回降 learning。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.debug = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _row(self, sid: str = "1319") -> dict:
        data = json.loads(gcache.path(self.debug, "a1", "32").read_text(encoding="utf-8"))
        return next(r for r in data["rows"]
                    if str(r.get("platform_chapter_id")) == sid)

    def _seed(self, **kw) -> None:
        gcache.update_row(self.debug, "a1", "32", "1319",
                          name="5.2 正在播的节", required_minutes=55, **kw)

    def test_tick_then_progress_not_started_keeps_learning(self):
        """tick 标 learning 后，progress 回读到滞后的 not_started → 保持 learning。

        这是 2026-11-08 实测的震荡场景：1319 在 learning/not_started 间来回闪，
        done_minutes 却从 0 涨到 5（学时是平台真值，照更）。
        """
        self._seed(done_minutes=0, state="not_started")
        gcache.update_row(self.debug, "a1", "32", "1319", playing=True)
        # progress：平台列表 state 还没翻（滞后），但学时已涨到 5
        gcache.update_row(self.debug, "a1", "32", "1319",
                          done_minutes=5, state="not_started", state_text="未学习",
                          playing=False)
        row = self._row()
        self.assertEqual(row["state"], "learning")
        self.assertEqual(row["state_text"], "学习中")
        self.assertEqual(row["done_minutes"], 5)

    def test_progress_done_overrides_learning(self):
        """平台明确 done（终态）→ 无条件写，learning 被覆盖。"""
        self._seed(state="not_started")
        gcache.update_row(self.debug, "a1", "32", "1319", playing=True)
        gcache.update_row(self.debug, "a1", "32", "1319",
                          done_minutes=55, state="done", state_text="已完成",
                          playing=False)
        row = self._row()
        self.assertEqual(row["state"], "done")
        self.assertEqual(row["state_text"], "已完成")

    def test_progress_syncs_non_learning_rows(self):
        """非 learning 的行仍按平台值正常同步（原行为不变）。"""
        self._seed(state="not_started")
        gcache.update_row(self.debug, "a1", "32", "1319",
                          done_minutes=10, state="learning", state_text="学习中",
                          playing=False)
        row = self._row()
        self.assertEqual(row["state"], "learning")

    def test_progress_read_failure_still_rolls_back(self):
        """progress 读不到行（state 缺省）→ 原撤回语义保留：按分钟推导。"""
        self._seed(state="not_started", done_minutes=0)
        gcache.update_row(self.debug, "a1", "32", "1319", playing=True)
        gcache.update_row(self.debug, "a1", "32", "1319",
                          done_minutes=0, playing=False)
        row = self._row()
        self.assertEqual(row["state"], "not_started")

    def test_playing_none_leaves_learning_untouched(self):
        """`playing=None`（调用方不关心标记，如 SKIP 回写）→ 原样跳过，不撤回。"""
        self._seed(state="learning", state_text="学习中", done_minutes=3)
        gcache.update_row(self.debug, "a1", "32", "1319", done_minutes=4)
        row = self._row()
        self.assertEqual(row["state"], "learning")
        self.assertEqual(row["state_text"], "学习中")
        self.assertEqual(row["done_minutes"], 4)


class TestLearningHeartbeat(unittest.TestCase):
    """修复 C 写方：learning 记 `learning_at` 心跳时间戳。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.debug = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _row(self) -> dict:
        data = json.loads(gcache.path(self.debug, "a1", "32").read_text(encoding="utf-8"))
        return next(r for r in data["rows"]
                    if str(r.get("platform_chapter_id")) == "1319")

    def test_tick_writes_learning_at(self):
        gcache.update_row(self.debug, "a1", "32", "1319",
                          name="5.2", required_minutes=55,
                          state="not_started", playing=True)
        self.assertTrue(self._row().get("learning_at"))

    def test_leaving_learning_clears_learning_at(self):
        gcache.update_row(self.debug, "a1", "32", "1319",
                          name="5.2", required_minutes=55, playing=True)
        gcache.update_row(self.debug, "a1", "32", "1319",
                          done_minutes=55, state="done", playing=False)
        self.assertNotIn("learning_at", self._row())


class TestStaleLearningNormalize(unittest.TestCase):
    """修复 C 读方：/api/state 对超时 learning 降级展示（不改缓存文件）。"""

    def _norm(self, sections):
        from xxt.panel_gxks.app import _normalize_stale_learning
        return _normalize_stale_learning(sections)

    def test_stale_learning_downgrades(self):
        old = (datetime.now() - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
        sections = {"rows": [
            {"platform_chapter_id": "1319", "state": "learning", "learning_at": old,
             "done_minutes": 5, "required_minutes": 55, "state_text": "学习中"},
        ]}
        r = self._norm(sections)["rows"][0]
        self.assertEqual(r["state"], "not_started")
        self.assertIn("已中断", r["state_text"])

    def test_stale_learning_with_enough_minutes_becomes_done(self):
        old = (datetime.now() - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
        sections = {"rows": [
            {"platform_chapter_id": "1317", "state": "learning", "learning_at": old,
             "done_minutes": 19, "required_minutes": 19, "state_text": "学习中"},
        ]}
        r = self._norm(sections)["rows"][0]
        self.assertEqual(r["state"], "done")

    def test_fresh_learning_untouched(self):
        fresh = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sections = {"rows": [
            {"platform_chapter_id": "1319", "state": "learning", "learning_at": fresh},
        ]}
        r = self._norm(sections)["rows"][0]
        self.assertEqual(r["state"], "learning")

    def test_old_row_without_timestamp_untouched(self):
        """修复前写的老行没有 learning_at → 无法判断，保持原样。"""
        sections = {"rows": [
            {"platform_chapter_id": "1319", "state": "learning"},
        ]}
        r = self._norm(sections)["rows"][0]
        self.assertEqual(r["state"], "learning")

    def test_empty_and_missing_rows(self):
        self.assertEqual(self._norm({}), {})
        self.assertEqual(self._norm({"rows": []}), {"rows": []})


class TestSkipWritesBack(unittest.TestCase):
    """修复 B：driver 跳过已达标节时把平台真值回写缓存。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.debug = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _row(self, sid: str = "1317") -> dict:
        data = json.loads(gcache.path(self.debug, "a1", "32").read_text(encoding="utf-8"))
        return next(r for r in data["rows"]
                    if str(r.get("platform_chapter_id")) == sid)

    def test_skip_write_corrects_stale_row(self):
        """1317 场景：缓存停在 not_started/未解锁，SKIP 回写后变 done/已解锁。"""
        # 11-04 的陈旧行（昨晚播完但 read_row 只读当前页 → 回写空转）
        gcache.update_row(self.debug, "a1", "32", "1317",
                          done_minutes=0, state="not_started", state_text="未学习",
                          name="4.6 人工智能重塑未来战场", required_minutes=19,
                          practice_locked=True, practice_ready=False)
        # driver SKIP 时的回写（find_section 刚读到的平台真值）
        gcache.update_row(self.debug, "a1", "32", "1317",
                          done_minutes=19, state="done", state_text="已完成",
                          practice_ready=True, practice_locked=False,
                          exam_id=14691, name="4.6 人工智能重塑未来战场",
                          required_minutes=19)
        row = self._row()
        self.assertEqual(row["state"], "done")
        self.assertFalse(row["practice_locked"])
        self.assertTrue(row["practice_ready"])
        self.assertEqual(str(row["exam_id"]), "14691")

    def test_skip_branch_in_run_gxks_writes_cache(self):
        """source 级守护：SKIP 分支必须调 update_row（零额外平台请求的回写）。"""
        src = Path(__file__).resolve().parents[1] / "scripts" / "run_gxks.py"
        text = src.read_text(encoding="utf-8")
        self.assertIn('section_status(row) == "done"', text)
        skip_block = text.split('section_status(row) == "done"', 1)[1][:2000]
        self.assertIn("gcache.update_row", skip_block)
        self.assertIn('state="done"', skip_block)


if __name__ == "__main__":
    unittest.main()
