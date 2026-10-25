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
    """把答案填入该题作答区（不提交）。返回 ok | no-input | no-match。

    富文本填入后 dispatch input 事件，确保 UEditor/平台感知内容变化（暂存依赖）。
    """
    js = """
    ([qid, text, qtype]) => {
      const q = document.getElementById(qid);
      if (!q) return 'no-input';
      // UEditor 官方 API（key=answer+题号）——setContent 进入编辑器内部模型，暂存/提交才能读到
      const num = qid.replace('question', '');
      const ed = window.UE && UE.getEditor('answer' + num);
      if (ed && ed.setContent) { ed.setContent(text); return 'ok'; }
      const ifr = q.querySelector('.edui-editor iframe');      // 兜底：直接改 DOM（仅当次会话可见）
      if (ifr && ifr.contentWindow && ifr.contentWindow.document.body) {
        ifr.contentWindow.document.body.innerText = text; return 'ok-dom';
      }
      const ta = q.querySelector('textarea');
      if (ta) { ta.value = text; return 'ok-dom'; }
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


def review_work(work_frame, conn: sqlite3.Connection,
                account_id: int) -> list[dict[str, Any]]:
    """判题回读：解析已提交作业页的每题得分，回写题库（verified/wrong）。

    得分标记按通用文本规则解析（"得分：X"/"X分"/"待批阅"），⚠️ 选择器待提交样本校准；
    题库匹配用题干片段 LIKE（字体解码差异容忍）。
    """
    import re as _re

    from ...ai.quiz import bank_record, bank_record_wrong

    soup = BeautifulSoup(work_frame.content(), "html.parser")
    results: list[dict[str, Any]] = []
    for q in soup.select("div.singleQuesId"):
        text = q.get_text(" ", strip=True)
        stem = _re.sub(r"^[\d\s]+", "", text)[:40]
        score = None
        m = _re.search(r"得分[：:]\s*([\d.]+)", text) or _re.search(r"([\d.]+)\s*分", text)
        if m:
            score = float(m.group(1))
        status = "pending" if "待批阅" in text else (
            "correct" if (score is not None and score > 0) else "wrong")
        results.append({"stem": stem, "score": score, "status": status})
        row = conn.execute(
            "SELECT question_hash FROM questions WHERE stem LIKE ? LIMIT 1",
            (f"%{stem[:14]}%",)).fetchone()
        if row is None:
            continue
        h = row["question_hash"]
        if status == "correct":
            bank_record(conn, h, stem, "fill", [], answer=None, verified=True)
            log.info("判题回读：答对转正 %s…", stem[:16])
        elif status == "wrong":
            bank_record_wrong(conn, h)
            log.info("判题回读：答错清空 %s…", stem[:16])
    conn.execute(
        "INSERT INTO run_logs(ts, level, event, detail, account_id) VALUES(?,?,?,?,?)",
        (time.strftime("%Y-%m-%dT%H:%M:%S"), "INFO", "work-review",
         f"回读 {len(results)} 题：" + "/".join(r["status"] for r in results), account_id))
    conn.commit()
    return results


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
                account_id: int, course_name: str, submit: bool = False,
                chapter_name: str = "", force: bool = False) -> dict[str, Any]:
    """对当前已打开的作业页整卷作答。submit=False 为半自动（只填不交）。

    force=True 跳过 work_enabled 开关（面板主动触发=用户已授权）。
    """
    quiz_cfg = dict(settings.quiz)
    if not quiz_cfg.get("work_enabled") and not force:
        return {"status": "disabled", "detail": "作业作答未启用（[quiz].work_enabled 或面板 --force）"}
    # 作业开关已开即视为作答启用（handle_quiz 的 enabled 门是弹题总开关语义）
    quiz_cfg["enabled"] = True
    questions = parse_work_questions(work_frame.content(), dynamic_map=_page_font_map(work_frame))
    stats = {"total": len(questions), "filled": 0, "manual": 0, "status": "semi"}
    llm = LLMClient(settings.llm, data_dir=settings.data_dir)
    for q in questions:
        dec = handle_quiz(quiz_cfg, conn, llm, {**q, "course": course_name,
                                                "chapter": chapter_name})
        detail = (f"{chapter_name or course_name}｜{q['qtype_cn']}｜{(q['stem'])[:30]}…｜"
                  f"{dec.get('action')}｜置信{dec.get('confidence', 0):.2f}")
        conn.execute(
            "INSERT INTO run_logs(ts, level, event, detail, account_id) VALUES(?,?,?,?,?)",
            (time.strftime("%Y-%m-%dT%H:%M:%S"),
             "WARNING" if dec["action"] == "manual" else "INFO",
             "work-answer", detail, account_id))
        conn.commit()
        if dec["action"] in ("fill", "fill_submit") and dec.get("answer"):
            r = _fill_answer(work_frame, q["qid"], dec["answer"], q["qtype"])
            if r in ("ok", "ok-dom"):
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
    elif stats["filled"]:
        # 半自动关键一步：点平台"暂存"把已填答案存为草稿——否则关浏览器后平台侧为空
        try:
            work_frame.locator("#tempsave").first.click(timeout=6_000)
            work_frame.wait_for_timeout(2_500)  # 等 saveWork() 完成
            stats["status"] = "draft-saved"
            log.info("作业半自动完成：填 %d 题，暂人工 %d 题，已点暂存（平台草稿可登录核对）",
                     stats["filled"], stats["manual"])
        except Exception as exc:
            stats["status"] = "semi"
            log.warning("暂存失败（答案仅在本次会话，登录平台可能为空）：%r", exc)
    else:
        stats["status"] = "semi"
        log.info("作业完成：填 0 题，待人工 %d 题", stats["manual"])
    return stats
