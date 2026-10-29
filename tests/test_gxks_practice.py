"""合成答卷验证标签语义、题干/选项完整性与场次关联，零平台访问。"""

import json

import pytest

from xxt.adapters.gxks.practice import parse_capture


@pytest.fixture
def capture():
    return {
        "account_label": "jia",
        "questions_doc": {"exam_start_id": "run-1", "section_id": "exercise-1",
                          "section_name": "示例节", "paper_label": "练习甲",
                          "type_map": {"2": "单选题"}, "questions": [{"qid": "q-1"}]},
        "stage1": {"url": "http://example.test/ExamMain.aspx?Tk_Cl_Id=exercise-1&ExamStartId=run-1"
                          "&StartTime=2026/01/01%2012:00:00",
                   "frames": {"ExamDo": {"tm_arr": [["q-1", "2", "选择正确描述", "2.5", "2",
                                                     "<b>甲选项</b>;乙选项", "", "0", "k-1"]]}}},
        "stage2": {"exam_pages": [{"url": "http://example.test/ExamMessage.aspx?"
                                           "Tk_Cl_Id=exercise-1&Clerk_Kscj_Id=result-1"}]},
        "review_meta": {"final_url": "http://example.test/ViewExam.aspx?Clerk_Kscj_ID=result-1"},
        "review_html": """<div>考生姓名 甲同学 答卷时间 2026-01-01 12:00:00至12:01:00</div>
            <div>总得分 0 试卷总分 2.5</div>
            <input type="hidden" name="__VIEWSTATE" value="synthetic-state">
            <div id="ddTm_1"><dt>.1.选择正确描述<b>(2.5分)</b></dt>
            <dd><label for="tm_1_0">A、<b>甲选项</b></label><label for="tm_1_1">B、乙选项</label></dd>
            <dd><label><span class="tmda">考生答案</span><span class="red">A</span>
            <span class="tmda">标准答案</span><span class="green">B</span>
            <span class="tmda">考生得分</span><strong>0 分</strong></label></dd>
            <dd><label>试题解析<p>合成说明</p></label></dd></div>""",
    }


def test_official_answer_not_confused_with_selected(capture):
    got = parse_capture(**capture)
    q = got["questions"][0]
    assert q["answer"]["picked"]["keys"] == ["a"]
    assert q["answer"]["standard"]["keys"] == ["b"]
    assert q["answer"]["standard_source"] == "site"
    assert q["answer"]["is_correct"] is False
    assert q["answer"]["score_got"] == 0
    assert q["answer"]["explanation_html"] == "<p>合成说明</p>"
    assert q["parts"][0]["html"] == "<b>甲选项</b>"
    assert got["section_id"] == "" and got["exercise_id"] == "exercise-1"
    raw = json.dumps(got, ensure_ascii=False)
    assert "甲同学" not in raw and "__VIEWSTATE" not in raw and "synthetic-state" not in raw


@pytest.mark.parametrize("change,match", [
    ("count", "选项数"), ("order", "选项"), ("stem", "题干"), ("missing", "数量"),
    ("type", "尚未实测"), ("result", "不匹配"), ("attempt", "同一次"),
    ("score", "合计"), ("time", "开始时间"),
])
def test_incomplete_or_misaligned_capture_rejected(capture, change, match):
    row = capture["stage1"]["frames"]["ExamDo"]["tm_arr"][0]
    if change == "count":
        row[4] = "4"
    elif change == "order":
        row[5] = "乙选项;<b>甲选项</b>"
    elif change == "stem":
        row[2] = "不同的题干"
    elif change == "missing":
        capture["review_html"] = ""
    elif change == "type":
        row[1] = "3"
    elif change == "result":
        capture["review_meta"]["final_url"] = "http://example.test/ViewExam.aspx?Clerk_Kscj_ID=wrong"
    elif change == "attempt":
        capture["questions_doc"]["exam_start_id"] = "other"
    elif change == "score":
        capture["review_html"] = capture["review_html"].replace("总得分 0", "总得分 2")
    else:
        capture["review_html"] = capture["review_html"].replace("12:00:00", "11:00:00")
    with pytest.raises(ValueError, match=match):
        parse_capture(**capture)


def test_missing_standard_stays_unknown(capture):
    capture["review_html"] = capture["review_html"].replace(
        '<span class="tmda">标准答案</span><span class="green">B</span>', "")
    answer = parse_capture(**capture)["questions"][0]["answer"]
    assert answer["standard"] is None and answer["standard_source"] is None
    assert answer["is_correct"] is None


def test_green_selected_is_not_official_answer(capture):
    capture["review_html"] = capture["review_html"].replace('class="red"', 'class="green"')
    answer = parse_capture(**capture)["questions"][0]["answer"]
    assert answer["picked"]["keys"] == ["a"] and answer["standard"]["keys"] == ["b"]


def test_review_page_accepts_examid_alias(capture):
    """交卷后站点直接跳 `viewExam.aspx?ExamID=…&Clerk_kscj_ID=…`（不再带 `Tk_Cl_Id`）。

    2026-10-30 实测第 2 节：只认 `tk_cl_id` 会把真实答卷判成“不匹配”而拒收。
    """
    url = "http://example.test/viewExam.aspx?ExamID=exercise-1&Clerk_kscj_ID=result-1"
    capture["stage2"] = {"exam_pages": [{"url": url}]}
    capture["review_meta"] = {"final_url": url}
    assert parse_capture(**capture)["exercise_id"] == "exercise-1"


def test_unpadded_hour_matches_review_time(capture):
    """凌晨时段站点写 `0:00:00`、回顾页写 `00:00:00`，不能因此判成旧答卷误拒。"""
    capture["stage1"]["url"] = capture["stage1"]["url"].replace("12:00:00", "0:00:00")
    capture["review_html"] = capture["review_html"].replace("2026-01-01 12:00:00",
                                                            "2026-01-01 00:00:00")
    got = parse_capture(**capture)
    assert got["start_time"] == "2026/01/01 0:00:00"
    assert got["questions"][0]["answer"]["standard"]["keys"] == ["b"]


def test_answer_frame_located_by_structure_not_name(capture):
    """帧名属于模板细节：`ExamDo` 与 `ExamTmStepDo` 都必须能认出来。"""
    capture["stage1"]["frames"] = {
        "topFrame_ExamTmStepHeader": {"text": "2.01"},
        "mainFrame_ExamTmStepDo": capture["stage1"]["frames"]["ExamDo"],
    }
    got = parse_capture(**capture)
    assert got["questions"][0]["qid"] == "q-1"


def test_two_answer_frames_rejected(capture):
    """两个帧都有 `tm_arr` 时无法判断哪个是本次答卷，必须拒收。"""
    rows = capture["stage1"]["frames"]["ExamDo"]
    capture["stage1"]["frames"]["ExamView"] = rows
    with pytest.raises(ValueError, match="答题帧"):
        parse_capture(**capture)
