# 25 · 账号级额度核查：面板链路正确，CLI `xxt learn` 漏了覆盖

日期：2026-10-25 22:40~23:00
触发：用户「你上次测试的是全局配额，那每个账号管理里面的配额如果设置了，是否也生效？与全局的关系与优先级是否正确」

## 一、核查结论

| 入口 | 是否采用账号级额度/窗口 | 说明 |
|---|---|---|
| 面板「开始学习」/ `mlearn` / `auto` | ✅ 正确 | `run_accounts()` 为每个账号构造 runner 时调 `effective_pace/effective_quota` |
| 面板卡片显示"今日 x / 上限 y" | ✅ 正确 | `overview()` 用 `effective_quota`，并标"（全局）/（账号）"来源 |
| CLI `xxt learn`（同步版） | ❌ **漏了**（本次修复） | `StudyRunner` 只吃 `settings.quota/pace`，账号管理里的额度与时段窗口完全被忽略 |

优先级语义（正确，有测试覆盖 `tests/test_accounts.py`）：
- 账号级 `daily_max_minutes` / `daily_max_tasks` **非 0** → 覆盖全局 `[quota]` 对应项；
- 为 **0 或 NULL** → 跟随全局（面板 ✎ 文案「0=用全局默认」一致）；
- 两项独立判断，可以只覆盖其中一项（例如只给某号限 90 分钟、任务数仍跟随全局）；
- 合并后仍走统一链路：`sample_daily_budget` 浮动抽样 → 按当日 `run_logs` 扣减 → 任一归零即拒绝启动。

## 二、本次修复

`StudyRunner.__init__` 增加 `quota` / `pace` 参数（与 `AsyncStudyRunner` 对齐，不传则回落 `settings`），`sample_daily_budget` 改读 `self.quota`；`cli.py::cmd_learn` 按 #1 的账号行取 `effective_quota/effective_pace` 传入——`xxt learn` 固定用 `accounts/1/state.json`，因此对应的正是 1 号账号的设置。

新增测试 `TestRunnerQuotaOverride`：显式传入时生效、不传时回落全局。`pytest` 49 passed。

## 三、文件清单

- `src/xxt/adapters/chaoxing/study.py`：`__init__` 加 `quota/pace`，额度判定改读 `self.quota`
- `src/xxt/cli.py`：`cmd_learn` 传 `effective_*`（#1）
- `tests/test_accounts.py`：新增 `TestRunnerQuotaOverride`

## 四、交接块

- **进行中**：无
- **下一步第一个动作**：端到端实测账号级额度（面板路径）——把 #1 的 `daily_max_tasks` 设为 1（全局 60），点开始，预期"任务数额度达标（今日已完成 2 个）"且零平台访问；测完改回 0
- **阻塞点**：无
