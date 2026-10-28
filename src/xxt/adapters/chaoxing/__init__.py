"""超星学习通适配器。

平台 URL 来自配置；页面选择器统一放 selectors.py，平台改版时优先只改那一个文件。
课程列表数据源 = 学习空间页面自身发起的 visit/courselistdata 接口（HTML 片段），
复用页面自己的请求、零额外流量，账号安全友好（2026-10-24 实况核对）。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ..base import PlatformAdapter
from ...config import Settings
from ...log import get_logger

log = get_logger("chaoxing")


class ChaoxingAdapter(PlatformAdapter):
    name = "chaoxing"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.login_url: str = settings.platform["login_url"]
        self.space_url: str = settings.platform["space_url"]
        self.last_username: str | None = None  # 最近一次页面流程截获的平台昵称

    def login(self, state_path: Path) -> bool:
        from ...session.login import manual_login

        return manual_login(self.login_url, state_path)

    def probe_session(self, state_path: Path) -> tuple[str, str | None]:
        from ...session.login import probe_session

        return probe_session(self.space_url, state_path)

    def fetch_courses(self, state_path: Path) -> list[dict[str, Any]]:
        """访问学习空间一次，捕获页面自身的 courselistdata 响应并解析。

        账号安全考量：不额外请求任何接口，只截获浏览器正常加载时页面自己发的流量；
        结束时回写 storage_state 实现滚动续期（PLAN §5.1）。
        """
        from playwright.sync_api import sync_playwright

        from .courses import parse_course_cards

        holder: dict[str, str | object] = {}

        def on_response(response) -> None:
            if "courselistdata" in response.url and "body" not in holder:
                try:
                    holder["body"] = response.text()
                except Exception as exc:  # 响应体读取失败也要留下线索
                    log.warning("读取 courselistdata 响应失败：%r", exc)
            if "stu-base-info" in response.url and "name" not in holder:
                try:
                    holder["name"] = response.json().get("userInfo", {}).get("username") or ""
                except Exception:
                    pass

        with sync_playwright() as p:
            from ...core.browser import launch
            from ...core.stealth import hardened_context

            browser = launch(p, self.settings)
            context = hardened_context(browser, storage_state=str(state_path))
            page = context.new_page()
            page.on("response", on_response)
            try:
                page.goto(self.space_url, wait_until="domcontentloaded", timeout=60_000)
                deadline = time.monotonic() + 20
                while "body" not in holder and time.monotonic() < deadline:
                    page.wait_for_timeout(500)
                final_url = page.url
                if "passport2.chaoxing.com" in final_url:
                    raise RuntimeError("会话已失效，请先执行 xxt login")
                context.storage_state(path=str(state_path))  # 滚动续期
            finally:
                browser.close()

        if "body" not in holder:
            raise RuntimeError("未捕获到 courselistdata 接口（可能平台改版，重跑 scripts/recon_space.py 核对）")
        courses = parse_course_cards(str(holder["body"]))
        name = holder.get("name")
        self.last_username = name if isinstance(name, str) and name else None
        log.info("课程抓取完成：%d 门", len(courses))
        return courses

    def fetch_chapters(self, course_row: dict[str, Any], state_path: Path) -> list[dict[str, Any]]:
        """进入课程章节页，抓取章节树叶子节点（一次导航 + 一次章节导航点击）。"""
        from playwright.sync_api import sync_playwright

        from .chapters import parse_chapter_tree
        from .study import find_frame_with, open_chapter_frame

        with sync_playwright() as p:
            from ...core.browser import launch
            from ...core.stealth import hardened_context

            browser = launch(p, self.settings)
            context = hardened_context(browser, storage_state=str(state_path))
            page = context.new_page()
            try:
                tree_frame = open_chapter_frame(page, course_row["url"])
                html = tree_frame.evaluate(
                    "(document.querySelector('#fanyaChapter') || document.body).outerHTML"
                )
                if "passport2.chaoxing.com" in page.url:
                    raise RuntimeError("会话已失效，请先执行 xxt login")
                # 昵称抓取双通道：新版页等 stu-base-info 异步返回；旧版页读顶部 div.name
                page.wait_for_timeout(2500)
                holder = getattr(page, "_xxt_name_holder", {})
                name = holder.get("name")
                if not (isinstance(name, str) and name):
                    for fr in page.frames:
                        if "/stu" in (fr.url or ""):
                            try:
                                el = fr.locator("div.name").first
                                if el.count():
                                    name = el.inner_text(timeout=2_000).strip()
                                    if name:
                                        break
                            except Exception:
                                continue
                self.last_username = name if isinstance(name, str) and name else None
                context.storage_state(path=str(state_path))  # 滚动续期
            finally:
                browser.close()
        chapters = parse_chapter_tree(html)
        log.info("章节抓取完成：%d 个叶子节点", len(chapters))
        return chapters
