# 2026-11-23-01 G-8 待确认队列落地（gx_pending）+ G-9 第 1 级核对单

> 承接 [2026-11-11-02 定稿](2026-11-11-02-g9-g8-final-plan.md)。
> 11-11 只出了方案，代码改到一半停在 `gxks_work.py`；本篇是**落地完成**。
> 期间跨了两次收工/开工（11-11 → 11-23），代码一度被乱码编辑损坏，已用
> `git checkout` 恢复干净版本后重做。

---

## 一、用户定案回顾（实施依据）

| 项 | 定案 |
|---|---|
| 客观题入库 | 某账号做对并提交 → 标准答案进共享池 → **其他账号同课直接调用**；主观题不行 |
| 无标准答案 | 标「待验证」→ AI 生成 → **人核对** |
| G-8 表 | **建 `gx_pending`** |
| G-1 | **不做**重启面板按钮 |
| 三档处置 | **反对「一直停着」**；要明确提示哪个章节哪题；**可跳过继续下一题** |
| 放置 | 放进现有「**本次作答**」区，考虑多账号 |
| 题库结构 | 要**留扩展余地** |

---

## 二、本轮落地

### 2.1 schema v3 → v4：`gx_pending`

| 字段 | 用途 |
|---|---|
| `account_label` / `course_id` / `section_id` / `section_name` / `exercise_id` | 定位到账号/课/节/练习 |
| `question_ordinal` / `type_code` / `stem` | **定位到具体哪一题**（一行 = 一题，才能「跳过继续下一题」） |
| `reason` / `risk_json` | 为什么需人看 |
| `status` | `pending` → `verified` / `skipped` / `expired` / `submitted` |
| `decided_by` / `decided_at` / `decision_note` | **谁、何时、怎么决定**（留痕核心） |
| `work_file` / `op_id` | 指向 `work_*.json` 快照与 `gx_ops` 流水，可回溯 |

全部 `IF NOT EXISTS`，旧库原地升级不重建。**测试实证**：v3 库里已有 `gx_ops` 记录，升级后仍在。

### 2.2 `store/gxks_pending.py`（新模块）

- `add()`：同一 `(账号, 练习, 题号)` 已有 `pending` 时**不重复建**（4 秒轮询/重跑不刷屏），只更新风险
- `decide()`：只允许从 `pending` 出发，**终态不可覆盖**（防已有决定被冲掉）
- `expire_stale()`：超时标 `expired` 并留痕，**不删行**
- `list_pending(status=None)`：含已决定的也能查（「留痕可查」）
- `pending_counts()`：一次 `GROUP BY` 按账号统计，红点用 `__total__`

### 2.3 `store/gxks_questions.py`：客观题共享闸门

```python
OBJECTIVE_TYPE_CODES = frozenset({"2"})   # 已实测：单选
def is_objective(type_code) -> bool
def objective_registry(conn, *, type_code, standard_source) -> bool
```

- `lookup_answer` 加过滤：**主观题即使有 `site` 标准答案也不给别的账号用**
- 扩展余地就落在这里：多选/判断放行后把代码加进这个集合即可，不动表结构

### 2.4 `scripts/gxks_work.py`：G-9 第 1 级

新增纯函数 `build_review(plan, low_confidence)`：

| 情形 | 风险原因 |
|---|---|
| 客观题 + AI 生成 | `ai_used` + `no_site_answer` |
| 主观题（任何来源） | `subjective` |
| AI 且自评置信度低 | `low_confidence`（**仅提示**） |

**关键修正（用户质疑后）**：`confidence` **不作闸门** —— 它是 LLM 自报的主观数、
无校准、不是准确率。唯一硬条件是「有没有平台 `site` 标准答案」（`source == "bank"`）。

`main()` 里：
- `items` 补 `type_code`（`build_review` 需要）
- `result` 加 `review`（risks / counts / auto_eligible）
- **风险未清时即使配了 `auto_submit` 也不交卷**（打印提示，改由人工核对后手动交）
- 每题风险写 `gx_pending`，并写一条 `gx_ops(work-review)` 留痕

