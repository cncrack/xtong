"""P2.5 侦察：进入指定账号的章节作业页，转储题目结构（只读，不提交）。

用法：python scripts/recon_work.py <account_id> <chapter_idx 或名称关键词>
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "debug" / "work_page"


def _safe_count(frame, selector: str) -> int:
    try:
        return frame.locator(selector).count()
    except Exception:
        return 0


def main() -> None:
    aid = sys.argv[1] if len(sys.argv) > 1 else "2"
    needle = sys.argv[2] if len(sys.argv) > 2 else "2.12"
    OUT.mkdir(parents=True, exist_ok=True)
    import sqlite3

    conn = sqlite3.connect(ROOT / "data/xxt.db")
    conn.row_factory = sqlite3.Row
    course = conn.execute(
        "SELECT * FROM courses WHERE account_id=? AND name LIKE '%知行合一%'", (int(aid),)
    ).fetchone()
    ch = conn.execute(
        "SELECT * FROM chapters WHERE course_id=? AND (name LIKE ? OR idx=?)",
        (course["id"], f"%{needle}%", int(needle) if needle.isdigit() else -1),
    ).fetchone()
    print(f"目标：账号{aid}《{course['name']}》{ch['name']}（kid={ch['platform_chapter_id']}）")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            storage_state=str(ROOT / "data" / "accounts" / str(aid) / "state.json"))
        page = context.new_page()
        page.goto(course["url"], wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(4_000)
        # 双版本入口：直达树 或 点"章节"导航
        tree = next((f for f in page.frames
                     if _safe_count(f, ".chapter_item[id^=cur]") > 0), None)
        if tree is None:
            for fr in page.frames:
                url = fr.url or ""
                if ("/stu?" in url or url.rstrip("/").endswith("/stu")) \
                        and _safe_count(fr, "text=章节") > 0:
                    fr.locator("text=章节").first.click(timeout=10_000)
                    break
            page.wait_for_timeout(6_000)
            tree = next((f for f in page.frames
                         if _safe_count(f, ".chapter_item[id^=cur]") > 0), None)
        if tree is None:
            print("!! 未找到章节树")
            return
        tree.locator(f"#cur{ch['platform_chapter_id']}").first.click(timeout=15_000)
        page.wait_for_timeout(10_000)  # 作业页加载
        for i, fr in enumerate(page.frames):
            try:
                html = fr.evaluate("document.documentElement.outerHTML")
                (OUT / f"frame_{i}.html").write_text(html, encoding="utf-8")
                marks = {
                    "题干": len(re.findall(r"timu|mark_name|question|[题目]", html)) ,
                    "选项": len(re.findall(r"radio|checkbox|option|answer", html)),
                    "提交": len(re.findall(r"提交|submit|btnOk", html)),
                }
                print(f"[{i}] {fr.url[:90]}")
                print(f"    线索: {marks}")
            except Exception:
                pass
        page.screenshot(path=str(OUT / "work.png"), full_page=True)
        context.storage_state(path=str(ROOT / "data" / "accounts" / str(aid) / "state.json"))
        browser.close()
    print(f"产物：{OUT}")


if __name__ == "__main__":
    main()
