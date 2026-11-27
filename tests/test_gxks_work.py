"""在线作答编排（`adapters/gxks/work.py`）的离线测试。

样本全部来自 2026-10-30 抓到的真实练习页结构（`ExamDo.html` 的 `tm_arr` / `tx_arr`），
但**已替换为合成题干**，不含真实账号、课程与作答内容。
"""

from __future__ import annotations

import pytest

from xxt.adapters.gxks import work

# tx_arr 原样结构：每项 [代码, ?, 名称, ?, 序号]
TX_ROWS = [
    ["2", "1", "单选题", "", "1"],
    ["3", "2", "多选题", "", "2"],
    ["4", "2", "判断题", "", "3"],
    ["5", "6", "简答题", "", "4"],
    ["77", "2", "不定项选择题", "", "2"],
]

# tm_arr 原样结构：14 项，[0]题ID [1]题型 [2]题干 [3]分值 [4]选项数 [5]选项(分号) [8]知识点
RAW_ROWS = [
    ["900001", "2", "___是甲概念的定义。", "33", "4",
     "甲说法;《乙法》;《丙法》;《丁法》", "", "0", "1400001", "", "z", "0", "", "1"],
    ["900002", "2", "___属于乙范畴。", "34", "4",
     "乙主体;乙目的;乙对象;乙手段", "", "0", "1400002", "", "z", "0", "", "1"],
    ["900003", "2", "___是丙活动的实行者。", "33", "4",
     "丙主体;丙目的;丙对象;丙手段", "", "0", "1400003", "", "z", "0", "", "1"],
]


# ------------------------------------------------------------------ 题型表

def test_type_map_comes_from_page():
    """题型名称由页面自带（13 种全在），我们不必维护词表。"""
    got = work.parse_tx_arr(TX_ROWS)
    assert got["2"] == "单选题"
    assert got["77"] == "不定项选择题"
    assert len(got) == 5


def test_type_map_tolerates_missing_rows():
    assert work.parse_tx_arr(None) == {}
    assert work.parse_tx_arr([["2"]]) == {}          # 太短的行直接忽略，不炸


# ------------------------------------------------------------------ 读题

def test_parse_tm_rows_reads_all_fields():
    questions = work.parse_tm_rows(RAW_ROWS, {"2": "单选题"})
    assert len(questions) == 3
    first = questions[0]
    assert first["ordinal"] == 1
    assert first["qid"] == "900001"
    assert first["type_code"] == "2"
    assert first["type_name"] == "单选题"
    assert first["stem"] == "___是甲概念的定义。"
    assert first["max_score"] == 33.0
    assert first["options"] == ["甲说法", "《乙法》", "《丙法》", "《丁法》"]
    assert first["option_keys"] == ["a", "b", "c", "d"]


def test_parse_tm_rows_rejects_short_row():
    """字段不齐就停：少一列填下去等于乱答。"""
    with pytest.raises(work.WorkError, match="字段不完整"):
        work.parse_tm_rows([["900001", "2", "题干"]], {})


def test_parse_tm_rows_rejects_option_count_mismatch():
    """平台声明 4 个选项，分号只拆出 3 个——选项文本本身不能带分号之外的坑。"""
    bad = list(RAW_ROWS[0])
    bad[5] = "只有;三个选项"
    with pytest.raises(work.WorkError, match="选项数不符"):
        work.parse_tm_rows([bad], {})


def test_parse_tm_rows_rejects_empty():
    with pytest.raises(work.WorkError, match="没有题目"):
        work.parse_tm_rows([], {})


def test_parse_tm_rows_unknown_type_name_falls_back():
    questions = work.parse_tm_rows(RAW_ROWS[:1], {})
    assert questions[0]["type_name"] == "未知题型"


# ------------------------------------------------------------------ 模型答案 → 选项 key

OPTIONS = ["甲说法", "《乙法》", "《丙法》", "《丁法》"]


@pytest.mark.parametrize("raw,expect", [
    (["B"], ["b"]),                 # 纯字母
    (["b"], ["b"]),
    (["C、选项文字"], ["c"]),        # 字母 + 顿号 + 文本
    (["C. 选项文字"], ["c"]),
    (["《丙法》"], ["c"]),           # 纯选项文本
])
def test_keys_from_answer_shapes(raw, expect):
    got = work.keys_from_answer(raw, OPTIONS[:1] + ["《乙法》", "《丙法》", "《丁法》"])
    assert got == expect


def test_keys_from_answer_prefers_text_over_letter():
    """字母和文本都给时按文本定位：平台若调过选项顺序，字母会撒谎、文本不会。"""
    got = work.keys_from_answer(["A、《丁法》"], OPTIONS)
    assert got == ["d"]


def test_keys_from_answer_rejects_out_of_range_letter():
    with pytest.raises(work.WorkError, match="不存在的选项"):
        work.keys_from_answer(["F"], OPTIONS)


def test_keys_from_answer_rejects_unmappable_text():
    with pytest.raises(work.WorkError, match="无法映射"):
        work.keys_from_answer(["以上都不对"], OPTIONS)


