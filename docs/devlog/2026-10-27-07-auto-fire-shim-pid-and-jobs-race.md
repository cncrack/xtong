# 会话 49：自动启动实测——修 P0 面板并发崩溃 + 修 P0「mlearn 把自己判成双开」（venv shim pid）

- 日期：2026-10-27（早间）
- 触发：用户开机后手动起面板并开启调度，要求「测一下能否自动启动账号刷课」；实测中面板终端抛出 `RuntimeError: dictionary changed size during iteration`
- 结论：**自动启动链路本身是通的**（`auto-fire` + `session-start` 都正常发出），失败发生在子进程自查双开时——根因是 `.venv\Scripts\python.exe` 是 venv 的重定向器，`Popen` 拿到的 pid 与真正跑代码的进程不是同一个。

## 一、实测时间线（证据）

| 时刻 | 事件 | 说明 |
|---|---|---|
| 08:02:43 | 面板启动、`auto-request pid=3256` | 用户手动起面板 + 开调度 |
| 08:02:44 | `auto-plan #2 小号1 09:20` | 首轮巡检只排了 #2（#1 见下） |
| 08:09:12 | `diag-set-start` #2 09:20 → 08:11 | 本会话为验证「到点自动开刷」临时改库（先备份 `data/debug/start_backup.json`） |
| 08:09:14 | `auto-replan #2 定时改为 08:11（原 09:20）` | 运行中改定时实时生效 ✔ |
| 08:11:14 | `auto-fire｜来源=轮转` + `session-start` | **定时触发链路通了** ✔ |
| 08:11:44 | `auto-error 触发后不足 1 分钟会话已退出` | 见二：`sub-mlearn-20261027-081114.log` = `[FAIL] 账号 #2 已有会话在跑（…pid 5040）` |
| 08:24 | 面板重启（新代码）+ 停/启调度 | 守护 `pid=13964`，`/api/study/status` 200 |

**#1 为什么今天没被排**：今天 01:40:12 有一条 `auto-fire #1`（诊断期间改 `start_at` 触发、30 秒后 `auto-error` 秒退）。守护启动时从 `run_logs` 恢复 `fired`，于是认为 #1 今日已触发。属既有设计取舍（刻意不自动重试，避免连续访问），本次不改；重启守护也**不能**给回机会（`fired` 是跨重启从库里恢复的）——说明文案里"重启守护可补跑"只对 `skipped` 成立，对 `fired` 不成立，已记入待办。

## 二、P0-A：mlearn 把自己判成"别人在跑"（根因：venv shim pid）

链路复盘：

1. 面板 `study_start` → `_spawn("mlearn", ...)` → `proc.pid`；
2. 面板**立刻** `write_marker(data_dir, aid, course, pid=proc.pid)`（防 spawn 后 1~2 秒空窗被连点）；
3. 子进程 `xxt mlearn` 启动，`cli.py` 守卫调 `busy_sessions()` → 读到这条标记 → `pid_alive` 真、`pid_owns_file` 真；
4. 旧 `busy_sessions` 只排除 `pid == os.getpid()` → 判成"别人在跑" → `[FAIL]` 秒退。

第一版按 `os.getpid()` 修**没生效**，用最小复现（`data/debug/q_repro_busy.py`、`q_exec_probe.py`）才查到真因：

```
exe  = E:\xxt\.venv\Scripts\python.exe   ← venv 重定向器（shim）
popen_pid = 5908                          ← 面板拿到的 proc.pid
child_pid = 22640                         ← 真正跑 mlearn 的进程
```

`Popen` 起的是 shim，shim 再拉起真解释器（孙进程），两者 pid 不同；**孙进程的 `ppid` 恰好就是 shim**。面板写进标记的 pid 永远等于"子进程的父进程"，所以判据应是 `pid ∈ {os.getpid(), os.getppid()}`。

修法（`core/runmark.py::busy_sessions`）：

```python
me, parent = os.getpid(), os.getppid()
...
if int(info.get("pid") or 0) in (me, parent):
    continue   # 自己/替自己占位的父进程，不是"别人"
```

`live_session` 语义不动（面板仍要能看见这条会话）。

