"""gxks 列表解析回归测试（T3 只读部分，2026-10-29）。

夹具全部是**合成 HTML**，不是真实页面转储：
- 结构照实测（表头 7 列 / `showframe('名称', ID)` / `alert('请先完成学习！')` / `PageSplit1` 分页），
- 但名称、ID、数字都是编的，账号信息一个都没有。
"""

from __future__ import annotations

import pytest

from xxt.adapters.gxks import listparse, selectors
from xxt.adapters.gxks.listparse import (
    is_practice_locked,
    parse_course_page,
    parse_course_rows,
    parse_exam_id,
    parse_pager,
    parse_section_id,
    parse_state,
    to_minutes,
)


HEADER = """
<tr class="title">
  <th>课程名称</th><th>所属分类</th><th>学时要求</th><th>已完成学时</th>
  <th>状态</th><th>开始学习</th><th>课后练习</th>
</tr>
"""

PAGER = """
<div class="page">
  <div class="fright"><li>1/3</li></div>
  <div>
    <a id="PageSplit1_BtnPrevious" class="btn disabled" disabled>上一页</a>
    <a id="PageSplit1_BtnNext" class="btn">下一页</a>
  </div>
</div>
"""


def page(rows_html: str = "", pager_html: str = PAGER) -> str:
    return f"""
<html><body>
<form id="form1">
<table class="table">
  <thead>{HEADER}</thead>
  <tbody>{rows_html}</tbody>
</table>
{pager_html}
</form>
</body></html>
"""


ROW_LOCKED = """
<tr>
  <td>1.1 轮廓概述</td>
  <td>甲课程</td>
  <td>45分钟</td>
  <td>0分钟</td>
  <td>未学习</td>
  <td><a href="javascript:;" onclick="showframe('1.1 轮廓概述',1001)">开始学习</a></td>
  <td><a href="javascript:;" onclick="alert('请先完成学习！');">参加练习</a></td>
</tr>
"""

ROW_LEARNING = """
<tr>
  <td>1.2 轮廓法规</td>
  <td>甲课程</td>
  <td>42分钟</td>
  <td>21分钟</td>
  <td>学习中</td>
  <td><a href="javascript:;" onclick="showframe('1.2 轮廓法规',1002)">开始学习</a></td>
  <td><a href="javascript:;" onclick="alert('请先完成学习！');">参加练习</a></td>
</tr>
"""

# 2026-10-29 1.1 实测形态：学时 46 ≥ 要求 45 → 状态「已完成」、练习解锁成 showExam(练习ID)
ROW_DONE = """
<tr>
  <td>1.3 轮廓概述</td>
  <td>甲课程</td>
  <td>45分钟</td>
  <td>46分钟</td>
  <td>已完成</td>
  <td><a href="javascript:;" onclick="showframe('1.3 轮廓概述',1003)">开始学习</a></td>
  <td><a href="javascript:;" onclick="showExam(9001)">参加练习</a></td>
</tr>
"""


def test_to_minutes_basic():
    assert to_minutes("45分钟") == 45
    assert to_minutes("0分钟") == 0
    assert to_minutes(" 126 分钟 ") == 126
    assert to_minutes("") is None
    assert to_minutes(None) is None
    assert to_minutes("未设置") is None


def test_parse_section_id_from_onclick():
    assert parse_section_id("showframe('1.1 轮廓概述',1001)") == 1001
    assert parse_section_id('showframe("1.1 轮廓概述", 1002)') == 1002
    # 取不到就返回 None，绝不退回用名称当主键（名称会改）
    assert parse_section_id("alert('请先完成学习！');") is None
    assert parse_section_id("") is None
    assert parse_section_id(None) is None


def test_is_practice_locked_reads_onclick_without_clicking():
    assert is_practice_locked("alert('请先完成学习！');") is True
    assert is_practice_locked(selectors.PRACTICE_LOCK_HINT) is True
    assert is_practice_locked("goPractice(1002)") is False
    # 没有 onclick 时按“锁着”处理，宁可多等不硬闯
    assert is_practice_locked(None) is True


def test_parse_exam_id_only_when_unlocked():
    """达标后练习入口变成 showExam(15263)；锁着时是 alert，必须返回 None（不拿节 ID 凑）。"""
    assert parse_exam_id("showExam(15263)") == 15263
    assert parse_exam_id("showExam( 7 )") == 7
    assert parse_exam_id("alert('请先完成学习！');") is None
    assert parse_exam_id("") is None
    assert parse_exam_id(None) is None


def test_done_row_unlocks_practice_and_carries_exam_id():
    rows = parse_course_rows(page(ROW_DONE))
    assert len(rows) == 1
    row = rows[0]
    assert row["state"] == "done"
    assert row["done_on_platform"] is True
    assert row["done_minutes"] >= row["required_minutes"]
    assert row["practice_locked"] is False
    assert row["exam_id"] == 9001


def test_locked_row_has_no_exam_id():
    row = parse_course_rows(page(ROW_LOCKED))[0]
    assert row["practice_locked"] is True
    assert row["exam_id"] is None


