# 会话 36：画像区/选择账号表格化 + 平台画像字段扩展 + 搜索字段对齐

> 日期：2026-10-26（序号 08）
> 上承：`2026-10-26-07-batch-default-off-trend-hbar-login-soft.md`

## 1. 本次目标与结果

用户提出"账号列表表格化 + 画像尽量从平台获取 + 搜索字段对齐 + 备注名实相符"，方案 v2 经确认后实现（第 6 条'录入后自动串联登录/读画像'按用户要求保持现状、后续再议）。

| # | 需求 | 处理 | 结果 |
|---|---|---|---|
| 1 | 画像参数尽量从平台获取，不够再补 | `account_profiles` 增 `school/dept/cls`；平台读取回填并展示字段清单 | ✅ |
| 2 | 搜索栏字段对齐 | 统计页 `profQ` 与选择器 `pk_q` 统一字段集与 placeholder | ✅ |
| 3 | "没有备注却在搜索里" | 表格新增「备注名」列（备注 `note` 并入搜索集） | ✅ |
| 4 | 账号列表做成表格（标题行 + 各参数，与画像同步） | 选择账号弹窗 + 统计页画像区**两处**表格化 | ✅ |
| 5 | 批量平台读取 | 不做（只留单账号按钮） | ✅（按用户 A） |
| 6 | 加密录入后自动串联登录+读画像 | 不做，保持三步分离（后续再议） | ✅（保持现状） |

## 2. 关键决策

1. **平台字段集**：依据 `profile_read.py` 已枚举的候选键（name/student_no/school/dept/cls），新增 `school`(学校)/`dept`(院系)/`cls`(班级) 三列——**只展示、不进统计维度**；`年级/专业/学期` 平台不提供，保留手填补。
2. **"足够"证据优先**：代码里 + 日志中均无真实平台返回 → 面板在平台读取后显示**平台实际返回的键名清单**（只键名无值），首次实跑即可核对、随后据实补键名。
3. **表格化列头统一**（两处一致）：账号｜备注名｜姓名｜平台昵称｜学号｜年级｜专业｜学期｜学校｜院系｜班级（+来源/状态/操作）。画像区 8 列可编辑、其余只读；选择器只读 + 行首复选。
4. **搜索字段集统一**：账号ID/备注名/姓名/平台昵称/学号/年级/专业/学期/学校/院系/班级/备注(note)。
5. **迁移懒触发**：列迁移在 `connect()` 内执行 = 首次访问 DB 的请求才落列（面板重启后仅请求静态 `/` 不会触发，需一次 DB 触达）。

## 3. 改动文件清单

- `src/xxt/store/db.py`：`account_profiles` CREATE TABLE 增 `school/dept/cls`；迁移循环同步补列。
- `src/xxt/core/stats.py`：`get_profile`/`set_profile`/`merge_platform_profile`/`list_profiles` 全部纳入 `school/dept/cls`。
- `src/xxt/panel/app.py`
  - CSS：`.profrow*` → `#profBody/.ptable*`（含 `.ptable.pick` 行禁用态）
  - HTML：`profQ`/`pk_q` placeholder 统一；画像区副标题；选择器按钮 title
  - JS：`renderProfiles`（表格化，8 个可编辑单元格 `pf_n/s/g/m/t/sc/dp/cl`）、`saveProfile`（含新字段）、`FIELD_CN`（+学校/院系/班级）、`readProfileFromPlatform`（回显平台字段清单）、`renderPick`（表格化 + 复选 + 已排禁用）、`pickVisible`（搜索集扩充）、`profiles_set_api`（接收新字段）
- `tests/test_panel.py`：新增 `test_profile_roundtrip_includes_platform_fields`
- `data/debug/diag_ui4.py`：临时诊断（data/ 不入库）

## 4. 验证记录

- `py_compile`（app.py/stats.py/db.py）→ exit 0；`check_js.py` → node --check exit 0
- `pytest -q` → **97 passed**（96 + 新增 1）
- 面板重启（`python -m xxt.cli panel`）→ HTTP 200
- `data/debug/diag_ui4.py`（headless 实渲染）：
  - 画像区表头 13 列、2 行、首行 8 个可编辑 input ✔
  - 选择器表头 13 列、2 行 ✔
  - 两处 placeholder 一致 ✔
  - **pageerror = 0** ✔
- 现网 DB 迁移核验：访问 `/api/profiles` 后 `account_profiles` 含 `school/dept/cls` ✔

## 5. 踩坑与风险

1. **迁移懒触发易误判**：面板重启后仅请求 `/` 不会触发 `connect()`，直接查 PRAGMA 会误以为"迁移失败"；须先打一次 DB 触达的接口再查。
2. 平台真实字段键仍未实证——`read_profile` 键名候选（`username/studentno/schoolname/deptname/classname` 等）是否命中待首次实跑确认；命中与否都只补空、不覆盖手填。
3. `create_plan`/统计维度**未**动；`school/dept/cls` 不参与 `aggregate` 维度。

## 6. 交接块

- **进行中**：✅ 无
- **下一步第一个动作**：用户 `Ctrl+F5` → ①「📈 统计 → 画像设置」看表格列头与可编辑单元格 ②点某账号「⬇ 平台读取」→ **把 toast 里"平台返回字段：…"清单发我**（据此补键名）③「📚 批次 → ＋新建批次 → ＋选择账号」看表格化选号 ④在 `profQ`/`pk_q` 输关键字验证搜索覆盖
- **阻塞点**：无
- **等用户**：① 平台读取的字段清单（决定是否补映射）② 表格化观感 ③ 作业全链路实测 ④ 弹题选择器实况 ⑤ ≥3 账号并发