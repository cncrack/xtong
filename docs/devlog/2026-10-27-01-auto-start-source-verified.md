# 2026-10-27-01 核实：四次 auto 守护启动的真实来源

## 一、目标

devlog 14 §8 遗留的核实项：10-26 的 **11:34:42 / 11:39:16 / 11:42:22 / 11:43:21** 四次 `子进程 auto 已启动`，是否用户点击、是否诊断脚本、是否工具层重试。用户当场确认"我没有点"，本次用文件证据落定。

## 二、取证材料（全部为磁盘实证，无平台访问）

| 证据 | 内容 |
|---|---|
| `data/debug/auto_live_out.txt` | mtime `11:43:08`，内容为一次完整脚本流程：原始 `start_at={1:'10:14',2:'09:20'}` → 临时改 `{1:'23:50',2:'23:55'}` → `POST /api/auto/start → {"ok":true}` → 抓状态 → `POST /api/auto/stop → {"ok":true}` → `已还原 start_at` → `running:false` |
| `data/logs/sub-auto-*.log` × 4 | `113442`（96B，仅 logging 行，**无** `[auto]` 计划行 → 当时 `_spawn` 还没加 `-u`，print 未 flush）；`113916`/`114222`（312B，含 `#1 23:50`/`#2 23:55` 计划行）；`114321`（386B，含计划行 + `[auto] 守护退出`，11:44:00 结束） |
| `data/logs/xxt.log` | 四条 `子进程 auto 已启动 pid=25112/7728/9512/13512`，均出自 `xxt.panel`；`xxt.cli` 侧各有一条 `auto 守护启动` |
| `data/debug/guard_out.txt` | mtime `11:43:11`，内容是 `diag_guard.py` 的双开守卫测试（伪造 `data/run/1.json` + CLI 退出码），**不含** `POST /api/auto/start` |
| 代码 | `src/xxt/panel/app.py` 中 `_spawn(..., "auto")` 只有一处调用点：`auto_start()`（`POST /api/auto/start`）。前端 `toggleAuto()` 是唯一发 `auto/start|stop` 的 JS，且仅绑定按钮点击，无任何自动/自愈逻辑 |
| 脚本清点 | `data/debug/*.py` + `scripts/*.py` 中，会 `POST /api/auto/start` 的只有 `diag_auto_live.py`（`diag_auto_dry.py` 干跑不启动、`diag_guard.py` 走 CLI 不启动） |
| `ConsoleHost_history.txt` | 4133 行，共用全局单文件；`test_auto_live.py` 只有 **2 行**执行记录（`L4051` 无 sleep、`L4055` 带 `Start-Sleep 4`）；`L4059` 之后重命名为 `diag_auto_live.py` 且未再执行；无任何直接 `POST /api/auto/*` 手敲命令，也无 `xxt.exe auto` |

## 三、结论（按确定程度分三层）

**1. 不是用户点击（确定）**
- spawn 唯一入口是 `POST /api/auto/start`；前端只有按钮一处调用，无轮询/自愈触发。
- 四次读到的计划值都是 `23:50 / 23:55`——这是 `diag_auto_live.py` 里写死的临时值（`future = {1: "23:50", 2: "23:55"}`），UI 不可能产生这种取值，用户也不会手工设成两个号差 5 分钟的深夜时刻。
- `11:43:21` 那次启动时 `start_at` 已被脚本还原创 10:14/09:20，但守护仍打印 23:50/23:55 → 因为它读的是 `app_config['auto.plan']` 缓存（该残留当晚已清除）。这进一步说明触发它的仍是同一个脚本上下文。

**2. 四次全部与 `diag_auto_live.py` 相关（高置信）**
- `11:34:42`：首次执行，随后 `11:34:43` 面板被杀（为给 `_spawn` 加 `-u` 而改 `app.py` → `py_compile` → `Stop-Process` → 重启面板），子进程随之终止，故日志只有一行 logging、无 print。
- `11:42:22`：完整执行一次（`11:42:22 + 45s sleep ≈ 11:43:07` 抓状态并 stop，与 `auto_live_out.txt` 的 mtime `11:43:08` 精确吻合）。
- `11:39:16` / `11:43:21`：形态相同（启动后读到同一批 23:5x 计划、40 秒内被 stop；后者留下 `[auto] 守护退出`），但没有对应的独立执行记录。

**3. 执行次数与历史记录不一致（确定的矛盾）**
- 实测 4 次启动 vs 历史 2 条脚本命令。`ConsoleHost_history.txt` 是 PSReadLine 全局单文件，只有"交互式终端输入"才落盘；以一次性 `-Command` 方式起的 PowerShell 不写历史。因此**"只跑过 2 次"不成立**，多出的两次来自不经交互终端的执行通道（工具重试 / 并行会话），现有材料无法再细分。

## 四、后果评估（无损失）

- 四次守护计划均为未来时刻，`jobs` 全程为空，当日 `today_videos = 0`，未产生任何学习会话或平台访问。
- 唯一的真实副作用是 `app_config['auto.plan']` 与 `data/run/auto.json` 残留，均已在 10-26 清除。

## 五、机制性改进（本次不动代码，留作可选）

1. `auto_start()` 现在只回 `{"ok": true}`；可给 `run_logs.auto-start` 的 detail 加上来源标识（如 `source=ui|api`、或 `User-Agent` 截断），让"谁开的守护"从页面直接可判。
2. 给 `diag_auto_live.py` 补 AGENTS「诊断脚本三原则」：写 `diag_auto_live` 事件、加文件锁防并发、`finally` 里除还原 `start_at` 外还要清 `app_config['auto.plan']`（10-26 已加 `diag_logs_page.py` 作范本）。
3. 守护启动时若 `app_config['auto.plan']` 与当前 `start_at` 不一致，应打一条 WARNING（缓存陈旧可见），避免"看到 23:50 却查不出谁写的"。

## 六、交接块

- **进行中**：无。核实已完成，未改任何代码。
- **下一步第一个动作**：等用户决定是否采纳第五节任一条（需先出方案再动手）；否则继续用页面「📜 日志 → 业务事件」观察守护。
- **阻塞点**：无。
- **文件清单**：本文件（新建）、`STATUS.md`（最近更新行 + §3 待核遗留行改为核实结论）。
