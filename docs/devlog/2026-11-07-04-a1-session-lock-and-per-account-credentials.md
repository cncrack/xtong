# 2026-11-07-04 A1 会话锁 + 凭据按账号 + 浏览器启动串行化

> 用户 2026-11-07：「先做 A1，然后我现在要用另一个真实账号来进行多账号，2 个账号同时刷课，
> 目前代码功能是否完善可实现」→ 核对后查出**三个缺口**（A1 未做 + 一个 P0 顶号 bug + 一个 P1 竞态），
> 用户拍板「都做」→ 本文件。

---

## 一、核对结论：2 个账号同时刷，改之前**不能直接跑**

架构是对的（一账号 = 一进程 + 一 Chrome + 一端口 + 一 profile），并发闸/排队/错峰/账本隔离都已在位。
但存在三个会真咬人的缺口：

| # | 缺口 | 后果 |
|---|---|---|
| **P0** | 自动重登**不分账号**，所有脚本 `load_credentials()` **无参**读全局 `data/account.local.json` | **账号 B 掉登录 → 用 A 的账号密码登 B → A 被顶号**（A 那边在播的会话断），B 还没登上，A 的登录态可能写进 B 的快照 |
| **P1** | `data/tmp/chrome_start.json` / `chrome_start.result.json` **全局唯一** | 两个账号**同时**点「启动并登录」→ 后写的 spec 覆盖前一个 → 起错 profile / 端口对不上 |
| **A1** | 账号级会话锁**从未落地**（`PLAN-GXKS.md:222` 第一条硬约束，欠了一个多月） | 双开只靠面板内存表 `_JOBS`：另一个面板、手敲 `run_gxks.py`、面板重启后认领失败 都能绕过。**同账号两路心跳是最强机器指纹** |

---

## 二、P0：凭据按账号取（`login.load_credentials_for`）

### 改法

新增 `load_credentials_for(acc, settings)`：账号表里的 `name`/`pw` 优先。

### ⚠ 第一版是错的，被测试当场抓住（这条要记住）

第一版写成「账号没填 → **回落**全局旧文件」。看起来兼得兼容与修复，实际上把漏洞又补回来了：
**B 没配凭据时，仍然会去读全局文件里 A 的凭据** → 照样顶号。
`test_relogin_uses_per_account_credentials` 直接抓到（B 被放行了）。

**最终规则（安全边界，别放宽）**：

- 账号自己填了 `name`+`pw` → 用它；
- 没填 → **只在「账号表里只有 1 个账号」时**才回落全局旧文件（单账号老环境兼容）；
- **≥2 个账号 → 一律不回落，返回 None**（按"未配置"处理，掉线即停等人工）。

理由：全局文件是**单账号时代**的遗留，多账号下它归属于哪个号是**不可知**的。
宁可"未配置"，也不能在一个不认识的账号上试一份来源不明的凭据。

### 另一个坑：读账号表必须按 `settings.data_dir`

`_single_account()` 里原来写 `gaccounts.load_accounts()`（默认路径跟 `find_project_root()`）。
`settings` 与 `find_project_root()` **可能不是同一个目录**（测试、`XXT_ROOT` 指到别处）。
用错目录会把**真实项目里的账号表**读成"单账号" → 回落 → 又变顶号。
改成显式 `load_accounts(settings.data_dir / "accounts.local.json", ...)`（测试当场抓到）。

### 改的调用点（4 处）

| 文件 | 原来 | 现在 |
|---|---|---|
| `scripts/run_gxks.py` | `glogin.load_credentials()` | `load_credentials_for(acc, settings)`，且 `acc` 提前取（后面额度判定复用，不重复读表） |
| `scripts/gxks_sections.py` | 同上 | 同上 |
| `scripts/gxks_work.py` | `_ensure_login(page, settings)` | 加 `label` 参数 → `load_credentials_for` |
| `scripts/gxks_session_check.py` | `_relogin(page, settings)` | 加 `label` 参数 → `load_credentials_for` |
| `panel_gxks/app.py` | `is_configured(credentials_path())` 两处 | `is_configured_for(acc, settings)` |

面板的报错文案也改了：不再说「data/account.local.json 里没有账号密码」，
而是「**账号 X 没有可用的账号密码：请到「✎ 编辑」里给它填上自己的**（不要依赖全局文件，多账号会串号）」。

---

## 三、A1：账号级会话锁（`adapters/gxks/locks.py`，新文件）

### 为什么不直接用 `core/runmark`

`runmark` 的键是 `account_id: int`（超星账号表主键），第二平台用 `label: str`。
硬塞 int 要先哈希 —— 而撞键的后果是**一个号把另一个号挡在门外**（或更糟：放行双开）。

