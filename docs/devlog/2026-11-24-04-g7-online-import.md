# 2026-11-24-04 G-7 客观题自动入库 + 已做标记 + 操作记录加账号

> 用户 2026-11-24 三条要求，一次做完：
> 1. 「这个操作记录里面最好在合适的位置加上是谁操作的」
> 2. 「客观题的题目和答案要入，主观题，题目要入，答案暂不考虑入，因为每个人不一样」
> 3. 「如果做过的，在面板的列表中状态要发生修改」
> 另定：「平台判分页的标准答案 = 平台官方答案，对的，这个更好」

---

## 一、先回答「先前 2 次是怎么处理的」

`gx_q_papers` 里 `default` 有 2 条（`exercise_id=15263`、`14669`），
`gx_q_imports.raw_json` 显示都是 **10-29 通过手动离线导入**：

```powershell
python scripts/import_gxks_practice.py --capture-dir data/debug/stage_2_1 \
  --db data/gxks_questions.db --account-label default
```

采集脚本把 5 份快照写成文件 → 该脚本读文件 → `parse_capture()` 解析 → `import_bundle()` 入库。
**每做一次练习就得手动跑一遍**，这就是 G-7 要填的洞。

`data/debug/stage_2_1/` 里那 5 份快照还在（10-30 01:01），结构已核对：
`practice_stage1.json` 就是答题帧的 `tm_arr`，`viewExam.html` 是判分页。

---

## 二、为什么不复用 `parse_capture`（用户的疑问）

**我上次说「成本高」不准确**，真实理由有三条：

### ① 它的 5 份输入本来就是在线的数据

| 文件 | 在线对应物 | 交卷前就有？ |
|---|---|---|
| `practice_stage1.json` | `paper["frame"]` + `paper["raw_rows"]` | ✅ |
| `exam_questions.json` | `paper["questions"]` | ✅ |
| `practice_stage2.json` | 交卷后跳转 URL | ✅（交完即有） |
| `view_exam.json` | 判分页 URL + title | ✅ |
| `exam_html/viewExam.html` | 判分页 HTML | ✅ |

为了用 `parse_capture` 得先序列化→写盘→再读回来，**纯往返，不增加任何校验能力**。

### ② 它的签名暴露了设计意图

```python
def parse_capture(*, account_label, questions_doc, stage1, stage2,
                  review_meta, review_html, course_doc=None):
```

入参**全是已序列化的 `dict`/`str`**，没有活对象 —— 这是为「离线归档」设计的形态。

### ③ 但核心逻辑**必须复用**

判分页解析（`div[id^=ddTm_]`、`span.tmda` 标「标准答案」）是最有价值、最容易写错的部分。
所以抽成 **`parse_review_question()`**，两条路共用：

```
practice.py
  ├─ parse_review_question(node, row, type_map)   ← 单题解析（新，共用）
  ├─ parse_capture(...)                           ← 离线导入，改调上面那个
  └─ parse_review_online(...)                     ← 在线流水线（新）
```

**不是两套解析，也不是为复用而往返写文件。**

### 在线路径**比离线更严**

`parse_capture` 只比题干字符串；`parse_review_online` 额外用 `qid`（`row[0]`）逐题对齐 ——
在线时我们**知道**每道题的平台 ID，没理由不用。

---

## 三、客观题入答案、主观题只入题目

用户原话：「客观题的题目和答案要入，主观题，题目要入，答案暂不考虑入，因为每个人不一样」，
并确认方案 A（主观题的 `picked` 也不存）。

实现位置在 `import_bundle()`（**不是**另写一个函数）：

```python
a = q.get("answer", {})
if not is_objective(q.get("type_code", "")):
    a = {}          # 主观题：picked / standard / score 全不写
```

### 为什么主观题还是写一行空答案

`gx_q_answers.import_id` 是这次快照的一部分，**缺行会让"答卷完整性"校验（题数对不上）失败**。
写空行 + `NULL` 是最小侵入的做法，且 `lookup_answer` 只认 `standard_source='site'`，
`NULL` 天然不会被复用。

`lookup_answer` 里原有的 `is_objective()` 闸门继续生效 —— 双重保险。

---

## 四、平台标准答案 → 待确认项自动核验

用户定案：「平台判分页的标准答案 = 平台官方答案，对的，这个更好」。

新增 `gxks_pending.verify_by_site_answer()`：

```python
UPDATE gx_pending SET status='verified', decided_by='site-answer',
       decision_note='平台判分页标准答案（官方答案，无需人工再核）'
WHERE account_label=? AND exercise_id=? AND status='pending'
  AND question_ordinal IN (...)
```

- 只动**仍是 `pending`** 的行：人工已决定过的（skip/verify）不被覆盖
- `decided_by='site-answer'` 让留痕能区分「人工核的」和「平台答案核的」

这与 G-9「`confidence` 不作闸门」的原则一致：**平台判分是客观事实，AI 自评不是**。

---

## 五、交卷后入库的接线

`scripts/gxks_work.py`：

```python
if do_submit:
    gwork.submit(paper["frame"])
    try:
        graded = gwork.wait_graded(page)              # 等 viewExam.aspx
        graded_bundle = practice.parse_review_online(...)
        gxks_questions.import_bundle(db, graded_bundle)
        gxks_pending.verify_by_site_answer(...)
    except Exception as exc:
        print(f"[WARN] 判分页入库未完成：...")
        print("       交卷本身已成功，不受影响。")
```

