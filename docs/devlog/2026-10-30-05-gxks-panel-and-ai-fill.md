# 2026-10-30-05 第二平台独立控制台 + 课后练习 AI 回填

> 平台事实看 `docs/gxks/gaoxiaokaoshi.md`；排期与治理看 `docs/gxks/PLAN-GXKS.md`（本次新增 §十二），本文只记“这次做了什么、为什么这么做、下一步谁做什么”。

## 1. 本次目标与结果

**用户目标**（原话）：

1. 「我想通过面板来点击做作业，调用 AI 来给出答案回填……我希望通过面板来操作，更可控，可随时开始和停，命令不方便」
2. 「面板上最好能显示列出是第几节第几题，答案是什么，方便查阅与核对。日志也要记录操作记录。后面测试成熟后可以转为自动提交」
3. 「这个面板和超星的是不交叉的，各自独立吧」

**结果**：代码级完成，**未在真实环境跑过 AI 回填**（诚实边界见 §5）。

| 项 | 状态 |
|---|---|
| 独立面板（`xxt gxks-panel`，8301） | ✅ 实机起过，`/`、`/api/state` 均 200，JS 过 `node --check` |
| 课后练习 AI 回填（读题→题库→LLM→回填→不提交） | ✅ 代码 + 合成 fixture 测试；⏳ 未实跑 |
| 逐题核对表（题号/题干/答案/来源/置信度） | ✅ |
| 操作记录（`gx_ops` 表 + 面板表格） | ✅ |
| 多账号（账号表 + 端口归属 + 下拉切换） | ✅ 结构就绪；账号仍需手改 `accounts.local.json` |
| 自动交卷 | ⛔ 默认关（按用户「测试成熟后再转自动」） |
| `pytest` | **442 passed**（基线 431 → +11） |

## 2. 关键决策与理由

### D1 面板与超星彻底独立，不做“一套面板两套数据”

- 新包 `src/xxt/panel_gxks/`（`app.py` + `page.py`），**不 import** `xxt.panel`；反向也确认 `xxt.panel` 零引用。
- 新命令 `xxt gxks-panel`，端口 **8301**（超星 8300），并行开互不干扰。
- 新表 `gx_ops` 落在**第二平台自己的库** `data/gxks_questions.db`（schema v2→v3，`CREATE TABLE IF NOT EXISTS` 原地升），不碰 `run_logs` / `xxt.db`。
- **唯一共用**的是 AI Provider 中心（`ai/llm.py` + `ai/quiz.py`）：Key 配一次两边都能用，且**不在这里另存 Key**。
- 理由：两平台的账号主键（`account_label` vs `accounts.id`）、业务表、页面语义全不同；合进一个 `create_app` 只会让每次改版互相牵连——这正是用户说的“不交叉”。

### D2 重活全部走子进程，面板只做“看 / 管 / 核对”

Playwright 同步 API 一阻塞就是几秒到几分钟，放面板进程里等于把界面锁死。所以面板只负责起停 + 读结果：`scripts/gxks_sections.py`（读节列表）、`scripts/gxks_work.py`（做题）、`run_gxks.py`（刷课）。

子进程 stdout/stderr **全部落盘** `data/logs/gxks-<类型>-<代号>-<时间>.log`：2026-10-25 那次“点了没反应”就是被 DEVNULL 吃掉了 traceback；这里把尾巴直接显示在账号卡上。

### D3 端口是账号属性，不是全局配置（本次修的真隐患）

写面板时发现：面板起 `gxks_work.py` 不传 `--port` → 脚本按 `[gxks] port_base` 猜 **9300**；而 `accounts.local.json` 里这个账号写的是 **9222**；`run_gxks.py` 又默认 **9222**。**三个脚本两个答案**，故障表现就是“点下去没反应”，而且日志不报错（因为它连的是没人监听的端口）。

修法：新增 `accounts.resolve_port(label, fallback=...)`，面板起子进程**一律显式 `--port <账号端口>`**，`[gxks] port_base` 降级为“账号表里查不到时的兜底”。

### D4 回填必须“真点击”，不能只置 `checked`

