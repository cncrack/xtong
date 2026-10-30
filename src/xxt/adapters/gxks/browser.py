"""第二平台的浏览器后端：一个入口，两种起法（P1，2026-10-30）。

为什么要有这层：本机与容器的起浏览器方式**根本不同**，但上层脚本（读列表、刷课、
做练习）不该因此出现两套代码。

- `cdp`（默认，本机）：连人已经起好的 Chrome（`--remote-debugging-port`）。
  窗口看得见，人工能点「交卷」；代价是必须有外部 Chrome，且**容器里连不上**
  （容器内的 127.0.0.1 是容器自己，连不到宿主机那个 Chrome）。
- `managed`（容器 / 服务器）：程序自己 `launch_persistent_context`，profile 落
  `data/profiles/<代号>`。不依赖外部 Chrome、不暴露调试端口；代价是无头，
  没人能人工点交卷 —— 交卷只能走 `auto_submit` 或面板二次确认（PLAN-GXKS §13.2）。
  无头 UA 必须显式设成真实 Chrome（否则带 `HeadlessChrome`），见 `_launch_managed()`。

配置：`[gxks] browser_backend = "cdp" | "managed"`，缺省 `cdp`（与 P1 之前行为一致）；
`managed` 是否无头由 `[gxks] managed_headless`（缺省 true）决定。

⚠ `managed` 第一次跑时 profile 是空的 = 未登录：`gxks_sections` / `gxks_work` 会走
自动重登（要求 `data/account.local.json` 有凭据），`run_gxks` 不会（它只在掉线时重登）
—— 容器首跑的登录引导属于 P3，别把「未登录」误判成「后端坏了」。

**Q31 不变**：不管哪种后端，干完活都要 `Session.close()`。Playwright 连着时，人工点
「交卷」弹的 confirm 会被自动 dismiss，人就交不了卷了。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...core.browser import persistent_launch_kwargs
from ...core.stealth import STEALTH_JS, context_kwargs
from . import accounts, selectors

_log = logging.getLogger(__name__)

DEFAULT_BACKEND = "cdp"
BACKENDS = ("cdp", "managed")
PROFILE_ROOT = "profiles"          # 落在 data/profiles/<代号>


class SessionError(RuntimeError):
    """起不了浏览器 / 找不到可用页面。

    `code` 给调用方决定提示与退出码，`hint` 是给人看的一句话（可为空）：
    `connect` 连不上 CDP、`no_page` 连上了没页面、`browser` 起不来、`label` 代号非法、
    `navigate` 打开首页失败。
    """

    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.hint = hint


@dataclass
class Session:
    """一个已就绪的浏览器会话。谁开谁关（`close()` 幂等）。"""

    backend: str
    page: Any = None
    context: Any = None        # managed 才有（持久化上下文）
    browser: Any = None        # cdp 才有
    _playwright: Any = field(default=None, repr=False)

    @property
    def managed(self) -> bool:
        """是否程序自己起的浏览器（容器形态）。"""
        return self.backend == "managed"

    def close(self) -> None:
        """断开（cdp）或关掉（managed）浏览器，并停掉 Playwright。

        先断浏览器再停 Playwright：断连是 Q31 的硬要求，不能让 `stop()` 失败把它挡掉；
        任何一步失败都只吞掉——收尾出错不该盖住调用方真正的报错。
        """
        for target, method in ((self.context, "close"), (self.browser, "close"),
                               (self._playwright, "stop")):
            if target is None:
                continue
            try:
                getattr(target, method)()
            except Exception:                              # noqa: BLE001  收尾不背主错
                pass
        self.page = self.context = self.browser = self._playwright = None


def _gxks_cfg(settings: Any) -> dict[str, Any]:
    raw = getattr(settings, "raw", None)
    cfg = raw.get("gxks") if isinstance(raw, dict) else None
    return cfg if isinstance(cfg, dict) else {}


def backend_of(settings: Any) -> str:
    """读配置决定用哪种后端；拼错的值回落 `cdp` 并告警（不因为一个字段起不来）。"""
    name = str(_gxks_cfg(settings).get("browser_backend", DEFAULT_BACKEND) or "").strip().lower()
    if name not in BACKENDS:
        if name:
            _log.warning("gxks.browser_backend=%r 不认识（可选 %s），按 %s 处理",
                         name, "/".join(BACKENDS), DEFAULT_BACKEND)
        return DEFAULT_BACKEND
    return name


def data_dir_of(settings: Any) -> Path:
    path = getattr(settings, "data_dir", None)
    return Path(path) if path is not None else Path("data")


def profile_dir(settings: Any, label: str) -> Path:
    """`managed` 后端每账号独立的 profile 目录。

    目录名直接用账号代号：**不能混用、不能随手清** —— 登录态和续播点都在里面
    （平台把续播位置存 cookie，见平台档案 §2.10 #7）。代号非法一律拒，绝不回落成
    某个默认目录，否则两个账号会共用一份登录态。
    """
    key = str(label or "").strip().lower()
    if not accounts.is_valid_label(key):
        raise SessionError("label",
                           f"账号代号 {label!r} 非法：只允许 a-z0-9_- 且 ≤24 字符",
                           "用账号表里的 label（data/accounts.local.json）")
    return data_dir_of(settings) / PROFILE_ROOT / key


def _start_playwright() -> Any:
    """薄封装：只为让单测能替身，真实现就是 `sync_playwright().start()`。"""
    from playwright.sync_api import sync_playwright

    return sync_playwright().start()


def cdp_profile(settings: Any, label: str, acc: Any = None) -> Path:
    """本机 cdp 形态给 Chrome 用哪个 `--user-data-dir`。

    账号表里显式配了 `profile` 就用它（老环境、想复用既有 Chrome 档案的人）；
    没配则落到 `data/profiles/<代号>` —— **与 managed 后端同一个目录**，于是
    “本机开着窗口人工登录一次，容器里直接接着跑”成了同一个 profile，不用再登一次。

    刻意**不**回落到系统默认 Chrome 档案（`%LOCALAPPDATA%/Google/Chrome/User Data`）：
    那个目录平时被日常 Chrome 占着，带调试端口去开只会给老进程开个新标签、端口起不来
    （见 `chrome.py` 开头的坑），而且把自动化登录态混进日常档案。
    """
    explicit = str(getattr(acc, "profile", "") or "").strip()
    return Path(explicit) if explicit else profile_dir(settings, label)


def _pick_list_page(browser: Any) -> Any:
    """挑出含课程列表帧的页面（与 `scripts/watch_gxks.connect_page` 同一判据）。"""
    for ctx in browser.contexts:
        for page in ctx.pages:
            try:
                frames = page.frames
            except Exception:                              # noqa: BLE001  页面正在关时取帧会抛
                continue
            if any(selectors.LIST_PATH in (f.url or "") for f in frames):
                return page
    return None


def _pick_any_page(browser: Any) -> Any:
    """随便一个页面（“会话检查 / 登录”用：停在登录页时它**没有列表帧**）。"""
    for ctx in browser.contexts:
        for page in ctx.pages:
            return page
    return None


def _open_cdp(settings: Any, port: int = 0, require_list: bool = True) -> Session:
    """连本机现成的 Chrome。cdp 下**不导航**：人已经开好页面，脚本沿用，避免打断人工操作。"""
    from .accounts import DEFAULT_PORT

    cdp_port = int(port or _gxks_cfg(settings).get("port_base") or DEFAULT_PORT)
    url = f"http://127.0.0.1:{cdp_port}"
    pw = _start_playwright()
    try:
        browser = pw.chromium.connect_over_cdp(url)
    except Exception as exc:                               # noqa: BLE001
        try:
            pw.stop()
        except Exception:                                  # noqa: BLE001
            pass
        raise SessionError(
            "connect", f"连不上 CDP {url}：{exc}",
            f"先起浏览器：chrome.exe --remote-debugging-port={cdp_port} "
            f'--user-data-dir="<该账号专用目录>"',
        ) from exc
    page = _pick_list_page(browser) if require_list else _pick_any_page(browser)
    if page is None:
        session = Session("cdp", browser=browser, _playwright=pw)
        session.close()                                    # Q31：连上了没页面也要立刻断
        raise SessionError("no_page", "连上了但找不到平台页面（Chrome 是否已登录并打开学习入口？）",
                           "先在浏览器里登录并打开课程列表页，再重跑")
    return Session("cdp", page=page, browser=browser, _playwright=pw)


def _launch_managed(pw: Any, path: Path, settings: Any, headless: bool) -> Any:
    """起持久化上下文（返回 `BrowserContext`）。

    **刻意不用 `core.browser.persistent_launch`**：它只传 headless + LAUNCH_ARGS，
    不设 UA → 无头下 `navigator.userAgent` 里是 `HeadlessChrome/156…`（2026-10-30
    干跑实测），站点一眼看出自动化。这里改用同模块的 `persistent_launch_kwargs`
    （纯参数组装）+ `core.stealth.context_kwargs` 补真实 UA/语言/视口，再注入
    `STEALTH_JS` —— 指纹面与超星侧保持一致，且不用改 `persistent_launch` 本身。
    """
    kw = context_kwargs(persistent_launch_kwargs(path, settings, headless=headless))
    ctx = pw.chromium.launch_persistent_context(**kw)
    ctx.add_init_script(STEALTH_JS)
    return ctx


def _open_managed(settings: Any, *, label: str, start_url: str | None,
                  headless: bool | None) -> Session:
    """程序自己起一个带 profile 的浏览器（容器 / 服务器形态）。"""
    path = profile_dir(settings, label)
    if headless is None:
        headless = bool(_gxks_cfg(settings).get("managed_headless", True))
    pw = _start_playwright()
    try:
        ctx = _launch_managed(pw, path, settings, bool(headless))
    except Exception as exc:                               # noqa: BLE001
        try:
            pw.stop()
        except Exception:                                  # noqa: BLE001
            pass
        raise SessionError(
            "browser", f"起浏览器失败（profile={path.name}，headless={bool(headless)}）：{exc}",
            "无头机器先装内核：playwright install chromium（容器镜像里别漏这一步）",
        ) from exc
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    if start_url:
        try:
            page.goto(start_url, wait_until="commit", timeout=60000)
        except Exception as exc:                           # noqa: BLE001
            session = Session("managed", context=ctx, _playwright=pw)
            session.close()
            raise SessionError("navigate", f"打开 {start_url} 失败：{exc}") from exc
    return Session("managed", page=page, context=ctx, _playwright=pw)


def open_session(
    settings: Any,
    *,
    label: str = "",
    port: int = 0,
    start_url: str | None = None,
    headless: bool | None = None,
    require_list: bool = True,
) -> Session:
    """按配置开一个可用会话。

    - `label`：账号代号，`managed` 必须给（决定 profile 目录），`cdp` 可空；
    - `port`：CDP 端口，0 = 配置 `[gxks] port_base`（再没有就 9222）；
    - `start_url`：`managed` 起完浏览器后打开的地址（`cdp` 忽略——不打断人工已开的页面）；
    - `headless`：仅 `managed` 生效，None = 配置 `[gxks] managed_headless`（缺省 true）；
    - `require_list`：cdp 下是否只接“含课程列表帧”的页面。**会话检查/登录要传 False** ——
      停在登录页的浏览器没有列表帧，按默认判据会被当成“找不到页面”。
    """
    if backend_of(settings) == "managed":
        return _open_managed(settings, label=label, start_url=start_url, headless=headless)
    return _open_cdp(settings, port=port, require_list=require_list)
