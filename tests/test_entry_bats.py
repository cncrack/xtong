"""根目录入口脚本（`开面板.bat` / `验收自检.bat`）的静态断言。

为什么要给 bat 写测试（2026-10-30 实测踩到）：bat 里写中文 + `rem` 注释，
在 GBK 控制台下会**逐行报 `'xx' is not recognized as an internal or external command`**，
而且 `timeout /t` 在无控制台环境（CI / 被管道接走）会挂住不返回。
所以钉死三件事：

① 正文（`echo` 出去的文字）**只用 ASCII**——中文说明一律放 Python 侧或注释；
② 不用 `rem` 注释，用 `::`（`rem` 后面跟中文在 GBK 下会被拆成命令）；
③ 不用 `timeout /t`（无控制台时挂住），改用 `exit /b`。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BATS = ["开面板.bat", "验收自检.bat"]


@pytest.mark.parametrize("name", BATS)
def test_bat_exists(name: str) -> None:
    assert (ROOT / name).is_file(), f"根目录缺入口脚本 {name}"


@pytest.mark.parametrize("name", BATS)
def test_bat_switches_to_utf8_and_cds_to_own_dir(name: str) -> None:
    text = (ROOT / name).read_text(encoding="utf-8")
    assert "chcp 65001" in text, "bat 必须先切 UTF-8，否则中文/emoji 输出乱码"
    assert 'cd /d "%~dp0"' in text, "bat 必须先 cd 到自身目录，双击才不受 cwd 影响"


@pytest.mark.parametrize("name", BATS)
def test_bat_echo_lines_are_ascii_only(name: str) -> None:
    """`echo` 出去的行不能有非 ASCII：GBK 控制台下会逐行报“不是内部或外部命令”。"""
    bad: list[str] = []
    for line in (ROOT / name).read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped.lower().startswith("echo"):
            continue
        if any(ord(ch) > 127 for ch in stripped):
            bad.append(stripped)
    assert not bad, f"{name} 有非 ASCII 的 echo 行（GBK 下会报错）：{bad}"


@pytest.mark.parametrize("name", BATS)
def test_bat_avoids_rem_and_timeout(name: str) -> None:
    text = (ROOT / name).read_text(encoding="utf-8")
    # `rem` 后面跟中文在 GBK 下会被当命令；用 `::`
    assert not re.search(r"(?mi)^\s*rem\b", text), f"{name} 用了 rem 注释，请改 ::"
    # `timeout /t` 在无控制台环境会直接退出/挂住
    assert "timeout /t" not in text, f"{name} 用了 timeout /t，无控制台时会挂住"


def test_panel_bat_guards_against_double_start() -> None:
    """端口已在监听时不要再起第二个（WinError 10048，用户会以为坏了）。"""
    text = (ROOT / "开面板.bat").read_text(encoding="utf-8")
    assert "8301" in text
    assert ":already_running" in text
    assert "TcpClient" in text, "要用 TCP 探活判断端口是否已在监听"


def test_panel_bat_starts_panel_in_its_own_window() -> None:
    """面板必须独立窗口跑：本窗口退出不该把面板带走。"""
    text = (ROOT / "开面板.bat").read_text(encoding="utf-8")
    assert 'start "XXT GXKS panel"' in text
    assert "xxt.cli gxks-panel" in text


def test_check_bat_skips_node_when_missing() -> None:
    """没装 node 的机器不该卡在 node --check 上（pytest 里已有页面静态断言兜底）。"""
    text = (ROOT / "验收自检.bat").read_text(encoding="utf-8")
    assert "where node" in text
    assert "[SKIP]" in text
    assert "pytest" in text
