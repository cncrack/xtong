"""从平台读取画像（best-effort）：姓名 / 学号 / 学校 / 院系。

纪律（2026-10-26 会话 32 新增）：
- **一次访问**：只打开学习空间一次（`platform.space_url`），复用页面自身发出的
  `stu-base-info` 响应——与 `session.login.probe_session` 同一抓取点，**零额外请求**。
- **绝不猜**：`userInfo` 里没有的字段一律留空，绝不按学号反推入学年份或编专业。
- **平台细节只在本文件**：URL 与字段键名全部收在这里（AGENTS 硬性约定）。
- ⚠ **尚未实况验证**：真实响应里是否含学号/院系属待证实项；抓到什么回什么，
  连返回的**字段名清单**（只有键名，没有值）一起回给面板便于后续核对。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...log import get_logger

log = get_logger("chaoxing.profile_read")

# 字段键名候选（按优先级）。同义键在不同校区/版本可能不同，故给候选列表。
_NAME_KEYS = ("username", "realname", "realName", "name")
_NO_KEYS = ("studentno", "studentNo", "stuno", "stuNo", "sno", "studentnum",
            "studentNumber", "number", "userno", "userNo")
_SCHOOL_KEYS = ("schoolname", "schoolName", "school")
_DEPT_KEYS = ("deptname", "deptName", "dept", "college", "faculty", "department")
_CLASS_KEYS = ("classname", "className", "class")


def _pick(info: dict[str, Any], keys: tuple[str, ...]) -> str:
    """按候选键取值；只认真实出现的字符串，绝不猜。"""
    for k in keys:
        for real_k, v in info.items():
            if real_k.lower() == k.lower() and isinstance(v, str) and v.strip():
                return v.strip()
    return ""


def read_profile(state_path: Path, space_url: str, timeout_ms: int = 30_000) -> dict[str, Any]:
    """打开学习空间一次并截获 `stu-base-info`。

    返回 `{"ok", "name", "student_no", "school", "dept", "cls", "fields", "error"}`。
    `fields` 是响应里 userInfo 的**键名清单**（不含值），便于确认平台到底给了什么。
    """
    if not state_path.exists():
        return {"ok": False, "error": "该账号还没有登录会话（先跑一次 login）"}
    from playwright.sync_api import sync_playwright

    from ...core.stealth import LAUNCH_ARGS, hardened_context

    holder: dict[str, Any] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=LAUNCH_ARGS)
        try:
            context = hardened_context(browser, storage_state=str(state_path))
            page = context.new_page()

            def on_response(response) -> None:
                if "stu-base-info" in response.url and "info" not in holder:
                    try:
                        holder["info"] = response.json().get("userInfo") or {}
                    except Exception:  # 解析失败也留个空 dict，避免重复等待
                        holder["info"] = {}

            page.on("response", on_response)
            page.goto(space_url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(3_000)  # 等 stu-base-info 异步返回（与 probe_session 同）
            if "passport2.chaoxing.com" in (page.url or ""):
                return {"ok": False, "error": "会话已失效，请重新登录该账号后再试"}
        except Exception as exc:
            log.warning("读取平台画像失败：%r", exc)
            return {"ok": False, "error": f"打开学习空间失败：{exc}"}
        finally:
            try:
                browser.close()
            except Exception:
                pass

    info = holder.get("info") or {}
    if not info:
        return {"ok": False, "error": "页面没有返回 stu-base-info（可能改版或未登录）"}
    out = {"ok": True, "name": _pick(info, _NAME_KEYS),
           "student_no": _pick(info, _NO_KEYS), "school": _pick(info, _SCHOOL_KEYS),
           "dept": _pick(info, _DEPT_KEYS), "cls": _pick(info, _CLASS_KEYS),
           "fields": sorted(str(k) for k in info.keys())}
    log.info("平台画像读取：字段 %s；命中 %s", out["fields"],
             [k for k in ("name", "student_no", "school", "dept", "cls") if out[k]])
    return out