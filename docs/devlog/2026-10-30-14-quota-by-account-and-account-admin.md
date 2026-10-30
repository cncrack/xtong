# 2026-10-30-14 A1 定案（额度按账号）+ 面板账号增删改 + 多账号面板优化

> 本篇对应两条用户指令：
> ① 「『日额度按账号』还是『按课程』—— 这个我们方案是如何订的呢」→ 我给出依据，用户「按你建议处理」；
> ② 「面板中多账号管理是如何规划和处理的呢，有具体的方案吗，可以先落地在面板上看效果吗？」

## 一、本次目标

1. 把 **A1（日额度口径）** 定下来并落到代码，让 B1 能开工；
2. 回答「多账号管理怎么规划」，并**先落地到面板上能看见效果**；
3. 顺带优化面板（多账号合计、卡片直连刷新节列表）。

## 二、A1 定案：额度按「账号合计」

### 2.1 先摆事实：现状本来就是按账号，A1 真正问的是另一件事

逐行核过代码后确认（这也是回答用户「方案是如何订的呢」的实证）：

| 位置 | 事实 |
|---|---|
| `adapters/gxks/ledger.py`（改前） | 账本一行是 `at/date/account/section_id/name/minutes/result`，**没有 `course`** |
| `ledger.today_totals()`（改前） | 过滤条件只有 `date` + `account`，去重键 `account+section_id+date` |
| `adapters/gxks/accounts.py` | `daily_sections` / `daily_minutes` 是**账号**属性（写 `accounts.local.json` 每个账号里） |
| `scripts/run_gxks_multi.py` | `quota_left(acc.label, ...)` —— 只传代号 |
| `panel_gxks/app.py` | 卡片「今日 x/10 剩 y」= `acc.daily_sections - today["sections"]` |
| `PLAN-GXKS.md` §11.6 | 方案原话：两个额度都是「**每个账号各自一份**」 |

⇒ **「按账号合计」不是待选项，而是已实现的现状**，且是有意设计的。A1 卡的是
**B1（账号 × 课程分层）**：一个账号下有多门课时，10 节 / 600 分钟是**两门课共用一个池子**
还是**每门课各 10 节**。

### 2.2 结论与理由

**按账号合计；账本加 `course` 字段但只做统计与去重。**

1. **额度是刹车，而封号按账号/IP 维度发生**。按课程分池 = 同一账号一天能干两倍量，
   跟设上限的初衷相反。
2. **「学够」根本不靠额度**。方案已解耦（§11.6 ⭐ + D20）：当日目标 = 平台剩余待学，自动算；
   额度只在"意外多学"时刹车。按课程分额度不能让人多学有用的，只会把刹车拆松。
3. **加字段 ≠ 改口径，且必须加**：现在去重键是 `section_id`，**不同学习计划复用同一个节 ID**
   是常见做法 → 会被当成"同一节"去重 → **少记额度、账号反而超刷**。改成 `course + section_id`。
4. **不做 `gxks.quota_scope` 开关**：口径只有一种，加开关等于给未来留一个"两套语义"的坑。

### 2.3 落地（`ledger.py`）

- `record(..., course="")` 新增可选参数；老账本没有该字段 → 读的时候按 `""` 处理，新旧混读不炸；
- `today_totals(..., course=None)`：缺省**不按课程过滤**（额度口径）；给了就只统计那门课（展示用）；
  去重键 `(course, section_id)`；新增返回 `courses: {课程: {sections, minutes}}`；
- `quota_left(..., course=None)`：**不限的那一侧返回 `None` 而不是 0**。

### 2.4 顺带修的两个真隐患

| # | 隐患 | 影响 |
|---|---|---|
| 1 | `parse_accounts` 里写 `secs if secs > 0 else DEFAULT` | 用户按 §11.6 把额度设 `0 = 不限`，**读回来被悄悄改回 10 / 600** —— “不限”从来没生效过。改为 `>= 0` |
| 2 | `quota_left` 把 0 当成"额度 0 → 已用完" | 与上一条同源：这正是超星 `reached_locally()` 把 0 读成"今天已学够"的老毛病。改成返回 `None` |

