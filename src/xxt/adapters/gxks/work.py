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


# ---------------------------------------------------------------- 题型规格表
#
# ## 为什么需要它（2026-11-27 用户报「1.6 是判断题，又不行了」）
#
# 原来只有「单选能用」，靠 `gxks.allowed_types` 白名单挡着其他题型 ——
# 遇到新题型就停,用户只能一节一节试出来再找我加。这不是解法。
#
# 实测后我发现**不同题型的差别只在两处**，其余（点选项、check_index、回读）
# 完全一样：
#
#   ① 选项从哪来：
#      单选题 → `tm_arr[5]` 是 "A选项;B选项;C选项;D选项"
#      判断题 → `tm_arr[5]` 是**空串**，选项是平台内置的「对 / 错」
#   ② 选项的 value 是什么：
#      单选题 → `a` / `b` / `c` / `d`
#      判断题 → `0` / `1`（且 0=对、1=错，与 label 顺序相反！）
#
# 所以做成数据表：加新题型只加一行，不用改填题逻辑。
# 每项: (选项文本列表, 对应的 value 列表)
TYPE_SPECS: dict[str, dict] = {
    # 单选：选项与 value 都按 a/b/c/d 顺序，由 tm_arr 提供
    "2": {"options_from": "tm_arr", "value_style": "letter"},
    # 多选：结构与单选一致，只是 keys 可能多个；能否用取决于平台渲染
    "3": {"options_from": "tm_arr", "value_style": "letter"},
    # 判断题：平台内置两个 radio，`tm_arr[5]` 是空串（选项不在里面）。
    # ⚠ 实测 2026-11-27（练习 14684）原始 HTML：
    #     <label for="tm_1_1"><input type=radio name=tmda_1 value="0">1、错</label>
    #     <label for="tm_1_0"><input type=radio name=tmda_1 value="1">2、对</label>
    #   「1、」「2、」是**序号前缀，不是答案**。真实含义是：
    #     **value=0 → 错，value=1 → 对**
    #   （我一开始按 label 顺序猜反了，实测 DOM 才纠正。）
    "4": {"options_from": "builtin", "value_style": "binary",
          "builtin_options": ["对", "错"],
          "builtin_values": ["1", "0"]},
}

#: 判断题的「对/错」别名 → 归一到 0/1。模型可能答「对/错/正确/错误/True/False」
#: ⚠ 实测口径：value=1 是「对」，value=0 是「错」（见 TYPE_SPECS["4"] 的注释）。
BINARY_ALIASES = {
    "1": "1", "0": "0",
    "对": "1", "正确": "1", "是": "1", "true": "1", "t": "1",
    "错": "0", "错误": "0", "否": "0", "false": "0", "f": "0",
}


def type_spec(code: str) -> dict | None:
    """取题型规格；没登记 = 尚未实测。"""
    return TYPE_SPECS.get(str(code or "").strip())


def supported_types() -> set[str]:
    """当前代码**真正能填**的题型集合（供配置默认值与面板提示共用）。"""
    return set(TYPE_SPECS)


def question_options(question: dict) -> tuple[list[str], list[str]]:
    """按题型规格返回 `(选项文本, 对应 value)`。

    单选从 `tm_arr` 来；判断题用平台内置的「对/错」+ 0/1。
    """
    code = str(question.get("type_code") or "").strip()
    spec = type_spec(code) or {}
    if spec.get("options_from") == "builtin":
        return (list(spec.get("builtin_options") or []),
                list(spec.get("builtin_values") or []))
    options = list(question.get("options") or [])
    return options, _keys(len(options))


