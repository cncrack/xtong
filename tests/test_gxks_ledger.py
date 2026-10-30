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


# ---------------------------------------------------------------- 额度口径（A1）
# 2026-10-30 定案：**额度按账号合计**，`course` 只做统计与去重，不参与额度判定。

def test_zero_limit_means_unlimited_not_exhausted(tmp_path) -> None:
    """`0` = 不限（PLAN-GXKS §11.6）。返回 `None` 而不是 0——返回 0 会被读成“用完”。

    这正是超星 `reached_locally()` 的老毛病（0/0 被读成“今天已学够”）。
    """
    p = tmp_path / "l.jsonl"
    ledger.record(p, account="jia", section_id="1", minutes=60)
    q = ledger.quota_left("jia", sections_limit=0, minutes_limit=0, path=p)
    assert q["left_sections"] is None
    assert q["left_minutes"] is None
    assert q["unlimited"] == {"sections": True, "minutes": True}
    assert q["exhausted"] is False
    assert q["sections"] == 1                     # 统计照常给


def test_one_side_unlimited_still_enforces_the_other(tmp_path) -> None:
    p = tmp_path / "l.jsonl"
    for i in range(3):
        ledger.record(p, account="jia", section_id=str(i), minutes=60)
    q = ledger.quota_left("jia", sections_limit=0, minutes_limit=120, path=p)
    assert q["left_sections"] is None             # 节数不限
    assert q["exhausted"] is True                 # 但时长已经超了
    q2 = ledger.quota_left("jia", sections_limit=3, minutes_limit=0, path=p)
    assert q2["left_minutes"] is None
    assert q2["exhausted"] is True                # 节数到顶


def test_course_field_defaults_empty_for_old_rows(tmp_path) -> None:
    """老账本没有 `course` 字段：读的时候按 `""` 处理，新旧混在一起也不能炸。"""
    p = tmp_path / "l.jsonl"
    p.write_text('{"at": "2026-10-30T09:00:00", "date": "2026-10-30", "account": "jia",'
                 ' "section_id": "1", "minutes": 45, "result": "done"}\n',
                 encoding="utf-8")
    t = ledger.today_totals(p, "jia", day="2026-10-30")     # 显式给日期，不依赖跑测试那天
    assert t["sections"] == 1
    assert list(t["courses"]) == [""]


def test_quota_is_account_wide_across_courses(tmp_path) -> None:
    """多门课共用一个额度池：两门课各学 2 节，账号合计就是 4 节。"""
    p = tmp_path / "l.jsonl"
    ledger.record(p, account="jia", section_id="1", minutes=45, course="32")
    ledger.record(p, account="jia", section_id="2", minutes=45, course="32")
    ledger.record(p, account="jia", section_id="1", minutes=45, course="33")   # 同 ID 不同课
    ledger.record(p, account="jia", section_id="2", minutes=45, course="33")
    t = ledger.today_totals(p, "jia")
    assert t["sections"] == 4                     # 额度按账号合计
    assert t["minutes"] == 180.0
    q = ledger.quota_left("jia", sections_limit=5, minutes_limit=600, path=p)
    assert q["left_sections"] == 1
    # 按课程拆分只作展示
    assert t["courses"]["32"]["sections"] == 2
    assert t["courses"]["33"]["sections"] == 2


def test_same_section_id_in_two_courses_counts_twice(tmp_path) -> None:
    """跨课程节 ID 撞车必须算两节：只按 section_id 去重会少记额度、账号反而超刷。"""
    p = tmp_path / "l.jsonl"
    ledger.record(p, account="jia", section_id="1289", minutes=45, course="32")
    ledger.record(p, account="jia", section_id="1289", minutes=45, course="33")
    assert ledger.today_totals(p, "jia")["sections"] == 2
    # 同一门课里重复跑同一节仍然只算一次
    ledger.record(p, account="jia", section_id="1289", minutes=45, course="32")
    assert ledger.today_totals(p, "jia")["sections"] == 2


def test_course_filter_only_narrows_statistics(tmp_path) -> None:
    p = tmp_path / "l.jsonl"
    ledger.record(p, account="jia", section_id="1", minutes=45, course="32")
    ledger.record(p, account="jia", section_id="2", minutes=30, course="33")
    only32 = ledger.today_totals(p, "jia", course="32")
    assert only32["sections"] == 1 and only32["minutes"] == 45.0
    assert ledger.today_totals(p, "jia")["sections"] == 2      # 缺省不过滤
