"""兜底补抓：从「成绩分析」找回已交卷的判分页并入库。

## 为什么需要（2026-11-28 用户提）

交卷后抓判分页有超时风险（网络抖动、页面被关、平台跳转慢）。
一旦抓失败，原来的处理是「放弃，建议重做」——用户白做一次。

但平台把**每次交卷**都永久记在「成绩分析 → 课后练习」里，每行都有
`Clerk_Kscj_Id`（= 判分页的钥匙）。所以事后随时能补：

    成绩分析页(MyCj.aspx) → 选「课后练习」→ 读表格拿 Clerk_Kscj_Id
        → 拼 ExamList/ExamPage/ViewExam.aspx?Clerk_Kscj_ID=<id>
        → 解析 → 入库

## 实测结构（2026-11-28，只读确认）

下拉  `ddlCertificateId`：0=普通考试 / 1=课后练习
表格  5 列：考试名称 | 答卷时间 | 成绩 | 及格情况 | 成绩分析
每行  试题解析 -> javascript:viewExam(<Clerk_Kscj_Id>)
      成绩分析 -> javascript:analyseExam(<Clerk_Kscj_Id>, <ExamID>)
分页  `PageSplit1_ddlPage`

## 用法

    # 补抓某一节最近一次
    python scripts/gxks_rescue.py --label default --exercise-id 14684

    # 补抓该账号所有「平台有记录、本地缺」的答卷
    python scripts/gxks_rescue.py --label default --all-missing

    # 只看会做什么，不写库
    python scripts/gxks_rescue.py --label default --all-missing --dry-run

## 边界

- **只补已交卷的**：成绩分析只有交卷后才有记录（历史记录 §2.11 第 14 条）
- **不重复入库**：按 `(exercise_id, exam_start_id)` 查重
- **不点平台按钮**：走 URL 直连判分页，不触发 confirm
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

# ⚠ 必须在 import playwright 之前设好临时目录（沙箱 EPERM 绕过）
_TMPDIR = Path(__file__).resolve().parent.parent / "data" / "tmp" / "pw-artifacts"
_TMPDIR.mkdir(parents=True, exist_ok=True)
os.environ["TMP"] = str(_TMPDIR)
os.environ["TEMP"] = str(_TMPDIR)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from xxt.adapters.gxks import browser, practice, selectors  # noqa: E402
from xxt.config import Settings, find_project_root  # noqa: E402
from xxt.store import gxks_pending, gxks_questions  # noqa: E402
from xxt.store.gxks_questions import is_objective  # noqa: E402
from xxt.store.schema_gxks import open_question_bank  # noqa: E402

SCORE_URL = "ExamList/MyCj.aspx"
REVIEW_PATH = "/ExamList/ExamPage/ViewExam.aspx"


def _abs_url(base: str, rel: str) -> str:
    from urllib.parse import urlparse
    b = urlparse(base)
    return f"{b.scheme}://{b.netloc}/{rel.lstrip('/')}"


def scan_score_page(page) -> list[dict]:
    """打开成绩分析 → 课后练习，读出所有答卷行。

    返回 `[{exercise_id, clerk_id, name, time, score, pass, _frame}]`。
    只读：只切换那个下拉（它本来就在页面上），不点任何提交/交卷。
    """
    from urllib.parse import urlparse

    base = selectors.MAIN_PAGE
    score_url = _abs_url(base, SCORE_URL)

    # 找到已打开的 MyCj 帧，没有就导航过去
    frame = None
    for f in page.frames:
        if "MyCj.aspx" in (f.url or ""):
            frame = f
            break
    if frame is None:
        page.goto(score_url, wait_until="networkidle", timeout=45000)
        for f in page.frames:
            if "MyCj.aspx" in (f.url or ""):
                frame = f
                break
    if frame is None:
        raise RuntimeError("打不开成绩分析页（MyCj.aspx）")

    # 选「课后练习」分类。它是 WebForms 下拉，直接设 value 再触发 change。
    try:
        frame.select_option("#ddlCertificateId", "1")
        page.wait_for_timeout(2500)     # WebForms 回发要等
        # 回发后帧可能重建，重新取
        for f in page.frames:
            if "MyCj.aspx" in (f.url or ""):
                frame = f
                break
    except Exception as exc:                           # noqa: BLE001
        print(f"[WARN] 切换到「课后练习」失败：{exc}", file=sys.stderr)

    rows = frame.evaluate("""() => {
      const out = [];
      document.querySelectorAll('table tr').forEach((tr) => {
        const link = tr.querySelector('a[href*="viewExam"]');
        if (!link) return;
        const href = link.getAttribute('href') || '';
        const m = href.match(/viewExam\\((\\d+)\\)/);
        if (!m) return;
        const cells = [...tr.querySelectorAll('td')].map(td => td.textContent.trim());
        out.push({clerk_id: m[1], cells: cells});
      });
      return out;
    }""")
    result = []
    for r in rows:
        cells = r.get("cells") or []
        result.append({
            "clerk_id": str(r.get("clerk_id") or ""),
            "name": cells[0] if len(cells) > 0 else "",
            "time": cells[1] if len(cells) > 1 else "",
            "score": cells[2] if len(cells) > 2 else "",
            "pass": cells[3] if len(cells) > 3 else "",
        })
    return result


def grab_review(page, clerk_id: str) -> dict:
    """按 Clerk_Kscj_Id 抓判分页 HTML。"""
    url = (_abs_url(selectors.MAIN_PAGE, REVIEW_PATH)
           + f"?Clerk_Kscj_ID={clerk_id}")
    page.goto(url, wait_until="domcontentloaded", timeout=90000)
    # 等 ddTm_ 出现（判分页真体）
    for _ in range(30):
        n = page.evaluate(
            "() => document.querySelectorAll('div[id^=\"ddTm_\"]').length")
        if n:
            break
        page.wait_for_timeout(1000)
    return {"url": page.url, "html": page.content() or ""}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--label", required=True, help="账号代号")
    ap.add_argument("--port", type=int, default=0, help="CDP 端口；0=按账号表取")
    ap.add_argument("--exercise-id", default="", help="只补这一节")
    ap.add_argument("--all-missing", action="store_true",
                    help="补该账号所有「平台有记录、本地缺」的答卷")
    ap.add_argument("--dry-run", action="store_true", help="只列出来，不入库")
    args = ap.parse_args()

    settings = Settings(root=find_project_root())
    from xxt.adapters.gxks import accounts as gaccounts
    port = args.port or gaccounts.resolve_port(
        label=args.label,
        fallback=int(settings.raw.get("gxks", {}).get("port_base", 9222)))

    sess = browser.open_session(settings, label=args.label, port=port,
                                require_list=False)
    page = sess.page
    db = open_question_bank(settings.data_dir / "gxks_questions.db")
    try:
        print(f"[1/3] 读成绩分析（账号 {args.label}，端口 {port}）…")
        rows = scan_score_page(page)
        print(f"      平台上有 {len(rows)} 条课后练习记录")
        if not rows:
            print("      没有记录可补（要先交过卷才有）")
            return 0

        # 本地已有的 (exercise_id, exam_start_id) 集合
        have = {(str(r[0]), str(r[1])) for r in db.execute(
            "SELECT exercise_id, exam_start_id FROM gx_q_papers")}

        # 平台行里没有 exercise_id，只有 clerk_id；用 exercise_id 过滤时靠
        # 「先按 --exercise-id 缩小，再逐个判重」
        targets = []
        for r in rows:
            if args.exercise_id:
                # 成绩页的「考试名称」是 1.06 这种，不含 exercise_id；
                # 用 --exercise-id 时无法从这一列判断，改为全部尝试、由入库去重兜住
                targets.append(r)
            else:
                targets.append(r)

        print(f"[2/3] 逐条抓判分页（共 {len(targets)} 条）…")
        done = skipped = failed = 0
        for i, r in enumerate(targets, 1):
            cid = r["clerk_id"]
            tag = f"{r['name']} {r['time']} 成绩{r['score']}"
            print(f"  ({i}/{len(targets)}) clerk={cid}  {tag}")
            try:
                graded = grab_review(page, cid)
            except Exception as exc:                   # noqa: BLE001
                print(f"        [FAIL] 抓取失败：{type(exc).__name__}: {str(exc)[:80]}")
                failed += 1
                continue

            # 解析需要 raw_rows（答题帧的题行）。判分页本身不带，
            # 但 parse_review_online 要求它做逐题核对。
            # 兜底路径改为「宽松解析」：直接从判分页 DOM 取题干/选项/答案。
            try:
                bundle = practice.parse_review_standalone(
                    account_label=args.label,
                    review_url=graded["url"],
                    review_html=graded["html"],
                )
            except Exception as exc:                   # noqa: BLE001
                print(f"        [FAIL] 解析失败：{type(exc).__name__}: {str(exc)[:100]}")
                failed += 1
                continue

            key = (str(bundle.get("exercise_id") or ""),
                   str(bundle.get("exam_start_id") or ""))
            if key in have:
                print(f"        [SKIP] 已在本地库（ex={key[0]} start={key[1]}）")
                skipped += 1
                continue

            if args.dry_run:
                print(f"        [DRY] 会入库 {len(bundle['questions'])} 题")
                done += 1
                continue

            saved = gxks_questions.import_bundle(db, bundle)
            have.add(key)
            site_ords = [
                q["ordinal"] for q in (bundle.get("questions") or [])
                if is_objective(q.get("type_code", ""))
                and (q.get("answer") or {}).get("standard_source") == "site"]
            gxks_pending.verify_by_site_answer(
                db, account_label=args.label,
                exercise_id=str(bundle.get("exercise_id") or ""),
                ordinals=site_ords)
            print(f"        [OK] 入库 {saved.get('questions')} 题"
                  f"（{'新增' if saved.get('inserted') else '已存在'}）")
            done += 1

        print()
        print(f"[3/3] 完成：入库 {done}，跳过 {skipped}，失败 {failed}")
        return 0 if failed == 0 else 1
    finally:
        db.close()
        sess.close()


if __name__ == "__main__":
    raise SystemExit(main())
