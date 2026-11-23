# 2026-11-23-06 一键停止脚本 + 启动时权限/任务自检

> 用户 2026-11-23 22:20 两个实际痛点：
> ① 「这个命令太长了，执行哪里记得住」——贴出他执行失败的现场：
>    `$p = (Get-CimInstance ...).ProcessId` 之后 `taskkill /PID $p /T /F`
>    报 `错误: 无效语法。需要 '/PID' 的值。`
> ② 「我怎么知道是以管理员还是普通权限启动？」
> 另问：「如果后面有添加新账号呢？那会自动注册成功吗？」

---

## 一、为什么那条命令会失败（先解释清楚）

用户贴的现场：

```powershell
PS E:\xxt-gxks> $p = (Get-CimInstance Win32_Process -Filter "Name='xxt.exe'" | Where-Object CommandLine -like '*gxks-panel*').ProcessId
PS E:\xxt-gxks> taskkill /PID $p /T /F
错误: 无效语法。需要 '/PID' 的值。
```

**根因**：那一刻面板已经停了（我查证：`8301 未监听`、无 `xxt.exe` 进程），
所以 `$p` 是**空值**，命令变成 `taskkill /PID  /T /F` → 语法错。

这条命令还有两个毛病：
- **太长记不住**（用户原话）；
- **只按进程名找**：`xxt.exe` 是 venv 重定向器，真正的 worker 是 `python.exe`，
  名字会变；而且按名字找会漏掉“正在监听 8301 的那个进程”。

---

## 二、解法 1：一键停止脚本

新增两个文件：

| 文件 | 用法 |
|---|---|
| `scripts/stop_panel.ps1` | `.\scripts\stop_panel.ps1` |
| `stop_panel.bat` | **双击**，或 `stop_panel.bat` |

### 设计要点

1. **优先按「谁监听 8301」找**（最准）：
   `Get-NetTCPConnection -LocalPort 8301 -State Listen` → `OwningProcess`
   —— 不管进程叫 `xxt.exe` / `python.exe` / 重定向器，都能找到真正那个。
2. **兜底按命令行找**：`Win32_Process` 里 `CommandLine like '*gxks-panel*'`。
3. **`taskkill /T /F` 连带整棵进程树**：
   `powershell → xxt.exe → python → reloader → worker` 一次停干净，
   不给 reloader 复活的机会。
4. **没在跑时不报错**：打印「没有找到正在运行的面板」并 `exit 0`
   —— 正是用户上次踩的坑（空变量导致 taskkill 语法错）。
5. **停完复查端口已释放**：`Start-Sleep 800ms` 后再查 8301，
   没释放就 `exit 1` 并提示再跑一次 —— 不假装成功。

---

## 三、解法 2：启动时打印权限 + 任务自检

用户不知道自己是管理员还是普通权限。与其让他查 `whoami /groups`，
不如**面板启动时直接告诉他**：

```
[OK] 运行权限：**管理员（已提权）** → 新增账号时能自动注册计划任务
```
或
```
[!] 运行权限：普通权限（未提权）
      影响：新增账号时**无法自动注册**计划任务（会提示「拒绝访问」）。
      需要时请用管理员 PowerShell 跑一次（一条命令注册全部账号）：
        powershell -NoProfile -ExecutionPolicy Bypass -File scripts\register_all_chrome_tasks.ps1
```

再报计划任务状态：

```
[OK] 计划任务：7 个账号都已注册（xxt-gxks-chrome-x 这类名字，每账号一个）
```
或
```
[!] 计划任务：2 个账号**缺少**计划任务 → test04、test05
      未提权，自动补注册会失败。请在管理员窗口跑：...
```

### 判据为什么用 `IsUserAnAdmin()`

不是「属于 Administrators 组」，而是**是否真的提权**（High Mandatory Level）。
普通管理员组用户双击启动时是 Medium 完整性级别，
`Register-ScheduledTask` 建「以 Administrator 运行」的任务**仍会拒绝访问**。
（这条是 2026-11-23 实测出来的：我诊断用的 shell 是 High，Python 子进程不是。）

---

## 四、回答用户的三个问题

### Q1：以后添加新账号会自动注册吗？

**会，取决于面板权限**：

| 面板启动方式 | 新增账号时 |
|---|---|
| 管理员（已提权） | ✅ 自动注册成功 |
| 普通权限 | ❌ 提示「拒绝访问」+ 给出批量脚本命令 |

**启动时的自检会提前告诉你**属于哪种，不用等点按钮才发现。

### Q2：现在需要重启面板吗？

**需要**。用户截图显示两次启动：第一次 `PAGE len=158378`（旧），
第二次 `PAGE len=158785`（新，热重载已生效）。
但**要看到本轮新加的「权限/任务自检」输出，必须重启**。

### Q3：为什么 Ctrl+C 一堆红字、要按多次？

**不是程序出错**，是 uvicorn `reload=True` 在 Windows 上的关停噪声：

| 红字 | 真相 |
|---|---|
| `KeyboardInterrupt` | Ctrl+C 本身（uvicorn 把 SIGINT 转成异常作为关闭入口） |
| `CancelledError` | 关停时取消在途请求（SSE 日志推送）的正常表现 |
| `Event loop is closed` | Windows + reload 的关停竞态 |

**要按多次的原因**：`reload=True` 是多层进程树，按一次只打断最近一层；
reloader 父进程只要活着就**再 spawn 一个 worker** → 端口还在 → 看着“没停”。

**现在用 `stop_panel.bat` 双击即可**，一次停干净、不刷红字。

---

## 五、验证

| 项 | 结果 |
|---|---|
| 新测试 `test_gxks_stop_and_selfcheck.py` | **14 条** |
| 全量 `pytest` | **1379 passed**（1365 → 1379） |
| `push_check` | PASS |
| `stop_panel.ps1` BOM | ✅ `EF BB BF` |
| `stop_panel.bat` | ASCII 纯净（cmd 里中文会变问号） |
| 实测停止脚本 | 面板未跑时正确打印「没有找到…」并 `exit 0` |
| 实测自检输出 | `elevated: True` + 7 个账号都已注册 |

---

## 六、改动的文件

- `scripts/stop_panel.ps1`（新）
- `stop_panel.bat`（新，双击入口）
- `src/xxt/panel_gxks/app.py`（`_is_elevated` / `_print_permission_and_tasks` / serve 调用）
- `tests/test_gxks_stop_and_selfcheck.py`（新）

## 交接块

- **进行中**：无
- **下一步第一个动作**：用户双击 `stop_panel.bat` 停面板 → 重新启动 →
  看启动输出里的「运行权限」「计划任务」两行 → 点两账号「启动并登录」验证并行
- **阻塞点**：无
