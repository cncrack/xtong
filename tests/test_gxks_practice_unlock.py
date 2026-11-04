"""练习解锁状态必须回写（2026-11-04 用户实拍「已完成但显示未解锁」）。

## 现象

1306「3.3 中国古代军事思想」在「已完成」区显示 **47/45 分 + 「未解锁」**，
而平台早已把它标为 `done / 已完成`。

## 两个独立的原因

### 原因 1：`update_row` 没写 `practice_locked` / `exam_id`

回读的行**本来就带**这两个字段（`listparse.py:162-163` 从「参加练习」的 onclick 解析：
`alert('请先完成学习！')` → 锁定；`showExam(<id>)` → 解锁 + 练习 ID），
但 `update_row` 只写 `practice_ready` → 缓存里的「未解锁」永远不消失，
而且 `exam_id` 一直是 None → 面板的「做练习」按钮永远不显示。

### 原因 2：平台是**异步**开放的

练习在达标后**不是立刻**解锁（onclick 要等平台后台把 `alert` 换成 `showExam`）。
而 `progress` 事件每 5 分钟才一次（`read_progress_every`），
那一刻拿到的可能还是锁定态 → 光靠周期回写会一直显示「未解锁」。

**修法**：达标收工后**趁已离开播放页**（`leave_player()` 之后）主动重读一次，
只针对当前这一节，代价约 8 秒。
"""

from __future__ import annotations

import inspect
import unittest
from pathlib import Path

from xxt.adapters.gxks import sections_cache


class TestUpdateRowWritesPracticeFields(unittest.TestCase):
    def test_signature_accepts_practice_locked_and_exam_id(self):
        sig = inspect.signature(sections_cache.update_row)
        self.assertIn("practice_locked", sig.parameters)
        self.assertIn("exam_id", sig.parameters)

    def test_body_writes_them(self):
        src = inspect.getsource(sections_cache.update_row)
        self.assertIn('r["practice_locked"] = bool(practice_locked)', src)
        self.assertIn('r["exam_id"] = exam_id', src)


class TestPracticeUnlockReread(unittest.TestCase):
    """达标后要主动重读一次（平台异步开放），但**必须放在全部刷完之后**。

    ## 我犯的错（2026-11-04）

    我原来在**每节收工后**就回读一次列表，想同步练理解锁状态。
    **但列表页与播放页抢同一个 iframe** → 重读把播放页冲掉 → 视频重建、
    `m_time` 归零 → 日志刷 `rebuild` / `paused`。

    实测 1307 在 11:00、11:04 各被冲了一次（学时仍上传，但进度被打断）。

    ⇒ 改成：攒进 `need_practice_check`，**全部刷完之后**统一重读一次。
    """

    def _run_src(self) -> str:
        return Path("scripts/run_gxks.py").read_text(encoding="utf-8")

    def test_collects_instead_of_reading_immediately(self):
        """刷课途中**只收集**，不重读。"""
        src = self._run_src()
        self.assertIn("need_practice_check.append", src)
        # 收集点后面不能紧跟 read_row
        seg = src.split("need_practice_check.append", 1)[1].split("if idx < args.max_sections", 1)[0]
        self.assertNotIn("read_row", seg,
                         "刷课途中不能重读列表（会冲掉播放页）")

    def test_reads_after_all_sections_done(self):
        """重读要放在**全部刷完之后**（汇总之后）。"""
        src = self._run_src()
        read_pos = src.find("回读练理解锁状态")
        self.assertGreater(read_pos, -1, "找不到收尾的重读逻辑")
        summary_pos = src.find("=== 本次汇总 ===")
        self.assertGreater(read_pos, summary_pos,
                           "重读必须放在汇总之后（那时已经没有播放要保护）")

    def test_reads_row_and_updates_cache(self):
        src = self._run_src()
        # ⚠ 用 `find_section`（逐页找）而不是 `read_row`（只读当前页）：
        # 实测 1306 在第 2 页、1307~1309 在第 3 页，`read_row` 会读不到跨页的节
        # （静默打印「读不到（跳过）」）。2026-11-05 复核发现。
        self.assertIn("drv.find_section(", src)
        self.assertNotIn("fresh = drv.read_row(sid)", src)
        self.assertIn('practice_locked=fresh.get("practice_locked")', src)
        self.assertIn('exam_id=fresh.get("exam_id")', src)

    def test_failure_does_not_break_playback(self):
        """重读失败不能影响刷课。"""
        src = self._run_src()
        self.assertIn("练理解锁重读失败", src)


if __name__ == "__main__":
    unittest.main()
