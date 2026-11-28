"""自动检测判分页（2026-11-28 用户要求）。

## 要解决什么

用户实拍：「我忘记直接点交卷了，没有点确认交卷并入库」。
两道人工动作（浏览器交卷 + 面板确认）容易漏一个，漏了就白做。

所以脚本在等待期间**同时**用 `chrome.page_urls()`（纯 HTTP 只读，
不建 CDP 连接 → 不会吞掉交卷 confirm，Q31）检测判分页是否出现。

## 最大的坑：同节的**旧**判分页

判分页 URL 只有 `ExamID` + `Clerk_kscj_ID`，**没有 exam_start_id**。
只按 `ExamID` 认，会把「上一次做同一节留下的判分页」当成新的，
直接把旧答卷当新数据入库。

所以用「基线排除」：等待开始前已经存在的 `Clerk_kscj_ID` 记进基线，
检测时跳过它们。
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
WORK = (ROOT / "scripts" / "gxks_work.py").read_text(encoding="utf-8")

BASE = "http://x/ExamList/ExamPage/viewExam.aspx"


def find_review(urls: list[str], exercise_id: str, known: set[str]) -> str:
    """复刻 `gxks_work.py::_find_review` 的判定（纯函数，离线可测）。"""
    for u in urls:
        if "viewexam.aspx" not in u.lower():
            continue
        q = parse_qs(urlparse(u).query)
        eid = (q.get("ExamID") or q.get("examid") or [""])[0]
        cid = (q.get("Clerk_kscj_ID") or q.get("clerk_kscj_id") or [""])[0]
        if str(eid) != str(exercise_id):
            continue
        if cid and cid in known:
            continue
        return u
    return ""


def test_source_has_auto_detect():
    """代码里要真有这条自动检测路径。"""
    assert "_find_review" in WORK
    assert "baseline_clerk" in WORK
    assert "自动入库，不用点面板" in WORK


def test_ignores_old_review_page():
    """同一节的历史判分页（基线里）不能被当成本次的。"""
    urls = [f"{BASE}?ExamID=15264&Clerk_kscj_ID=99999"]
    assert find_review(urls, "15264", {"99999"}) == ""


def test_detects_new_review_page():
    urls = [f"{BASE}?ExamID=15264&Clerk_kscj_ID=88888"]
    assert "88888" in find_review(urls, "15264", {"99999"})


def test_ignores_other_exercise():
    urls = [f"{BASE}?ExamID=14684&Clerk_kscj_ID=77777"]
    assert find_review(urls, "15264", set()) == ""


def test_ignores_non_review_page():
    assert find_review(["http://x/Homew/MainPage.aspx?menu=3"], "15264", set()) == ""


def test_mixed_picks_the_new_one():
    """旧的和新的同时存在时，必须挑新的。"""
    urls = [f"{BASE}?ExamID=15264&Clerk_kscj_ID=99999",
            f"{BASE}?ExamID=15264&Clerk_kscj_ID=88888"]
    got = find_review(urls, "15264", {"99999"})
    assert "88888" in got and "99999" not in got
