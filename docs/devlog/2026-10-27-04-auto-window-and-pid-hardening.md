# 2026-10-27-04 加固：窗口预校验 + 开跑回读 + pid 归属 + 清空定时清缓存（顺带修守护崩溃 P0）

## 一、目标

落实 devlog 27-03 盘点的第 1~3 条（用户“建议一起做”），并在实测中把第 4 条相关缺陷一并修掉。
全程**零平台访问**：所有场景都在 spawn 前的本地判定处拦下，或让子进程在额度前置检查处返回。

## 二、改动清单

### 1. A1 守护侧窗口预校验（`cli.py`）

触发前先判 `effective_pace(settings, row)` + `within_daily_window`（与子进程同一判定，避免“守护放行、子进程秒退”）：

| 情形 | 行为 |
|---|---|
| 窗口还没开始（`ws<=we and now<ws`，或跨午夜窗口） | `auto-skip`(INFO)「定时 xx:xx 早于学习窗口 …，等窗口开启后触发」，**不加入 skipped**，继续等 |
| 已过窗口结束 | `auto-skip`(WARNING)「已过学习窗口 …，今日不再自动启动（未消耗机会；改窗口/定时后重启守护可补跑）」+ 记 skipped |
| 等待提示 | `wait_noted` 集合保证每账号每天只写一次，不刷屏 |

### 2. A2 触发后回读（`cli.py`）

面板的 `ok=True` 只代表“进程起来了”。现在 `ok` 后记 `pending[aid]=(monotonic, ISO)`，下一轮（≥25 秒）回读：

- 标记进程还活着（`runmark.marker_process_alive`）→ 正常；
- 进程已消失且期间无 `session-stop`（`_stopped_since` 查 `run_logs`）→ `auto-error`(WARNING)
  「触发后不足 N 分钟会话已退出且未见学习记录（多为定时落在窗口外或额度已满）；本次不自动重试，改窗口/定时后重启守护或手动 ▶ 补跑」。

**刻意不自动重试**：秒退原因多为额度/窗口这类当天不会再变的状态，重试只会连续访问平台。

### 3. B pid 归属校验（`core/runmark.py` + `panel/app.py`）

- 新增 `pid_create_time(pid)`（Windows `GetProcessTimes`；Linux `/proc/<pid>/stat` 第 22 字段 + `/proc/stat btime`）、
  `pid_owns_file(path, pid)`（进程创建时间不得晚于标记文件 mtime +5s）、`marker_process_alive(data_dir, aid)`。
- `live_session` 改用 `marker_process_alive` → `busy_sessions`（面板/CLI 防双开）一并受益。
- `panel._pid_alive(pid, marker=None)` 改为委托 `runmark`：不传 marker 只判存活（兼容旧调用），传了就核归属。
- 5 个使用点全部带 marker：`_marker_jobs`、`_startup_cleanup`、`auto_start`（拒启动前）、`auto_stop`、`study_stop`。
- **两处 `taskkill /F` 前必核归属**；归属不符时**只清标记 + 写 WARNING**，绝不杀陌生进程。

### 4. C 清空定时即清缓存（`cli.py`）

`aid in planned and not row["start_at"]` → `planned.pop(aid)` 记 `auto-replan`
「定时已清空 → 丢弃缓存计划 xx:xx，改按学习窗口随机」，随后落入随机分支。

### 5. F 随机计划不得越窗（`cli.py::_random_start`，实测中暴露）

原实现无条件 `+randint(0,120)` 分钟：窗口 23:00–23:30 会随机出 23:59，窗口再短还会算出 24:xx
（字符串比较永远“未到点”）。现改为在窗口内取偏移（上限 `min(120, 窗口跨度-1)`，跨午夜用 24h 折算）。

### 6. P0 顺带修复：轮转路径守护一触发就崩（实测踩到）

`course_pid = picks[0].get("pid")` —— 学习安排返回 dict（有 `pid`），**课程轮转返回 `sqlite3.Row`**
（列名是 `platform_course_id`），`Row` 没有 `.get` → `AttributeError`，守护进程在触发瞬间直接退出。
之前只验证过“学习安排”路径，所以一直没暴露。现抽出 `_course_ref(pick)` 统一两种形态并补测试。

## 三、实测证据（真面板 + 真守护，零平台访问）

**A1（`diag_auto_window.py`，两分支）**
```
== ① 定时早于窗口 ｜start_at=01:34 窗口=02:46-02:47
[INFO] auto-skip｜定时 01:34 早于学习窗口 02:46–02:47，等窗口开启后触发
== ② 已过窗口   ｜start_at=01:34 窗口=00:01-00:02
[WARNING] auto-skip｜已过学习窗口 00:01–00:02（当前 01:36），今日不再自动启动（未消耗机会…）
无 auto-fire、无 session-start → 确认零 spawn
```

