# 2026-11-03-03 一键启动浏览器并自动登录

> 接续 `-01` / `-02`。本篇记录 2026-11-03 20:20~21:55 的工作。

## 本次目标与结果

用户提问：「你启动开启了浏览器，为何不能自动登录呢？」→ 查明是**三个叠加的原因**，全部修复。

| # | 问题 | 结果 |
|---|---|---|
| 1 | 面板起的 Chrome 拿不到桌面，调试端口永远起不来 | ✅ 改由**交互式计划任务**起 |
| 2 | 注入快照后仍「未登录」（票过期） | ✅ 自动回退到**账号密码重登** |
| 3 | **自动重登永远失败**（`no_form`） | ✅ 真 bug，已修 |
| 4 | 浏览器没开时点「刷新节列表」白等一分钟 | ✅ 提前拦下并给可点的下一步 |
| 5 | 节列表读取失败**无痕迹** | ✅ 失败也写操作记录 |

**实机验证**（从「Chrome 已关闭」的干净状态）：

```
点「启动并登录」→ 8.0 秒 → ok=True，当前已是登录状态 → 可以直接刷课
点「刷新节列表」→ 33 秒 → 36 节，done=15，minutes_done=831 ✅
```

**测试**：`pytest` **957 全绿**（新增 11 例）。

## 关键决策及理由

### 决策 1（问题 1）：Chrome 必须由**交互式计划任务**起

**实测对比**（同一组参数、同一个 chrome.exe）：

| 由谁起 | 结果 |
|---|---|
| 面板（后台计划任务，非交互会话） | Chrome 进程出现，但**调试端口永远不响应** |
| 交互式 shell（有桌面） | 端口 2 秒内就绪，100% 成功 |

这是 Windows **会话隔离**：非交互会话（Session 0）里的进程拿不到用户桌面，Chrome 静默失败。

**做法**：注册 `-LogonType Interactive` 计划任务 `xxt-gxks-chrome`，跑
`scripts/start_chrome.ps1`（有桌面）。面板只负责：
① 写 `data/tmp/chrome_start.json`（label + port）
② `schtasks /run` 触发
③ 轮询端口 + 读 `chrome_start.result.json`（含 pid）

**副产品**：Chrome 不再属于面板的子进程 → 面板重启/退出都不会带走它。

### 决策 2（问题 3）：`_find_frame` 只探一次就判 `no_form` —— 真 bug

**现象**：`relogin` 永远返回 `{"result": "no_form"}`，但**手动在那台浏览器里登录完全正常**。

**根因**：登录页 `Loginb.aspx` 是**异步渲染**的：

| 等待时间 | `input` 个数 | `#frmLogin` |
|---|---|---|
| `goto(load)` 后 2 秒 | **0** | 不存在 |
| 5 秒后 | 5 | 存在 |

而 `_find_frame()` 只 `query_selector` **一次**就返回 None → `login()` 直接判 `no_form`。

**修法**：`_find_frame(page, wait_ms=15000)` 改成轮询等待（100ms 一次），
`login()` 新增 `form_wait_ms` 参数。

### 决策 3（问题 2）：登录态三层兜底

cookie 全是 **session 级**（`expires=-1`），关窗即失效 → 昨天的快照今天必然不认。
所以 `_chrome_start_message()` 按顺序：

1. **注入快照**（零密码、最快）→ 成功即返回；
2. 快照票过期是**常态** → 自动改用**账号密码重登**一次；
3. 遇**验证码** → 立刻停，转人工（绝不试错：试错=撞库特征）。

⚠ `restored=0` 且 `logged_in=True` 表示「本来就登着、没走注入」，
文案不能说「注入 0 条 cookie」（已修 + 加测试）。

### 决策 4（问题 4）：刷新节列表前**先探端口**

原来 Chrome 没开时要点到脚本里连 CDP 才报 `ECONNREFUSED`，表现是
「点了刷新 → 任务跑一下 → 消失」，用户完全不知道要开浏览器。

现在接口先 `gchrome.port_alive()`，不通就直接返回
`{"ok": false, "need_browser": true, "error": "..."}`，
前端在按钮下方给一条**持久提示条** + 「启动并登录」按钮（toast 只停 7 秒，长消息会错过）。

## 事故与教训

### ① 端口 8301 被**旧 worktree 的面板**占着（真凶）

`E:\xxt\.venv\Scripts\xxt.exe panel` —— 另一个 worktree 的面板早就占着 8301，
它的代码是**旧版**，所以我的新接口一直返回 500，而本地 `TestClient` 一切正常。

这正是 AGENTS.md 里记过的老坑「**venv 不跨目录复用**」，我这次是反过来踩的：
**两个 worktree 抢同一个端口**。

**排查过程**（值得记住）：
- `Get-CimInstance Win32_Process` 查不到持有者 → 因 uvicorn reload 是
  **父 + spawn 子进程**两层，父进程死了子进程还活着
