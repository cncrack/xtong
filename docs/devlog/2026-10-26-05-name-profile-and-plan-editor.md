# 2026-10-26-05 姓名画像 + 批次选择器重做 + 编辑既有批次 + 修两个 UI bug

## 一、本次目标与结果

用户在同一条消息里提了 7 点（姓名设为画像参数 / 日志账号筛选是否为 bug / 平台读取现在做还是明天做 / 日志没按账号筛选 / 画像保存没提示 / 新建批次要"账号列表→弹出→多条件搜索多选" / 已建批次如何管理账号），并用 `AskUserQuestion` 拍板三件事：**现在就做全部**、**姓名升级为可编辑画像字段**、**平台读取现在写代码明天由用户点**。

| 要求 | 结果 |
|---|---|
| 姓名作为画像参数 | ✅ `account_profiles` 加 `name` 列；`get/set/list_profile` 全支持；`display_name = 手填姓名 or 平台昵称`（前端卡片、批次选择器、统计搜索统一用它） |
| 日志没有按账号筛选（bug） | ✅ 根因定位并修复：初始化顺序问题——`switchView(VIEW)` 早于 `syncRun()`，若 localStorage 记住的是 logs 页，`fillLogAccounts()` 在 `ACCOUNTS` 加载前跑 → 下拉只有"全部"；现改为在 `syncRun()` 拿到账号后补调 |
| 画像保存没提示（bug） | ✅ `saveProfile` 改 `toast('✔ 画像已保存…')`；失败也 toast（红底），不再静默 |
| 新建批次界面不合理 | ✅ 弹窗重做成"已选清单 + ＋选择账号"；选择器弹窗支持关键字/只看未排/年级/专业筛选 + 多选 + 全选可见/清空 |
| 已建批次怎么管理账号 | ✅ 批次行加「✎ 编辑」：复用同一弹窗，改名/换课程/增删账号；后端新增 `update_plan`（冲突校验排除自身） |
| 平台读取姓名/学号 | ✅ `adapters/chaoxing/profile_read.py` + `POST /api/accounts/{id}/profile_from_platform` + 画像行「⬇ 平台读取」按钮；**代码就绪，明天由用户点**（一次只读访问个人中心） |
| 保存/启停要有反馈 | ✅ 批次启停/删除、画像保存、平台读取全部 toast 统一反馈 |

## 二、关键决策及理由

1. **姓名做成独立列而非塞 `note`**
   `note` 是用户自由文本，塞进去无法结构化展示/检索，也没法参与统计与选择器过滤。加列 + `display_name` 回退逻辑后，一处定义（手填优先、否则平台昵称）全局复用。

2. **`display_name` 回退而不是"覆盖"**
   平台昵称是只读抓取值（可能旧/错），用户手填的才是确认过的真值。所以显示层一律 `name or platform_username`，且在卡片上，当二者不同时额外把平台昵称标出来，避免用户困惑"名字怎么变了"。

3. **平台读取"只补空、不覆盖手填"（`merge_platform_profile`）**
   用户手改过的姓名/学号是他确认过的真值，平台值不得悄悄覆盖。函数只填当前为空的字段，返回 `filled/skipped` 让用户看清"回填了什么、保留了什么"；有任何回填才把 `source` 标 `platform`。

4. **平台读取复用页面自身响应，不新增请求**
   `profile_read.py` 只打开一次个人中心 `space_url`，截获页面本身就会发的 `stu-base-info` 响应取 `userInfo`（与 `session.login.probe_session` 同一抓取点）→ 零额外请求，符合最小访问原则。抓不到**不猜**，如实报错。且日志只记"字段名清单/回填数"，**不记值**（避免敏感信息进日志）。

5. **选择器勾选状态与筛选解耦（`PKSEL` Set）**
   再踩一次上一个会话的坑：若沿用 `querySelectorAll(':checked')`，加了搜索后筛掉的账号会从 DOM 消失 → 静默丢勾选。所以主弹窗用 `PLSEL`、选择器内部用 `PKSEL` 两个 `Set` 存状态，渲染只是投影，过滤只影响"看得见哪些"。

6. **编辑既有批次复用同一弹窗（`PLANMODE`）**
   新建与编辑界面几乎一致，用一个弹窗 + `PLANMODE`（id 或 null）切换标题/按钮/接口（`POST /api/plans` vs `POST /api/plans/{id}/update`），避免两套 UI 分叉维护。

7. **`update_plan` 的冲突校验必须排除自身**
   启用态下重查"账号是否已在别的启用批次"，若不带 `exclude_plan=plan_id`，自己批次里的账号会跟自己撞车 → 永远无法编辑。这与 `set_enabled` 里已有的写法对齐。

8. **日志筛选的根因是"渲染顺序"而非接口**
   `/api/logs?account=` 后端一直支持。真正问题是下拉在账号数据到手前被灌满。修法有两种（改成事件回调 / 在 syncRun 里补一手），选了后者：改动最小、幂等（`fillLogAccounts` 保留当前选择，账号被删则回退到"全部"）。

## 三、改动的文件清单