def test_parse_state_unknown_stays_unknown():
    assert parse_state("未学习") == "not_started"
    assert parse_state("学习中") == "learning"
    assert parse_state("已完成") == "done"
    # 未见过的文案不能猜成“没学完”，否则会无限重试
    assert parse_state("待批阅") == "unknown"
    assert parse_state("") == "unknown"


def test_parse_state_tolerates_inner_whitespace():
    """`get_text(" ")` 会把子节点用空格拼起来（如 `<span>已完</span><span>成</span>`）。

    不归一空白就会变成 unknown → 永远等不到达标；在解析层堵掉这类“改版就死”的坑。
    """
    assert parse_state("已完 成") == "done"
    assert parse_state("学习中\n\t") == "learning"
    assert parse_state("  未 学 习 ") == "not_started"


def test_header_row_using_td_is_skipped():
    """真实页面表头用的是 `<td>`（Q9 踩到）：按 `th` 判会把表头当成一条节。"""
    header_td = (
        "<tr><td>课程名称</td><td>所属分类</td><td>学时要求</td><td>已完成学时</td>"
        "<td>状态</td><td>开始学习</td><td>课后练习</td></tr>"
    )
    rows = parse_course_rows(page(header_td + ROW_LOCKED))
    assert len(rows) == 1
    assert rows[0]["platform_chapter_id"] == "1001"
    assert rows[0]["idx"] == 0


def test_parse_course_rows_locked_and_learning():
    rows = parse_course_rows(page(ROW_LOCKED + ROW_LEARNING))
    assert len(rows) == 2

    first = rows[0]
    assert first["platform_chapter_id"] == "1001"
    assert first["name"] == "1.1 轮廓概述"
    assert first["idx"] == 0
    assert first["required_minutes"] == 45
    assert first["done_minutes"] == 0
    assert first["state"] == "not_started"
    assert first["practice_locked"] is True
    assert first["done_on_platform"] is False

    second = rows[1]
    assert second["platform_chapter_id"] == "1002"
    assert second["idx"] == 1
    assert second["required_minutes"] == 42
    assert second["done_minutes"] == 21
    assert second["state"] == "learning"


def test_done_not_inferred_from_minutes():
    """实测出现过「42/42 仍为学习中」，所以达标不能用分钟数推断。"""
    row = """
<tr>
  <td>1.2 轮廓法规</td>
  <td>甲课程</td>
  <td>42分钟</td>
  <td>42分钟</td>
  <td>学习中</td>
  <td><a href="javascript:;" onclick="showframe('1.2 轮廓法规',1003)">开始学习</a></td>
  <td><a href="javascript:;" onclick="alert('请先完成学习！');">参加练习</a></td>
</tr>
"""
    rows = parse_course_rows(page(row))
    assert rows and rows[0]["done_minutes"] == rows[0]["required_minutes"] == 42
    assert rows[0]["state"] == "learning"
    assert rows[0]["done_on_platform"] is False


def test_parse_pager_and_next_flag():
    pager = parse_pager(page(ROW_LOCKED))
    assert pager["current"] == 1
    assert pager["total"] == 3
    assert pager["has_next"] is True
    assert pager["has_prev"] is False           # disabled + class=disabled


def test_parse_pager_last_page_disables_next():
    last = PAGER.replace("<li>1/3</li>", "<li>3/3</li>").replace(
        'id="PageSplit1_BtnNext" class="btn"', 'id="PageSplit1_BtnNext" class="btn disabled"'
    )
    pager = parse_pager(page(ROW_LOCKED, last))
    assert pager["current"] == 3
    assert pager["has_next"] is False


def test_parse_course_page_shape():
    out = parse_course_page(page(ROW_LOCKED))
    assert set(out) == {"rows", "pager"}
    assert len(out["rows"]) == 1
    assert out["pager"]["total"] == 3


def test_parse_tolerates_empty_and_broken_html():
    assert parse_course_rows("") == []
    assert parse_course_rows("<html><body>无表格</body></html>") == []
    assert parse_pager("") == {
        "current": None,
        "total": None,
        "has_next": None,
        "has_prev": None,
    }


def test_table_falls_back_when_class_changed():
    """改版把 class 换掉时，退取“行最多的表”，不至于整页读不出。"""
    html = page(ROW_LOCKED).replace('class="table"', 'class="grid"')
    rows = parse_course_rows(html)
    assert len(rows) == 1
    assert rows[0]["platform_chapter_id"] == "1001"


def test_adapter_exposes_parser_without_network():
    from types import SimpleNamespace

    from xxt.adapters import get_adapter

    adapter = get_adapter(SimpleNamespace(platform={"name": "gxks"}))
    out = adapter.parse_course_page(page(ROW_LOCKED))
    assert out["rows"][0]["platform_chapter_id"] == "1001"
    # 驱动部分仍必须是“明确未实现”，不能悄悄返回空列表
    with pytest.raises(NotImplementedError):
        adapter.fetch_courses(None)
    with pytest.raises(NotImplementedError):
        adapter.fetch_chapters({}, None)


def test_listparse_is_pure():
    """解析层不得自己开浏览器/发请求：只能依赖 bs4 与 selectors。"""
    import inspect

    src = inspect.getsource(listparse)
    for banned in ("playwright", "httpx", "subprocess", "socket"):
        assert banned not in src, f"listparse 不应依赖 {banned}"
