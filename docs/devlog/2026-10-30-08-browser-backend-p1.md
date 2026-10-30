# 2026-10-30-08 P1 浏览器后端：`cdp` / `managed` 一个入口

## 1. 本次目标与结果

用户「继续做」→ 执行 P0 交接块里的 P1。**全部达成**，代码级完成、未实跑 `managed`。

| 目标 | 结果 |
|---|---|
| `[gxks] browser_backend = "cdp" \| "managed"` 统一入口 | ✅ `adapters/gxks/browser.py::open_session()` |
| `managed` 复用 `core/browser.persistent_launch`，profile 落 `data/profiles/<代号>` | ✅ |
| 只新增、不改超星在用的 `launch()` / `hardened_context()` | ✅ 二者一行未动 |
| 现存脚本行为不变（缺省仍是 `cdp`） | ✅ 空端口真跑 `[FAIL] 连不上 CDP …` + exit 2，与旧版一致 |
| 单测 | ✅ `tests/test_gxks_browser.py` 15 条（4 subtests），子集 72 passed |

## 2. 关键决策

**D1 缺省必须是 `cdp`。** 三个脚本现在都在 `cdp` 下跑，改缺省等于改所有现存行为。`managed` 只在容器/服务器上由配置显式打开。

**D2 `cdp` 下绝不导航。** 人是自己把 Chrome 开到学习入口的，脚本一 `goto` 就把人正在看的页面拽走（还可能打断人工确认弹窗）。所以 `start_url` 这个参数在 `cdp` 分支里**被忽略**，只有 `managed` 用——只有 `gxks_sections.py` 传它，因为它自己不打开首页；`gxks_work` / `run_gxks` 本来就自己导航。

**D3 「连上了但没平台页面」也要先断开再抛。** 旧代码是 `return browser, None`，由脚本 `finally` 里 close；新代码在 `raise SessionError` 之前就 `session.close()`。理由同 Q31：常连会吞掉人工点「交卷」的 confirm。单测 `test_no_page_disconnects_before_raising` 钉死这条。

**D4 代号非法一律拒，不回落默认目录。** `profile_dir()` 走 `accounts.is_valid_label()`；回落成 `data/profiles/default` 的后果是两个账号共用一份登录态与续播 cookie，表现出来是「甲的进度跑到乙身上」，最难查。为此把 `accounts._LABEL_RE` 的判定开放成 `is_valid_label()`。

**D5 配置只进本机覆盖层。** `browser_backend` / `managed_headless` 都写在 `config/settings.local.toml`（不入库），`Settings` 的既有键语义零改动 —— 延续 P0 的隔离原则。`managed` 无头默认 true，**不读 `[stealth].headless`**（那是超星的键，读了就是跨平台耦合）。

**D6 失败统一成 `SessionError(code, hint)`。** `connect` / `no_page` / `browser` / `label` / `navigate` 五种；`hint` 是给人照抄的一句话（例如 `chrome.exe --remote-debugging-port=`、`playwright install chromium`）。脚本侧仍映射 **exit 2**，`gxks_sections.py` 的文档承诺没变。

## 3. 改动的文件清单

- 新增 `src/xxt/adapters/gxks/browser.py`（218 行）：`backend_of` / `profile_dir` / `Session` / `SessionError` / `open_session`，`_start_playwright()` 薄封装只为让单测能替身
- 新增 `tests/test_gxks_browser.py`（15 条 + 4 subtests）
- `src/xxt/adapters/gxks/accounts.py`：新增公开 `is_valid_label()`
- `scripts/gxks_work.py`：`wg.connect_page` → `gbrowser.open_session`；`browser` → `session`；finally 用 `session.close()`
- `scripts/gxks_sections.py`：同上 + `start_url=selectors.MAIN_PAGE` + 新增 `except SessionError → 2`
- `scripts/run_gxks.py`：同上；`acc_label` **前移到开浏览器之前**（`managed` 的 profile 目录由它决定）；`Settings(root=find_project_root())` 传进去
- `config/settings.local.toml`（不入库）/ `config/settings.local.example.toml`（入库）：加 `browser_backend`、`managed_headless`
- `docs/gxks/PLAN-GXKS.md`：§13.6 P1 标 ✅ + 「P1 已落地」小节；§13.7 第 1 条改为「C 形态未实现；`managed` 已实现未实跑」；§九 加一条

## 4. 验收原文

```
[OK] 三脚本编译通过
=== 真跑一次（故意指向空端口 9555）===
[FAIL] 连不上 CDP http://127.0.0.1:9555：BrowserType.connect_over_cdp: connect ECONNREFUSED 127.0.0.1:9555
       先起浏览器：chrome.exe --remote-debugging-port=9555 --user-data-dir="<该账号专用目录>"
exit=2

pytest tests/test_gxks_browser.py → 15 passed, 4 subtests passed
pytest test_imports/module_boundaries/gxks_panel/m0/panel/config_local_override/accounts → 72 passed
Settings 实测：backend= cdp | headless= True | platform= gxks | panel= {'host': '127.0.0.1', 'port': 8301}
```

## 5. 没做的（不假装做完）

1. **`managed` 一行都没实跑**：本机没真起无头浏览器（最小访问原则，不为验证而空跑一次平台访问）。`launch_persistent_context` 在这台机器能不能起来、无头下能不能登录，都未知。
2. **容器首跑没有登录引导**：`managed` 的 profile 第一次是空的 = 未登录。`gxks_sections` / `gxks_work` 有本地凭据时会自动重登，`run_gxks` **不会**（它只在掉线时重登）。容器首跑必须先登录一次 —— 属 P2/P3。
3. **形态 C 未实现**（回填后断开 → 面板展示整卷 → 人点确认 → 脚本重连代点交卷）。
4. **未推送**：本地待推 4 个 commit。

## 6. 交接块

- **进行中**：P1 已提交，P2（面板接管登录：`/api/browser/start|stop`、`/api/login`、账号卡「会话」列）未开始。
- **下一步第一个动作**：先在面板侧加 `GET /api/browser/state`（只读、不访问平台）暴露 `backend_of()` 与 `profile_dir()` 是否存在，让用户能看见「现在是 cdp 还是 managed、profile 在不在」；再动 `/api/browser/start`。
- **阻塞点**：`managed` 的真实可用性没有证据。要拿到证据的最小代价是**用户在自己机器上把 `browser_backend` 改 `managed`、`managed_headless` 改 `false` 跑一次 `gxks_sections.py`**（会起一个真实 Chrome 窗口，能看见、能登录）—— 这一步需要用户点头，否则 P3 的容器交付全是纸面。

## 7. 用户下一步操作清单

1. （可选，验证 `managed`）改 `config/settings.local.toml`：`browser_backend = "managed"` + `managed_headless = false`，然后跑
   `.venv\Scripts\python.exe scripts\gxks_sections.py --label <代号>`
   —— 应当弹出一个新的 Chrome 窗口并打开学习入口；把结果（成功/报错原文）贴回来。**验完记得改回 `cdp`**。
2. 说「推送」→ 我重跑 `scripts/push_check.py` 全绿后推 4 个 commit（`5533ca4`、`91cfe81`、`06b90a8`、本次）。
3. 说「继续」→ 我做 P2 面板接管登录（先只读的 `/api/browser/state`）。
