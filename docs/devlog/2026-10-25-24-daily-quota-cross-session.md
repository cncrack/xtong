# 24 · 每日时长额度跨会话 + DB 覆盖层裸串修复（捞到 Provider 切换失效 P0）

日期：2026-10-25 22:00~22:30
触发：用户「额度有生效吗，可以设置 2 个任务或者多少分钟数试试」+「有些还没落实，你检查一下」+「按你建议处理」

## 一、目标与结果

| 目标 | 结果 |
|---|---|
| 再实测一次额度（任务数） | ✅ 22:13:33 `session-end｜任务数额度达标（今日已完成 2 个）｜视频 0 个`，28 秒 = 纯错峰，**零浏览器零平台访问** |
| 每日"分钟数"额度真正每天生效（跨会话） | ✅ 22:22:06 `session-end｜时长额度达标（今日已学 17 分钟）｜视频 0 个`，17 分钟来自历史记录累计 |
| config.py 静默回退加日志 | ✅ 加了之后立刻捞出一个 P0（见第三节） |
| 回答"同步版有没有必要删" | 结论：**保留**，理由见第五节；未动代码，等用户最终拍板 |

三次实测对照（同一账号 #1，今日已有 2 个视频 / 1020s）：

| 时间 | 设置 | 结束原因 | 从启动到判定 | 是否碰平台 |
|---|---|---|---|---|
| 21:51:28 | 2 个 / 15 分钟 | 任务数额度达标（今日已完成 2） | 150s（错峰 134s + 16s 浏览器+课程页） | **碰了一次**（旧代码） |
| 22:13:33 | 2 个 / 10 分钟 | 任务数额度达标（今日已完成 2 个） | 28s = 错峰 28s | 否 |
| 22:22:06 | 60 个 / 10 分钟 | 时长额度达标（今日已学 17 分钟） | 169s = 错峰 169s | 否 |

## 二、关键决策

1. **时长额度的度量改成"读库"而不是"读内存"**：原来 `study_start = time.monotonic()` 是进程内计时，停掉再启动就重新计满一整份额度，"每天 240 分钟"名不副实。现改为从 `run_logs` 当日 `video-done` 详情的尾部 `Ns` 累计秒数（这套解析面板早就在用，只是 runner 没用），口径唯一化到 `core/quota.py`。
2. **两项额度都在会话启动前扣减**：任务数减当日完成数、时长减当日已学秒数，任一归零即拒绝启动（零浏览器）。循环内仍保留"本次会话内实时判定"，因为一个会话可能跑很久。
3. **日志语义改为"剩余额度"**：`当日剩余额度：214 分钟 / 43 个（今日已学 17 分钟 2 个）`，一眼能看出"为什么停了"。
4. **裸字符串兼容放在读取方，而不是只修写入方**：历史数据已经写成裸串了，只修写入方等于老配置永远修不好。读取方兼容 + 写入方规范化，两头都做。
5. **`llm.key_enc` 故意继续存裸 base64**（不 json.dumps）：`ai/llm.py` 直接读该行做 `b64decode`，加引号会解不开。它是独立字段，不进配置树。

## 三、意外收获：Provider 切换从来没生效过（P0）

给 `config.py` 加日志后第一次运行就打出：

```
覆盖层键 llm.base_url 的值不是合法 JSON，已忽略：'https://api.agnes-ai.cn/v1'
覆盖层键 llm.model 的值不是合法 JSON，已忽略：'agnes-2.5-flash'
覆盖层键 llm.key_enc 的值不是合法 JSON，已忽略：'gsFF+qUQTb6z4O5...'
```

根因：面板 `POST /api/llm/providers/save` 写 `llm.base_url` / `llm.model` 时**没做 json.dumps**（裸字符串），而 `config.py` 的覆盖层用 `json.loads` 解析失败后直接 `continue` → 这两个覆盖项**永久不生效**。

