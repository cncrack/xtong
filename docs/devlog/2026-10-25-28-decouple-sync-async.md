# 2026-10-25-28 同步/异步解耦 + 时段窗口两处补强

## 一、本次目标与结果

| 目标 | 结果 |
|---|---|
| 把"碰 page 的同步函数"从共用路径里摘出来，让异步引擎结构上不可能误用 | ✅ 新增 `pagejs.py`（纯数据）/`pagesync.py`（同步专用），`study.py` 瘦身 97 行，`astudy.py` 只剩一行 `from .pagejs import` |
| 加 CI 守护，防止混用复发 | ✅ `tests/test_module_boundaries.py` 4 条断言 |
| 小修①：越窗后不再白点一次"下一节"+白等一个拟人间隔 | ✅ 两个引擎都把窗口检查前移到点击之前 |
| 小修②：账号级学习窗口"只填一侧"静默失效 | ✅ 面板保存时直接拦，报"需同时填写开始与结束" |
| 顺带修 bug | ✅ `panel/app.py` 的"✔ 读判分"接口相对导入少一个点（必然 500） |

测试：`pytest` **62 passed**（57 → 62，新增 5 条）。

## 二、为什么要解耦（不是删同步版）

`astudy.py` 原来有 `from .study import WATCHER_JS, match_chapter_id`，`_play_video` 里还有一句
`from .study import build_watcher_js`。这些函数本身是纯的，但同一个文件里躺着
`username_sniffer()` / `open_chapter_frame()`——它们直接返回同步 Playwright 对象。
"能 import 到纯函数"和"能 import 到同步副作用函数"共用同一个入口，就是 devlog 26 事故的温床。

拆分后的边界：

| 模块 | 内容 | 谁可用 |
|---|---|---|
| `pagejs.py` | `WATCHER_JS`、`build_watcher_js()`、`match_chapter_id()` | 全部（不含 page 参数，天然安全） |
| `pagesync.py` | `find_frame_with()`、`open_chapter_frame()`、`current_task_title()`、`username_sniffer()` | `study.py` / `cli.py::cmd_work` / `panel/app.py` |
| `study.py` | 只剩 `StudyRunner` | — |

守护断言（`tests/test_module_boundaries.py`）：

1. `astudy.py` 不得 `from .study` / `from .pagesync` import
2. `pagejs.py` 剥掉 docstring 后不得出现 `page.xxx(`/`frame.xxx(` 与 `import playwright`
3. `pagesync.py` 不得出现 `async def` / `await`
4. 全仓扫描 `from ...pagesync import`，命中者必须在白名单（study/cli/panel）

## 三、顺带发现的两个问题

### 3.1 "✔ 读判分"必然 500（已修）

`panel/app.py` 的 `work_review_api` 写的是 `from .adapters.chaoxing.study import ...`
（`.adapters` 解析成 `xxt.panel.adapters`，不存在）。同文件 1402 行是 `..adapters`（正确），
说明是复制粘贴时漏了一个点。**这个接口从上线起就没成功过**——用户还没点过，明天一点就炸。

### 3.2 题库是"全局共享答案"，不只是"共享题目"

`questions` 表字段：`question_hash, stem, type, options, answer, correct_count, wrong_count`
——**没有 account_id**。`ai/quiz.py::bank_lookup` 只要 `answer IS NOT NULL` 就返回，
`work.py::review_work` 判对时把 `correct_count+1`、判错时把 `answer = NULL`。
后果（用户 2026-10-25 指出）：

- A 账号答过的题，B 账号遇到一模一样的题 → 直接拿 A 的答案，秒出且不问 AI
- A 账号被判错 → 全局把答案清空，把 B 已经验证正确的答案一起带走

用户结论：**题库（题目本体）可以共享，答案不能共享**。方案见 §六（待确认后实施）。

## 四、时段窗口的两处补强

### 4.1 账号级窗口的生效链路（已核对）

`accounts.window_start/window_end` → `core/accounts.py::effective_pace()`（**两个都填才覆盖**）
→ `run_accounts` 每账号各传一份；`cmd_learn` 传 #1 的 → `pace.within_daily_window()`
（支持跨午夜，如 22:00–06:00）。

### 4.2 到点怎么停：播完当前视频才停

窗口只在"启动前"和"每轮循环顶部"判定，`_play_video` 播放期间不查 → 正在播的视频一定播完
（刻意的：中途掐断会留半截进度、也更容易被平台看出异常）。