配套改了调用方（`run_gxks.py` / `run_gxks_multi.py`）对 `None` 的处理，
日志里 "不限" 也照实打出来，不再打印 `还剩 0 节`。

## 三、面板多账号管理（本次落地）

### 3.1 规划（回答「怎么规划」）

多账号在面板里的完整生命周期，分四层，本次把**第 2 层（增删改）**补齐：

| 层 | 做什么 | 状态 |
|---|---|---|
| 1 账号表 | `data/accounts.local.json`：一账号一端口一 profile | 已有（devlog 29-01） |
| 2 **增删改** | **面板对话框新增 / 编辑 / 停用 / 删除** | ✅ **本次落地** |
| 3 总览 | 卡片 + 合计行 + 切账号看详情 | 卡片已有（devlog 12），**合计行本次新增** |
| 4 调度 | `run_gxks_multi.py` 串行；额度按账号合计 | 已有；本次同步 `None` 语义 |

**三条硬约束**（写进 `accounts.save_account` docstring）：

1. **原子写**：临时文件 + `os.replace`，中途崩了不留半张表（半张表 = 账号凭空消失）；
2. **端口唯一 + 越界报错不静默钳制**：静默钳制会让人以为"改成功了"；
3. **只改目标那一条**：其它账号（含用户手写的额外字段、顶层备注键）原样保留
   —— 面板与命令行可能同时在编辑。

另外三条**产品决策**：

- **凭据空值 = 不改**：面板不回显密码（后端只回布尔），所以空字符串必须解释成"别动"，
  否则每次编辑都会把凭据清空；
- **在跑的账号不许改 / 停 / 删**：改端口会让子进程的归属当场错位；
- **删最后一个账号一律拒**：删完面板就空了，用户连"新增"按钮都得靠猜。
- **删除不删 profile 与登录态快照**：人可能后悔，重登一次很贵。

### 3.2 后端

| 接口 | 说明 |
|---|---|
| `POST /api/accounts/save` | 新增（无 `original_label`）/ 修改（有）；写 `account-add` / `account-edit`，**修改带「旧值 → 新值」** |
| `POST /api/accounts/toggle` | 启用 / 停用，写 `account-toggle` |
| `POST /api/accounts/delete` | 删除，**要求 `confirm: true`**，写 `account-del` |
| `GET /api/state` 的 `accounts[]` | 新增 `courses` / `has_credentials`（**只回布尔**） |
| `GET /api/state` 的 `cards[]` | `today.left_sections` 在"不限"时回 **`null`**；新增 `today.by_course` |

`gxks_ops.ACTIONS` 新增 4 个动作词，面板 `ACTION_CN` 同步中文名。

### 3.3 前端

- 区块头 **`＋ 新增账号`** 按钮 + `<dialog>` 表单（代号/显示名/端口/日节数/日时长/账号/密码/启用）；
  **密码框永远不回填**（面板拿不到，回填等于自欺）；
- 卡片动作区新增：**刷新节列表**（台账 B8，不用先切账号）、**✎ 编辑**、**停用/启用**、**删除**
  （全部 `stopPropagation`，不会顺手切账号）；
- 多账号时顶部一行**合计**：`合计 N 节 · M 分钟｜运行中 / 空闲 / 额度用完 / 停用`；
- `0 = 不限` 在卡片上显示成「不限」，不再显示「0 节」；
- 修 `load()`：选中账号被删掉后原本会一直报"连接中断"，现在回落第一个账号。

## 四、改动文件清单

