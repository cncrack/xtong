# 2026-10-27-08 守护失败处理 + 章节入口兜底（P0 全部落地）

## 一、本次目标与结果

**目标**（用户「按 P0 全做」批准的五项）：

| 编号 | 内容 | 结果 |
|---|---|---|
| P0-1 | 确认守护"发起即成功"的旧语义已改掉 | ✅ 上轮已改（`auto-try` → 回读 → `auto-fire`），本次补齐配置化与原因透传 |
| P0-2 | 次数/退避/回读等待做成配置项 | ✅ 新增 `config/settings.toml` 的 `[auto]`（默认 2 次 / 15 分钟 / 25 秒，`max_tries=0` = 不限） |
| P0-3a | 入口章节被无条件标 `visited`，会把 `done` 降级 | ✅ `mark_entry_visited()`：`status<>'done'` 才写 |
| P0-3b | 点了知识点但页面没跳过去，仍当"非视频任务点"收工 | ✅ `_wait_task_page()` 15 秒确认 + 重试一次，再不成就换下一节 |
| P0-3c | 找不到「下一节」= 直接到课程末尾 | ✅ "找不到"/"不可点"都先 `_jump_next_pending()` 回列表续进（最多 3 节） |
| P0-3d | `auto-error` 只写猜测原因（"多为窗口外或额度已满"） | ✅ `_session_end_reason()` 抄子进程 `session-end` 的后两段 |

**结果**：**167 项测试全绿**（146 → 167，+21：`test_auto_replay.py` 6 条、`test_auto_limits.py` 10 条、`test_chapter_status.py` 5 条）。三项代码改动只验证了静态路径与单测，**尚未在实况会话中复跑**（需等 #1 明日 08:55 的自动启动，或用户手动 ▶ 一次）。

## 二、关键事实（本次查证，写下来防再犯）

1. **`_JOBS` 只剩 3 分 46 秒的记忆**：#1 今早 09:10:13 `auto-try` → 09:10:43 `auto-fire` → 09:13:23 进 4.8 → 09:13:59 `session-end｜找不到下一节按钮｜视频 0 个`。面板"看不到在刷"不是面板 bug——会话已结束，`_JOBS` 里自然没东西；真正缺的是**卡片上留一条"上次会话的结局"**（列入 P1，本轮不做）。
2. **两号章节目录完全一致**：70 节、`platform_chapter_id` 差异 0 处；差异只在"入口行为"——#2 走链式「下一节」推进，#1 冷启动点知识点后页面没跳。
3. **`done` 被降级的实证**：10-25 22:47:46 #1「增强自我信心（上）」播完 470s（`ended=True`）并写了 `video-done`，`23:03:36` 会话结束时旧代码无条件 `UPDATE ... status='visited'` → 卡片 26/70 而非 27/70，且这节下次还可能被重新选中。
4. **`[auto]` 是三层配置的中间层**：`config/settings.toml` → `app_config` 覆盖层 → 内置默认。`s.raw.get("auto")` 已合并完毕，直接读即可。

## 三、改动文件清单

| 文件 | 改动 |
|---|---|
| `src/xxt/cli.py` | 新增 `_auto_limits()`（读 `[auto]`，带上下限钳制，0=不限）、`_session_end_reason()`（取 `session-end` 详情后两段）；`cmd_auto` 接线：`max_tries/retry_minutes/confirm_seconds` 全部来自配置，回读等待用 `confirm_seconds`，失败告警附"子进程报：…"，试满上限写"今日不再自动重试，可手动 ▶ 补跑"，`auto-start` 详情附重试参数 |
| `config/settings.toml` | 新增 `[auto]` 段（`max_tries=2` / `retry_minutes=15` / `confirm_seconds=25`，含语义注释） |
| `src/xxt/adapters/chaoxing/astudy.py` | 新增模块级 `_wait_task_page()`、`mark_entry_visited()`；`run()` 内新增 `_enter_chapter()`、`_jump_next_pending()`、入口 3 节重试循环；两处"下一节不可用"改兜底续进；结尾改调 `mark_entry_visited()` |
| `tests/test_auto_replay.py` | 上轮新增 6 条（今日是否已学的回放判定），本次一并提交 |
| `tests/test_auto_limits.py` | 新增 10 条：`_auto_limits` 默认值 / 覆盖 / 0=不限 / 越界钳制 / 写坏回落；`_session_end_reason` 取后两段、短详情原样、旧会话不串、他号与他事件不串、NULL 详情 |
| `tests/test_chapter_status.py` | 新增 5 条：`done` 不降级、`pending→visited`、`only_pending` 不碰 visited/failed、`only_pending` 仍标 pending、未知章节 no-op |

