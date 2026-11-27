"""离线回收已获授权的课后练习快照；不联网、不执行页面脚本、不代交卷。

首版只接已实测的单选答卷。存储支持任意题型/部件，其他解析器待真实样本验证。
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup, Tag

_SHOWFRAME_RE = re.compile(r"showframe\(\s*'([^']*)'\s*,\s*(\d+)\s*\)")
_SHOWEXAM_RE = re.compile(r"showExam\((\d+)\)")


def section_index(course_doc: dict | None) -> dict[str, dict]:
    """从课程列表页快照按**练习 ID** 反查节 ID / 节名 / 课程名。

    为什么要它：练习页 URL 只有 `Tk_Cl_Id`（练习 ID），**没有节 ID**；而归类导出需要节 ID。
    列表行结构（实测 2026-10-29）：

        [节名, 课程分类, 学时要求, 已完成学时, 状态,
         {links: showframe('节名', 节ID)}, {links: showExam(练习ID)}]

    找不到就是 `{}`（列表快照可能只含当前页，不为缺项报错）。
    """
    out: dict[str, dict] = {}
    course = (course_doc or {}).get("course") or {}
    for table in course.get("tables") or []:
        for row in table.get("rows") or []:
            if not isinstance(row, list) or len(row) < 2:
                continue
            section_name = str(row[0]).strip()
            course_name = str(row[1]).strip()
            chapter_id = ""
            exercise_ids: list[str] = []
            for cell in row[2:]:
                if not isinstance(cell, dict):
                    continue
                for link in cell.get("links") or []:
                    onclick = link.get("onclick") or ""
                    frame_hit = _SHOWFRAME_RE.search(onclick)
                    if frame_hit:
                        chapter_id = frame_hit.group(2)
                        section_name = section_name or frame_hit.group(1).strip()
                    exam_hit = _SHOWEXAM_RE.search(onclick)
                    if exam_hit:
                        exercise_ids.append(exam_hit.group(1))
            for exercise_id in exercise_ids:
                out[exercise_id] = {"section_id": chapter_id, "section_name": section_name,
                                    "course_name": course_name}
    return out


def text_of(fragment: str) -> str:
    """HTML 片段 → 纯文本（空白归一）。

    题干与选项的归一化规则必须在回收与回填两侧完全一致，否则同一道题在两边会得出
    不同指纹、也匹配不上。因此这个函数公开给 `work.py` 复用，不各自再写一份。
    """
    return " ".join(BeautifulSoup(fragment, "html.parser").get_text(" ", strip=True).split())


_text = text_of  # 旧内部名，保留以免二次改动调用点


def _params(url: str) -> dict[str, str]:
    return {k.lower(): v[0] for k, v in parse_qs(urlparse(url).query).items()}


def _exercise_id(params: dict[str, str]) -> str:
    """练习 ID：练习页写 `Tk_Cl_Id`，答卷回顾页写 `ExamID`，两者是同一个值。

    2026-10-30 实测：交卷后站点直接跳到
    `viewExam.aspx?ExamID=14669&Clerk_kscj_ID=22359501`（不再经过带 `Tk_Cl_Id`
    的交卷成功页），只认 `tk_cl_id` 会把真实答卷判成“不匹配”而拒之门外。
    """
    return params.get("tk_cl_id") or params.get("examid") or ""


def _norm_time(value: str) -> str:
    """把 `2026/10/30 0:54:42` 与 `2026-10-30 00:54:42` 归一成同一串。

    站点在凌晨时段用**不补零的小时**（`0:54:42`），回顾页却写 `00:54:42`；
    直接比字符串会把合法答卷判成“开始时间不一致”。
    """
    m = re.match(r"\s*(\d{4})[-/](\d{1,2})[-/](\d{1,2})\s+(\d{1,2}):(\d{2}):(\d{2})", value or "")
    if not m:
        return (value or "").strip().replace("/", "-")
    year, month, day, hour, minute, second = m.groups()
    return f"{int(year):04d}-{int(month):02d}-{int(day):02d} {int(hour):02d}:{minute}:{second}"


def _labeled(node: Tag, label: str) -> Tag | None:
    for span in node.select("span.tmda"):
        if span.get_text(strip=True) == label:
            value = span.find_next_sibling()
            if value is not None and "tmda" not in value.get("class", []):
                return value
    return None


def _choice(value: Tag | None, keys: list[str]) -> dict | None:
    if value is None:
        return None
    shown = value.get_text(" ", strip=True)
    selected = shown.lower().strip()
    if selected and selected not in keys:
        raise ValueError("答案不属于本题选项，停止入库等待核实")
    return {"kind": "single", "keys": [selected] if selected else [], "text": shown}


def _choice_alias(value: Tag | None, keys: list[str],
                  texts: list[str]) -> dict | None:
    """同 `_choice`，但允许用**选项文本**作答（判断题的判分页写「对」「错」）。

    ⚠ 2026-11-28 判断题实拍：判分页的 `标准答案` 是文本「对」，不是字母 key。
    单选题仍是字母（A/B/C/D），所以两种都认：
      ① 文本直接命中某个选项（对/错）-> 取它的下标对应的 key
      ② 否则按原逻辑（字母 key）
    """
    if value is None:
        return None
    shown = value.get_text(" ", strip=True)
    selected = shown.lower().strip()
    # ① 判断题的二进制答案**优先**：平台判分页写「对/错」，而答题侧/题库
    #    用的 value 是 0/1（0=错、1=对，实测）。必须先换掉，否则会走下面的
    #    「文本命中选项」分支返回字母 key，入库后和回填对不上，等于白存。
    binary = {"对": "1", "正确": "1", "true": "1",
              "错": "0", "错误": "0", "false": "0"}
    if selected in binary:
        return {"kind": "single", "keys": [binary[selected]], "text": shown}
    # ② 文本命中选项 -> 换成 key（单选：选项文本直接作答的容错）
    norm = {_text(x).lower(): keys[i] for i, x in enumerate(texts) if i < len(keys)}
    if selected in norm:
        return {"kind": "single", "keys": [norm[selected]], "text": shown}
    # ② 字母 key
    if selected and selected not in keys:
        raise ValueError("答案不属于本题选项，停止入库等待核实")
    return {"kind": "single", "keys": [selected] if selected else [], "text": shown}


def _answer_rows(frames: dict) -> list:
    """取答题帧的 `tm_arr`：不认帧名，只认结构。

    站点在不同练习模板下换过帧名：普通练习是 `ExamDo`，2026-10-30 实测的单题步骤
    练习是 `ExamTmStepDo`（`ExamTmStepMain.aspx` 的 frameset）。帧名属于模板细节，
    `tm_arr` 才是真锚点，因此按“哪个帧里有非空 tm_arr”定位；命中零个或多个都
    视为快照不完整，宁可拒收也不猜。
    """
    hits = [f["tm_arr"] for f in frames.values()
            if isinstance(f, dict) and isinstance(f.get("tm_arr"), list) and f["tm_arr"]]
    if len(hits) != 1:
        raise ValueError(f"提交前快照应恰有一个答题帧，实际命中 {len(hits)} 个")
    return hits[0]


def parse_review_question(*, node: Tag, row: list, type_map: dict,
                          questions_doc_question: dict | None = None) -> dict:
    """解析判分页（`viewExam.aspx`）里的**一道题**。

    ## 为什么抽成独立函数（2026-11-24 G-7）

    这条解析逻辑原来只存在于 `parse_capture()` 里，而 `parse_capture` 的入参全是
    **已序列化的快照**（为了离线归档）。在线流程（`gxks_work.py` 交卷后）手上是
    活对象，为了复用就得把数据序列化→写盘→读回来，纯属往返浪费。

    抽出来后两条路共用同一份解析，不会出现「离线解析对了、在线漏了一个字段」。

    ## 三类数据必须用同一套归一化

    题干用 `text_of()`、选项文本用 `text_of()` —— 与回填侧（`work.py`）完全一致，
    否则同一道题在两边算出不同指纹、也匹配不上（2026-10-30 的教训）。

    `node` = `div[id^="ddTm_"]` 那一块；`row` = 答题页 `tm_arr` 的原始行
    （用来交叉核对 qid / 题型 / 选项数 / 满分）。
    """
    code = str(row[1])
    ordinal = int(str(node.get("id", "")).split("_")[-1] or 0)
    qid = str(row[0])
    max_score = float(row[3])
    opts = str(row[5]).split(";")
    if len(opts) != int(row[4]):
        raise ValueError("分号拆分后的选项数与平台字段不一致")
    if node.find("dt") is None:
        raise ValueError("回顾页缺少题目结构")
    # 题干：去掉 `<b>` 之后与答题页的 `row[2]` 比对（`<b>` 是题号装饰）
    heading = BeautifulSoup(str(node.find("dt")), "html.parser")
    for b in heading.select("b"):
        b.decompose()
    stem = _text(str(row[2]))
    displayed = re.sub(r"^\.*\d+\.", "", heading.get_text(" ", strip=True)).strip()
    if " ".join(displayed.split()) != stem:
        raise ValueError("回顾题干与提交前题干不一致")
    # 选项：可见文本必须与平台字段逐项相等（防错配答案）
    #
    # ⚠ 2026-11-28 判断题实拍（判分页真实结构）：
    #     <dd><label for="tm_1_0">1、错</label><label for="tm_1_0">2、对</label></dd>
    #   ① 两个 label 的 `for` **相同**（都是 tm_1_0）→ 按 for 前缀取会重复命中
    #   ② 文本是「1、错」「2、对」，前缀是**序号**不是 A/B
    #   ③ `tm_arr[5]` 是空串（选项不在平台字段里）
    #   旧代码拿空串去比 -> 必然 "回顾选项或排序变化，拒绝错配答案"
    # 所以判断题走**内置选项**路径，且选项文本就用平台给的（去掉序号前缀）。
    is_builtin = bool(opts) is False or opts == [""]
    if is_builtin:
        # 判断题：从判分页第一个 dd 里的 label 取可见选项（保留平台顺序）
        dd_first = node.find("dd")
        raw_labels = dd_first.select("label") if dd_first else []
        shown = []
        for x in raw_labels:
            s2 = x.get_text(" ", strip=True).strip()
            s2 = re.sub(r"^\d+、", "", s2)      # 去掉「1、」「2、」序号前缀
            shown.append(s2)
        # 平台可能给「错/对」顺序，也可能「对/错」；按出现顺序当作选项顺序
        if len(shown) < 2:
            shown = ["对", "错"]
        opts_list = shown[:2]
        opts_html = list(opts_list)
    else:
        labels = node.select(f'label[for^="tm_{ordinal}_"]')
        visible_opts = [re.sub(r"^[A-Z]、", "", x.get_text(" ", strip=True)).strip()
                        for x in labels]
        if visible_opts != [_text(x) for x in opts]:
            raise ValueError("回顾选项或排序变化，拒绝错配答案")
        opts_list = [_text(x) for x in opts]
        # 单选保留**原始 HTML**（`<b>甲选项</b>` 这类），下游据此还原渲染
        opts_html = [str(x) for x in opts]

    parts = [{"kind": "option", "key": chr(97 + i), "text": opt,
              "html": opts_html[i] if i < len(opts_html) else opt}
             for i, opt in enumerate(opts_list)]
    keys = [p["key"] for p in parts]
    picked = _choice_alias(_labeled(node, "考生答案"), keys, opts_list)
    standard = _choice_alias(_labeled(node, "标准答案"), keys, opts_list)
    grade = _labeled(node, "考生得分")
    score_match = (re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*分\s*", grade.get_text())
                   if grade else None)
    score = float(score_match[1]) if score_match else None
    if score is not None and score > max_score:
        raise ValueError("单题得分超过满分，停止入库")
    explanation = ""
    for label in node.select("dd > label"):
        if label.get_text(strip=True).startswith("试题解析"):
            explanation = re.sub(r"^\s*试题解析", "", label.decode_contents(), count=1)
    # 题号：优先用 `questions_doc` 里登记的顺序（离线路径），否则从 `ddTm_N` 取
    if questions_doc_question is not None:
        ordinal = int(questions_doc_question.get("no") or ordinal)
        expected_qid = str(questions_doc_question.get("qid") or qid)
        if expected_qid != qid:
            raise ValueError("题 ID 与题目清单不一致")
    return {
        "ordinal": ordinal, "qid": qid, "type_code": code,
        "type_name": (type_map or {}).get(code, "未知题型"),
        "stem": stem, "stem_html": str(row[2]), "max_score": max_score, "parts": parts,
        "extra": {"parent_qid": str(row[7]), "knowledge_id": str(row[8])},
        "answer": {"picked": picked,
                   "picked_source": "site" if picked is not None else None,
                   "standard": standard,
                   "standard_source": "site" if standard is not None else None,
                   "score_got": score,
                   "is_correct": picked["keys"] == standard["keys"]
                   if picked is not None and standard and standard["keys"] else None,
                   "explanation_html": explanation},
    }


def parse_capture(*, account_label: str, questions_doc: dict, stage1: dict,
                  stage2: dict, review_meta: dict, review_html: str,
                  course_doc: dict | None = None) -> dict:
    """以题号＋完整题干＋选项顺序交叉核对，避免按颜色或列表位置盲配答案。

    `course_doc` 是可选的课程列表页快照（`practice_meta.json`）：有它才能把节 ID 与
    课程名填进答卷（练习页 URL 只有练习 ID）。缺省时这两项留空，不影响题目与答案。
    """
    start = _params(stage1["url"])
    if start.get("examstartid") != questions_doc.get("exam_start_id"):
        raise ValueError("提交前快照与题目清单不是同一次练习")
    if start.get("tk_cl_id") != questions_doc.get("section_id"):
        raise ValueError("旧清单的练习 ID 与提交前快照不一致")
    review = _params(review_meta["final_url"])
    result_id = review.get("clerk_kscj_id")
    matches = [p for p in stage2.get("exam_pages", [])
               if _params(p["url"]).get("clerk_kscj_id") == result_id
               and _exercise_id(_params(p["url"])) == start.get("tk_cl_id")]
    if not result_id or len(matches) != 1:
        raise ValueError("答卷回顾与交卷成功页不匹配")
    rows = _answer_rows(stage1["frames"])
    soup = BeautifulSoup(review_html, "html.parser")
    blocks = soup.select('div[id^="ddTm_"]')
    if not rows or len(rows) != len(blocks) or len(rows) != len(questions_doc["questions"]):
        raise ValueError("题目数量不一致，拒绝漏题入库")
    questions = []
    for no, row in enumerate(rows, 1):
        if len(row) < 9 or str(row[0]) != str(questions_doc["questions"][no - 1]["qid"]):
            raise ValueError("题 ID 或原始字段不完整")
        if str(row[1]) != "2":
            # 离线归档只收单选：别的题型没有实测过回顾页结构，
            # 宁可拒绝入库也不要瞎猜（2026-10-30 定的）。
            raise ValueError(f"题型 {row[1]} 尚未实测解析，请保留原始快照")
        node = soup.find(id=f"ddTm_{no}")
        if node is None:
            raise ValueError("回顾页缺少题目结构")
        questions.append(parse_review_question(
            node=node, row=row, type_map=questions_doc.get("type_map", {}),
            questions_doc_question=questions_doc["questions"][no - 1]))
    body = soup.get_text(" ", strip=True)
    time_match = re.search(r"答卷时间\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", body)
    if not time_match or _norm_time(time_match[1]) != _norm_time(start.get("starttime", "")):
        raise ValueError("答卷开始时间不一致，不能把旧答卷关联到新作答")
    total = re.search(r"总得分\s*(\d+(?:\.\d+)?)", body)
    maximum = re.search(r"试卷总分\s*(\d+(?:\.\d+)?)", body)
    if not total or not maximum:
        raise ValueError("缺少答卷总分，不能确认完整回收")
    got_score, total_score = float(total[1]), float(maximum[1])
    scores = [q["answer"]["score_got"] for q in questions]
    if all(s is not None for s in scores) and abs(sum(scores) - got_score) > 0.001:
        raise ValueError("逐题得分合计不等于答卷总得分")
    if abs(sum(q["max_score"] for q in questions) - total_score) > 0.001:
        raise ValueError("逐题满分合计不等于试卷总分")
    hit = section_index(course_doc).get(start["tk_cl_id"], {})
    if hit and questions_doc.get("section_name") and hit["section_name"] != questions_doc["section_name"]:
        raise ValueError("课程列表快照与题目清单的节名不一致")
    return {
        "format_version": 1, "account_label": account_label,
        "exam_start_id": start["examstartid"], "exercise_id": start["tk_cl_id"],
        # course_id 存平台课程名（如“军事理论（字幕版）”）：`ddlClass` 的 value 尚未采集，
        # 存名字既稳定又可直接用于分组；节 ID 才是真正的节粒度主键。
        "course_id": hit.get("course_name", ""), "section_id": hit.get("section_id", ""),
        "section_name": hit.get("section_name") or questions_doc.get("section_name", ""),
        "paper_label": questions_doc.get("paper_label", ""), "start_time": start.get("starttime", ""),
        "result_id": result_id, "total_score": total_score, "got_score": got_score,
        "type_map": questions_doc.get("type_map", {}), "questions": questions,
        "source_rows": rows,
    }


def parse_review_online(*, account_label: str, review_url: str, review_html: str,
                        raw_rows: list, type_map: dict, exercise_id: str,
                        exam_start_id: str = "", section_id: str = "",
                        section_name: str = "", course_id: str = "",
                        start_time: str = "") -> dict:
    """**在线**解析判分页（交卷后立刻调用），产出与 `parse_capture` 同构的 bundle。

    ## 与 `parse_capture` 的分工（2026-11-24 G-7）

    - `parse_capture`：离线归档用。入参是 5 份**已序列化的快照**，校验「回顾页与
      提交前快照是不是同一次练习」（靠 `ExamStartId` / `Clerk_kscj_ID` / 题数 / 题干）。
    - `parse_review_online`：在线流水线用。手上是**活数据**（答题帧刚读到的
      `raw_rows` + `type_map`），交卷后拿到的 `review_html`。省掉序列化往返，
      但**保留同样严格的逐题核对**（qid、题干、选项数、选项文本、满分、总分）。

    ## 校验比离线更严的一点

    离线路径只比题干字符串；这里额外用 `qid`（`row[0]`）逐题对齐 —— 在线时
    我们**知道**每道题的平台 ID，没理由不用它。
    """
    review = _params(review_url)
    result_id = review.get("clerk_kscj_id")
    if not result_id:
        raise ValueError("判分页 URL 缺少 Clerk_kscj_ID（可能没交卷成功）")
    review_exercise = _exercise_id(review)
    if review_exercise and exercise_id and review_exercise != exercise_id:
        raise ValueError("判分页的练习 ID 与本次练习不一致")
    soup = BeautifulSoup(review_html, "html.parser")
    blocks = soup.select('div[id^="ddTm_"]')
    if not raw_rows:
        raise ValueError("交卷前没有读到题目，无法与判分页核对")
    if len(raw_rows) != len(blocks):
        raise ValueError("判分页题数与交卷前不一致，拒绝入库")
    # 答题卡题号 → 该题在 `raw_rows` 里的行（在线没有 `questions_doc`，按顺序对齐）
    questions = []
    for no, row in enumerate(raw_rows, 1):
        if len(row) < 9:
            raise ValueError(f"第 {no} 题原始字段不完整")
        node = soup.find(id=f"ddTm_{no}")
        if node is None:
            raise ValueError(f"判分页缺少第 {no} 题结构（ddTm_{no}）")
        q = parse_review_question(node=node, row=row, type_map=type_map)
        if q["ordinal"] != no:
            raise ValueError(f"判分页题号错位：期望 {no}，实际 {q['ordinal']}")
        questions.append(q)
    body = soup.get_text(" ", strip=True)
    total = re.search(r"总得分\s*(\d+(?:\.\d+)?)", body)
    maximum = re.search(r"试卷总分\s*(\d+(?:\.\d+)?)", body)
    got_score = float(total[1]) if total else None
    total_score = float(maximum[1]) if maximum else None
    # 逐题满分合计必须等于试卷总分：防「只抓到部分题目」
    if total_score is not None:
        if abs(sum(q["max_score"] for q in questions) - total_score) > 0.001:
            raise ValueError("逐题满分合计不等于试卷总分，可能漏题")
    # 逐题得分合计必须等于总得分（全都有分数时才校验）
    scores = [q["answer"]["score_got"] for q in questions]
    if got_score is not None and all(s is not None for s in scores):
        if abs(sum(scores) - got_score) > 0.001:
            raise ValueError("逐题得分合计不等于答卷总得分")
    # 答卷开始时间（判分页会写「答卷时间 yyyy-MM-dd HH:mm:ss」）
    if start_time:
        tm = re.search(r"答卷时间\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", body)
        if tm and _norm_time(tm[1]) != _norm_time(start_time):
            raise ValueError("答卷开始时间不一致，不能把旧答卷关联到新作答")
    return {
        "format_version": 1, "account_label": account_label,
        "exam_start_id": exam_start_id, "exercise_id": exercise_id,
        "course_id": course_id, "section_id": section_id,
        "section_name": section_name, "paper_label": "",
        "start_time": start_time, "result_id": result_id,
        "total_score": total_score, "got_score": got_score,
        "type_map": type_map or {}, "questions": questions,
        "source_rows": raw_rows,
    }
