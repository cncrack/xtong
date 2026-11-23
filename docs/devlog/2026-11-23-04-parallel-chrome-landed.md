# 2026-11-23-04 落地：多账号并行启动浏览器（方案 B）+ label 自动生成

> 承接 [2026-11-23-02 方案](2026-11-23-02-plan-parallel-chrome.md) 与
> [2026-11-23-03 定稿](2026-11-23-03-plan-b-final.md)。
> 用户 11-23 拍板：方案 B、自动注册、任务名用 label、保留旧全局任务、
> 注册时机选 C、**不加拼音库用 `user01` 递增**。

---

## 一、这次到底修了什么（先说清根因）

### 用户遇到的现象

点「启动并登录」→ 弹红框「交互式任务 xxt-gxks-chrome 不存在或起不来」，
并让你手工执行一大串 `Register-ScheduledTask`。

### 查证排除了三个猜测

| 猜测 | 实测 |
|---|---|
| 任务不存在 | ❌ 存在，`State: Ready`、`Principal: Administrator / Interactive` |
| 权限不足 | ❌ 当前是 administrator，`IsInRole=True` |
| 任务在跑时不能重复 `/run` | ❌ 实测第二次 `/run` 返回 **rc=0**，只多一行 `INFO: currently running.` |
| Python 调 `schtasks` 有问题 | ❌ 直接复现 `rc=0`、stdout 含 `SUCCESS` |

**证据**：`chrome_start.json` 写于 21:08:31（点击时），`result.json` 到 21:10:51
（我手动触发）才出现 → 中间那 2 分 20 秒里 `/run` 确实失败了。

### 真 bug：报错把一切都归为「任务不存在」

```python
if done.returncode != 0:
    return {"ok": False, "error": "交互式任务 ... 不存在或起不来。\n" + 注册命令}
```

`schtasks` 的 stdout/stderr **被丢弃**，于是任何失败（瞬时异常、权限、任务占用）
都显示成「不存在」，把用户引向**反复注册**——这就是「以前能用现在不行」的错觉来源。

### 更根本：结构是全局单点，账号一多必然排队

> 用户原话：「7 个账号要多开时只能排队…后面可能会更多账号，
> 这个我们在先前设计与开发的时候已经说过了，账号数量比较多」

旧设计：进程内串行锁 + **唯一** `chrome_start.json` + **唯一**任务
→ 每账号排队 10~40 秒，任何一次失败污染下一个。

---

## 二、改动

### 2.1 每账号一个计划任务（方案 B）

| 项 | 旧 | 新 |
|---|---|---|
| 任务名 | `xxt-gxks-chrome`（全局） | **`xxt-gxks-chrome-<label>`** |
| 任务动作 | `-File start_chrome.ps1` | `-File start_chrome.ps1 **-Label <label>**` |
| spec | `chrome_start.json` | `chrome_start_<label>.json` |
| result | `chrome_start.result.json` | `chrome_start_<label>.result.json` |
| 并发 | `_CHROME_START_LOCK` 串行 | **删锁，真并行** |

**关键**：`-Label` 在**注册时**写进任务动作（`schtasks` 运行期传不进参数），
所以每个任务各读各的 spec，**互不覆盖**。账号再多也不会撞。

### 2.2 自动注册（用户选方案 C）

- **启动时**：后台线程遍历账号表，缺的任务补上（不阻塞面板启动）
- **使用时**：`/run` 前若任务不存在则先注册
- 注册走 `_TASK_REGISTER_LOCK`（**只串行注册，启动并行**）
- 注册失败 → 回落到给用户可复制的手工命令

### 2.3 报错如实化（四分支）

| 情况 | 判断 | 行为 |
|---|---|---|
| 任务正在跑 | stdout 含 `currently running` | **不报错**，继续等端口 |
| 任务不存在 | `cannot find` / `找不到` | 给手工注册命令 |
| 无权限 | `access is denied` / `拒绝访问` | 提示以管理员启动 |
| 其他 | 兜底 | 带 `returncode` + **原始输出** + 排查命令 |

所有失败分支都回传 `output` 字段（原始 stdout+stderr）——这次误导的根源就是把它丢了。

### 2.4 状态可见

`/api/state` 新增 `chrome_task: {name, registered, state}`，
面板「浏览器与会话」显示 `任务 已注册` / `任务 未注册`。

### 2.5 label 自动生成（`user01` 递增）

```python
suggest_label(["default", "test"])   # -> "user01"
suggest_label(["user01", "user02"])  # -> "user03"
suggest_label([...user01~user99])    # -> "user100"
```

- 只在**新增账号**且代号留空时生成；编辑已有账号留空仍报错（改代号=重命名，必须显式）
- **不加 `pypinyin`**（用户决定）：拼音要额外依赖，多音字/重名仍要兜底，
  而 `label` 只是内部标识，中文名放 `display` 字段（面板显示的就是它）

---

## 三、验证

| 项 | 结果 |
|---|---|
| 新测试 `test_gxks_parallel_chrome.py` | **21 条**（任务命名 / 删锁并行 / 按 label 分文件 / 自动注册 / 四分支报错 / 状态可见 / suggest_label） |
| 全量 `pytest` | **1358 passed** |
| `push_check` | **PASS** |
| `start_chrome.ps1` BOM | ✅ `EF BB BF`（edit 工具会丢，已补回并用测试守） |
| PowerShell 语法 | ✅ Tokenize 通过 |
| 页面指纹 | `PAGE` 158378 → **158785** |

### 更新的两条旧测试（行为变更，不是放宽）

| 旧测试 | 为什么改 |
|---|---|
| `test_browser_start_is_serialized` | 串行是旧契约，现在要求**并行**；改为断言「无锁 + 按 label 分文件」，并写明历史原因 |
| `test_spec_has_url_field` | 写 spec 的函数从 `_locked` 改名为 `_impl` |
| `test_save_rejects_bad_label` | `""` 现在合法（自动生成），去掉该用例；非法值照旧拒 |

---

## 四、你要做的（一次性）

**重启面板**（让新代码生效）：

```powershell
# 在自己的 PowerShell 窗口
cd E:\xxt-gxks
.venv\Scripts\xxt.exe gxks-panel
```

启动后它会**自动**为每个账号注册 `xxt-gxks-chrome-<label>` 任务（后台线程，不拖慢启动）。

然后在面板点两个账号「启动并登录」→ **两个会同时开始**（不再排队）。

**验证任务已注册**：

```powershell
schtasks /query /fo table | findstr xxt-gxks-chrome
```

应该看到 `xxt-gxks-chrome-default`、`xxt-gxks-chrome-test`…（每个账号一个）。
旧的 `xxt-gxks-chrome` 仍在（按你意思保留）。

---

## 五、改动的文件

- `scripts/start_chrome.ps1`（`param([string]$Label)` + 文件名按 label 分开 + 保留旧回落）
- `src/xxt/panel_gxks/app.py`（任务名/注册/状态/删锁/四分支报错/预注册）
- `src/xxt/panel_gxks/page.py`（任务状态标签）
- `src/xxt/adapters/gxks/accounts.py`（`suggest_label` + 留空自动生成）
- 测试：新增 1 个、更新 3 个

## 交接块

- **进行中**：无（代码完成并验证；**需用户重启面板生效**）
- **下一步第一个动作**：用户重启面板 → 点两个账号「启动并登录」→ 确认并行、不报错
- **阻塞点**：无