- `src/xxt/store/db.py`：`account_profiles` 加 `name` 列（schema + `connect()` 幂等补列迁移元组首位）
- `src/xxt/core/stats.py`：
  - `get_profile` 返回 `name`/`platform_name`/`display_name`；`set_profile` 收 `name`；`list_profiles` 带 `name`/`platform_name`/`display_name`
  - 新增 `merge_platform_profile(conn, account_id, data)`（只补空、不覆盖手填，返回 `filled/skipped`）
  - `aggregate` 的搜索 haystack 扩到 `display_name`/`name`/`platform_name`/`student_no`
- `src/xxt/core/plans.py`：新增 `update_plan(conn, plan_id, *, name=None, course_name=None, account_ids=None, enabled=None)`（None=不变；空账号拒绝"要清空请删除批次"；启用态冲突校验 `exclude_plan=plan_id`）
- `src/xxt/adapters/chaoxing/profile_read.py`（**新建**）：`read_profile(state_path, space_url)`——一次打开个人中心，截获 `stu-base-info` 的 `userInfo`，候选键名列表 `_pick()` 提 姓名/学号/学校/院系/班级，返回键名清单 `fields`（不含值）；**标注"尚未实况验证"**
- `src/xxt/panel/app.py`：
  - API：`POST /api/plans/{id}/update`；`POST /api/accounts/{id}/profile_from_platform`；`POST /api/profiles` 接 `name`；`overview` 下发每账号 `profile`（含 name）/`student_no`/`display_name`
  - HTML：`planDlg` 重做（`#planTitle`/`#pl_picked`/＋选择账号/清空/`#pl_count`）；新增 `pickDlg`（`#pk_q`/`#pk_onlyfree`/`#pk_grade`/`#pk_major`/全选/清空/`#pk_count`/`#pk_list`）；画像行加"姓名"输入与「⬇ 平台读取」；`#logAcc` 首项文案改"全部账号（不筛选）"
  - JS：`PLANSEL/PKSEL/PLBUSY/PLANMODE`；`toast()`；`accName/accDesc`；`openPlan`（新建/编辑双模式）；`renderPicked/pickedRemove/clearPicked`；`openPicker/fillPickFilters/pickVisible/renderPick/pickToggle/pickAll/pickApply`；`doPlan`（按 `PLANMODE` 走 create/update）；`readProfileFromPlatform()`；`saveProfile` 带 `name` + toast；卡片主名与头像改用 `display_name`；`syncRun` 补 `fillLogAccounts()`；`filtered()` 匹配范围加 `display_name`
  - CSS：`.toast/.toast.show/.toast.bad`、`.picked/.pkrow/.x`、`.pbar select`、`.profrow .pfname`
- `tests/test_plans.py`：新增 5 条（改名换课程 / 换账号 / 空值拒绝 / 批次不存在 / 冲突排除自身）
- `tests/test_stats.py`：新增 5 条（姓名往返 / `display_name` 回退平台 / `merge_platform_profile` 只补空 / 无新值不标平台 / 按姓名搜统计）
- `data/debug/`（不入库）：更新 `smoke_tabs4.py`（选择器/编辑/姓名/平台读取按钮检查 + 建→改→删批次全清理）

## 四、验证记录

- `pytest`：**95 passed**（85 → 95）
- `node --check` 面板内嵌 JS：exit 0
- TestClient 冒烟（真实 `data/xxt.db`，画像先快照后还原、批次建→改→删全清理）：11 项 UI 检查全 ✔；路由 `profile_from_platform` / `plans/{id}/update` 均在；写姓名后 `overview.display_name` 生效；按姓名搜统计命中 1 号账号；批次改名/换课程/改账号回读正确、删除无残留
- 实况 8300（重启后）：4 个 tab + `viewLogs`/`logAcc`/`pbar`/`planQ`/`statQ`/`statTrend`/`profQ`/课程维度 全部 OK；画像接口带出 `platform_name` 与 `display_name`

## 五、遗留与风险

1. **`profile_read.py` 未实况验证**：学习通 `userInfo` 是否真含"学号/院系"属待证实项；需用户明天点一次「⬇ 平台读取」才知。**抓不到就留空，绝不猜**。
2. **年级/专业/学期平台大概率没有**（教务系统数据），仍需手填；平台读取主要解决"姓名/学号"。
3. **选择器未做"分页/虚拟滚动"**：账号上百时列表会很长（已有搜索/筛选缓解），后续按需再加。
4. **`update_plan` 不改 `enabled`**（编辑时不动启停）：启停仍走独立的「▶ 启用/⏸ 停用」按钮，避免编辑时误改状态。

## 六、交接块

- **进行中**：✅ 无。姓名画像 + 批次选择器重做 + 编辑既有批次 + 两个 bug 全部完成，95 全绿，实况面板已重启验证
- **下一步第一个动作**：让用户 `Ctrl+F5` 强刷面板 → 「📚 批次」点「＋ 新建批次」用「＋ 选择账号」试搜索/多选 → 对已建批次点「✎ 编辑」改名换课增删账号 → 「📈 统计 → 🏷 画像设置」填姓名/学号并点「⬇ 平台读取」验证
- **阻塞点**：无。**等用户**：明天点「⬇ 平台读取」验证姓名/学号能否从平台取到（年级/专业/学期多半取不到）
- **等用户**：① 10-27 批次错峰实测（既有待办）② 平台画像读取实况 ③ 填画像看分组 ④ 判分样本校准 ⑤ 弹题选择器实况 ⑥ ≥3 账号并发