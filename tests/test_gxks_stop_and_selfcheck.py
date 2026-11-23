"""2026-11-23：停止面板脚本 + 启动时权限/任务自检。

## 用户的两个痛点

> 「这个命令太长了，执行哪里记得住」
> （贴出：`$p = (Get-CimInstance ... ).ProcessId` → `taskkill /PID $p /T /F`
>   → `错误: 无效语法。需要 '/PID' 的值。`）

根因：`$p` 为空（面板已停），`taskkill /PID  /T /F` 就报语法错；
而且那条命令本来就太长，记不住。

> 「我怎么知道是以管理员还是普通权限启动？」

原来只能靠用户自己去 `whoami /groups` 看完整性级别。

## 本轮的解法

1. `scripts/stop_panel.ps1` + `stop_panel.bat`：一条命令（或双击）停干净，
   先按「谁监听 8301」找（最准），再按命令行兜底，`taskkill /T` 连整棵树。
2. 面板启动时打印**运行权限**（管理员提权 / 普通权限）+
   **哪些账号缺计划任务**，并直接给出下一步该跑什么。
"""

from __future__ import annotations

import unittest
from pathlib import Path

from xxt.panel_gxks import app

ROOT = Path(__file__).resolve().parents[1]
APP_SRC = Path(app.__file__).read_text(encoding="utf-8")


class TestStopScript(unittest.TestCase):
    def setUp(self):
        self.ps1 = ROOT / "scripts" / "stop_panel.ps1"
        self.bat = ROOT / "stop_panel.bat"

    def test_scripts_exist(self):
        self.assertTrue(self.ps1.exists(), "需要一键停止脚本")
        self.assertTrue(self.bat.exists(), "需要可双击的 bat 封装")

    def test_ps1_has_bom(self):
        """Windows PowerShell 5.1 按 GBK 读无 BOM 的 .ps1，中文注释会解析失败。"""
        self.assertEqual(self.ps1.read_bytes()[:3], b"\xef\xbb\xbf")

    def test_finds_pid_by_port_not_only_name(self):
        """按「谁监听 8301」找最准：xxt.exe / python / 重定向器名字都会变。"""
        text = self.ps1.read_text(encoding="utf-8-sig")
        self.assertIn("Get-NetTCPConnection", text)
        self.assertIn("8301", text)
        self.assertIn("OwningProcess", text)

    def test_falls_back_to_commandline(self):
        text = self.ps1.read_text(encoding="utf-8-sig")
        self.assertIn("gxks-panel", text)
        self.assertIn("Win32_Process", text)

    def test_kills_tree(self):
        """`/T` 连带整棵进程树 —— 否则 reloader 会再 spawn worker，停不干净。"""
        text = self.ps1.read_text(encoding="utf-8-sig")
        self.assertIn("taskkill", text)
        self.assertIn("/T", text)
        self.assertIn("/F", text)

    def test_handles_nothing_running(self):
        """面板没跑时不能报错（用户上次 $p 为空导致 taskkill 语法错的同类问题）。"""
        text = self.ps1.read_text(encoding="utf-8-sig")
        self.assertIn("没有找到正在运行的面板", text)
        self.assertIn("exit 0", text)

    def test_verifies_port_released(self):
        """停完要复查端口真的释放了，而不是假装成功。"""
        text = self.ps1.read_text(encoding="utf-8-sig")
        self.assertIn("Start-Sleep", text)
        self.assertIn("已释放", text)

    def test_bat_is_ascii_only(self):
        """bat 用 ascii 写，避免中文在 cmd 里变问号。"""
        raw = self.bat.read_bytes()
        raw.decode("ascii")  # 抛错即失败
        self.assertIn(b"stop_panel.ps1", raw)


class TestPermissionSelfCheck(unittest.TestCase):
    def test_elevated_detector_uses_isuseranadmin(self):
        """判据必须是**提权**（IsUserAnAdmin），不是「属于管理员组」：
        普通管理员组用户双击启动是 Medium 级别，建管理员任务仍会拒绝访问。"""
        seg = APP_SRC.split("def _is_elevated()", 1)[1][:600]
        self.assertIn("IsUserAnAdmin", seg)

    def test_startup_prints_permission(self):
        seg = APP_SRC.split("def _print_permission_and_tasks()", 1)[1][:2500]
        self.assertIn("管理员（已提权）", seg)
        self.assertIn("普通权限（未提权）", seg)

    def test_startup_reports_missing_tasks(self):
        seg = APP_SRC.split("def _print_permission_and_tasks()", 1)[1][:2500]
        self.assertIn("缺少", seg)
        self.assertIn("register_all_chrome_tasks.ps1", seg)

    def test_startup_reports_all_registered(self):
        seg = APP_SRC.split("def _print_permission_and_tasks()", 1)[1][:2500]
        self.assertIn("都已注册", seg)

    def test_serve_invokes_selfcheck(self):
        """serve() 里必须真的调用（只定义不调用等于没做）。"""
        seg = APP_SRC.split("def serve(", 1)[1][:3000]
        self.assertIn("_print_permission_and_tasks()", seg)

    def test_non_windows_safe(self):
        """非 Windows 直接返回，不抛错（Linux 上 CI 能过）。"""
        seg = APP_SRC.split("def _print_permission_and_tasks()", 1)[1][:400]
        self.assertIn('if os.name != "nt":', seg)
        self.assertIn("return", seg)


if __name__ == "__main__":
    unittest.main()