**三条硬约束**：

1. **顺序不能反**：没交卷就没有判分页（测试 `test_submit_happens_before_import` 守着）
2. **入库失败不影响交卷**：整段在 `try` 里，交卷是既成事实，入库只是附加收益
3. **等页面而非固定 sleep**：`wait_graded()` 轮询 URL 里出现 `viewExam.aspx`

---

## 六、操作记录加「账号」列

用户原话：「这个操作记录里面最好在合适的位置加上是谁操作的」。

**发现一个真 bug**：后端原来是

```python
ops = gxks_ops.recent_ops(db, account_label=label or None, limit=60)
```

只查**当前选中账号** —— 那样这列永远只有一个值，加列毫无意义。
更糟：批量动作写的是 `account_label=""`（不归属单账号），**这个过滤会把它整条滤掉**，
用户根本看不到批量记录。

改：查全部账号（`limit=100`），前端加「账号」列：

- 显示名优先（用户认得「林鑫阳」不是 `default`）
- 批量记录 → `(批量)`
- CSS `#ops td:nth-child(-n+3)` → `(-n+4)`（多一列，漏改会把「动作」压成竖条）

---

## 七、已做过的节在列表里看得出来

`gx_q_papers` 按 `(account_label, exercise_id)` 记每次作答。新增
`_annotate_attempts()` 在 `/api/state` 里挂两个字段：

```python
row["attempt_count"] = 3
row["attempt_last_at"] = "2026-10-29 21:16:17"
```

前端 `practiceCell`：

| 情况 | 显示 |
|---|---|
| 没做过 | `可做` + `#15263` |
| 做过 3 次 | `已做 3 次` + `可重做` + `#15263` |

**只影响展示，不改缓存文件** —— 缓存的职责是「平台读到什么就存什么」，
作答次数是本地题库的事实，混写会让缓存过期时把次数一起弄丢（测试守着不许 `write_text`）。

---

## 八、验证

| 项 | 结果 |
|---|---|
| 新增测试 | `test_gxks_online_import.py` **17 条** |
| 全量 `pytest` | **1417 passed** |
| 页面指纹 | 160764 → **161787** |
| 修旧测试 | 3 条（`practiceCell` 文案、CSS 列数、`recent_ops` 锚点） |

### 17 条新测试守什么

| 组 | 条数 | 内容 |
|---|---|---|
| `TestParseReviewOnline` | 5 | 正常解析 / 练习ID不符 / 缺 Clerk_kscj_ID / 题数不符 / 题干不符 |
| `TestSubjectivityRule` | 3 | 客观题入题目+答案 / 主观题只入题目 / 主观题不被跨账号复用 |
| `TestPendingVerifiedBySiteAnswer` | 3 | 只标列出的题号 / 不覆盖人工决定 / 空列表 noop |
| `TestWorkScriptWiring` | 3 | 等判分页后入库 / 入库不破坏交卷 / 交卷在入库之前 |
| `TestAttemptAnnotation` | 3 | 挂 attempt_count / 不写缓存 / 前端显示「已做 N 次」 |

---

## 九、你要做的（跑一次练习验证）

`default`（林鑫阳）36 节全学完、都有 `exam_id`，正在跑着 9222。

1. 面板节列表挑一节 → 点「**做练习**」
2. 等 AI 回填完 → **看逐题表核对**
3. **你自己点「交卷」**（脚本不替你交）
4. 交卷后看终端应出现：
   ```
   [5/5] 等判分页并入库（客观题）…
         入库 新增 N 题（题目全入；答案只入客观题）
         待确认清单：N 题已按平台标准答案标为已核验
   ```
5. 再刷新节列表 → 那一节应显示 **「已做 1 次」+「可重做」**

### ⚠ 一个无法离线验证的点

判分页的真实结构我只能按 `practice.py` 已有选择器写。**如果第 4 步报错**，
把 `[WARN] 判分页入库未完成：...` 整行贴给我 —— 那是真实 DOM 与预期不符，
按实际结构调整即可（交卷已经成功，不用重做）。

---

## 改动的文件

- `src/xxt/adapters/gxks/practice.py`（抽 `parse_review_question`，新增 `parse_review_online`）
- `src/xxt/adapters/gxks/work.py`（新增 `wait_graded`）
- `src/xxt/store/gxks_questions.py`（`import_bundle` 按题型过滤答案）
- `src/xxt/store/gxks_pending.py`（新增 `verify_by_site_answer`）
- `src/xxt/panel_gxks/app.py`（`_annotate_attempts`、`ops` 查全部账号）
- `src/xxt/panel_gxks/page.py`（操作记录「账号」列、`practiceCell` 已做次数、CSS 列数）
- `tests/test_gxks_online_import.py`（新，17 条）
- `tests/test_gxks_partial_cache_and_locked.py` / `test_gxks_page.py` / `test_gxks_pending_ui.py`（断言同步）

## 交接块

- **进行中**：无（代码完成、1417 全绿、已提交未推送）
- **下一步第一个动作**：用户对 `default` 挑一节做练习 → 自己交卷 → 看是否出现「入库 N 题」
- **阻塞点**：判分页真实结构未经实测；若不符，按 `[WARN]` 原文调选择器
