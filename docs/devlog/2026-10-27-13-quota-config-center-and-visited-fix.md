# 会话 55-56：额度口径改为"只看看过的视频" + 守护/节级策略全部搬上面板 + visited 一致性回补

日期：2026-10-27（下午）
前置：会话 54 交接收尾（`7eb0b79`、`d5b0a68`、`cda4c03..d5b0a68` 已推送）

## 一、本次目标

用户三连问推到这一步：

> 「前面不是说要弄成配置项可修改吗，全局默认是 3 还是多少，可以设置默认值……是否可以考虑把看过的视频累加在一起，中间其它模拟人的就不要算在内。从早上的时间来推算也没有 240 分钟呀」
> 「这样子太乱了，把前面所有疑问的和未解决的问题重新整理……你打算什么时候落实，不能开工吗　你不做成配置项我怎么改。　改。」

落成三件事：
1. **额度口径改造**：只累加"真播完并记账"的视频秒数（会话 55，提交 `0fa944a`）
2. **配置项落地到面板**：`[learn]/[auto]` 全部搬上 UI，可改可校验（会话 56）
3. **visited 一致性回补**：按"有没有记账"把误标的两节归位（会话 56）

## 二、额度口径：为什么会是 186 分钟而不是 105

| 数字 | 含义 | 出处 |
|---|---|---|
| 240 | `[quota] daily_minutes` 上限 | `settings.toml` |
| 174 | 今日实际额度 = 240 × 0.725（`float_ratio` 随机） | 日志 `当日剩余额度：174 分钟 / 45 个` |
| 186 | 会话**墙钟**（08:32:06 → 11:38:46） | 旧判定口径 |
| 105 | 真播完记账的视频内容（13 条 = 6293s） | `run_logs.event='video-done'` |

旧代码**两套口径打架**：入口 `remaining_minutes(budget, done_seconds)` 按视频算，主循环
`(time.monotonic() - study_start)/60 >= budget` 按墙钟算。所以"视频只学了 105 分钟就收工"，
用户看到的却是"才 9 点怎么就没额度了"。

### 改法

新增纯函数（可测、无副作用）：

```python
def quota_reached(session_seconds: float, budget_minutes: float | None) -> bool:
    return bool(budget_minutes) and session_seconds / 60 >= budget_minutes
```

主循环 `played is True` 时 `session_seconds += self._last_video_dur`，与 `stats["videos"] += 1`
同一处记账；停止原因改为「时长额度达标（本会话视频 X 分钟）」，日志同时打印墙钟供对照。
`budget_minutes = 0/None` 仍表示不限。

## 三、配置项：从"能改 toml"到"面板能改"

用户原话：「**必为配置项，前面一直提过。你不做成配置项我怎么改。**」

面板「⚙ 全局设置 → 📚 学习与作业」新增「⏱ 守护与节级策略」分组，10 个控件：

| 控件 | 键 | 范围 | 作用 |
|---|---|---|---|
| 每日最多自动发起 | `auto.max_tries` | 0~10 | 0=不限 |
| 失败退避（分钟） | `auto.retry_minutes` | 1~240 | 防连续敲平台 |
| 开跑回读（秒） | `auto.confirm_seconds` | 15~300 | 确认会话真在跑 |
| 单节点最多点几次 | `learn.enter_retries` | 1~5 | 点不开算本节失败 |
| 连续几节打不开收工 | `learn.max_skip_chapters` | 1~10 | 失败的节保持 pending |
| 连续非视频收工 | `learn.non_video_streak` | 1~20 | 作业/阅读穿过去 |
| 中断续播次数 | `learn.partial_retries` | 0~3 | 0=关闭（B3） |
| 续播门槛（%） | `learn.partial_retry_ratio_pct` | 30~95 | 已播够才值得续 |
| 每日登录预算 | `learn.login_budget_per_day` | 1~5 | 失败尝试也算 |
| 非视频节点关键词 | `learn.non_video_marks` | 文本 | 平台改叫法时改这里 |

实现要点：
- `GET /api/settings` 增加对应 10 个字段（`fresh.raw["auto"]` + `fresh.learn`，读取统一走 `_i()` 容错）
- `PUT /api/settings` 用 `int_rules` 表驱动：**越界直接拒绝并回显原因**（不静默钳制，避免"我明明填了 5 却变成 3"）
- 空关键词允许（回落内置默认）
- 前端 `saveSettings()` 改为先拼 `body` 再逐项塞数值，空输入不提交（避免把空串写成 0）

