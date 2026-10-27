# 会话 43：日志页四项改造落地（守护事件入库 / 系统日志 / 可搜索筛选 / 自动刷新）

- 日期：2026-10-26
- 结果：**四项全部完成并实测通过**，111 → 113 测试全绿，面板已用新代码重启。

## 一、用户诉求（早前拍板，本次执行）

1. 日志页只看得到业务事件，**看不到守护/系统在干什么**（页面停在 09:46）。
2. 账号筛选是超长下拉，账号多了没法用 → 改**可搜索**。
3. 「⟳ 刷新」与"自动刷新"语义混淆 → 改成**自动刷新开关**。
4. 一并加**系统日志视图**（读文件，不用再去翻 `data/logs/`）。

## 二、改动清单

### ① 守护决策写进 `run_logs`（`src/xxt/cli.py::cmd_auto`）

新增内部函数 `_ev(aid, event, detail, level="INFO")`：失败绝不影响调度（吞异常），`aid=0` 表示系统级。

| 事件 | 何时写 | detail 示例 |
|---|---|---|
| `auto-start` | 守护启动 | `守护启动（每 30 秒巡检）｜今日待跑 2 个账号` |
| `auto-plan` | 首次算出当日计划 | `#1 default 23:55、#2 小号1 23:55` |
| `auto-fire` | 成功触发 / 已在跑（手动开） | `《知行合一…》｜来源=轮转` |
| `auto-skip` | 未登录 / 无课 / 缺课 / 面板判缺课 | `缺课《知行合一…》｜不消耗当日机会（补抓后可手动▶）`（WARNING） |
| `auto-error` | 面板未响应等失败（同因只记首次） | `触发失败：…（30 秒后重试）`（WARNING） |
| `auto-stop` | 守护收到 Ctrl+C | `运行 12 分钟｜今日触发 2 个｜跳过 0 个` |

**去重**：只写"状态变化"，不随 30 秒巡检重复写（否则一天上千行把真事件顶掉）。

### ② 顺带修掉两个守护自身缺陷（改这处时发现的）

- **跨日不重置**：原来只清 `planned`，`fired/skipped` 跨天不清 → 守护长跑过零点后，**次日在 fired 里的账号永不触发**。现三个集合一起清。
- **重启会重复刷**：原来守护重启后 `planned` 为空 → 计划时刻重新随机，且当天已刷完的账号会被再触发一次。现启动时从库恢复：
  - `app_config['auto.plan']`（当日）→ 恢复 `planned`；
  - `run_logs` 当日 `auto-fire` → 恢复 `fired`；
  - **刻意不恢复 `skipped`**：未登录/缺课本就可当天修好（登进去、补抓课程），重启后应允许补跑。

### ③ `/api/logs` 支持系统日志（`src/xxt/panel/app.py`）

```
GET /api/logs?source=event|sys&min_level=WARNING|INFO&sub=0|1&accounts=1,2&n=20
```

- `source=sys`：尾部读 `data/logs/xxt.log`（deque 2000 行），正则
  `^(\d{4}-\d\d-\d\d) (\d\d:\d\d:\d\d),\d+ (\w+) \[([^\]]+)\] (.*)$` → `{ts, level, event=logger, detail, who:"系统"}`。
- `sub=1`：并入**当天**的 `sub-*.log`（最多 4 个）；文件里带时间戳的 logging 行**跳过**（`xxt.log` 已有，避免重复），只补 print/traceback 行（`event=sub:<文件名>`，时间对齐上一条 logging 行）。
- `accounts=1,2`：供可搜索筛选多命中，SQL 走 `IN (?,?)`。

### ④ 前端日志页（同一文件 `_PAGE`）

- 新增「来源」下拉：`业务事件 / 系统日志（警告+）/ 系统日志（全部）/ 含子进程输出`。
- 账号筛选：`<select>` → **`<input list="logAccList">` + `<datalist>`**；解析规则 `#2`/`2` → 该号，文字 → 姓名/备注/平台昵称模糊（可多命中）；输入但未命中 → 只提示"未匹配到账号"，不发请求。
- 「⟳ 刷新」→ **自动刷新开关**（`localStorage.logAuto`，默认开）+「⟳ 刷一次」+「更新于 hh:mm:ss」；关闭后 `syncRun()` 不再重渲染日志表（账号卡片/统计仍 15 秒刷新）。
- 配色：`auto-fire/video-done` 绿、`auto-start/auto-plan` 蓝、`auto-skip/auto-stop/auto-error/WARNING` 橙。
- 面板侧 `auto_stop` 也补写一条 `auto-stop`（守护被 taskkill 时自己写不了）。