### 2.5 面板

- `/api/state` 带 `pending`（按当前账号）+ `pending_counts`（全账号合计）
- `renderWork` 末尾调 `renderPendingBlock`：**放进「本次作答」区**，不新建区域
- 每行显示 **节名 / 第几题 / 原因（中文）/ 题干 / 时间**，满足「明确告诉哪个章节哪里出问题」
- **无任何阻塞动作**（不给「挂起/暂停」按钮），符合「可跳过继续下一题」
- 新增动作词 `work-review` / `work-verify` / `work-skip`（面板显示中文）
- CSS 含 375px 断点

---

## 三、过程中踩的坑（写下来免得重犯）

1. **编辑工具连续产出乱码**：同一轮里多次出现 `0.0oli`、`label292`、`indentbug=2`、
   `soreness`、`out_path日本人` 这类损坏，`py_compile` 直接报语法错。
   → 处置：`git checkout -- scripts/gxks_work.py` 恢复干净版本，改**小块 + 每次编译校验**，
   并用脚本扫 `U+FFFD` 确认零乱码。**大块整段替换是这次的根因。**
2. **`db` 关闭后仍被使用**：把 `gxks_pending` 查询写在 `return` 字典里，而 `db` 已
   `close()` → 面板 `/api/state` 500（`ProgrammingError: Cannot operate on a closed database`）。
   → 移到 `try` 内、`finally: db.close()` 之前。已加回归测试守护。
3. **旧测试硬编码 schema 版本**：`assert int(ver) == GXKS_SCHEMA_VERSION == 3`。
   → 改成只断言「等于常量 + 下限」，并在测试里顺带断言 v4 新表存在。

---

## 四、验证

| 项 | 结果 |
|---|---|
| `tests/test_gxks_pending.py`（新） | 14 条 |
| `tests/test_gxks_work_review.py`（新） | 11 条 |
| `tests/test_gxks_pending_ui.py`（新） | 10 条 |
| 全量 `pytest` | **1336 passed**（1326 → 1336 中的增量含三条新文件） |
| `node --check`（内嵌 JS） | ✅ 通过 |
| `push_check` | **PASS** |
| 页面指纹 | `PAGE` 155945 → **158378** |

---

## 五、没做的（明确边界）

- **G-9 第 2 级 `guarded` 不启用**：只留 `auto_eligible` 数据判据，不据此交卷。
  启用另需：题库该题 `site` 答案充足 + 人工核对 ≥10 次零误填 + 用户书面同意
- **主观题「逐题重新生成」不实做**：`allowed_types` 只放行单选，主观题整卷拒填，
  走不到那一步；接口位置已在 `build_review` 的 `subjective` 分支留好
- **G-1 重启面板按钮**：用户已明确不做
- **自动入库接线（G-7）**：`gxks_work.py` 仍只查不写题库；真实判分页样本要靠 G-5 实跑产生

## 六、改动的文件

- `src/xxt/store/schema_gxks.py`（v4 + `gx_pending`）
- `src/xxt/store/gxks_pending.py`（新）
- `src/xxt/store/gxks_questions.py`（`is_objective` / `objective_registry` / `lookup_answer` 过滤主观题）
- `src/xxt/store/gxks_ops.py`（+3 动作词）
- `scripts/gxks_work.py`（`build_review` + 接线 + pending 留痕）
- `src/xxt/panel_gxks/app.py`（`/api/state` 带 pending）
- `src/xxt/panel_gxks/page.py`（`renderPendingBlock` + 动作词 + CSS）
- 三个新测试文件 + `tests/test_gxks_ops.py`（版本断言修正）

## 交接块

- **进行中**：无（本轮落地完成并验证）
- **下一步第一个动作**：用户启动面板验收 → 面板「本次作答」区应有待确认清单（当前无数据，需 G-5 实跑产出）；然后开工 **G-5 练习 AI 回填实跑**
- **阻塞点**：G-5 需要用户在 9222 Chrome 登录后点「做练习」；客观题入库的**真实判分页样本**也靠它产生
