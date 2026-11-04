"""节列表缓存的**命名规则**与按课程读取（B1，2026-10-30）。

为什么值得单测：文件名被三处拼接（脚本写、面板卡片读、面板 `/api/state` 读）。
散着拼，加课程维度时必然有一处漏改，表现为"脚本说写了、面板说没有"。
这里钉死五件事：

① 命名规则唯一：有课程 `sections_<代号>_<课程>.json`，无课程 `sections_<代号>.json`；
② **外部输入不许进路径**：课程 value 来自页面 DOM，含中文/斜杠/`..` 一律退回无课程名；
③ **老文件要能读到**：升级前所有缓存都不带课程，读的时候必须回落，不能"升级即空白"；
④ 读的时候先带课程、再老文件（优先级不能反，否则新数据被旧数据盖住）；
⑤ `list_all` 只认该账号自己的文件，别把别人的缓存算进来。
"""

from __future__ import annotations

import json

import pytest

from xxt.adapters.gxks import sections_cache as sc


# ---------------------------------------------------------------- 命名

def test_filename_with_and_without_course() -> None:
    assert sc.filename("default", "32") == "sections_default_32.json"
    assert sc.filename("default") == "sections_default.json"
    assert sc.filename("default", "") == "sections_default.json"
    assert sc.filename("default", None) == "sections_default.json"


def test_course_accepts_int_and_whitespace() -> None:
    assert sc.filename("jia", 32) == "sections_jia_32.json"
    assert sc.filename("jia", "  32  ") == "sections_jia_32.json"


@pytest.mark.parametrize("bad", [
    "军事理论",          # 中文：不在白名单
    "../../etc/passwd",  # 路径穿越
    "a/b",               # 路径分隔符
    "a\\b",              # Windows 分隔符
    "a b",               # 空格
    "a.b",               # 点（会被当扩展名）
    "x" * 33,            # 超长
])
def test_unsafe_course_never_enters_the_path(bad: str) -> None:
    """课程 value 来自页面 DOM，属外部输入：非法一律退回"无课程"的名字。"""
    assert sc.safe_course(bad) == ""
    assert sc.filename("default", bad) == "sections_default.json"


def test_path_helper(tmp_path) -> None:
    assert sc.path(tmp_path, "jia", "32") == tmp_path / "sections_jia_32.json"
    assert sc.path(tmp_path, "jia") == tmp_path / "sections_jia.json"
    assert sc.legacy_path(tmp_path, "jia") == tmp_path / "sections_jia.json"


# ---------------------------------------------------------------- 读取优先级

def test_read_paths_prefers_course_then_legacy(tmp_path) -> None:
    got = sc.read_paths(tmp_path, "jia", "32")
    assert [p.name for p in got] == ["sections_jia_32.json", "sections_jia.json"]


def test_read_paths_dedups_when_no_course(tmp_path) -> None:
    """没有课程时两个候选是同一个文件，不能重复读两遍。"""
    assert [p.name for p in sc.read_paths(tmp_path, "jia")] == ["sections_jia.json"]


def test_read_paths_unsafe_course_falls_back(tmp_path) -> None:
    got = sc.read_paths(tmp_path, "jia", "../evil")
    assert [p.name for p in got] == ["sections_jia.json"]


# ---------------------------------------------------------------- list_all

def _write(p, course: str = "") -> None:
    p.write_text(json.dumps({"ok": True, "course": course, "rows": []}), encoding="utf-8")


def test_list_all_covers_legacy_and_courses(tmp_path) -> None:
    _write(tmp_path / "sections_jia.json")
    _write(tmp_path / "sections_jia_32.json", "32")
    _write(tmp_path / "sections_jia_33.json", "33")
    names = {p.name for p in sc.list_all(tmp_path, "jia")}
    assert names == {"sections_jia.json", "sections_jia_32.json", "sections_jia_33.json"}


