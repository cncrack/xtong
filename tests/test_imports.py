"""全模块导入冒烟测试（P0-1 教训，2026-10-25）。

背景：`session/login.py` 的缩进错误因「函数内延迟导入 + 无测试覆盖」，
43 个测试全绿仍漏到分发 exe，`xxt login` 必崩。
本测试遍历 xxt 包内所有模块逐个导入，任何语法/导入错误都会在此暴露。
"""

from __future__ import annotations

import importlib
import pathlib
import pkgutil

import xxt


def test_all_modules_importable() -> None:
    failed: list[str] = []
    for info in pkgutil.walk_packages(xxt.__path__, prefix="xxt."):
        try:
            importlib.import_module(info.name)
        except Exception as exc:  # noqa: BLE001 - 冒烟测试需收集全部失败项
            failed.append(f"{info.name}: {exc!r}")
    assert not failed, "以下模块导入失败：\n" + "\n".join(failed)


def test_package_loaded_from_this_repo() -> None:
    """导入的 xxt 必须来自本仓库 src（P0-2 教训：venv 曾指向迁移前的旧目录）。"""
    root = pathlib.Path(__file__).resolve().parents[1]
    loaded = pathlib.Path(xxt.__file__).resolve()
    assert loaded.is_relative_to(root / "src"), (
        f"xxt 实际加载自 {loaded}，不是本仓库的 {root / 'src'}"
        "（editable 安装指向了别的副本，测试结果不可信）"
    )