def normalize_keys(question: dict, keys: list[str]) -> list[str]:
    """把 key 归一到该题型真正要用的 value 形态。

    判断题要把 `a/b`（按选项顺序算出来的）换成 `0/1`，
    并接受「对/错/正确/错误/true/false」这类文本答案。
    """
    code = str(question.get("type_code") or "").strip()
    spec = type_spec(code) or {}
    if spec.get("value_style") != "binary":
        return [str(k).lower() for k in keys]
    options, values = question_options(question)
    out: list[str] = []
    for k in keys:
        s = str(k).strip().lower()
        # 已经是 0/1
        if s in ("0", "1"):
            out.append(s)
            continue
        # 文本答案（对/错/正确/错误…）
        if s in BINARY_ALIASES:
            out.append(BINARY_ALIASES[s])
            continue
        # a/b 形态：按选项顺序换成 value
        if len(s) == 1 and "a" <= s <= "z":
            idx = ord(s) - ord("a")
            if 0 <= idx < len(values):
                out.append(values[idx])
                continue
        # 直接命中选项文本
        for i, opt in enumerate(options):
            if s == str(opt).strip().lower():
                if i < len(values):
                    out.append(values[i])
                break
        else:
            raise WorkError(
                "structure", f"第 {question.get('ordinal')} 题答案 {k!r} "
                             f"无法映射到判断题的 对/错")
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
        code = str(row[field["type_code"]]).strip()
        spec = type_spec(code) or {}
        raw_opts = str(row[field["options"]])
        declared = int(row[field["option_count"]])
        if spec.get("options_from") == "builtin":
            # 判断题这类：平台不给选项串（`tm_arr[5]` 是空串），选项是内置的。
            # 不要去校验 declared（实测判断题 declared=1，而真实 radio 有 2 个）。
            options = list(spec.get("builtin_options") or [])
            option_keys = list(spec.get("builtin_values") or [])
        else:
            options = [text_of(x) for x in raw_opts.split(";")]
            if len(options) != declared:
                raise WorkError(
                    "structure",
                    f"第 {ordinal} 题选项数不符：拆出 {len(options)}，平台声明 {declared}")
            option_keys = _keys(len(options))
        questions.append({
            "ordinal": ordinal,
            "qid": str(row[field["qid"]]).strip(),
            "type_code": code,
            "type_name": type_map.get(code, "未知题型"),
            "stem": text_of(row[field["stem"]]),
            "max_score": float(row[field["max_score"]]),
            "options": options,
            "option_keys": option_keys,
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
                f"尚未实测，停止回填（当前支持：{sorted(supported_types())}）")
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
            # `key` 通常已经在 `normalize_keys()` 里换成该题型真正的 value
            # （单选 a/b/c/d；判断 0/1）。这里做一次**幂等**的小写兜底：
            # 纯数字（判断题的 0/1）原样保留，字母统一小写 ——
            # 平台单选 value 是小写，调用方传大写时也能点中。
            k = str(key).strip()
            if not k.isdigit():
                k = k.lower()
            # `input[name=..][value=..]` **不限定 type**，radio / checkbox 都能命中
            # ——所以多选（若用 checkbox）在"点选项"这一步天然兼容，无需特判。
            selector = f'input[name="{name}"][value="{k}"]'
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
            filled.append({"ordinal": ordinal, "key": k})
    return filled


def read_checked(frame: Any) -> dict[int, str]:
    """回读每题当前选中的 value：`{题号: value}`。交卷前核对用。

    ⚠ 2026-11-27：原来只认 `input.ExamRadio:checked`，而**判断题的 radio
    没有 `ExamRadio` class**（实测原始 HTML 只有 `type=radio name=tmda_N`），
    于是判断题回读永远是空 → 回填校验必然判"不一致"。
    改成按 `name^="tmda_"` 匹配，单选/判断都覆盖。
    """
    data = frame.evaluate(
        "() => { const out = {};"
        " document.querySelectorAll('input[name^=\"tmda_\"]:checked').forEach(el => {"
        "   out[el.name.replace('tmda_', '')] = el.value; });"
        " return out; }")
    return {int(k): str(v) for k, v in (data or {}).items() if str(k).isdigit()}


def submit(page: Any) -> None:
    """交卷：必须调**按钮自己的函数 `SubmitExam()`**，不能直接 `frmExam.submit()`。

    ## 为什么（2026-11-27 实拍「试卷保存失败，请稍后再保存」）

    平台交卷按钮的 onclick 是 `SubmitExam()`，它内部做了一串**必要前置**：
        ForbitOperate=1 -> stopclock() -> SaveMode=1 -> 写 LeftTime -> PackAnswer() -> frmExam.submit()
    而原来的 `submit()` 直接 `frmExam.submit()`，**跳过了这些**，服务端拿到的
    状态不完整 -> `ExamSave.aspx` 回一句
        alert('试卷保存失败，请稍后再保存')

    ## 函数在哪个帧（实测 14684）

      ExamTmStepHeader 帧：`SubmitExam`      <- 交卷入口在这
      ExamTmStepDo 帧    ：`PackAnswer` / `NavNextTm` / `frmExam` / `check_index`
      ExamSave 帧        ：交卷结果（失败时就是那句 alert）

    所以按「哪个帧里有 SubmitExam」去找，不认帧名（帧名改过一轮，见
    docs/gxks/gaoxiaokaoshi.md §2.11 第 18 条）。
    """
    # ① 首选：找到有 SubmitExam 的帧，调它（会走完整前置）
    for f in page.frames:
        try:
            if f.evaluate("() => typeof SubmitExam") == "function":
                f.evaluate("() => SubmitExam()")
                return
        except Exception:                                   # noqa: BLE001
            continue
    # ② 兜底：老路径 `frmExam.submit()`（只在没有 SubmitExam 的普通练习上）
    for f in page.frames:
        try:
            if f.evaluate("() => typeof frmExam") == "object":
                f.evaluate(selectors.EXAM_SUBMIT_JS)
                return
        except Exception:                                   # noqa: BLE001
            continue
    raise WorkError("structure", "找不到交卷入口（SubmitExam / frmExam 都没有）")


# 判分页出现的判定：交卷后站点会跳到 `viewExam.aspx?...&Clerk_kscj_ID=...`
_REVIEW_URL_RE = re.compile(r"viewExam\.aspx", re.I)


def wait_graded(page: Any, *, timeout_ms: int = 60_000) -> dict:
    """等交卷后的**判分页**加载完，返回 `{url, html, title}`。

    ## 为什么要单独等（2026-11-24 G-7）

    交卷 → 平台判分 → 跳 `viewExam.aspx`，中间有服务端写库与整页刷新。
    不等就取 `page.content()` 会拿到"正在提交"的过渡页，解析出来是空题目。

    判据用 **URL 里出现 `viewExam.aspx`**（结构锚点），不按固定 sleep。
    """
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        try:
            url = page.url or ""
        except Exception:                                  # noqa: BLE001
            url = ""
        if _REVIEW_URL_RE.search(url):
            # URL 已是对的结果页还不够：等文档就绪，避免拿到骨架
            try:
                page.wait_for_load_state("domcontentloaded", timeout=10_000)
            except Exception:                              # noqa: BLE001
                pass
            return {"url": url, "html": page.content() or "",
                    "title": (page.title() or "")}
        page.wait_for_timeout(POLL_MS)
    raise WorkError("structure", "交卷后没等到判分页（viewExam.aspx）："
                                 "可能没交成功，或平台改版了跳转路径")
