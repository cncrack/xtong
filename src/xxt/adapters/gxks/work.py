"""在线作答编排：开卷 → 读题 → 出答案 → 回填（默认不提交）。

与 `practice.py` 的分工：那里回收**已交卷**的答卷（离线、12 项交叉校验）；这里
驱动**未交卷**的练习页。两者共用 `tm_arr` 这个结构锚点，但代码路径不合并——
回收的校验和"填表"混在一起，改一个必然弄坏另一个。

安全边界（用户 2026-10-29 / 10-30 定）：
- 默认只填不交，交卷始终由人点（Q31：连着 CDP 会吞 confirm）；
- 题型未实测即拒填（配置 `gxks.allowed_types`），宁可停也不猜；
- 本模块不管断开浏览器——调用方填完必须立刻断开，否则人工交卷的 confirm 被吞。
"""

from __future__ import annotations

import re
import time
from typing import Any, Callable

from . import selectors
from .practice import text_of

# 建卷要服务端写库，慢网络下 15 秒不够；这里给足但不无限等
OPEN_TIMEOUT_MS = 45_000
# 等答题帧出现（tm_arr 非空）的上限
TM_ARR_TIMEOUT_MS = 20_000
# 轮询间隔：URL 稳定判定与找帧共用，避免固定 sleep 把节奏写死
POLL_MS = 600

# "A、选项文本" / "A. 选项内容" / "A）选项"，容忍常见分隔符
_OPTION_LETTER_RE = re.compile(r"^\s*([A-Za-z])\s*[、.．:：)）]?\s*(.*)$", re.S)


