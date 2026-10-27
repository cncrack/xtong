# 2026-10-27-11 修 B1/B2 + 徽标 hover + 「没学过就是待学」数据回补 + 尝试次数配置化

## 一、用户三个提问的答复（先结论）

| 提问 | 结论 | 证据 |
| --- | --- | --- |
| #2 为什么没出现「3 节全打不开」？ | **同一个 bug，只是 #2 今天没走到那条路径** | #2 今日 13 个视频全部 `played=True` 记账，全程未触发"播放中断→还要继续换下一节" |
| #2 也停了，是你停的吗？ | **不是，是额度正常收工** | `data/logs/sub-mlearn-20261027-083206.log` 末尾：`[小号1] 视频 13 个；时长额度达标` → `session-end｜时长额度达标｜视频 13 个` |
| 尝试次数做成配置项落实了吗？ | **一半**：守护的 `[auto] max_tries` 早在 27-08 已落地；**节级三个次数还是魔法数字**，本次补上 | 见 §四 |

### #1 与 #2 的路径差异（为什么"同一份代码、两种结果"）

```
#2（正常）：进节 → 播完(ended=True) → 记 video-done → 标 done → 点下一节 → …… → 额度达标收工
                                                                  ↑ 全程没碰"回章节列表兜底"这一段
#1（出事）：进 5.1 → 播到 441/551s 异常退出(ended=False) → 点"下一节"不可点
                                → 触发 P0-3c 兜底 _jump_next_pending()
                                → 撞 B1（缓存 frame 已 detached）+ B2（取回全表首个 pending=自己）
                                → 5.1/5.2/5.3 连续 3 次失败 → 收工，视频 0 个
```

补充事实：#1 的视频进度在 11:13:32 之后骤降（377→441 花了 3 分 32 秒，此前 340→377 只用 32 秒），
说明播放本身被降速/卡住——这是**触发点**，但把它当"播放结束"继续换节，才是 3 节全废的放大器。

另一条容易误读的对比：#2 的 5.1/5.2/5.3 早在 09:50 / 09:59 / 10:08 就已真播完记账，
所以它今天根本不会再碰这三节；#1 是第一次学，才暴露问题。

### 面板重启是否影响正在跑的会话

**不影响**。#2 会话 08:32 启动，一直跑到 11:38 额度达标，期间我重启面板 3 次（每次 `taskkill` 的是 8300 端口的监听进程），
#2 的日志无中断痕迹。会话是 `xxt mlearn` 独立子进程，不是面板内的任务。

## 二、B1/B2 修复

| 编号 | 原缺陷 | 修法 |
| --- | --- | --- |
| B1 | `astudy.py` 的 `tree_frame` 整个会话只 `_open_chapter_frame` 取一次，任务点/章节切换后 iframe 被重建 → 旧句柄 detached（`Frame was detached`）| 新增模块级 `_frame_alive(frame)`（`evaluate("1")` 探活）；`_enter_chapter` 每次点击前探活，失效就重新取 frame；点击抛异常时把 `tree_frame` 置 `None` 强制下一轮重取 |
| B2 | `_jump_next_pending` 用 `status='pending' ORDER BY idx LIMIT 1` 取**全表第一个** pending——当前节没播完仍是 pending，取回的永远是它自己；且失败后 `mark_entry_visited(..., only_pending=True)` 把它标 visited → 永久跳过 | 抽出可测函数 `pick_pending_after(conn, course_id, after_pid, tried)`：先取当前节 idx **之后**的 pending，到尾部再从头绕回，本会话试过的跳过；**失败不再写库**，只进内存 `tried_entry` |
| B2 配套 | `kid/leaf_name` 进节后从不更新，"下一节"按钮已经跳到新节了它还不知道 → 兜底时按旧 idx 往后取会错序 | 每轮用 `match_chapter_id` 得到 `cid` 后反查 `platform_chapter_id/name`，同步刷新 `kid/leaf_name`；两处 `_jump_next_pending(...)` 调用传 `after_pid=kid` |

