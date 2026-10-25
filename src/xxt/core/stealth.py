"""浏览器反检测（PLAN §5.5/§5.6 落地）。

背景：2026-10-25 用户手机端收到"账号在谷歌浏览器登录，请修改密码"提醒——平台检测到
自动化浏览器特征。

2026-10-25 二次审计（用户要求重查）实测到的矛盾：
  1. UA 写死 Chrome/131 而实际内核是 156.0.8078.4（差 25 个大版本，最容易被比对）
  2. navigator.webdriver 被覆盖成 undefined，真机是 false（undefined 本身也是特征）
  3. navigator.plugins 是 [1,2,3,4,5] 假数组，plugins[0] 是数字而非 Plugin，长度对但形状假
  4. 未关 AutomationControlled，内核底层仍带自动化标记
本版按真机形状重做以上四项，并给所有 launch 统一加 LAUNCH_ARGS。

残留（headless 方案固有，需换真实 Chrome 才能消除）：WebGL 仍是 SwiftShader 软件渲染、
outerHeight 可能等于 innerHeight。取舍见 devlog 2026-10-25-23。
"""

from __future__ import annotations

# 正常 Chrome 稳定版 UA：版本号必须与内核一致（Playwright 1.64 内置 Chromium 156.0.8078.4）
CHROME_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36")

# 所有 chromium.launch 统一携带：关闭 AutomationControlled，去掉 no-first-run 弹窗
LAUNCH_ARGS: list[str] = [
    "--disable-blink-features=AutomationControlled",
    "--no-first-run",
    "--no-default-browser-check",
]

STEALTH_JS = """
// 1) webdriver：真机是 false
Object.defineProperty(navigator, 'webdriver', {get: () => false});
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en']});
// 2) plugins：仿成真机 PluginArray（5 个 PDF 条目，元素带 name/filename，原型也对）
(() => {
  const mk = (name) => ({
    name, filename: 'internal-pdf-viewer', description: 'Portable Document Format',
    length: 1, item: () => null, namedItem: () => null,
  });
  const ps = ['PDF Viewer', 'Chrome PDF Viewer', 'Chromium PDF Viewer',
              'Microsoft Edge PDF Viewer', 'WebKit built-in PDF'].map(mk);
  ps.item = (i) => ps[i] || null;
  ps.namedItem = (n) => ps.find((p) => p.name === n) || null;
  ps.refresh = () => {};
  try { Object.setPrototypeOf(ps, PluginArray.prototype); } catch (e) {}
  Object.defineProperty(navigator, 'plugins', {get: () => ps});
  // mimeTypes 与之配套（真机 4 条），headless shell 是 0 条
  const ms = ['application/pdf', 'text/pdf'].map((type) => ({
    type, suffixes: 'pdf', description: 'Portable Document Format',
    enabledPlugin: ps[0],
  }));
  ms.item = (i) => ms[i] || null;
  ms.namedItem = (n) => ms.find((m) => m.type === n) || null;
  try { Object.setPrototypeOf(ms, MimeTypeArray.prototype); } catch (e) {}
  Object.defineProperty(navigator, 'mimeTypes', {get: () => ms});
})();
// 3) userAgentData：真机有 brands，headless shell 完全没有（指纹库必查）
if (!navigator.userAgentData) {
  Object.defineProperty(navigator, 'userAgentData', {get: () => ({
    brands: [
      {brand: 'Chromium', version: '156'},
      {brand: 'Google Chrome', version: '156'},
      {brand: 'Not=A?Brand', version: '24'},
    ],
    mobile: false,
    platform: 'Windows',
    getHighEntropyValues: () => Promise.resolve({
      platform: 'Windows', platformVersion: '15.0.0', architecture: 'x86',
      bitness: '64', model: '', wow64: false,
    }),
  })});
}
window.chrome = window.chrome || {runtime: {}, loadTimes: () => ({})};
Object.defineProperty(navigator, 'permissions',
  {get: () => ({query: () => Promise.resolve({state: 'granted'})})});
"""


def hardened_context(browser, **kw):
    """统一创建加固 context：正常 UA + 视口 + 反自动化指纹注入。

    storage_state/proxy 等照常传；viewport/screen 固定为常见笔记本尺寸，
    避免默认 1280x720 且 outerHeight==innerHeight 的组合特征。
    """
    kw.setdefault("user_agent", CHROME_UA)
    kw.setdefault("locale", "zh-CN")
    kw.setdefault("viewport", {"width": 1280, "height": 800})
    kw.setdefault("screen", {"width": 1280, "height": 800})
    ctx = browser.new_context(**kw)
    ctx.add_init_script(STEALTH_JS)
    return ctx
