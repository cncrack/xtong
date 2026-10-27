# 会话 57：auto-error 语义拆解 + 守护策略独立成页 + 随机退避 + 配置变更审计

日期：2026-10-27（下午）
前置：`2a08144`（配置区 10 控件 + visited 回补 + 门禁扫 staged）

## 一、用户问题与结论

### 1. 「为什么是 auto-error?」

用户看到 `2026-10-27T14:03:56 #1 auto-error 已确认开跑但会话已退出…` 困惑：明明没发起新尝试，怎么报错？

**查实：这条来自 `_revive_if_dead()`，语义是"撤回今日已学"，不是"本轮失败"。**
旧代码把两件事写成同一个事件名，排查时必然误读。

修法：
- `_revive_if_dead` 改写 `auto-revoke`，文案改为「撤回「今日已学」：上次发起后会话已退出…」
- `_replay_auto_events` 新增 `auto-revoke` 分支：**无论隔多久都撤销**（`auto-error` 仍保留 5 分钟窗口用于兼容历史数据）
- 面板日志筛选条加 `auto-revoke`（按 WARNING 着色）

### 2. 「守护与节级策略这个单独做成另一个标签页不是更好吗，排在上面」

`⚙ 全局设置` 从 2 个 tab 变 3 个，顺序：
**⏱ 守护与节级策略（默认打开） → 📚 学习与作业 → 🤖 AI 模型**

守护页按语义分三组：「发起与回读」/「失败怎么退避」/「单节怎么处理」，共 12 个控件；
底部新增「🕘 最近配置变更」。`switchTab()` 改为 `[0,1,2]` 通用实现。

顺手修正一处误导文案：`每日时长额度` 的说明原写「按会话在场时长计（含拟人休息）」，与 2026-10-27 改动后的真实口径相反，改为「按本会话真播完并记账的视频时长计」。

### 3. 「可以设置随机，模拟人的行为，要弄成配置项可以设置需要的次数」

退避从"固定 15 分钟"升级为**指数 + 随机抖动 + 封顶**：

```python
def _backoff_minutes(base: int, n_fail: int, jitter_pct: int, cap: int) -> int:
    wait = max(1, base) * (2 ** max(0, min(n_fail, 6) - 1))
    if jitter_pct > 0:
        wait *= 1 + random.uniform(-jitter_pct, jitter_pct) / 100.0
    return max(1, min(cap or 480, int(round(wait))))
```

新增两个配置项（`[auto]`，面板可改，范围校验）：

| 键 | 默认 | 范围 | 含义 |
|---|---|---|---|
| `retry_jitter_pct` | 40 | 0~100 | ±这个比例内随机（base=15 时第一次落在 9~21 分钟） |
| `retry_max_minutes` | 90 | 15~480 | 封顶，再糟也不会超过 |

`_auto_limits()` 返回值从 3 元组变 5 元组，`auto-start` 横幅同步打印「退避 15 分钟（±40% 随机，封顶 90 分钟）」。

### 4. 「每次调整都要记录清楚，信息，方便查阅和调整」

新增 `config_audit` 表（`store/db.py`，幂等建表）：

```sql
CREATE TABLE IF NOT EXISTS config_audit(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL, source TEXT NOT NULL,
  key TEXT NOT NULL, old_value TEXT, new_value TEXT
);
```

- `PUT /api/settings` 的 `upsert()` 改为带审计：**值没变不写**（否则每点一次保存都刷一条无用记录）
- `GET /api/settings` 返回 `recent_changes`（最近 12 条）
- 面板守护页底部渲染为「时间 键名 旧值 → 新值」，18 个键都有中文名

## 二、实况：#1 第 3 次尝试成功

14:03:56 用户「停调度→启调度」，守护 pid 14716 重启，`max_tries=3` 生效。

```
14:10:27  auto-try       第 3/3 次发起
14:10:57  auto-fire      已确认开跑
14:12:31  进入知识点：第三章｜4.5 增强自我信心（上）
14:20:36  video-done     增强自我信心（上）｜470s  ended=True
14:26:38  video-done     增强自我信心（下）｜379s
```

**B1（`_frame_alive` 探活）首次真实验证通过**：没有再出现 `Frame was detached`，播完一节能顺利进下一节。
上午 11:17 那次失败跑的是 11:59 才提交的修复之前的代码（见下"踩坑 1"）。

## 三、验证

- `pytest` **206 passed**（新增：`TestBackoffMinutes` 4 例、`auto-revoke` 回放 2 例、面板审计 1 例；`TestAutoLimits` 断言随 5 元组更新）
- 面板重启后（pid 4712 → 新）端到端：
  - 12 个控件全部存在，`⏱ 守护与节级策略` 在 `📚 学习与作业` 之前
  - `GET` 返回 `auto_retry_jitter_pct=40`、`auto_retry_max_minutes=90`
  - 改 jitter=60 → 审计写入 `old_value=''`（首次，无旧值）→ `new_value='60'`
  - 越界 200 → `{"ok":false,"error":"auto_retry_jitter_pct 需在 0~100 之间"}`

## 四、踩坑

1. **差点误判 B1 失效**：11:17 日志里连续 `Frame was detached`，当前源码已有探活修复，于是判定"修复没生效"；
   查 `git log -S _frame_alive` 才发现修复 **11:59 才提交**、故障 **11:17 产生**——跑的是旧代码。
   **教训：判"修复无效"前必须先核提交时间 vs 日志时间。**（已存为长期经验）
2. PowerShell `python -c "...'id=\"x\"'..."` 引号又被吃掉 → 一律写 `data/debug/*.py`。
3. 我假设存在 `works` 表，实际是 `work_questions` → 查库前先列表。
4. `SearchReplace` 插入函数时缩进写成 3 空格触发 IDE 报错，但 `py_compile` 通过说明位置正确——**IDE 的 lint 提示可能是缓存，以编译器为准**。

## 五、改动文件

- `src/xxt/cli.py` — `auto-revoke` 事件 + `_auto_limits` 5 元组 + `_backoff_minutes` + 两处退避调用
- `src/xxt/config.py` — `DEFAULTS["auto"]` 五项（原来只有 toml 里有）
- `config/settings.toml` — `retry_jitter_pct`、`retry_max_minutes`
- `src/xxt/store/db.py` — `config_audit` 表
- `src/xxt/panel/app.py` — 3 tab 布局 + 2 控件 + 审计写入/读取/渲染 + 日志筛选
- `tests/test_auto_limits.py`、`tests/test_auto_replay.py`、`tests/test_panel.py` — +7 例

## 交接块

- **进行中**：#1 正在学习会话中（14:10 起，额度 196 分钟 / 57 个），预计 16:00 前后收工
- **下一步第一个动作**：#1 收工后读 `sub-mlearn-20261027-141027.log` 末尾，确认 B3（中断续播）是否被触发、额度过期口径是否打印「本会话视频 X 分钟」
- **阻塞点**：Q19（目标优先级三层状态机）、Q20（打不开原因+入口）仍是「方案已出，待拍板」
