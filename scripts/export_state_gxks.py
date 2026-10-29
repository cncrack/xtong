"""导出当前浏览器会话（cookie + localStorage）到 `data/storage_state_gxks.json`。

**零请求、零风险**：只是把浏览器里已有的 cookie 读出来落盘，不访问站点、不顶号。

为什么做这件事（Q23）：本站与学习绑定的只有 `ASP.NET_SessionId`（明文）与「节 ID = 秒数」
cookie，没有签名也没有加密 ⇒ **会话持久化 = 存 cookie**，重启 Chrome/进程后不用重登，
比“掉线后自动重登”更根本（重登本身可能撞上验证码闸门）。

用法：

    .venv\\Scripts\\python.exe scripts/export_state_gxks.py

输出文件被 `.gitignore` 的 `storage_state*.json` 覆盖，**不会入库**。
本脚本只打印 cookie 的**名字与数量**，不打印值。
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

from xxt.adapters.gxks import selectors

DEFAULT_OUT = Path("data") / "storage_state_gxks.json"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9222, help="Chrome 调试端口")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="输出文件")
    args = ap.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{args.port}")
        ctx = None
        for c in browser.contexts:
            if any((pg.url or "").startswith(selectors.BASE) for pg in c.pages):
                ctx = c
                break
        if ctx is None:
            ctx = browser.contexts[0] if browser.contexts else None
        if ctx is None:
            print("[FAIL] 没有可用的浏览器上下文")
            return 2

        cookies = [c for c in ctx.cookies() if selectors.BASE.split("//")[1] in (c.get("domain") or "")]
        names = sorted({c.get("name") for c in cookies})
        session_level = [c.get("name") for c in cookies if not c.get("expires") or c.get("expires") == -1]

        ctx.storage_state(path=str(out_path))
        browser.close()

    size = out_path.stat().st_size
    print(f"已导出 → {out_path}（{size} 字节，{datetime.now():%H:%M:%S}）")
    print(f"站点 cookie {len(cookies)} 条：{', '.join(names)}")
    if session_level:
        print(f"其中会话级（关浏览器即失）：{', '.join(sorted(set(session_level)))}")
    print("⚠ 该文件等同登录态，**不要外发、不要入库**；复用方式见 docs/gxks/PLAN-GXKS.md Q23。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
