"""会话检查 / 恢复 / 重登：这个账号的浏览器**现在到底登没登录**，没登录怎么办。

判据只有一条是可靠的 —— **真去要一次页面，看它落到哪儿**：
- 落在登录页（`Login.aspx` / `Loginb.aspx` / `Default.aspx`）⇒ 没登录；
- 落在平台内页 ⇒ 登录着。

`.ASPXAUTH` cookie 存在与否**只作参考**（`selectors.py:36` 说它是会话级、`__init__.py:66`
说它是持久的，两处矛盾未裁决），所以 `ticket` 字段不参与 `logged_in` 判定。

三种动作（互斥）：

| 动作 | 干什么 | 发几次请求 |
|---|---|---|
| （缺省）`check` | 只看登录态 | 平台内页 **0 次**；否则 1 次 |
| `--restore` | 未登录 → 把 `storage_state_gxks_<代号>.json` 的 cookie 注入 → 再探 | 注入本身 0 次，复探 1 次 |
| `--relogin` | 未登录 → 真浏览器填表点登录（`login.login()`） | 登录 1 次，不复试 |

**纪律**：`--relogin` 走正常流程、**只试一次**；一旦落到验证码闸门（`needVerify=1` 或
`trVerify` 可见）立即停，交给人在这台浏览器窗口里手输 —— 连错验证码是撞库特征。
登录成功后顺手把登录态存回快照（滚动刷新那张票）。

用法：
    .venv\\Scripts\\python.exe scripts/gxks_session_check.py --label default --port 9222
    .venv\\Scripts\\python.exe scripts/gxks_session_check.py --label default --restore
    .venv\\Scripts\\python.exe scripts/gxks_session_check.py --label default --relogin

输出：最后一行打印 JSON（面板读它），同时落 `data/debug/session_<代号>.json`。
退出码：0 检查完成（登录与否都算成功）；2 连不上浏览器；3 需要人工（验证码）；
        4 没有可用账号密码；5 其他失败。
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
from xxt.adapters.gxks import login as glogin        # noqa: E402
from xxt.adapters.gxks import selectors              # noqa: E402
from xxt.config import Settings, find_project_root   # noqa: E402

EXIT_OK, EXIT_NO_BROWSER, EXIT_FAIL = 0, 2, 5
EXIT_NEED_HUMAN, EXIT_NO_CRED = 3, 4


def _platform_host() -> str:
    return (urlparse(selectors.MAIN_PAGE).hostname or "").lower()


def _detect(page, *, force: bool = False) -> tuple[bool, str, bool]:
    """探一次登录态，返回 `(logged_in, url, navigated)`。

    `force=False` 时若页面已在平台内页就**零新请求**；`force=True`（注入 cookie 之后）
    必须真要一次页面，否则拿的还是注入前的旧 URL，等于自欺。
    """
    url = page.url or ""
    host = _platform_host()
    if not force and gdriver.at_login_page(url):
        return False, url, False
    if not force and host and host in url.lower():
        return True, url, False
    page.goto(selectors.MAIN_PAGE, wait_until="commit", timeout=60000)
    page.wait_for_timeout(1500)
    url = page.url or ""
    return (not gdriver.at_login_page(url)), url, True


def _relogin(page, settings) -> dict:
    """真浏览器填表登录一次。**遇验证码立即返回、不重试。**"""
    cred = glogin.load_credentials(glogin.credentials_path(settings))
    if cred is None:
        return {"result": "no_credentials"}
    if not gdriver.at_login_page(page.url or ""):
        # 不在登录页就先要一次学习入口 —— 服务端会把未登录的人踢到登录页
        page.goto(selectors.MAIN_PAGE, wait_until="commit", timeout=60000)
        page.wait_for_timeout(1500)
    if not gdriver.at_login_page(page.url or ""):
        return {"result": "already", "url": page.url}      # 刚好已登录，别白填一次表
    return glogin.login(page, cred)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="", help="账号代号；缺省取第一个")
    ap.add_argument("--port", type=int, default=0, help="CDP 端口；0=按账号表")
    ap.add_argument("--restore", action="store_true", help="未登录则注入快照 cookie 再探")
    ap.add_argument("--relogin", action="store_true", help="未登录则用账号密码重登一次")
    args = ap.parse_args()
    if args.restore and args.relogin:
        print("[FAIL] --restore 与 --relogin 不能同时用", file=sys.stderr)
        return EXIT_FAIL
    action = "restore" if args.restore else ("relogin" if args.relogin else "check")

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
    restored = 0
    relogin_out: dict | None = None
    state: dict = {}
    backend = session.backend
    ticket = False
    try:
        page = session.page
        ctx = gbrowser.context_of(session)          # cdp 取第一个上下文，managed 直接用
        logged_in, url, nav = _detect(page)
        navigated = navigated or nav

        if not logged_in and action == "restore":
            restored = gbrowser.restore_cookies(session, gbrowser.state_path(settings, label))
            if restored:
                logged_in, url, nav = _detect(page, force=True)
                navigated = navigated or nav
        elif not logged_in and action == "relogin":
            relogin_out = _relogin(page, settings)
            if relogin_out.get("result") in ("ok", "already"):
                logged_in, url, nav = _detect(page)      # login() 已等到非登录页
                navigated = navigated or nav

        if ctx is not None:
            ticket = any(c.get("name") == ".ASPXAUTH" for c in ctx.cookies(selectors.MAIN_PAGE))
        if logged_in:
            # 滚动刷新快照：每次确认登录着就把当前票存一遍，被踢时手里总有最近一份
            try:
                state = gbrowser.save_state(session, gbrowser.state_path(settings, label))
            except Exception as exc:                    # noqa: BLE001  存不下不该判检查失败
                state = {"saved": False, "error": type(exc).__name__}
    except Exception as exc:                            # noqa: BLE001
        print(f"[FAIL] {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_FAIL
    finally:
        session.close()                                 # Q31：不留常连

    need_human = bool(relogin_out and relogin_out.get("result") == "need_verify")
    result = {
        "ok": True, "label": label, "port": port,
        "backend": backend,
        "action": action,
        "logged_in": bool(logged_in),
        "url": url[:200],
        "ticket": bool(ticket),
        "navigated": navigated,
        "restored": restored,
        "relogin": relogin_out,
        "need_human": need_human,
        "state": state or gbrowser.state_summary(gbrowser.state_path(settings, label)),
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    cache = settings.data_dir / "debug" / f"session_{label}.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    if need_human:
        print("[WARN] 被推到验证码闸门：请在这台浏览器窗口里人工输验证码并登录",
              file=sys.stderr)
    elif relogin_out is not None and relogin_out.get("result") == "no_credentials":
        print("[WARN] data/account.local.json 里没有可用的账号密码，无法自动重登",
              file=sys.stderr)
    else:
        print(f"[{'OK' if logged_in else 'WARN'}] "
              f"{'已登录' if logged_in else '未登录（落到登录页）'} → {url[:120]}",
              file=sys.stderr)
    print(json.dumps(result, ensure_ascii=False))

    if need_human:
        return EXIT_NEED_HUMAN
    if relogin_out is not None and relogin_out.get("result") == "no_credentials":
        return EXIT_NO_CRED
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
