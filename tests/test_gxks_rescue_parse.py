"""`parse_review_standalone` —— 只凭判分页 HTML 解析（兜底补抓用）。

## 背景（2026-11-28 用户提）

用户发现「成绩分析 → 课后练习」里每次交卷都永久留一条记录，带
`Clerk_Kscj_Id`（判分页的钥匙）。所以交卷后即使当场抓判分页失败，
也能事后补。

但 `parse_review_online` 要求 `raw_rows`（答题帧的题行）做逐题核对，
事后补抓拿不到。于是新增 `parse_review_standalone`：**完全从判分页
DOM 推断**题干/选项/题型/答案。

## 它靠什么保证不出错（没有 raw_rows 可比）

  ① 每题都有 `dt` 与至少 2 个选项
  ② 逐题满分合计 == 试卷总分（判分页自带这两个数）
  ③ 答案取 `span.tmda` 的「标准答案」，来源仍是平台官方

题型推断规则：恰好 2 个选项且是「对/错」→ 判断题(4)；否则按单选(2)。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from xxt.adapters.gxks import practice

ROOT = Path(__file__).resolve().parents[1]
# 真实落盘的判分页（单选题 ×3，100 分）
SINGLE_CHOICE_HTML = ROOT / "data" / "debug" / "stage_2_1" / "exam_html" / "viewExam.html"


def _html() -> str:
    return SINGLE_CHOICE_HTML.read_text(encoding="utf-8", errors="replace")


def test_parses_real_single_choice_review():
    """真实判分页（单选题）要能解析出 3 题 / 100 分。"""
    url = ("http://www.gaoxiaokaoshi.com/ExamList/ExamPage/ViewExam.aspx"
           "?Clerk_Kscj_ID=22359501")
    b = practice.parse_review_standalone(
        account_label="甲同学", review_url=url, review_html=_html())
    assert len(b["questions"]) == 3
    assert b["got_score"] == 100.0
    assert b["total_score"] == 100.0
    assert b["exam_start_id"] == "22359501"
    for q in b["questions"]:
        assert q["type_code"] == "2"          # 单选题
        assert len(q["parts"]) >= 2
        std = q["answer"]["standard"]
        assert std is not None and std["keys"]
        assert q["answer"]["standard_source"] == "site"


def test_rejects_non_review_url():
    """不是判分页（缺 Clerk_Kscj_ID）要明确拒绝。"""
    with pytest.raises(ValueError):
        practice.parse_review_standalone(
            account_label="甲同学",
            review_url="http://x/ExamPage/ExamTmStepMain.aspx?Tk_Cl_Id=1",
            review_html=_html())


def test_rejects_empty_page():
    with pytest.raises(ValueError):
        practice.parse_review_standalone(
            account_label="甲同学",
            review_url="http://x/ViewExam.aspx?Clerk_Kscj_ID=1",
            review_html="<html><body>没有题目</body></html>")


def test_judge_type_inferred_from_pair():
    """两个选项且是「对/错」-> 判为判断题(type 4)，这是兜底路径的关键。

    判断题在判分页没有 `tmtx` 字段，只能按选项推。
    """
    html = """
    <html><body>
      <div id="ddTm_1">
        <dl>
          <dt>.1.国防是国家生存与发展的安全保障。<b>(33分)</b></dt>
          <dd>
            <label>1、错</label><label>2、对</label>
          </dd>
          <dd>
            <label><span class="tmda">考生答案</span>
              <span class="green">对</span>
              <span class="tmda">标准答案</span>
              <span class="green">对</span>
              <span class="tmda">考生得分</span><span>33 分</span></label>
          </dd>
        </dl>
      </div>
    </body></html>
    """
    b = practice.parse_review_standalone(
        account_label="甲同学",
        review_url="http://x/ViewExam.aspx?Clerk_Kscj_ID=9&ExamID=14684",
        review_html=html)
    q = b["questions"][0]
    assert q["type_code"] == "4"
    assert q["max_score"] == 33.0
    assert q["answer"]["standard"]["keys"] == ["1"]   # 对 = 1


def test_score_sum_mismatch_rejected():
    """逐题满分合计与试卷总分不符时必须拒收（防止漏题）。"""
    html = """
    <html><body>
      <div>总得分 33.0 试卷总分 100.0</div>
      <div id="ddTm_1">
        <dl>
          <dt>.1.题干一<b>(33分)</b></dt>
          <dd><label>A、甲</label><label>B、乙</label></dd>
          <dd><label><span class="tmda">标准答案</span><span>甲</span></label></dd>
        </dl>
      </div>
    </body></html>
    """
    with pytest.raises(ValueError, match="可能漏题"):
        practice.parse_review_standalone(
            account_label="甲同学",
            review_url="http://x/ViewExam.aspx?Clerk_Kscj_ID=9",
            review_html=html)
