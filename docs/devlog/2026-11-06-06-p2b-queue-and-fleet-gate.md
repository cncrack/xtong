# 2026-11-06-06 P2b：真排队 + 全局日时长闸

## 一、本次目标

用户：「继续」。接着 P2a 做剩下的两块：

1. **P2b 队列** —— `PLAN-GXKS.md` §11.5 写的是「否则按并发上限**排队**」，
   而 P2a 只做到「**拒绝**」；
2. **`fleet_daily_minutes`** —— §11.6 的全局日总量闸，方案有、代码零。

## 二、全局日时长闸（`fleet_daily_minutes`）

### 与另外两个「额度」的区别（这是最容易混的地方）

| 闸 | 维度 | 防的是 | 在哪配 |
|---|---|---|---|
| 账号 `daily_sections` / `daily_minutes` | **单个账号** | 一个号一天学 8 小时很可疑 | 账号表（✎ 编辑） |
| **`fleet_daily_minutes`（本项）** | **全部账号合计** | 这台机器 / 这个出口 IP 一天总时长异常 | 面板设置 |
| `max_concurrency` | **同时在跑几个** | 本机资源 + 并发特征 | 面板设置 |

⚠ **`0` 的语义在两项上相反**，这是刻意的、也是最容易埋雷的地方：

- `max_concurrency`：**0 会被钳成 1**（它意思是「限几个」，0 = 闸门失效，最危险）；
- `fleet_daily_minutes`：**0 = 不限**（沿用额度语境，与 `PLAN-GXKS.md` 一致）。

两处都在代码注释里写明了，并有测试守护（`test_concurrency_never_zero` /
`test_fleet_zero_means_unlimited`）。

### 为什么用账本而不是实时计时

账本（`data/gxks_ledger.jsonl`）是**唯一权威**（`ledger.py` 模块早已这样定）。
实时计时会漏掉「刚跑完还没记账」与「面板重启前的消耗」，而账本每次任务收尾都写、跨进程可见。

默认 **0 = 不开**，理由写在 `gxks_panel_settings.DEFAULTS` 里：
它是最粗的一刀，开得太小会让前几个账号把配额吃光、后面的号一天都跑不了。

## 三、P2b 队列

### 数据结构与落盘

| 项 | 说明 |
|---|---|
| 内存 | `_QUEUE: dict[label, {...}]`（按 label 唯一，同账号不重复排队） |
| 落盘 | `data/gxks_queue.json`（原子替换） |
| 为什么和 `gxks_jobs.json` **分两个文件** | 那个回答「谁在跑」，这个回答「谁在等」；语义不同、清理时机也不同 |

### 四个触发点

1. **入队**：`do_study` / 批量 `start` 遇到 `concurrency_full` → `_queue_add`，
   返回 `queued: True`（**算受理成功**，不是失败）。
2. **有空位就泵**：`_queue_pump()` 先算 `room = 上限 - 在跑`，`room <= 0` 就停；
   队头优先（先来先跑）；每起一个就重新算（`while` 循环）。
3. **任务结束时立刻泵**：`_jobs_snapshot` 检测到有任务自然结束 → `_on_job_finished()`
   —— 不能等后台循环（20 秒），用户刚看到一个跑完，队列应该马上接上。
   ⚠ 只在**真有任务结束**时触发：这个函数被 4 秒轮询高频调用，不能每次都泵。
4. **后台循环**：`_queue_worker` 每 20 秒看一次。间隔理由：刷课一节 20~45 分钟，
   20 秒延迟无感；太短会白耗 CPU（每次都要探端口，最坏吃一个 timeout）。
   线程异常**绝不退出**（catch + 睡 60 秒继续）—— 它死了队列就永远不动，且用户看不出来。

### 停止必须撤队

`_stop_one` 先 `_queue_drop`。不撤的话会出现最让人火大的行为：
**点了「停止」，20 秒后后台循环把它又拉起来了**。
只排队、没在跑时点停止 → 返回「已取消排队（它本来还没开始跑）」，也算成功。

### 重启不丢