- `netstat -ano` 看到的 PID 可能是**已死进程的僵尸 socket**
- 真正要杀的是 `multiprocessing.spawn ... spawn_main(parent_pid=...)` 那些**子进程**

**结论**：`netstat` 显示 LISTENING 的 PID 如果 `Get-Process` 查不到，
去 `Get-CimInstance Win32_Process` 里找 `spawn_main` 子进程。

### ② 我的编辑把路由「吞进」了模块级函数

我把 `_chrome_start_message` 插到了 `create_app` 中间，导致后面所有
`@app.post(...)` 路由都变成了它的**内部函数** → `create_app` 返回 None。

**教训**：往大文件中间插函数时，先确认插入点是**模块级**（缩进 0），
插完用 AST 复查函数层级（`ast.parse` + 打印 `col_offset`）。

### ③ `git checkout --` 把本轮未提交改动全冲掉了

我为了修复 ② 用了 `git checkout -- app.py`，结果本轮所有未提交改动
（`need_browser`、交互式任务）全部丢失，只能重做。

**教训**：`git checkout --` 只能用于「本轮没有想保留的改动」；
改动多时应先 `git stash` 或 `git diff > backup.patch`。

### ④ PowerShell 脚本必须存成**带 BOM 的 UTF-8**

`scripts/start_chrome.ps1` 无 BOM 时，Windows PowerShell 5.1 按系统 ANSI（中文 GBK）读，
中文注释变乱码 → **解析失败** → 计划任务返回码 1、无任何输出。

**自查方法**：

```powershell
$errs=$null
[System.Management.Automation.Language.Parser]::ParseFile($p,[ref]$null,[ref]$errs)
```

### ⑤ 后台起面板用**计划任务**才耐久

`Start-Process` 起的进程会随后台作业结束被回收（表现为「端口无监听 + 日志全空」）。
改用计划任务：

```powershell
$a = New-ScheduledTaskAction -Execute "E:\xxt-gxks\.venv\Scripts\python.exe" `
       -Argument "E:\xxt-gxks\scripts\run_panel.py" -WorkingDirectory "E:\xxt-gxks"
Register-ScheduledTask -TaskName "xxt-gxks-panel" -Action $a -Force
Start-ScheduledTask -TaskName "xxt-gxks-panel"
```

⚠ `-LogonType` 是 `New-ScheduledTaskPrincipal` 的参数，不是 `Register-ScheduledTask` 的。

## 改动文件清单

| 文件 | 改什么 |
|---|---|
| `src/xxt/adapters/gxks/login.py` | `_find_frame(wait_ms)` 轮询等表单；`login(form_wait_ms=15000)` |
| `src/xxt/panel_gxks/app.py` | `_start_chrome_interactive()`（新）、`_chrome_start_message()`（新，三层登录兜底）、`browser_start` 重写、`refresh_sections` 加端口预检 |
| `src/xxt/panel_gxks/page.py` | 按钮改「启动并登录」；`showNotice/hideNotice`（持久提示条）；`startBrowser/refreshSections` 用提示条；`.b-notice` 样式 |
| `scripts/gxks_sections.py` | `_record_fail()`：读取失败也写操作记录（原来只 print 到 stderr） |
| `scripts/run_panel.py` | **新文件**：带 `__main__` 保护的启动入口（uvicorn reload 需要） |
| `scripts/start_chrome.ps1` | **新文件**：交互式会话里起 Chrome（必须带 BOM） |
| `tests/test_gxks_panel_browser.py` | 5 个旧测试改适配新实现；新增 `TestChromeStartLogins`（6 例登录策略） |
| `tests/test_gxks_panel.py` | `sections/refresh` 测试 mock 端口探测；新增 `test_sections_refresh_refuses_when_browser_down` |
| `tests/test_gxks_recent24.py` | `test_spans_midnight` 显式传 `day=`，消除「现在几点」依赖 |

## 交接块

### 进行中

无。面板跑在计划任务 `xxt-gxks-panel` 里（venv python，热重载开）；
Chrome 跑在计划任务 `xxt-gxks-chrome` 里（交互式，可被面板一键拉起）。

### 下一步第一个动作

**等用户验收「启动并登录」按钮**（`http://127.0.0.1:8301/` → 账号与任务 → 浏览器与会话）。
实测 8 秒完成，返回「当前已是登录状态 → 可以直接刷课」。

### 阻塞点

**无技术阻塞**。

### 等用户

1. **验收一键启动**：先关掉 `data/profiles/default` 的 Chrome，再点「启动并登录」。
2. **待拍板**：多账号运行日志是否做成**可切换页签**（当前是「当前账号日志 + 其他账号一行提示」）。
3. **注意**：`E:\xxt` 那个 worktree 的面板也会抢 8301。若再次出现「新代码不生效」，
   先 `netstat -ano | findstr :8301` 查 PID，再到 `Get-CimInstance Win32_Process` 里
   找 `spawn_main` 子进程杀掉。
