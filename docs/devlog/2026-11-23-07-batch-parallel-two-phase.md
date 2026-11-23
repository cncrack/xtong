# 2026-11-23-07 批量启动真并行（两阶段：先全部触发，再统一等端口）

> 用户 2026-11-23 23:49：「我选择了 2 个账号，为什么只启动一个账号」
> 追问后拍板：「按 A 执行」（真并行 + 可配同时启动上限）。

---

## 一、为什么方案 B 之后仍然串行（我上次说错了）

方案 B 改的是**文件隔离**（每账号一个 spec，互不覆盖）——那部分生效了。

**但批量循环本身还在逐个等**：

```python
for label in labels:
    got = _browser_start_one(...)   # 内部 for _ in range(80): sleep(0.5) → 最多等 40 秒
```

我上次说「删掉锁就并行」**是错的**：锁没了，但**每个账号内部仍会阻塞等到端口就绪**才返回，
所以第二个账号必须等第一个起完。

### 现场证据（用户那次的实际数据）

操作记录：

```
23:48:13  default  browser-start  ok
23:49:57  test     browser-start  ok      ← 间隔 1 分 44 秒
```

两个账号**都启动了**，只是间隔太久，用户看到「只有一个」。
spec 文件也印证：先只有 `chrome_start_default.json`，之后才有 `chrome_start_test.json`。

---

## 二、修法：拆成两阶段

| 阶段 | 函数 | 行为 |
|---|---|---|
| ① 触发 | `_trigger_chrome(label, port)` | 只写 spec + `schtasks /run`，**立即返回，不等端口**（几十毫秒） |
| ② 等待 | `_await_chrome_ready(label, port, result)` | 轮询端口就绪（最多 40 秒），超时读 result.json 拿错误 |

### 批量流程（改后）

```python
# 先对所有账号**全部触发**（每个几十毫秒）
for label in labels:
    pretriggered[label] = _trigger_chrome(label, port)

# 再进循环：端口大多已在起，等待被摊薄 → 整批耗时 ≈ 最慢的一个
for label in labels:
    got = _browser_start_one(..., pretriggered=pretriggered)
    # 内部看到 pretriggered 命中就只等端口，**不重复 /run**
```

### 边界（刻意保守）

- **只对 `start-browser` 预触发**：`start` / `start-and-study` 要按并发闸、日额度
  逐个判定，无条件预触发会让「本该被拦下」的账号也把 Chrome 起了。
- **已活着的账号不预触发**：`alive_map` 里为真的直接跳过（省钱省时间）。
- **预触发过的账号不重复 `/run`**：`_browser_start_one` 检查 `pretriggered`，
  命中则只等端口 —— 重复触发有起两个实例的风险。
- **单独点一个账号**：行为不变（触发 → 立即等），不会因为改动而变慢。

---

## 三、顺手修的两个 bug

### Bug 1：`stop_panel.ps1` 用了只读自动变量 `$pid`

```
+ foreach ($pid in $targets) {
    WriteError: (PID:String) [], SessionStateUnauthorizedAccessException
    + FullyQualifiedErrorId : VariableNotWritable
```

`$PID` / `$pid` 是 PowerShell 的**只读自动变量**（当前进程 ID），不能赋值。
→ 改名 `$targetPid`。这是用户第一次跑 `stop_panel.bat` 报错的真因（当时只看到
「8301 仍在监听」，没看到这条 stderr）。

### Bug 2：测试用全局 mock 导致取错调用

`test_relogin_*` 用 `mock.patch.object(panel_app.subprocess, "run")` **全局**替换，
而面板现在还会调 `schtasks /query`（任务自检）。于是 `run.call_args`（最后一次调用）
可能是 schtasks 而非 relogin 命令 → `ValueError: '--port' is not in list`。

→ 改成**按内容筛选**：`next(c.args[0] for c in run.call_args_list if "--relogin" in c.args[0])`。

---

## 四、验证

| 项 | 结果 |
|---|---|
| `test_gxks_parallel_chrome.py` 新增 `TestTwoPhaseParallel` | **6 条**（触发不等 / 等待独立 / 单个仍触发后等 / 批量先全触发 / 只对 start-browser / 不重复触发） |
| 全量 `pytest` | **1385 passed** |
| 面板卡死事件 | 已用 `stop_panel.bat` 停掉并释放 8301（原因是当时面板跑的是**旧代码**，批量串行被我的测试请求压住） |

### 面板卡死的说明（不要误判）

我用 HTTP 直接压 `/api/accounts/batch` 测试时，面板卡住 300 秒无响应。
**原因**：那一刻面板（pid 15080，23:47 启动）跑的是**旧代码**——我的两阶段改动在
23:50 之后才写完，热重载虽开着但被阻塞中的请求卡住。
用 `stop_panel.bat` 停掉后 8301 正常释放。**不是新代码的死锁。**

---

## 五、你要做的（验证真并行）

1. **启动面板**（会加载新代码）：
   ```powershell
   cd E:\xxt-gxks
   .venv\Scripts\xxt.exe gxks-panel
   ```
2. 浏览器 `Ctrl+F5`
3. **勾选 2 个账号 → 「批量启动浏览器」**
4. **预期：两个账号几乎同时开始起 Chrome**（都在 10~40 秒内就绪），
   而不是先等完一个再等另一个。整批耗时≈最慢的一个。

如果还是要等 1 分多，说明**面板没重启到新代码**——看启动输出的 `PAGE len=`，
应该是 **158785 或更大**。

---

## 六、改动的文件

- `src/xxt/panel_gxks/app.py`（拆 `_trigger_chrome` / `_await_chrome_ready`、批量两阶段预触发、`_browser_start_one` 支持 pretriggered）
- `scripts/stop_panel.ps1`（`$pid` → `$targetPid`，修只读变量 bug）
- `tests/test_gxks_parallel_chrome.py`（+6 条并行守护）
- `tests/test_gxks_panel_browser.py`（mock 改为按内容筛选）
- `tests/test_gxks_start_chrome_script.py`（写 spec 的函数改名）

## 交接块

- **进行中**：无（代码完成并测试；**需用户重启面板验证**）
- **下一步第一个动作**：用户启动面板 → 勾 2 个账号「批量启动浏览器」→ 观察是否同时开始
- **阻塞点**：无
