"""自动重登（只在**页面已经落到登录页**时用）：读本地凭据 → 真浏览器填表登录。

用法（面板已开、9222 在、页面已掉到登录页）：

    .venv\\Scripts\\python.exe scripts/relogin_gxks.py
    .venv\\Scripts\\python.exe scripts/relogin_gxks.py --dry-run   # 只检查凭据与当前页面，不填不提交

纪律（不要放宽）：

- 凭据只从 `data/account.local.json`（gitignore 内）读，**不打印、不回显、不入日志**；
- 占位符（还是“在这里填…”）→ 视为未配置，直接退出，绝不拿占位符去撞登录；
- 遇到 `needVerify=1` / 验证码可见 → 退出码 **4**，转人工，**绝不试错验证码**；
- 一次只登录一次，失败就停（连试 = 撞库特征）。
"""

from __future__ import annotations

import argparse
import sys

from playwright.sync_api import sync_playwright

from xxt.adapters.gxks import driver as gdriver
from xxt.adapters.gxks import login as glogin
from xxt.adapters.gxks import selectors

CDP = "http://127.0.0.1:9222"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9222, help="Chrome 调试端口")
    ap.add_argument("--cred", default=str(glogin.DEFAULT_CRED_PATH), help="凭据文件路径")
    ap.add_argument("--dry-run", action="store_true", help="只检查，不填表不提交")
    args = ap.parse_args()

    cred = glogin.load_credentials(args.cred)
    if cred is None:
        print(f"[SKIP] 凭据未配置（文件缺失 / 仍是占位符）：{args.cred}")
        print("       请把 name/pw 填好后重跑；不配置就一直按“掉线即停”跑。")
        return 2
    print(f"凭据已配置（账号长度 {len(cred['name'])} 位，不回显）")

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{args.port}")
        page = None
        for ctx in browser.contexts:
            for pg in ctx.pages:
                if (pg.url or "").startswith(selectors.BASE):
                    page = pg
                    break
            if page is not None:
                break
        if page is None:
            print("[FAIL] 找不到站点页面（先手动打开站点并落到登录页）")
            return 2

        print(f"当前页面：{gdriver.shorten_url(page.url)}")
        if not gdriver.at_login_page(page.url):
            print("[SKIP] 当前不在登录页 → 已登录，无需重登（避免多会话互踢）")
            return 0

        if args.dry_run:
            need = glogin.is_need_verify(page.url)
            print(f"[DRY] 会提交登录；验证码闸门={need}")
            return 0

        out = glogin.login(page, cred)
        print(f"结果：{out['result']}"
              + (f"（{out.get('reason')}）" if out.get("reason") else "")
              + (f" 落地页={gdriver.shorten_url(out.get('url') or '')}" if out.get("url") else ""))

        if out["result"] == "ok":
            browser.close()
            return 0
        if out["result"] == "need_verify":
            # 验证码触发原因未知（§7.2）；试错 = 撞库特征，是这里最危险的动作
            print("⚠ 服务端要验证码 → 必须人工登录。脚本不再尝试。")
            browser.close()
            return 4
        browser.close()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
