# 2026-11-06-02 多账号批量启动：补浏览器闸 + 讲清「跳过 vs 失败」

## 一、用户三问与回答

用户原话：「面板上账号与任务的右侧栏的浏览器与会话是跟随每个账号不同而变化的吗……如果是多个账号要同时启动学习，是需要我一个一个去点击吗……已经有一个账号在学习了，这时候就不能再选中它，但目前好像还是可以选中的」

### ① 右侧栏「浏览器与会话」是按账号的 ✅

- 后端 `/api/state` 一次算全部：`browsers = {label: {...}}`（`app.py` 的 `browsers` 字典），
  每个账号有自己的 `port` / `profile` / 登录态快照 / 会话检查结果 / `started_by_panel`；
- 前端悬停某行 → `hoverAcctSide()` → `renderBrowserFor(state, HOVER_LABEL)` 整块切过去；
- 卡片 5 个按钮统一用 `browserLabel() = HOVER_LABEL || LABEL`（2026-11-05 修的「看着 B 停 A」）。

**不是全局共享**，但一次只显示一个账号；「对所有账号批量启动浏览器」这个动作**不存在**（P1）。

### ② 批量启动**早就有**，不是一个个点

表格勾选框 → 批量栏「开始学习」→ `POST /api/accounts/batch`（2026-10-30 造的）。
`batchAction('start')` 会先 `prompt` 问每号跑几节，再逐个 `_study_one`。

### ③ 「运行中还能勾选」——不是 bug，但界面没说清

复选框是有意不禁用的：**批量「停止」恰恰需要选中正在跑的账号**。
用户感觉「不妥」是因为界面**没有当场解释**「勾了它也只会跳过」。

## 二、查出来的真问题（P0）

### 问题 1：单账号/批量「学习」不检查浏览器是否开着

`run_gxks.py` 第一步就 `open_session()` 连 CDP；浏览器没开时打印
`[FAIL] 连不上 CDP http://127.0.0.1:9222` 后 `return 2` **立刻退出**。
而面板早已回了 `ok:true` + 「开始刷课，最多 N 节」→ 用户看到的是「启动了但什么都没发生」。

批量把这个坑放大 N 倍：7 个账号里几个浏览器没开，就是几个白起的进程。

**对照**：`refresh_sections` 早在 2026-11-03 就做了端口前置检查，
但**两条主路（单账号学习、批量学习）反而漏了**。→ 说明「只在一处手写判断」迟早会漏。

### 问题 2：批量汇总把「跳过」当「失败」

`_study_one` 对「已在跑」返回 `ok:false`，批量汇总就报
「1 个没做成——a1：该账号已有任务在跑」。用户看到红提示会以为批量挂了一半。

### 问题 3（审查时发现）：批量串行探端口

若直接对每个账号调 `port_alive`，7 个账号最坏 7 × 0.5 秒 = 3.5 秒卡顿
（`chrome.port_alive` 的 timeout 说明：本机已关闭端口是 TimeoutError 而非 ConnectionRefused）。

## 三、改了什么

### `src/xxt/panel_gxks/app.py`

1. **新增 `_require_browser_port(settings, acc, alive=None)`**
   - managed 后端直接放行（那种形态浏览器由脚本自己起）；
   - 默认自己探一次，也接受调用方传入**已并发探好**的结果；
   - 拒绝时给**可照做**的提示：账号名 + 端口 + `启动并登录` 按钮位置 + 手动启动命令；
   - ⚠ profile 路径**不能写死** `data/profiles/<label>`：账号表允许自定义 `profile`，
     写死会让用户照着敲命令、开出一个没有登录态的浏览器。改用 `gbrowser.cdp_profile()`。

2. `_study_one` 增加 `port_alive` 参数 + 调用 `_require_browser_port`；
   「已在跑」返回带 `skipped: True`（与失败区分）。

3. `refresh_sections` 改用同一个 `_require_browser_port`（去掉自己那份手写判断）。

4. `accounts_batch`：
   - **只对 `start` 探端口**，且用 `ports_alive()` **一次并发**探完再逐个判定
     （停止/启用/停用/删除根本不看浏览器，白探一轮纯浪费）；
   - 汇总拆成 `done / skipped / failed` 三类，文案：「完成 1 个（a2）；跳过 1 个（a1 已在跑）」；
   - `ok` 只反映**有没有真失败** —— 全是「已在跑」的跳过时仍算成功，
     否则前端会弹红 toast。

### `src/xxt/panel_gxks/page.py`

1. 批量栏：算出「选中的里有几个在跑」，按钮文案变
   「开始学习（跳过 2 个在跑）」+ 旁边「其中 N 个运行中」标签 + `title` 解释；