## 三、"没学过 / 没完成 = 待学"语义统一（用户明确口径）

会话收尾原本无条件 `mark_entry_visited(self.conn, course_row["id"], kid)`，
等于把**最后一次播放失败/没播完的视频**也标成 visited，明天不再选它 → 永久漏学。
现在只有**非视频节点**（作业/测验/讨论/阅读/考试/练习/公告）才记 visited，视频节失败一律保持 pending。

新增 `is_non_video_name(name)` + `_NON_VIDEO_MARKS`，数据回补与收尾判定共用同一口径。

### 数据回补（#1，course_id=1）

脚本 `data/debug/fix_visited_ch4.py`（先 dry-run，`--apply` 才写库；不入库）：

| idx | 章节 | 判定 | 结果 |
| --- | --- | --- | --- |
| 12 | 第一章｜2.12 作业 | 非视频节点 | 保持 visited |
| 25 | 第三章｜4.5 增强自我信心（上） | 无 video-done | visited → **pending** |
| 28 | 第三章｜4.8 学会积极思维（下） | 无 video-done | visited → **pending** |
| 29 | 5.1 改变学习方法（上） | 无 video-done（只播 441/551s） | visited → **pending** |
| 30 | 5.2 改变学习方法（下） | 无 video-done | visited → **pending** |
| 31 | 5.3 提升专业能力（上） | 无 video-done | visited → **pending** |

判据可验证：#2 在 09:50/09:59/10:08 有这三节的 video-done（551s/526s/479s），#1 一条都没有。

## 四、尝试次数配置化（用户点名）

`config/settings.toml` 的 `[learn]` 新增（默认值 = 修复前的硬编码行为，升级即兼容）：

```toml
enter_retries = 2       # 单节点开最多点几次（每次点完等任务页 15 秒），钳 1~5
max_skip_chapters = 3   # 连续几节都打不开就收工，钳 1~10
non_video_streak = 4    # 连续几个非视频任务点算本段结束，钳 1~20
```

- `config.py` DEFAULTS 同步补齐；`astudy.py` 新增 `_learn_int(settings, key, default, lo, hi)`，类型错/越界一律回落默认
- 替换点：`_enter_chapter` 的 `range(2)`、"下一节"按钮的 `range(2)` → `range(enter_retries)`；
  `_jump_next_pending` 的 `range(3)`、首节入口的 `len(tried_entry) < 3` → `max_skip`；`no_video_streak >= 4` → 配置值
- 会话启动打一行 `[#N] 节级策略：单节最多点 X 次｜连续 Y 节打不开收工｜连续 Z 个非视频点结束`，方便对账

**仍未配置化（有意保留）**：`LOGIN_BUDGET_PER_DAY=2`（每账号每日登录尝试上限）——安全底线，27-04 决策明确不上配置。

## 五、面板徽标 hover（用户选的折中方案）

- `badge(t, c, ti)` 支持第三参 `title`
- 「刷课中 11:06」：`title="正在刷课（课程名）——会话有效；下课后恢复显示“已登录”"`
- 「已登录」：`title="会话文件有效：点右侧「🔑 会话」可查看/刷新"`
- 「未登录」：`title="本机没存到会话，点右侧「🔑 登录」扫码"`

维护提醒：整段卡片 HTML 在**模板字符串**里，内层不能再嵌反引号，`title` 用字符串拼接（`'…'+run.course+'…'`）。

## 六、改动文件清单

| 文件 | 改动 |
| --- | --- |
| `src/xxt/adapters/chaoxing/astudy.py` | `_NON_VIDEO_MARKS`/`is_non_video_name`/`_learn_int`/`pick_pending_after`/`_frame_alive`；`_enter_chapter` 探活重取；`_jump_next_pending` 改造；首节入口与收尾语义修正；`kid/leaf_name` 刷新；节级策略日志 |
| `src/xxt/config.py` | `[learn]` 默认值补 3 项 |
| `config/settings.toml` | `[learn]` 段补 3 项 + 注释 |
| `src/xxt/panel/app.py` | `badge` 支持 title；三处徽标 hover 文案 |
| `tests/test_astudy_policy.py` | 新增 11 个用例（选节顺序/绕回/跳过已试/done 不选/非视频判定/配置钳制） |
| `data/debug/fix_visited_ch4.py` | 一次性数据回补脚本（**不入库**，含 dry-run） |

