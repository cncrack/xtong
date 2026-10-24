"""浏览器反检测（PLAN §5.5/§5.6 落地）。

背景：2026-10-25 用户手机端收到"账号在谷歌浏览器登录，请修改密码"提醒——平台检测到
自动化浏览器特征。两处指纹：navigator.webdriver=true（Playwright 默认）与
HeadlessChrome UA。所有 context 一律经 hardened_context 创建。
"""

from __future__ import annotations

# 正常 Chrome 稳定版 UA（覆盖 HeadlessChrome 标记）
CHROME_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
window.chrome = window.chrome || {runtime: {}, loadTimes: () => ({})};
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => { const a = [1,2,3,4,5];
  a.item = i => a[i]; a.namedItem = () => null; return a; }});
Object.defineProperty(navigator, 'permissions', {get: () => ({query: () => Promise.resolve({state: 'granted'})})});
"""


def hardened_context(browser, **kw):
    """统一创建加固 context：正常 UA + 反自动化指纹注入（storage_state/proxy 等照常传）。"""
    kw.setdefault("user_agent", CHROME_UA)
    kw.setdefault("locale", "zh-CN")
    ctx = browser.new_context(**kw)
    ctx.add_init_script(STEALTH_JS)
    return ctx