| 文件 | 改了什么 |
|---|---|
| `src/xxt/adapters/gxks/ledger.py` | 模块 docstring 增 A1 口径；`record(course=)`；`today_totals(course=)` + `courses` 汇总 + 去重键改 `(course, section_id)`；`quota_left` 对不限侧返回 `None` + `unlimited` 字段 |
| `src/xxt/adapters/gxks/accounts.py` | `parse_accounts` 的 `> 0` → `>= 0`（让 0=不限 真生效）；新增 `LIMITS` / `AccountError` / `_read_doc` / `_write_raw` / `_legacy_as_row` / `_read_raw_with_legacy` / `save_account` / `delete_account` / `set_enabled`；`_CN` 中文键名 |
| `src/xxt/panel_gxks/app.py` | `_account_rows` 加 `courses` / `has_credentials`；`_account_cards` 的 `left` 支持不限 + `by_course`；新增 3 个账号管理路由（含"写前取旧值"做留痕、在跑保护、最后一个账号保护） |
| `src/xxt/panel_gxks/page.py` | `<dialog>` 表单 + `.fld`/`.dlg-*` 样式；`acctCard` 加 4 个按钮与"不限"渲染；`renderAccounts` 加合计行；新增 `apiPost`/`openAccount`/`closeAccount`/`saveAccount`/`toggleAccount`/`deleteAccount`/`refreshSectionsFor`；`load()` 回落；`ACTION_CN` 加 4 项；`STATE` 全局 |
| `src/xxt/store/gxks_ops.py` | `ACTIONS` 加 `account-add/edit/toggle/del` |
| `scripts/run_gxks.py` | 记账带 `course`；额度打印支持"不限"；`left_sections is None` 时不收敛节数 |
| `scripts/run_gxks_multi.py` | 同上（`left is None` 时用 `--max-sections` 或默认 3） |
| `开面板.bat` | **新增**：双击起面板（独立窗口）+ 自动开浏览器；端口已在监听时不重复起 |
| `验收自检.bat` | **新增**：`pytest` → 导出页面 JS → `node --check`（无 node 则 SKIP） |
| `.gitignore` | 加 `pytest-of-*/`、`tmp*/`（pytest 临时目录偶尔落到项目根） |
| `tests/test_gxks_ledger.py` | **+7 条**：0=不限、单侧不限、老账本无 `course`、按账号合计、跨课程同 ID 算两节、`course` 只缩统计 |
| `tests/test_gxks_accounts_write.py` | **新增，35 条**：新增/校验/代号小写/端口唯一/越界/0=不限/编辑不动 enabled·额度·courses·display/凭据空值不改/清凭据/课程逗号串/启停/删除/原子写/顶层键保留/坏文件覆盖/**legacy 迁移 4 条** |
| `tests/test_gxks_panel_accounts.py` | **新增，18 条**：新增/回包无凭据/端口冲突/非法代号/越界报错中文/留痕/编辑保密码/旧值→新值/启停/删除二次确认/最后一个账号/在跑保护/卡片 null/`by_course`/OSError 不变 500 |
| `tests/test_gxks_page.py` | **+12 条**：对话框与按钮存在、密码不回填、删除问两次、编辑时代号只读、空数字框被拒、`apiPost` 处理 400、不限渲染、动作词中文、卡片刷新节列表、合计行 |
| `tests/test_entry_bats.py` | **新增，11 条**：两个 bat 存在 / `chcp 65001` / `cd %~dp0` / **echo 行纯 ASCII** / 不用 `rem` 与 `timeout /t` / 防重复启动 / 独立窗口 / 无 node 时 SKIP |
| `docs/gxks/TODO-GXKS.md` | A1 移入「已定案」并写理由；B8 勾掉；新增 B9（代理自检）/ B10（账本实跑核对）；P2 加 C4；P3 加 2 条边界 |
| `docs/gxks/PLAN-GXKS.md` | §11.6 增「A1 定案」段；§九 变更记录 +1；§12.5 第 3 条部分解除 |

## 五、验证（已全部实跑通过）

> 本次会话**前半段 shell 全程不可用**（每次 `pwsh` 返回 `[exit code: 3221225794]`，`0xC0000142`
> DLL 初始化失败），`pytest` / `git` / 端口检查全部跑不了。用户放开权限后 shell 恢复，
> **下面全部是实跑结果**，不是"应该能过"。

| 项 | 结果 |
|---|---|
| `pytest` 全量 | ✅ **632 passed**（基线 550，本次 +82），1 warning（httpx 弃用，非本次引入） |
| 页面 JS `node --check` | ✅ 通过（PAGE 36666 字符 / JS 23976 字符） |
| 面板重启 | ✅ 旧进程 pid 27460 用的是**系统 python**（`C:\Program Files\Python311`）→ 页面 26752 字符、无 `openAccount`，确认是旧代码；杀后用 venv 重启，页面 **36666 字符**，`openAccount`/`apiPost`/`f-sections`/`refreshSectionsFor`/`account-toggle`/`合计` 全部命中 |
| 账号新增（含 legacy 迁移） | ✅ `yi@9223` 新增后，表内是 `default@9222, yi@9223` —— legacy 账号被正确带入 |
| 端口冲突 | ✅ 新增 `bing@9222` 被拒：`端口 9222 已被 default 占用——一账号一端口，请换一个（如 9223 / 9224）` |
| `0 = 不限` | ✅ `daily_sections=0` → `limits.sections=0`、`today.left_sections=null` |
| 删除二次确认 | ✅ 不带 `confirm` 被拒：`删除需要二次确认（前端会先问一次）` |
| 启停 | ✅ `toggle` 后卡片 `enabled=false` |
| 操作记录留痕 | ✅ 四条动作齐全，`account-edit` 带「日节数 6 → 0，日时长 400 → 0」 |
| 清理 | ✅ 删掉测试账号，表内恢复为 `default@9222` 单账号 |
| 两个 bat | ✅ `验收自检.bat` 端到端跑通（632 passed + JS OK）；`开面板.bat` 两个分支都验过（起面板 / 已在运行） |

### 5.1 实跑揪出的**第六个**真 bug（最严重的一个）

**现象**：`data/accounts.local.json` 原本不存在（走 legacy 回落，面板显示 `default`）。
在面板上「新增账号」时，`save_account` 以**空表**为底新建文件 →
**legacy 里那个真实账号从面板上消失了**，而且后续端口冲突检测也查不到它（它已不在表里）。
实测证据：新增 `yi` 后表内只剩 `yi@9223`，`default` 没了；再新增 `bing@9222` **没有报端口冲突**。

**根因**：`_read_raw()` 只读新表；新表不存在时返回空列表，而 `load_accounts()` 的回落分支
（新表为空才读 legacy）在**文件被创建的那一刻就失效了**。

**修法**：新增 `_legacy_as_row()` + `_read_raw_with_legacy()` —— 新表**不存在**时，
先把 legacy 账号折成一行带进来；`save_account` / `delete_account` / `set_enabled` 三处都改用它。
新表**一旦存在就不再**折 legacy（否则用户删掉的账号会自己回来）。

**回归测试**：`test_new_table_starts_from_legacy_account`、
`test_new_account_cannot_steal_legacy_port`、`test_delete_can_remove_legacy_account`、
`test_existing_table_does_not_resurrect_legacy`，并给测试文件加了 `autouse` 的
`XXT_ROOT` 隔离 fixture（否则测试会读到开发机真实 legacy 账号，表现为"端口 9222 已被 default 占用"这种看不懂的失败）。

### 5.2 入口脚本（新增）

用户问「为什么要跑命令才能打开面板，不是直接打开就行了吗」→ 加两个双击入口：

| 文件 | 作用 |
|---|---|
| `开面板.bat` | 双击即起面板（独立窗口）+ 自动开浏览器；**端口已在监听时不重复起**（避免 WinError 10048） |
| `验收自检.bat` | 改完代码跑这个：`pytest` → 导出页面 JS → `node --check`（没装 node 就 SKIP） |

⚠ **bat 的两条硬约束**（实测踩到并写进 `tests/test_entry_bats.py`）：
① `echo` 出去的行**必须纯 ASCII** —— 中文 + GBK 控制台会逐行报
`'xx' is not recognized as an internal or external command`（第一次写就中了）；
② 不用 `rem` 注释（改 `::`）、不用 `timeout /t`（无控制台时会挂住）。


**静态自检 + 实跑共揪出六个真 bug（全部已修）**：

1. `accounts_save` 里"旧值 → 新值"的 `before` 原本写在 `save_account()` **之后**，
   读到的已经是新值 → 留痕永远显示"没变"。已把取值移到写之前，
   并加 `test_edit_log_records_old_to_new` 钉死；
2. 编辑表单里数字框**留空**时 `Number("")` 是 `0`，会被当成"不限"悄悄存进去。
   已改成显式拦截（"不能为空（要「不限」请显式填 0）"）并加断言；
3. `save_account` 的额度/端口缺省值取的是**全局默认**（10 / 600 / 9222），
   于是一次"只改端口"的编辑会把用户设的额度**悄悄重置回默认**。已改成缺省取
   **该账号原来的值**，并加 `test_edit_without_quota_fields_keeps_existing_values` 钉死；
4. 同一类问题的另两处：`display`（显示名会被洗成空）与 `courses`（会被清成"跑全部课程"，
   **多跑课 = 多耗额度**）。都改成"只有显式给了才动"，各加一条断言；
5. 前端 `toggleAccount` / `deleteAccount` / `saveAccount` 原来直接 `r.json()`：
   后端 `_safe_label` 抛的 400 是 FastAPI 的 `{"detail": ...}`（没有 `ok` 字段），
   `data.ok` 是 `undefined` → falsy → **失败会被 toast 成“已提交”**。已抽 `apiPost()`
   统一处理 `detail` 并加断言；
6. **最严重的一个**：新增账号会把 legacy 账号挤出面板（见 §5.1，已修 + 4 条回归测试）。

## 六、关键决策及理由

1. **额度按账号、`course` 只统计**：见 §二。
2. **不做 `gxks.quota_scope` 开关**：一种口径比两种方言安全（超星那套 0 的两义就是这么来的）。
3. **凭据空值 = 不改**：面板不回显密码是硬边界，那就必须把"空"解释成"别动"。
4. **删除要求前端 `confirm` + 后端 `confirm: true` 双保险**：删除是不可逆动作。
5. **编辑对话框不让改代号**：改代号等于重命名，登录态快照文件名（`storage_state_gxks_<代号>.json`）
   与 profile 目录都会对不上；要做就单独做一次"重命名 + 迁移文件"。
6. **合计行只在多账号时出现**：单账号时它就是卡片自己，纯噪声。
7. **`left_sections` 用 `null` 表示不限**：0 与 null 语义必须分开，否则又是一次"0 被读成已用完"。

## 七、已知边界（本次新增）

1. **UI 未逐个手点**：对话框/合计行只经 API 实测 + 静态断言；**建议你打开页面看一眼**
   （尤其 `<dialog>` 在你浏览器里的实际观感）；
2. **编辑不允许改代号**（P3 第 8 条）；
3. **删除不删 profile / 快照**（P3 第 7 条）；
4. `sections_<代号>.json` 仍无课程维度（B1 未做，本次只动了额度口径）；
5. **`开面板.bat` 起的面板会跟着 bat 窗口一起被收掉**（如果在 AI 工具终端里跑）。
   你自己**双击**运行不受影响；这一点与 AGENTS.md 的"长驻进程必须在沙箱外起"是同一条。

## 八、交接块

- **进行中**：无。A1 已定案并落地；面板账号增删改 + 两个 bat 已实跑验收；
- **下一步第一个动作**：开工 **B1（`sections_<代号>_<课程>.json`）**——现在额度不按课程分池，
  B1 只剩"缓存文件分课程"这一件事；
- **阻塞点**：**无**。632 全绿，面板已用新代码重启并实测。

### 你现在只需要做一件事

**双击 `开面板.bat`**（项目根目录），它会起面板并自动打开 `http://127.0.0.1:8301`。
然后在页面上看一眼：

1. 账号区右上角有 **`＋ 新增账号`**；
2. 卡片底部一排：**开始学习 / 刷新节列表 / ✎ 编辑 / 停用 / 删除**；
3. 点 **✎ 编辑** → 表单里「日节数上限」等能正确回填（密码框**永远是空的**，这是故意的）。

以后改完代码，双击 **`验收自检.bat`** 就能一次跑完 pytest + JS 检查。
