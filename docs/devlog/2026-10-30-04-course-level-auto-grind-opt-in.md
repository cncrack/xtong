# 2026-10-30-04 课程级自动刷 opt-in（抓树不再被偷刷）

## 本次目标与结果

**目标**：修复用户实测发现的 bug——「我抓到我需要课程的章节，但我没有说要刷这个课程，调度器就自动把这门课程启动来进行刷课了」。

**结果**：✅ 成功落盘，全量 pytest **314 passed**（基线 310 + 本批新增 4）。

## 根因

`rotation.course_weights` 的候选判据过宽：只要课程 `total>0 且 pending>0` 就进轮转池。抓章节树会写入 pending 行 → 课程立刻被守护轮转选中 → 自动开刷。全程没有「用户是否要刷这门课」的显式入口。守护选课路径：`cli.py` 无学习安排时走 `pick_courses`（即 `course_weights`）→ 命中即 spawn。

## 关键决策与方案（方案 A：课程级 opt-in）

用户在设计讨论中拍板三点：

1. **默认全关**：新列 `auto_grind INTEGER NOT NULL DEFAULT 0`；老库已有的课迁移后一律回到「未被自动刷」保守态（不猜测用户意图）。
2. **多选池、串行轮刷**：可同时纳入多门课，但守护同一时刻只刷一门（尊重既有单会话锁，防双开/风控）；今天刷 A、明天刷 B 自然轮转，不要求先刷完一门。
3. **面板四分区显示**：自动刷队列以列表平铺，未纳入/已学完各自折叠。

手动「▶ 刷这门课」不受 `auto_grind` 约束（没勾选的课照样能手动刷一次）。有启用中的「学习安排」时仍按安排走（排课本身即显式意图，不经轮转过滤）。

## 改动文件清单

- `src/xxt/store/db.py`：`SCHEMA_VERSION 9→10`；`courses` 建表加 `auto_grind` 列 + `connect()` 幂等 `_ensure_col` 补列。
- `src/xxt/core/rotation.py`：`course_weights` 候选 SQL 加 `AND auto_grind = 1`，重写 docstring 说明 opt-in 语义。
- `src/xxt/panel/app.py`：
  - overview 课程 SQL 加 `c.auto_grind`；`_find_course_row` 返回 `auto_grind`。
  - 新增端点 `POST /api/course/auto-grind`（开/关、写 `config_audit` 留痕、同值幂等、`_log_event` 可见）。
  - 前端 `mkCourse` 每行加「🔄 自动刷 开/关」开关（无树的课不显示）；`renderAccounts` 课程区重写为四分区（①正在刷 ②自动刷队列·N门平铺 ③其他·未纳入 折叠 ④已学完 折叠）；新增 JS `setAutoGrind()` 调端点后 `syncRun()`。
- `src/xxt/cli.py`：守护无可自动刷课程时的 `auto-skip` 文案改为用户可照做的引导（原「先抓课程/章节」不再准确）。
- `tests/test_rotation.py`：夹具两门课置 `auto_grind=1`；新增 `test_course_not_auto_grind_is_excluded`（未纳入排除、开启后进池）。
- `tests/test_panel.py`：新增 `AutoGrindToggleTest`（默认不进池 / 开关端点联动 course_weights / 审计留痕与幂等 / 未知课报错）。JS 语法守卫 `node --check` 覆盖新前端。

## 交接块

- **进行中**：本批（auto_grind）+ 会话72 前两批（A/B/C/D 误报三连 + hotfix/徽章）均在工作区**未提交**，等用户「提交」口令。
- **下一步第一个动作**：用户「提交」→ devlog/STATUS/PLAN 已同步，定向 `git add` 本会话文件 → commit；「推送」→ 先跑 `scripts/push_check.py` 全绿再 push。
- **等用户实机验证**：① 重启面板后课程行出现「🔄 自动刷」开关，默认「关」；② 只抓章节树不再被守护自动开刷；③ 手动点开关「开」后，到点守护才轮刷该课；④ 多门同开时面板「⏭ 自动刷队列 · N 门待轮」平铺、同刻只刷一门。
- **阻塞点**：无。老库迁移靠 `_ensure_col` 幂等补列，首次连库自动完成。