2. 运行中那一行的复选框加 `title`：「批量「开始学习」会自动跳过它；批量「停止」用它」
   （**不禁用** —— 否则批量停止做不到）；
3. `batchAction`：
   - 「开始学习」的 confirm 明确写出「会跳过哪几个、只启动剩下几个、不会重复开」；
   - 「浏览器没起」的失败改用**持久提示条** `showNotice` 摊开原文
     （toast 3 秒消失，端口号和启动命令来不及看）；
   - 加 `BUSY` 闸防连点（批量比单个慢得多，N 次端口探测）；
   - 成功/失败判定改看 `data.failed`，不再看 `data.ok`。
4. `startStudy` → `startStudySafe`：单账号学习同样把 `need_browser` 原文摊开，
   并共用 `BUSY` 闸。**删掉旧 `startStudy`**（改名后已无调用者，留着重名会误导）。

## 四、审查中发现并修正的自身问题（5 处）

| # | 问题 | 后果 | 修法 |
|---|---|---|---|
| 1 | `_require_browser_port` 把 profile 路径写死 `data/profiles/<label>` | 自定义 profile 的账号照着命令敲会开错目录（无登录态） | 改用 `gbrowser.cdp_profile()` |
| 2 | `startStudy` 改名后旧函数残留 | 死代码 + 名字误导 | 删除 |
| 3 | `startStudySafe` / `batchAction` 走 `apiPost` 绕过 `BUSY` 防连点 | 双击起两个进程、第二次必报错弹红条 | 两处都加 `BUSY` 闸 |
| 4 | 批量对**所有动作**探端口 | 停止/启用/停用/删除白吃 timeout | 只在 `start` 探 |
| 5 | 批量**串行**探端口 | 7 个账号最坏 3.5 秒 | 改 `ports_alive()` 并发 |

另修一处**测试脆弱性**：`test_study_spawns_with_account_port` 之前能过，
是因为开发机 9222 恰好真开着 Chrome（依赖机器状态）。已补 `port_alive` 替身。

## 五、验证

- `pytest -q`：**1083 passed**（15 subtests）；
- 新增/改写测试：
  - `test_gxks_panel.py`：+`test_study_refuses_when_browser_down`，修原有用例补替身；
  - `test_gxks_panel_batch.py`：`_start` 夹具（端口替身）、`skipped ≠ failed`、
    `test_start_reports_accounts_without_browser`、`test_non_start_actions_do_not_probe_ports`、
    `test_start_probes_ports_concurrently`；
  - `test_gxks_page.py`：运行中可勾但带说明、批量栏算出 runningSel、批量 confirm 讲跳过、
    `startStudySafe` 摊开 `need_browser`；
- 页面 JS `node --check`：通过（落 `data/debug/_check_page.js` 临时文件）；
- **实机只读验证** `data/debug/diag_require_browser.py`：
  `default`(9222) 放行，其余 6 个拦下，输出的启动命令里 profile 路径正确。

## 六、改动文件清单

- `src/xxt/panel_gxks/app.py`（`_require_browser_port` / `_study_one` / `refresh_sections` / `accounts_batch`）
- `src/xxt/panel_gxks/page.py`（批量栏 / 复选框 title / `batchAction` / `startStudySafe` / `.bb-note`）
- `tests/test_gxks_panel.py`、`tests/test_gxks_panel_batch.py`、`tests/test_gxks_page.py`
- `data/debug/diag_require_browser.py`（新增，只读诊断，不入库）

## 七、答案：多账号先后关系怎么管

**现在（P0 之后）**：

- **不会重复开**：后端 `_study_one` / 批量都强制「该账号已有任务在跑 → 跳过」，
  这条从 2026-10-30 就有；
- **会提前告知**：勾选时按钮上就写「跳过 N 个」，confirm 里列出是哪几个；
- **仍可勾选**：因为批量「停止」要用到运行中的账号；
- **浏览器没开会立刻报**，不会白起进程。

**仍缺（P1/P2，等用户排期）**：

- P1：批量「启动浏览器」（现在多账号仍需逐个点「启动并登录」）；
- P2：全局并发上限 + 启动随机错峰（`PLAN-GXKS.md` §7.3 明确要求，目前完全没有）。

## 八、交接

**进行中**：无。

**下一步第一个动作**：等用户决定是否开工 **P1（批量启动浏览器）**；
用户已表态「先做 P0 再做 P1 和 P2」。

**阻塞点**：无。

**已知边界**：

- P0 只解决「误报成功」与「界面不说清」，**不限制同时开几个**。
  用户若一次勾 7 个且 7 个浏览器都开着，仍是 7 个同时开播 —— 风控面未变，属 P2。
- `need_browser` 的提示条是**持久**的（`showNotice`），需用户手动关
  （或下次成功操作时被覆盖）。这是有意的：端口号/命令要留得住。