`data/debug/` 下本轮诊断脚本（`q_cmp_struct.py` / `q_grep_old.py` / `q_vd1.py` / `q_acc1.py` 及输出 txt）属 `data/`，不入库。

## 四、关键决策与理由

1. **失败重试不无限次**：默认 2 次、间隔 15 分钟。理由是"连续访问触发风控"比"少刷一节"代价更大；`max_tries=0` 留给愿意全天盯的用户。上限 10 次是防手滑（写 `99` 会被钳到 10）。
2. **兜底换节用"标 visited 让它别再被选中"**：`_jump_next_pending` 失败时把该节 `pending→visited`，否则每次会话都撞同一节；但只动 `pending`，绝不动 `done`（那是已真实完成的成果）。
3. **`no_video_streak >= 4` 的阈值没动**：4.8 那种"点击后没跳页"已被 `_wait_task_page` 在入口处拦住，进了任务页之后连续 4 个无视频才是真的穿过作业点。改阈值会增加打扰（作业点确实存在）。
4. **面板卡片"上次会话结局"没做**：属于展示层缺口，与本轮 P0 的"别白掉一整天"是两件事；放 P1，等用户点名再做（卡片加一行"上次：09:13 结束 · 找不到下一节 · 0 个"）。

5. **本轮没覆盖的缺口（待用户拍板，未动手）**：回读只看 25 秒这个时间点，只能抓“秒退”，抓不到“已确认开跑、几分钟后自行退出”——今早 #1 就是 09:10:43 `auto-fire`（那时进程确实活着）→ 09:13:59 才退（视频 0 个），今日仍被算作已学。建议补丁：巡检时对 `fired` 账号再核一次进程存活（纯本地、零平台访问），退出且当日 `video-done` 为 0 → 降级 `fired` 并写 `auto-error` 允许当日重试。

## 五、交接块

- **进行中**：P0 五项全部落地，代码 + 测试已全绿（`push_check` 全绿），**随本 devlog 一同提交**；但**守护尚未重启**（新配置与兜底要重启 `xxt auto` 才生效）。
- **下一步第一个动作**：① 定向 `git add`（cli.py / settings.toml / astudy.py / 三个测试文件）→ `git commit -m "session: 守护失败重试配置化 + 章节入口兜底"` ② 重启守护（面板「调度」停 → 启，或 `xxt auto`）让 `[auto]` 与兜底逻辑生效 ③ 观察 #1 下一轮触发：应能看到 `auto-try → auto-fire`，若失败则 `auto-error` 里带**子进程自报原因**。
- **阻塞点**：无。**注意**：本机 `data/xxt.db` 里 #1 的 4.5 仍是 `visited`（历史被降级的数据），本轮只保证**不再发生**，没有回补；若要修正进度显示，可按 `video-done` 逐条回补 `done`（需用户确认后再动手）。

## 六、用户下一步操作清单

1. 重启一次守护（面板「⏱ 调度」停 → 启），让新的失败重试配置生效。
2. 明日 08:55 看 #1 是否自动开刷；若失败，把面板「📜 日志」里那条 `auto-error` 的原文发我（现在里面会带子进程的停止原因）。
3. 若要立刻验证兜底：手动 ▶ 启动 #1，观察日志里是否出现「改从章节列表进下一节」这类行。
