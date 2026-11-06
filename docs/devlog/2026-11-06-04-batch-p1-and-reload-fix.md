# 2026-11-06-04 批量五动作核对 + P1 批量启动浏览器 + G-1 热重载归位

## 一、用户提问与回答

用户原话：「那开始『开始学习（跳过 1 个在跑）』的停止，启用，停用，是否也是可以批量操作的呢，是否实现了呢？」

**答案：早已实现**（2026-10-30 造表格时就做了），四个动作用同一个
`POST /api/accounts/batch`，复用 `_stop_one` / `gaccounts.set_enabled`。
实机逐项验过：

| 动作 | 实测结果 |
|---|---|
| 停止（空闲账号） | 原来报「1 个没做成」红色失败 → **已改**为跳过 |
| 停用 test | `ok=True done=1`，`enabled` 变 False ✅ |
| 启用 test | `ok=True done=1`，`enabled` 变 True ✅ |
| 停用（运行中 default） | 拒绝且不被停用 ✅（原来算 failed） |

## 二、改了什么（按「三类结果」统一语义）

### 问题：同一类「预期内不发生」用了两套判定

- 「开始学习」遇到在跑的账号 → `skipped`（绿）
- 「停止」遇到没在跑的 → `failed`（红）← **不一致**

批量停 5 个、4 个本来空闲时，会报「4 个没做成」+ 红 toast，用户以为系统故障。

### 修法：把「没做成」拆成三类

| 类别 | 含义 | 前端呈现 |
|---|---|---|
| `skipped` | 本来就不需要做（已在跑 / 没在跑） | 绿 toast，文案「跳过 N 个（…）」 |
| `blocked` | 状态前提不满足，**用户能自己解决**（有任务在跑，先停再做） | 黄持久提示「有 N 个正在跑，需先「停止」再 X」 |
| `failed` | 真出错（未知代号 / Chrome 没起来 / 写盘失败） | 红提示 |

`ok` 只反映**有没有真失败** → 全跳过也算成功。

### 文件

- `src/xxt/panel_gxks/app.py`
  - `_stop_one`：无在跑任务 → `skipped: True`
  - `accounts_batch`：`disable`/`delete` 遇到在跑 → `blocked: True`；
    汇总拆 `done / skipped / blocked / failed`
- `src/xxt/panel_gxks/page.py`
  - `batchAction`：`disable`/`delete` 的 confirm 里预先写明「哪几个会被拒绝」；
    `CN` 加 `start-browser`
  - 结果呈现三分支：`need_browser` → 摊开原文；`blocked` → 黄条；其余 → toast

## 三、P1：批量「启动浏览器」

### 为什么做

用户原话：「如果是多个账号要同时启动学习，是需要我一个一个去点击吗」——
学习能批量了，但**浏览器还得一个个点「启动并登录」**，7 个账号就是 7 次点击 + 7 次等待。

### 做法：抽 `_browser_start_one()`，单个与批量共用

与 P0 同一思路（那次 `refresh_sections` 自己手写端口检查、主路反而漏了）。
新增模块级 `_browser_start_one(settings, label, run_session=...)`：

- managed 后端 → `skipped`（容器形态本就不在这里起浏览器）
- 端口已活 → `skipped`（已在运行，不重复起）
- 有任务在跑 → `blocked`（起浏览器会断连正在跑的任务）
- Chrome 没起来 → `failed`（原文透出）
- 成功 → 真起 + 注入登录态（`_chrome_start_message`）

`/api/browser/start` 改为调用它，并对「端口已活」做适配（对单按钮而言那是**成功**）。

批量动作新增 `start-browser`，且：

- 端口探活**一次并发**做完（先知道谁已在跑，省掉白起计划任务）；
- 前端按钮**只在选中里真有浏览器没开时才显示**（`deadSel`），
  文案带「（N 个没开）」；
- confirm 写明「每个 10~40 秒、这段时间不要重复点」；
- 请求前先 `toast` 一句，避免长时间无反馈被当成卡死。

### 实机验证 ✅

```
labels=['test'], action='start-browser'
→ ok=True done=1
→ message: 完成 1 个（test）
→ 明细: Chrome 已启动（端口 9233） | ✅ 已用账号密码登录成功 → 可以直接刷课
→ 复核: 9233 listener pid=25448 image=chrome.exe port_alive=True
```

