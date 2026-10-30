"""会话检查：这个账号的浏览器**现在到底登没登录**（面板「检查会话」按钮的数据源）。

判据只有一条是可靠的 —— **真去要一次页面，看它落到哪儿**：
- 落在登录页 ⇒ 没登录；
- 落在平台内页 ⇒ 登录着。

`.ASPXAUTH` cookie 存在与否**只作参考**（`selectors.py:36` 说它是会话级、`__init__.py:66`
说它是持久的，两处矛盾未裁决），所以 `ticket` 字段不参与 `logged_in` 判定。

**最小访问**：页面已经在平台内页时**零新请求**（直接读 URL）；只有页面不在平台域
（比如 about:blank）才导航一次学习入口。

用法：
    .venv\\Scripts\\python.exe scripts/gxks_session_check.py --label default --port 9222

输出：最后一行打印 JSON（面板读它），同时落 `data/debug/session_<label>.json`。
退出码：0 检查完成（登录与否都算成功）；2 连不上浏览器；5 其他失败。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from xxt.adapters.gxks import accounts as gaccounts  # noqa: E402
from xxt.adapters.gxks import browser as gbrowser    # noqa: E402
from xxt.adapters.gxks import driver as gdriver      # noqa: E402
from xxt.adapters.gxks import selectors              # noqa: E402
from xxt.config import Settings, find_project_root   # noqa: E402

EXIT_OK, EXIT_NO_BROWSER, EXIT_FAIL = 0, 2, 5


def _platform_host() -> str:
    return (urlparse(selectors.MAIN_PAGE).hostname or "").lower()


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="", help="账号代号；缺省取第一个")
    ap.add_argument("--port", type=int, default=0, help="CDP 端口；0=按账号表")
    args = ap.parse_args()

    settings = Settings(root=find_project_root())
    accs = gaccounts.load_accounts()
    label = args.label or (accs[0].label if accs else "default")
    port = args.port or gaccounts.resolve_port(label, fallback=gaccounts.DEFAULT_PORT)

    try:
        # require_list=False：停在登录页的浏览器没有列表帧，按列表判据会被误判成“没页面”
        session = gbrowser.open_session(settings, label=label, port=port, require_list=False)
    except gbrowser.SessionError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        if exc.hint:
            print(f"       {exc.hint}", file=sys.stderr)
        return EXIT_NO_BROWSER

    navigated = False
    backend = session.backend
    ticket = False
    try:
        page = session.page
        # cdp 走 browser.contexts，managed 走 session.context —— 两边都能读 cookie
        ctx = session.context
        if ctx is None and getattr(session.browser, "contexts", None):
            ctx = session.browser.contexts[0]
        url = page.url or ""
        host = _platform_host()
        if gdriver.at_login_page(url):
            logged_in = False
        elif host and host in url.lower():
            logged_in = True                     # 已在平台内页：零新请求
        else:
            page.goto(selectors.MAIN_PAGE, wait_until="commit", timeout=60000)
            page.wait_for_timeout(1500)
            navigated = True
            url = page.url or ""
            logged_in = not gdriver.at_login_page(url)
        if ctx is not None:
            ticket = any(c.get("name") == ".ASPXAUTH" for c in ctx.cookies(selectors.MAIN_PAGE))
    except Exception as exc:                                 # noqa: BLE001
        print(f"[FAIL] {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_FAIL
    finally:
        session.close()                                      # Q31：不留常连

    result = {
        "ok": True, "label": label, "port": port,
        "backend": backend,
        "logged_in": bool(logged_in),
        "url": url[:200],
        "ticket": bool(ticket),
        "navigated": navigated,
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    cache = settings.data_dir / "debug" / f"session_{label}.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[{'OK' if logged_in else 'WARN'}] "
          f"{'已登录' if logged_in else '未登录（落到登录页）'} → {url[:120]}",
          file=sys.stderr)
    print(json.dumps(result, ensure_ascii=False))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
