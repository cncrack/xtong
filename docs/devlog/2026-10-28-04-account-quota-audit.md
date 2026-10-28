# 2026-10-28-04 账号级额度改动补审计（Q41）

## 一、目标与结果

**目标**：用户实测发现「改了账号卡片上的额度/任务数，日志和『最近配置变更』里没有任何记录」，
问「修改的记录在日志当中没有体现，是吗？」——确认属实后要求补审计。

**结果**：✅ 已完成。面板保存账号时把变化写进 `config_audit`（key 形如
`account.2.daily_max_minutes`），「最近配置变更」展示时翻成「账号#2 · 每日时长(分钟)」。
测试 **256 全绿**（新增 1 条）。

## 二、根因

`update_account_api` → `core.accounts.set_overrides` 只 `UPDATE accounts`，
**从未写过 `config_audit`**。而「最近配置变更」读的就是 `config_audit`，
此前只有全局项（`put_settings` 里的 `upsert`）往里写 → 账号级改动天然不可见。

**不是配置没生效，是没有留痕。**

## 三、改动

| 文件 | 改动 |
|---|---|
| `src/xxt/panel/app.py` | `update_account_api`：写库前逐字段比对新旧值，变了才写 `config_audit`；`status` 单独记一条。同值不记，避免每点一次保存刷一条无意义记录 |
| `src/xxt/panel/app.py` | `_recent_changes`：把 `account.<id>.<col>` 翻成 `账号#<id> · <中文字段名>`；非账号 key 原样返回（不影响现有 `auto.max.tries` 的断言） |
| `tests/test_panel.py` | 新增 `test_account_quota_change_writes_audit`：首次 120 → `old=""`；同值不刷；改 100 → `old="120"`；任务数同样留痕 |

字段中文名映射（展示层）：每日时长(分钟)/每日任务数/定时开始/学习窗口起/止/
作业策略/优先级/状态/别名/备注/代理/有效期至。

## 四、验证

- `pytest tests/test_panel.py -q` → **36 passed**
- `pytest -q` → **256 passed**

## 五、踩坑

1. 测试里直接 `INSERT INTO accounts(id=1, ...)` 报 `UNIQUE constraint failed` ——
   `connect()` 初始化会 seed 一个 id=1 的 default 账号。改成 `INSERT OR IGNORE` +
   `UPDATE ... SET daily_max_minutes=NULL` 把它拉回「未设覆盖」的初始态。

## 六、未做

- **旧记录追不回**：今天 08:5x 用户把 #1 设成 120 分钟/3 个、#2 设成 100 分钟/4 个时，
  面板跑的还是旧代码，这两次没有审计记录（结果本身已由 `accounts` 表现值佐证）。
- `note`/`label` 这类长文本会整段入库；目前量小，不做截断。

## 七、交接块

- **进行中**：无，已闭环。
- **下一步第一个动作**：**重启面板**后才生效（改的是 `panel/app.py`）；
  守护（`xxt auto`）是独立进程，不受面板重启影响。
- **阻塞点**：无。
