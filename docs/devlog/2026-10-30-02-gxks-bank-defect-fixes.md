# 2026-10-30-02 新站题库三处缺陷修复 + 路径统一

## 目标与结果

用户在"下一步建议"里选了第 1 项（先修缺陷再验证第 2 节），本轮把代码审查发现的四处真实缺陷全修掉，并顺手把历史数据补齐、测试补上。

**结果：全部完成。** `pytest` 398 passed（原 396 + 新增 2 条）；真实题库唯一一条答卷补全分类字段，快照数保持 1（证明"补分类不算新作答"的语义修正确实生效）。

## 四处缺陷与修法

### 缺陷 1：`course_id` / `section_id` 入库恒为空

- **根因**：练习页只带 `Tk_Cl_Id`（练习 ID，如 `15263`），**URL 里没有节 ID**；原 `parse_capture` 找不到就硬写 `""`，导致题库无法按节分组、导出无从归类。
- **证据**：`practice_meta.json` 的课程列表行结构（实测）
  `[节名, 课程分类, 学时要求, 已完成学时, 状态, {links:[onclick:"showframe('1.1 国防概述',1289)"]}, {links:[onclick:"showExam(15263)"]}]`
  → 练习 ID 与节 ID 在同一行，**可以反查**。
- **修法**：`practice.py` 新增 `section_index(course_doc)`（按 `showExam(N)` → `showframe('节名', 节ID)` 建索引）与 `parse_capture(..., course_doc=...)`；节名两侧不一致直接 `raise`（宁可失败不写错归属）。`import_gxks_practice.py` 新增可选输入 `course_doc = practice_meta.json`，存在才读。
- **口径**：`section_id` 存数字节 ID（真正的节粒度主键）；`course_id` 暂存**平台课程名**（如"军事理论（字幕版）"），因为 `ddlClass` 的 value 尚未采集，存名字既稳定又能直接分组。

### 缺陷 2：跨节同题无处归并

- **根因**：`gx_q_items` 的唯一键是 `UNIQUE(platform_qid, fingerprint)`——同一道题在不同节被考到时平台题 ID 不同，于是存成两条"题目版本"，检索和导出都会重复。
- **取舍**：**不删数据**。证据保留粒度没错（每个平台题 ID 都是一次真实出现），错的是"没有归并视图"。
- **修法**：`schema_gxks.py` 版本 1→2，新增 `gx_q_items_fingerprint` 索引（建表语句全 `IF NOT EXISTS`，旧库原地可升，不重建数据）；`gxks_questions.py` 新增 `bank_items()`，`GROUP BY fingerprint` 出一条，带 `occurrences`（出现次数）与 `platform_qids`（各平台题 ID）。

### 缺陷 3：幂等键把元信息算进去 → 补分类产生重复快照

- **根因**：原 `digest` 覆盖整个 bundle（含 `course_id`/`section_id`/`section_name`），且 dedup 的 enrich UPDATE 在"已存在就 return"**之后**。结果：只想补一个 `course_id`，就会多出一条 `gx_q_imports` 快照 + 一份重复 `gx_q_answers`。
- **修法**：新增 `_content_digest()`，只取 `exam_start_id` / `exercise_id` / `questions`；把 enrich UPDATE（`COALESCE(NULLIF(...))` 只填空值）**提到幂等判定之前**。
- **测试语义变更**：`test_enrich_classification_without_losing_history` 里 `snapshots == 2` 改为 `== 1`——这是修好后的正确语义，不是放宽断言。

### 缺陷 4：账本 / 账号表路径相对当前工作目录

- **根因**：`ledger.py` 与 `accounts.py` 都写死 `Path("data")/...`（相对 cwd）。从别的目录启动（比如工具内终端、或 `XXT_ROOT` 指向外部数据目录时）就会写出**第二份账本**，同一账号的当日额度被拆到两处，额度判定直接失效。实测 `data/gxks_ledger.jsonl` 至今不存在，说明这个机制还没真正跑过，属于"上线即错"的隐患。
- **修法**：两文件都改为 `default_path()` → `find_project_root() / "data" / ...`（`XXT_ROOT` > exe 目录 > 向上找 `config/settings.toml` 或 `.git`），四个函数 / `load_accounts` 的 path 参数默认改 `None`，内部 `_resolve()`。`run_gxks_multi.py:77` 同步。

