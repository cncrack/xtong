"""P2.5 作业页作答器：解析 doHomeWorkNew 题目 → ai.quiz 管线作答 → 填入。

2026-10-24 实况核对（王泽轩 2.12 样本）：
  题目容器 div.singleQquesId[id^=question] > div.TiMu
  题型 span.newZy_TItle 文本【单选题/多选题/判断题/填空题/简答题】
  题干 .Zy_TItle（内联样式结构，取文本时剥题型标记）
  简答作答区 = UEditor 富文本（.edui-editor iframe）；提交 = a.bluebtn(submitCheckTimes)
  选择题选项结构本卷无样本——留通用规则（li 含 radio/checkbox），待遇到校准
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any

from bs4 import BeautifulSoup

from ...ai.llm import LLMClient
from ...ai.quiz import handle_quiz
from ...log import get_logger

log = get_logger("chaoxing.work")

QTYPE_MAP = {"单选题": "single", "多选题": "multi", "判断题": "judge",
             "填空题": "fill", "简答题": "fill"}

# 学习通 font-cxsecret 字体加密的经验对照（密文字符→明文）。
# 来源：实况样本上下文推断，逐步扩充；正规方案（fontTools 字形比对）列入后续。
CIPHER_MAP = {
    "実": "学", "宯": "业", "宬": "就", "宲": "竞", "宱": "争", "宎": "高",
    "宔": "本", "宥": "先", "宩": "自", "宍": "干", "宨": "校",
}


def decode_cxsecret(text: str) -> str:
    return "".join(CIPHER_MAP.get(ch, ch) for ch in text)


def parse_work_questions(html: str,
                         dynamic_map: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """解析作业页 → [{qid, qtype, qtype_cn, stem, options}]（按页面顺序）。

    dynamic_map：本页内嵌密文字体的动态解码映射（优先）；静态经验表兜底。
    """
    from .font_cipher import decode

    def dec(text: str) -> str:
        return decode(text, dynamic_map=dynamic_map, static_map=CIPHER_MAP)

    soup = BeautifulSoup(html, "html.parser")
    out: list[dict[str, Any]] = []
    for q in soup.select("div.singleQuesId"):
        type_el = q.select_one(".newZy_TItle")
        qtype_cn = (type_el.get_text(strip=True) if type_el else "").strip("【】")
        stem_el = q.select_one(".Zy_TItle")
        stem = stem_el.get_text(" ", strip=True) if stem_el else ""
        stem = stem.replace(f"【{qtype_cn}】", "", 1).strip()
        options: list[str] = []
        for li in q.select("ul li"):  # 选择题选项（无样本的通用规则，待校准）
            t = li.get_text(" ", strip=True)
            if li.select_one("input[type=radio],input[type=checkbox]") and t:
                options.append(t)
        out.append({
            "qid": q.get("id", ""),
            "qtype": QTYPE_MAP.get(qtype_cn, "fill"),
            "qtype_cn": qtype_cn,
            "stem": dec(stem)[:400],
            "options": [dec(o) for o in options],
        })
    return out


def _fill_answer(work_frame, qid: str, answer: list[str], qtype: str) -> str:
    """把答案填入该题作答区（不提交）。返回 ok | no-input | no-match。"""
    js = """
    ([qid, text, qtype]) => {
      const q = document.getElementById(qid);
      if (!q) return 'no-input';
      const ed = q.querySelector('.edui-editor iframe');       // UEditor 富文本（简答/填空）
      if (ed && ed.contentWindow && ed.contentWindow.document.body) {
        ed.contentWindow.document.body.innerText = text; return 'ok';
      }
      const ta = q.querySelector('textarea');                   // 普通文本框兜底
      if (ta) { ta.value = text; return 'ok'; }
      return 'no-input';
    }
    """
    if qtype in ("single", "multi", "judge"):
        return work_frame.evaluate(
            """([qid, answers]) => {
              const q = document.getElementById(qid);
              if (!q) return 'no-input';
              let ok = 0;
              for (const li of q.querySelectorAll('ul li')) {
                const inp = li.querySelector("input[type=radio],input[type=checkbox]");
                const t = li.innerText.trim();
                if (inp && answers.some(a => t.includes(a) || a.includes(t))) {
                  inp.checked = true; inp.click(); ok++;
                }
              }
              return ok ? 'ok' : 'no-match';
            }""", [qid, answer])
    return work_frame.evaluate(js, [qid, " ".join(answer), qtype])


def _page_font_map(work_frame) -> dict[str, str]:
    """提取本页内嵌密文字体并生成动态解码映射（失败返回空表，静态表兜底）。"""
    from .font_cipher import build_dynamic_map, extract_cipher_font

    try:
        font = extract_cipher_font(work_frame.content())
        if font:
            m = build_dynamic_map(font)
            if m:
                log.info("字体动态解码：%d 个密文字符", len(m))
                return m
    except Exception as exc:
        log.warning("字体动态解码失败（用静态表）：%r", exc)
    return {}


def answer_work(work_frame, settings, conn: sqlite3.Connection,
                account_id: int, course_name: str, submit: bool = False) -> dict[str, Any]:
    """对当前已打开的作业页整卷作答。submit=False 为半自动（只填不交）。"""
    quiz_cfg = dict(settings.quiz)
    if not quiz_cfg.get("work_enabled"):
        return {"status": "disabled", "detail": "作业作答未启用（[quiz].work_enabled）"}
    # 作业开关已开即视为作答启用（handle_quiz 的 enabled 门是弹题总开关语义）
    quiz_cfg["enabled"] = True
    questions = parse_work_questions(work_frame.content(), dynamic_map=_page_font_map(work_frame))
    stats = {"total": len(questions), "filled": 0, "manual": 0, "status": "semi"}
    llm = LLMClient(settings.llm)
    for q in questions:
        dec = handle_quiz(quiz_cfg, conn, llm, q)
        detail = (f"{course_name}｜{q['qtype_cn']}｜{(q['stem'])[:30]}…｜"
                  f"{dec.get('action')}｜置信{dec.get('confidence', 0):.2f}")
        conn.execute(
            "INSERT INTO run_logs(ts, level, event, detail, account_id) VALUES(?,?,?,?,?)",
            (time.strftime("%Y-%m-%dT%H:%M:%S"),
             "WARNING" if dec["action"] == "manual" else "INFO",
             "work-answer", detail, account_id))
        conn.commit()
        if dec["action"] in ("fill", "fill_submit") and dec.get("answer"):
            r = _fill_answer(work_frame, q["qid"], dec["answer"], q["qtype"])
            if r == "ok":
                stats["filled"] += 1
                log.info("作业填写：[%s] %s…", q["qtype_cn"], q["stem"][:24])
            else:
                stats["manual"] += 1
                log.warning("作业填写失败（%s）：题 %s", r, q["qid"])
        else:
            stats["manual"] += 1
    if submit and stats["filled"]:
        work_frame.locator("a.bluebtn").first.click(timeout=8_000)  # submitCheckTimes
        stats["status"] = "submitted"
        log.warning("作业已提交（%d 题）——自动提交为校准后功能，请留意", stats["filled"])
    else:
        log.info("作业半自动完成：填 %d 题，待人工 %d 题（未提交）",
                 stats["filled"], stats["manual"])
    return stats
