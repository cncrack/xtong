# 2026-10-26-01 题目共享 / 答案按账号隔离 + 判分三态化

## 一、本次目标与结果

| 目标（用户 2026-10-26 明确） | 结果 |
|---|---|
| 一门课程的**题目可以共用**数据库 | ✅ `questions` 只留题干/题型/选项，成为共享题库 |
| **答案不能共用**，只能各自用 AI 生成 | ✅ 新增 `answers(question_hash, account_id)`，读取强制带 `account_id` |
| 判分"解析不到"不能当成答错 | ✅ 三态 `correct/wrong/pending/unknown`，只有明确 0 分才清空 |
| 判分回写不能影响别的账号 | ✅ `bank_record_wrong` 只改本账号那行 |
| 历史遗留答案怎么处理 | ✅ 迁到 `answers(account_id=0, source='legacy')`，**永不参与自动作答** |

测试：`pytest` **66 passed**（62 → 66，新增 5 条隔离/迁移用例）。

## 二、关键决策

### 2.1 为什么不是"每个账号一个数据库/一张表"

用户问：每个账号是否该有独立数据库或数据表保存。结论：**不分开库**，用 `account_id` 维度列。

| 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| 每账号一个 `.db` | 物理隔离、可单独备份/迁移 | 跨账号统计要 ATTACH 多库；事务/迁移变复杂；`work_questions` 缓存键已含账号 | ❌ |
| 单库 + account_id 列 | 统计、迁移、备份都简单；已有 `courses/chapters/run_logs/work_questions` 都是这个模式 | 靠代码纪律保证过滤 | ✅ 采用 |

真正的"每账号专属数据"落地形式：
- 登录态：`data/accounts/{id}/state.json`（已经是每账号一份文件）
- 答案：`answers` 表的 `account_id` 行
- 题目缓存：`work_questions.cache_key = "{account_id}|{course_id}|{chapter}"`（早就是账号维度）

### 2.2 不做 `quiz.share_answers_across_accounts` 开关

上一轮我提过加个开关，用户问"这个开关的作用是什么"。它本来是给"想复用某个号已验证答案"留的逃生口，但这正好是用户明确否定的行为，留着只会被误开。**决定：不做开关**，改用更硬的手段——`account_id` 设为强制关键字参数（漏传直接 TypeError），`bank_lookup(account_id<=0)` 直接返回 None。

## 三、Schema 变更与迁移

```sql
-- 共享题库：只留题目本体
questions(question_hash UNIQUE, stem, type, options, created_at, updated_at)
-- 按账号的答案
answers(question_hash, account_id, answer, correct_count, wrong_count,
        source /* llm|manual|legacy */, updated_at,
        PRIMARY KEY(question_hash, account_id))
```

迁移写在 `store/db.py::connect()`（幂等）：老库若 `questions.answer IS NOT NULL` →
复制到 `answers(account_id=0, source='legacy')` → 清空 `questions.answer`。

真实库执行结果：

```
questions 9 行；answers 9 行；account_id=0（遗留） 9 行且都有答案
仍残留全局 answer 的行：0
```

备份：`data/debug/xxt.db.bak-20261026-pre-answers-split`（迁移前）。

## 四、判分回读（读判分）改造

用户问"这个分值能直接给出吗，如果没有直接给出呢"——现实是三种：

| 页面情况 | 得分 | 处理 |
|---|---|---|
| 客观题已批阅 | `得分：2` / 得分容器内 `2分` | correct（>0）/ wrong（=0） |
| 主观题未批阅 | `待批阅` | pending，**不动题库** |
| 没提交成功 / 结构改版 / 字体没解出 | 什么都没有 | unknown，**不动题库** |

原实现把第三种也算 wrong 并 `answer = NULL`（全局），会把验证正确的答案误删。
另外两处一并修：

1. 分数正则收紧（原来 `([\d.]+)\s*分` 会误抓题干里的"（2分）"）
2. 题目匹配改成 qid 精确（`work_questions.qid`），退化才用题干前缀且**要求唯一**，多候选直接跳过（不猜）

## 五、批量启动与数据可视页（方案定稿，待排期）

### 5.1 批量启动：统一建任务，**不统一开跑**

```
plans(id, name, course_name, enabled, extra_json)
plan_accounts(plan_id, account_id)
```

启动时刻不新增字段，按账号自身规则算：

```
该账号此刻可启动 ⟺ start_at 已到 ∧ 账号窗口 ∩ 全局窗口 ∧ 今日额度未满 ∧ 未在跑
```

- 一个 plan 只是"把这门课排给这批账号"，真正的错峰来自每账号不同的 `start_at`/窗口
- 单账号个性化设置（面板 ✎ 管理）永远优先于 plan
- 若某账号也想"跟随 plan 的默认时间"，把它的 `start_at` 留空即可
- 调度复用现有 `auto` 守护，每分钟算一次，只补开没在跑的

### 5.2 数据可视页：维度里必须有年级/专业/开学期

```
account_profiles(account_id PK, real_name, student_no, grade /*2024级*/,
                 major, class_name, college, term /*开学期，如 2026-2027-1*/)
study_sessions(account_id, course_id, started_at, ended_at, videos, seconds, stop_reason)
```

- `term` 单独一列（不用日期推断）：同一批人可能同时上"2025-2026-2"和"2026-2027-1"的课
- `study_sessions` 让统计不再解析 `run_logs.detail` 自由文本
- 维度做成白名单映射：`account / course / grade / major / class / term / day`，加维度只加一行
- 接口：`GET /api/stats?by=grade&term=2026-2027-1&range=week`

## 六、改动文件清单

| 文件 | 改动 |
|---|---|
| `src/xxt/store/db.py` | 新 `answers` 表；`questions` 去答案列；`connect()` 加幂等迁移 |
| `src/xxt/ai/quiz.py` | `bank_lookup/bank_record/bank_record_wrong/solve/handle_quiz` 全部加 `account_id`（强制关键字）；`account_id<=0` 直接拒绝 |
| `src/xxt/adapters/chaoxing/work.py` | `review_work` 三态化 + qid 精确匹配 + 正则收紧；新增 `_match_question_hash`；`answer_work` 传 `account_id` |
| `src/xxt/adapters/chaoxing/study.py` / `astudy.py` | `_handle_quiz` 传 `account_id` |
| `src/xxt/panel/app.py` | 参考答案的 `bank_lookup/bank_record` 传 `account_id` |
| `tests/test_quiz_ai.py` | 5 条新用例：跨账号不可见、题目共享、判错不牵连、legacy 不复用 |

## 七、教训

1. "共享什么、不共享什么"必须写进**函数签名**和**表结构**，写进注释等于没写。
2. 迁移要给历史数据一个"不会被误用"的落点（`account_id=0`），而不是删掉——删了就再也说不清哪些是旧答案。
3. 判分这类"读页面"的功能，宁可不判也不要误判：`unknown` 是一个合法结论。

## 交接块

- **进行中**：题库答案隔离 + 判分三态已落库落码，66 测试全绿；批量启动与数据可视页方案见 §5
- **下一步第一个动作**：等用户点单——① 批量启动（`plans` 表 + 调度）② 数据可视页（`account_profiles` + `study_sessions` + `/stats`）
- **阻塞点**：无。⚠ 面板进程仍在跑旧代码，用户看新逻辑需重启面板