所以**沿用同一套判据**（`pid_alive` + `pid_owns_file` 防 pid 复用，直接调 `runmark`，不复制实现），
只换文件名：`data/run/gxks_<label>.json`（与超星侧的 `<int>.json` 永不撞）。

### 守什么（21 条测试）

① 别人持有 → 拒；② 自己人（自己/父进程）持有 → 放行**且不重写**；
③ 进程已死的陈旧标记 → 可接管（否则一次崩溃永久锁死该账号）；
④ pid 复用 → 不判活（否则停止时 taskkill 掉无关进程）；
⑤ 不同 label 完全独立；⑥ 坏文件当空闲；⑦ 释放要校验归属。

### 「自己人」判据（超星侧 2026-10-27 踩过的坑，直接搬过来）

面板是「先 spawn、立刻替子进程写标记（`pid=proc.pid`）」。子进程自查读到这条**自己**的标记，
若不排除「自己/父进程」→ 一秒自杀（表现是「定时触发了但学不了」）。
判据必须含 **ppid**：`.venv\Scripts\python.exe` 是 venv 的**重定向器**，
`Popen` 拿到的是 shim 的 pid，真进程的 ppid 才是它。

### 接线

| 位置 | 做什么 |
|---|---|
| `run_gxks.py` 启动 | 抢锁 → 失败 `[FAIL]` **返回 5**，且**在网络自检之前**（双开时不该先访问一次平台）；`atexit.register(release)` 兜住十几处 `return` 的收尾 |
| `_study_one()` | spawn 前查 `holder()` → `skipped + locked` 并说清"可能来自另一个面板或命令行" |
| `_start()` | spawn 后**替子进程写锁**（`pid=proc.pid`），写失败只吞（锁是红线但不该拦主流程） |
| `_stop_one()` | 杀完按 pid **校验归属**再释放（不校验会把别人刚抢到的锁删掉 → 又能双开）；面板表里没有但落盘锁被占 → **不杀不认识的进程**，说清"不是本面板发起的" |

---

## 四、P1：浏览器启动串行化

`data/tmp/chrome_start.json` 与计划任务动作都是**全局唯一**的。

- 试过「spec 文件名带 label」——**行不通**：计划任务的动作是注册时写死的
  `-File start_chrome.ps1`，运行期传不进参数/环境变量，分文件只会让脚本找不到 spec。
- 最终用**进程内互斥锁** `_CHROME_START_LOCK` 包住整个「写 spec → 触发 → 等端口」，
  实现拆到 `_start_chrome_interactive_locked()`。
- 代价：第二个账号排队等前一个起完（10~40 秒）。可接受 —— 同时开两个 Chrome 反而抢资源。

---

## 五、验证

- `pytest`：**1170 passed**（1151 → 1170，+19），15 subtests
- 新增 `tests/test_gxks_locks.py` **21 条**：
  - 锁本体 16 条（per-label 独立 / 非法代号拒 / 自己人与父进程 / 不重写 / 死锁接管 /
    pid 复用 / 坏文件 / 归属校验释放 / describe 无凭据）；
  - 面板集成 5 条（另一端持锁 → 拒绝且不起子进程；别的账号不受影响；面板替子进程写锁；
    停止清锁；锁被别的入口持有时不杀也不谎报成功）；
  - 脚本集成 2 条（别的活进程持锁 → 返回 **5** 且**没走到连 CDP**；网络自检失败退出后**不留锁**）。
- `tests/test_gxks_login.py` +7 条：按账号取 / 多账号**不回落** / 单账号可回落 /
  无 settings 不回落 / 占位符 / 半填 / 非字符串字段。
- `test_gxks_panel_browser.py` 改了 1 条 + 新增 1 条 P0 回归
  （`test_relogin_uses_per_account_credentials`：全局文件有 A 的凭据、B 在账号表里没填 → B 必须被拒）。
- `test_gxks_start_chrome_script.py`：断言改看 `_start_chrome_interactive_locked`，
  新增 `test_browser_start_is_serialized`（谁把锁去掉立刻红）。
- `scripts/start_chrome.ps1` 的 **UTF-8 BOM 被我的编辑弄丢过**，`test_has_utf8_bom` 抓到并已恢复
  （这正是那条测试存在的理由：无 BOM → PowerShell 5.1 按 GBK 读 → 中文注释乱码 → 解析失败、静默返回码 1）。
- `scripts/push_check.py`：**PASS**（拦截 0；告警 169 条都是文档里讨论 cookie 语义的既有内容）

---

