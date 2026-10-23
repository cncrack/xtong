"""实况侦察：用已保存会话访问学习空间，转储课程列表相关 HTML 与 XHR，用于核对/修正选择器。

用法：python scripts/recon_space.py
产物：data/debug/space.html、data/debug/space_xhr.json（data/ 不入库，含真实数据）

平台改版后可重复运行本脚本重新核对选择器（见 adapters/chaoxing/selectors.py）。
"""

from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "data" / "accounts" / "default" / "state.json"
OUT = ROOT / "data" / "debug"
SPACE_URL = "https://i.chaoxing.com/"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    captured: list[tuple[int, str]] = []
    bodies: dict[str, object] = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=str(STATE))
        page = context.new_page()

        def on_response(response):
            if response.request.resource_type in ("xhr", "fetch"):
                captured.append((response.status, response.url))
                if "courselistdata" in response.url:  # 课程列表接口，保存响应体
                    try:
                        bodies["courselistdata"] = response.text()[:200_000]
                    except Exception:
                        bodies["courselistdata"] = "<读取失败>"

        page.on("response", on_response)
        page.goto(SPACE_URL, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(6_000)  # 课程列表通常由前端 XHR 异步渲染

        (OUT / "space.html").write_text(page.content(), encoding="utf-8")
        (OUT / "space_xhr.json").write_text(
            json.dumps(captured, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        if bodies:
            (OUT / "courselistdata.json").write_text(
                json.dumps(bodies.get("courselistdata"), ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
            print("已捕获 courselistdata 响应体")
        print("最终 URL：", page.url)
        print("页面标题：", page.title())
        for sel in (".course-info", ".course", "[class*=course]", "a[href*='courseid']"):
            print(f"选择器 {sel!r} 命中：", page.locator(sel).count())
        browser.close()
    print(f"产物已写入 {OUT}")


if __name__ == "__main__":
    main()
