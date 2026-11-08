# 2026-11-08-04 节缓存状态一致性（两个进度区显示 bug 修复）+ T4 账本聚合

> 用户 14:50 报告 + 当场提问：
> ① 两个账号进度区显示不一样——甲（default）「还是原来的显示内容」，4.6 已学完仍在待学区显示「0/19 分 未学习 未解锁」；乙（test）正在播的节能显示「学习中」
> ② G-4 并发口径三个数字互相冲突，具体是什么内容
> ③ 多账号时进度区/日志的交互设计要优化；「注意我们约定，不要老是我提醒」
> ④ T4 可以并行开始

---

## 一、根因（日志 + 缓存 + 代码三重证据，全部只读核实）

### 症状①：「学习中」显示不稳定 / 进度区像没更新

35 秒间隔两次 `/api/state` 快照（实测）：

```
default 1319: learning/0分  →  not_started/5分     （被打回！）
test   1295: learning/16分 →  not_started/17分    （同样被打回！）
```

机制：`tick`（30 秒、零平台开销）把当前节标 `learning`；`progress`（300 秒、要回读平台列表）拿到的 `state` 是**平台列表页**的值——平台列表页的 state 翻转比学时滞后（学时 60 秒上报一次、列表缓存更久），回读到 `not_started` 是常态。`sections_cache.update_row` 原来**无条件照写**平台 state → 把 tick 刚标的 learning 覆盖掉。两个账号都在 learning↔not_started 间震荡；test 只是 tick(30s)/progress(300s) 相位上恰好让人看到 learning 的时刻多。default 看起来「完全不动」还叠加了症状②的旧行观感。

### 症状②：4.6（1317）平台已 done，缓存停在 11-04 的 not_started/未解锁

三层叠加：

1. 11-07 晚 1317 播完时 `read_row` 还有「只读当前页」的 P0（1317 在第 5 页/共 6 页）→ progress 回读 `row=None` → 回写空转，缓存行永远停在 11-04；
2. 今天 driver 用 `find_section`（跨页）知道 1317 done，日志打印 `[SKIP] 1317 已达标，跳过` 后 `continue` —— **只有正在播的节**才有人回写缓存，历史节没人纠正；
3. 面板「刷新节列表」对**任务在跑的账号被后端拒绝**（防列表页抢播放页 frame，11-04 踩过）→ 人工也救不回来。

### G-4 三个数字的语义（用户问「具体是什么内容」）

不是矛盾，是**三个阶段的值**，但代码里没有权威来源统一它们：

| 值 | 出处 | 语义 |
|---|---|---|
| 1 | `run_gxks_multi.py` 写死串行，引 D12 | **核实期约束**（PLAN §7.3.1/§882：同出口 IP 多账号同时刷最易被关联，先串行跑稳） |
| 2 | 面板并发闸默认（11-06 加） | 实现时取的中间值（11-07 两账号验收用的） |
| 10 | PLAN §11.6 `max_concurrency=10`（10-29 用户暂定） | **产品目标值**（核实期结束后） |

关键事实：**D12 解除并发的前置（Q7/C6 零风险实验）已于 11-07 实测通过**（3 个不最小化窗口 `hidden=False` 全保持）。PLAN 原文：「并发>1 必须先验掉 Q7/C6」。已向用户提出拍板选项：A（认定已验 → 可配、默认 2）/ B（维持串行 1）/ C（直接 10，不推荐：同 IP 10 会话未验证 + 本机内存，PLAN §11.12 自列风险）。我倾向 A。**等用户拍板，未动代码。**

## 二、修复（A/B/C + T4）

| # | 修复 | 文件 |
|---|---|---|
| A | `update_row`：progress 回读**不许回降 learning**。平台明确 `done`（终态）才覆盖；`not_started`/`learning` 不回降（progress 事件本身证明 driver 还在播这一节） | `sections_cache.py` |
| B | driver 判定 `SKIP 已达标` 时，**顺手把该节平台真值回写缓存**（`find_section` 刚读过，**零额外平台请求**）→ 1317 这类历史节在下次任务启动时被纠正 | `scripts/run_gxks.py` |
| C | learning 记 `learning_at` 心跳（tick 每次刷新）；读方 `app._normalize_stale_learning` 对超 90 秒无心跳的 learning 行按 `done_minutes` 推导降级展示（**只影响 /api/state 展示，不改缓存文件**；无时间戳的老行保持原样） | `sections_cache.py` + `app.py` |
| T4 | `ledger.batch_totals`：账本**只读一次**、单遍按账号聚合「今日 + 近 24h」；`_account_cards` 从每账号 2 次全量 jsonl 读（100 账号 × 4 秒轮询 = 200 次/4 秒）降到 1 次 | `ledger.py` + `app.py` |

