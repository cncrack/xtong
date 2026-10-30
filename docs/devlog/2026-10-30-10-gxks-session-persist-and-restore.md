# 2026-10-30-10 · P2 正向链真机验通 + 登录态持久化与恢复（P2.5）

## 一、本次目标

1. 在**真实登录态**下跑通 P2 正向链（面板点「启动浏览器」→ 人工登录 → 点「检查会话」），把上一条 devlog（`09`）遗留的"未验"消掉；
2. 用户追加要求：「**保存这个登陆会话，后期如果被踢了，可以再次登陆**」→ A（恢复登录）+ B（自动重登）都做。

## 二、最终结果

**两项都成了，且 A 有真机往返实证。**

### 2.1 P2 正向链（13:02~13:05）

| 步骤 | 实测结果 |
|---|---|
| 面板「启动浏览器」 | Chrome 起，`started_by_panel=true`、`since 13:02:14`、profile `data/profiles/default` |
| 窗口落点 | `Loginb.aspx`（新 profile = 空档案，未登录，符合预期） |
| 人工登录 | 13:03:18 落到 `Homew/MainPage.aspx` |
| 「检查会话」 | `logged_in=true`、`navigated=false`（**零新请求**）、`.ASPXAUTH` 在 |
| 复用该会话读节列表 | `gxks_sections.py` 成功：9 节 / 共 4 页 / 已完成 9 / 543 min |

### 2.2 同一次实测挖出的两个坑

1. **`Default.aspx` 是登录页，但代码不认**：未登录访问学习入口会被 302 到
   `Default.aspx?ReturnUrl=…`（`Login.aspx` 自己也 302 到它，实测 200 / 3717 字节）。
   而 `selectors.LOGIN_PAGES` 只认 `Login.aspx`/`Loginb.aspx` → `at_login_page()` 认不出
   ⇒ **「检查会话」会把未登录取当成已登录**（假阳性）。已补 `DEFAULT_PAGE` + 断言
   （含 `?ReturnUrl=…`、大小写、以及"学习入口本身不是登录页"的反例）。
2. **代理会把 503 伪装成"站点故障"**：v2rayN 系统代理开着时 Chrome 得到
   `chrome-error://chromewebdata/` + 「HTTP ERROR 503」；关掉后同一 URL 直连 **200**。
   已作为经验记为坑（见 PLAN-GXKS §13.7 第 6 条）。

### 2.3 登录态持久化（A）+ 自动重登（B）

**关键实测**：本站 4 条 cookie（`.ASPXAUTH` / `ASP.NET_SessionId` / `Clerk` / `ZYLTheme`）
**全是会话级**（`expires=-1`）⇒ Chrome 一关就全没，**profile 目录里留不下任何东西**。
所以"保存会话"必须落成 json，被踢后再注入。

- **A 恢复登录**：`data/storage_state_gxks_<代号>.json` → `ctx.add_cookies()` → 再探一次。
- **B 重新登录**：`login.login()` 真浏览器填表**一次**；遇 `needVerify`/验证码行即停转人工。
- **A 的真机往返**（`data/debug/diag_restore_roundtrip.py`，留痕 `data/debug/restore_roundtrip.json`）：

```
step1 刷新快照（4 条 cookie，1403 字节）
step2 clear_cookies()       ← 模拟"被踢/关浏览器"
step3 → Loginb.aspx         logged_in=false   ← 判据确实是有效的
step4 restore_cookies()     → 注入 4 条
step5 → MainPage.aspx       logged_in=true    ← 注入真的有效
step6 再刷新快照
```

## 三、关键决策及理由

1. **不做后台自动轮询重登**：那是最像撞库的行为。两个动作都只在**点按钮**时跑，
   `--relogin` 每次只试一次，验证码立即交回人工（`login.py` 既有纪律不变）。
2. **快照文件名统一由 `browser.state_path()` 决定**（`storage_state_gxks_<代号>.json`）。
   一开始 `export_state_gxks.py` 写的是无代号名，面板按代号找 → 「恢复登录」永远是灰的
   —— 这正是"存了却找不到"的假故障，已统一（脚本改为复用同一函数，删掉重复的那份文件）。
3. **前缀必须带 `storage_state`**：同时命中 `.gitignore` 的 `storage_state*.json` 与
   `push_check.py` 的同名拦截；成果里只回 cookie 的**名字与数量**，不含值。
4. **文案要区分三种情形**：恢复成功 / 本来就登着（`restored=0`，没走注入）/ 注入了但
   服务端不认。第一版把"本来就登着"说成"已恢复登录（注入 0 条）"，实测当场发现并改。
5. **`login.credentials_path()` 跟着 `settings.data_dir` 走**：原来 `DEFAULT_CRED_PATH` 是
   CWD 相对路径，面板/测试在不同 CWD 下会读到（或读不到）**真实凭据**。