## 七、验证

- `py_compile` 0；`pytest` **186 passed**（原 175 + 新增 11）
- 面板重启后 headless 实测（`data/debug/diag_card_layout.py`）：两张卡 `课程区高=88 / 进度行=23 / 按钮相对左=340·宽88·高23` 完全一致，`溢出=False`、`pageerror: 无`
- 回补脚本 dry-run 与 `--apply` 结果一致，作业节点未被动

## 八、本轮踩坑复盘（用户要求"记住、提醒、避免重犯"）

1. **"同一份代码就该有同样结果"是错觉**：差异往往来自**数据/路径**而非代码——#2 的这三节早刷完了，#1 是首学；bug 藏在"视频异常中断"这条少走的分支里。排查顺序应是：先对齐**输入与路径**（今天各自的 video-done、章节状态、走到哪个分支），再怀疑代码。
2. **把进程死因归给"我操作过"要先证伪**：本次靠日志时间戳（#2 结束 11:38:46 vs 面板重启时刻）与 `session-end` 原因行，一分钟就能排除。**日志永远比记忆可靠。**
3. **`visited` 曾是"万能垃圾桶"**：既当"路过非视频节点"，又当"打不开就跳过"。两种语义混在一个值里，必然误伤——一改就改全部三处（兜底换节、首节失败、会话收尾），漏一处就继续漏学。
4. **切片式修复会留下残骸**：`_pick_pending_after` 先内嵌、后抽成模块级，两次 patch 后旧定义还在 → 出现"同名函数两份"。改完必须 `grep` 旧名字确认无残留。
5. **模板字符串里嵌字符串的坑**：内层用反引号会提前终止外层模板；单引号里写 `${x}` 不插值，会原样显示。此坑在面板里反复出现。
6. **配置化要"默认值等于原行为"**：否则升级瞬间行为漂移，出问题分不清是修 bug 还是改参数。本次三项默认值严格对应修复前的 2/3/4。

## 九、交接块

- **进行中**：B1/B2/配置化/hover/数据回补 已全部落盘并提交；**B3（`ended=False` 不重试）未修**，等用户拍板
- **下一步第一个动作**：用户点「⏱ 调度」→ 点停 → 点启，让 #1 用新代码补跑（今日 tries 2/2 已用尽，需重启守护才重置到 0；真实访问平台 1 次）
- **阻塞点**：B3 未修前，#1 若再次遇到"视频播放中途卡住"，仍会 ended=False 换节（但 **B1/B2 已修，不会再连废 3 节**，最多浪费一节进度并保持待学）
- **待用户拍板（挂了 3 轮，提醒）**：① B3 是否修（建议修：ended=False 且已播 >60% 时重载任务页续播一次，而不是换节）② 今日 #1 的 5.1 平台侧已记 441s 进度，回补 pending 后会从头还是续播，需实况看一次
- **疑问清单（提醒用户一起看）**：① #1 视频在 11:13 后骤降速是平台限速还是本机资源竞争（#2 同时段正常，倾向平台侧对 #1 限速）② 若明天 #1 仍在 5.1 卡住，说明该账号在该视频被限速，需要换时段或降倍速

## 十、用户下一步操作清单

1. 浏览器 **Ctrl+F5** 刷新面板（看新徽标 hover：鼠标停在「刷课中」/「已登录」上）
2. 点顶部「⏱ 定时调度」→「■ 停」→ 再点启（重置 #1 的今日尝试次数，新代码才生效）
3. 明天看 #1 是否从 4.5 开始正常推进；若 5.1 仍卡在 440s 左右，把 `data/logs/sub-mlearn-*.log` 结尾发我