**B（`q_pid_reuse.py`）**
```
场景1（marker pid 是本进程、mtime 1 小时前）stop → {'ok': False}
      [WARNING] 标记 pid=4928 存活但不像守护进程（pid 可能已被复用），未强杀，仅清标记
      脚本进程全程存活、marker 已清
场景2（正常归属）start → {'ok': True} pid=9144 存活=True；stop → {'ok': True} 存活=False
```

**C（`q_plan_cleared.py`）**
```
[INFO] auto-replan｜#1 default 定时已清空 → 丢弃缓存计划 23:55，改按学习窗口随机
[INFO] auto-replan｜#2 小号1 定时已清空 → 丢弃缓存计划 23:58，改按学习窗口随机
[INFO] auto-plan｜#1 default 23:59、#2 小号1 23:26   ← 23:59 越窗，促成 F 的修复
```

**A2（`q_readback.py`：额度已满 → 子进程在 `async_playwright` 之前返回，零浏览器）**
```
[INFO] session-start｜知行合一，做最好的自己｜定时启动
[INFO] auto-fire｜《知行合一，做最好的自己》｜来源=轮转      ← 崩溃修复生效，轮转路径能跑了
[INFO] auto-plan｜#1 default 01:38、#2 小号1 23:50
第 32 秒命中：[WARNING] auto-error｜触发后不足 1 分钟会话已退出且未见学习记录（…额度已满）…
```

收尾：全部还原（`start_at` 10:14/09:20、窗口 NULL、`daily_max_tasks=0`、`auto.plan` 无、`data/run` 空、`jobs={}`、`auto.running=false`）；`pytest` **132 passed**（+17：pid 5 / 面板 3 / 计划与选课 9）。

## 四、文件清单

| 文件 | 变化 |
|---|---|
| `src/xxt/core/runmark.py` | `pid_create_time` / `pid_owns_file` / `marker_process_alive`；`live_session` 改用归属校验 |
| `src/xxt/panel/app.py` | `_pid_alive(pid, marker)` 委托 runmark；5 处调用带上标记文件；两处 taskkill 前核归属，不符只清标记 |
| `src/xxt/cli.py` | 窗口预校验、`pending` 回读、`_stopped_since`、清空定时清缓存、`_random_start`、`_course_ref`（P0 修复） |
| `tests/test_runmark_pid.py` | 新增 5 条（自身创建时间 / 正常归属 / pid 复用 / 死 pid / 无标记） |
| `tests/test_auto_plan.py` | 新增 9 条（随机越窗 4 / 窗口判定 2 / 选课两种形态 3） |
| `tests/test_panel.py` | 新增 3 条（复用 pid 不锁死启动 / 复用 pid 不杀 / 正常归属真杀） |
| `deploy/README.md`、`docs/DEPLOY_LINUX.md` | 登记 Docker 已知限制：`taskkill` 仅 Windows，容器内“停止”无效，待改跨平台 |
| `data/debug/diag_auto_window.py`、`q_pid_reuse.py`、`q_plan_cleared.py`、`q_readback.py` | 诊断脚本（不入库，均带留痕/锁/还原） |

## 五、交接块

- **进行中**：无。1+2+3 已全部落地并实测，P0（轮转崩溃）顺带修完。
- **下一步第一个动作**：等用户验收后说「提交」；可选做 D 之外的遗留项或重建 `dist\xxt.exe`。
- **阻塞点**：无。
- **Docker 部署同步（用户 2026-10-27 点名“后期要记得同步”）**：
  1. 本次**不需要**改 `deploy/Dockerfile` 或 `docker-compose.yml` —— 没有新增依赖/环境变量/文件；
  2. 但 `auto_stop` / `study_stop` 仍用 `taskkill`（Windows 专有），容器内停不掉孤儿守护与会话 → Docker 落地时要改成跨平台（`proc.terminate()` / `os.kill`）；
  3. pid 归属校验已兼容 Linux（`/proc`），部署后建议 `docker compose exec` 验一次 `/api/auto/start|stop` 与 `data/run` 读写权限；
  4. 上述限制已在**本次同步写进** `deploy/README.md`（「注意」末条）与 `docs/DEPLOY_LINUX.md`（常见问题表末行），部署文档与代码口径一致。
- **待观察**：A2 的回读告警在真实秒退时是否会出现（本次用“额度已满”构造过一次，第 32 秒命中）。