6. **B 故意不在真机上跑**：主动打一次登录，命中验证码就白丢一次机会（最小访问原则）；
   逻辑由 `test_gxks_login.py` + 面板路由测试覆盖，真被踢时再用。
7. **STATUS.md 不动**：按 2026-10-27 用户定的主题隔离规则（Q35 第二平台已整条移出超星台账），
   本项目状态只写 `docs/gxks/` 与本目录 devlog。

## 四、改动的文件清单

| 文件 | 改了什么 |
|---|---|
| `src/xxt/adapters/gxks/selectors.py` | 新增 `DEFAULT_PAGE` 并加进 `LOGIN_PAGES`（实测证据注释） |
| `src/xxt/adapters/gxks/browser.py` | 新增 `STATE_PREFIX` / `context_of()` / `state_path()` / `state_summary()` / `save_state()` / `restore_cookies()` |
| `src/xxt/adapters/gxks/login.py` | 新增 `credentials_path(settings)` |
| `scripts/gxks_session_check.py` | 新增 `--restore` / `--relogin`（退出码 3 需人工、4 无凭据）；登录成功即刷新快照；改用 `credentials_path` |
| `scripts/export_state_gxks.py` | 重写：走 `open_session` + `save_state` + `state_path`，加 `--label`，只打印 cookie 名 |
| `src/xxt/panel_gxks/app.py` | 抽 `_session_script()`；新增 `POST /api/session/restore|relogin`；`_browser_state` 加 `state` / `credentials` |
| `src/xxt/panel_gxks/page.py` | 「恢复登录」「重新登录」两按钮 + 两个 handler + 状态表两行 + `ACTION_CN` 两项 |
| `src/xxt/store/gxks_ops.py` | `ACTIONS` 加 `session-restore` / `session-relogin` |
| `tests/test_gxks_driver.py` | `at_login_page` 测试改名并补 4 条断言（`Default.aspx` 三个形态 + 学习入口反例） |
| `tests/test_gxks_session_state.py` | **新增 19 条**：文件名/摘要不含值/只注入平台域/往返/凭据路径 |
| `tests/test_gxks_panel_browser.py` | 新增 7 条：无快照拒恢复、恢复成功、票作废、本来就登着、无凭据拒重登、重登成功、验证码转人工 |
| `docs/gxks/PLAN-GXKS.md` | §13.6 P2 行改"已验"+ 新增「P2.5 登录态持久化与恢复」小节；§13.7 第 5 条改写、新增第 6 条（代理 503）；§九 变更记录新增一条 |

（`data/debug/diag_cdp_inspect.py`、`diag_restore_roundtrip.py` 在 `data/` 内，不入库。）

## 五、验证

- `pytest` 全量 **518 passed**（原 494 + 新增 19 会话态 + 面板 7 → 部分重合，净 +24）；
- 面板 JS 抽取后 `node --check` 通过；
- 面板实机：`GET /` 200 / **15489 字符**、两个新按钮在位；`/api/browser/state` 回
  `state.saved=true / cookies=4 / session_level=4 项 / credentials=true`；
- `POST /api/session/restore` 实机两种文案都对：本来就登着 → 「本来就登录着，没有注入
  （顺手刷新了快照）」；往返实验里注入 4 条 → `logged_in=true`。

## 六、交接块

- **进行中**：无。A+B 已完成并自测；
- **下一步第一个动作**：等用户拍板**节进度表分区 + 页面美化的方案**（见下节），拍板后一次性改
  `panel_gxks/page.py` 的 `renderSections` 与样式；之后才是 P3（`Dockerfile.gxks` + compose 8301，
  `.dockerignore` 属共享改动，动前必须问用户）；
- **阻塞点**：无阻塞。两个已知边界：① `managed`（容器无头）仍从未实跑；② B（自动重登）
  真机一次未跑（故意）。

## 七、用户操作清单（面板已重启，pid 见回复）

1. 打开 `http://127.0.0.1:8301` → 「浏览器与会话」卡片现在有 5 个按钮：
   **启动浏览器 / 停止浏览器 / 检查会话 / 恢复登录 / 重新登录**；
2. 关掉 Chrome 或被踢之后：先「启动浏览器」→ 点 **恢复登录**（零密码，最快）；
   若提示"服务端不认"再点 **重新登录**（会真填表登录一次，弹验证码就手动输）；
3. 卡片里两行新信息：**登录态快照**（时间 + cookie 条数 + 是否会话级）、**账号密码**（是否已配置）；
4. ⚠ 本次重启面板后，`_BROWSERS` 登记表清空 → 「停止浏览器」对你之前那个窗口是灰的
   （它在面板眼里不是"自己起的"），要关就在窗口里直接关。