def test_keys_from_answer_rejects_empty():
    with pytest.raises(work.WorkError, match="没有给出答案"):
        work.keys_from_answer([], OPTIONS)


# ------------------------------------------------------------------ 出答案编排

def _questions():
    return work.parse_tm_rows(RAW_ROWS, {"2": "单选题"})


def test_plan_prefers_bank():
    seen = []

    def bank(stem, options):
        seen.append(stem)
        return {"keys": ["c"], "confidence": 1.0} if "甲概念" in stem else None

    plan = work.plan_answers(_questions(), allowed_types={"2"}, bank=bank,
                             ask_llm=lambda q: {"keys": ["a"], "confidence": 0.5})
    assert seen[0] == "___是甲概念的定义。"
    assert [p["source"] for p in plan] == ["bank", "ai", "ai"]
    assert plan[0]["keys"] == ["c"]
    assert plan[1]["confidence"] == 0.5


def test_plan_rejects_unmeasured_type_before_answering():
    """题型没实测就整卷停，不能"跳过这题继续填"——跳过会留下空题。"""
    questions = _questions()
    questions[1]["type_code"] = "3"
    questions[1]["type_name"] = "多选题"
    with pytest.raises(work.WorkError, match="多选题"):
        work.plan_answers(questions, allowed_types={"2"}, bank=lambda s, o: {"keys": ["a"]})


def test_plan_without_llm_reports_clearly():
    with pytest.raises(work.WorkError, match="未配置 AI"):
        work.plan_answers(_questions(), allowed_types={"2"},
                          bank=lambda s, o: None, ask_llm=None)


def test_plan_reason_is_truncated():
    plan = work.plan_answers(_questions()[:1], allowed_types={"2"},
                             ask_llm=lambda q: {"keys": ["a"], "confidence": 0.9,
                                                "reason": "很" * 200})
    assert len(plan[0]["reason"]) <= 60


# ------------------------------------------------------------------ 回填与回读（假页面）

class _FakeLocator:
    def __init__(self, frame, selector):
        self.frame = frame
        self.selector = selector

    def count(self):
        return 1

    @property
    def first(self):
        return self

    def click(self, timeout=0):
        self.frame.clicked.append(self.selector)


class _FakeFrame:
    """只模拟 work.py 用到的那两个方法，不引入 playwright。"""

    def __init__(self, checked=None):
        self.clicked = []
        self.checked = checked or {}

    def locator(self, selector):
        return _FakeLocator(self, selector)

    def evaluate(self, script, arg=None):
        return {str(k): v for k, v in self.checked.items()}


def test_fill_answers_clicks_real_radio():
    frame = _FakeFrame()
    work.fill_answers(frame, {1: ["b"], 2: ["d"]})
    assert frame.clicked == ['input[name="tmda_1"][value="b"]',
                             'input[name="tmda_2"][value="d"]']


def test_fill_answers_accepts_uppercase_key():
    frame = _FakeFrame()
    work.fill_answers(frame, {3: ["A"]})
    assert frame.clicked == ['input[name="tmda_3"][value="a"]']


def test_read_checked_parses_ordinals():
    frame = _FakeFrame(checked={1: "b", 3: "a"})
    assert work.read_checked(frame) == {1: "b", 3: "a"}


# ---------------------------------------------------------------- 判断题（type_code=4）
#
# 2026-11-27 用户报「1.6 是判断题，又不行了」。实测真实 DOM 后确认：
#   <label for="tm_1_1"><input type=radio name=tmda_1 value="0">1、错</label>
#   <label for="tm_1_0"><input type=radio name=tmda_1 value="1">2、对</label>
# 「1、」「2、」是序号前缀，**value=0 是错、value=1 是对**。
# 且判断题 radio **没有 ExamRadio class**，原来的 read_checked 读不到。

_JUDGE_ROW = ["9385823", "4", "国防是国家生存与发展的安全保障。", "33", "1",
              "", "", "0", "1475172", "", "z", "0", "", "1"]


def test_judge_options_are_builtin():
    """判断题的选项是平台内置的对/错，不来自 tm_arr（那格是空串）。"""
    qs = work.parse_tm_rows([_JUDGE_ROW], {"4": "判断题"})
    q = qs[0]
    assert q["type_code"] == "4"
    assert q["options"] == ["对", "错"]
    assert q["option_keys"] == ["1", "0"]     # 对=1、错=0（实测）


def test_judge_does_not_fail_on_empty_option_string():
    """回归：原来会因为 `declared(1) != 拆出(1)` 之类误判而抛错。"""
    qs = work.parse_tm_rows([_JUDGE_ROW], {"4": "判断题"})
    assert len(qs) == 1


def test_judge_normalize_text_answers():
    """模型答「对/错/正确/错误」都要能映射成 1/0。"""
    q = work.parse_tm_rows([_JUDGE_ROW], {"4": "判断题"})[0]
    assert work.normalize_keys(q, ["对"]) == ["1"]
    assert work.normalize_keys(q, ["错"]) == ["0"]
    assert work.normalize_keys(q, ["正确"]) == ["1"]
    assert work.normalize_keys(q, ["错误"]) == ["0"]