## 历史数据补齐

新增 `backfill_classification(conn, *, account_label, exam_start_id, course_id="", section_id="", section_name="")`——**只填 `NULL`/空串，不覆盖已有值**，天然幂等。

实跑（`data/debug/_backfill_classify.py`，留痕 `data/debug/diag_backfill_classify.json`）：

```
before: {'papers': 1, 'question_versions': 3, 'attempt_questions': 3, 'snapshots': 1}
before row: {'course_id': '', 'section_id': '', 'section_name': '1.1 国防概述'}
after  row: {'course_id': '军事理论（字幕版）', 'section_id': '1289', 'section_name': '1.1 国防概述'}
changed: True
after : {'papers': 1, 'question_versions': 3, 'attempt_questions': 3, 'snapshots': 1}
```

## 关键决策

- **D（本轮新）**：题目"证据表"与"归并视图"分开——`gx_q_items` 保留每次出现，`bank_items()` 提供归并，导出走后者。删数据会让"这题在哪些节考过"永久丢失。
- **D（本轮新）**：`course_id` 暂时存课程名而非平台课程 ID，理由见缺陷 1 口径。等 `ddlClass` 的 value 采集到再单独加 `course_code` 列，不做语义漂移的原地改值。

## 改动文件清单

| 文件 | 改动 |
|---|---|
| `src/xxt/adapters/gxks/practice.py` | 新增 `_SHOWFRAME_RE`/`_SHOWEXAM_RE`/`section_index()`；`parse_capture` 增 `course_doc` 参数、填 `course_id`/`section_id`、节名不一致即 raise |
| `src/xxt/store/gxks_questions.py` | 新增 `_content_digest()`/`backfill_classification()`/`bank_items()`；enrich UPDATE 提前到幂等判定之前 |
| `src/xxt/store/schema_gxks.py` | `GXKS_SCHEMA_VERSION` 1→2；新增 `gx_q_items_fingerprint` 索引 |
| `src/xxt/adapters/gxks/ledger.py` | 删 `DEFAULT_PATH`；新增 `default_path()`/`_resolve()`；4 个函数 path 默认 `None` |
| `src/xxt/adapters/gxks/accounts.py` | `DEFAULT_PATH`/`LEGACY_PATH` → `default_path()`/`default_legacy_path()` |
| `scripts/run_gxks_multi.py` | `gaccounts.DEFAULT_PATH` → `gaccounts.default_path()` |
| `scripts/import_gxks_practice.py` | 新增可选输入 `course_doc = practice_meta.json` |
| `tests/test_gxks_question_bank.py` | 版本断言 1→2；`snapshots` 2→1；新增 backfill 只填空值、bank_items 跨节归并 2 条用例 |
| `docs/gxks/PLAN-GXKS.md` | §11.4 表结构与口径、三处修正说明、§九 变更记录 |
| `data/debug/_backfill_classify.py` | 新建（只跑一次、幂等、留 json 痕迹） |

## 踩坑记录

1. **`SearchReplace` 造成重复代码块**：改 `ledger.py` 时替换片段跨了函数边界，生成重复的 `mkdir/open/return` 与两个 `def read_rows`，且残留未定义的 `DEFAULT_PATH`。教训：替换前先读全函数，替换后立刻读回确认结构完整。
2. **参数名遮蔽**：`accounts.py` 里 `legacy_path` 参数与模块函数同名，函数体内调不到——先写 `globals()["legacy_path"]()`（丑），最后改函数名为 `default_legacy_path()` 解决。
3. **PowerShell 拼多语句 `python -c`** 必报引号错误 → 一律写脚本文件再执行。

## 交接块

- **进行中**：无。四处缺陷已修完、测试全绿、历史数据已补。
- **下一步第一个动作**：等用户在 Chrome（9222）里点开 **2.1《国家安全概述》** 的「参加练习」（`showExam(14669)`，节 ID 1298），按既有 5 步流程采集第 2 节答卷，验证 `course_id` / `section_id` 能自动落库（不再需要 backfill）。
- **阻塞点**：无技术阻塞。刷课按用户要求**明天白天再继续**（2.4 起）。