class WorkError(RuntimeError):
    """作答链路的可预期失败。`code` 是机器码，面板按码渲染，不靠中文关键词猜。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _keys(count: int) -> list[str]:
    return [chr(97 + i) for i in range(count)]


# ------------------------------------------------------------------ 纯函数（可离线测）

def parse_tx_arr(rows: list | None) -> dict[str, str]:
    """题型表 → `{代码: 名称}`。

    题型表由页面自带（`tx_arr`，13 种，含题库里尚未实测的简答/论述/操作题），
    因此**题型名称不必我们维护**，平台加了新题型这里自动就有。
    """
    out: dict[str, str] = {}
    for row in rows or []:
        if isinstance(row, list) and len(row) >= 3 and str(row[0]).strip():
            out[str(row[0]).strip()] = str(row[2] or "未知题型").strip()
    return out


def parse_tm_rows(rows: list | None, type_map: dict[str, str] | None = None) -> list[dict]:
    """把 `tm_arr` 原始行转成题目字典。

    字段位置来自 `selectors.EXAM_TM_ARR_FIELDS`（平台事实），改版只改那里。
    任一题字段不完整或选项数对不上就抛错——不完整的一行填下去等于乱答。
    """
    field = selectors.EXAM_TM_ARR_FIELDS
    type_map = type_map or {}
    need = max(field.values()) + 1
    questions: list[dict] = []
    for ordinal, row in enumerate(rows or [], 1):
        if not isinstance(row, list) or len(row) < need:
            got = len(row) if isinstance(row, list) else 0
            raise WorkError("structure", f"第 {ordinal} 题字段不完整（{got}/{need}）")
        options = [text_of(x) for x in str(row[field["options"]]).split(";")]
        declared = int(row[field["option_count"]])
        if len(options) != declared:
            raise WorkError(
                "structure", f"第 {ordinal} 题选项数不符：拆出 {len(options)}，平台声明 {declared}")
        code = str(row[field["type_code"]]).strip()
        questions.append({
            "ordinal": ordinal,
            "qid": str(row[field["qid"]]).strip(),
            "type_code": code,
            "type_name": type_map.get(code, "未知题型"),
            "stem": text_of(row[field["stem"]]),
            "max_score": float(row[field["max_score"]]),
            "options": options,
            "option_keys": _keys(len(options)),
        })
    if not questions:
        raise WorkError("structure", "答题帧里没有题目（tm_arr 为空）")
    return questions


def keys_from_answer(answers: list[str], options: list[str]) -> list[str]:
    """把模型给的答案映射成选项 key（a/b/c/d）。

    模型可能回「B」「B、选项文本」「选项文本」三种形态，都认；映射不上就抛错——
    填错一题比不填更糟，用户要核对的正是"它到底填了什么"。
    """
    norm_options = [text_of(o) for o in options]
    valid = set(_keys(len(norm_options)))
    out: list[str] = []
    for raw in answers or []:
        text = str(raw or "").strip()
        if not text:
            continue
        letter_only = re.fullmatch(r"([A-Za-z])", text)
        if letter_only:
            key = letter_only.group(1).lower()
            if key not in valid:
                raise WorkError("ai", f"模型给了不存在的选项 {key}")
            out.append(key)
            continue
        matched = _OPTION_LETTER_RE.match(text)
        if matched:
            letter, rest = matched.group(1).lower(), text_of(matched.group(2))
            if rest and rest in norm_options:
                # 文本精确命中优先：平台若调过选项顺序，字母会撒谎、文本不会
                out.append(chr(97 + norm_options.index(rest)))
                continue
            if letter in valid:
                # 文本对不上（模型改写/截断）时按模型明说的字母填。
                # 不因为描不对就整卷拒收——留到人工核对页上改，比整卷不做更可行。
                out.append(letter)
                continue
        if text in norm_options:
            out.append(chr(97 + norm_options.index(text)))
            continue
        raise WorkError("ai", f"模型答案无法映射到选项：{text[:40]}")
    if not out:
        raise WorkError("ai", "模型没有给出答案")
    return out


def plan_answers(
    questions: list[dict],
    *,
    allowed_types: set[str],
    bank: Callable[[str, list[str]], dict | None] | None = None,
    ask_llm: Callable[[dict], dict] | None = None,
) -> list[dict]:
    """逐题出答案：题库优先 → LLM 兜底。

    `bank` / `ask_llm` 由调用方注入（题库走 sqlite、LLM 走 Provider 中心），
    这样这段编排可以完全离线测，不必连平台也不必真调模型。
    """
    plan: list[dict] = []
    for question in questions:
        code = question["type_code"]
        if code not in allowed_types:
            raise WorkError(
                "structure",
                f"第 {question['ordinal']} 题题型 {code}（{question['type_name']}）"
                f"尚未实测，停止回填")
        hit = bank(question["stem"], question["options"]) if bank else None
        if hit:
            plan.append({**question, "keys": list(hit["keys"]), "source": "bank",
                         "confidence": float(hit.get("confidence", 1.0)), "reason": ""})
            continue
        if ask_llm is None:
            raise WorkError(
                "ai", f"第 {question['ordinal']} 题题库未命中，且未配置 AI 作答")
        got = ask_llm(question)
        plan.append({**question, "keys": list(got["keys"]), "source": "ai",
                     "confidence": float(got.get("confidence", 0.0)),
                     "reason": str(got.get("reason", ""))[:60]})
    return plan


# ------------------------------------------------------------------ 浏览器部分

def settle(page: Any, timeout_ms: int = OPEN_TIMEOUT_MS) -> str:
    """等 URL 离开建卷中间态。

    实测建卷是两跳：`chkExam.aspx` → `ExamCreate.ashx`（服务端写库）→ 302 落地
    frameset。卡在中间态去抓会拿到空页，所以必须等 URL 稳定再往下走。
    """
    deadline = time.time() + timeout_ms / 1000
    url = page.url or ""
    while time.time() < deadline:
        if not any(hint in url for hint in selectors.EXAM_TRANSIENT_HINTS):
            return url
        page.wait_for_timeout(POLL_MS)
        url = page.url or url
    return url


def open_exercise(page: Any, exercise_id: str, *, timeout_ms: int = OPEN_TIMEOUT_MS) -> str:
    """直接导航到建卷地址：等价于点「参加练习」，但不经过 confirm。

    **不点列表里的按钮**是刻意的：`showExam()` 里带 `confirm`，脚本连着 CDP 时
    弹窗会被自动 dismiss（Q31），点出来反而不可控。
    """
    if not str(exercise_id).strip():
        raise WorkError("structure", "缺少练习 ID")
    url = selectors.EXAM_START_URL.format(exercise_id=str(exercise_id).strip())
    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    return settle(page, timeout_ms)


def find_answer_frame(page: Any) -> Any:
    """按结构找答题帧：哪个帧的 `tm_arr` 非空就是它。

    不认帧名——2026-10-30 实测帧名已经从 `ExamDo` 换成 `ExamTmStepDo`，
    按名字找会在平台换模板时静默抓空。
    """
    deadline = time.time() + TM_ARR_TIMEOUT_MS / 1000
    while time.time() < deadline:
        for frame in page.frames:
            try:
                rows = frame.evaluate(selectors.EXAM_TM_ARR_JS)
            except Exception:      # noqa: BLE001  未加载完/跨域的帧会抛，跳过即可
                continue
            if rows:
                return frame
        page.wait_for_timeout(POLL_MS)
    raise WorkError("structure", "没等到答题帧（tm_arr 为空）：练习页可能未落地")


def read_paper(page: Any) -> dict:
    """读一次完整题面：{frame, questions, type_map, raw_rows}。"""
    frame = find_answer_frame(page)
    try:
        type_rows = frame.evaluate(selectors.EXAM_TYPE_ARR_JS)
    except Exception:              # noqa: BLE001  题型表缺失不影响单选题
        type_rows = []
    raw_rows = frame.evaluate(selectors.EXAM_TM_ARR_JS)
    type_map = parse_tx_arr(type_rows)
    return {"frame": frame, "type_map": type_map, "raw_rows": raw_rows,
            "questions": parse_tm_rows(raw_rows, type_map)}


def fill_answers(frame: Any, keys_by_ordinal: dict[int, list[str]]) -> list[dict]:
    """按题号点选项。**必须真点击**：`onclick="check_index(N)"` 会刷新答题卡，
    只把 `checked` 置 true 会让平台侧认为这题没答过。

    点完回读校验；有一个没选上就抛错，不把"部分填好"当成成功。
    """
    filled: list[dict] = []
    for ordinal, keys in sorted(keys_by_ordinal.items()):
        name = selectors.EXAM_RADIO_NAME.format(ordinal=ordinal)
        for key in keys:
            selector = f'input[name="{name}"][value="{key.lower()}"]'
            locator = frame.locator(selector)
            if locator.count() != 1:
                raise WorkError(
                    "structure", f"第 {ordinal} 题选项 {key} 命中 {locator.count()} 个，停止回填")
            try:
                locator.first.click(timeout=5000)
            except Exception as exc:                     # noqa: BLE001
                # 被遮挡时退回 DOM 点击：仍然触发 onclick，不会绕过 check_index
                try:
                    frame.evaluate(
                        "(sel) => document.querySelector(sel).click()", selector)
                except Exception:                        # noqa: BLE001
                    raise WorkError(
                        "structure", f"第 {ordinal} 题选项 {key} 点不上：{exc}") from exc
            filled.append({"ordinal": ordinal, "key": key.lower()})
    return filled


def read_checked(frame: Any) -> dict[int, str]:
    """回读每题当前选中的 key：`{题号: key}`。交卷前核对用。"""
    data = frame.evaluate(
        "() => { const out = {};"
        " document.querySelectorAll('input.ExamRadio:checked').forEach(el => {"
        "   out[el.name.replace('tmda_', '')] = el.value; });"
        " return out; }")
    return {int(k): str(v) for k, v in (data or {}).items() if str(k).isdigit()}


def submit(frame: Any) -> None:
    """交卷：`frmExam.submit()`。

    只在 `gxks.auto_submit=true` 时由后台进程调用。默认路径是人工点交卷——
    因为交卷前平台还会弹一次确认，而人在场时能看见题目是否真的填对了。
    """
    frame.evaluate(selectors.EXAM_SUBMIT_JS)
