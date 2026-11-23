# 2026-11-23-02 方案：多账号并行启动浏览器（B 方案）+ 报错如实化

> 用户 2026-11-23 21:27：
> 「两个账号同时点击，要多账号登陆呀，肯定会选择多个账号同时点击登陆的，
> 先前也没有出现过这个问题，方案 B 是不是更好些。先确定方案，再动手改」

---

## 一、查证结论（先摆事实，再谈方案）

| 事实 | 证据 |
|---|---|
| 计划任务 `\xxt-gxks-chrome` **存在且正常** | `State: Ready`、`Principal: Administrator / Interactive`、动作 = `start_chrome.ps1` |
| 任务运行中再 `/run` **不会失败** | 实测：第二次 `/run` 返回 `rc=0`，只是多一行 `INFO: ... is currently running.` |
| `subprocess.run(['schtasks','/run','/tn','xxt-gxks-chrome'])` **本身没问题** | Python 直接复现：`rc=0`、stdout 有 `SUCCESS` |
| 面板是**用户手动**在 PowerShell 起的 | `powershell.exe(12512) → xxt.exe(18948) → python(10972) → python(25312)` |
| 当前身份 = administrator，**有管理员权限** | `IsInRole(Administrator) = True` |
| 7 个账号端口互不相同 | 9222 / 9233 / 9288 / 9225 / 9283 / 9267 / 9250 |
| profile 按 label 自动分配 | `data/profiles/<label>` |

### 那次的失败到底是什么

`chrome_start.json` 写入于 **21:08:31**（用户点击时），`result.json` 到 **21:10:51**（我手动触发）才出现。
→ **中间 2 分 20 秒里，那次 `/run` 失败了**。

**已排除**：任务不存在（存在）、权限不足（有管理员）、任务在跑（实测 rc=0）。
**最可能**：那一刻 `schtasks` 进程本身异常（杀软拦截 / 系统繁忙 / 瞬时句柄问题）——
但**代码把它一律解释成「任务不存在」**，这是**真 bug**，也是这次误导的根源。

### 但更根本的问题是结构性的

即使用户只点一次，现有设计也有**串行瓶颈**：

```
_CHROME_START_LOCK（进程内互斥）
  └─ 写全局唯一的 chrome_start.json（后写覆盖前写）
       └─ schtasks /run（任务动作注册时写死，运行期传不进参数）
            └─ start_chrome.ps1 读同一个 json
```

**7 个账号要多开时，只能一个接一个排队（每个 10~40 秒），且任何一次失败都会污染下一个。**

---

## 二、方案 B：每账号一个计划任务 + 独立 spec 文件

### 核心改动

| 项 | 现状 | 方案 B |
|---|---|---|
| 计划任务 | 全局 1 个 `xxt-gxks-chrome` | **按需为每个账号注册** `xxt-gxks-chrome-<label>` |
| spec 文件 | `data/tmp/chrome_start.json`（全局唯一） | `data/tmp/chrome_start_<label>.json` |
| result 文件 | `chrome_start.result.json` | `chrome_start_<label>.result.json` |
| 并发 | `_CHROME_START_LOCK` **串行** | **删除该锁**，多账号真并行 |
| 脚本参数 | 动作写死 `-File start_chrome.ps1` | 动作带 `-Label <label>`：`-File start_chrome.ps1 -Label <label>` |

### 为什么这样能根治

1. **`-Label` 在注册时就写进任务动作**，运行期不需要传参 → 每个账号的任务各读各的 spec，**互不覆盖**
2. 不同任务天然可并行（Windows 计划任务本身支持多实例）
3. 某个账号失败**不影响**其他账号
4. 注册是**幂等**的：第一次用到某账号才注册，之后一直有效

### 自动注册（关键：不再让用户手工跑命令）

新增 `_ensure_chrome_task(label)`：

```
检查 schtasks /query /tn xxt-gxks-chrome-<label>
  存在 → 直接 /run
  不存在 → 自动 Register-ScheduledTask（用当前用户、Interactive、Limited）
            注册失败 → 才返回可照做的命令
```

