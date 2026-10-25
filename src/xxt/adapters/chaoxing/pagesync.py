"""同步 Playwright 页面辅助函数（**仅供同步引擎与作业链路使用**）。

⚠️ 异步引擎（astudy.py）严禁 import 本模块：这里的每个函数都直接调用同步 API
（`page.goto` / `frame.locator`），在 async Playwright 下调用会返回协程而不执行，
正是 2026-10-25 "账号#2 协程未 await" 事故的诱因（devlog 26）。

使用者白名单（由 `tests/test_module_boundaries.py` 守护）：
- `study.py`（同步 StudyRunner）
- `cli.py::cmd_work`（作业 AI 代答）
- `panel/app.py` 的参考答案 / 判分回读接口

纯数据与 JS 拼接请放 `pagejs.py`（两边都能用）。
"""

from __future__ import annotations

import time


def find_frame_with(page, selector: str, timeout_s: int = 30):
    """轮询所有 frame 直到某 frame 出现选择器（条件等待，非行为延时）。"""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        for fr in page.frames:
            try:
                if fr.locator(selector).count() > 0:
                    return fr
            except Exception:
                continue
        page.wait_for_timeout(800)
    return None


def username_sniffer():
    """课程页（fanyav3）会调 stu-base-info 接口，内含平台昵称——截获它，零额外请求。"""
    holder: dict[str, str] = {}

    def on_response(response) -> None:
        if "stu-base-info" in response.url and "name" not in holder:
            try:
                holder["name"] = response.json().get("userInfo", {}).get("username") or ""
            except Exception:
                pass

    return on_response, holder


def open_chapter_frame(page, course_url: str):
    """进入课程并定位章节树 frame（双版本兼容，2026-10-24 实测：
    账号1=新版 fanyav3/stu 需点导航"章节"；账号2=旧版 mycourse/stu 可能直达）。
    顺带截获平台昵称（page._xxt_name_holder）。"""
    sniffer, holder = username_sniffer()
    page.on("response", sniffer)
    page._xxt_name_holder = holder
    page.goto(course_url, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(4_000)

    # ① 章节树已在某个 frame（旧版直达 / 新版已展开）
    tree_frame = find_frame_with(page, ".chapter_item[id^=cur]", timeout_s=8)
    if tree_frame is not None:
        return tree_frame

    # ② 在主 frame（fanyav3/stu 或 mycourse/stu）点导航"章节"
    for fr in page.frames:
        url = fr.url or ""
        if "/stu?" not in url and not url.rstrip("/").endswith("/stu"):
            continue
        try:
            loc = fr.locator("text=章节").first
            if loc.count():
                loc.click(timeout=10_000)
                break
        except Exception:
            continue

    tree_frame = find_frame_with(page, ".chapter_item[id^=cur]", timeout_s=25)
    if tree_frame is None:
        raise RuntimeError("未找到课程主页面（可能课程入口改版）")
    return tree_frame


def current_task_title(page) -> str:
    """旧版知识点页当前任务点标题（.prev_title），拿不到返回空。"""
    fr = find_frame_with(page, ".prev_title", timeout_s=3)
    if fr is None:
        return ""
    try:
        return fr.locator(".prev_title").first.inner_text(timeout=3_000).strip()
    except Exception:
        return ""
