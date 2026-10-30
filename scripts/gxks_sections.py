"""列出课程各节的学时状态与练习解锁情况——面板「节进度」表的数据源。

只读列表页：不点「开始学习」、不开练习，翻页走 ASP.NET 回发（`__doPostBack`），
这是平台唯一支持的翻页方式，加 URL 参数翻不动。

用法：

    python scripts/gxks_sections.py                # 只读当前页
    ... --pages 2                                  # 抓到第 2 页
    ... --all                                      # 抓到最后一页（补齐整门课）
    ... --cache-only                               # 不连浏览器，直接读上次结果
    ... --label demo --port 9300

输出：最后一行打印 JSON（面板读它），同时落
`data/debug/sections_<label>.json`。该文件同时充当 `--cache-only` 的缓存。

退出码：0 成功；2 连不上浏览器；3 掉登录；5 其他失败。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from xxt.adapters.gxks import accounts as gaccounts  # noqa: E402
from xxt.adapters.gxks import browser as gbrowser  # noqa: E402
from xxt.adapters.gxks import driver as gdriver  # noqa: E402
from xxt.adapters.gxks import login as glogin  # noqa: E402
from xxt.adapters.gxks import selectors  # noqa: E402
from xxt.config import Settings, find_project_root  # noqa: E402
from xxt.store import gxks_ops  # noqa: E402
from xxt.store.schema_gxks import open_question_bank  # noqa: E402

# 行里要带出去的字段：都是 `listparse.parse_course_rows()` 已有的，不额外抓 DOM
FIELDS = ("idx", "name", "platform_chapter_id", "required_minutes", "done_minutes",
          "state", "state_text", "practice_locked", "exam_id", "done_on_platform",
          "category")


def _default_label() -> str:
    """缺省账号代号：取账号表第一个，表空则 `default`。

    ⚠ 代号必须匹配 `accounts._LABEL_RE`（`[a-z0-9_-]`）；中文只能放在 `display`。
    """
    accounts = gaccounts.load_accounts()
    return accounts[0].label if accounts else "default"


def _slim(row: dict, page: int) -> dict:
    out = {k: row.get(k) for k in FIELDS}
    out["page"] = page
    # 面板要一眼看出"这节能不能做练习"，判定只认平台给的 onclick：
    # 锁着时 onclick 是 alert('请先完成学习！')，解锁后才是 showExam(id)。
    out["practice_ready"] = bool(not row.get("practice_locked") and row.get("exam_id"))
    return out


def _gather(drv, pages: int, all_pages: bool) -> tuple[list[dict], dict]:
    """读列表；`all_pages` 时按分页器一路翻到底。"""
    rows: list[dict] = []
    pager = drv.current_pager()
    seen: set[str] = set()
    start = int(pager.get("current") or 1)
    last = int(pager.get("total") or 1) if all_pages else max(start, pages)
    page = start
    while True:
        for raw in drv.read_page(min_rows=1).get("rows") or []:
            section_id = str(raw.get("platform_chapter_id") or "")
            if section_id and section_id in seen:
                continue                      # 翻页回发偶尔重复当前页，去重
            seen.add(section_id)
            rows.append(_slim(raw, page))
        if page >= last:
            break
        if not drv.next_page():
            print(f"[..] 第 {page} 页之后翻不动，停在 {page}/{last}", file=sys.stderr)
            break
        page = int(drv.current_pager().get("current") or page + 1)
        if page > last + 1:
            break
    return rows, drv.current_pager()


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="",
                    help="账号代号（accounts.local.json 里的 label）；缺省取第一个")
    ap.add_argument("--port", type=int, default=0, help="CDP 端口；0=取配置 port_base")
    ap.add_argument("--pages", type=int, default=1, help="读到第几页（含）")
    ap.add_argument("--all", action="store_true", help="一路翻到最后一页")
    ap.add_argument("--cache-only", action="store_true", help="不连浏览器，读上次结果")
    args = ap.parse_args()
    label = args.label or _default_label()

    settings = Settings(root=find_project_root())
    cache = settings.data_dir / "debug" / f"sections_{label}.json"

    if args.cache_only:
        if not cache.exists():
            print(f"[FAIL] 没有缓存 {cache.name}，先跑一次不带 --cache-only 的",
                  file=sys.stderr)
            return 5
        print(cache.read_text(encoding="utf-8"))
        return 0

    port = args.port or gaccounts.resolve_port(
        label, fallback=int(settings.raw.get("gxks", {}).get("port_base",
                                                          gaccounts.DEFAULT_PORT)))
    session = None
    try:
        # cdp 后端忽略 start_url（不打断人工已开的页面）；managed 后端起完浏览器直接开首页
        session = gbrowser.open_session(settings, label=label, port=port,
                                        start_url=selectors.MAIN_PAGE)
        page = session.page
        if gdriver.at_login_page(page.url):
            cred = glogin.load_credentials()
            if not cred:
                print("[FAIL] 已掉登录且无本地凭据 → 请人工登录后重跑", file=sys.stderr)
                return 3
            page.goto(selectors.LOGIN_PAGE, wait_until="commit", timeout=60000)
            page.wait_for_timeout(2000)
            out = glogin.login(page, cred)
            if out["result"] != "ok":
                print(f"[FAIL] 重登失败（{out['result']}）", file=sys.stderr)
                return 3

        drv = gdriver.GxksDriver(page)
        rows, pager = _gather(drv, args.pages, args.all)
        result = {
            "ok": True, "label": label,
            "read_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "pager": pager,
            "summary": {
                "sections": len(rows),
                "done": sum(1 for r in rows if r["state"] == "done"),
                "practice_ready": sum(1 for r in rows if r["practice_ready"]),
                "practice_locked": sum(1 for r in rows if r["practice_locked"]),
                "minutes_done": sum(int(r.get("done_minutes") or 0) for r in rows),
                "minutes_required": sum(int(r.get("required_minutes") or 0) for r in rows),
            },
            "rows": rows,
        }
    except gbrowser.SessionError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        if exc.hint:
            print(f"       {exc.hint}", file=sys.stderr)
        return 2
    except Exception as exc:                                # noqa: BLE001
        print(f"[FAIL] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 5
    finally:
        if session is not None:
            session.close()                                 # Q31：不留常连

    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    db = open_question_bank(settings.data_dir / "gxks_questions.db")
    try:
        gxks_ops.record_op(
            db, account_label=label, action="sections-read",
            detail=f"读取节列表 {len(rows)} 节（已完成 {result['summary']['done']}、"
                   f"可做练习 {result['summary']['practice_ready']}）",
            payload={"pager": pager, "summary": result["summary"]})
    finally:
        db.close()

    print(f"[OK] {len(rows)} 节 → {cache.name}", file=sys.stderr)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
