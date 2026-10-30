"""导出当前浏览器会话（cookie + localStorage）到该账号的登录态快照。

**零请求、零风险**：只把浏览器里已有的 cookie 读出来落盘，不访问站点、不顶号。

为什么必须存文件（Q23 / 2026-10-30 实测）：本站 4 条 cookie（`.ASPXAUTH` /
`ASP.NET_SessionId` / `Clerk` / `ZYLTheme`）**全是会话级**（`expires=-1`），Chrome 一关
就全没、profile 目录里留不下东西 ⇒ **会话持久化只能靠这份 json**，被踢或重启后再靠
`browser.restore_cookies()` 注入回来（面板「恢复登录」按钮就是干这个的）。

文件名由 `browser.state_path()` 统一决定（`data/storage_state_gxks_<代号>.json`）：
面板、脚本、恢复逻辑必须**用同一个名字**，否则会出现“存了却找不到”的假故障。

用法：
    .venv\\Scripts\\python.exe scripts/export_state_gxks.py --label default

输出文件被 `.gitignore` 的 `storage_state*.json` 覆盖，**不会入库**（`push_check` 也会拦）。
本脚本只打印 cookie 的**名字与数量**，不打印值。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from xxt.adapters.gxks import accounts as gaccounts  # noqa: E402
from xxt.adapters.gxks import browser as gbrowser    # noqa: E402
from xxt.adapters.gxks import selectors              # noqa: E402
from xxt.config import Settings, find_project_root   # noqa: E402


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="", help="账号代号；缺省取第一个")
    ap.add_argument("--port", type=int, default=0, help="Chrome 调试端口；0=按账号表")
    args = ap.parse_args()

    settings = Settings(root=find_project_root())
    accs = gaccounts.load_accounts()
    label = args.label or (accs[0].label if accs else "default")
    port = args.port or gaccounts.resolve_port(label, fallback=gaccounts.DEFAULT_PORT)

    try:
        # require_list=False：停在登录页也能导（那时本来就没有列表帧）
        session = gbrowser.open_session(settings, label=label, port=port, require_list=False)
    except gbrowser.SessionError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        if exc.hint:
            print(f"       {exc.hint}", file=sys.stderr)
        return 2

    out_path = gbrowser.state_path(settings, label)
    try:
        ctx = gbrowser.context_of(session)
        host = (urlparse(selectors.MAIN_PAGE).hostname or "").lower()
        platform = [c for c in (ctx.cookies() if ctx is not None else [])
                    if host and host in str(c.get("domain") or "").lower()]
        info = gbrowser.save_state(session, out_path)
    except Exception as exc:                                 # noqa: BLE001
        print(f"[FAIL] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 5
    finally:
        session.close()                                      # Q31：不留常连

    if not info.get("saved"):
        print(f"[FAIL] 导出后读不回快照：{out_path}", file=sys.stderr)
        return 5
    print(f"[OK] {label} 的登录态已存 → {out_path}（{info.get('size', 0)} 字节，"
          f"{info.get('at', '')}）", file=sys.stderr)
    print(f"     站点 cookie {len(platform)} 条：{', '.join(sorted({c.get('name', '') for c in platform}))}",
          file=sys.stderr)
    if info.get("session_level"):
        print(f"     其中会话级（关浏览器即失）：{', '.join(info['session_level'])}",
              file=sys.stderr)
    print("⚠ 该文件等同登录态：不要外发、不要入库（.gitignore 已覆盖）。", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
