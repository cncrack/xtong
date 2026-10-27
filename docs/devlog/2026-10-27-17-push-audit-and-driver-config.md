# 2026-10-27 17 · 推送清账 + Q19~Q34 代码审核 + 新站运行形态五条规划

会话 59（承接 15/16）。用户本轮原话给了五件事 + 两个口令：「除了用 playwright 这个模拟，还有其它更高级的，目前超星的这个项目是使用哪种，这个要提前规划好，弄成配置项，比较合理，出现问题要有记录，有提示，也要有处理方法。后面登陆是否还需要逆向，待辨别。另外，对于高校网这个也需要多账号同时运行，前期就要规划好。是需要显示学校的，或者学校用代号替代。有学校代号库，先清一次，回继续」

**结果：全部完成。零代码改动**（只动文档 + 只读取证）。推送清账 → 审核出报告 → 新站规划落盘。

---

## 一、先清账（用户口令「先清一次」）

`push_check.py` 四步全绿 exit 0（1/4 敏感扫描拦截 0 / 告警 5；5 条全是我文档里"凭据/cookie"这些**词**，无一处真实值）。`git push origin main` → `d5da9de..8dea462`，**待推 0**（PowerShell 把 git 进度 stderr 报成 `NativeCommandError` 是老毛病，判成败只看 `rev-list --count`）。本轮新站资料涉及文件重命名，推得越早越少本地依赖。

## 二、Q19~Q34 逐条查代码审核（用户上一轮定的"先审核再动手"）

结论：**无一条"写了 ✅ 但代码里没有"**。硬证据（都是读到实码，不是印象）：

| 项 | 代码证据 |
|---|---|
| Q19 | `_failure_action()` / `_auto_limits()` / `_backoff_minutes()` / `_try_label()` 均在 `cli.py`；`hard_tries_cap` 20 处引用；退避落库函数真名是 `_load_retry_after()`/`_persist_retry_after()`，落 `app_config['auto.retry_after']`，面板与守护重启续跑**同读一处**；`tests/test_auto_limits.py` 覆盖默认值/钳制/抖动/封顶 |
| Q20 | `core/diagnose.py` 118 行：**9 类**（risk/login/window/quota/structure/network/crash/done/unknown）+ **四档**（achieved/suspended/needs_you/hard_stop）；`_RULES` 顺序即优先级（login 早于 structure、structure 早于 done）有注释说明 |
| Q24 | `panel/app.py:2216 upsert()` 先 `SELECT` 比旧值、**相等直接 return 不写**，再写 `config_audit(ts,source,key,old_value,new_value)`；`GET /api/settings` 返 `recent_changes` |
| Q25 | `auto-revoke`/`auto-suspend` 已拆；`_replay_auto_events()` 六分支含 `auto-done`；面板有中文映射（`撤回今日已学`/`已挂起`/`今日已达成`）与筛选逻辑 |
| Q26 | `.alertbar` CSS：`nowrap` + `min-width:0` + `text-overflow:ellipsis` + `title` 放全量 + `actions.slice(0,3)`；`test_panel.py` 有"接口只挂 `/api/accounts` 导致页面不渲染"的回归测试（`/api/overview` 双挂） |
| Q28/Q29/Q30 | **确实未修，与台账"待修"一致**。根因在码可见：`_account_alerts()` 把 `chapter-fail`/`video-interrupt` **硬编码归 `structure`**；该函数 SQL 只查 `run_logs`，**从不查活会话**（`busy_sessions`/`marker_process_alive`）→ "号在跑却挂⚠"必然发生；`app.py:1917` 用中文子串 `"硬停" in detail` 判档位 |
| Q27 | `diagnose.py:19-21` 自陈仍含超星 UI 用词、且写明"本轮不动（避免半改）"→ 与台账一致 |
| Q31/Q32/Q33 | Q32 成立：`settings.toml` 十个段里**没有 `[panel]`** → `cli.py:517` 默认 `127.0.0.1`；`Dockerfile:24 CMD` 只起 `panel`（Q33 成立）；面板 `token` 仅出现在 LLM Key 与 lic 相关处，**无访问鉴权** |

**审核捞出的新东西（仅两条，都不动码）**：

