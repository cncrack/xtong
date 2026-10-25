# 会话 37：批次课程按平台课程ID锚定 + 弹窗可缩放/列宽优化

> 日期：2026-10-26（序号 09）
> 上承：`2026-10-26-08-profile-table-and-platform-fields.md`

## 1. 本次目标与结果

用户提出两件事：①**课程名歧义**——"不同年级/专业的学生课名可能一样但内容不同"，问批次里课程名怎么确定/选择；②**弹窗太小、不可调整、部分列过宽**。方案 A（平台课程ID锚定，含窗口优化）经确认后实现。

| # | 需求 | 处理 | 结果 |
|---|---|---|---|
| 1 | 课程名歧义（同名不同内容） | 批次新增 `course_pid`（平台课程ID）；面板课程改**列表选择器**（按 pid 聚合，显示覆盖账号数 + pid）；运行按 pid 精确匹配 | ✅ |
| 2 | 同名多门 / 跨账号缺课不可见 | 选择器每项标"N 个账号有此课"；选定后提示"本批次 M 个账号缺课：#x" | ✅ |
| 3 | 弹窗太小、无法调整 | 弹窗加 `resize:both` 右下角可拖拽；`#planDlg`/`#pickDlg` 分别加宽到 840/1180 | ✅ |
| 4 | 部分列过宽、挤占空间 | 选择器表格列宽逐列调窄、表头缩短（备注名→备注、平台昵称→昵称）；修掉表格撑破 grid 容器 | ✅ |
| 5 | 老批次（仅课名）兼容 | `course_pid` 为空仍按课名匹配；批次行加"建议补选课程ID"提示 | ✅（用户选"自动兼容+提示"） |

## 2. 关键决策

1. **课程身份用平台ID（pid），不用课名**：读码发现运行时链路是"面板 POST 课名 → 子进程 `xxt mlearn <课名>` → `_pick_course` 在**账号#1**按 `pid=? OR name LIKE %名%` **LIMIT 1** 选种子 → `run_accounts` 再按 pid 逐账号匹配"。据此定位三个真实风险：
   - **R1**：种子只从账号#1取——批次含 #2/#3 且该课不在 #1 时，`_pick_course` 返回 None → 整轮失败；
   - **R2**：同账号同名多门——`name LIKE + LIMIT 1` 可能选错（命中用户担心的"同名不同内容"）；
   - **R3**：跨账号同名不同 pid——被静默跳过（不刷错，但像 bug）。
   选 pid 锚定一次性根治 R1/R2/R3，符合"课程锚定必须准、错了排查贵"的用户诉求。
2. **画像参数 ≠ 课程锚定**：向用户澄清——画像（姓名/学号/年级/专业/学校/院系/班级）只做登记/统计分组，**不参与"刷哪门课"的匹配**；课程锚定错了才影响"做"。二者解耦，降低焦虑。
3. **防错四原则**：① pid 直接来自平台课程列表（抓课程时已入库，非手抄）② 选择器"覆盖账号数/缺课账号"动手前即可见 ③ 运行前按 pid 预检、缺课账号明确报出（`该账号无此课程（pid=…）`）不进会话 ④ **绝不隐式兜底**（匹配不到就停，不"猜一门最像的"）。
4. **兼容策略**：老批次无 pid → 仍按课名匹配 + 提示补选；不阻断已排批次（用户选"自动兼容+提示"）。
5. **列宽/缩放**：`dialog{resize:both}` + `#pickDlg` 加宽；`#pk_list` 由 `.paccs`(grid) 改回 `display:block;overflow:auto`（原 grid 会被 13 列表格撑破）；`.ptable.pick` 逐列 `width`。

## 3. 改动文件清单

- `src/xxt/store/db.py`
  - `plans` CREATE TABLE 增 `course_pid TEXT`；迁移补列。
  - 新增 `_ensure_col()` 幂等补列助手：**修并发竞态**（面板每请求各开连接，首次触达新列时多连接同时 ALTER → `duplicate column name`），把"并发已加"视为成功。
- `src/xxt/core/plans.py`：`list_plans`/`create_plan`/`update_plan` 纳入 `course_pid`（空串=清空）；`course_for_account` 返回类型 `str|None` → **`{"name","pid"}|None`**。
- `src/xxt/cli.py`
  - 新增 `_pick_course_in(conn, *, account_ids, name, pid)`：在**给定账号集合**里按 pid 精确 → 课名全等 → 模糊。
  - `cmd_mlearn`：**先定账号再解析课程**（修 R1），用 `_pick_course_in`；新增 `--course-pid`；打印匹配到的课程与 pid。
  - `auto` 守护：`course_for_account` 改为 dict，POST `/api/study/start` 透传 `course_pid`。
