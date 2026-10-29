"""登录与凭据（高校考试网适配层）。

三件事，**都不打印凭据值**：

1. `load_credentials()` 从 `data/account.local.json`（gitignore 内）读账号密码；
   `is_configured()` 负责识别“还是占位符”的情况——占位符一律视为未配置，
   宁可不自动重登，也不能拿“在这里填密码”去撞登录。
2. `is_need_verify()` 判断是不是被推到验证码闸门（`needVerify=1` 或验证码行可见）→ **立即转人工**。
3. `login()` 走**真浏览器正常流程**（填表 + 点登录），不逆向拼 `HidLogin.aspx` 的 POST
   （`gaoxiaokaoshi.md` 对策 7：绕开正常流程是最强机器人特征）。

纪律（PLAN-GXKS §11.3 / D15）：

- 出现验证码 → 停，转人工，**绝不试错**（连错 = 撞库特征，最危险）；
- 每次守播/调度**只登录一次**，不循环重试；
- `screenType` 由服务端渲染，**不碰**；不自洽（报 1440 却走 Login.aspx）反而是自己造的破绽。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import selectors
from .driver import at_login_page

# 占位符前缀：模板里写的是“在这里填学号 / 在这里填密码”
_PLACEHOLDER_HINTS = ("在这里填", "填学号", "填密码", "TODO", "your_")

DEFAULT_CRED_PATH = Path("data") / "account.local.json"


def load_credentials(path: str | Path = DEFAULT_CRED_PATH) -> dict[str, str] | None:
    """读凭据；文件不存在 / 解析失败 / 仍是占位符 → `None`（调用方按“未配置”处理）。"""
    p = Path(path)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:                                      # noqa: BLE001  坏文件等同没配
        return None
    if not isinstance(data, dict):
        return None
    name = str(data.get("name") or "").strip()
    pw = str(data.get("pw") or "").strip()
    if not name or not pw:
        return None
    if any(h in name or h in pw for h in _PLACEHOLDER_HINTS):
        return None
    return {"name": name, "pw": pw}


def is_configured(path: str | Path = DEFAULT_CRED_PATH) -> bool:
    return load_credentials(path) is not None


def is_need_verify(url: str, verify_row_visible: bool = False) -> bool:
    """被推到验证码闸门 → 必须转人工。

    两个信号任一成立即停：URL 带 `needVerify=1`，或验证码行（`trVerify`）真的可见。
    **不猜、不试**——验证码触发原因至今未知（§7.2）。
    """
    return (selectors.NEED_VERIFY_HINT in (url or "")) or bool(verify_row_visible)


def has_auth_ticket(context: Any) -> bool:
    """本地零请求判断“登录票还在不在”（Q20 实测：`AUTH_COOKIE` 就是那张票）。

    价值：每节开播前就能知道下一次请求会不会被踢，**不用等到页面掉登录才发现**。
    但注意：票在 **≠** 服务端一定还认（Q20 假说未定），所以它只能用于“提前告警”，
    不能反过来当作“一定能继续学”的依据。
    """
    try:
        return any(c.get("name") == selectors.AUTH_COOKIE
                   for c in context.cookies(selectors.BASE))
    except Exception:                                      # noqa: BLE001  读不到就当有，别误停
        return True


def _find_frame(page: Any) -> Any | None:
    """表单可能在主文档也可能在 iframe 里，按 `#frmLogin` 是否存在找。"""
    for frame in [page, *page.frames]:
        try:
            if frame.query_selector(f"#{selectors.LOGIN_FORM_ID}") is not None:
                return frame
        except Exception:                                  # noqa: BLE001  帧在导航时会抛
            continue
    return None


def login(page: Any, cred: dict[str, str], *, settle_ms: int = 4000,
          wait_ms: int = 30000) -> dict[str, Any]:
    """在已打开的登录页上走正常流程登录。

    返回 `{"result": "ok|need_verify|no_form|failed", ...}`，**不含凭据值**。
    """
    if is_need_verify(page.url):
        return {"result": "need_verify", "reason": "url_need_verify"}

    frame = _find_frame(page)
    if frame is None:
        return {"result": "no_form", "url": page.url}

    try:
        row = frame.query_selector(f"#{selectors.LOGIN_VERIFY_ROW}")
        if row is not None and row.is_visible():
            return {"result": "need_verify", "reason": "verify_row_visible"}
    except Exception:                                      # noqa: BLE001
        pass

    try:
        frame.fill(f"input[name='{selectors.LOGIN_FIELD_NAME}']", cred["name"])
        frame.fill(f"input[name='{selectors.LOGIN_FIELD_PW}']", cred["pw"])
    except Exception as exc:                                # noqa: BLE001
        return {"result": "failed", "reason": f"fill:{type(exc).__name__}"}

    try:
        btn = frame.query_selector(selectors.LOGIN_SUBMIT_SELECTOR)
        if btn is not None:
            btn.click()
        else:
            frame.press(f"input[name='{selectors.LOGIN_FIELD_PW}']", "Enter")
    except Exception as exc:                                # noqa: BLE001
        return {"result": "failed", "reason": f"submit:{type(exc).__name__}"}

    page.wait_for_timeout(settle_ms)
    waited = settle_ms
    while waited < wait_ms:
        url = page.url or ""
        # 掉回验证码闸门要立刻停，别等着超时
        if is_need_verify(url):
            return {"result": "need_verify", "reason": "redirect_need_verify", "url": url}
        if not at_login_page(url):
            return {"result": "ok", "url": url}
        page.wait_for_timeout(1000)
        waited += 1000
    return {"result": "failed", "reason": "still_on_login", "url": page.url}