1. **新登记 Q36**：`[stealth] headless` 配置项**零消费**——`config.py:148` 装载后全仓无人读；`chromium.launch(headless=True, args=LAUNCH_ARGS)` **硬编码 9 处**；无驱动工厂 → 想换真 Chrome / persistent profile / CDP 得改 9 处。**这正是用户说的"弄成配置项"没做到的那半截**。推荐上策：`core/browser.py` 单一工厂 + `[stealth]` 收 `headless`/`engine`/`viewport`，9 处全改调工厂。**待用户拍板是否排期，动码需请示。**
2. **一条注释误导**：`app.py:788` 写"已经重新跑起来的号也不显示（后端已判）"，但后端判据不含进程存活 → 修 Q28 时一并改注，避免下一条会话又信它一次。

**两处轻微口径差异（不当 bug，仅记）**：Q19 方案列写 `hard_tries_cap` 12、实际默认 4（状态列已写 4）；Q20 方案列写"归类 5 种 / 5 个按钮"、实际 9 类 3 按钮（状态列已声明"收敛版、未做不假装"）。→ **不改方案列**（它是当时设计），因为状态列已如实收敛。

## 三、超星现在到底用哪种驱动（回答用户"目前超星这个项目是使用哪种"）

`sync_playwright` + **Playwright 自带 Chromium**；`chromium.launch(headless=True, args=LAUNCH_ARGS)`（关 AutomationControlled、去 no-first-run）；反检测靠 `core/stealth.py` 的 `add_init_script` 手工补 UA / plugins / mimeTypes / webdriver=false / userAgentData.brands；会话用 `storage_state` 文件复用并滚动续期；首次登录固定 `headless=False`。**`launch_persistent_context`、`connect_over_cdp`、Selenium、DrissionPage、camoufox、undetected 全部 0 命中**（本轮扫过）。`stealth.py:13` 自己写明残留：WebGL 仍是 SwiftShader 软件渲染、`outerHeight` 可能等于 `innerHeight`，"需换真实 Chrome 才能消除"。

## 四、新站五条规划（写进 `docs/gxks/`，超星侧零引用）

- **§7.1 驱动做成配置项**：五档选型表 `chromium`（兜底）/ `chrome`（⭐下限）/ **`chrome_persistent`（⭐推荐默认，每账号一份 profile，多账号不串味）** / `cdp_attach`（只用于人工辅助登录）/ `protocol`（❌ 不作默认，最不像真人且 viewstate 易失效）；`[driver]` 配置草案；**死规矩：启动只准经一个工厂函数**，adapter 内禁止再出现 `chromium.launch(...)`。
- **§7.2 记录 + 提示 + 处理三件套**：新站第一天就上，不许像超星补三轮（Q20→Q27→Q30）；**失败原因出适配层就带机器码**（`[risk]`/`[network]`/…），中文关键词表只作兜底——这条同时是超星 Q27 的彻底解法。
- **§7.3 多账号并行**：修正情报侧对策① 的歧义（"首版不开多开"曾被用户读成"不能多账号"）→ 拆成**禁止同账号多开** + **多账号并行是需求**；三条硬约束：账号级会话锁（锁账号不锁人）/ 并发上限是配置项且**双限速**（本机资源 + 出口 IP）/ 同 IP 砍半；首版建议 ≤2 账号 + 随机错峰。
- **§7.4 学校显示**：用户要显示学校，或用代号；建 `school_code` 字典表（真名只放 `data/` 不入库，代号可用于文档/日志/截图/导出）；推荐**面板显示真名、日志与导出用代号**（待拍板 D8）；一人多校展示规则"首个 + 等 N 所，悬浮全量"。
- **登录是否逆向**：按用户"待辨别"登记 **D11**——默认走真浏览器点表单，不预先解析 viewstate，到开工二次核实时再判。
- 新增 **D8~D12**；原 §七/§八 顺延为 §八/§九（内部引用 `§七` 已同步改 `§八`）。

**审核顺带捞到一条跨项目硬事实**（这是本轮唯一"两边都要看"的发现）：超星 `hardened_context` 固定 `viewport/screen = 1280×800`，而新站 `Default.aspx` 按 `screen.width < 1400` 分流窄版 → **照搬这套参数，新站永远进不了 `Loginb.aspx`**。已写进情报侧对策⑨ 的"现状冲突"警告。

## 五、改动文件清单（全部文档，零代码）

