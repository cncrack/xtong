"""batch_totals（2026-11-08 T4）：账本一次读、按账号聚合。

为什么：面板 `_account_cards` 原来每账号各调 `today_totals` + `recent_totals`，
两者都各自 `read_rows()` 把整个 jsonl 读进内存逐行解析 —— O(账号数 × 行数)。
100 账号 × 每 4 秒轮询 = 每 4 秒 200 次全量读，面板会卡。
`batch_totals` 只读一次、单遍分桶，复杂度降到 O(行数)。

守护：batch 与单账号版**逐字段同口径**（今日认 `date`、近24h 认 `at`、
去重键 `course + section_id`、分钟数坏行跳过累计但节数照算）。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from xxt.adapters.gxks import ledger


class TestBatchTotals(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = Path(self.tmp.name) / "l.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, rows: list[dict]) -> None:
        self.p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows),
                          encoding="utf-8")

    def _row(self, account, sid, minutes=30.0, result="done", course="",
             hours_ago=0.0, day=None):
        at = datetime.now() - timedelta(hours=hours_ago)
        return {"at": at.isoformat(timespec="seconds"),
                "date": day or at.date().isoformat(),
                "account": account, "course": course, "section_id": sid,
                "name": "n", "minutes": minutes, "result": result}

    def test_batch_matches_single_account_calls(self):
        """batch 与逐账号调 today/recent **逐字段一致**（含跨日、窗口外、partial）。"""
        yesterday = (datetime.now() - timedelta(days=1)).date().isoformat()
        self._write([
            self._row("jia", "1301", 35.9, "done", ""),
            self._row("jia", "1302", 35.8, "done", "32", hours_ago=2),
            self._row("jia", "1301", 10.0, "partial", ""),
            self._row("jia", "1303", 5.0, "done", "", hours_ago=30, day=yesterday),
            self._row("yi", "1301", 20.0, "done", ""),
            self._row("yi", "1301", 20.0, "done", "32"),   # 同节不同课程 = 两节
            self._row("other", "9999", 1.0),               # 不在聚合列表里
        ])
        batch = ledger.batch_totals(self.p, ["jia", "yi", "bing"])
        for acc in ("jia", "yi"):
            self.assertEqual(batch[acc]["today"], ledger.today_totals(self.p, acc), acc)
            self.assertEqual(batch[acc]["recent"], ledger.recent_totals(self.p, acc, 24), acc)
        # 只聚合请求过的账号；没记录的账号给零值结构
        self.assertNotIn("other", batch)
        self.assertEqual(batch["bing"]["today"]["sections"], 0)
        self.assertEqual(batch["bing"]["today"]["minutes"], 0.0)
        self.assertEqual(batch["bing"]["recent"]["sections"], 0)

    def test_empty_and_missing_file(self):
        self._write([])
        b = ledger.batch_totals(self.p, ["jia"])
        self.assertEqual(b["jia"]["today"]["sections"], 0)
        self.assertEqual(b["jia"]["recent"]["sections"], 0)
        b2 = ledger.batch_totals(self.p.with_name("nope.jsonl"), ["jia"])
        self.assertEqual(b2["jia"]["today"]["sections"], 0)

    def test_bad_rows_skipped(self):
        """坏 JSON 行整行跳过；分钟数坏行：分钟跳过但节数照算（与 today_totals 同口径）。"""
        now = datetime.now()
        good = self._row("jia", "1301", 30.0)
        bad_minutes = {"at": now.isoformat(timespec="seconds"),
                       "date": now.date().isoformat(), "account": "jia",
                       "course": "", "section_id": "1302", "name": "n",
                       "minutes": "abc", "result": "done"}
        with self.p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(good, ensure_ascii=False) + "\n")
            f.write('{"at": broken json\n')
            f.write(json.dumps(bad_minutes, ensure_ascii=False) + "\n")
        b = ledger.batch_totals(self.p, ["jia"])
        self.assertEqual(b["jia"]["today"]["sections"], 2)
        self.assertEqual(b["jia"]["today"]["minutes"], 30.0)

    def test_dedup_key_includes_course(self):
        """去重键是 course + section_id：同节两课程算两节（防额度少记）。"""
        self._write([
            self._row("jia", "1301", 30.0, "done", ""),
            self._row("jia", "1301", 30.0, "done", "32"),
        ])
        b = ledger.batch_totals(self.p, ["jia"])
        self.assertEqual(b["jia"]["today"]["sections"], 2)
        self.assertEqual(b["jia"]["today"]["minutes"], 60.0)


if __name__ == "__main__":
    unittest.main()
