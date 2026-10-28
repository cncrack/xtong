"""Q36（2026-10-28）：浏览器启动统一入口。

背景：`[stealth] headless` 一直是死配置——写了没人读，12 处 launch 各自写死布尔值。
本文件守四件事：三级优先（显式 > 配置 > 默认）、launch 参数一致、
同步/异步两条路都通、**以后不许再绕过工厂直接 launch**。
"""

import asyncio
from pathlib import Path

from xxt.core import browser as bmod
from xxt.core.stealth import LAUNCH_ARGS


class _S:
    """最小的 Settings 替身：只提供 `raw`。"""

    def __init__(self, headless=None):
        self.raw = {} if headless is None else {"stealth": {"headless": headless}}


def test_default_true_without_any_config(monkeypatch):
    """配置里连 [stealth] 都没有 → 默认无头（挂机不弹窗）。"""
    monkeypatch.setattr(bmod, "_raw_config", lambda _s: {})
    assert bmod.resolve_headless() is True


def test_follows_config_value():
    assert bmod.resolve_headless(_S(False)) is False
    assert bmod.resolve_headless(_S(True)) is True


def test_override_beats_config():
    # 人工登录：配置写 true 也必须有头，否则用户看不见登录页
    assert bmod.resolve_headless(_S(True), override=False) is False
    # 半自动作业核对：配置写 false 也要有头
    assert bmod.resolve_headless(_S(False), override=True) is True


def test_missing_stealth_section_defaults_true():
    class _Empty:
        raw = {"quota": {"daily_minutes": 120}}

    assert bmod.resolve_headless(_Empty()) is True


def test_launch_kwargs_carry_stealth_args():
    kw = bmod.launch_kwargs(_S(True))
    assert kw["headless"] is True
    assert kw["args"] == list(LAUNCH_ARGS)


def test_extra_args_appended_without_duplicate():
    kw = bmod.launch_kwargs(_S(True), extra_args=[LAUNCH_ARGS[0], "--lang=zh-CN"])
    assert kw["args"].count(LAUNCH_ARGS[0]) == 1
    assert "--lang=zh-CN" in kw["args"]


class _SyncChromium:
    def __init__(self):
        self.kw = None

    def launch(self, **kw):
        self.kw = kw
        return "browser"


class _SyncPW:
    def __init__(self):
        self.chromium = _SyncChromium()


class _AsyncChromium:
    def __init__(self):
        self.kw = None

    async def launch(self, **kw):
        self.kw = kw
        return "browser"


class _AsyncPW:
    def __init__(self):
        self.chromium = _AsyncChromium()


def test_sync_launch_passes_kwargs():
    pw = _SyncPW()
    assert bmod.launch(pw, _S(True)) == "browser"
    assert pw.chromium.kw["headless"] is True
    assert pw.chromium.kw["args"] == list(LAUNCH_ARGS)


def test_async_launch_passes_kwargs():
    pw = _AsyncPW()
    out = asyncio.run(bmod.async_launch(pw, _S(False)))
    assert out == "browser"
    assert pw.chromium.kw["headless"] is False


def test_no_direct_launch_outside_factory():
    """源码级守护：新增代码若要开浏览器，必须走 core.browser，不许再写死 headless。"""
    root = Path(__file__).resolve().parents[1] / "src" / "xxt"
    offenders = []
    for path in root.rglob("*.py"):
        if path.name == "browser.py":
            continue
        for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "chromium.launch(" in line and not line.lstrip().startswith("#"):
                offenders.append(f"{path.relative_to(root)}:{no}")
    assert offenders == [], f"这些地方绕过了 core.browser：{offenders}"
