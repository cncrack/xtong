# 2026-10-26-11 选择账号筛选栏加 学校/院系/班级 + （未填X）反选

## 本次目标

用户在「选择账号」弹窗截图里指出：搜索框后面只有「全部年级 / 全部专业」，问**能不能加学校名称，或者平台上其他能区分的字段**，并强调"**注意要防被封**"。

## 结论：平台能给的字段 + 防封边界

- 平台"学习空间"页自身会返回 `stu-base-info`，可取：**姓名 / 学号 / 学校 / 院系 / 班级**（候选键名收在 `adapters/chaoxing/profile_read.py`，抓到什么回什么，不猜）。这些字段**早已入库** `account_profiles`。
- 因此筛选**完全在本地 SQLite / 内存对象里过滤，零新增平台访问**——加多少筛选都不会增加风控面。
- 唯一会碰平台的是**补这些字段**（当前两号 `school/dept/cls` 全空）：那是"每账号打开学习空间一次、复用页面自带 `stu-base-info`、零额外请求"，且按用户要求**串行、不并发、带间隔**。**本次未执行**，等用户发话。

用户拍板：**学校 + 院系 + 班级 全加**。

## 改动（`src/xxt/panel/app.py`）

1. `#pickDlg .pbar` 新增三个下拉：`pk_school / pk_dept / pk_cls`，位于「只看未排」之后、年级/专业之前。
2. 新增 `PICKFILTERS` 常量（`[id, profile键, 中文名]` × 5），`fillPickFilters()` 改为按下表通用生成：
   - 选项来自本地画像的 distinct 值（`.trim()` 去空白、排序）；
   - **每个下拉末位追加「（未填X）」（`value=__empty__`）**，用于一键筛出"画像不全"的账号去补；
3. `pickVisible()` 改为按 `PICKFILTERS` 通用求交集：`__empty__` 判 `字段为空`，其余精确相等；原先只硬编码 `grade/major` 两行判断。
4. 未改后端：`/api/overview` 在上一会话已下发 `school/dept/cls`。

## 验证

- `py_compile` 0；`check_js.py`（node --check）0
- `pytest` **104 passed**
- 重启面板无 traceback；headless `diag_ui8.py`（纯前端注入假数据、不写库）：
  - 5 个下拉齐全：`pk_school: 全部学校|（未填学校）`、`pk_dept: 全部院系|（未填院系）`、`pk_cls: 全部班级|（未填班级）`、`pk_grade: 全部年级|2026|（未填年级）`、`pk_major: 全部专业|（未填专业）`
  - 给 `#1` 注入 `school=测试大学` → 选「测试大学」只出 `#1`；选「（未填学校）」只出 `#2`；选「（未填班级）」出 `#1 #2`
  - 1500px 视口下筛选栏**仍是一行**（`pk_q/pk_school/pk_major` 的 `offsetTop` 均 59~60），未撑爆
  - **pageerror=0**

## 未做 / 后续

- 学校/院系/班级**实际数据仍是空**：要真能按学校筛人，需先跑一次串行画像读取（每号一次、复用 `stu-base-info`、不并发）
- 学号未做成下拉（学号几乎唯一，用搜索框更合适）

## 改动的文件清单

- `src/xxt/panel/app.py`（pbar HTML 三个 select；`PICKFILTERS` + `fillPickFilters()` / `pickVisible()` 通用化）
- `STATUS.md`、`docs/PLAN.md`（v0.16 行补充筛选栏）、本 devlog
- 临时诊断（`data/` 已 gitignore）：`data/debug/diag_ui8.py`

## 交接块

- **进行中**：✅ 无
- **下一步第一个动作**：用户 `Ctrl+F5` →「📚 批次 → ＋新建批次 → ＋选择账号」看搜索框后是否多了「全部学校 / 全部院系 / 全部班级」三个下拉，并用「（未填学校）」验证能筛出画像不全的账号
- **阻塞点**：无（下拉值全空属预期，补齐需用户授权串行平台读取）
