"""从平台读取画像（best-effort）：姓名 / 学号 / 学校 / 院系 / 班级。

纪律：
- **一次访问**：只打开学习空间一次。优先复用页面自身发出的 `stu-base-info`；
  若该接口不存在（**2026-10-26 实测：新版个人空间已不再发此接口**），改为读**同一页 DOM**，
  仍然**零额外请求**。
- **绝不猜**：只取结构明确的位置，取不到就留空，绝不按学号反推入学年份或编专业。
- **平台细节只在本文件**：URL 与字段键名/选择器全部收在这里（AGENTS 硬性约定）。
- 实测结构（2026-10-26 会话 40）：`i.chaoxing.com/base` 的 `.header` 首行=学校名、
  `.user`=姓名；该页**没有**院系/班级/学号，也没有个人资料入口链接 → 这三项常为空，
  需人工补或后续另找页面。
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
# 学校名后缀（用于 DOM 兜底时的“像不像学校”判定，宁漏不错）
_SCHOOL_SUFFIX = ("学院", "大学", "学校", "分校", "校区", "职院", "中学")


def _looks_like_school(text: str) -> bool:
    """首行像学校名：4~30 字、无数字、以院校后缀结尾。宁可漏判，绝不误判。"""
    t = (text or "").strip()
    if not (4 <= len(t) <= 30) or "\n" in t:
        return False
    if any(ch.isdigit() for ch in t):
        return False
    return t.endswith(_SCHOOL_SUFFIX)


def _parse_space_dom(header_text: str, user_text: str) -> dict[str, str]:
    """从个人空间 DOM 取 学校/姓名（2026-10-26 实测新版首页结构）。

    `.header` 形如「XX学院⏎输入邀请码⏎姓名」，只取其中**唯一像学校名**的一行；
    `.user` 是姓名。取不到就返回空串——不猜、不用学号反推。
    """
    school = next((ln.strip() for ln in (header_text or "").splitlines()
                   if _looks_like_school(ln)), "")
    raw = (user_text or "").strip()
    name = raw.splitlines()[0].strip() if raw else ""
    if name and (len(name) > 20 or any(ch.isdigit() for ch in name)):
        name = ""
    return {"school": school, "name": name}


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
            # 新版个人空间不再发 stu-base-info → 读同一页 DOM 兜底（仍零额外请求）
            try:
                dom = page.evaluate(
                    """() => {const h=document.querySelector('.header'),
                        u=document.querySelector('.user');
                        return {header:h?(h.innerText||''):'', user:u?(u.innerText||''):''};}""")
            except Exception:
                dom = {}
            holder["dom"] = dom or {}
        except Exception as exc:
            log.warning("读取平台画像失败：%r", exc)
            return {"ok": False, "error": f"打开学习空间失败：{exc}"}
        finally:
            try:
                browser.close()
            except Exception:
                pass

    info = holder.get("info") or {}
    dom = holder.get("dom") or {}
    dom_got = _parse_space_dom(dom.get("header", ""), dom.get("user", ""))
    if not info and not any(dom_got.values()):
        return {"ok": False,
                "error": "页面既没有 stu-base-info，也没读到学校/姓名（可能改版或未登录）"}
    # 接口优先、DOM 兜底；两者都没有的字段一律留空（院系/班级/学号该页不给）
    out = {"ok": True,
           "name": _pick(info, _NAME_KEYS) or dom_got["name"],
           "student_no": _pick(info, _NO_KEYS),
           "school": _pick(info, _SCHOOL_KEYS) or dom_got["school"],
           "dept": _pick(info, _DEPT_KEYS), "cls": _pick(info, _CLASS_KEYS),
           "source": "stu-base-info" if info else "space-dom"}
    out["fields"] = (sorted(str(k) for k in info.keys()) if info
                     else ["dom:.header", "dom:.user"])
    log.info("平台画像读取（来源 %s）：字段 %s；命中 %s", out["source"], out["fields"],
             [k for k in ("name", "student_no", "school", "dept", "cls") if out[k]])
    return out