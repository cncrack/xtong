"""运行账本：日额度、去重、坏行容错。"""

from __future__ import annotations

from datetime import datetime

from xxt.adapters.gxks import ledger


def test_record_and_today_totals(tmp_path) -> None:
    p = tmp_path / "l.jsonl"
    ledger.record(p, account="jia", section_id="1", minutes=60, name="1.1")
    ledger.record(p, account="jia", section_id="2", minutes=60, name="1.2")
    t = ledger.today_totals(p, "jia")
    assert t["sections"] == 2
    assert t["minutes"] == 120.0
    assert t["section_ids"] == ["1", "2"]


def test_same_section_counted_once(tmp_path) -> None:
    p = tmp_path / "l.jsonl"
    for _ in range(3):
        ledger.record(p, account="jia", section_id="1", minutes=62)
    t = ledger.today_totals(p, "jia")
    assert t["sections"] == 1           # 去重
    assert t["minutes"] == 186.0        # 分钟不重复扣减口径，按实际累加


def test_accounts_are_isolated(tmp_path) -> None:
    p = tmp_path / "l.jsonl"
    ledger.record(p, account="jia", section_id="1", minutes=60)
    ledger.record(p, account="yi", section_id="1", minutes=60)
    assert ledger.today_totals(p, "jia")["minutes"] == 60.0
    assert ledger.today_totals(p, "yi")["minutes"] == 60.0


def test_other_day_ignored(tmp_path) -> None:
    p = tmp_path / "l.jsonl"
    ledger.record(p, account="jia", section_id="1", minutes=60,
                  at=datetime(2026, 10, 1, 9, 0, 0))
    assert ledger.today_totals(p, "jia", day="2026-10-29")["sections"] == 0


def test_broken_lines_skipped(tmp_path) -> None:
    p = tmp_path / "l.jsonl"
    ledger.record(p, account="jia", section_id="1", minutes=60)
    with p.open("a", encoding="utf-8") as f:
        f.write("{not json\n")
        f.write('{"account": "jia"}\n')          # 缺 date
    assert ledger.today_totals(p, "jia")["sections"] == 1


def test_quota_left_exhausts(tmp_path) -> None:
    p = tmp_path / "l.jsonl"
    for i in range(3):
        ledger.record(p, account="jia", section_id=str(i), minutes=60)
    q = ledger.quota_left("jia", sections_limit=3, minutes_limit=600, path=p)
    assert q["left_sections"] == 0 and q["exhausted"] is True
    assert q["left_minutes"] == 420.0
    q2 = ledger.quota_left("jia", sections_limit=10, minutes_limit=100, path=p)
    assert q2["exhausted"] is True        # 分钟先耗尽
    assert q2["left_minutes"] == 0.0


def test_missing_file_is_empty(tmp_path) -> None:
    q = ledger.quota_left("jia", sections_limit=5, minutes_limit=300,
                          path=tmp_path / "nope.jsonl")
    assert q["sections"] == 0 and q["left_sections"] == 5 and q["exhausted"] is False
