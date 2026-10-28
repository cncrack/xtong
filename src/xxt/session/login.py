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


def manual_login(login_url: str, state_path: Path, timeout_s: int = 600) -> bool:
    """打开有头浏览器等待用户人工登录，成功后保存会话到 state_path。

    页面跳转与 cookie 变化全程写日志（data/logs/xxt.log），供远程诊断。
    登录成功信号：出现带值的 uid / _uid / vc3 任一 cookie（学习通登录后写入）。
    """
    from playwright.sync_api import sync_playwright

    state_path.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        from ..core.browser import launch
        from ..core.stealth import hardened_context

        # 人工登录必须让人看见窗口 → 显式覆盖配置（Q36）
        browser = launch(p, headless=False)
        context = hardened_context(browser)
        page = context.new_page()
        log.info("浏览器已启动，正在打开登录页：%s", login_url)
        page.goto(login_url)
        print(">> 请在打开的浏览器窗口中完成登录（扫码 / 账号密码 / 短信）。", flush=True)
        print(">> 登录成功后程序会自动保存会话并关闭浏览器，无需其他操作。", flush=True)
        log.info("登录页已打开，等待登录（超时 %s 秒）……", timeout_s)
        ok = False
        last_url = ""
        deadline = time.monotonic() + timeout_s
        try:
            while time.monotonic() < deadline:
                cookies = context.cookies()
                names = sorted({c["name"] for c in cookies})
                url = page.url
                if url != last_url:
                    log.info("页面跳转：%s（当前 cookie：%s）", url, names)
                    last_url = url
                if any(
                    c["name"] in ("uid", "_uid", "vc3") and c["value"] for c in cookies
                ):
                    ok = True
                    break
                page.wait_for_timeout(1000)
        except Exception as exc:  # 用户手动关窗等异常也要留下诊断线索
            log.error("等待登录期间出现异常：%r", exc)
        finally:
            if ok:
                context.storage_state(path=str(state_path))
                log.info("检测到登录成功，会话已保存：%s", state_path)
            else:
                log.warning(
                    "登录超时/未完成：最后页面=%s", last_url or "(未知)"
                )
            browser.close()
    return ok


def probe_session(space_url: str, state_path: Path) -> tuple[str, str | None]:
    """探测已保存会话是否有效。返回 (state, platform_username)。

    state ∈ valid | invalid | no_state；platform_username 从页面自身的
    stu-base-info 接口截获（零额外请求），供面板展示"刷的是谁"。
    """
    if not state_path.exists():
        return "no_state", None
    from playwright.sync_api import sync_playwright

    holder: dict[str, str] = {}

    def on_response(response) -> None:
        if "stu-base-info" in response.url and "name" not in holder:
            try:
                holder["name"] = response.json().get("userInfo", {}).get("username") or ""
            except Exception:
                pass

    with sync_playwright() as p:
        from ..core.browser import launch
        from ..core.stealth import hardened_context

        browser = launch(p)  # 会话探测：跟随 [stealth].headless
        context = hardened_context(browser, storage_state=str(state_path))
        page = context.new_page()
        page.on("response", on_response)
        state = "invalid"
        try:
            page.goto(space_url, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(3_000)  # 等 stu-base-info 异步返回
            final_url = page.url
            # 未登录访问学习空间会被重定向到 passport 登录页，视为会话失效
            if "passport2.chaoxing.com" not in final_url and "login" not in final_url.lower():
                state = "valid"
                context.storage_state(path=str(state_path))  # 滚动续期（PLAN §5.1）
        finally:
            browser.close()
    return state, (holder.get("name") or None)