代价：最多超出约"一个视频时长 + 一个拟人间隔"。2026-10-25 实测 #1 就是 23:02:25 播完后
还点了下一节、等满 69 秒，23:03:36 才发现超窗。

**修复**：窗口检查前移到"点下一节"之前 → 超窗时直接停，省一次真实页面推进 + 一个间隔。

### 4.3 只填一侧的静默失效（已修）

`effective_pace` 用的是 `if row["window_start"] and row["window_end"]`，
所以只填开始时间会**静默跟随全局**，界面不报错。现在 `PUT /api/accounts/{id}` 直接拒绝。

## 五、改动文件清单

| 文件 | 改动 |
|---|---|
| `src/xxt/adapters/chaoxing/pagejs.py` | 新增（纯数据：WATCHER_JS/build_watcher_js/match_chapter_id） |
| `src/xxt/adapters/chaoxing/pagesync.py` | 新增（同步专用 4 个 page 辅助函数） |
| `src/xxt/adapters/chaoxing/study.py` | 删 97 行（函数搬走），改 import，窗口检查前移 |
| `src/xxt/adapters/chaoxing/astudy.py` | 改 import 到 pagejs，删函数内 import，窗口检查前移 |
| `src/xxt/cli.py` | `cmd_work` 改 import pagesync |
| `src/xxt/panel/app.py` | 参考答案改 import pagesync；修"读判分"相对导入；账号保存加窗口成对校验 |
| `tests/test_module_boundaries.py` | 新增（4 条边界守护） |

## 六、待确认方案（未实施）

### 6.1 题库共享 / 答案按账号隔离

```sql
-- questions 只留"题目本体"（共享）
questions(question_hash PK, stem, type, options, created_at, updated_at)
-- answers 按账号隔离（每题每账号一行）
answers(question_hash, account_id, answer, correct_count, wrong_count,
        source TEXT,          -- llm|manual|legacy
        verified_at TEXT, PRIMARY KEY(question_hash, account_id))
```

- 读：先查 `answers` 本账号 → 未命中再调 AI（AI 答案只写本账号）
- 判分回读：`correct_count/wrong_count` 只改本账号那行
- 迁移：现有 `questions.answer` 写入 `answers(account_id=0, source='legacy')`，
  **account_id=0 视为"未归属历史数据"，默认不参与自动作答**（避免继续污染），只在面板题库页展示
- 可选开关：`quiz.share_answers_across_accounts`（默认 false）

### 6.2 批量启动（统一对某门课程开多账号）

- 新表 `plans(id, name, course_name, created_at, enabled)` + `plan_accounts(plan_id, account_id)`
- 启动时间 = 每账号 `start_at` ∪ 账号级窗口 ∩ 全局窗口（不另造新时间字段）
- 调度器复用现有 `auto` 守护：每分钟算"哪些账号现在该开"，只补开没在跑的
- 扩展位：`plans.extra_json`（指定章节 / 只刷视频 / 跳过作业……）

### 6.3 数据可视页

- `accounts` 增加画像字段（或新表 `account_profiles` 1:1 更好扩展）：
  `real_name, student_no, grade(年级), major(专业), class_name, college`
- 新增 `study_sessions` 聚合表（`account_id, course_id, started_at, ended_at, videos,
  seconds, stop_reason`）——不再靠解析 `run_logs.detail` 自由文本
- 面板新页 `/stats`：按 账号/课程/年级/专业 交叉统计（今日/本周/累计），
  维度做成可插拔的 `GROUP BY` 白名单，新增维度只加一行

## 七、教训

1. 共用模块的"入口粒度"决定事故概率：只要同一个文件里既有纯函数又有同步副作用函数，
   下游一定会图省事 import 那个文件。**按"是否碰 page"拆文件，而不是按业务拆。**
2. 静默回退是最难查的坑：`if a and b` 这种"要么全覆盖、要么全不覆盖"的逻辑，
   必须在写入端校验，而不是靠文档提醒。
3. 日志里"到末尾"这类结论性话术，要么给出判断依据，要么别写——本次两次误报都是文案先于事实。

## 交接块

- **进行中**：解耦与两处小修已完成并测试全绿；§六 三项方案（题库隔离 / 批量启动 / 可视页）待用户拍板
- **下一步第一个动作**：等用户选定 §6.1 是否开工（题库迁移涉及 schema 变更，需先备份 `data/xxt.db`）
- **阻塞点**：无。今晚 #1 已因时段窗口正常结束（23:03:36，视频 3 个），#2 数据已复原待明日