## 三、实测证据（零平台访问）

`data/debug/diag_logs_page.py`（脚本自带 trace：向 `run_logs` 写 `diag-logs-page` 两条，并把 `start_at` 临时推到 23:55 避免真触发，`finally` 还原）：

```
原始 start_at = {1: '10:14', 2: '09:20'}
页面元素：logSrc=True datalist=True logAuto=True logAccInput=True；旧长下拉已移除=True
POST /api/auto/start → {'ok': True}
业务事件里的守护记录：
    2026-10-26T16:08:35 auto-plan | #1 default 23:55、#2 小号1 23:55
    2026-10-26T16:08:35 auto-start | 守护启动（每 30 秒巡检）
系统日志(警告+) = 0 条（今日无警告）；含子进程 = 8 条（含 sub: 行）
按账号 #1 筛 = 5 条；按 #1,#2 = 5 条
POST /api/auto/stop → {'ok': True}
停止后最新事件 = auto-stop | 面板停止定时调度（已在跑的学习会话不受影响）
已还原 start_at = {1: '10:14', 2: '09:20'}
```

`data/debug/diag_ui12.py`（headless 实渲染）：

```
业务事件行数 = 20；含子进程行数 = 20；有 sub: 行 = True
系统日志(警告+) = 暂无记录；输入 #2 后 who 列 = ["乙同学"]
无匹配提示 = 未匹配到账号：“zzz-不存在”（清空即看全部）
关闭后 localStorage = 0；刷新后开关 = False；pageerror = []
```

新增 2 条单测（`tests/test_panel.py`）：`test_logs_accounts_filter`、`test_logs_sys_source_reads_file`。

## 四、明确没做的事（避免误解）

| 项 | 原因 / 现状 |
|---|---|
| 面板退出时自动杀守护 | **不做**。守护是独立进程，用户可能想"关面板挂机"；`/api/study/status` 已按 `auto.json`+pid 判断，面板重开后仍显示"调度运行中"并可停 —— 这是特性不是 bug |
| `run_logs` 定期清理 | 未做。事件量小（每天每号 ≤5 行），先观察；将来要清理再加 `prune(keep_days=90)` |
| 守护侧 `window_end` 预校验 | 仍未做（§3 等用户 ⑧），与本次无关 |
| 跨平台 `auto_stop` | 仍用 `taskkill /F`（Windows）；Linux 部署时需换 `terminate()` |

## 五、文件清单

- `src/xxt/cli.py`：`cmd_auto` 事件入库 + 跨日重置 + 重启续跑恢复（+~65 行）
- `src/xxt/panel/app.py`：`import re/deque`；日志页 HTML/JS 重做；`/api/logs` 支持 `source/min_level/sub/accounts`；`auto_stop` 写事件
- `tests/test_panel.py`：+2 条（`accounts` 筛选、`sys` 日志解析/去重）
- 诊断（`data/` 已忽略）：`data/debug/diag_logs_page.py`、`diag_ui12.py`、`run_panel.py`，输出 `ui12_out.txt`

## 六、交接块

- **进行中**：无。四项改造已上线并实测。
- **下一步第一个动作**：用户 `Ctrl+F5` → 「📜 日志」看四件套（来源下拉 / 账号输入框 / 自动刷新开关 / 刷一次）；若要看守护动作，**先点「⏱ 定时调度」启动守护**（注意：`start_at` = 10:14/09:20 均已过点，一点两个号会立刻补跑，想晚上跑先把定时改成未来时刻）。
- **阻塞点**：无。
- **待提醒**：`dist\xxt.exe` 仍是 10-25 18:58 旧构建（不含本次日志页），需要时重建（`scripts/build_exe.py --onefile`，限并发后台跑）。
- **风险**：`data/logs/xxt.log` 会随运行增长，`source=sys` 每次只读尾部 2000 行；若日志轮转（`xxt.log.YYYY-MM-DD`）后想看更早的技术日志，目前只能翻文件。
