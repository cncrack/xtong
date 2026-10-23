"""实况侦察 3：在 fanyav3 课程页寻找并点击"章节/学习"入口，转储学习页结构。

用法：python scripts/recon_course_enter.py
产物：data/debug/course_enter/（候选元素、点击后 frames、XHR 索引）
"""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "data" / "accounts" / "default" / "state.json"
OUT = ROOT / "data" / "debug" / "course_enter"
URL = (
    "https://mooc1.chaoxing.com/visit/stucoursemiddle"
    "?courseid=266339100&clazzid=154181801&cpi=578875539&ismooc2=1&v=2"
)


def dump_state(page, tag: str) -> None:
    lines = []
    for i, fr in enumerate(page.frames):
        try:
            tree = fr.locator("#coursetree").count()
            video = fr.locator("video").count()
            lines.append(f"[{i}] {fr.url[:130]} | coursetree={tree} video={video}")
        except Exception:
            lines.append(f"[{i}] <已失效>")
    (OUT / f"frames_{tag}.txt").write_text("\n".join(lines), encoding="utf-8")
    print(f"--- frames（{tag}）---")
    for line in lines:
        print(line)
    for i, fr in enumerate(page.frames):
        try:
            (OUT / f"frame_{tag}_{i}.html").write_text(
                fr.evaluate("document.documentElement.outerHTML"), encoding="utf-8"
            )
        except Exception:
            pass


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    xhr: list[tuple[str, str]] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=str(STATE))
        page = context.new_page()

        def on_response(resp):
            if resp.request.resource_type in ("xhr", "fetch"):
                try:
                    xhr.append((resp.url, resp.text()[:100_000]))
                except Exception:
                    pass

        page.on("response", on_response)
        page.goto(URL, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(8_000)
        dump_state(page, "before")

        # 主 frame（fanyav3/stu）导航里的"章节"入口
        top = next((f for f in page.frames if "fanyav3/stu" in f.url), None)
        if top is None:
            print("!! 未找到 fanyav3/stu 主 frame")
        else:
            print(">> 点击主 frame 导航『章节』")
            top.locator("text=章节").first.click(timeout=10_000)
            page.wait_for_timeout(10_000)
            dump_state(page, "after")

        index = [f"xhr_{i}.json <- {u}" for i, (u, _) in enumerate(xhr)]
        for i, (u, body) in enumerate(xhr):
            (OUT / f"xhr_{i}.json").write_text(body, encoding="utf-8")
        (OUT / "xhr_index.txt").write_text("\n".join(index), encoding="utf-8")
        print(f"XHR 捕获 {len(xhr)} 个")
        context.storage_state(path=str(STATE))  # 滚动续期
        browser.close()


if __name__ == "__main__":
    main()