**自审抓出的回归**（未出门）：playing 块第一版写成 `if playing:`，`playing=None`（SKIP 回写不传 playing）会被 `elif not playing` 当 False 误撤回 learning —— 原语义是 None=不碰标记。已改回 `playing is True` / `playing is False` 严格判断，并补守护测试 `test_playing_none_leaves_learning_untouched`。

## 三、验证

- 新增 `tests/test_gxks_sections_stable.py` 13 条（不回降 / done 终态覆盖 / 撤回语义保留 / playing=None 不动 / 心跳写入与清除 / 读方降级四态 / SKIP 回写纠正 + source 守护）
- 新增 `tests/test_gxks_ledger_batch.py` 4 条（batch 与单账号版逐字段一致 / 空与缺文件 / 坏行 / 去重键含课程）
- 相关 23 条 + **全量 `pytest` 1239 passed 零回归**
- **离线端到端**（真实缓存副本 + 修复后代码，模拟 tick→progress→tick / SKIP 回写 / done 终态 / 心跳超时四场景）全过；诊断脚本 `data/debug/diag_verify_20261108_display.py` 用完即删

## 四、生效条件（⚠ 运行中的进程都加载着旧代码）

- 修复 A/B（写方，在 `run_gxks.py`/`sections_cache.py`）→ **下次任务启动**生效
- 修复 C 读方（`/api/state` 归一化）与 T4 → **面板重启**生效
- 4.6 的立即纠正路径：**停止该账号任务 → 点「刷新节列表」**（任务不在跑，后端不再拒绝，`gxks_sections` 重读平台列表）；或等下次任务启动时 SKIP 回写自动纠正

## 五、交互设计（T5/T6，用户要求主动推进）

方向在 devlog `2026-11-08-01` §三已定（「账号 × 课程」二维、列表唯一全量视图、详情按需加载、日志合并流）。本轮先落 T4 地基；具体交互方案（迷你卡只显示在跑的、点账号行按需加载节列表、日志合并流带 `[账号·课程]` 前缀 + 过滤下拉）已向用户提出，**等确认后落地**——按「确认后再改代码」约定不动手。

「不要老是我提醒」已记取：交互方向既定的待办（T5/T6/T7）由 AI 主动推进到「方案待确认」这一步，不再等用户开口。

## 六、改动的文件

- `src/xxt/adapters/gxks/sections_cache.py`（修复 A + C 写方）
- `src/xxt/panel_gxks/app.py`（修复 C 读方 `_normalize_stale_learning` + T4 卡片改 batch）
- `scripts/run_gxks.py`（修复 B：SKIP 回写）
- `src/xxt/adapters/gxks/ledger.py`（T4：`batch_totals`）
- `tests/test_gxks_sections_stable.py`（新，13 条）
- `tests/test_gxks_ledger_batch.py`（新，4 条）
- `tests/test_gxks_recent24.py`（源码断言随 T4 更新）

## 交接块

- **进行中**：无（代码全部落地，`pytest` 1239 全绿；待用户重启面板 + 任务后实机验收）
- **下一步第一个动作**：用户在自己的 PowerShell 窗口重启面板（`Ctrl+C` → `.venv\Scripts\xxt.exe gxks-panel` → 确认 `markers=CRLS` → 浏览器 `Ctrl+F5`），并重启两个账号的刷课任务 → 验收：① 正在播的节**稳定**显示「学习中」（不再闪烁）；② 1317 在下次任务启动时被 SKIP 回写纠正（或停任务后点「刷新节列表」立即纠正）；③ 中断任务 90 秒后该节不卡「学习中」
- **阻塞点**：无技术阻塞。**待用户**：G-4 并发口径拍板（A/B/C）；T5/T6 交互方案确认；G-5 练习 AI 回填实跑（此前一直漏掉的线）
