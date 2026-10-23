"""学习通登录与会话探测。

首次登录：有头浏览器人工完成（扫码/账密/短信），成功后保存 storage_state；
后续运行：加载 storage_state 复用会话，探测失效则提示重新登录。
P0 不接打码平台——人工最可靠；登录判定依据学习通登录成功后写入的 uid cookie。
"""

from __future__ import annotations

import time
from pathlib import Path

from ..log import get_logger

log = get_logger(__name__)


def manual_login(login_url: str, state_path: Path, timeout_s: int = 300) -> bool:
    """打开有头浏览器等待用户人工登录，成功后保存会话到 state_path。"""
    from playwright.sync_api import sync_playwright

    state_path.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(locale="zh-CN")
        page = context.new_page()
        page.goto(login_url)
        print(">> 请在打开的浏览器窗口中完成登录（扫码 / 账号密码 / 短信）。")
        print(">> 登录成功后程序会自动保存会话并关闭浏览器，无需其他操作。")
        ok = False
        deadline = time.monotonic() + timeout_s
        try:
            while time.monotonic() < deadline:
                cookies = context.cookies()
                if any(c["name"] == "uid" and c["value"] for c in cookies):
                    ok = True
                    break
                page.wait_for_timeout(1000)
        finally:
            if ok:
                context.storage_state(path=str(state_path))
            browser.close()
    return ok


def probe_session(space_url: str, state_path: Path) -> str:
    """探测已保存会话是否有效。返回 valid | invalid | no_state。

    判定 heuristic：未登录访问学习空间会被重定向到 passport 登录页。
    M1 实况验证时如发现更好的判定信号（如页面用户名元素）再修正。
    """
    if not state_path.exists():
        return "no_state"
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=str(state_path))
        page = context.new_page()
        try:
            page.goto(space_url, wait_until="domcontentloaded", timeout=30_000)
            final_url = page.url
        finally:
            browser.close()
    if "passport2.chaoxing.com" in final_url or "login" in final_url.lower():
        return "invalid"
    return "valid"