## 六、改动文件清单

- **新增** `src/xxt/adapters/gxks/locks.py`、`tests/test_gxks_locks.py`
- `src/xxt/adapters/gxks/login.py`：`load_credentials_for` / `is_configured_for` / `_single_account` / `_clean`
- `src/xxt/panel_gxks/app.py`：`_CHROME_START_LOCK` + `_start_chrome_interactive_locked`；
  `_study_one` 查锁；`_start` 替子进程写锁；`_stop_one` 释放锁 + 不杀陌生锁持有者；
  `_chrome_start_message` / `session_relogin` 改按账号判凭据
- `scripts/run_gxks.py`：抢锁（访问平台之前）+ `atexit` 释放 + 按账号取凭据
- `scripts/gxks_sections.py`、`scripts/gxks_work.py`、`scripts/gxks_session_check.py`：按账号取凭据
- `scripts/start_chrome.ps1`：注释说明为何不改文件名（BOM 已恢复）
- `tests/test_gxks_login.py`、`tests/test_gxks_panel_browser.py`、`tests/test_gxks_start_chrome_script.py`

---

## 七、交接

**进行中**：无。A1 + P0 + P1 全部完成并全绿。

**下一步第一个动作**：用户按下面「验收清单」实机跑 2 个账号。

**阻塞点**：无。

**给用户的验收清单（照做即可）**：

### 前置（一次性）

1. 在面板「＋ 新增账号」里把第二个账号加进去：
   - **代号**用英文（如 `yi`），**端口**必须与第一个不同（如 9223）；
   - **账号密码必须给它单独填一份** —— 这是本次修的核心。
     两个账号都填了，掉登录时各登各的，不会串号。
2. 「账号设置 → 面板设置」确认：**同时学习上限 = 2**（默认就是 2）、
   **启动错峰 = 30 秒**（默认）、**全局日时长 = 0**（不限）。

### 测试 1：并发不双开（起得来、互不影响）

表格里勾两个账号 → 「**启动并登录**」→
预期：两个 Chrome 各自起来（第二个会排队等第一个，属正常），**端口不同、profile 目录不同**。

然后勾两个 → 「**学习**」→ 预期：
- 两个任务都进「运行日志」的 `jobs`，`live_study = 2`；
- 卡片上「并发 2/2」；
- 两边的刷课日志分别落 `data/logs/gxks-study-<代号>-*.log`，互不混杂。

### 测试 2：⭐ 会话锁真的挡双开（最关键，可自己验）

在面板里让 `jia` 开始学习后，**另开一个 PowerShell** 跑：

```
cd E:\xxt-gxks
.venv\Scripts\python.exe scripts/run_gxks.py --label jia --account-label jia --no-netcheck
```

预期**立刻**输出并返回：

```
[FAIL] 账号 jia 已有会话在跑（pid 12345，自 2026-11-07 18:30:00 起）→ 不重复开
       同账号两路心跳是最强的机器指纹，也是 PLAN-GXKS §7.3 的第一条硬约束。
       要重开请先在面板点「停止」，或确认那个进程已经退出。
```

并且**不会**去连 Chrome、不会访问平台（这是「最小访问原则」，也是 `test_run_gxks_exits_5_when_other_holds_lock` 守的）。

再试反例：`--label yi`（另一个账号）→ 应该**能正常起**（不同账号互不影响）。

### 测试 3：停止后锁被清（不会把账号永久锁死）

面板里点 `jia` 的「停止」→ 再跑上面那条命令 → 预期这次**不再报已有会话**
（会走到网络自检/连浏览器，说明锁已释放）。

### 测试 4：P0 顶号已修（必须验，这是本次最重要的修复）

1. 只给 `jia` 填账号密码，**`yi` 不填**；
2. 面板上对 `yi` 点「重新登录」→ 预期**直接拒绝**：
   「账号 yi 没有可用的账号密码：请到「✎ 编辑」里给它填上自己的…」
   —— **绝不能**出现"拿 jia 的密码去登 yi"；
3. 反过来给两个都填上，各自「重新登录」→ 各登各的。

### 测试 5（可选）：并发启动浏览器不再撞文件

两个账号行上**几乎同时**各点一次「启动并登录」→ 预期两个都能起来，
端口各自正确（不再是"只有一个起得来、另一个端口对不上"）。

**已知边界**：
- 第二个浏览器会等第一个（进程内互斥，10~40 秒），这是有意的；
- 锁是**落盘**的，面板重启后仍然有效；如果某次进程被强杀留下陈旧锁，
  `locks.live()` 会因 pid 不存活而忽略它（不会永久锁死）。