注册用 `subprocess.run(['powershell','-NoProfile','-Command', '...'])` 调 ScheduledTasks 模块，
与用户手工跑的那条命令**等价**，但由面板自动完成。

### 用户完全无感

- 你照旧点「启动并登录」，两个（或七个）账号同时点 → 各自并行启动
- 第一次用某账号会多花 1~2 秒注册任务，之后秒起

---

## 三、附带修复（必须一起做，否则还会被误导）

### 3.1 报错如实化

现在：

```python
if done.returncode != 0:
    return {"ok": False, "error": "交互式任务 xxt-gxks-chrome 不存在或起不来。\n" + 注册命令}
```

改成按**真实原因**分支：

| 情况 | 判断 | 提示 |
|---|---|---|
| 任务不存在 | stderr 含 `cannot find` | 给注册命令（保留原兜底） |
| 任务在跑 | stdout 含 `currently running` | 「上一个启动还在进行，稍等」**（不报错）** |
| 权限不足 | stderr 含 `Access is denied` / `拒绝访问` | 「请以管理员身份运行面板」 |
| 其他 | 兜底 | **原样带出 stderr/stdout 原文** + 排查命令 |

**关键**：把 `schtasks` 的原始输出一并返回，而不是丢弃——这次的误导正是因为它被丢掉了。

### 3.2 页面指纹 + 任务状态自检

`/api/state` 的 `browser` 块增加：

```
"chrome_task": {"name": "xxt-gxks-chrome-default", "registered": true, "state": "Ready"}
```

面板「浏览器与会话」区显示一行小字：
`交互式任务：已注册（Ready）` 或 `未注册（首次启动时自动注册）`。

→ 下次真出问题时，**一眼看到任务状态**，不用猜。

---

## 四、改动清单（确认后执行）

| 文件 | 改动 |
|---|---|
| `scripts/start_chrome.ps1` | 加 `param([string]$Label)`；spec/result 文件名带 label；无 label 时回落旧行为（兼容手工调用） |
| `src/xxt/panel_gxks/app.py` | ① `_chrome_task_name(label)`；② `_ensure_chrome_task(label)` 自动注册；③ `_start_chrome_interactive` 去掉 `_CHROME_START_LOCK`、改用按 label 的文件；④ 报错按真实原因分支；⑤ `/api/state` 带 `chrome_task` |
| `src/xxt/panel_gxks/page.py` | 「浏览器与会话」区显示任务注册状态一行 |
| 测试 | 新增：任务名生成、报错分支、按 label 文件路径、无锁并发不互相覆盖 |

**不动的**：`run_gxks.py`、节缓存、账本、G-8/G-9 那套。**风险隔离**。

---

## 五、风险与回滚

| 风险 | 应对 |
|---|---|
| 旧任务 `xxt-gxks-chrome` 残留 | **保留不删**（其它 worktree / 手工脚本可能还在用）；新代码只用带 label 的 |
| 自动注册被策略拒绝 | 返回可照做的手工命令（原兜底逻辑保留） |
| 多账号同时注册任务 | 注册走 `_TASK_REGISTER_LOCK`（仅注册串行，**启动并行**） |
| 回滚 | 单 commit，`git revert` 即可；旧全局任务仍在 |

---

## 六、需要你拍板

1. **方案 B 是否认可**（每账号一个任务 + 独立 spec + 真并行）？
2. **自动注册**是否可以（面板自动建计划任务，不再让你手工跑命令）？
3. 任务命名 `xxt-gxks-chrome-<label>` 可以吗？（例：`xxt-gxks-chrome-default`）
4. 旧的全局任务 `xxt-gxks-chrome`：**保留**（我建议）还是删掉？

## 交接块

- **进行中**：方案待确认，**未动代码**
- **下一步第一个动作**：你回上面 4 个问题 → 我按「查→定→审→动码→验证」落地
- **阻塞点**：无（不依赖平台样本，纯本地改动）