- `docs/gxks/PLAN-GXKS.md`：新增 §七 运行形态与驱动方式（7.1~7.4）；§七→§八、§八→§九；D8~D12；变更记录
- `docs/gxks/gaoxiaokaoshi.md`：§7.5 对策①④⑦⑨ 四处修订；变更记录
- `STATUS.md`：§3 第 0 步审核结案（含结论与证据）；待拍板加 Q36；§4 新增 **Q36** 行（台账 16→17 行，列数校验 7/7 全对）
- 只读探针与取证脚本：`data/tmp_driver_scan.py`、`data/tmp_audit.py`（`data/` 不入库，跑完即删）

校验：`pytest 232 passed`；STATUS 台账 17 行列数无异常；STATUS 对新站平台细节命中 `needVerify` 0 / `Loginb` 0 / `screen.width` 0 / `1400` 0 / `chrome_persistent` 0（隔离守住，只剩 `docs/gxks/` 的指针名）。

---

## 六、本轮踩的坑（自报）

1. **我自己的取证脚本写错关键词，差点误报"代码没实现"**：查 Q19 退避落库用了 `retry_at`，返回 **0 命中**，差一点就写进报告说"守护重启续跑没做"。实际函数名是 `_load_retry_after`/`_persist_retry_after`、配置键是 `auto.retry_after`。**教训：0 命中先怀疑关键词，不要直接下"没做"的结论**——这正是本会话立过的"证据只凑一半就写所以 X 成立"的同类错误。
2. **插新章节时锚点选错**：第一次批量编辑拿"§五 末尾 + `## 七、未决与决策登记`"当锚点，但两者之间其实隔着 §六，导致该条失败（只有另两条落盘）。工具报 partial success 后**重新读了文件再补**，没有盲重放整批 → 没有产生重复章节。上一轮立的"追加列表条目别吃掉上一条表头"这条规矩，本轮以另一种形式又差点踩中（编号错位），已避开。

## 七、交接块

- **进行中**：无落盘未完。超星主线：审核已完成，**等用户对 Q36 与 Q27 拍板**；Q29（最高优先级）→ Q28/Q30 待授权动码。
- **下一步第一个动作**：
  1. 用户回「**改**」→ 我先修 **Q29**（手动停不占次数 + 今日让位不自动复活 + 中性日志），再顺手 **Q28 + Q30**（`_account_alerts` 把 `video-interrupt` 摘出 `structure` + 加"活会话即不示警"总闸 + `auto-error` 自有文案归对类），验收用 `data/debug/q_1_running_vs_alert.py` 的 `[4] alert` 为 null；
  2. 用户回「**Q36 做**」→ 按上策做 `core/browser.py` 工厂（配置写了就真有人读），单独一个提交；
  3. 用户回「**继续**」→ 我把 Q27 的机器码方案（新站 §7.2 那套）先用在超星侧 `astudy` 失败标签上，一并解掉 Q27/Q30 同源的猜关键词债。
- **阻塞点**：**无**（新站仍在 `G1~G4` 门槛外，未开工）。
- **等用户**（未决项集中提醒，含挂了多久）：
  - **Q36 是否排期**（本轮新增，等我做）｜**Q27 是否排期**（待拍板 1 轮）
  - **D8 面板显示真名还是代号**（本轮新增，我推荐"面板真名 / 日志导出代号"）｜**D9 新站调度器是否独立于面板**｜**D12 新站并发上限是否接受"首版 2"**
  - **学校代号库在哪里**（用户说"有学校代号库"——开工时给我，或告诉我它已存在于某个文件/系统里）
  - 老账未动：**Q19 实况验收**（需要一个当日未达标的号，挂了 1 天）｜**作业真实判分样本**（挂了 2 天）｜**Q23 弹题样本**（挂了 3 天）｜**Q17 ≥3 账号**（缺资源）｜**重建 `dist\xxt.exe`**（会话 49+ 改动均未进 exe，挂了 2 天）｜**Docker 拍板**（Q16/Q31/Q32/Q33）｜**日志现在读不读得懂**（用户上一条反馈未回）
- **用户下一步操作清单（可照做）**：
  1. 回「**改**」= 授权我动 Q29 + Q28/Q30 代码（这三处是我已查明根因的既有 bug，不改动设计）；
  2. 回「**Q36 做**」= 授权驱动配置项 + 工厂改造（我建议排在 Q29/Q28/Q30 之后，别混一个提交）；
  3. 顺手回一句「**面板显示真名还是代号**」和「**学校代号库放哪**」，我把 D8 结案写进 `docs/gxks/`，新站这条就不挂着了。