后果链：面板切 Provider → `llm.providers`（✓ JSON，生效）和 `llm.key_enc`（✓ 专用读取路径，生效）都换了，唯独 **base_url / model 还是旧的** → 新 Key 打旧地址 → 401/404。面板设置页刷新时显示的也是默认值，看起来像"保存成功但没生效"，与 devlog 51/52 的"看着像存了其实没存"是同一类症状。

修复三层：
- `config.py`：`json.loads` 失败 → 按裸字符串处理（+ debug 日志），历史数据立即恢复生效；
- `app.py` providers_save：base_url / model 改为 `json.dumps` 写入；
- `app.py` providers_del：删当前 Provider 时，名字改成 JSON 写入，并**同时把 base_url/model 回落内置默认**——原来只重置名字，会留下"名字是 Agnès、地址是已删除那家"的错配。

验证（`data/debug/verify_overlay.py`，临时脚本不入库）：
```
[OK] 裸字符串写入 'probe-model-bare' -> Settings.llm['model'] = 'probe-model-bare'
[OK] JSON写入 '"probe-model-json"' -> Settings.llm['model'] = 'probe-model-json'
还原后: agnes-2.5-flash | https://api.agnes-ai.cn/v1
```

## 四、改动的文件清单

- `src/xxt/core/quota.py`：新增 `parse_duration_seconds` / `sum_done_seconds` / `remaining_minutes`（时长口径唯一实现）
- `src/xxt/adapters/chaoxing/astudy.py`：额度前置改为"任务数 + 时长都按今日已用扣减"，日志改"剩余额度"
- `src/xxt/adapters/chaoxing/study.py`：同上（同步版对齐）
- `src/xxt/panel/app.py`：`today_stats` 复用 `sum_done_seconds`；providers_save/del 的 JSON 化与回落修复
- `src/xxt/config.py`：覆盖层读取失败留痕 + 裸串兼容
- `tests/test_quota_sync.py`：新增 `TestDurationQuota`（解析、累计、剩余计算）
- `data/debug/set_quota.py`、`data/debug/verify_overlay.py`、`data/debug/panel_api.py`：调试脚本（`data/` 不入库）

`pytest`：48 passed（原 46 + 新增 2）。

## 五、同步版是否要删（给用户的专业意见，代码未动）

现状：`StudyRunner`（同步）只被 `xxt learn` 使用；`AsyncStudyRunner` 是 mlearn/auto/面板的主引擎；astudy 反向从 study.py 借 `WATCHER_JS`/`match_chapter_id`/`username_sniffer` 等工具函数。

建议 **保留**，三条理由：
1. `xxt learn` 是 CLI 兜底入口，异步栈出问题（事件循环、aiohttp 代理等）时它是唯一能对照排查的另一条路；
2. 本次 frame 缓存 bug 的修复证明"两版要改两遍"的成本确实存在，但这个成本已经付过了——两版如今行为一致且各有测试；
3. 真正的风险不是"有两份代码"，而是"两份行为不一致"，而这靠**把额度/节奏/解析这些纯逻辑集中到 core**（本次已做）来消除，而不是靠删文件。

若将来要统一，正确做法是"**删引擎留命令**"：保留 `xxt learn` 入口，内部改为 `asyncio.run(run_accounts(...))`，顺带解决 `learn` 不写 `data/run/<id>.json` 导致面板误判空闲、可能同账号双开的问题。这项工作约 1 小时，建议排在无关紧要的时段做。

## 六、交接块

- **进行中**：无（本轮改动已全部完成并自测）
- **下一步第一个动作**：等用户对第五节意见拍板（保留 / 统一）；若统一则按"删引擎留命令"改 `cli.py` 的 `cmd_learn` 并补一个 `learn` 写 marker 的测试
- **阻塞点**：无。账号 #2 仍在跑（22:2x 在 `如何规划大学生活（上）`），额度已恢复 240 分钟 / 60 个
- **用户下一步操作清单**：
  1. 面板 Ctrl+F5 刷新，确认卡片显示"今日 X / 上限 Y（全局）"格式
  2. 若曾切过 Provider，重新点一次该 Provider 保存（让 base_url/model 以新格式落库），再试一次分析题目
  3. 什么都不用做也可以——#2 自己会按额度跑到停
