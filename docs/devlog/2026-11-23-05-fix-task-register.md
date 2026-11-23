# 2026-11-23-05 修：自动注册计划任务静默失败（两层转义 + 权限）

> 承接 [2026-11-23-04 落地](2026-11-23-04-parallel-chrome-landed.md)。
> 用户重启面板后点「启动并登录」，报：
> `触发计划任务失败（returncode=1）。任务名：xxt-gxks-chrome-default`
> **注意：这次报错如实显示了**——正是 04 那次「报错如实化」在起作用，
> 否则又会显示成「任务不存在」，让人继续去重复注册。

---

## 一、两个真 bug（一个比一个隐蔽）

### Bug 1：`-Command` 两层转义导致注册参数被拆散

旧实现：

```python
ps = (f'$a=New-ScheduledTaskAction -Execute powershell.exe '
      f'-Argument "-NoProfile -ExecutionPolicy Bypass -File \\"{script}\\" -Label {label}";'
      ...
      f'Register-ScheduledTask ... | Out-Null; Write-Output "REGISTERED"')
subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps], ...)
```

**实测报错**（我手工复现出来的）：

```
New-ScheduledTaskAction : A parameter cannot be found that matches
parameter name 'ExecutionPolicy'.
```

原因：`-Argument` 后面那个带引号的值，经过 **Python 字符串 → subprocess 参数
→ powershell -Command 解析** 三层后引号失效，`-Argument` 只收到 `-NoProfile`，
后面的 `-ExecutionPolicy Bypass ...` 被当成 `New-ScheduledTaskAction` 自己的参数。

### Bug 2：`| Out-Null` + 无条件 `Write-Output` 导致**静默失败被误判成功**

```powershell
Register-ScheduledTask ... | Out-Null; Write-Output "REGISTERED"
```

`Register-ScheduledTask` 报错后，**分号后面的 `Write-Output` 照样执行**。
面板侧判断 `"REGISTERED" in stdout` → **判成成功**。
于是：任务根本没建，但面板以为建好了；直到点「启动并登录」才报「任务不存在」。

**这就是「以前能用现在不行」的完整链条**：不是环境坏了，是注册一直没成功，
而旧代码把它当成功了。

---

## 二、修法

### 2.1 注册逻辑移进独立 `.ps1`（零转义）

新增 `scripts/register_chrome_task.ps1`：

- 参数 `-Label`，Python 只用 `-File <脚本> -Label <label>` 调用 → **完全避开引号转义**
- `-Argument` 的值用**反引号转义的双引号**（PowerShell 原生写法，不经过 subprocess 解析）
- 输出约定：成功 `OK: <taskName>` + `exit 0`；失败 `FAIL: <原因>` + `exit 1`
- **没有**「无条件打印成功」的语句

面板侧判断改成 **`returncode == 0` 且 stdout 含 `OK:`**，不再靠字符串碰运气。

### 2.2 权限真相：面板注册不了，必须提权跑一次

实测：`Register-ScheduledTask` 创建「以 Administrator 运行」的任务**需要提权**。
面板是普通权限进程 → `FAIL: 拒绝访问`。
（我这个诊断 shell 是 `High Mandatory Level`，所以手工能成；Python 子进程不行。）

所以自动注册**只在面板本身以管理员启动时**才成功。为此：

- 新增 `scripts/register_all_chrome_tasks.ps1`：**一次注册全部账号**
  （从 `data/accounts.local.json` 读 label，逐个调 `register_chrome_task.ps1`，汇总 `ok/failed`）
- 面板注册失败且检测到「拒绝访问」时，报错**明确写**：
  「需要管理员权限」+ 给出批量脚本命令，而不是让你反复重试

---

## 三、实测结果

**批量注册（在提权 PowerShell 里跑）**：

```
OK: xxt-gxks-chrome-default
OK: xxt-gxks-chrome-test
OK: xxt-gxks-chrome-test03
OK: xxt-gxks-chrome-test04
OK: xxt-gxks-chrome-test05
OK: xxt-gxks-chrome-text05
OK: xxt-gxks-chrome-tst001

DONE: ok=7 failed=0 total=7
```

`schtasks /query` 确认 7 个新任务 + 1 个旧全局任务全部 `Ready`。

**任务动作正确**：

```
Task To Run: powershell.exe -NoProfile -ExecutionPolicy Bypass
             -File "E:\xxt-gxks\scripts\start_chrome.ps1" -Label default
```

**实跑验证**：`schtasks /run /tn xxt-gxks-chrome-default` → rc=0，**9222 端口起来**。

---

## 四、验证

| 项 | 结果 |
|---|---|
| `tests/test_gxks_parallel_chrome.py` | **28 条**（新增权限场景 + 批量脚本存在性 + BOM） |
| 全量 `pytest` | **1365 passed** |
| `push_check` | PASS |
| 两个新 ps1 的 BOM | ✅ `EF BB BF` |

新增的回归守护直接锁住这两个 bug：
- `test_uses_file_not_inline_command`：不许再用 `"-Command"`
- `test_success_needs_exitcode_and_marker`：必须 `exitcode==0` **且** `OK:` 才算成功；
  代码里不许出现 `"REGISTERED"`
- `test_script_reports_failure_explicitly`：脚本失败必须 `FAIL:` + `exit 1`
- `test_permission_denied_points_to_bulk_script`：拒绝访问时必须提示提权 + 指向批量脚本

---

## 五、你要做的

**7 个任务我已经替你注册好了**（用提权 shell 跑的批量脚本）。所以现在：

1. **重启面板**（让新代码生效）：`cd E:\xxt-gxks` → `.venv\Scripts\xxt.exe gxks-panel`
2. 点两个账号「启动并登录」→ 应该**同时启动**、不再报错

**以后新增账号时**：如果面板不是管理员启动，自动注册会报「需要管理员权限」，
按提示跑一次批量脚本即可（一条命令注册全部）。

---

## 六、改动的文件

- `scripts/register_chrome_task.ps1`（新，单账号注册，零转义）
- `scripts/register_all_chrome_tasks.ps1`（新，批量注册全部账号）
- `src/xxt/panel_gxks/app.py`（改调 .ps1；成功判据改 exitcode+OK:；权限分支提示）
- `tests/test_gxks_parallel_chrome.py`（+7 条守护）

## 交接块

- **进行中**：无（已修并实测；7 个任务已注册）
- **下一步第一个动作**：用户重启面板 → 点两个账号「启动并登录」→ 确认并行不报错
- **阻塞点**：无