- `src/xxt/adapters/chaoxing/astudy.py`：`run_accounts` 有 pid 按 pid 匹配、无 pid 退回课名全等；缺课 `stop_reason` 带 pid/课名与"请核对或重新选课"。
- `src/xxt/panel/app.py`
  - overview `courses` 增发 `platform_course_id`/`clazz_id`。
  - `/api/plans`、`/api/plans/{id}/update` 接收 `course_pid`；`/api/study/start` 接收并透传 `course_pid`。
  - HTML：`pl_course` 由 `<input list>` 改 `<select>`（+`#pl_cov` 覆盖提示），删除 `<datalist>`。
  - JS：`buildCourseOptions`（按 pid 聚合下拉）、`updateCourseCov`（缺课提示）、`openPlan`（按 pid 回选 + 老批次占位项）、`doPlan`（送 `course_pid`）、`renderPlans`（老批次"建议补选课程ID"徽标）、`startGrind`/卡片 ▶ 透传 pid。
  - CSS：`dialog{resize:both;max-width:96vw;max-height:88vh;min-*}`；`#planDlg`/`#pickDlg` 加宽；`#pk_list` 改块级滚动；`.ptable.pick` 逐列列宽并缩短表头。
- `tests/test_plans.py`：`course_for_account` 断言改 dict 契约；新增 `test_course_pid_anchor`、`test_update_plan_sets_and_clears_pid`。
- `tests/test_panel.py`：新增 `test_plan_course_pid_roundtrip`、`test_legacy_plan_without_pid_still_creates`、`test_plan_update_sets_course_pid`、`test_overview_courses_include_platform_course_id`、`test_course_for_account_returns_pid`。
- `data/debug/diag_ui5.py`：临时诊断（data/ 不入库）。

## 4. 验证记录

- `py_compile`（db/plans/cli/astudy/app）→ exit 0；`check_js.py` → node --check exit 0
- `pytest -q` → **104 passed**
- 面板重启（`python -m xxt.cli panel`）→ 无 traceback
- `data/debug/diag_ui5.py`（headless 实渲染）：
  - `pl_course` = `SELECT`；选项含"（请选择课程）"+ 真实课程（形如`《知行合一，做最好的自己》 · 2 个账号有此课 · pid=266339100`）✔
  - 选课后覆盖提示："课程《…》：尚未选择账号" ✔
  - `getComputedStyle(planDlg).resize = both` ✔
  - 选择器表头：选/账号/备注/姓名/昵称/学号/年级/专业/学期/学校/院系/班级/状态 ✔
  - `#pk_list` display = block；`#pickDlg` width = 1180 ✔
  - **pageerror = 0** ✔
- 现网 DB `plans` 已含 `course_pid` 列 ✔

## 5. 踩坑与风险

1. **迁移并发竞态**：面板线程池并发首触新列 → `ALTER TABLE plans ADD COLUMN course_pid` 抛 `duplicate column name: course_pid`（另一连接已加并提交）。已用 `_ensure_col`（捕获该错视为成功）修掉，并对所有补列路径统一改用该助手。
2. **契约变更涟漪**：`course_for_account` 返回值由 `str` 改 dict，`tests/test_plans.py` 4 处旧断言失败——已同步为新契约（这是"改了共享函数必须全量回归"的实例）。
3. **老批次跨账号同名不同 pid 仍可能跳过**：这是 R3 的固有语义（不刷错、但会跳），靠"建议补选课程ID"提示引导用户重选，非静默。
4. 未做：批量平台读取、加密录入后自动串联（用户要求后续再议）。

## 6. 交接块

- **进行中**：✅ 无
- **下一步第一个动作**：用户 `Ctrl+F5` →「📚 批次 → ＋新建批次」看**课程下拉**（每项带"N 个账号有此课 · pid="，同名不同班分开列）；选课 + 选账号后看 `#pl_cov` 的"缺课：#x"提示；拖右下拉大 `planDlg` / 打开「＋选择账号」看 `pickDlg` 加宽与列宽。
- **阻塞点**：无
- **等用户**：① 新课程选择器观感（是否易选对）② 老批次"建议补选课程ID"是否需要一键补 ③ 白天实测：批次错峰 / 平台画像读取 / 作业判分校准 ④ 弹题选择器实况 ⑤ ≥3 账号并发