实测：手动 ▶ #2（同一 `/api/study/start` 路径）→ `[mlearn] 课程匹配《知行合一…》` → 错峰 90/174 秒 → 进知识点 → **开始播放 倍速 1.11/1.22、进度 69/495s**；标记里的 pid 也变成子进程自写的真 pid（`{"pid": 29052, ...}`）。`■ 停止` 同步验证：真进程退出、`session-stop` 入日志，标记残留由 `_marker_jobs` 下次判活时清掉。

## 三、P0-B：`/api/study/status` 并发遍历 `_JOBS` 崩溃

前端每 1~2 秒轮询该接口，而启动/停止会话、作业线程会同时 `_JOBS[aid] = ...` / `pop(aid)` → `for ... in _JOBS.items()` 抛 `RuntimeError: dictionary changed size during iteration`（ASGI 500）。

- 改快照遍历 `for aid, job in list(_JOBS.items())`；
- 清理时加 identity 判定 `if not alive and _JOBS.get(aid) is job`，避免把"期间刚被新会话替换的条目"删掉（否则界面丢会话）；
- 顺手修同源隐患：`work_api` 里 `if account_id in _JOBS and _JOBS[account_id]["proc"]...` → 一次 `job = _JOBS.get(...)` 取值，消除两步之间的 `KeyError`。

实测：连打 25 次 `/api/study/status` 全 200（此前必崩）。

## 四、顺手修：已触发账号的定时改完后，「今日定时」显示不刷新

`cmd_auto` 里 `if aid in fired or aid in skipped: continue` 在 `planned` 同步**之前**，导致已触发的号改了 `start_at` 后，`app_config['auto.plan']` 一整天停在旧时刻（本次 #2 还原 09:20 后界面仍显示 08:11，次日跨日才自愈）。改为先同步展示值再 `continue`（不触发、不写决策日志）。

实测：重启守护后 `auto.plan` = `{"2": "09:20", "1": "08:55"}` ✔

## 五、改动的文件清单

| 文件 | 改动 |
|---|---|
| `src/xxt/core/runmark.py` | `busy_sessions` 忽略 `os.getpid()` / `os.getppid()`（含 venv shim 说明） |
| `src/xxt/panel/app.py` | `study_status` 快照遍历 + identity 清理；`work_api` 改一次取值 |
| `src/xxt/cli.py` | `cmd_auto`：fired/skipped 的账号也同步 `planned`（仅展示） |
| `tests/test_runmark_pid.py` | +3：自身 pid 不算忙、父进程 pid 不算忙、真第三方进程仍算忙 |
| `tests/test_panel.py` | +1：轮询接口与启停线程并发改 `_JOBS` 时不 500 |

数据侧（不入库）：还原 #2 `start_at` 09:20（`diag-restore-start` 留痕）、`data/debug/{q_auto_state,q_ready,q_set_start,q_watch_fire,q_after,q_fired,q_restore_start,q_manual_start,q_repro_busy,q_exec_probe}.py`。

`pytest`：**146 passed**（+4）。

## 六、遗留 / 待办

1. **"重启守护可补跑"对 `fired` 不成立**：`auto-error` 文案与 `skipped` 文案可复用同一句，但 `fired` 是从 `run_logs` 恢复的，重启也回不来。若确实要"失败后可再来一次"，需给 `auto-fire` 增加"成功标记"（如 30 秒回读通过才落 `auto-fire`，否则落 `auto-fire-try`），属设计变更，等用户拍板。
2. `study_stop` / `auto_stop` 仍用 Windows 专有 `taskkill`（Docker 落地时改跨平台，已在 STATUS 登记）。
3. `dist\xxt.exe` 仍是 10-25 旧构建。

## 七、交接块

- **进行中**：无。#2 当前在刷《知行合一，做最好的自己》（错峰后已播放）；守护 `pid=20020`（08:32 起），`auto.plan` = `{#2 09:20, #1 08:55}`。
- **下一步第一个动作**：观察 #2 跑到额度停机是否正常（日志 `data/logs/sub-mlearn-*.log`），并确认 #1 明日 08:55 能自动开刷（今日 #1 的 `auto-fire` 已被 01:40 那次消耗）。
- **阻塞点**：无。