`[auto] max_tries` 本次**从 2 改为 3**（用户「改。」）：#1 今日 2 次尝试都已用尽、`_revive_plan` 拒绝收回，
14:10 的定时因此不会触发；改 3 并重启守护后才会真跑。**这是测试值，验证完应回落到 2**。

## 四、visited 一致性回补

发现两节状态与实际记账矛盾：

| 账号 | 节 | 原状态 | 证据 | 新状态 |
|---|---|---|---|---|
| #2 | idx=13 `3.1 如何做好人生规划（上）` | visited | 10-25 21:42 有 492s `video-done` | **done** |
| #2 | idx=22 `4.2 正确认识自我（下）` | visited | 今日播 7 分 23 秒中断、无记账 | **pending** |

旧 `mark_entry_visited` 把"看过但没播完"的节一律打 visited（既不算完成也不重学），
P0-11/P0-12 已把语义收窄为"非视频节点穿过"，但历史数据需要一次性归位。

脚本 `data/debug/fix_visited_consistency.py`（不在 git 内）：干跑 → `--apply`（自动备份 `data/xxt.db.bak-*`
+ 写 `run_logs(diag-visited-fix)` 留痕）。节名匹配用 `core_name()` 去掉章前缀与节编号
（`第三章｜4.2 正确认识自我（下）` → `正确认识自我（下）`），按 `account_id` 隔离。

结果：#1 `{done:26, pending:43, visited:1}`（作业）、#2 `{done:34, pending:35, visited:1}`（作业）。

## 五、验证

- `pytest` **199 passed**（新增 `TestStrategySettings::test_strategy_settings_roundtrip` / `test_strategy_settings_reject_out_of_range`）
- 面板重启后（pid 30076 → 新进程）端到端：页面 10 个控件全部存在；`GET` 返回 `max_tries=3`、
  `non_video_marks=作业,测验,讨论,阅读,考试,练习,公告`；合法 `PUT {"learn_non_video_streak":4}` → `ok`；
  越界 `{"auto_max_tries":99}` → `{"ok":false,"error":"auto_max_tries 需在 0~10 之间"}`；
  类型错 `{"auto_max_tries":"3"}` → `{"ok":false,"error":"auto_max_tries 需为整数"}`

## 六、踩坑

1. PowerShell 里 `-Pattern "a|b\"c\""` 双层引号会炸（`SelectString: 找不到接受实际参数…`）→ 改用 Grep 工具。
2. `python -c "…'id=\"s_mt\"'…"` 同样被 PowerShell 吃掉转义 → 一律写 `data/debug/*.py` 再执行（本项目老坑）。
3. 面板 `app_config` 一旦写入某键，**从此覆盖 `settings.toml`**（三层配置第 2 层）。本次端到端验证写了
   `learn.non_video_streak=4`（与 toml 同值，无副作用），但以后改 toml 前要先确认 DB 里有没有这个键。
4. `Settings` 类没有 `self.auto` 属性（`[auto]` 只经 `s.raw["auto"]` 暴露）——面板 GET 必须走 `raw`。

## 七、改动文件

- `src/xxt/adapters/chaoxing/astudy.py` — `quota_reached()`、`non_video_marks()`、主循环记账口径（会话 55）
- `src/xxt/config.py`、`config/settings.toml` — `[learn] non_video_marks`；`[auto] max_tries 2→3`
- `src/xxt/panel/app.py` — 配置区 10 控件 + `GET/PUT /api/settings` 扩字段与范围校验
- `tests/test_astudy_policy.py`、`tests/test_panel.py` — +8 用例
- `STATUS.md` — 待办表更新 + 未决疑问台账
- `data/debug/fix_visited_consistency.py`、`q_panel_settings.py`（不入库）

## 交接块

- **进行中**：无代码半成品；`[auto] max_tries=3` 为**临时测试值**（验证完回落 2）
- **下一步第一个动作**：用户在面板「停调度」→「启调度」，让 14:10 的 #1 真跑（守护只在启动时读一次 `max_tries`）
- **阻塞点**：① #1 的 B1（frame 探活）/B3（续播）只能靠这次实跑验证 ② 弹题、作业判分链路仍等实况样本
