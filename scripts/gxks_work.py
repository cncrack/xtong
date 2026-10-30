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
from xxt.adapters.gxks import work as gwork  # noqa: E402
from xxt.ai.llm import LLMClient, LLMError  # noqa: E402
from xxt.ai.quiz import build_messages, parse_llm_json  # noqa: E402
from xxt.config import Settings, find_project_root  # noqa: E402
from xxt.store import gxks_ops  # noqa: E402
from xxt.store.gxks_questions import lookup_answer  # noqa: E402
from xxt.store.schema_gxks import open_question_bank  # noqa: E402

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

    走超星那套 Provider 中心（`[llm]` + `data/` 里的加密 Key），所以面板配一次
    Key，两个平台都能用；不在这里另存一份 Key。
    """
    client = LLMClient(settings.llm, settings.data_dir)

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


def _ensure_login(page, settings: Settings) -> bool:
    """掉登录就试一次自动重登（凭据在 `data/account.local.json`，gitignore）。

    只试一次：连试等于撞库特征。要验证码就停，等人工。
    """
    if not gdriver.at_login_page(page.url):
        return True
    cred = glogin.load_credentials()
    if not cred:
        print("[FAIL] 已落到登录页，且未配置本地凭据 → 请人工登录后重跑", file=sys.stderr)
        return False
    print("[..] 检测到登录页，尝试自动重登（只此一次）")
    page.goto(selectors.LOGIN_PAGE, wait_until="commit", timeout=60000)
    page.wait_for_timeout(2000)
    out = glogin.login(page, cred)
    print(f"[..] 重登结果：{out['result']}")
    return out["result"] == "ok"


def _answer_text(question: dict, keys: list[str]) -> str:
    """把 key 还原成人看的一句（面板核对列用）：`B、《宪法》`。"""
    parts = []
    for key in keys:
        idx = ord(key) - 97
        options = question["options"]
        label = chr(ord("A") + idx) if 0 <= idx < 26 else "?"
        parts.append(f"{label}、{options[idx]}" if 0 <= idx < len(options) else label)
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
        if not _ensure_login(page, settings):
            return EXIT_LOGIN

        print(f"[1/4] 开卷 练习ID={args.exercise_id} …")
        landed = gwork.open_exercise(page, args.exercise_id)
        print(f"      落地 {landed[:100]}")

        print("[2/4] 读题 …")
        paper = gwork.read_paper(page)
        questions = paper["questions"]
        print(f"      {len(questions)} 题，题型 "
              f"{sorted({q['type_name'] for q in questions})}")

        print("[3/4] 出答案（题库优先 → AI 兜底）…")
        plan = gwork.plan_answers(
            questions, allowed_types=allowed,
            bank=lambda stem, options: lookup_answer(db, stem=stem, options=options),
            ask_llm=_make_llm(settings, args.section_name) if mode != "reference" else None)
        bank_hits = sum(1 for p in plan if p["source"] == "bank")
        print(f"      题库命中 {bank_hits} / AI {len(plan) - bank_hits}")

        filled: list[dict] = []
        checked: dict[int, str] = {}
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
                gwork.submit(paper["frame"])
        else:
            print("[4/4] 只出答案，未动页面（reference 模式）")

        items = [{"ordinal": p["ordinal"], "qid": p["qid"],
                  "type_name": p["type_name"], "stem": p["stem"],
                  "options": p["options"], "keys": p["keys"],
                  "answer_text": _answer_text(p, p["keys"]),
                  "source": p["source"], "confidence": p["confidence"],
                  "reason": p["reason"]} for p in plan]
        result = {
            "ok": True, "label": label, "mode": mode,
            "section_id": args.section_id, "section_name": args.section_name,
            "exercise_id": args.exercise_id, "landed_url": landed,
            "filled": bool(filled), "submitted": bool(do_fill and do_submit),
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
        gxks_ops.record_op(db, account_label=label,
                           action="work-fill" if result["filled"] else "work-open",
                           detail=detail, result="ok",
                           section_id=args.section_id, section_name=args.section_name,
                           exercise_id=args.exercise_id, payload=result["questions"])
        print(f"[OK] 结果已写 {out_path.name}")
        print(json.dumps(result, ensure_ascii=False))
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
