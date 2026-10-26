# 2026-10-27-02 加固：守护启停可追溯 + 诊断脚本三原则落地 + 发现面板反复消失真因

## 一、目标

承接 devlog 27-01 的核实结论（四次守护启动来源查了半小时），落实三条加固，并顺带解决"面板老是掉"。

## 二、改动清单

### 1. 启停来源可追溯（`src/xxt/panel/app.py`）

| 项 | 内容 |
|---|---|
| 新增 `_auto_who(request, source)` | `source=ui` → `面板按钮`；否则按 UA 记 `API(httpx)` / `API(requests)` / `API(浏览器)` / `API(无UA)` |
| `POST /api/auto/start` | 新增 `source` 参数；请求受理与被拒**都**写 `run_logs.auto-request`（被拒记 `WARNING`，含 `pid`）；第二次启动仍不 spawn |
| `POST /api/auto/stop` | `source` 参数；`auto-stop` 事件 detail 带来源 |
| `_log_event` | 新增 `level` 参数（默认 INFO），供 WARNING 事件使用 |
| 前端 `toggleAuto()` | 发 `?source=ui` |
| 日志配色 | `auto-request`/`auto-replan` 蓝、`auto-plan-stale` 黄 |

### 2. 守护侧：缓存计划与当前定时不一致必须可见（`src/xxt/cli.py`）

- `auto-start` detail 由"待跑 N 个账号"改为**带计划摘要**：`守护启动（每 30 秒巡检）｜今日计划 #1 23:50、#2 23:55`（无缓存时写"待首轮巡检确定"）——10-26 的 `23:5x` 残留就是靠这行能一眼认出。
- 首轮巡检：`planned[aid]` 与 `accounts.start_at` 不一致时 → `auto-plan-stale`（**WARNING**，文案含两侧时刻）并按当前 `start_at` 执行、覆盖缓存。
- 非首轮：用户在守护运行中改定时 → `auto-replan`（INFO）并即时生效（原实现"已算出计划就不再更新"，改定时要等次日）。

### 3. 诊断脚本按 AGENTS「三原则」加固（`data/debug/`，不入库）

`diag_auto_live.py` 重写：
- **留痕迹**：全程写 `run_logs.diag-auto-live`（开始/被锁拦截/中止/结束摘要）。
- **幂等可重入**：`data/run/diag-auto-live.lock` 文件锁（`O_EXCL`），10 分钟陈旧锁自动清；并发重跑直接退出（这正是 10-26 出现四次启动的土壤）。
- **清干净副作用**：`finally` 除还原 `start_at` 外，**再清 `app_config['auto.plan']`**（10-26 漏掉这条，导致守护下次读到 23:50 陈旧缓存）。
- 新增 `--via-ui` 开关：伪装面板按钮（`source=ui`）并发两次以验证“重复启动被拒”。
  > ⚠ **2026-10-27 会话 47 纠偏**：真起脚本时它同样带 `source=ui`，而首版 `_auto_who` 见 ui 就记“面板按钮”、跳过 UA 判断 → 脚本与真人点击在日志里**无法区分**。现已改为“自称 + 证据”双重校验，脚本应改带 `X-Diag` 头；详见 devlog `27-05`。
- 等待从 45s 缩到 15s（守护进 while 即巡检一轮）。

新增 `data/debug/diag_plan_stale.py`：布置"缓存 23:50/23:55 vs 当前定时 23:40/23:45"（都在未来，零开刷风险），验证陈旧缓存链路。

### 4. 测试（`tests/test_panel.py`，+2 → 共 17）

| 用例 | 断言 |
|---|---|
| `test_auto_start_records_source_and_rejects_second` | `_spawn` 被 mock（`pid=os.getpid()`，不真起进程）；第一次 `ok=True` 且 detail 含"面板按钮"；第二次 `ok=False`、`_spawn` 只调 1 次、写 `WARNING` 且含"被拒" |
| `test_auto_start_api_flagged_by_ua` | 无 `source` 且 UA=`python-httpx/0.27.0` → detail 含 `API(httpx)` |