def test_judge_normalize_letter_answers():
    """模型答 A/B（按选项顺序）也要映射对。"""
    q = work.parse_tm_rows([_JUDGE_ROW], {"4": "判断题"})[0]
    assert work.normalize_keys(q, ["a"]) == ["1"]    # 第1个选项=对
    assert work.normalize_keys(q, ["b"]) == ["0"]    # 第2个选项=错


def test_judge_binary_values_preserved():
    """已经是 0/1 的原样保留（不要被小写化或字母化）。"""
    q = work.parse_tm_rows([_JUDGE_ROW], {"4": "判断题"})[0]
    assert work.normalize_keys(q, ["1"]) == ["1"]
    assert work.normalize_keys(q, ["0"]) == ["0"]


def test_supported_types_includes_judge():
    assert "2" in work.supported_types()
    assert "4" in work.supported_types()


def test_unknown_type_still_rejected():
    """未登记题型仍要明确报错（不能因为加了判断题就放行所有）。"""
    row = list(_JUDGE_ROW)
    row[1] = "1"          # 填空题，尚未实测
    qs = work.parse_tm_rows([row], {"1": "填空题"})
    assert qs[0]["type_code"] == "1"
    # plan_answers 要拦住它
    try:
        work.plan_answers(qs, allowed_types={"2", "4"}, bank=None, ask_llm=None)
    except work.WorkError as exc:
        assert "尚未实测" in str(exc)
    else:
        raise AssertionError("未登记题型应被拒绝")


# ---------------------------------------------------------------- 交卷入口（2026-11-27）
#
# 用户点交卷，平台弹「试卷保存失败，请稍后再保存」。
# 实测各帧：SubmitExam 在 Header 帧，PackAnswer/frmExam 在 Do 帧。
# 交卷按钮的 onclick 是 SubmitExam()，它内部做了一串必要前置：
#   ForbitOperate=1 -> stopclock() -> SaveMode=1 -> 写 LeftTime
#   -> PackAnswer() -> frmExam.submit()
# 原来直接调 frmExam.submit() 跳过这些 -> 服务端状态不完整 -> 保存失败。


class _FakePage:
    """假 page：frames 里放几个假帧。"""

    def __init__(self, frames):
        self.frames = frames


class _FakeJsFrame:
    def __init__(self, values, calls):
        self._values = values          # {"SubmitExam": "function", ...}
        self._calls = calls

    def evaluate(self, script):
        if "typeof" in script:
            for k, v in self._values.items():
                if k in script:
                    return v
            return "undefined"
        # 调用语句，记下来
        self._calls.append(script)
        return None


def test_submit_prefers_SubmitExam():
    """有 SubmitExam 时必须调它（走完整前置），不能直接 frmExam.submit()。"""
    calls = []
    header = _FakeJsFrame({"SubmitExam": "function"}, calls)
    do_frame = _FakeJsFrame({"frmExam": "object", "PackAnswer": "function"}, calls)
    work.submit(_FakePage([header, do_frame]))
    assert any("SubmitExam()" in c for c in calls), calls
    assert not any("frmExam.submit" in c for c in calls), calls


def test_submit_falls_back_to_frmExam():
    """没有 SubmitExam 的普通练习才用 frmExam.submit() 兜底。"""
    calls = []
    do_frame = _FakeJsFrame({"frmExam": "object"}, calls)
    work.submit(_FakePage([do_frame]))
    assert any("frmExam.submit" in c for c in calls), calls


def test_submit_raises_when_no_entry():
    """两个入口都没有 → 明确报错，不静默。"""
    calls = []
    empty = _FakeJsFrame({}, calls)
    try:
        work.submit(_FakePage([empty]))
    except work.WorkError as exc:
        assert "交卷入口" in str(exc)
    else:
        raise AssertionError("没有交卷入口时应报错")


# ---------------------------------------------------------------- 答案文本（问号 bug）

def test_answer_text_judge_values():
    """回归：判断题 value 是 0/1，原来按字母算会越界显示「?」。"""
    import importlib.util
    from pathlib import Path as _P
    spec = importlib.util.spec_from_file_location(
        "_gw", _P(__file__).resolve().parents[1] / "scripts" / "gxks_work.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    q = {"options": ["对", "错"], "option_keys": ["1", "0"]}
    assert m._answer_text(q, ["1"]) == "A、对"
    assert m._answer_text(q, ["0"]) == "B、错"


def test_answer_text_single_choice_still_works():
    import importlib.util
    from pathlib import Path as _P
    spec = importlib.util.spec_from_file_location(
        "_gw2", _P(__file__).resolve().parents[1] / "scripts" / "gxks_work.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    q = {"options": ["甲", "乙", "丙"], "option_keys": ["a", "b", "c"]}
    assert m._answer_text(q, ["b"]) == "B、乙"