def test_list_all_does_not_leak_other_accounts(tmp_path) -> None:
    """`jia` 的 glob 不能把 `jia2` 的文件算进来（前缀陷阱）。"""
    _write(tmp_path / "sections_jia.json")
    _write(tmp_path / "sections_jia2.json")
    _write(tmp_path / "sections_jia2_32.json", "32")
    assert {p.name for p in sc.list_all(tmp_path, "jia")} == {"sections_jia.json"}


def test_list_all_empty_dir(tmp_path) -> None:
    assert sc.list_all(tmp_path / "nope", "jia") == []
    assert sc.list_all(tmp_path, "nobody") == []


def test_list_all_sorted_by_mtime_desc(tmp_path) -> None:
    import os
    import time

    old = tmp_path / "sections_jia.json"
    new = tmp_path / "sections_jia_32.json"
    _write(old)
    _write(new, "32")
    past = time.time() - 3600
    os.utime(old, (past, past))
    assert sc.list_all(tmp_path, "jia")[0].name == "sections_jia_32.json"


# ---------------------------------------------------------------- update_row 一致性

def _cache_with_row(tmp_path, **row) -> "object":
    """造一份只有一节的缓存，返回文件路径。"""
    p = tmp_path / "sections_jia.json"
    base = {
        "idx": 0, "name": "3.5 某节", "platform_chapter_id": "1308",
        "required_minutes": 20, "done_minutes": 20, "state": "done",
        "state_text": "已完成", "practice_locked": True, "exam_id": None,
        "practice_ready": False,
    }
    base.update(row)
    p.write_text(json.dumps({"ok": True, "label": "jia", "read_at": "2026-11-04 11:43:07",
                             "course": "", "rows": [base], "summary": {}}),
                 encoding="utf-8")
    return p


def test_update_row_derives_practice_ready_from_row(tmp_path) -> None:
    """**用户实拍的那个脏状态**（2026-11-05）：`locked=False exam_id=14691` 但 ready 还是 False。

    缓存里不该出现这种自相矛盾的行。回写方漏算 `practice_ready` 时（旧版
    `run_gxks.py` 用 `r.get("practice_ready")` → 恒为 None），
    `update_row` 必须按同一口径从行内信息推导，把 ready 纠正过来。
    """
    _cache_with_row(tmp_path)                       # 初始：锁着、ready=False
    ok = sc.update_row(tmp_path, "jia", "", "1308",
                       practice_locked=False, exam_id=14691,
                       practice_ready=None)         # 回写方没给 ready（旧版行为）
    assert ok is True
    row = json.loads((tmp_path / "sections_jia.json").read_text(encoding="utf-8"))["rows"][0]
    assert row["practice_locked"] is False
    assert row["exam_id"] == 14691
    assert row["practice_ready"] is True, "平台已解锁+有练习 ID，ready 必须跟着变 True"


def test_update_row_keeps_locked_row_not_ready(tmp_path) -> None:
    """反向不能过冲：仍锁着（或没有 exam_id）时，绝不能被推导成 ready=True。"""
    _cache_with_row(tmp_path, practice_ready=False)
    sc.update_row(tmp_path, "jia", "", "1308",
                  practice_locked=True, exam_id=None, practice_ready=None)
    row = json.loads((tmp_path / "sections_jia.json").read_text(encoding="utf-8"))["rows"][0]
    assert row["practice_ready"] is False


def test_update_row_practice_ready_change_marks_changed(tmp_path) -> None:
    """ready 变化必须算 `changed`，否则 `read_at` 不变 → 面板 `SEC_SIG` 不变 → 表格不重绘。"""
    p = _cache_with_row(tmp_path, practice_locked=False, exam_id=14691,
                        practice_ready=False)
    sc.update_row(tmp_path, "jia", "", "1308", practice_ready=True)
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["rows"][0]["practice_ready"] is True
    assert "本地回写" in data["read_at"]