`tm_arr` 里没有标准答案，但 radio 带 `onclick="check_index(N)"` —— 它刷新右侧答题卡。只把 `checked` 置 true，页面看着选上了，平台侧仍认为这题没答。所以 `fill_answers()` 走 `locator.click()`，被遮挡时退回 `element.click()`（仍然触发 onclick，不绕过 `check_index`）；点完立刻 `read_checked()` 回读，不一致直接抛错，**不把“部分填好”当成功**。

### D5 答案来源分两档，AI 答案不跨账号借

- `standard_source='site'`（平台判分给出的客观答案）→ 可复用，谁做都一样；
- `standard_source='ai'` → **只限本账号**（PLAN-GXKS §1.1 N3「题干可共用、答案仍按账号隔离」）。

`lookup_answer()` 只查 `site`，并且要求选项文本对得上：平台若调过选项顺序，同一个 key 指向的内容就变了。

### D6 默认不交卷，交卷留给“测试成熟后”

- `[gxks] auto_submit = false`；面板上显示「自动交卷：关」，不给随手可点的开关（要开就改配置，动作可追溯）。
- 技术备注：`frmExam.submit()` 不经过交卷按钮的 confirm，所以自动交卷**能**做到；但没人在场就没人看答案对不对。
- 顺带守住 Q31：`gxks_work.py` 跑完**主动断开 CDP**，否则用户在 Chrome 里点「交卷」的 confirm 会被 Playwright 静默 dismiss。

### D7 未实测题型 → 整卷停

`[gxks] allowed_types = "2"` 只放单选（当前唯一实测过的）。遇到 3=多选 / 4=判断 / 另外 11 种 → `WorkError('structure')` + 退出码 4，整卷不做。宁可停也不猜：填错答案比不填更坏。

### D8 账号代号只能用 `[a-z0-9_-]`

`accounts._LABEL_RE` 就是这么定的，`display` 才是给人看的中文。两个脚本的 `--label` 都改成**缺省取账号表第一个**，避免默认值写成中文代号后，写 `gx_ops` / `gx_q_papers` 时抛「账号必须使用本地代号」。

## 3. 改动的文件清单

**新增**

| 文件 | 作用 |
|---|---|
| `src/xxt/adapters/gxks/work.py` | 开卷、读题、题库匹配、LLM 兜底、真点击回填、回读校验、交卷（默认不调） |
| `src/xxt/panel_gxks/__init__.py` | 包入口 |
| `src/xxt/panel_gxks/app.py` | 7 条路由 + 子进程启停 + `gx_ops` 读写 |
| `src/xxt/panel_gxks/page.py` | 单页 HTML + 原生 JS（4 秒轮询） |
| `src/xxt/store/gxks_ops.py` | `gx_ops` 操作日志（`ACTIONS` 词表 / `record_op` / `recent_ops`） |
| `scripts/gxks_work.py` | 单节课后练习 CLI（最后一行打印 JSON，供面板读） |
| `scripts/gxks_sections.py` | 节列表 + 练习解锁判定 + 学时状态 CLI |
| `tests/test_gxks_work.py` | 解析/匹配/回填 12 条（合成题干、真实结构） |
| `tests/test_gxks_ops.py` | `gx_ops` 6 条 |
| `tests/test_gxks_panel.py` | 面板 11 条（含“回包不含凭据”“端口取账号”） |

**修改**

| 文件 | 改了什么 |
|---|---|
| `config/settings.toml` | `[platform]` 切到新站；新增 `[panel]` 8301、`[gxks]`（`work_mode` / `auto_submit` / `port_base` / `allowed_types`） |
| `src/xxt/store/schema_gxks.py` | `GXKS_SCHEMA_VERSION` 2→3，新增 `gx_ops` 表 + 2 索引 |
| `src/xxt/store/gxks_questions.py` | 新增 `lookup_answer()`（只认 `site` 来源，选项文本参与校验） |
| `src/xxt/adapters/gxks/selectors.py` | 练习页常量段（`EXAM_START_URL` / `EXAM_TM_ARR_*` / `EXAM_RADIO_*` / `EXAM_SUBMIT_JS`） |
| `src/xxt/adapters/gxks/practice.py` | `_text` 提升为公开 `text_of`（回填侧要复用同一套文本归一化） |
| `src/xxt/adapters/gxks/accounts.py` | 新增 `resolve_port()`（端口归属账号） |
| `src/xxt/cli.py` | 新增 `gxks-panel` 子命令 + `cmd_gxks_panel()` |
| `tests/test_gxks_question_bank.py` | 硬编码版本号 `"2"` 改为读 `GXKS_SCHEMA_VERSION` |
| `docs/gxks/PLAN-GXKS.md` | §九 变更记录 + 新增 §十二（独立性对照表 / 四块 UI / 端口归属 / 回填链路与边界 / 已知限制） |

