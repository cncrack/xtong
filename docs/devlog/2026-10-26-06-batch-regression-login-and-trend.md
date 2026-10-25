# 2026-10-26-06 修批次回归 + 账号一键登录 + 趋势图重做 + 画像显示姓名

## 一、本次目标与结果

用户一条消息报了 5 个问题并拍板三件事：**全部修**；登录按钮做成"**先无头自动、失败提示原因并转手动过验证码/扫码**，成功后刷新『已登录』并顺带读画像"；趋势图"**你认为方便看数据的就行**"。

| 用户反馈 | 结果 |
|---|---|
| 批次里点「＋ 新建批次」没反应 | ✅ **真 bug，已定位修复**（上个会话重命名漏改声明行，见下） |
| 每日趋势图不好看、也没数据 | ✅ 重做：加"峰值/合计/视频数"摘要 + 柱顶分钟标注 + 友好空状态 + 柱区加高；有数据时清晰可读 |
| 画像设置里 `#1 default` 应显示姓名 | ✅ 改为显示 `display_name`（手填姓名 or 平台昵称），备注名 `default` 降级为括号并列 |
| 姓名框要显示"已有的姓名" | ✅ 姓名框预填 `display_name`（= 当前生效姓名），不再只预填手填值 |
| 保存按钮"始终是修改上一轮的值？" | ✅ 已澄清：保存=把**输入框当前值**写回；根子在姓名框原先是空的，修正预填后语义一致 |
| 账号功能是否要"登录"按钮/功能 | ✅ **确实缺失**——原先只有"未登录/已登录"徽标，`xxt login` 也只处理默认账号。现新增：CLI `login --account`（多账号人工登录）+ 面板卡片「🔑 登录/重新登录」+ `POST /api/accounts/{id}/login` |

## 二、关键决策及理由

1. **① 的根因是"变量名不一致"，不是逻辑**：`openPlan()` 声明用的是 `let PLANSEL`，但 `renderPicked/clearPicked/openPicker/pickApply/doPlan` 全用 `PLSEL`（上个会话改名漏了声明行）→ 点「＋ 新建批次」执行到 `renderPicked()` 即抛 `ReferenceError: PLSEL is not defined`，`showModal()` 永远不可达 → 表现为"点击没反应"。修复：声明行与赋值行统一为 `PLSEL`。

2. **登录复用 `session.relogin.auto_relogin`，不新写登录逻辑**：它已含"解密凭据 → 无头代填 → 识别 验证码/密码错 → 2 次/天 登录预算"。面板只做**编排 + 转人工**，把"为什么不行 + 怎么办"翻译成中文回给前端。

3. **无头优先、失败转手动（用户明确要求）**：符合"最小访问 + 不硬闯风控"——验证码**绝不自动打码**，遇到就明确提示用户改手动。

4. **手动登录用子进程 `xxt login --account N`，而非在面板请求里阻塞**：有头浏览器生命周期独立、面板请求不长时间挂起；`--account` 也顺带让"多账号人工登录"首次可用。

5. **凭据列单独直查**：`find_account` 返回的是公开列（不含 `username_enc/password_enc`）；用错会 KeyError。改为在登录函数内 `SELECT username_enc,password_enc`，只在本函数使用，**绝不外泄、日志只记事件不记值**。

6. **趋势图按"方便看数据"设计**：柱状保留，顶部加当天分钟标注，标题给"峰值/合计/视频数"一句话摘要，全零时给明确空状态（而不是一屏几乎看不见的小柱）。

## 三、改动的文件清单

- `src/xxt/cli.py`
  - `cmd_login` 支持按账号登录：解析 `--account`（id/标签，缺省 1=default）→ `account_state_path(s, id)`；提示语带账号号
  - `login` 子命令新增 `--account`
