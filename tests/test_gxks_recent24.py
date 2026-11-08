"""「近 24 小时」口径（2026-11-03 用户：夜里连播跨零点，「今日 2 节」不对）。

## 决策

**显示**两个口径，**记账只按墙钟日**：

| 口径 | 用途 | 实测值（22:44 开跑学 3 节） |
|---|---|---|
| 今日（自然日，墙钟） | **额度判定**、剩余额度 | 2 节 / 68.3 分 |
| 近 24 小时（滑动窗口） | **展示**：这一趟学了几节 | 3 节 / 104.2 分 |

⚠ 记账**不能**改成「整趟记一天」：那是安全刹车，虚高额度 = 可能超刷 = 账号风险
（全记开始日 → 11-02 白天额度白给；全记结束日 → 11-02 学的 1 节记到 11-03，一节没扣）。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from xxt.adapters.gxks import ledger


class TestRecentTotals(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = Path(self.tmp.name) / "l.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, rows: list[dict]) -> None:
        self.p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows),
                          encoding="utf-8")

    def test_spans_midnight(self):
        """跨零点的两节：今日算 1 节，近 24h 算 2 节。

        ⚠ 显式传 `day=`，**不依赖「现在几点」**：`now - 3h` 在白天会落在同一天，
        断言就假了（2026-11-03 20:xx 实测翻车）。两个口径过滤的字段不同：
        `today_totals` 认 `date`、`recent_totals` 认 `at` —— 分开测才稳。
        """
        now = datetime.now()
        older = now - timedelta(hours=2)               # 确定在 24h 窗口内
        older_day = (now - timedelta(days=1)).date()   # 但归属"昨天"
        self._write([
            {"at": older.isoformat(timespec="seconds"),
             "date": older_day.isoformat(),
             "account": "default", "course": "", "section_id": "1301",
             "name": "a", "minutes": 35.9, "result": "done"},
            {"at": now.isoformat(timespec="seconds"), "date": now.date().isoformat(),
             "account": "default", "course": "", "section_id": "1302",
             "name": "b", "minutes": 35.8, "result": "done"},
        ])
        t = ledger.today_totals(self.p, "default", day=now.date().isoformat())
        r = ledger.recent_totals(self.p, "default", 24)
        self.assertEqual(r["sections"], 2, "近24h 按 `at` 过滤 → 两节都在")
        self.assertEqual(t["sections"], 1, "今日按 `date` 过滤 → 只有今天那节")

    def test_recent_excludes_older_than_window(self):
        old = datetime.now() - timedelta(hours=30)
        self._write([
            {"at": old.isoformat(timespec="seconds"), "date": old.date().isoformat(),
             "account": "default", "course": "", "section_id": "1301",
             "name": "a", "minutes": 35.9, "result": "done"},
        ])
        r = ledger.recent_totals(self.p, "default", 24)
        self.assertEqual(r["sections"], 0)

    def test_recent_never_less_than_today(self):
        """安全性：近24h ⊇ 今日 → 显示更保守，不会让人多刷。"""
        now = datetime.now()
        rows = []
        for i in range(3):
            at = now - timedelta(hours=i)
            rows.append({"at": at.isoformat(timespec="seconds"),
                         "date": at.date().isoformat(), "account": "default",
                         "course": "", "section_id": str(1300 + i), "name": "x",
                         "minutes": 30.0, "result": "done"})
        self._write(rows)
        t = ledger.today_totals(self.p, "default")
        r = ledger.recent_totals(self.p, "default", 24)
        self.assertGreaterEqual(r["sections"], t["sections"])

    def test_account_filter(self):
        now = datetime.now()
        self._write([
            {"at": now.isoformat(timespec="seconds"), "date": now.date().isoformat(),
             "account": "default", "course": "", "section_id": "1301",
             "name": "a", "minutes": 30.0, "result": "done"},
            {"at": now.isoformat(timespec="seconds"), "date": now.date().isoformat(),
             "account": "other", "course": "", "section_id": "1302",
             "name": "b", "minutes": 30.0, "result": "done"},
        ])
        r = ledger.recent_totals(self.p, "default", 24)
        self.assertEqual(r["sections"], 1)

    def test_quota_still_uses_today_only(self):
        """额度**必须**只按墙钟日（near24 不得参与判定）。"""
        import inspect
        src = inspect.getsource(ledger.quota_left)
        self.assertIn("today_totals(", src)
        self.assertNotIn("recent_totals(", src)


class TestRecent24InUI(unittest.TestCase):
    """面板要能看见「近24h」这个数。"""

    def test_card_exposes_recent24(self):
        from xxt.panel_gxks import app
        import inspect
        src = inspect.getsource(app._account_cards)
        self.assertIn("recent24", src)
        # T4（2026-11-08）：卡片改用 `batch_totals` 一次读全量账本按账号聚合，
        # 不再每账号各调 recent_totals（近24h 口径仍由 batch 内部同口径算出）。
        self.assertIn("batch_totals", src)

    def test_today_cell_shows_recent_badge(self):
        from xxt.panel_gxks.page import PAGE
        self.assertIn("c-r24", PAGE)
        self.assertIn("近24h", PAGE)

    def test_batch_bar_shows_recent_total(self):
        from xxt.panel_gxks.page import PAGE
        bar = PAGE.split("function renderBatchBar(", 1)[1].split("\n}", 1)[0]
        self.assertIn("近24h", bar)
