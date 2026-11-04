# 2026-11-05-01 复核审查：11-04 改动的 5 个隐患

> 用户 2026-11-05 开工要求：「把今天的修改做下重新复核审查」。
> 审查范围：`ee4ff86..HEAD`（11-04 全部改动，15 个文件 / 1190 行）。
> 结论：**功能方向都对，但 5 处细节没兜住**，全部已修。

## 审查方法

1. 逐文件读 diff（`git diff ee4ff86..HEAD -- <file>`）
2. 对每处改动问三个问题：
   - **边界条件**：空/None/跨页/换文件/类型不一致时会怎样？
   - **触发条件**：这个分支真的会被执行到吗？（还是永远走不到）
   - **数据一致性**：后端与前端、内存与磁盘的键/类型/口径是否一致？
3. 用真实数据验证推断（查节列表缓存的 `page` 字段、日志时间戳等）

## 发现的 5 个问题

### 问题 1（严重）：`tick` 心跳**永远不会发出**

**位置**：`src/xxt/adapters/gxks/driver.py` `watch()`

**原实现**：

```python
if last_clock is None or clock != last_clock:
    last_clock = clock
    last_tick_at = now        # ← 每次采样都刷新
elif now - last_tick_at >= 30:
    emit("tick", ...)         # ← 永远到不了
```

**为什么错**：`m_time`（页面计时器）**每秒都在涨**，所以每次采样都命中「变了」分支
→ `last_tick_at` 一直被刷新 → `elif` 永不成立 → **心跳一次都发不出来**。

用户要的「每 30 秒显示在走」完全没实现（且这个错很隐蔽：代码看着像对的）。

**修法**：只按**时间间隔**发；`clock` 仅用于判断「是不是真的在走」
（计时器不动 = 视频停了，不发心跳，留给 `no_report`/`stalled` 报）。

### 问题 2：SSE 换日志文件后**跳过开头**

**位置**：`src/xxt/panel_gxks/app.py` `_watch_logs_loop()`

**原实现**：只记 `last_size[key]`，用 `f.seek(prev)` 读增量。

**为什么错**：日志文件名带启动时刻（`gxks-study-default-20261104-115058.log`），
同一账号同一 `kind` **重新起任务会生成新文件**，但 `_JOBS` 的 key（`label|kind`）不变。
于是 `seek(旧文件大小)` 会**跳过新文件的开头**（新文件更短时甚至读到空）→ 日志丢行。

**修法**：指纹里带 `path`，`last_path[key] != info["path"]` 时把偏移归零、从头推。

### 问题 3：前端日志**按 label 分流，后端按 label|kind**

**位置**：`src/xxt/panel_gxks/page.py`

**为什么错**：11-04 修好了后端 `_JOBS` 的键（`label|kind`），SSE 也按
`label|kind` 分流推送，但**前端 `appendLogLines(label, text)` 把 `kind` 丢了** →
同一账号的 `study` 与 `sections` 两个任务日志**混在一起**。

**修法**：前端也引入 `logKey(label, kind)`；新增 `pickLogJob()`
按「在跑优先、`study` > `work` > `sections`」挑当前显示哪个任务。

### 问题 4：练理解锁收尾重读用 `read_row` → **跨页的节读不到**

**位置**：`scripts/run_gxks.py` 收尾段

**为什么错**：`read_row(sid)` 只读**当前页**。而收尾时要重读的节**可能跨页**：

| 节 | 页 |
|---|---|
| 1306 3.3 中国古代军事思想 | **第 2 页** |
| 1307 3.4 当代中国军事思想 | **第 3 页** |
| 1308 3.5 攻坚（一） | 第 3 页 |

若本次刷了 1306 和 1307，收尾时列表停在第 3 页 → 读 1306 返回 `None` →
静默打印「读不到（跳过）」→ **练理解锁状态没同步**。

**修法**：改用 `drv.find_section([sid])`（逐页找）。

### 问题 5：`exam_id` 类型不一致导致白写文件

**位置**：`src/xxt/adapters/gxks/sections_cache.py` `update_row()`

**为什么错**：回读的行里 `exam_id` 是 `int`，命令行参数传的可能是 `str`；
`r.get("exam_id") != exam_id` 会把「`30` vs `"30"`」当成变了 → 白写一次文件
（原子写 + `os.replace`，每次都多一次 I/O）。

**修法**：统一按 `str()` 比较。

## 已确认**没问题**的部分

| 改动 | 复核结论 |
|---|---|
| `stalled → reload` | 与 `rebuild` 共用冷却/次数上限，不会重载风暴 ✅ |
| `ended` 累计总闸（660s） | 大于「等待期 240s + 补播窗口 300s」 ✅ |
| `_JOBS` 键改 `label\|kind` | 所有读写点（`_spawn_script`/`_jobs_snapshot`/`_stop_one`/`_log_signature`）都已跟上 ✅ |
| `_stop_one` 停所有在跑任务 | 用捕获的 `job["proc"]` 杀，不会误杀新任务 ✅ |
| `CREATE_NO_WINDOW` | 与 `CREATE_NEW_PROCESS_GROUP` 组合正确（`DETACHED_PROCESS` 互斥）✅ |
| `SEC_N` 节数记忆 | 收敛逻辑正确（额度变小时自动钳制）✅ |
| 日志倒序 + `scrollTop` | 贴顶跟随、往下滚不抢，逻辑自洽 ✅ |
| `goto_page` 双向翻页 | `cur < target` 走 next、`cur > target` 走 prev ✅ |

## 测试

新增 `tests/test_gxks_review_20261105.py`（10 例）覆盖问题 1/2/3；
问题 4/5 并入 `tests/test_gxks_practice_unlock.py`。

**`pytest` 1037 全绿。**

## 教训（写进纪律）

> **「改对了」≠「跑得到」。** 每写一个条件分支，都要问：
> **这个分支在真实数据下会被执行到吗？**
>
> 问题 1 就是典型：代码逻辑自洽、看着完全正确，但因为 `m_time` 每秒都变，
> `elif` 分支**永远走不到** —— 功能等于没写。

## 交接块

### 进行中

无。面板/Chrome 都未启动（用户 2026-11-04 晚休息时关的）。

### 下一步第一个动作

**由用户操作**：点「启动并登录」→ 填节数 → 点「学习」。
1308（3.5 攻坚一，20 分钟）会从 0/20 开始，之后自动切 1309、1310。

### 阻塞点

无技术阻塞。

### 等用户

1. **验收 `tick` 心跳**（这次真的会每 30 秒出现一行 `tick 学习中（页面计时 N 秒）`）
2. **验收多任务日志不混流**（同时有 study 和 sections 时，日志应各归各）
3. **待拍板**：多账号日志页签
4. **待做**：面板启动时认领孤儿任务
