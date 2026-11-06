# 2026-11-06-03 实机验收 P0 + 查清热重载「没生效」的真相

## 一、本次目标

用户要求：① 把「热重载不生效」记成待办 ② 告诉他下一步做什么。

## 二、实机验收（用户授权我代为执行重启）

用户原话：「命令这么麻烦，我要敲很久，还是你来吧」。此前约定是「GUI 操作归用户」，
这次用户明确授权代为执行**进程级**操作（不含点击浏览器窗口）。

### 执行过程

1. **先记录现状**（关键：分清哪些进程能动）：
   - 面板：pid 20276（venv shim）→ 22736（真进程），8301 监听者是 22736；
   - 学习任务：pid 12456（shim）→ 3336（真进程），09:14:25 起，正在跑第 1 节。

2. **只停面板**（`Stop-Process -Id 22736`，**不用** `taskkill /T`）：
   - 停完立刻验证：学习任务 3336 / 12456 **两个都存活**；
   - 面板残留：0 个。

3. **`schtasks /run /tn xxt-gxks-panel`** → 新面板 16640 → 24720；
   - 学习任务全程无感，日志 `tick` 持续 30 秒一次（停面板时 1113 秒 → 验完 1143 秒）。

### 验收结果（全部通过）

| # | 项 | 实测输出 |
|---|---|---|
| 1 | 孤儿任务认领（11-05 的 `adopt_orphan_jobs`，**首次实机验证**） | `jobs: label=default kind=study alive=True since=09:14:25` |
| 2 | 卡片「停止」可点（11-06-01） | `started_by_panel=True since=09:13:59 port_alive=True` |
| 3a | 批量「浏览器没开」拦截 | `ok=False failed=1 need_browser=True`，**spawn 未被调用** |
| 3b | 跳过 ≠ 失败 | `ok=True done=0 skipped=1 failed=0`「跳过 1 个（default 已在跑）」 |
| 4 | 已完成区默认展开 | 页面含 `const SEC_OPEN = { todo: true, done: true }` |
| 5 | 节进度 | **done=25/36, practice_ready=25**（已对齐，比昨晚 +1 节） |

新代码确认已生效（7 项源码级检查）：`startStudySafe` 存在、`.bb-note` 存在、
旧 `startStudy` 已删、`stopBrowser` 存在、复选框提示文案存在。

## 三、热重载「不生效」——查清真相（**不是 bug**）

### 现象

`panel.reload` 页面显示 `True`，但面板跑了 58 分钟、代码改了三轮，**一次都没重启**。

### 走了三条弯路（都值得记）

1. **先怀疑 watchfiles 没装** → 确认未装，uvicorn 退回 `StatReload`。
   但读源码后发现 `StatReload` 只在 `reload_dirs` 里 `rglob("*.py")` 比 mtime，逻辑没问题。
2. **隔离验证 StatReload 检测逻辑** → 手工构造实例，改 `page.py` mtime，
   `should_restart()` **正确返回了那个文件**。说明检测没问题。
3. **跑一个最小 uvicorn 探针**（`data/debug/_reload_probe/`，port 8399）→
   **热重载完全成功**：日志 `StatReload detected changes in 'app.py'. Reloading...`，
   worker 从 16200 变成 33808，HTTP 仍 200。

→ 结论：uvicorn + StatReload 在这台机器上**是好的**，问题在面板自己的启动参数。

4. **查真实面板进程树** → 只有两层（16640 shim → 24720 真进程），
   **没有 `spawn_main` worker** → reload 实际是**关的**。

### 真因

```python
# serve() 的取值顺序：toml 默认 → **DB 覆盖**
reload_on = bool(panel.get("reload", True))          # True
db_reload = gxks_panel_settings.load(_db_path(settings)).get("reload")
if db_reload is not None:
    reload_on = bool(db_reload)                      # ← DB 里是 False
```

数据库 `meta.panel.reload = "false"`（用户在界面上关过），
**DB 优先于 toml**，于是 `reload_on=False`，uvicorn 根本没开 reload。

### 我自己犯的错（第 8 条纪律的来源）

第一次查的时候，我把 `settings.data_dir`（**目录**）当成 db 路径传给了
`gxks_panel_settings.load()`：

```python
gp.load(s.data_dir)   # ← 错，应该是 s.data_dir / 'gxks_questions.db'
```

`load()` 的设计是「**读不到就返回默认值**」（这是它该有的行为，面板不该因为读不到配置就起不来），
于是返回了 `{'reload': True}` → 我据此写下「reload=True 却没生效，是 bug」。

**真相：配置本身是 false，系统行为完全正确。**

教训（已写进 STATUS 第 8 条）：**读配置类函数若"读不到就回默认值"，必须先验证它真的读到了**；
下结论前先问「我的证据源是不是我想的那个」。

修正后的诊断脚本 `data/debug/diag_reload_state.py` 会同时打印：
三层配置（内置默认 / toml / DB）、**原始 `meta` 行**、以及最终 `serve()` 会用的值。

## 四、结论与待办

- **热重载不是 bug，是设置被关掉了**（G-1）；
- 但**默认值 true + DB false 的组合仍然危险**：界面开关、toml、DB 三处，
  用户很容易以为「开着」，实际是关的。所以仍作为待办修（见 STATUS §4 G-1）。

### 建议修法（两种，等用户拍板）

**A. 最简单的**：把 DB 里 `meta.panel.reload` 改回 `true`，重启面板验证进程树出现 worker。
- 优点：零代码改动、立刻见效；
- 缺点：开关仍在，下次还可能被误关。

**B. 更彻底的**：去掉这个开关，热重载**永远开**，只留一个 CLI 参数
（`--no-reload`）供排查「到底新代码还是旧代码」时用。
- 优点：消灭「以为开着其实是关的」这一类误判 —— 11-05 已经被咬过两次；
- 缺点：热重载在 Windows + StatReload 下每次改代码都会重载（代价是几秒），
  且如果我改的代码有语法错，面板会反复重启失败（但日志会说清楚）。

> 我的倾向：**B**，理由是这个开关的收益（省一次重启）远小于它的风险
> （让人误判代码没生效）。但这是行为改变，按约定要用户拍板。

## 五、改动文件清单

- `STATUS.md`：§3 记录实机验收结果与第 8 条纪律；§4 新增「第二平台待办」表 G-1/G-2
- `docs/devlog/2026-11-06-03-reload-truth-and-live-acceptance.md`（本篇）
- `data/debug/diag_reload_state.py`（新增，只读，不入库）
- `data/debug/diag_restart_needed.py`（新增，只读，不入库）

**无生产代码改动**（本轮只做验收 + 诊断）。

## 六、交接

**进行中**：无。

**下一步第一个动作**：用户拍板 G-1 的修法（A 改 DB / B 去掉开关），
然后按用户之前说的「先做 P0 再做 P1 和 P2」推进 **P1 批量启动浏览器**。

**阻塞点**：无。

**注意**：

- 验收时我用 API 直接调了两次 `/api/accounts/batch`（`test` 和 `default`），
  **都没有真正启动任务**（前者被浏览器闸拦下，后者被「已在跑」跳过），
  但这会在 `gxks_ops` 里留下 action 记录 —— 如果用户对日志里的这两条有疑问，就是这个原因。
- 学习任务 3336 仍在跑（4 节，09:14 起）。**不要用 `taskkill /T` 停面板**，
  它会把学习任务一起带走（`/T` = 整棵进程树）。