- `src/xxt/panel/app.py`
  - **修回归**：`let PLANSEL` → `let PLSEL`；`openPlan` 内 `PLANSEL=new Set(...)` → `PLSEL=`
  - `renderProfiles`：主显 `#id display_name`，备注名 `label` 以括号并列；姓名框 `value=display_name`；搜索 haystack 加 `display_name/platform_name`；底部说明改"预填当前生效姓名、保存写回"
  - `renderTrend` 重做 + CSS（`.tval` 新增；`.trendbox` 高 76→112px；`.tbar` 宽 64%+过渡）
  - 卡片 `.ops` 加「🔑 登录/重新登录」按钮（`doLogin(${a.id})`）
  - JS 新增 `doLogin(id)`（先无头自动 → 失败 confirm → 转手动）与 `pollLogin(id,deadline)`（3 秒轮询至 `has_session`，最长 3 分钟）
  - 新增端点 `POST /api/accounts/{account_id}/login`（`{manual:bool}`）+ 模块内 `_LOGIN_REASON`（captcha/wrong-password/budget/no-cred/unknown → 中文原因+建议）
- `data/debug/`（不入库）：新增 `diag_plan.py`、`diag_ui2.py`（headless 点验）

## 四、验证记录

- `pytest`：**95 passed**
- `node --check`（面板内嵌 JS）：exit 0 ——⚠ **但见下方踩坑，它查不出本次那类问题**
- headless 点验（`diag_ui2.py`，活面板 8300）：`typeof doLogin/pollLogin=function`、`viewPlans display=block`、**`planDlg.open=True`**、卡片登录按钮存在、画像显示 `#1 林锦阳（default）`、趋势 `峰值 211 分 · 合计 323 分 / 47 个视频`、`--- errors ---` 为空（无 pageerror）
- 端点契约（用不存在账号，**不触碰平台**）：`{"ok":false,"error":"账号不存在"}`
- TestClient 冒烟（`smoke_tabs4.py`，真实库快照还原 + 批次建改删清理）：**exit 0**，11 项 UI 检查全 ✔

## 五、踩坑与遗留风险

1. **（重要教训）`_PAGE` 是 Python 非原始字符串**：JS 里写 `'\n'`，Python 会把它转成**真换行**塞进 `_PAGE`，导致内嵌 JS 的单引号串跨行 → **整段脚本语法错误、全部函数 undefined**，现象却表现为"点击无反应 + 元素不可见"。修法：源码写 `\\n`（或改用无转义文案）。**`data/debug/check_js.py`（node --check 源码）查不出**——它读的是 Python 源码文本（`\n` 两字符合法），浏览器拿到的才是解码后的真换行。定位手段：headless `typeof 某函数` 为 `undefined` 即脚本整体挂了。
2. **无头自动登录依赖 passport 表单选择器**（`session/relogin._FORM` 标注"待核对"）：首次实况若选择器变更会返回 `unknown` → 正确转入手动，不误导为"成功"。需用户实况观察。
3. **登录预算 2 次/天**（`session/relogin.LOGIN_BUDGET_PER_DAY`，安全底线）：面板连点自动第 3 次会返回 `budget`（设计如此，防连爆）。
4. **手动登录轮询窗 3 分钟**：超时给提示，不自动重试。

## 六、交接块

- **进行中**：✅ 无。批次回归 + 一键登录 + 趋势图 + 画像显示/预填全部完成（95 全绿；headless 点验无报错；冒烟 exit 0）
- **下一步第一个动作**：用户 `Ctrl+F5` 强刷面板 → ①「📚 批次」点「＋ 新建批次」确认弹窗能开 ②「📈 统计」看新趋势图观感 ③「📇 账号卡片」点某账号「🔑 登录」试登录
- **阻塞点**：无
- **等用户**：① 登录按钮实况：**无头自动是否成功**？失败能否顺畅转手动并弹窗过验证码 ② 趋势图观感是否够用 ③（10-27 待办）平台画像读取、批次错峰、判分样本校准