## 4. 验收证据

```
pytest -q                 → 442 passed, 1 warning  （基线 431，+11）
python -m py_compile      → gxks_sections.py / gxks_work.py 均 OK
node --check 提取的 JS    → 语法 OK
xxt gxks-panel 实机起     → [OK] 第二平台面板：http://127.0.0.1:8301
GET /api/state            → 200 {"accounts":[…"port":9222…],"accounts_file":false,
                                 "auto_submit":false,"sections":null,"work":null,"ops":[]}
GET /                     → 200, 11801 字节，含“单账号回落”提示
POST /api/work（未知账号）→ 400 未知账号：…（检查 data/accounts.local.json）
POST /api/stop（空闲）    → 200 {"ok":false,"error":"该账号没有在跑的任务"}
```

诊断脚本（只读、放 `data/debug/`、不叫 `test_*.py`）：`_probe_panel_gxks.py`（TestClient 走一遍路由）、`_page_gxks.js`（JS 语法检查用提取物）。

## 5. 诚实边界：还没证明的事

1. **AI 回填没在真实环境跑过**。测试用的是真实 DOM 结构与合成题干；“服务端真的建卷 → 读题 → 点选 → 回读一致”整条链要等一次实跑。
2. **面板起任务没实跑**：本次只验证到“路由正确、子进程参数正确”（用 mock 断言 `--port` / `--max-sections`）。真的点「做练习」需要 Chrome 在 9222 且已登录。
3. **多账号只做了结构**：账号表、端口归属、下拉切换都通了，但没有第二个账号实测过；账号增删改仍要手改 `accounts.local.json`。
4. **打包版起不了脚本**：`scripts/` 不在 exe 包内，`_start()` 会明确拒绝而不是默默失败。

## 6. 交接块

- **进行中**：无（本次改动已完成并有测试；面板进程已停，端口 8301 空闲）。
- **下一步第一个动作**：让用户起 Chrome（`--remote-debugging-port=9222` + 已登录的专用 user-data-dir）→ `xxt gxks-panel` → 点「刷新节列表」→ 在 2.2 / 2.3 点「做练习」，看首份逐题表与 `gx_ops` 记录。
- **阻塞点**：无技术阻塞。实跑需要用户提供一次人工登录的 Chrome 窗口（且不要同时跑其它脚本，避免 Q31）。

## 7. 用户下一步操作清单

1. **起浏览器**（和以前一样，但注意端口要和账号表一致——当前单账号是 9222）：
   `chrome.exe --remote-debugging-port=9222 --user-data-dir="D:/xxt-profiles/jia"`，在里面登录新站。
2. **起面板**（在**你自己的 PowerShell 窗口**，AI 工具沙箱里起的会被回收）：
   `.venv\Scripts\python.exe -m xxt.cli gxks-panel` → 浏览器开 `http://127.0.0.1:8301`
3. **点「刷新节列表」**：约 1 分钟，出「节进度」表（应能看到 2.1/2.2/2.3 已完成、2.4 起未学习）。
4. **点 2.2 或 2.3 的「做练习」**：会开卷 → 出答案 → 回填 → 停下。**交卷请你自己在 Chrome 里点**（脚本跑完会自己断开，不会吞确认框）。
5. **核对完回来告诉我**：逐题表里哪题答案不对、题干有没有截断、有没有哪节报「题型未实测」。这三样决定我下一步补什么。
6. **要不要接自动交卷**：等 2~3 节实跑核对无误后，你说一声我把 `auto_submit` 打开。
