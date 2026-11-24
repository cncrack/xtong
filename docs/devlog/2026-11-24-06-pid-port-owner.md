# 2026-11-24-06 修复：Chrome pid 取端口持有者（方案 A+B）

> 用户 2026-11-24 晚上：「现在第一个账号林鑫阳怎么不能登陆」。
> 排查发现根因后，用户说「先改」——这是启动流程的一个隐蔽真 bug。

---

## 一、现象

操作记录（G-24 加的批量留痕又立功）：

```
22:37:22 | (批量) | batch | error | 批量「start-browser」提交 2 个：成功 1（test）、
                                     失败 1。原因：default=等了 40 秒，端口 922…
```

- `22:35:39` 写 spec（label=default, port=9222）
- `22:37:05` 一批 chrome 进程起来
- `22:37:09` 脚本看到端口通 → 写 result `ok:true, pid:27024`
- `22:37:22` 面板报超时失败
- 之后 27024 **已不存在**，浏览器窗口短暂出现后退出

用户看到：第一个账号（林鑫阳）打开的是 `Loginb.aspx`（登录页）或直接失败。

---

## 二、根因：pid 用的是 Chrome 启动器，不是端口持有者

### 证据链

| 时间 | 事件 |
|---|---|
| 22:37:09 | 脚本写 `ok:true, pid=27024` |
| 现在 | 27024 不存在、带 `profiles\default` 的 chrome 进程数 = 0 |

### 为什么 pid 会死

`chrome.exe` 是**启动器进程**。启动流程：

```
chrome.exe（Start-Process -PassThru 拿到它）
  └─ 检查没有已有实例
     └─ 建 profile 锁、把工作交接给真正的新进程
        └─ 自己退出（$proc.Id 变成死 pid）
```

脚本 `start_chrome.ps1` 原写法：

```powershell
$proc = Start-Process ... -PassThru
# 等端口通了…
Write-Result @{ ok = $true; pid = $proc.Id }   # ← 启动器的 pid，会死
```

面板拿到死 pid，于是：
- 「是否在运行」判错（重启/超时）
- 「停止浏览器」杀不掉真进程
- 明明窗口出现过，面板却报「端口 9222 未响应」

**这也解释了上午 test03 的「报 ok 但窗口没了」**：当时我归因为"用户关了窗口"，其实更可能是同样的 pid 失效。

---

## 三、修复（方案 A + B 双保险）

### 方案 A：脚本侧写端口真实持有者

```powershell
# 等端口通之后，再查一次谁在监听
$procId = 0
for ($k = 0; $k -lt 10; $k++) {
    $conn = Get-NetTCPConnection -LocalPort $port -State Listen -EA SilentlyContinue |
            Select-Object -First 1
    if ($conn -and $conn.OwningProcess -gt 0) {
        $procId = [int]$conn.OwningProcess
        break
    }
    Start-Sleep -Milliseconds 300
}
if ($procId -le 0) { $procId = $proc.Id; $ownerKnown = $false }  # 兜底
Write-Result @{ ok = $true; pid = $procId; launcher_pid = $proc.Id; owner_known = $ownerKnown }
```

### 方案 B：面板侧再兜一层

`_await_chrome_ready()` 里，端口就绪后**不读 result 文件的 pid**，而是调用
`gchrome.port_owner_pid(port)`（用 `netstat -ano` 查 LISTENING）——

```python
if gchrome.port_alive(port):
    pid = gchrome.port_owner_pid(port)   # 不信 result 文件，防脚本侧竞态
    return {"ok": True, "pid": pid}
```

这样即使脚本侧退出时机有竞态（写了死 pid），面板也能拿到活的。

---

## 四、顺带发现/处理的

### BOM 丢失

改 `start_chrome.ps1` 时丢了 BOM（头部从 `EF BB BF` 变成 `23 20`）。**这是 .ps1 大忌**：
PowerShell 5.1 按 GBK 读中文注释会乱码、解析失败。已用
`New-Object System.Text.UTF8Encoding($true)` 补回。**这是第一次改这个文件，以后编辑完要查 BOM。**

### 面板侧之前的信任

`_await_chrome_ready` 原来读 `result` 文件的 pid，现在改成查端口真实持有者。改动小、职责清晰。

---

## 五、验证

| 项 | 结果 |
|---|---|
| 新增测试 | `TestPidIsPortOwner` **3 条**（脚本查 OwningProcess / 面板兜底 / 函数存在） |
| 全量 `pytest` | **1420 passed** |
| 门禁 | **PASS** |

### 3 条新测试守什么

1. `test_script_writes_port_owner_not_launcher` —— 脚本最终写入前有 `OwningProcess`
2. `test_app_uses_port_owner_as_backstop` —— 面板用 `port_owner_pid` 兜底，不信 result 文件
3. `test_port_owner_pid_function_exists` —— `chrome.py` 的 `port_owner_pid` 用 netstat

---

## 六、你要做的

林鑫阳现在 9222 已经干净（无进程、无锁）。**直接在面板点「启动」** 即可。

起来了再跑 G-7 练习。如果还有问题，把操作记录（`gx_ops`）和启动终端输出贴给我。

---

## 改动的文件

- `scripts/start_chrome.ps1`（写端口持有者 pid + 补 BOM）
- `src/xxt/adapters/gxks/chrome.py`（新增 `port_owner_pid`）
- `src/xxt/panel_gxks/app.py`（`_await_chrome_ready` 用 `port_owner_pid` 兜底）
- `tests/test_gxks_parallel_chrome.py`（+3 条 `TestPidIsPortOwner`）

## 交接块

- **进行中**：无（1420 全绿、已提交未推送）
- **下一步第一个动作**：用户启动林鑫阳 → 确认能进主页 → 跑 G-7 练习
- **阻塞点**：无