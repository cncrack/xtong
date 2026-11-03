"""`scripts/start_chrome.ps1` 的**编码与参数**回归。

## 为什么单独守这两条

2026-11-03 这个脚本连踩两次同一个坑：

1. **BOM 丢失** → Windows PowerShell 5.1 按系统 ANSI（中文 GBK）读无 BOM 的 .ps1，
   中文注释变乱码 → **解析失败** → 计划任务返回码 1、**无任何输出**
   （表现是面板报「等了 40 秒，端口 9222 仍未响应」，查不到原因）。
2. **没传启动 URL** → Chrome 停在 `chrome://new-tab-page/`，而刷课脚本要求页面里
   已有 `LibraryStudyList.aspx` 帧（cdp 模式故意不导航）→ 点「开始学习」立刻报
   「连上了但找不到平台页面」。

两个都是**静默失败**，只能靠测试守。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PS1 = ROOT / "scripts" / "start_chrome.ps1"


class TestStartChromeScript(unittest.TestCase):
    def test_file_exists(self):
        self.assertTrue(PS1.exists(), "start_chrome.ps1 不存在")

    def test_has_utf8_bom(self):
        """⚠ 必须带 BOM，否则 PowerShell 5.1 按 GBK 读会解析失败（静默）。"""
        raw = PS1.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"),
                        "start_chrome.ps1 必须存成**带 BOM 的 UTF-8**，"
                        "否则中文注释会让 PowerShell 5.1 解析失败、任务返回码 1 且无输出")

    def test_no_hardcoded_repo_root(self):
        """仓库有多个 worktree（E:\\xxt / E:\\xxt-gxks），不能写死盘符。"""
        text = PS1.read_text(encoding="utf-8-sig")
        self.assertNotIn('$root = "E:', text)
        self.assertIn("$PSScriptRoot", text)

    def test_passes_launch_url(self):
        """必须把 spec 里的 url 拼进 Chrome 命令行，否则停在 new-tab-page。"""
        text = PS1.read_text(encoding="utf-8-sig")
        self.assertIn("$spec.url", text)
        self.assertIn("$argv += $url", text)

    def test_chrome_args_match_adapter(self):
        """⚠ ps1 的 Chrome 参数必须与 `chrome._CHROME_ARGS` 一致。

        2026-11-03 踩到：ps1 只写了前两个，漏了 `--no-proxy-server` 与
        `--disable-blink-features=AutomationControlled` →
        系统代理（本机 10808）把视频 CDN 流量也接过去，m3u8/ts 都 200，
        但**分片解密失败**：播放器永远 `waiting`、`readyState=0`、`buffered=[]`。
        用户体感是「昨天还能播，今天播不了」。
        """
        sys.path.insert(0, str(ROOT / "src"))
        from xxt.adapters.gxks import chrome as gchrome

        text = PS1.read_text(encoding="utf-8-sig")
        missing = [a for a in gchrome._CHROME_ARGS if a not in text]
        self.assertEqual(missing, [],
                         f"start_chrome.ps1 缺少这些 Chrome 参数（会与适配层行为不一致）：{missing}")

    def test_no_proxy_server_present(self):
        """单独守 `--no-proxy-server`：它决定视频能不能解密成功。"""
        text = PS1.read_text(encoding="utf-8-sig")
        self.assertIn("--no-proxy-server", text,
                      "缺少 --no-proxy-server：系统代理会掐断视频分片解密")

    def test_writes_result_file(self):
        text = PS1.read_text(encoding="utf-8-sig")
        self.assertIn("chrome_start.result.json", text)
        self.assertIn("Write-Result", text)

    def test_parses_if_powershell_available(self):
        """有 PowerShell 时做一次真解析（中文系统上能抓到编码问题）。"""
        exe = "powershell.exe"
        try:
            done = subprocess.run(
                [exe, "-NoProfile", "-Command",
                 "$e=$null;$t=$null;"
                 f"[System.Management.Automation.Language.Parser]::ParseFile('{PS1}',"
                 "[ref]$t,[ref]$e)|Out-Null;"
                 "if($e.Count){$e[0].Message}else{'OK'}"],
                capture_output=True, text=True, timeout=60)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            self.skipTest("没有 powershell.exe")
        self.assertIn("OK", done.stdout, f"PowerShell 解析失败：{done.stdout}{done.stderr}")


class TestChromeSpecIncludesUrl(unittest.TestCase):
    """面板写参数文件时**必须带 url**（把缺陷挡在源头）。"""

    def test_spec_has_url_field(self):
        sys.path.insert(0, str(ROOT / "src"))
        from xxt.panel_gxks import app as A
        import inspect
        src = inspect.getsource(A._start_chrome_interactive)
        self.assertIn('"url"', src)
        self.assertIn("MAIN_PAGE", src)


if __name__ == "__main__":
    unittest.main()
