"""启动第二平台面板（带 `__main__` 保护，供后台/双击/脚本调用）。

## 为什么不直接 `xxt gxks-panel`

面板默认开 **热重载**（`panel.reload`，2026-11-03 用户定：改代码不用手动重启）。
uvicorn 的 reload 走 multiprocessing 的 **spawn** 模式，子进程会**重新 import 入口模块**：

- 入口是 `xxt.exe`（console_scripts 生成的 wrapper）时，`__main__` 里没有
  `if __name__ == "__main__":` 保护 → 子进程递归启动自己 → 报
  `forgotten to use the proper idiom in the main module`（2026-11-03 实测踩到）。
- 本文件有保护 → 可以安全地开 reload。

## 用法

    # 前台（Ctrl+C 停止）
    .venv\\Scripts\\python.exe scripts\\run_panel.py

    # 后台（PowerShell）
    Start-Process -FilePath ".venv\\Scripts\\python.exe" `
      -ArgumentList "scripts\\run_panel.py" -WorkingDirectory "E:\\xxt-gxks" `
      -WindowStyle Hidden `
      -RedirectStandardOutput "data\\logs\\panel.out.log" `
      -RedirectStandardError  "data\\logs\\panel.err.log"
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _main() -> int:
    src = ROOT / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    from xxt.cli import main as cli_main
    return cli_main(["gxks-panel"])


if __name__ == "__main__":
    raise SystemExit(_main())
