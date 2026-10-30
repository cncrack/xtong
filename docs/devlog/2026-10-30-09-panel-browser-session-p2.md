# 2026-10-30-09 面板接管登录（P2）

## 1. 目标与结果

**目标**：把「每次开跑前手敲 `chrome.exe --remote-debugging-port=… --user-data-dir=…`」从人工删掉，
改成面板上三个按钮：**启动浏览器 / 停止浏览器 / 检查会话**（PLAN-GXKS §13.6 P2）。

**结果：成功**（代码 + 文档 + 测试全部落盘；**未在真实登录态下实跑**，见 §5）。

顺带查出并修掉一个会让 P2 白做的缺口：单账号回落表里没有 `profile` 字段，
原实现要求 `acc.profile` 非空 → 在这台机器上「启动浏览器」按钮**永远是灰的**。

## 2. 做了什么

### 2.1 新增 `src/xxt/adapters/gxks/chrome.py`（136 行）

只做两件事：**怎么把 Chrome 正确地拉起来**、**它现在活着吗**，不碰任何页面。

| 函数 | 要点 |
|---|---|
| `find_chrome()` | `XXT_GXKS_CHROME` > 常见安装路径 > 注册表 `App Paths` > `shutil.which`。**显式指定却不存在 → 返 None，不猜别的路径去撞**（否则会在“我以为用的是这个 Chrome”上浪费一小时） |
| `launch_argv()` | 端口与 profile 都是**账号属性**，不信全局配置；URL 必须放**最后**（Chrome 把裸参数当要打开的页面） |
| `port_alive()` | 只打 `127.0.0.1:<port>/json/version`，**零外网请求** |
| `wait_alive()` | 冷启动要几秒，超时一律 False（不抛） |
| `start()` | `CREATE_NEW_PROCESS_GROUP \| DETACHED_PROCESS` —— 面板重启/退出不带走用户正在登录的窗口 |

⚠ 写进模块头的坑：若该 profile 已被一个**不带调试端口**的 Chrome 打开（用户自己双击的），
再带 `--remote-debugging-port` 启动只会让老进程开个新标签、**端口不会起来** →
超时后一律提示「先把该账号的 Chrome 全部关掉」，不猜。

### 2.2 新增 `scripts/gxks_session_check.py`（116 行）

**判据只有一条可靠：真去要一次页面，看它落在哪儿。**
- 落在登录页 ⇒ 未登录；落在平台内页 ⇒ 登录着。
- `.ASPXAUTH` 存在与否**只作参考字段**，不参与 `logged_in` 判定（`selectors.py:36` 说是会话级、
  `__init__.py:66` 说是持久 cookie，两处矛盾未裁决）。
- **最小访问**：页面已经在平台域 ⇒ **零新请求**；只有停在 `about:blank` 之类才导航一次学习入口。
- 输出：最后一行打印 JSON（面板读它），同时落 `data/debug/session_<label>.json`；
  退出码 0（登录与否都算查成功）/ 2（连不上浏览器）/ 5（其他）。

### 2.3 面板四个路由 + 一张卡片

- `GET /api/browser/state`（只读，空代号 = 第一个账号）；
- `POST /api/browser/start` / `POST /api/browser/stop`；
- `POST /api/session/check`（**会访问平台**，只在点按钮时跑，90 秒超时，解析脚本最后一行 JSON）；
- `/api/state` 回包多一块 `browser`；前端新增「浏览器与会话」卡片
  （后端 / Chrome 路径 / 端口 / user-data-dir / managed profile / 上次检查 + 三个按钮）；
- 动作词 `browser-start` / `browser-stop` / `session-check` 登进 `gxks_ops.ACTIONS`。

**三条安全边界（都有测试钉死）**：
1. `managed` 后端下**拒绝**在面板里起本机 Chrome（容器里没有窗口可给人用）；
2. 「停止浏览器」只 `taskkill` **面板自己拉起的 pid**；不是自己起的直接拒（防误杀日常 Chrome）；
3. 账号有任务在跑时不许起浏览器（两边抢同一会话会互相顶掉页面）。

### 2.4 干跑查出的缺口 + 修法

只读冒烟（真实账号表）第一次跑出来 `profile: (账号表未配) / can_start: False` —— 按钮是灰的。

- 新增 `browser.cdp_profile(settings, label, acc)`：账号表显式配了 `profile` 就用它；
  没配则落到 `data/profiles/<代号>`（**与 managed 后端同一目录**：本机开着窗口人工登录一次，
  容器里直接接着跑，不用再登一次）。
- **绝不**回落到系统默认 Chrome 档案（`%LOCALAPPDATA%/Google/Chrome/User Data`）：
  那个目录被日常 Chrome 占着，带调试端口只会给老进程开新标签、端口起不来；
  而且会把自动化的登录态混进日常档案。
- 修后冒烟：`profile = data/profiles/default`、`can_start = True`、回包无凭据。

## 3. 关键决策及理由

