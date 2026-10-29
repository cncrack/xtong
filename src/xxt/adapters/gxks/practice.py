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


def _text(fragment: str) -> str:
    return " ".join(BeautifulSoup(fragment, "html.parser").get_text(" ", strip=True).split())


def _params(url: str) -> dict[str, str]:
    return {k.lower(): v[0] for k, v in parse_qs(urlparse(url).query).items()}


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
               and _params(p["url"]).get("tk_cl_id") == start.get("tk_cl_id")]
    if not result_id or len(matches) != 1:
        raise ValueError("答卷回顾与交卷成功页不匹配")
    rows = stage1["frames"]["ExamDo"]["tm_arr"]
    soup = BeautifulSoup(review_html, "html.parser")
    blocks = soup.select('div[id^="ddTm_"]')
    if not rows or len(rows) != len(blocks) or len(rows) != len(questions_doc["questions"]):
        raise ValueError("题目数量不一致，拒绝漏题入库")
    questions = []
    for no, row in enumerate(rows, 1):
        if len(row) < 9 or str(row[0]) != str(questions_doc["questions"][no - 1]["qid"]):
            raise ValueError("题 ID 或原始字段不完整")
        code = str(row[1])
        if code != "2":
            raise ValueError(f"题型 {code} 尚未实测解析，请保留原始快照")
        opts = str(row[5]).split(";")
        if len(opts) != int(row[4]):
            raise ValueError("分号拆分后的选项数与平台字段不一致")
        node = soup.find(id=f"ddTm_{no}")
        if node is None or node.find("dt") is None:
            raise ValueError("回顾页缺少题目结构")
        heading = BeautifulSoup(str(node.find("dt")), "html.parser")
        for b in heading.select("b"):
            b.decompose()
        stem = _text(str(row[2]))
        displayed = re.sub(r"^\.*\d+\.", "", heading.get_text(" ", strip=True)).strip()
        if " ".join(displayed.split()) != stem:
            raise ValueError("回顾题干与提交前题干不一致")
        labels = node.select(f'label[for^="tm_{no}_"]')
        visible_opts = [re.sub(r"^[A-Z]、", "", x.get_text(" ", strip=True)).strip() for x in labels]
        if visible_opts != [_text(x) for x in opts]:
            raise ValueError("回顾选项或排序变化，拒绝错配答案")
        parts = [{"kind": "option", "key": chr(97 + i), "text": _text(opt), "html": opt}
                 for i, opt in enumerate(opts)]
        keys = [p["key"] for p in parts]
        picked = _choice(_labeled(node, "考生答案"), keys)
        standard = _choice(_labeled(node, "标准答案"), keys)
        grade = _labeled(node, "考生得分")
        score_match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*分\s*", grade.get_text()) if grade else None
        score = float(score_match[1]) if score_match else None
        max_score = float(row[3])
        if score is not None and score > max_score:
            raise ValueError("单题得分超过满分，停止入库")
        explanation = ""
        for label in node.select("dd > label"):
            if label.get_text(strip=True).startswith("试题解析"):
                explanation = re.sub(r"^\s*试题解析", "", label.decode_contents(), count=1)
        questions.append({
            "ordinal": no, "qid": str(row[0]), "type_code": code,
            "type_name": questions_doc.get("type_map", {}).get(code, "未知题型"),
            "stem": stem, "stem_html": str(row[2]), "max_score": max_score, "parts": parts,
            "extra": {"parent_qid": str(row[7]), "knowledge_id": str(row[8])},
            "answer": {"picked": picked, "picked_source": "site" if picked is not None else None,
                       "standard": standard, "standard_source": "site" if standard is not None else None,
                       "score_got": score,
                       "is_correct": picked["keys"] == standard["keys"]
                       if picked is not None and standard and standard["keys"] else None,
                       "explanation_html": explanation},
        })
    body = soup.get_text(" ", strip=True)
    time_match = re.search(r"答卷时间\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", body)
    if not time_match or time_match[1] != start.get("starttime", "").replace("/", "-"):
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
