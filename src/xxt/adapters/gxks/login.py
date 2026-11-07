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
import time
from pathlib import Path
from typing import Any

from . import selectors
from .driver import at_login_page

# 占位符前缀：模板里写的是“在这里填学号 / 在这里填密码”
_PLACEHOLDER_HINTS = ("在这里填", "填学号", "填密码", "TODO", "your_")

DEFAULT_CRED_PATH = Path("data") / "account.local.json"


def credentials_path(settings: Any) -> Path:
    """凭据文件位置：跟着 `settings.data_dir` 走，不用相对 CWD 的默认值。

    为什么必须这样：面板/脚本的 CWD 不一定是项目根，测试也会把 `XXT_ROOT` 指到临时
    目录 —— 用相对路径就会去读到**真实凭据**（或反向：该读的读不到）。
    """
    data = getattr(settings, "data_dir", None)
    return (Path(data) if data is not None else Path("data")) / "account.local.json"


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


def _clean(value: Any) -> str:
    """去空白；占位符一律当空（与 `load_credentials` 同一判据）。

    ⚠ 只认 `str`：调用方可能传进来一个非 Account 对象（测试替身、鸭子类型），
    对它 `getattr(acc, "name")` 会拿到任意对象 —— `str()` 出来非空，就会被误判
    「已配置凭据」，进而拿垃圾去撞登录。
    """
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text or any(h in text for h in _PLACEHOLDER_HINTS):
        return ""
    return text


def load_credentials_for(acc: Any, settings: Any = None) -> dict[str, str] | None:
    """**按账号**取凭据。返回 `{"name","pw"}` 或 None（= 未配置，调用方按"掉线即停"处理）。

    ## 为什么必须有（2026-11-07 查出的 P0，多账号下会顶号）

    原来 `run_gxks.py` / `gxks_sections.py` / `gxks_work.py` / `gxks_session_check.py`
    全都是 `load_credentials()` —— **无参**，读的是单账号时代的全局
    `data/account.local.json`。

    多账号同时跑时后果很严重：**账号 B 掉登录 → 脚本在 B 的浏览器里填 A 的账号密码**
    → A 被顶号（A 那边正在播的会话断）、B 还是没登上，而且 A 的登录态可能被写进
    B 的快照文件。这正是「多账号」最该防的事，却因为这段代码从来没在多账号下
    掉过登录，一直没被发现。

    而账号表里本来就有 per-account 的 `name`/`pw`（`Account.configured`），
    只是脚本侧从来没读过。

    ## 回落规则（这条是安全边界，别放宽）

    - 账号自己填了 `name`+`pw` → 用它；
    - 没填 → **只在「账号表里只有一个账号」时**才回落全局旧文件；
    - 多账号（≥2 条）时**一律不回落到全局文件**，返回 None。

    为什么多加这个限制：第一版我写成「没填就回落」，结果 B 没配凭据时仍然会拿
    全局里的 A 的凭据去登 —— 等于把刚修的 P0 又补回来了（测试当场抓到）。
    全局文件是**单账号时代**的遗留，多账号下它归属于哪个号是**不可知**的，
    唯一安全的做法是不用。
    """
    name = _clean(getattr(acc, "name", ""))
    pw = _clean(getattr(acc, "pw", ""))
    if name and pw:
        return {"name": name, "pw": pw}
    if _single_account(settings):
        try:
            path = credentials_path(settings)
        except Exception:                                  # noqa: BLE001
            path = DEFAULT_CRED_PATH
        return load_credentials(path)
    return None


def _single_account(settings: Any) -> bool:
    """账号表里是不是**只有**一个账号（只有这种场景才允许回落全局凭据文件）。

    ⚠ 必须按 `settings.data_dir` 读，不能用 `accounts.load_accounts()` 的默认路径 ——
    后者跟的是 `find_project_root()`（环境变量/仓库根），与 `settings` 可能不是同一个
    目录（测试、`XXT_ROOT` 指到别处时就是这种情况）。用错目录的后果是**读成真实
    项目里的账号表**，把「多账号」误判成「单账号」→ 回落全局凭据 → 又变成顶号。

    读不到账号表（`settings` 为 None、文件缺、异常）时返回 **False**：
    宁可"未配置"（掉线即停等人工），也不能在一个不认识的账号上试一份来源不明的凭据。
    """
    if settings is None:
        return False
    try:
        from . import accounts as gaccounts

        data = Path(settings.data_dir)
        rows = gaccounts.load_accounts(data / "accounts.local.json",
                                       data / "account.local.json")
        return len(rows) == 1
    except Exception:                                      # noqa: BLE001
        return False


def is_configured_for(acc: Any, settings: Any = None) -> bool:
    """该账号能否自动重登（按账号判，不看全局文件）。"""
    return load_credentials_for(acc, settings) is not None


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


def _find_frame(page: Any, *, wait_ms: int = 0) -> Any | None:
    """表单可能在主文档也可能在 iframe 里，按 `#frmLogin` 是否存在找。

    ⚠ **必须能等**（2026-11-03 实测的坑）：登录页 `Loginb.aspx` 是**异步渲染**的，
    `goto` 到 `load` 之后 2 秒内 `document` 里还一个 input 都没有
    （实测 `input=0`、`bodyLen=1409`，要 5 秒后才出现 `#frmLogin`）。
    原来这里只 `query_selector` 一次就返回 None → `login()` 直接判 `no_form`，
    表现是「自动重登永远失败，但手动在那台浏览器里登录完全正常」。

    `wait_ms > 0` 时轮询等待（100ms 一次），让调用方能用上真正可靠的判定。
    """
    deadline = time.monotonic() + max(0, wait_ms) / 1000.0
    while True:
        for frame in [page, *page.frames]:
            try:
                if frame.query_selector(f"#{selectors.LOGIN_FORM_ID}") is not None:
                    return frame
            except Exception:                              # noqa: BLE001  帧在导航时会抛
                continue
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.1)


def login(page: Any, cred: dict[str, str], *, settle_ms: int = 4000,
          wait_ms: int = 30000, form_wait_ms: int = 15000) -> dict[str, Any]:
    """在已打开的登录页上走正常流程登录。

    `form_wait_ms`：等 `#frmLogin` 出现的最长时间。登录页是异步渲染的
    （实测要 5 秒才出现表单），不能只探一次就判 `no_form`。

    返回 `{"result": "ok|need_verify|no_form|failed", ...}`，**不含凭据值**。
    """
    if is_need_verify(page.url):
        return {"result": "need_verify", "reason": "url_need_verify"}

    frame = _find_frame(page, wait_ms=form_wait_ms)
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
