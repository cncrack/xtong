"""单节课后练习：开卷 → 读题 → 题库/AI 出答案 → 回填（默认不提交）。

用法（Chrome 已登录、CDP 端口在；`[gxks] browser_backend = "managed"` 时改成
程序自己起浏览器，不需要外部 Chrome，见 `adapters/gxks/browser.py`）：

    .venv\\Scripts\\python.exe scripts/gxks_work.py --exercise-id 14670 \\
        --section-id 1299 --section-name "2.2 国家安全形势"

    ... --mode reference      # 只出答案，不动页面（不动平台、不新建答卷）
    ... --auto-submit         # 填完直接交卷；默认关，人工核对后再点

输出：最后一行打印一段 JSON（面板读它），同时落
`data/debug/work_<label>_<练习ID>_<时间戳>.json`，并把操作写进 `gx_ops`。

退出码：0 成功；2 找不到页面；3 登录失效；4 题型未实测；5 其他失败。

⚠ 跑完本脚本会主动断开浏览器。**不要让它常连**：Playwright 连着 CDP 时，
人工点「交卷」的 confirm 会被自动 dismiss（Q31），人就交不了卷了。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from xxt.adapters.gxks import accounts as gaccounts  # noqa: E402
from xxt.adapters.gxks import browser as gbrowser  # noqa: E402
from xxt.adapters.gxks import driver as gdriver  # noqa: E402
from xxt.adapters.gxks import login as glogin  # noqa: E402
from xxt.adapters.gxks import selectors  # noqa: E402
from xxt.adapters.gxks import practice  # noqa: E402
from xxt.adapters.gxks import work as gwork  # noqa: E402
from xxt.ai.llm import LLMError  # noqa: E402   (LLM 错误类型共用；客户端用 gxks 专属的)
from xxt.ai.quiz import build_messages, parse_llm_json  # noqa: E402
from xxt.config import Settings, find_project_root  # noqa: E402
from xxt.store import gxks_ops, gxks_pending  # noqa: E402
from xxt.store import gxks_questions  # noqa: E402
from xxt.store.gxks_questions import is_objective, lookup_answer  # noqa: E402
from xxt.store.schema_gxks import open_question_bank  # noqa: E402

#: 模型自报置信度低于它 → 在核对单里标「模型自己说不太确定」。
#: ⚠ **只作展示提示，不作任何闸门**（2026-11-11 用户质疑后修正）：
#: 这个数来自 LLM 自己输出，没有校准、也不是准确率，拿它决定「能不能自动交卷」
#: 等于把判断权交给模型的自评。真正的闸门是「题库里有没有平台 site 标准答案」。
LOW_CONFIDENCE = 0.85

EXIT_OK, EXIT_NO_PAGE, EXIT_LOGIN, EXIT_TYPE, EXIT_FAIL = 0, 2, 3, 4, 5


def _settings() -> Settings:
    return Settings(root=find_project_root())


def _allowed_types(settings: Settings) -> set[str]:
    raw = str(settings.raw.get("gxks", {}).get("allowed_types", "2"))
    return {x.strip() for x in raw.split(",") if x.strip()}


def _default_label() -> str:
    """缺省账号代号：取账号表第一个，表空则 `default`。

    ⚠ 代号必须匹配 `accounts._LABEL_RE`（`[a-z0-9_-]`）——“甲同学”那类中文是
    `display` 字段（给人看的），拿中文当主键会让 `gx_ops` / `gx_q_papers` 的写入
    校验直接抛“账号必须使用本地代号”。
    """
    accounts = gaccounts.load_accounts()
    return accounts[0].label if accounts else "default"


def _make_llm(settings: Settings, chapter: str) -> Callable[[dict], dict]:
    """构造 `ask_llm(question) -> {keys, confidence, reason}`。

    ⚠ 2026-11-26 解耦（用户明确要求「两个项目不能互相影响」）：

    原来这里调 `LLMClient(settings.llm, settings.data_dir)` —— 那个共享类的
    `_api_key()` 会去读超星 `xxt.db` 的 `app_config.llm.key_enc`，gxks 根本读不到。
    2026-11-25 我给共享类加了 `api_key` 参数绕过，但那**改了共用文件**，
    合并回 main 会冲突，而且超星代码也不该被 gxks 的需求污染。

    现在改用 gxks 专属的 `GxksLLMClient`（`adapters/gxks/llm.py`）：
    - 它继承 `LLMClient.chat()`，**调 API 的逻辑还是共用的一份**；
    - 只覆写 `_api_key()`，从 gxks 自己的库取 Key；
    - 共用文件 `ai/llm.py` 恢复成 main 原样，零冲突。
    """
    from xxt.adapters.gxks.llm import make_gxks_llm
    client = make_gxks_llm(settings)

    def ask(question: dict) -> dict:
        # 题型固定当单选：`gxks.allowed_types` 默认只放 "2"（单选），其他题型在
        # `plan_answers` 里就整卷停了，走不到 LLM。新增题型实测后同时改这两处。
        messages = build_messages(
            question["stem"], "single", question["options"],
            chapter=chapter, course=str(settings.raw.get("platform", {}).get("name", "")))
        data = parse_llm_json(client.chat(messages))
        return {"keys": gwork.keys_from_answer(data["answer"], question["options"]),
                "confidence": data.get("confidence", 0.0),
                "reason": data.get("reason", "")}

    return ask


def _make_verifier(settings: Settings, chapter: str) -> Callable[[dict, list[str]], dict]:
    """构造 `verify_llm(question, keys) -> {keys, agree, confidence, reason}`。

    这是**交卷前二次验证**（2026-11-28 用户要求）：换个角色把已给的答案独立
    复核一遍，专门对付「AI 自己算出不对、却替题目圆场」这类错误
    （1.6 第 2 题「2025年是海军成立75周年」就是这么错的）。

    为什么不复用 `_make_llm` 的 prompt：两次问法必须**不同**——第一次是
    「你来答题」（倾向于把题做对），第二次是「你来校对」（倾向于挑毛病）。
    同一段 prompt 问两遍，模型会给同样的答案，什么都没验到。
    """
    from xxt.adapters.gxks.llm import make_gxks_llm
    from xxt.ai.quiz import build_verify_messages
    client = make_gxks_llm(settings)
    course = str(settings.raw.get("platform", {}).get("name", ""))

    def verify(question: dict, keys: list[str]) -> dict:
        options = question["options"]
        # 把 key 还原成模型看得懂的形式（字母 + 选项文本），别只给它 "a"
        shown = []
        for k in keys:
            idx = ord(str(k)) - ord("a") if str(k).isalpha() else -1
            if 0 <= idx < len(options):
                shown.append(f"{chr(ord('A') + idx)}、{options[idx]}")
            else:
                shown.append(str(k))
        messages = build_verify_messages(
            question["stem"], "single", options,
            answer="；".join(shown), chapter=chapter, course=course)
        data = parse_llm_json(client.chat(messages))
        try:
            v_keys = gwork.keys_from_answer(data["answer"], options)
        except gwork.WorkError:
            # 审核者给不出合法选项时，就当它没表态（不阻断）
            v_keys = []
        # agree 缺省时用「答案是否与第一遍相同」推断，比信任模型自报更稳
        agree = data.get("agree")
        if not isinstance(agree, bool):
            agree = bool(v_keys) and [str(x) for x in v_keys] == [str(x) for x in keys]
        return {"keys": v_keys,
                "agree": agree,
                "confidence": data.get("confidence", 0.0),
                "reason": data.get("reason", "")}

    return verify


def build_review(plan: list[dict], *, low_confidence: float = LOW_CONFIDENCE) -> dict:
    """G-9 第 1 级 + G-8：产出「交卷前核对单」的风险明细（纯函数，可离线测）。

    用户 2026-11-11 定案两条，都很容易写错：

    ① **`confidence` 不作闸门**。它是 `_make_llm` 里 LLM 自报的
       `data.get("confidence", 0.0)` —— 主观、无校准、不是答案准确率。
       只作展示提示（「模型自己说不太确定」），不参与任何自动决策。

    ② **客观题 / 主观题分开**：
       - 客观题（单选/多选/判断）答案唯一。命中题库 `site` 标准答案就直接用；
         没有才走 AI，并标 `no_site_answer` 等人核对（待验证）。
       - 主观题答案不唯一，**禁止自动入库、禁止跨账号共享**
         （见 `gxks_questions.objective_registry`），一律进待确认。

    唯一的硬条件 = 该题有没有平台 `site` 标准答案，由 `source == "bank"` 表达
    （`lookup_answer` 只认 `standard_source='site'`）。

    `auto_eligible` 只表示「数据上满足 G-9 第 2 级的条件」，
    **本轮第 2 级仍未启用**（见 devlog 2026-11-11-02），调用方不得据此直接交卷。
    """
    risks: list[dict] = []
    for p in plan:
        objective = is_objective(str(p.get("type_code") or ""))
        reasons: list[str] = []
        if not objective:
            reasons.append("subjective")
        if p.get("source") == "ai":
            reasons.append("ai_used")
            if objective:
                reasons.append("no_site_answer")
        conf = float(p.get("confidence") or 0.0)
        if p.get("source") == "ai" and conf < low_confidence:
            reasons.append("low_confidence")
        if reasons:
            risks.append({
                "ordinal": int(p["ordinal"]),
                "type_code": str(p.get("type_code") or ""),
                "type_name": str(p.get("type_name") or ""),
                "stem": str(p.get("stem") or ""),
                "source": p.get("source"),
                "confidence": conf,
                "reasons": reasons,
                "primary_reason": reasons[0],
            })
    counts = {
        "total": len(plan),
        "bank": sum(1 for p in plan if p.get("source") == "bank"),
        "ai": sum(1 for p in plan if p.get("source") == "ai"),
        "risk": len(risks),
    }
    auto_eligible = bool(plan) and all(
        is_objective(str(p.get("type_code") or "")) and p.get("source") == "bank"
        for p in plan
    )
    return {"risks": risks, "counts": counts, "auto_eligible": auto_eligible}


def _ensure_login(page, settings: Settings, label: str = "") -> bool:
    """掉登录就试一次自动重登（凭据默认在 `data/account.local.json`，gitignore）。

    ⚠ **凭据按账号取**（2026-11-07 修 P0）：原来这里 `load_credentials()` 无参，
    读的是单账号时代的全局文件 —— 多账号同时跑时「B 掉登录 → 用 A 的密码登 B」
    会把 A 顶号。现在优先用账号表里该账号的 `name`/`pw`，没填才回落全局旧文件。

    只试一次：连试等于撞库特征。要验证码就停，等人工。
    """
    if not gdriver.at_login_page(page.url):
        return True
    acc = gaccounts.find_by_label(gaccounts.load_accounts(), label) if label else None
    cred = glogin.load_credentials_for(acc, settings) if acc is not None else \
        glogin.load_credentials(glogin.credentials_path(settings))
    if not cred:
        print("[FAIL] 已落到登录页，且未配置本地凭据 → 请人工登录后重跑", file=sys.stderr)
        return False
    print("[..] 检测到登录页，尝试自动重登（只此一次）")
    page.goto(selectors.LOGIN_PAGE, wait_until="commit", timeout=60000)
    page.wait_for_timeout(2000)
    out = glogin.login(page, cred)
    print(f"[..] 重登结果：{out['result']}")
    return out["result"] == "ok"


def _await_confirm_and_reclaim(
    settings: Settings, *, label: str, port: int, exercise_id: str,
    section_id: str, section_name: str, db, timeout_sec: int,
    result_file: Path, raw_rows: list, type_map: dict,
    exam_start_id: str = "", start_time: str = "",
    graded_timeout: int = 300,
) -> dict | None:
    """G-6 人工复核交卷流程：脚本填完停等「确认交卷」→ 你手动交卷 → 回收判分页入库。

    ## 为什么断开的 session 之后才等（2026-11-25 用户拍板）

    填完题后脚本**必须断开 CDP**，否则 Playwright 连着会**自动 dismiss 你手动点交卷的
    confirm**（Q31）。所以流程：

    ① 写 `work_review_<label>_<ex>.json`（phase=waiting，含 `raw_rows`/`type_map`），
       让面板知道"这题在做、等确认"；
    ② 断开 CDP（由调用方 close session）；
    ③ 驻留轮询该文件的 phase —— 面板点「确认交卷」会把它改成 confirm；
    ④ 收到 confirm → 重新连 CDP（`require_list=False`，判分页不是列表页）→ 轮询
       `viewExam.aspx` 判分页 → 用 signal 里存的 `raw_rows`/`type_map` 做逐题核对 →
       `parse_review_online` → `import_bundle` 入库；
    ⑤ 超时没等到 confirm → 写 work-review 日志后退出，不泄漏进程。

    ## 为什么要把 raw_rows/type_map 存进 signal

    判分页解析（`parse_review_online`）需要**交卷前的题目行**（`raw_rows`）和题型表
    （`type_map`）做逐题核对。这两个数据只在**第一次 session**（答题帧）里有；
    重连 CDP 后拿不到。所以在断开前把它们序列化进 signal 文件，重连时读回来。

    返回 `graded_bundle`（入库成功）或 None。
    """
    tmp = settings.data_dir / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    signal = tmp / f"work_review_{label}_{exercise_id}.json"

    # ① 写 waiting 信号（幂等：已存在则覆盖）
    try:
        signal.write_text(json.dumps({
            "label": label, "exercise_id": exercise_id,
            "phase": "waiting", "result_file": result_file.name,
            "raw_rows": raw_rows, "type_map": type_map,
            "exam_start_id": exam_start_id, "start_time": start_time,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }, ensure_ascii=False), encoding="utf-8")
    except Exception:                                   # noqa: BLE001
        print("[WARN] 写等待确认信号失败，跳过驻留流程", file=sys.stderr)
        return None

    # ③ 等确认 —— 同时**自动检测你是否已经自己交卷**（2026-11-28 用户要求）
    #
    # 用户实拍：「我忘记直接点交卷了，没有点确认交卷并入库」。
    # 两道人工动作（浏览器交卷 + 面板确认）容易漏一个，漏了就白做。
    #
    # 所以这里不只听信号文件，还每秒用 `chrome.page_urls()`（**纯 HTTP 只读**，
    # 不建 CDP 连接 → 不会吞掉交卷的 confirm，Q31）看判分页有没有出现。
    # 一旦发现本次练习的判分页，就当作「已确认」继续走入库，不必等面板。
    #
    # ⚠ 只认**本次练习**的判分页：URL 里必须带 `ExamID=<本次 exercise_id>`，
    # 否则可能抓到上一次留下的旧判分页，把旧数据当新的入库。
    print(f"[5/6] 已填完，等你核对并交卷…（最多等 {timeout_sec}s）")
    print("      两种都行：① 浏览器点「交卷」→ 我自动发现并入库；"
          "② 或点面板「确认交卷并入库」")
    from xxt.adapters.gxks import chrome as _gchrome_early
    from urllib.parse import parse_qs as _pqs, urlparse as _uparse

    def _find_review(known_clerk: set[str]) -> str:
        # 找**本次**的判分页 URL；找不到返回空串。
        #
        # ⚠ 判分页 URL 只有 ExamID + Clerk_kscj_ID，**没有 exam_start_id**，
        # 所以只按 ExamID 认会把「上一次同一节的旧判分页」也当成新的
        # （1.8 做第二次时就会踩到，直接把旧答卷当新的入库）。
        # 这里用两个条件卡住：
        #   ① ExamID == 本次 exercise_id；
        #   ② Clerk_kscj_ID **不在** known_clerk 里 —— 那是「等待开始前
        #      就已经存在的判分页」，属于历史，不能算本次。
        for _u in _gchrome_early.page_urls(port):
            _low = _u.lower()
            if "viewexam.aspx" not in _low:
                continue
            _q = _pqs(_uparse(_u).query)
            _eid = (_q.get("ExamID") or _q.get("examid") or [""])[0]
            _cid = (_q.get("Clerk_kscj_ID") or _q.get("clerk_kscj_id") or [""])[0]
            if str(_eid) != str(exercise_id):
                continue
            if _cid and _cid in known_clerk:
                continue                    # 等待开始前就有的旧判分页
            return _u
        return ""

    # 基线：等待开始前已经存在的判分页（历史），要排除掉
    baseline_clerk: set[str] = set()
    try:
        for _u in _gchrome_early.page_urls(port):
            if "viewexam.aspx" in _u.lower():
                _q = _pqs(_uparse(_u).query)
                _cid = (_q.get("Clerk_kscj_ID") or _q.get("clerk_kscj_id") or [""])[0]
                if _cid:
                    baseline_clerk.add(_cid)
    except Exception:                                   # noqa: BLE001
        pass

    deadline = time.monotonic() + timeout_sec
    phase = "waiting"
    auto_detected = False
    auto_url = ""
    while time.monotonic() < deadline:
        time.sleep(1.0)                      # 轮询间隔 1s
        try:
            data = json.loads(signal.read_text(encoding="utf-8"))
            phase = str(data.get("phase") or "waiting")
        except Exception:                               # noqa: BLE001
            pass                    # 文件暂时读不到不算错，继续等
        if phase == "confirm":
            break
        if phase == "cancel":
            # 面板/用户在等待中取消了（如去重做、放弃）
            print("[SKIP] 用户取消确认，未交卷")
            signal.unlink(missing_ok=True)
            return None
        # 自动检测：本次练习的判分页出现了吗？
        try:
            auto_url = _find_review(baseline_clerk)
        except Exception:                               # noqa: BLE001
            auto_url = ""
        if auto_url:
            print("      检测到判分页（你自己交卷了）→ 自动入库，不用点面板")
            auto_detected = True
            break
    else:
        print("[SKIP] 等待超时：既没检测到本次判分页，也没收到面板确认。")
        print("       答案未入库（你可在面板重新做，或交卷后用兜底补抓脚本）")
        signal.unlink(missing_ok=True)
        return None

    # ④ 等判分页 —— **先纯 HTTP 只读轮询，绝不提前连 CDP**（2026-11-27 实拍）
    #
    # 用户实拍：「点了没有反映，没有弹窗」。根因就是 Q31 的另一个面：
    # 原实现收到确认后**立刻 connect_over_cdp** 再等 URL —— 而 Playwright 连着
    # CDP 时，平台交卷的 confirm 会被自动 dismiss，用户点了交卷毫无反应。
    #
    # 现在：轮询期间只用 `chrome.page_urls()`（HTTP 读 /json/list，不建连接），
    # 你的 confirm 能正常弹；等 URL 真变成 viewExam.aspx 了，才短暂连一次抓 HTML。
    if auto_detected:
        # 判分页已经在了（你自己点的交卷被我们发现了）→ 不用再等
        print("[6/6] 判分页已就绪，直接抓取入库…")
    else:
        print(f"[6/6] 收到确认，等你在浏览器点「交卷」…（最多等 {graded_timeout}s）")
    from urllib.parse import parse_qs, urlparse

    from xxt.adapters.gxks import chrome as _gchrome
    from xxt.adapters.gxks.selectors import MAIN_PAGE

    review_url = ""
    deadline = time.monotonic() + graded_timeout
    # 自动检测到的情况：把它找出来填进 review_url，直接跳过下面的等待
    if auto_detected and auto_url:
        review_url = auto_url          # 就是刚才检测到的那个，不重找
    while not review_url and time.monotonic() < deadline:
        for url in _gchrome.page_urls(port):
            low = url.lower()
            if "viewexam.aspx" in low:
                review_url = url                      # 直接到了判分页
                break
            # ⚠ 2026-11-27 实拍：平台交卷后先跳 **ExamMessage.aspx**（交卷成功提示页），
            # 不是直接到 viewExam.aspx（`practice.py` 那句「不再经过交卷成功页」已过时）。
            # 那个中间页的 URL 里已经带了拼判分页所需的两个参数：
            #   Tk_Cl_Id      → viewExam 的 ExamID
            #   Clerk_Kscj_Id → viewExam 的 Clerk_kscj_ID
            # 所以我们**自己拼**判分页 URL，不用等平台自动跳。
            if "exammessage.aspx" in low:
                q = parse_qs(urlparse(url).query)
                exam_id = (q.get("Tk_Cl_Id") or q.get("tk_cl_id") or [""])[0]
                result_id = (q.get("Clerk_Kscj_Id") or q.get("clerk_kscj_id") or [""])[0]
                if exam_id and result_id:
                    base = urlparse(MAIN_PAGE)
                    review_url = (f"{base.scheme}://{base.netloc}"
                                  f"/ExamList/ExamPage/viewExam.aspx"
                                  f"?ExamID={exam_id}&Clerk_kscj_ID={result_id}")
                    print(f"      交卷成功页 → 拼出判分页 {review_url[:90]}")
                break
        if review_url:
            break
        time.sleep(2.0)
    if not review_url:
        print("[SKIP] 等到超时仍未出现判分页——你可能还没点交卷，或平台跳转路径变了。"
              "答案未入库（可重做一次）", file=sys.stderr)
        signal.unlink(missing_ok=True)
        return None

    print("      判分页已定位，抓取并入库…")
    try:
        session2 = gbrowser.open_session(
            settings, label=label, port=port, require_list=False)
        try:
            # ⚠ 2026-11-28 实拍：交卷成功了（说明 SubmitExam 修复有效），但
            #   这里 `goto` 判分页 30 秒超时 -> 没入库。两个原因：
            #   ① 用户手动交卷后**浏览器已经自己跳到判分页**，我们不该再 goto 一次
            #      （重新加载 = 又等一遍，实测判分页本身加载就很慢）
            #   ② 判分页题多资源多，30 秒不够
            # 所以：**先等一会儿让页面自己到位**；确实没到才 goto，且给 90 秒。
            page2 = session2.page
            deadline2 = time.monotonic() + 60        # 等平台自己跳转，最多 60s
            while time.monotonic() < deadline2:
                if "viewexam.aspx" in (page2.url or "").lower():
                    break
                time.sleep(1.0)
            if "viewexam.aspx" not in (page2.url or "").lower():
                print("      浏览器没自己跳到判分页，主动导航…")
                page2.goto(review_url, wait_until="domcontentloaded",
                           timeout=90_000)
            else:
                print("      判分页已在浏览器中打开，直接抓取")
            graded = gwork.wait_graded(page2, timeout_ms=60_000)
            return practice.parse_review_online(
                account_label=label, review_url=graded["url"],
                review_html=graded["html"], raw_rows=raw_rows,
                type_map=type_map, exercise_id=exercise_id,
                section_id=section_id, section_name=section_name,
                exam_start_id=exam_start_id, start_time=start_time)
        finally:
            session2.close()
    except Exception as exc:                            # noqa: BLE001
        print(f"[WARN] 抓取判分页失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return None
    finally:
        signal.unlink(missing_ok=True)


def _answer_text(question: dict, keys: list[str]) -> str:
    """把 key 还原成人看的一句（面板核对列用），**按题型**解析 value。

    ⚠ 2026-11-27 修：判断题的 value 是 0/1，而这里原来按字母算
    （ord(key)-97），"1" 会算出 -48 越界 → 面板显示「?」。
    现在先按该题的 option_keys 反查下标，查不到再退回字母假设。
    """
    options = list(question.get("options") or [])
    spec_keys = list(question.get("option_keys") or [])
    parts = []
    for key in keys:
        s = str(key).strip()
        idx = -1
        if s in spec_keys:
            idx = spec_keys.index(s)
        elif s.lower() in spec_keys:
            idx = spec_keys.index(s.lower())
        elif len(s) == 1 and "a" <= s.lower() <= "z":
            idx = ord(s.lower()) - ord("a")
        if 0 <= idx < len(options):
            parts.append(chr(ord("A") + idx) + "、" + options[idx])
        else:
            parts.append(s)
    return "；".join(parts)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--exercise-id", required=True, help="练习 ID（列表页 showExam(...) 括号里）")
    ap.add_argument("--section-id", default="", help="节 ID（日志与题库归类用）")
    ap.add_argument("--section-name", default="", help="节名（显示用）")
    ap.add_argument("--label", default="",
                    help="账号代号（accounts.local.json 里的 label）；缺省取第一个")
    ap.add_argument("--port", type=int, default=0, help="CDP 端口；0=取配置 port_base")
    ap.add_argument("--mode", default="", choices=["", "ai", "reference", "owner"],
                    help="作答策略；缺省取配置 gxks.work_mode")
    ap.add_argument("--auto-submit", action="store_true", help="填完直接交卷（默认不交）")
    ap.add_argument("--no-fill", action="store_true", help="只出答案，不动页面")
    ap.add_argument("--no-verify", action="store_true",
                    help="关掉交卷前二次验证（默认开：AI 答的题再独立复核一遍）")
    args = ap.parse_args()
    label = args.label or _default_label()

    settings = _settings()
    gxks_cfg = settings.raw.get("gxks", {})
    allowed = _allowed_types(settings)
    mode = args.mode or str(gxks_cfg.get("work_mode", "ai"))
    do_fill = mode == "ai" and not args.no_fill
    do_submit = args.auto_submit or bool(gxks_cfg.get("auto_submit", False))
    if mode == "owner":
        print("[SKIP] 该账号作业策略=号主自理：请在浏览器里自己的做完")
        return EXIT_OK
    port = args.port or gaccounts.resolve_port(
        label, fallback=int(gxks_cfg.get("port_base", gaccounts.DEFAULT_PORT)))
    started = time.monotonic()

    db = open_question_bank(settings.data_dir / "gxks_questions.db")
    session = None
    try:
        try:
            session = gbrowser.open_session(settings, label=label, port=port)
        except gbrowser.SessionError as exc:
            print(f"[FAIL] {exc}", file=sys.stderr)
            if exc.hint:
                print(f"       {exc.hint}", file=sys.stderr)
            return EXIT_NO_PAGE
        page = session.page
        if not _ensure_login(page, settings, label):
            return EXIT_LOGIN

        print(f"[1/4] 开卷 练习ID={args.exercise_id} …")
        landed = gwork.open_exercise(page, args.exercise_id)
        print(f"      落地 {landed[:100]}")
        # ⚠ 2026-11-27 端到端演练查出的断点：`import_bundle` 强制要求
        # `exam_start_id` 非空（它是每次作答的唯一键），而原来脚本**从没提取过它**
        # → 判分页解析出来的 bundle 带着空字符串，入库前就被 `_validate` 拒绝。
        # 它就在答题页 URL 里：`...&ExamStartId=24726784&...`
        exam_start_id = ""
        m_start = re.search(r"[?&]ExamStartId=(\d+)", landed or "", re.I)
        if m_start:
            exam_start_id = m_start.group(1)
            print(f"      本次作答 ExamStartId={exam_start_id}")
        else:
            print("      [WARN] 答题页 URL 里没找到 ExamStartId，入库会失败",
                  file=sys.stderr)
        # 答卷开始时间（判分页会拿它交叉核对，避免把旧答卷认成这次的）
        exam_start_time = ""
        m_t = re.search(r"[?&]StartTime=([^&]+)", landed or "", re.I)
        if m_t:
            from urllib.parse import unquote as _uq
            exam_start_time = _uq(m_t.group(1)).replace("/", "-")

        print("[2/4] 读题 …")
        paper = gwork.read_paper(page)
        questions = paper["questions"]
        print(f"      {len(questions)} 题，题型 "
              f"{sorted({q['type_name'] for q in questions})}")

        print("[3/4] 出答案（题库优先 → AI 兜底）…")
        # 二次验证开关（2026-11-28 用户要求）：默认开，可用 --no-verify 关掉。
        want_verify = (mode != "reference") and not getattr(args, "no_verify", False)
        plan = gwork.plan_answers(
            questions, allowed_types=allowed,
            bank=lambda stem, options: lookup_answer(db, stem=stem, options=options),
            ask_llm=_make_llm(settings, args.section_name) if mode != "reference" else None,
            verify_llm=(_make_verifier(settings, args.section_name)
                        if want_verify else None))
        bank_hits = sum(1 for p in plan if p["source"] == "bank")
        print(f"      题库命中 {bank_hits} / AI {len(plan) - bank_hits}")

        # 2026-11-27：把答案 key 归一到**该题型真正的 value**。
        # 单选是 a/b/c/d；判断题平台用的是 0/1（0=错、1=对，实测 DOM），
        # AI 常答「对/错/正确/错误」，这里统一映射，避免「点不上」。
        for p in plan:
            try:
                p["keys"] = gwork.normalize_keys(p, p["keys"])
            except gwork.WorkError as exc:
                print(f"      [WARN] 第 {p['ordinal']} 题答案无法归一：{exc}",
                      file=sys.stderr)

        filled: list[dict] = []
        checked: dict[int, str] = {}
        # G-7：交卷后回收的判分结果（没交卷时保持 None，不参与 result）
        graded_bundle: dict | None = None
        if do_fill:
            print("[4/4] 回填（不提交）…")
            filled = gwork.fill_answers(
                paper["frame"], {p["ordinal"]: p["keys"] for p in plan})
            checked = gwork.read_checked(paper["frame"])
            wrong = [p["ordinal"] for p in plan
                     if checked.get(p["ordinal"]) not in (p["keys"] or [])]
            if wrong:
                raise gwork.WorkError("structure", f"回填后回读不一致：第 {wrong} 题")
            if do_submit:
                print("[!!] auto_submit=true → 交卷")
                gwork.submit(page)      # submit 现在需要整个 page（SubmitExam 在 Header 帧里）
                # G-7（2026-11-24 用户方案 A）：交卷成功后**立刻回收判分页入库**。
                #
                # 为什么必须在这里做：平台只在判分页（`viewExam.aspx`）显示「标准答案」，
                # 那是**平台官方答案**（不是 AI 推的），也是题库唯一的可信来源。
                # 离开这个页面就再也拿不到了（除非重新做一遍）。
                #
                # ⚠ 入库失败**绝不能影响交卷结果**：交卷是既成事实，入库只是附加收益。
                # 所以整段包在 try 里，出错只记日志。
                try:
                    print("[5/5] 等判分页并入库（客观题）…")
                    graded = gwork.wait_graded(page)
                    graded_bundle = practice.parse_review_online(
                        account_label=label,
                        review_url=graded["url"], review_html=graded["html"],
                        raw_rows=paper["raw_rows"],
                        type_map=paper.get("type_map") or {},
                        exercise_id=args.exercise_id,
                        section_id=args.section_id,
                        section_name=args.section_name,
                        start_time="")
                    saved = gxks_questions.import_bundle(db, graded_bundle)
                    print(f"      入库 {'新增' if saved.get('inserted') else '已存在'} "
                          f"{saved.get('questions')} 题"
                          f"（题目全入；答案只入客观题）")
                    # 平台官方答案 = 已核验，不必继续挂在待确认清单上（用户 2026-11-24）
                    site_ordinals = [
                        q["ordinal"] for q in (graded_bundle.get("questions") or [])
                        if is_objective(q.get("type_code", ""))
                        and (q.get("answer") or {}).get("standard_source") == "site"]
                    cleared = gxks_pending.verify_by_site_answer(
                        db, account_label=label, exercise_id=args.exercise_id,
                        ordinals=site_ordinals)
                    if cleared:
                        print(f"      待确认清单：{cleared} 题已按平台标准答案标为已核验")
                except Exception as exc:                       # noqa: BLE001
                    print(f"[WARN] 判分页入库未完成：{type(exc).__name__}: {exc}")
                    print("       交卷本身已成功，不受影响。")
        else:
            print("[4/4] 只出答案，未动页面（reference 模式）")

        # ⚠ 2026-11-28：组装 result 时**必须带上 `verify`**。
        # `plan_answers` 算好了二次验证结果，但这里原来没搬到 items 里，
        # 于是面板「二次验证」列永远空着（实测 1.7 第一次跑就是这样）。
        # 用 `**({"verify": p["verify"]} if "verify" in p else {})` 保持向后兼容：
        # 没做验证的题（题库命中 / --no-verify）不带这个字段。
        items = [{"ordinal": p["ordinal"], "qid": p["qid"],
                  "type_code": p.get("type_code", ""),
                  "type_name": p["type_name"], "stem": p["stem"],
                  "options": p["options"], "keys": p["keys"],
                  "answer_text": _answer_text(p, p["keys"]),
                  "source": p["source"], "confidence": p["confidence"],
                  "reason": p["reason"],
                  **({"verify": p["verify"]} if "verify" in p else {})}
                 for p in plan]
        review = build_review(plan, low_confidence=LOW_CONFIDENCE)
        pending_ids: list[int] = []
        if do_fill and do_submit and not review["auto_eligible"]:
            print("[!] risky items found -> auto submit skipped")
            do_submit = False

        result = {
            "ok": True, "label": label, "mode": mode,
            "section_id": args.section_id, "section_name": args.section_name,
            "exercise_id": args.exercise_id, "landed_url": landed,
            "filled": bool(filled), "submitted": bool(do_fill and do_submit),
            "review": review,
            "graded": ({"imported": True,
                        "questions": len(graded_bundle.get("questions") or []),
                        "got_score": graded_bundle.get("got_score"),
                        "total_score": graded_bundle.get("total_score")}
                       if graded_bundle else None),
            "questions": items,
            "summary": {"total": len(items), "bank": bank_hits,
                        "ai": len(items) - bank_hits,
                        "checked": {str(k): v for k, v in checked.items()}},
            "elapsed_seconds": round(time.monotonic() - started, 1),
        }

        # 结果落盘 + 写操作日志（同一个连接，不另开：WAL 下多连接写会互相等）
        out_dir = settings.data_dir / "debug"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"work_{label}_{args.exercise_id}_" \
                             f"{datetime.now():%Y%m%d_%H%M%S}.json"
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                            encoding="utf-8")

        detail = (f"{args.section_name or args.section_id} 已回填 {len(items)} 题"
                  f"（题库 {bank_hits} / AI {len(items) - bank_hits}）"
                  if result["filled"] else
                  f"{args.section_name or args.section_id} 仅出答案 {len(items)} 题")
        op_id = gxks_ops.record_op(
            db, account_label=label,
            action="work-fill" if result["filled"] else "work-open",
            detail=detail, result="ok",
            section_id=args.section_id, section_name=args.section_name,
            exercise_id=args.exercise_id, payload=result["questions"])

        for risk in review["risks"]:
            pending_ids.append(gxks_pending.add(
                db, account_label=label,
                question_ordinal=int(risk["ordinal"]),
                reason=str((risk.get("reasons") or ["ai_used"])[0]),
                # ⚠ 2026-11-27 修崩溃：这里原来写 `args.course`，但 argparse
                # **从未定义** `--course` → 每次填完题走到写待确认记录就抛
                # `AttributeError: 'Namespace' object has no attribute 'course'`，
                # 整个 work 任务在「已填题但还没写出 [5/6] 等确认信号」之前就死了
                # （用户实拍：点交卷后「关闭」没反应，因为脚本早已退出）。
                # 课程名的正确来源与 L100 一致：配置里的 platform.name。
                course_id=str(settings.raw.get("platform", {}).get("name", "")),
                section_id=args.section_id, section_name=args.section_name,
                exercise_id=args.exercise_id,
                type_code=str(risk.get("type_code") or ""),
                stem=str(risk.get("stem") or ""),
                risk=risk, work_file=out_path.name, op_id=op_id))
        if pending_ids:
            gxks_ops.record_op(
                db, account_label=label, action="work-review", result="ok",
                detail="pending {} item(s): {}".format(
                    len(pending_ids),
                    ", ".join("Q{} {}".format(r["ordinal"], r["primary_reason"])
                              for r in review["risks"]))[:200],
                section_id=args.section_id, section_name=args.section_name,
                exercise_id=args.exercise_id,
                payload={"pending_ids": pending_ids,
                         "counts": review["counts"],
                         "auto_eligible": review["auto_eligible"]})

        print(f"[OK] 结果已写 {out_path.name}")
        print(json.dumps(result, ensure_ascii=False))

        # G-6 人工复核交卷（2026-11-25 用户拍板）：填完停等「确认交卷」→ 回收判分页入库。
        # 只在本轮确实**填了题**且开启 work_review 时才驻留；reference 模式不动页面、不驻留。
        work_review = bool(gxks_cfg.get("work_review", False))
        review_timeout = int(gxks_cfg.get("work_review_timeout", 600))
        # 「收到确认后等判分页」的独立超时（2026-11-27 实拍）：
        # 你点了「确认交卷」之后还要**切到浏览器窗口**手动点交卷 + 确认弹窗，
        # 原来写死 60 秒，实拍就超时了（[WARN] 交卷后没等到判分页）。
        # 给足 5 分钟，且独立于「等你核对」的 work_review_timeout。
        graded_timeout = int(gxks_cfg.get("work_review_graded_timeout", 300))
        if work_review and result.get("filled"):
            # ⚠ 必须先断开 CDP：连着会吞掉你手动点交卷的 confirm（Q31）。
            # finally 里会再关一次（幂等，无副作用）。
            if session is not None:
                session.close()
                session = None
            graded = _await_confirm_and_reclaim(
                settings, label=label, port=port,
                exercise_id=args.exercise_id,
                section_id=args.section_id, section_name=args.section_name,
                db=db, timeout_sec=review_timeout, result_file=out_path,
                raw_rows=paper["raw_rows"],
                type_map=paper.get("type_map") or {},
                exam_start_id=exam_start_id, start_time=exam_start_time,
                graded_timeout=graded_timeout)
            if graded:
                try:
                    saved = gxks_questions.import_bundle(db, graded)
                    print(f"      入库 {saved.get('questions')} 题"
                          f"（题目全入；答案只入客观题）")
                    site_ordinals = [
                        q["ordinal"] for q in (graded.get("questions") or [])
                        if is_objective(q.get("type_code", ""))
                        and (q.get("answer") or {}).get("standard_source") == "site"]
                    cleared = gxks_pending.verify_by_site_answer(
                        db, account_label=label, exercise_id=args.exercise_id,
                        ordinals=site_ordinals)
                    # 2026-11-27：原来这里丢弃了返回值，用户看不到「哪些题已按
                    # 平台标准答案自动核验」（数据库里其实是标了 verified 的）。
                    if cleared:
                        print(f"      待确认清单：{cleared} 题已按平台标准答案"
                              f"标为已核验（无需人工再核）")
                    elif site_ordinals:
                        # ⚠ 2026-11-28：原来说「暂无待确认项（可能已核验过）」——
                        # 「可能」这种措辞让用户没法判断到底是"早核验过了"还是"根本
                        # 不需要核"。实测（1.6 第三次作答）：题库命中 3/3、平台判分
                        # 全对，`gx_pending` 里压根没有新的待确认行 —— **这是正常且
                        # 最好的结果**，不该说得含糊。
                        # 现在只陈述事实：这些题走的是平台标准答案。
                        print(f"      待确认清单：本次 {len(site_ordinals)} 题均用平台标准答案"
                              f"判定，无需人工确认")
                except Exception as exc:              # noqa: BLE001
                    print(f"[WARN] 入库未完成：{type(exc).__name__}: {exc}", file=sys.stderr)

        return EXIT_OK
    except gwork.WorkError as exc:
        code = "structure" if exc.code == "structure" else "error"
        print(f"[FAIL] {exc}", file=sys.stderr)
        gxks_ops.record_op(db, account_label=label, action="error", result="fail",
                           detail=str(exc)[:200], code=code,
                           section_id=args.section_id, section_name=args.section_name,
                           exercise_id=args.exercise_id)
        return EXIT_TYPE if exc.code == "structure" else EXIT_FAIL
    except LLMError as exc:
        print(f"[FAIL] AI 作答失败：{exc}", file=sys.stderr)
        gxks_ops.record_op(db, account_label=label, action="error", result="fail",
                           detail=f"AI 作答失败：{exc}"[:200], code="ai",
                           section_id=args.section_id, section_name=args.section_name,
                           exercise_id=args.exercise_id)
        return EXIT_FAIL
    except Exception as exc:                                # noqa: BLE001
        print(f"[FAIL] {type(exc).__name__}: {exc}", file=sys.stderr)
        gxks_ops.record_op(db, account_label=label, action="error", result="fail",
                           detail=f"{type(exc).__name__}: {exc}"[:200], code="crash",
                           section_id=args.section_id, section_name=args.section_name,
                           exercise_id=args.exercise_id)
        return EXIT_FAIL
    finally:
        # 必须断开：连着 CDP 会吞掉人工交卷的 confirm（Q31）
        if session is not None:
            session.close()
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
