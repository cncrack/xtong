"""列出课程各节的学时状态与练习解锁情况——面板「节进度」表的数据源。

只读列表页：不点「开始学习」、不开练习，翻页走 ASP.NET 回发（`__doPostBack`），
这是平台唯一支持的翻页方式，加 URL 参数翻不动。

用法：

    python scripts/gxks_sections.py                # 只读当前页
    ... --pages 2                                  # 抓到第 2 页
    ... --all                                      # 抓到最后一页（补齐整门课）
    ... --cache-only                               # 不连浏览器，直接读上次结果
    ... --label demo --port 9300
    ... --label demo --course 32 --all             # 指定课程（多课程时用）

输出：最后一行打印 JSON（面板读它），同时落
`data/debug/sections_<label>_<课程>.json`（**没有课程时**落 `sections_<label>.json`）。
命名规则集中在 `adapters/gxks/sections_cache.py`，面板按同一套规则读，两边不会错位。

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
from xxt.adapters.gxks import listparse  # noqa: E402
from xxt.adapters.gxks import login as glogin  # noqa: E402
from xxt.adapters.gxks import sections_cache  # noqa: E402
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


def _record_fail(settings: Settings, label: str, detail: str) -> None:
    """失败也写一条操作记录（2026-11-03）。

    ## 为什么必须留痕

    面板上任务一结束 `jobs` 就空了、运行日志的尾巴也没了，用户能回看的只有
    「操作记录」表。失败只 `print` 到 stderr 的话，**事后完全查不到原因**——
    实测（2026-11-03 01:13）Chrome 被关掉后点「刷新节列表」，用户只看到任务
    一闪而过，不知道是浏览器没开。

    写库失败不能反过来把主流程带崩，所以整个包在 try 里。
    """
    try:
        db = open_question_bank(settings.data_dir / "gxks_questions.db")
        try:
            gxks_ops.record_op(db, account_label=label, action="sections-read",
                               detail=detail[:400], result="fail")
        finally:
            db.close()
    except Exception:                                           # noqa: BLE001
        pass


def _slim(row: dict, page: int) -> dict:
    out = {k: row.get(k) for k in FIELDS}
    out["page"] = page
    # 面板要一眼看出"这节能不能做练习"，判定只认平台给的 onclick：
    # 锁着时 onclick 是 alert('请先完成学习！')，解锁后才是 showExam(id)。
    # ⚠ 公式抽到 `listparse.practice_ready()`（2026-11-05）：刷课脚本回写缓存时
    # 也要用同一个口径，否则两边算出不同结果（实测 1307 因此一直显示「未解锁」）。
    out["practice_ready"] = listparse.practice_ready(
        row.get("practice_locked"), row.get("exam_id"))
    return out


def _gather(drv, pages: int, all_pages: bool) -> tuple[list[dict], dict]:
    """读列表；`all_pages` 时从**第 1 页**一路翻到底。

    ⚠ **必须显式回到第 1 页再开始**（2026-11-04 修的真 bug）：

    原来的写法是「以当前所在页为起点」：

        start = pager.get("current")     # 实测可能是 4（上次刷课停在最后一页）
        last  = pager.get("total")
        page  = start
        while True:
            drv.read_page()              # ⚠ 这一句内部会点搜索按钮回发
            if page >= last: break       # 于是 4 >= 4 立刻退出

    而 `read_page()` 会点 `#btnSearch` 触发 ASP.NET 回发，**回发会把页码重置回第 1 页**。
    结果是：读到的明明是第 1 页，却标成第 4 页，然后 `page >= last` 立即跳出循环。

    实测后果：36 节只存到 **27 节**，`summary {sections: 27, done: 8}`，
    缺掉第 1 页的 1289~1297 共 9 节（其中 1.1~1.9 全是「已完成」，
    所以「已完成」从 16 掉到 8，看起来像数据错了）。

    修法：先把页码拉回 1（`goto_page(1)`，双向翻页已在 2026-11-04 补齐），
    再按实际页码递增，每轮都以**读回的分页器**为准（不信本地 `page` 变量）。
    """
    rows: list[dict] = []
    seen: set[str] = set()

    # 回到第 1 页；回不去就说明被挤出 iframe 上下文，交给 read_page 自己重进
    if not drv.goto_page(1):
        drv.ensure_list_context()
        if not drv.goto_page(1):
            print("[..] 回不到第 1 页，按当前位置开读", file=sys.stderr)

    pager = drv.current_pager()
    last = int(pager.get("total") or 1) if all_pages else max(1, pages)
    # 每轮都以**读回来的 pager.current** 为准：read_page 的回发会重置页码，
    # 本地累加的 page 变量不可信。
    for _ in range(last + 2):
        parsed = drv.read_page(min_rows=1)
        page = int((parsed.get("pager") or {}).get("current")
                   or drv.current_pager().get("current") or 1)
        for raw in parsed.get("rows") or []:
            section_id = str(raw.get("platform_chapter_id") or "")
            if section_id and section_id in seen:
                continue                  # 翻页回发偶尔重复当前页，去重
            seen.add(section_id)
            rows.append(_slim(raw, page))
        if page >= last:
            break
        if not drv.next_page():
            print(f"[..] 第 {page} 页之后翻不动，停在 {page}/{last}", file=sys.stderr)
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
    ap.add_argument("--course", default="",
                    help="课程 value（列表页 ddlClass）；空 = 自动读当前选中课程，"
                         "缓存落 sections_<代号>_<课程>.json（G-12 后不再落无后缀文件）")
    ap.add_argument("--cache-only", action="store_true", help="不连浏览器，读上次结果")
    args = ap.parse_args()
    label = args.label or _default_label()
    course = sections_cache.safe_course(args.course)

    settings = Settings(root=find_project_root())
    debug_dir = settings.data_dir / "debug"
    # 课程不合法（含中文/路径符）时 safe_course 返回 ""，等价于"不带课程"——绝不拼进路径
    cache = sections_cache.path(debug_dir, label, course)

    if args.cache_only:
        # 读取时按「先带课程、再老文件」找：升级前的老缓存还能用，不会突然"没有节列表"
        hit = next((p for p in sections_cache.read_paths(debug_dir, label, course)
                    if p.exists()), None)
        if hit is None:
            print(f"[FAIL] 没有缓存 {cache.name}（也没有老文件），"
                  f"先跑一次不带 --cache-only 的", file=sys.stderr)
            return 5
        print(hit.read_text(encoding="utf-8"))
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
            # 凭据**按账号**取（2026-11-07 修 P0）：原来 `load_credentials()` 无参读全局
            # 文件，多账号时会拿 A 的密码登 B（顶号）。
            acc = gaccounts.find_by_label(gaccounts.load_accounts(), label)
            cred = glogin.load_credentials_for(acc, settings)
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
        # G-12（2026-11-08）：没指定课程时，读**页面当前真实选中的课程**。
        # 原来只在命令行 --course 给值才按课分文件；面板传空时写回无后缀老文件，
        # 于是「刷新 → course 仍空 → 下次还传空」死循环（实测 default 永远停在
        # sections_default.json，而刷课脚本按课写的是 sections_test_32.json）。
        # driver.current_course() 读 ddlClass 的 selected value，零额外平台请求。
        # ⚠ 指定课程优先：--course 明确给值时不能覆盖它（用户可能就要另一门课）。
        if not course:
            course = drv.current_course()
        # ⚠ path 必须**重算**：L167 算路径时还没连浏览器，拿到的是旧 course。
        cache = sections_cache.path(debug_dir, label, course)
        # 指定了课程就先切过去；切不过去直接停（不猜切换方式，见 driver.select_course）
        if course and args.course:
            if not drv.select_course(course):
                print(f"[FAIL] 切不到课程 {course}（该账号可能没有这门课，"
                      f"或站点改了切换方式）→ 停", file=sys.stderr)
                return 2
        rows, pager = _gather(drv, args.pages, args.all)
        result = {
            "ok": True, "label": label, "course": course,
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
        # 带上 hint（连不上浏览器时 hint 里有可照做的命令）一起落库，用户才查得到
        _record_fail(settings, label, f"读取节列表失败：{exc}"
                     + (f"（{exc.hint}）" if exc.hint else ""))
        return 2
    except Exception as exc:                                # noqa: BLE001
        print(f"[FAIL] {type(exc).__name__}: {exc}", file=sys.stderr)
        _record_fail(settings, label, f"读取节列表失败：{type(exc).__name__}: {exc}")
        return 5
    finally:
        if session is not None:
            session.close()                                 # Q31：不留常连

    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    db = open_question_bank(settings.data_dir / "gxks_questions.db")
    try:
        where = f"课程 {course}" if course else "当前课程"
        gxks_ops.record_op(
            db, account_label=label, action="sections-read",
            detail=f"读取节列表 {len(rows)} 节（{where}；已完成 "
                   f"{result['summary']['done']}、"
                   f"可做练习 {result['summary']['practice_ready']}）",
            payload={"pager": pager, "summary": result["summary"],
                     "course": course})
    finally:
        db.close()

    print(f"[OK] {len(rows)} 节 → {cache.name}", file=sys.stderr)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