1. **不实现 `/api/login`（原计划里有）**：登录是**人在窗口里打字 + 可能的验证码**，
   脚本代打没有意义且更容易触发风控。面板只做「起窗口」和「起完检查」两件事，
   把登录留给人和站点自己。真要做自动重登，已有 `login.py` 走脚本内部路径，不必经过面板。
2. **judge 只看落点、不看 cookie `expires`**：`.ASPXAUTH` 语义两处文档互相矛盾（§13.7 第 3 条），
   拿一个未裁决的字段当判据等于给自己埋坑。
3. **两次探活（启动前 + 启动后）**：启动前探是为了「已经在跑就别重复起」；
   启动后探是为了区分「进程起来了但端口没响应」这种**静默失败**（profile 被占用的典型表现），
   否则面板会报成功、实际连不上。
4. **profile 默认落 `data/profiles`**：与其让按钮永久置灰等用户去手改 JSON，
   不如给一个安全默认值；同目录与 managed 共用还顺手解决了 P3 的「容器首跑要重新登录」。
5. **`chrome.py` 放 adapters 层**：Chrome 命令行是平台相关的（Windows 注册表、`taskkill`），
   属于「平台强相关逻辑只写在 adapters 内」的硬约定。

## 4. 改动的文件清单

| 文件 | 动作 |
|---|---|
| `src/xxt/adapters/gxks/chrome.py` | 新增（拉起 + 探活） |
| `src/xxt/adapters/gxks/browser.py` | 加 `cdp_profile()`；`_open_cdp(require_list=)`、`_pick_any_page()`（登录页没有列表帧） |
| `scripts/gxks_session_check.py` | 新增（会话检查 CLI） |
| `src/xxt/panel_gxks/app.py` | `_record()` / `_kill_pid()` / `_browser_state()` + 四个路由 + `/api/state` 的 `browser` 块 |
| `src/xxt/panel_gxks/page.py` | 「浏览器与会话」卡片 + `renderBrowser()/startBrowser()/checkSession()` + `ACTION_CN` |
| `src/xxt/store/gxks_ops.py` | `ACTIONS` 加 `browser-start` / `browser-stop` / `session-check` |
| `tests/test_gxks_chrome.py` | 新增 9 条 |
| `tests/test_gxks_panel_browser.py` | 新增 18 条 |
| `tests/test_gxks_browser.py` | 加 `TestCdpProfile` 3 条 |
| `docs/gxks/PLAN-GXKS.md` | §13.6 P2 标 ✅ + 「P2 已落地」小节、§13.7 第 5 条、§九 变更记录 |
| `data/debug/diag_panel_browser_state.py` | 新增（只读冒烟，不入库） |

## 5. 验证与未验证

**验过**：
- `test_gxks_chrome.py` 9 条（探活用临时 `http.server`、启动用 `Popen` 替身，**不碰真 Chrome**）；
- `test_gxks_panel_browser.py` 18 条（managed 拒起 / 只杀自己的 pid / 任务在跑拒起 / 会话检查三种失败路径 / 不泄凭据）；
- `test_gxks_browser.py` 16 → 19 条；全量 `pytest` **491 passed**；
- 面板 JS 用 `node --check` 过一遍（拼字符串模板最容易漏括号）；
- 只读冒烟（真实账号表）：`backend=cdp / chrome 找到 / profile=data/profiles/default / can_start=True / 回包无凭据`。

**没验**：
- 本机 9222 **当前没有** Chrome 在听，所以「启动 → 人工登录 → 检查会话落到平台内页」这条**正向链**没跑过；
  只验到三条负路径（找不到 Chrome / 端口没响应 / managed 就拒）；
- 真实登录可能**顶掉既有会话**（Q20 的 `.ASPXAUTH` 是会话级），首次真跑要有人在旁边。

## 6. 交接块

- **进行中**：P2 代码/文档/测试已全绿；P3（Docker：`Dockerfile.gxks` + compose 8301 + `.dockerignore`）未开工。
- **下一步第一个动作**：先让用户跑一次「启动浏览器 → 登录 → 检查会话」把正向链证实（见 §7），
  再开 P3 的 `Dockerfile.gxks`（`.dockerignore` 属共享改动，**动之前必须问用户**）。
- **阻塞点**：无硬阻塞；P3 需要用户点头才加 `.dockerignore`。

## 7. 用户下一步操作清单

1. 起面板（**沙箱外**或自己的 PowerShell）：`.venv\Scripts\python.exe -m xxt.cli gxks-panel`
   （或 `.venv\Scripts\xxt.exe gxks-panel`），开 `http://127.0.0.1:8301`。
2. 在「浏览器与会话」卡片点 **启动浏览器** → 会开一个**新窗口**（profile 在 `data/profiles/default`），
   首次是**未登录**状态，请在里面正常登录一次（有验证码就人工输）。
3. 登录后点 **检查会话** → 期望看到绿色「页面上已登录」；把结果（成功 / 失败原文）贴回来。
4. ⚠ 若你原本还有别的 Chrome 窗口在用同一份档案，**先关掉再点启动**（否则端口起不来，面板会明确提示）。