## 三、实测证据（真面板 + 真守护，零平台访问）

**A. `diag_auto_live.py --via-ui`**（`auto_live_out.txt`）
```
原始 start_at = {1: '10:14', 2: '09:20'}
临时改为 {1: '23:50', 2: '23:55'}
POST /api/auto/start?source=ui → {"ok": true}
再次启动 → {"ok": false, "error": "定时调度已在运行"}
守护状态 auto = {"running": true, "since": "00:54"}｜jobs = {}
POST /api/auto/stop?source=ui → {"ok": true}
已还原 start_at = {1: '10:14', 2: '09:20'}，已清 auto.plan 缓存
守护子进程日志：账号#1 今日计划 23:50 开刷 / 账号#2 今日计划 23:55 开刷
```

**B. `diag_plan_stale.py`**（`plan_stale_out.txt`）
```
auto-request  [INFO]    启动请求已受理（pid=2872）｜来源=面板按钮
auto-start    [INFO]    守护启动（每 30 秒巡检）｜今日计划 #1 23:50、#2 23:55
auto-plan-stale [WARNING] #1 default 缓存计划 23:50 ≠ 当前定时 23:40（已按当前定时执行…）
auto-plan-stale [WARNING] #2 小号1 缓存计划 23:55 ≠ 当前定时 23:45（已按当前定时执行…）
auto-stop     [INFO]    面板停止定时调度（来源=面板按钮；已在跑的学习会话不受影响）
已还原 start_at = {1: '10:14', 2: '09:20'}｜auto.plan = None
```

收尾健康检查：`pytest` **115 passed**；8300 在听；`jobs={}`、`auto.running=false`；`data/run/auto.json` 不存在；`*.lock` 无残留。

## 四、顺带查明：面板"老是掉"的真因

本轮启动面板时发现：`Start-Process` 拉起的进程**在 Bash 命令结束的瞬间就被回收**（launcher 立即消失、无 stdout/stderr、端口未监听）。原因是我这条命令跑在 Qoder 的 **sandbox.exe** 里，命令结束会清理子进程树。

- 现象印证：昨天起的面板（pid 30984，23:39 建）在我今天第一次 `Stop-Process` 时报"8300 未监听"，实际它还活着 —— 说明探测与回收都存在时序问题。
- 解法：把启动命令用 `required_permissions=all`（**沙箱外**）执行 → 进程常驻，页面立刻变成新版本（79,604 字符、含 `source=ui`）。
- 这也解释了此前"面板反复消失、每次都要重起"的困惑：不是程序崩，是被沙箱收走了。

## 五、文件清单

| 文件 | 变化 |
|---|---|
| `src/xxt/panel/app.py` | `Request` 导入、`_auto_who`、`auto_start/auto_stop` 加 source 与事件、`_log_event` 加 level、前端 `?source=ui`、日志配色 |
| `src/xxt/cli.py` | `_plan_brief()`、首轮 `auto-plan-stale`、运行中 `auto-replan`、`first_pass` |
| `tests/test_panel.py` | +2 用例（mock `_spawn`，不真起守护） |
| `STATUS.md` | 最近更新、§3 交接字段、§4（沙箱回收说明）、§5 会话 45 |
| `data/debug/diag_auto_live.py` | 三原则加固 + `--via-ui` |
| `data/debug/diag_plan_stale.py` | 新增（陈旧缓存链路验证） |

## 六、交接块

- **进行中**：无。三条加固完成并实测，等用户说「提交」。
- **下一步第一个动作**：`Ctrl+F5` 面板 →「📜 日志」选「业务事件」，应能看到 `auto-request`（来源=面板按钮）与 `auto-plan-stale`（黄）。之后可重建 `dist\xxt.exe`。
- **阻塞点**：无。
- **注意**：**起面板必须在沙箱外**（`required_permissions=all`），否则命令一结束进程就被回收；用户自己开 PowerShell 跑 `.venv\Scripts\xxt.exe panel` 不受此限。
