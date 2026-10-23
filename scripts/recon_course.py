"""实况侦察：进入指定课程页，转储章节树结构与视频 iframe 布局（M2 用）。

用法：python scripts/recon_course.py <courseid>
产物：data/debug/course_<id>/（frame 列表、章节树 HTML、首章视频帧信息）
一次进入 + 点开第一章，共 2 个页面动作（最小访问原则）。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "data" / "accounts" / "default" / "state.json"


def main() -> None:
    course_id = sys.argv[1] if len(sys.argv) > 1 else "266339100"
    out = ROOT / "data" / "debug" / f"course_{course_id}"
    out.mkdir(parents=True, exist_ok=True)
    url = (
        "https://mooc1.chaoxing.com/visit/stucoursemiddle"
        f"?courseid={course_id}&clazzid=154181801&cpi=578875539&ismooc2=1&v=2"
    )
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=str(STATE))
        page = context.new_page()
        xhr: list[tuple[str, str]] = []

        def on_response(resp):
            if resp.request.resource_type in ("xhr", "fetch"):
                try:
                    xhr.append((resp.url, resp.text()[:100_000]))
                except Exception:
                    xhr.append((resp.url, "<读取失败>"))

        page.on("response", on_response)
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(12_000)
        print("最终 URL：", page.url)
        (out / "outer.html").write_text(page.content(), encoding="utf-8")

        # 转储所有 frame 渲染后的 HTML
        for i, fr in enumerate(page.frames):
            try:
                (out / f"frame_{i}.html").write_text(
                    fr.evaluate("document.documentElement.outerHTML"), encoding="utf-8"
                )
            except Exception:
                pass

        # 转储 XHR（文件名用序号，URL 与路径映射写在索引里）
        index = []
        for i, (u, body) in enumerate(xhr):
            (out / f"xhr_{i}.json").write_text(body, encoding="utf-8")
            index.append(f"xhr_{i}.json <- {u}")
        (out / "xhr_index.txt").write_text("\n".join(index), encoding="utf-8")
        print(f"XHR 捕获 {len(xhr)} 个")

        def dump_frames(tag: str) -> None:
            lines = []
            for i, fr in enumerate(page.frames):
                has_tree = fr.locator("#coursetree").count()
                has_video = fr.locator("video").count()
                lines.append(f"[{i}] {fr.url[:120]} | coursetree={has_tree} video={has_video}")
            (out / f"frames_{tag}.txt").write_text("\n".join(lines), encoding="utf-8")
            print(f"--- frames（{tag}）---")
            for line in lines:
                print(line)

        dump_frames("initial")

        # 找到章节树所在 frame 并转储
        for fr in page.frames:
            if fr.locator("#coursetree").count():
                html = fr.evaluate("document.querySelector('#coursetree').outerHTML")
                (out / "coursetree.html").write_text(html, encoding="utf-8")
                n = fr.locator(".posCatalog_select").count()
                print(f"章节树在 frame：{fr.url[:100]}，叶子节点 .posCatalog_select 共 {n} 个")
                break
        else:
            print("!! 未找到 #coursetree")

        context.storage_state(path=str(STATE))  # 滚动续期
        browser.close()
    print(f"产物已写入 {out}")


if __name__ == "__main__":
    main()