`adopt_queue()` 在 `create_app` 里、**孤儿任务认领之后**执行（顺序很重要：
先认领才能在「已在跑」的集合里去重）。剔除三类：账号表里没有的、已经在跑的、格式坏的。

## 四、语义变化（重要）

**P2a 的行为被 P2b 取代**：

| 场景 | P2a（旧） | P2b（新） |
|---|---|---|
| 单账号开，并发满 | `blocked` 拒绝 | **入队**，返回 `queued` |
| 批量 3 个、上限 2 | 起 2 个 + 1 个 `blocked` | 起 2 个 + **1 个排队** |
| 汇总文案 | 「N 个因并发已满未启动」 | 「N 个已排队等空位」 |
| 前端颜色 | 红/黄提示 | **绿 toast**（排队是受理由，不是失败） |

`blocked` 现在只剩两种来源：`fleet_full`（今日全局时长满了）与「有任务在跑需先停」。

## 五、验证

- `pytest -q`：**1130 passed**（15 subtests）
- `tests/test_gxks_concurrency.py` 增至 **31 条**，新增队列专项：
  - 落盘 / 重启重建 / 剔除（在跑+未知）/ 泵起（有空位）/ 泵不起（满）/ 全局满清队；
  - 停止撤队 / `/api/state` 带 queue；
  - `fleet` 满则拒、0 = 不限。
- 改写 P2a 的 4 条为 P2b 语义（`queued` 而不是 `blocked`）；
- 页面 JS `node --check` 通过；
- 实机（重启面板后）：
  ```
  DB: {..., 'max_concurrency': 2, 'start_jitter': 30, 'fleet_daily_minutes': 0}
  /api/state: live_study=0  fleet_minutes_today=154.9  fleet_limit_minutes=0  queue=[]
  页面: f-fleet / 「全局日时长上限」/ 「排队中 」/ save 带 fleet_daily_minutes   全部 OK
  ```

## 六、改动文件清单

- `src/xxt/store/gxks_panel_settings.py`（+`fleet_daily_minutes`：白名单 / 解析 / 钳制 0~1440）
- `src/xxt/panel_gxks/app.py`
  - 队列一套：`_queue_path/_read_queue/_save_queue/_queue_add/_queue_drop/_queue_snapshot/
    adopt_queue/_queue_pump/_queue_worker/_ensure_queue_worker`
  - `_on_job_finished` + `_jobs_snapshot` 里的触发
  - 全局闸：`_fleet_daily_limit/_fleet_minutes_today/_check_fleet_daily`
  - `do_study` 入队、批量入队、`_stop_one` 撤队、`create_app` 恢复队列、`/api/state` 加 `queue`
- `src/xxt/panel_gxks/page.py`（`f-fleet` 输入框、排队标签、`batchAction` 三分支改写、save/load 接线）
- `tests/test_gxks_concurrency.py`（21 → 31 条）
- `data/debug/diag_p2b_queue.py`（新增，只读）

## 七、交接

**进行中**：无。

**下一步第一个动作**：用户 `Ctrl+F5` → 「账号设置 → 面板设置」应多出「**全局日时长上限**」（默认 0）；
批量栏在有人排队时应出现「**排队中 N**」黄标签。

**阻塞点**：无。

**已知边界（都是软闸/软队列的固有限制，写进代码注释了）**：

1. **并发计数是进程内的**：`_live_study_count()` 读 `_JOBS`，面板重启后靠
   `adopt_orphan_jobs` 认领恢复；**认领失败时计数会归零** → 可能实际在跑 2 个、面板以为 0 个，
   于是多起一个。要根除得把「正在跑」也做成完全以 pid 文件为准（当前已是尽力而为）。
2. **队列不保证「跑完自动续」**：一个账号跑完本节后**不会**自己再排队下一批
   （那是自动续课/守护的范畴，属 `run_gxks.py --max-sections` 一次性跑完 N 节）。
3. **`fleet_daily_minutes` 只看账本里已记账的分钟**：正在跑但还没记账的那一节不计入，
   所以上限可能被"超出一点"（比如上限 100、已记 98，再起一个 45 分钟的还会放行）。
   这是有意的：宁可放行也不误拒（账本读不到就直接不拦）。
