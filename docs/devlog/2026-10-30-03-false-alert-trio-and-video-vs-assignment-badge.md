# 2026-10-30-03 会话72下半：面板误报三连（页面打不开 / 配置已重载 / 未归类）+ 已学完徽章拆作业

## 本次目标与结果

**结果：全部成功。** `pytest` **310 全绿（308→310，+2）**。

用户按会话 72 第一批（devlog `2026-10-30-02`：加载中 hotfix + 已学完徽章）重启面板验收后，连续贴出四个「看不懂 / 疑似异常」现象。全部**先只读核查定位根因**、出方案表待用户确认，用户「**都做吧**」（D 选推荐的 (b) 口径）后一并落盘 A/B/C/D 四项修复。**全程属会话 72**，本条为其第二批。

## 一、四个现象与根因（只读核查确认）

1. **「⚠ 页面打不开 · 会自动重试」= 误报**。实为 #1《知行合一》视频已刷完。根因 **A**：`core/diagnose.py` 词表 `structure`（页面打不开）关键词含「**下一节**」，正常学完时 astudy 写收尾「下一节按钮不可点；本地已无待学章节」被 `structure` 抢在 `done` 前命中 → 误判可重试 → 守护对已学完课**反复发起**（违反最小访问原则）。
2. **69/70 = 作业，非漏刷**。那 1 节是「第一章｜2.12 作业」`status='visited'`（作业不是视频，刷视频路过标记，不归刷视频）。根因 **D**：徽章把作业混进分母，「69/70」易被读成「还差一节视频」。
3. **auto-reload「配置已重载」反复（12:51 等）= 无害自激**。根因 **C**：守护自己回写 `app_config` 的 `auto.plan` / `auto.retry_after`（`_persist_retry_after`），`Settings.__init__` 又把它们并进 `raw` → 下轮 `_sched_sig` 见变化 → 守护自检测「配置已重载」（措辞误导，非真外部改动）。
4. **「⚠ 未归类 · 会自动重试」（12:19）= 硬崩归类缺口**。根因 **B**：观察到的硬崩无 traceback / 无 session-end / 机器没睡眠重启 / 无 WER → OS 级带走进程（长空闲连接掉线）。旧 `cli.py` 用占位句「会话已退出且无停止说明」喂 `classify` 命中不了任何词 → `unknown`。

> 顺带核实（未改码）：「12:19 是我操作引起的吗」→ **否**，守护是 11:48 用户在面板点「定时调度」启动、自动跑今日计划，无双开。面板重启不自动拉起守护（`_spawn` 无 DETACHED，Ctrl+C 会连会话+守护一起带走）——Q33 已知未做。

## 二、四项修复（A/B/C/D）

- **A（治误报根因）**：`diagnose.py` 的 `structure` 关键词删「下一节」并加注释。真失败句另有词兜底——「找不到下一节按钮」靠「找不到」、「连续 N 节打不开任务页」靠「打不开 / 任务页」，均不受此删词影响。归到 `done` 后（`done` 不在 `_SUSPENDABLE`）既不示警也不重试。
- **B 治标（修显示）**：`cli.py::_revive_if_dead` 改 `code = classify(why) if why else "crash"` —— 无 session-end 直接归 `crash`「会话异常中止」（仍 suspendable，会自动重试），不再显示「未归类」。
- **B 治本（少掉一次崩）**：`astudy.py` 拟人间隔 `while` 等待包 `try/except`，掉线时写 `stats["stop_reason"] = f"拟人间隔等待中断：{exc!r}"` 并干净 `break`，让 cli 能写 session-end（TargetClosed→crash、timeout→network）。**注**：只覆盖可捕获子集，OS 级硬杀仍靠 B 治标兜显示。
- **C（消日志噪声）**：`cli.py::_sched_sig` 跳过 `_daemon_owned = {auto.plan, auto.retry_after}`（守护自有运行态，非用户配置）；用户可调的 `[auto]` 参数变化仍能被识别。
- **D（徽章口径 (b)）**：`/api/overview` 按 `learn.non_video_marks` 建 `LIKE` 拆出 `tasks`（非视频总数）/ `tasks_done`（非视频已 done），SQL 加两子查询；前端 `vTotal=total-tasks`、`vDone=done-tasks_done`、`tasksOpen=tasks-tasks_done`。已学完且仍有作业待做时，徽章写「**✔ 视频刷完 · x/x｜另 N 项作业待做**」，全清才写「✔ 已学完」；区标题同步。**后端 `rotation.course_weights` 仍 `pending==0` 不重刷**（作业另做，自动刷课不再选中该课）。

## 改动的文件清单

- `src/xxt/core/diagnose.py`：`structure` 关键词删「下一节」+ 注释（A）。
- `src/xxt/cli.py`：① `_revive_if_dead` 无 session-end 归 `crash`（B 治标）；② `_sched_sig` 跳过 `_daemon_owned`（C）。
- `src/xxt/adapters/chaoxing/astudy.py`：拟人间隔 `while` 包 `try/except`，掉线写 stop_reason + break（B 治本）。
- `src/xxt/panel/app.py`：`/api/overview` 加 `_nv_where/_nv_params` 与 `tasks/tasks_done` 子查询；`mkCourse` 加 `vTotal/vDone/tasksOpen`；ctl / nums / zoneTitle 三处徽章改口径 (b)（D）。
- `tests/test_auto_limits.py`：`TestClassify` 加用例「下一节按钮不可点；本地已无待学章节」→ `done`（A 回归）。
- `tests/test_q44_reopen_reload.py`：`test_sched_sig_ignores_daemon_owned_keys`（C）。
- `tests/test_panel.py`：`NoTreeGatingTest.test_overview_splits_video_from_assignment`（D）。

## 交接块

- **进行中**：会话 72 **两批均未提交**（02 加载中 hotfix + 已学完徽章；03 本篇 A/B/C/D 误报三连），等用户口令走提交 / 推送。
- **下一步第一个动作**：用户重启面板（`.\.venv\Scripts\xxt.exe panel`）+ 浏览器 Ctrl+F5 → ① 已学完且仍有作业的课（#1《知行合一》：69 视频刷完、2.12 作业待做，是活靶子）徽章显示「✔ 视频刷完 · 69/69｜另 1 项作业待做」而非「69/70」；② 不再对已学完课报「⚠ 页面打不开 · 会自动重试」；③ 日志不再反复刷「配置已重载」。成功 / 报错原文都发回来。
- **阻塞点**：线上 `c95a61c` 带着「加载中」bug，**应尽快提交并推送覆盖**（02 hotfix 与本批 03 一起上）。
- **用户下一步操作清单**：重启面板 → 按上面三点验收 → 回「提交」或「推送」。
