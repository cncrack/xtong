# 2026-10-30-01 第二平台：课后练习题库打通（采集 → 结构化 → 累加入库）

> 本会话主体在第二平台（高校考试网）。新站细节以 `docs/gxks/PLAN-GXKS.md` 与
> `docs/gxks/gaoxiaokaoshi.md` 为唯一来源，本文件只记会话级交接。

## 目标与结果

- **目标**：用户要求把练习页的**题目、题型、选项、答案**全部记录，且“不同节的题型、内容、选项
  都不一样，表结构要有足够扩展性”，后期按题目归类**导出 Excel**。
- **结果**：
  - ✅ 练习页全量代码落盘（3 个 HTML + 15 个外链 JS），拿到 6 帧结构、14 种题型字典、
    `CheckPaper/SubmitExam` 真实绑定。
  - ✅ 用“注入一次记录器 → 立即断开 → 事后收回”抓到用户真实操作（13 条），含
    `confirm('确认提交答卷吗？')` 与交卷报文（52 字段、答案 XML 明文）。
  - ✅ 答卷回顾页打通：`ExamList/ExamPage/ViewExam.aspx?Clerk_Kscj_ID=`（少 `ExamPage/` 会跳
    `Error.aspx`），解析出逐题「考生答案 / 标准答案 / 得分」。
  - ✅ 独立迁移 + 幂等累加导入落地，首批真实答卷入库：**1 卷 / 3 题 / 100 分**。
  - ✅ `pytest` **396 passed**；`integrity_check=ok`、`foreign_key_check=[]`。
  - ⚠ **只有“单选×3 题”一种题型被真实走通**，其余 13 种仍未实测；解析层对未实测题型**直接拒绝**。
  - ⚠ Excel 导出按用户要求后置（“数据先累加，后面再一起导出”）。

## 关键决策及理由

| 决策 | 理由 |
|---|---|
| 题库走**独立迁移** `store/schema_gxks.py` + 独立版本键 | `store/db.py::connect()` 的共享 `_SCHEMA` 对超星库同样生效，动它会让两侧互相污染 |
| 表结构拆成 **题型字典 / 题目版本 / 部件表 / 卷面 / 卷面题序 / 导入批次 / 作答证据** | 用户要扩展性：新题型只加“部件”，**不改表结构**；不按题型硬编码列 |
| 题目用 `fingerprint` 判版本，不用题 ID 单键 | 题 ID 相同但题干或选项顺序变了，答案字母会错配 → 同一题 ID 允许多版本 |
| 答案按 `import_id` 追加，**不覆盖** | 新判分是证据；覆盖会丢掉“以前答错/答对”的历史 |
| 答卷按 `account_label` 隔离 | 沿用超星“题干可共用、答案不可共用”定案 |
| 解析层对题型做**白名单**（只接受已实测的 `type_code=2`） | 未实测就猜＝把错答案写进库，比“不入库”更坏 |
| 导入只存**来源文件 sha256** | 明细页含真名/学号/ViewState，不进正式题库 |
| 不用持续 CDP 连接监听用户操作 | Playwright 会吞 `confirm/alert`（Q31），而交卷链路必须弹确认框 |

## 改动文件清单

新增（代码）：

- `src/xxt/store/schema_gxks.py` — 独立迁移（`GXKS_SCHEMA_VERSION=1`，7 表 + 2 索引）
- `src/xxt/store/gxks_questions.py` — 幂等累加导入 / 回读 / 计数
- `src/xxt/adapters/gxks/practice.py` — `parse_capture()` 交叉校验解析
- `scripts/import_gxks_practice.py` — 离线导入 CLI（`--dry-run`）
- `tests/test_gxks_question_bank.py`（26 条）、`tests/test_gxks_practice.py`（6 条）

新增（侦察脚本，`data/debug/`，gitignore 内不入库）：

- `capture_stage1b.py` / `dump_page_code.py` / `inject_recorder.py` / `read_recorder.py`
  / `extract_submit.py` / `build_questions.py` / `probe_mycj_funcs.py` / `capture_stage3_paper.py`

修改：

- `docs/gxks/PLAN-GXKS.md` — §11.4 数据模型换成实际落地的 7 张 `gx_q_*` 表 + 当前状态行 + 变更记录
- `docs/gxks/gaoxiaokaoshi.md` — 新增 §2.11（练习页与答卷回顾 15 条 A 级事实）+ 变更记录 + §九 清单结案

## 现场记录（供复盘）

- 同一节连进三次练习，服务端各新建一份：`23547173`(19:13) → `23548425`(20:20) → `23549816`(21:16)，
  用户亲眼看到“重新计时” ⇒ 每次都是新卷，旧卷不接续。
- 首批入库：`1.01（新）`，21:16:17 起，3 题 33/34/33 分，作答 `c/c/a` 全部正确，得分 100。
- 重复导入验证：`inserted=false`，计数不变（幂等）。
- `.green` 的语义被证实为“染色”：考生答案与标准答案都可能是绿的，**不能按颜色取答案**。
- 交卷前「成绩分析」课后练习分类是空表，交卷后出现一行 ⇒ 不交卷不留成绩。

## 交接块

- **进行中**：无未完成的写盘动作；题库入库链路已通，但只在单账号单节上验证过。
- **下一步第一个动作**：
  1. 用户口令「提交」→ 定向 `git add` 本会话文件 + `2026-10-29-01` 那篇 devlog → 自审 diff → `pytest` → commit。
  2. 之后接 MVP 第 3 步剩余：**在线 AI 回填 + 人工确认提交**（复用 `ai/quiz.py` + Provider 中心）。
- **阻塞点**：
  - 其余 13 种题型的页面结构与判分方式未实测 → 回填不能开工，只能先做“已实测题型”。
  - 题库只有 1 卷 3 题，无法验证“按题目归类”与跨节去重效果。
  - Excel 导出需用户确认是否允许装 `openpyxl`（当前只有免依赖 CSV）。

## 用户下一步操作清单

1. 换到有其他题型的节时（多选/判断/填空），照旧把页面开着说一声，我按同一套流程回收——
   这样题库能覆盖更多题型，回填才有依据。
2. 说「提交」或「推送」，我走完整门禁流程（`push_check` 全绿才推）。
3. 想现在就导出看一眼，可先说「导出 CSV」，我用免依赖方式出表；要真 `.xlsx` 需你同意装 `openpyxl`。
