# 2026-10-29-01 第二平台：多账号 + 多课程骨架（MVP 第 3 步第一半）

> 本会话主体在第二平台（高校考试网）。新站细节仍以 `docs/gxks/PLAN-GXKS.md` 为唯一来源，
> 本文件只记会话级交接（做了什么、为什么、下一步）。

## 目标与结果

- **目标**：用户问“是只固定这一门课、这一个人用，还是可以多人用、可选不同课程”，
  并拍板“按你推荐的做”＝先做 A（多课程）+ B（多账号）。
- **结果**：
  - ✅ 账号表、运行账本、多账号串行调度三块地基落地，`pytest` **364 passed**（+16）。
  - ✅ `run_gxks.py` 接上 `--account-label` / `--course` / `--ignore-quota`，每节完成自动记账。
  - ✅ 播放页本机信息补扫完成（零请求），Q29 结案。
  - ⚠ **多课程只能做结构、不能验证**：本账号 `ddlClass` 只有 1 项（军事理论），切换方式未证实（Q30）。
  - ⚠ 账本当天上线、历史未追溯（今日 11 节未入账）。

## 关键决策及理由

| 决策 | 理由 |
|---|---|
| 账号表 + 账本放 `adapters/gxks/`，不放核心层 | 平台相关（端口/课程/凭据路径），符合“平台细节只在 adapters” |
| **保留 `account.local.json` 回落** | 已有单账号流程正在跑，不能因为加多账号把它弄挂 |
| 日额度靠**本地 jsonl 记账** | 站点只给累计学时、查不到“今天”，没有别的数据源 |
| 多账号**串行**（并发 1） | D12：同 IP 多号同时刷最易被关联；先稳再谈并发 |
| 失败隔离＝**单账号失败只记下游** | “多人用”时一个人掉线不能拖住所有人 |
| 课程切换只做“最低假设 + 回读校验” | `onchange` 为空、没有第二门课，硬猜会**静默跑错课** |
| 不补录今天已完成的 11 节 | 补了会立刻把当天额度判成“用完”，今晚跑不了；次日零点自然准确 |

## 改动文件清单

新增：

- `src/xxt/adapters/gxks/accounts.py` — 账号表解析/校验/回落
- `src/xxt/adapters/gxks/ledger.py` — 运行账本（日额度）
- `scripts/run_gxks_multi.py` — 多账号串行调度
- `tests/test_gxks_accounts.py`（9 条）、`tests/test_gxks_ledger.py`（7 条）
- `data/debug/probe_courses.py` / `probe_menu.py` / `probe_click.py` / `probe_study_fp.py`
  / `open_window.py`（均为只读探测或辅助开窗，gitignore 内）

修改：

- `src/xxt/adapters/gxks/selectors.py` — 新增 `COURSE_SELECT_ID`（标注“切换方式未证实”）
- `src/xxt/adapters/gxks/driver.py` — 新增 `list_courses()` / `select_course()`
- `scripts/run_gxks.py` — 额度预检、记账、`--course` 切课
- `docs/gxks/PLAN-GXKS.md` — 新增 §11.16、Q30、Q29 结案、2 条变更记录

## 现场记录（供复盘）

- 2.2《国家安全形势》20:10:17 达标收工：**62/60 done**，用时 53.6 分钟，零重载零重建零补播。
- 19:13 进练习页侦察时给 1.1 留下一条考试记录（`ExamStartId=23547173`，60 分钟倒计时），
  用户选择**自己进站点看一眼状态**；为避免打断播放，另开了一个**独立 Chrome 窗口**
  （`Target.createTarget(newWindow=True)`，实测播放进度不受影响：19:52 仍 45/60 在涨）。
- 用户在独立窗口点「参加练习」**无反应也无提示** → 诊断（`probe_click.py`）证明元素正常
  （`A.btn_4`、`onclick=showExam(15263)`、可见可点），故判断是**弹窗被拦且提示框也被抑制**，
  建议在地址栏右侧“弹窗已阻止”里允许本站，或直接用 `chkExam.aspx` URL 打开（会新建一场）。

## 交接块

- **进行中**：无未完成的写盘动作；多账号骨架已过测试，未在“两个真实账号”上跑过。
- **下一步第一个动作**：
  1. 若用户提供第二个账号 → 建 `data/accounts.local.json`，起第二个 Chrome
     （`--remote-debugging-port=9223 --user-data-dir=...`），跑 `run_gxks_multi.py --dry-run`
     确认计划，再正式跑。
  2. 若无第二账号 → 继续 MVP 第 4 步之前先做 **C：独立单文件 EXE**（`xxt-gxks.exe`，
     独立数据目录，连本机 Chrome 9222）。
- **阻塞点**：
  - 多课程切换方式**未证实**（Q30），需一门以上的课程样本。
  - 账号表在本机尚未创建（当前走 `account.local.json` 回落，`label=default`）。
  - 账本从 2026-10-30 零点起才准确。

## 用户下一步操作清单

1. 有空时进站点看 1.1 那条练习记录状态（不必急着做，也不影响刷课）。
2. 告诉我：是否有**第二个账号**可以测多账号；该账号是否有多于一门课。
3. 今晚若要继续跑，说一声即可（当前批次仍在按 4 节上限跑）。