这是第一次真正跑通「点一下批量启动浏览器、它自己把 Chrome 拉起来并登录好」。

## 四、G-1 热重载「归位」（不是 bug，但仍需修）

### 真相回顾（2026-11-06-03 已查清）

数据库 `meta.panel.reload = false`（界面开关被关过），而 `serve()` 是 **DB 优先于 toml**，
所以 uvicorn 根本没开 reload。当时我误判成 bug，是因为把目录当 db 路径传给了 `load()`
（它「读不到就回默认值」）——**证据源搞错了**，非代码错。

### 本次处理

1. **DB 改回 `true`**（走 `gxks_panel_settings.save`，幂等）；
2. **`serve()` 打印配置来源**：现在启动会输出
   `[OK] 热重载：开（来源：数据库 meta.panel.reload（界面开关））→ 改代码自动生效`
   —— 下次 3 秒就能判断，不必再查三层；
3. **新增 `load_strict()`**（G-2）：
   - 返回 `ok / settings / present / error / db_exists`；
   - `present` 是数据库里**真实存在的原始键值**，能区分「没配」与「读不到」；
   - **只读**：文件不存在就不连（`sqlite3.connect` 会新建空库，诊断脚本不许留副作用），
     存在则用 `mode=ro` URI 打开。

### 结果

重启面板后实测：`/api/panel/settings` 返回 `{'reload': True, 'poll_seconds': 4}`，
页面含全部新代码（`start-browser` / `deadSel` / `10~40 秒` / `请先「停止」` 均命中）。

## 五、验证

- `pytest -q`：**1097 passed**（15 subtests）
- 新增/改写测试：
  - `test_gxks_panel_batch.py`：+4（start-browser 起浏览器 / 跳过 / blocked / 真失败）、
    +1（disable 遇运行中 → blocked）、`test_enable_on_disabled_account`；
    改写 `test_stop_reports_accounts_without_job`（跳过≠失败）、
    `test_delete_refuses_running_account`（blocked≠失败）
  - `test_gxks_panel_settings.py`：+3（`load_strict` 区分缺失/不可读、给原始值、
    传目录必须失败且**不创建空库**）
  - `test_gxks_page.py`：+3（批量按钮按 `deadSel` 出现、耗时提醒、停用/删除预警告）
- 页面 JS `node --check`：通过
- 实机：批量 `start-browser` 真的把 9233 的 Chrome 拉起并登录

## 六、改动文件清单

- `src/xxt/panel_gxks/app.py`（`_browser_start_one` 抽取、三类汇总、`serve()` 打印来源）
- `src/xxt/panel_gxks/page.py`（批量栏加按钮、`deadSel`、confirm 文案、三分支呈现）
- `src/xxt/store/gxks_panel_settings.py`（新增 `load_strict`）
- `tests/test_gxks_panel_batch.py`、`tests/test_gxks_panel_settings.py`、`tests/test_gxks_page.py`
- `data/debug/diag_reload_state.py`（改：用 `load_strict` + 打印进程树提示）
- `data/debug/diag_p1_batch_browser.py`（新增，只读）

## 七、交接

**进行中**：无。

**下一步第一个动作**：用户开面板 `Ctrl+F5` 验收批量按钮；
之后决定 **P2（并发上限 + 启动随机错峰）** 是否开工 —— 这是 `PLAN-GXKS.md` §7.3 明确要求的、
目前**完全没做**的一块（批量启动现在是「你勾几个就同时开几个」）。

**阻塞点**：无。

**注意**：

- 实机验证时我启动了 `test`(9233) 的 Chrome（pid 25448），**它现在还开着**。
  不需要就点面板上的「停止」关掉（或告诉我关）。
- 批量 `start-browser` 是**串行**的：7 个账号约 3~5 分钟。期间前端 `BUSY` 锁着，
  重复点击会被忽略（有 toast 提示，不会静默）。
- `data/gxks_browsers.json` 现在记了 `test` 的 pid —— 关掉浏览器后确认该条目被清
  （`/api/browser/stop` 会清；手动关窗口则要等下次启动时的认领清理）。
