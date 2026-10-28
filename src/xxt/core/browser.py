"""浏览器启动的唯一入口（Q36，2026-10-28）。

为什么需要这个模块：`[stealth] headless` 以前是**死配置**——`config/settings.toml`
里写了，全仓没有任何代码读它；12 处 `p.chromium.launch(headless=True, args=LAUNCH_ARGS)`
各自写死布尔值，面板上改不动、改了也不生效（与 Q43 的 `accounts.priority` 同类）。

本模块只收口三件事，反检测细节仍留在 `core/stealth.py`：

1. **无头（headless）怎么定**：显式参数（必须让人看见的场景）> 配置 `[stealth].headless`
   > 内置默认 `True`。三级优先，任何一层缺失都安全回退；
2. **launch 参数统一**（`LAUNCH_ARGS`，可选追加）；
3. **同步 / 异步两个入口**，调用方不必各写一遍。

为什么不把 UA 也做成配置项：UA 必须与内核版本一致（见 `stealth.py` 顶部注释），
随手一改等于把“自动化特征”写回脸上；要跟内核版本走，只能在 `stealth.py` 里改常量。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .stealth import LAUNCH_ARGS


def _raw_config(settings: Any | None) -> dict[str, Any]:
    """取配置原始 dict：优先用调用方已有的 `Settings`（省一次读库），否则自己读一次。"""
    raw = getattr(settings, "raw", None) if settings is not None else None
    if isinstance(raw, dict):
        return raw
    try:
        from ..config import Settings

        return Settings().raw
    except Exception:  # 配置读不出来不该阻断启动，回退内置默认
        return {}


def resolve_headless(settings: Any | None = None, override: bool | None = None) -> bool:
    """决定这次要不要无头。

    override 是“这次场景必须这样”，优先级最高：
    - 人工登录：`override=False`（必须让人看见窗口）；
    - 半自动作业核对：`override=True`（用户要对着窗口改答案）。
    没给 override 时跟随配置 `[stealth].headless`，再没有就默认 `True`。
    """
    if override is not None:
        return bool(override)
    stealth = _raw_config(settings).get("stealth")
    if not isinstance(stealth, dict) or "headless" not in stealth:
        return True
    return bool(stealth["headless"])


def launch_kwargs(
    settings: Any | None = None,
    *,
    headless: bool | None = None,
    extra_args: list[str] | None = None,
) -> dict[str, Any]:
    """组装 `chromium.launch()` 的关键字参数（同步/异步共用，便于单测）。"""
    args = list(LAUNCH_ARGS)
    for extra in extra_args or []:
        if extra not in args:
            args.append(extra)
    return {"headless": resolve_headless(settings, headless), "args": args}


def launch(
    playwright: Any,
    settings: Any | None = None,
    *,
    headless: bool | None = None,
    extra_args: list[str] | None = None,
) -> Any:
    """同步启动浏览器（`playwright` 是 `sync_playwright()` 返回的对象）。"""
    return playwright.chromium.launch(
        **launch_kwargs(settings, headless=headless, extra_args=extra_args)
    )


async def async_launch(
    playwright: Any,
    settings: Any | None = None,
    *,
    headless: bool | None = None,
    extra_args: list[str] | None = None,
) -> Any:
    """异步启动浏览器（`playwright` 是 `async_playwright()` 返回的对象）。"""
    return await playwright.chromium.launch(
        **launch_kwargs(settings, headless=headless, extra_args=extra_args)
    )


def persistent_launch_kwargs(
    profile_dir: str | Path,
    settings: Any | None = None,
    *,
    headless: bool | None = None,
    extra_args: list[str] | None = None,
    match_screen: bool = False,
) -> dict[str, Any]:
    """组装 `chromium.launch_persistent_context()` 的参数（第二平台默认形态，PLAN §11.1）。

    与 `launch_kwargs` 的三点差别：

    1. **必须给 `user_data_dir`**：每账号独立 profile，登录态、cookie、续播 cookie
       全部落在里面——目标站点的续播点就存在 cookie（见平台档案 §2.10 #7），
       所以**profile 目录不能混用、不能随手清**；
    2. **`match_screen=True` 用 `no_viewport`**：让视口跟随真实窗口。写死尺寸的风险
       来自“按屏幕宽度分流登录页”的历史传闻（实测 1920 宽仍落 `Login.aspx`，分流规则
       未证实），但写死视口本身就会让 `screen.width` 与窗口对不上，属于自造破绽；
    3. **目录不存在就建**：Playwright 会自己建，但失败时报错含糊，容易被误判成
       “站点拒绝访问”，先把目录建好能把问题定位在正确的地方。
    """
    kwargs = launch_kwargs(settings, headless=headless, extra_args=extra_args)
    path = Path(profile_dir).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    kwargs["user_data_dir"] = str(path)
    if match_screen:
        kwargs["no_viewport"] = True
    return kwargs


def persistent_launch(
    playwright: Any,
    profile_dir: str | Path,
    settings: Any | None = None,
    *,
    headless: bool | None = None,
    extra_args: list[str] | None = None,
    match_screen: bool = False,
) -> Any:
    """同步启动持久化上下文（返回 `BrowserContext`，不是 `Browser`）。"""
    return playwright.chromium.launch_persistent_context(
        **persistent_launch_kwargs(
            profile_dir,
            settings,
            headless=headless,
            extra_args=extra_args,
            match_screen=match_screen,
        )
    )


async def async_persistent_launch(
    playwright: Any,
    profile_dir: str | Path,
    settings: Any | None = None,
    *,
    headless: bool | None = None,
    extra_args: list[str] | None = None,
    match_screen: bool = False,
) -> Any:
    """异步启动持久化上下文（返回值同样是 `BrowserContext`）。"""
    return await playwright.chromium.launch_persistent_context(
        **persistent_launch_kwargs(
            profile_dir,
            settings,
            headless=headless,
            extra_args=extra_args,
            match_screen=match_screen,
        )
    )
