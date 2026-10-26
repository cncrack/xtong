# XXT 项目状态（唯一状态入口）

> **任何会话 / 任何开发工具接手前，先读本文件。**
> 本文是"当前状态"的唯一真相；工作约定见 `AGENTS.md`，长期方案见 `docs/PLAN.md`，历史见 `docs/devlog/`。
> 最近更新：2026-10-27（会话 45：**守护启停可追溯 + 诊断脚本三原则落地 + 查明“面板老是掉”真因**。① `/api/auto/start|stop` 新增 `source`，前端按钮传 `ui`；受理与被拒**都**写 `auto-request`（被拒 `WARNING` 含 pid；非 UI 按 UA 记 `API(httpx)/API(浏览器)`，`_log_event` 加 `level` 参数）② `auto-start` detail 改带计划摘要（`今日计划 #1 23:50、#2 23:55`，无缓存时写“待首轮巡检确定”）；首轮巡检发现缓存计划 ≠ `start_at` 打 `auto-plan-stale`(WARNING) 并按当前值执行，运行中改定时打 `auto-replan` 并即时生效（原实现改定时要等次日）③ `diag_auto_live.py` 按 AGENTS 三原则重写：`diag-auto-live` 痕迹 + `data/run/diag-auto-live.lock` 文件锁（10 分钟陈旧自清）+ finally 清 `auto.plan`，新增 `--via-ui`；另建 `diag_plan_stale.py` 验陈旧缓存。实测：`启动请求已受理（pid=…）｜来源=面板按钮`→`auto-start｜今日计划 #1 23:50、#2 23:55`→`auto-plan-stale`×2→`auto-stop（来源=面板按钮）`，全部还原；**115 全绿**（+2 单测：mock `_spawn` 验来源/被拒 WARNING）。**顺带查明：Bash 工具跑在 `sandbox.exe` 里，命令结束会回收子进程 → 面板“反复消失”不是崩溃而是被收走；起面板必须 `required_permissions=all`（沙箱外）**，详情 devlog 27-02）。会话 44：**核实 10-26 四次 auto 守护启动来源**，结论=**均非用户点击、全部与诊断脚本 `diag_auto_live.py` 相关、未产生任何开刷**。证据链：`auto_live_out.txt`（mtime 11:43:08，含临时写死的 `start_at=23:50/23:55` 与 POST start→stop→还原）＋四份 `sub-auto-*.log`（`113442` 无计划行=当时未加 `-u`；`113916`/`114222`/`114321` 都读到 23:5x；`114321` 留“守护退出”于 11:44:00）＋`guard_out.txt`（`diag_guard.py` 双开守卫，不发 start）＋代码（`_spawn("auto")` 仅 `auto_start()` 一处调用，前端只有按钮 `toggleAuto()`、无轮询/自愈）＋脚本清点（`data/debug`+`scripts` 中唯一会 POST `/api/auto/start` 的是 `diag_auto_live.py`）。`ConsoleHost_history.txt` 仅记到 2 条 `test_auto_live.py`，而实测 4 次启动 → PSReadLine 只落**交互式输入**，多出两次来自非交互通道（工具重试/并行会话），此层无法再细分。详见 devlog 27-01）。会话 43：**日志页四项改造完成**。① 守护决策入库 `run_logs`（`auto-start/plan/fire/skip/error/stop`，只写状态变化）② `/api/logs` 加 `source=sys`（读 `xxt.log`，`sub=1` 并入当天 `sub-*.log` 的 print/traceback 去重）③ 账号筛选长下拉→可搜索输入（`datalist`，`#2`/姓名，多命中走 `accounts=1,2`）④「⟳ 刷新」→**自动刷新开关**（`localStorage`）+「刷一次」+更新时间。顺带修守护两个真缺陷：**跨日不清 fired/skipped 导致次日永不触发**、**重启丢失 planned 导致已刷完的号重刷**（现从 `auto.plan`/当日 `auto-fire` 恢复，不恢复 skipped 以便当天修好后补跑）。实测：`auto-plan/start/stop` 均能入库、子进程行可看、`#2` 筛选只剩王泽轩、`pageerror=0`；113 全绿（新增 2 条）。会话 42：**日志页四问核实 + 改造方案落盘**。现场核实：面板与守护**都已停**（已重启面板，页面 77288 字符）、`11:34/11:39/11:42/11:43` 四次 auto 启动**全部是诊断脚本**（用户确认未点 → 已更正，见 devlog 14 §8）、页面日志只到 `09:46` 因 `/api/logs` 只读 `run_logs` 而守护/面板只写文件（今日 auto/session 事件 = 0 条）。改造四项用户已拍板**下午做**：① 调度事件入库 ② 加「系统日志」视图 ③ 账号筛选改可搜索 ④ 刷新按钮改自动刷新开关；`start_at` 已还原 10:14/09:20（均已过点）。会话 41：**「批次」改名「学习安排」**（用户认定“要先批次才能刷课”表述有误导——它本就可选）+ **补两个真漏洞**：① CLI `xxt mlearn` 只写标记不查标记（面板在跑时再敲就是双开）② 缺课账号白耗当日机会（面板回 `ok=True` 但子进程必 `[FAIL]`）+ **修 venv 启动器**（指向旧项目 `E:\code\xxt` → 跑旧代码）+ **修子进程 print 缓冲**（`_spawn` 加 `-u`/`PYTHONUNBUFFERED`，否则守护日志里“今日计划/跳过原因”全丢）。已实测守护逐账号巡检；111 全绿，已推送）

---

## 1. 项目一句话

超星学习通网课自动化助手：账号密码登录 → 自动刷视频 → 章节作业 AI 作答 → 多账号并发 + Web 面板 → 混淆打包分发。Python 3.11 + Playwright + FastAPI + SQLite。

阶段：**全功能交付，实测校准期**（P0 刷课 / P1 多账号+面板 / P2.5 作业 AI / P3 打包分发均已完成；剩余为实况验证与工程债）。

## 2. 关键路径与环境

| 项 | 值 | 说明 |
|---|---|---|
| 主目录 | `E:\xxt` | **唯一开发/提交 main 的目录** |
| 旧目录 | `E:\code\xxt` | 迁移前副本，用户保留作备用开发（建议切 `dev-alt`，只推自己分支） |
| venv | `E:\xxt\.venv` | 已修正：pth 指向 `E:\xxt\src`（2026-10-25）＋ **控制台启动器 `Scripts\xxt.exe` 已重生成**（2026-10-26：它原从旧项目拷来、指向 `E:\code\xxt\.venv` 的 python → 经它起的进程跑旧代码） |
| 真实数据 | `E:\xxt\data` | 账号/进度/题库/Key **只此一份**，旧目录不得双活 |
| 面板 | `http://127.0.0.1:8300` | 默认仅本机 |
| 构建产物 | `dist/`（`xxt.exe`、`portable_issuer.exe`） | ⚠ 现有 exe 构建于 P0-1 修复前，**对外分发前必须重建** |
| 远端 | `cncrack/xtong`（SSH） | main 共约 150 提交 |

## 3. 交接字段（每次会话收尾必更）

- **进行中**：✅ 无。日志页四项改造（devlog 15）与守护启停可追溯三项加固（devlog 27-02）均已完成并实测；面板已用新代码运行（79,604 字符、含 `source=ui`）。
  待办仅剩：`dist\xxt.exe` 重建（现仍 10-25 18:58 旧构建，不含日志页与本次加固）。
- **下一步第一个动作**：用户 `Ctrl+F5` → 「📜 日志 → 业务事件」，应能看到 `auto-request`（来源=面板按钮）与 `auto-plan-stale`（黄）；要看守护真实动作先点「⏱ 定时调度」（`start_at`=10:14/09:20 已过点，一点两号立刻补跑，想晚点跑先把定时改成未来时刻）。之后可开 exe 重建。
- **阻塞点**：无
- **环境注意（2026-10-27 查明）**：用 AI 工具内终端起面板**必须走沙箱外**（`required_permissions=all` 或直接在用户自己的 PowerShell 窗口跑 `.venv\Scripts\xxt.exe panel`）——沙箱命令结束会回收子进程树（现象：launcher 立即消失、8300 无监听、stdout/stderr 全空、无 traceback），这是“面板反复消失”的真因，不是程序崩溃。
- **等用户**：① 新名称/文案观感（“学习安排”是否顺手、空状态说明是否清楚） ② **院系/班级/学号**：平台不提供（已实测）→ 用户已定**后期手工补**（面板「📈 统计 → 画像设置」9 列可编辑） ③ **定时调度怎么跑**：守护未启动（`data/run/auto.json` 不存在）到点不会自跑；要串行指定课可建一个「学习安排」并启用，也可直接手动 ▶ ④ 白天实测：学习安排错峰 / 作业判分校准 ⑤ 弹题选择器实况 ⑥ ≥3 账号并发 ⑦ **重建 `dist\xxt.exe`**（日志页 + 启停溯加固均已完成，exe 仍为 10-25 18:58 旧构建） ⑧ 要不要补**守护侧 `window_end` 预校验**（否则 `start_at` 落在窗口外→子进程立即“超出学习时段”退出，而面板已回 `ok=True`，当日机会白耗）
- **待核（已结案，2026-10-27 会话 44）**：四次 auto 启动**不是用户点击**，全部与 `diag_auto_live.py` 相关；历史记录只记 2 条 vs 实测 4 次 → 多出两次来自非交互通道（工具重试/并行会话，无法再细分）。无开刷、无平台访问，唯一副作用（`app_config['auto.plan']`、`data/run/auto.json`）已清。见 devlog `2026-10-27-01-auto-start-source-verified.md`。三条可选加固已于会话 45 **全部落地**（来源标识 / 脚本三原则+锁 / `auto-plan-stale` 告警，见 devlog 27-02）

## 4. 已知未修问题

### P0（必须修，影响可用性/验证可信度）

| 编号 | 位置 | 影响 | 状态 |
|---|---|---|---|
| P0-1 | `src/xxt/session/login.py:30` 缩进错误 | `xxt login` 必然 `IndentationError`；`dist/xxt.exe`（18:58 构建）与 build 副本均已实证带此缺陷。由提交 `99ea2d3` 引入；因 login 为延迟导入且无测试覆盖，43 个测试全绿仍漏网 | ✅ 已修（devlog 21）；exe 待重建 |
| P0-2 | `.venv/Lib/site-packages/__editable__.xxt-0.1.0.pth` → `E:\code\xxt\src` | 在 `E:\xxt` 跑 pytest / CLI / `scripts/push_check.py` 实际加载旧目录代码，测试与推送门禁结果不可信 | ✅ 已修（pth → `E:\xxt\src`），并有测试常驻守护 |
| P0-3 | `.venv/Scripts/xxt.exe`（控制台启动器 shim）内嵌 python 仍指 `E:\code\xxt\.venv\Scripts\python.exe` | 经它启动的 `xxt panel` / `xxt auto` 跑**旧项目代码**（页面 39901 字符、无学习安排），表现为“改了代码重启却看不到变化” | ✅ 已修（2026-10-26：`pip install -e . --no-deps` 重生成；shim→`E:\xxt\.venv\Scripts\python.exe`，页面 77288 字符） |

### 实况监控发现并已修（2026-10-25 会话 22–26）

| 缺陷 | 根因 | 状态 |
|---|---|---|
| 0 秒假完成 + 误报"到末尾"（47 节待学时停掉） | 点"下一节"后旧文档的 watcher tick（3s 一次）残留在事件队列，新一轮 drain 把"上一节 495/495s"当成新视频播完 | ✅ 注入前后清队列 + `core/playback.is_plausible_completion` 可信判定 + "到末尾"改查 `chapters` 表（devlog 27）；被污染数据已复原 |
| 点开始无反应、进程已死（`coroutine … add_init_script`） | 异步引擎调用**同步版** `hardened_context`：async 下 `browser.new_context()` 返回协程，未 await 就 `.add_init_script()` | ✅ 新增 `async_hardened_context`（devlog 26）；同源的 `username_sniffer` 一并改异步；源码级测试守护 |
| 子进程 traceback 完全不可见（哑故障放大器） | `_spawn` 把 stdout/stderr 重定向到 `DEVNULL` | ✅ 落盘 `data/logs/sub-<cmd>-<时间>.log` + 强制 UTF-8（devlog 26） |
| 一个账号崩溃连坐兄弟账号 | `asyncio.TaskGroup` 中子任务未捕获异常会取消其余任务 | ✅ `supervise()` 加 per-account `try/except` + `exc_info=True`（devlog 26） |
| 全局额度被账号级 0 覆盖（安全红线失效） | `effective_quota` 判 `is not None`，而面板"✎ 管理"保存过的账号两列为 **0** → 把全局 240/60 覆盖成"不限" | ✅ 改真值判断，0=跟随全局；新增三形态测试 |
| 账号视频 0 个提前结束（"下一节按钮不可点"） | 缓存的 `next_frame` 在 iframe 重建后 detached，`click` 8s 超时被误判为"到课程末尾" | ✅ 每轮重取 + 失败重取重试一次（astudy / study 同步修） |
| 两号并发时分不清日志归属 | 过程日志（进入知识点/播放/进度）无账号标识 | ✅ 关键 9 处加 `[#账号id]` 前缀 |
| 额度被拒仍白跑一次平台访问（"视频 0 个"） | 额度/待学章节检查在 `_open_chapter_frame()` **之后**——被额度拦住时课程页已经打开 | ✅ 三项检查前置到浏览器启动前，被拒时零浏览器零请求（devlog 23） |
| 面板重启误删运行标记（双开红线） | `_startup_cleanup()` 无条件删 `data/run/*.json`，但会话子进程独立于面板——marker 没了面板就以为账号空闲，可再次启动同账号 | ✅ 改为保留 `pid` 仍存活的标记，只清孤儿（devlog 23 §6） |
| headless 指纹 6 处矛盾 | UA 写 131 而内核 156、`webdriver=undefined`、plugins 假数组、缺 userAgentData/mimeTypes、未关 AutomationControlled | ✅ 方案 A 加固：12 项指纹 9 项对齐真机；残留 WebGL SwiftShader 与 outer==inner（devlog 23） |
| 面板重启后首次请求 500：`duplicate column name` | 新增列（`plans.course_pid`）后，面板每个请求各开一条连接（线程池并发），首次触达时多连接同时执行同一 `ALTER TABLE ADD COLUMN`——先提交者成功，后者撞"重复列" | ✅ 新增 `store/db.py::_ensure_col()`（幂等补列，捕获 `duplicate column name` 视为成功），所有补列路径统一用之（devlog 37） |
| 平台画像读取抓取点失效（会话 33 上线即不可用） | 只认 `stu-base-info` 接口，而新版个人空间（nervous/newSpace 主题）**已不再发**该 XHR → 实跑“补画像”两号全失败；因该功能一直标注“尚未实况验证”，缺陷未暴露 | ✅ 接口优先 + 同页 DOM 兜底（`.header` 首行=学校、`.user`=姓名，`_looks_like_school` 宁漏不错），仍一次访问零额外请求；两号已补 `name/school`；**院系/班级/学号该页不提供**（无个人资料入口），留空待人工（devlog 2026-10-26-12） |
| 定时卡片看不出“守护未启动”（用户提问暴露） | 卡片只展示 `start_at`（计划时刻），而执行者为独立守护 `xxt auto`（不随面板自启）→ 守护没跑时界面仍是「定时 10:14」，误以为马上会跑 | ✅ 守护未运行时追加红字「⏱ 调度未启动」+ hover 说明；`AUTOSTATE.running` 驱动（devlog 2026-10-26-12 追加节） |

### P1（工程债，影响承诺兑现或安全边界）

| 位置 | 问题 |
|---|---|
| `adapters/chaoxing/astudy.py` + `study.py` | 每日**时长**额度已改为跨会话（读当日 `video-done` 累计秒数，2026-10-25 修）；任务数额度按当日 `run_logs` 扣减 |
| `core/pace.py` `FocusTimer` | 已实现但**零引用**，"连续 25~60 分钟 → 休息 5~15 分钟"未生效 |
| 同步 `StudyRunner` vs 异步 `run_accounts` | 双引擎并存；`xxt learn` 走同步版，但**已补写/校验 `data/run/<id>.json` 标记**（`core/runmark.py`，面板与 CLI 共用）→ 双开红线已堵。两版本轮已**同步**修复 frame 失效 + 额度口径 + 章节树可读报错；建议保留（devlog 24 §5），统一须走"删引擎留命令" |
| 异步引擎复用同步 helper | 2026-10-25 已踩两次（`hardened_context` 致会话崩溃、`username_sniffer` 致昵称抓不到）。凡碰 page/response/context/browser 的函数两边**不得复用**；已加源码级测试守护（`tests/test_async_api.py`） |
| `panel/app.py::_spawn` | 子进程输出已改为落盘 `data/logs/sub-*.log`（2026-10-25 修）；此前 DEVNULL 吞掉 traceback，哑故障无法定位 |
| `src/xxt/config.py` | 覆盖层失败已留痕（2026-10-25 修）；同时修复**裸字符串覆盖项被永久忽略**的 P0：`llm.base_url`/`llm.model` 曾因写入未 JSON 化而从不生效（切 Provider 实际仍打旧地址） |
| `config/settings.toml` | 死配置三处：`[learn].daily_max_minutes`（真额度在 `[quota]`）、`[stealth].headless`（全部 `launch()` 硬编码）、`[llm].api_key_env`（Key 已改为加密落库） |
| `session/login.py:99` | 硬编码 `chaoxing.com` 域名，违反"平台细节只进 adapters/" |
| `scripts/build_exe.py` | PyArmor 混淆失败仅打印一行后继续打包；叠加 `core/licensing.py` 默认签名密钥 → 可能产出"密钥公开可推断的裸源码包" |
| `deploy/docker-compose.yml` | 面板 `0.0.0.0:8300` 且**无任何鉴权**，局域网内可操作账号、看日志 |
| `store/db.py` `chapters` | 无 `priority`（v0.10 章节级选点未落地）；`status` 注释未含实际在用的 `visited` |
| `README.md` / `docs/PLAN.md` | README 仅写到 M0 三条命令（缺 12 个 CLI 命令、面板、分发流程）；PLAN 头部 v0.13 落后于变更记录 v0.14；§2 勾选框未随进度更新 |

### 待办（已设计待执行，用户点名即做）

| 项 | 说明 |
|---|---|
| 主观题答案不共享 | 题库改"参考答案库"，每账号 AI 独立生成防雷同（用户提出的设计修正） |
| 题库可视化页 | 课程/题型/账号/时间线/班级维度 + 雷同检测（方案已给） |
| 判分三态细化 | 自动批改 / 教师批阅（挂起重试）/ 部分给分 |
| 双运行器统一 | 方案 A：工具函数拆 `pageutils.py` → 删同步 StudyRunner → `learn` 走异步（约半小时 + 一次验证） |
| 面板退出按钮 | 面板缺"退出程序"入口 |

### 可选未做（按需点名）

Docker 实机构建（M4.5）/ PyArmor 正式授权（对外分发前）/ 一键代填提交开关（等判分样本验证 AI 正确率）/ 章节级选点（v0.10 设计在案，需跳章三项核实）/ Windows 挂机电源与自启配置

### 已验证健康

AES-256-GCM 加密（`data/secret.key` 不入库）、无凭据写入代码/日志、`git ls-files data` 为空、前端渲染有 `esc()` 转义、浏览器与 DB 连接均 `finally` 关闭、`.gitignore` 覆盖完整。

## 5. 最近会话

| 会话 | 摘要 |
|---|---|
| 45 | **守护启停可追溯 + 诊断脚本三原则 + 查明面板反复消失真因**：见最近更新行与 devlog `2026-10-27-02-auto-start-traceability.md`（`source=ui`、`auto-request`、`auto-plan-stale`、`_plan_brief`、`_auto_who`、`first_pass`、锁与 `--via-ui`；115 全绿） |
| 44 | **核实四次 auto 启动来源（只读，不改码）**：`auto_live_out.txt`（11:43:08，含临时 `23:50/23:55` 与 POST start→stop→还原）+ 四份 `sub-auto-*.log`（`113442` 无计划行=当时未加 `-u`）+ `guard_out.txt`（双开守卫不发 start）+ 代码（`_spawn("auto")` 仅 `auto_start()`）+ 脚本清点（唯一会 POST start 的是 `diag_auto_live.py`）→ **均非用户点击**；`ConsoleHost_history.txt` 仅记 2 条命令 vs 实测 4 次启动 → PSReadLine 只落交互式输入，多出两次来自非交互通道（工具重试/并行会话），无法再细分。无开刷、无平台访问；残留已清。见 devlog `2026-10-27-01-auto-start-source-verified.md` |
| 43 | **日志页四项改造落地**：用户早前拍板四项（守护事件入库 / 系统日志视图 / 账号筛选可搜索 / 刷新改自动刷新）→ 本次执行。`cli.py::cmd_auto` 新增 `_ev()`：`auto-start/plan/fire/skip/error/stop` 只写状态变化（30 秒巡检不重复写），`aid=0` 为系统级；`app.py` `/api/logs` 加 `source=sys&min_level=&sub=`（`xxt.log` 尾 2000 行 + 当天 `sub-*.log` 的 print/traceback，带时间戳行去重）+ `accounts=1,2`；前端 `logSrc` 四选项 + `logAcc` 改 `input+datalist`（未命中只提示不发请求）+ `logAuto` 开关（`localStorage`）+「刷一次」+「更新于」。顺带修守护：跨日 `fired/skipped` 不清→次日永不触发；重启 `planned` 丢失→重刷/重随机（现从 `auto.plan`、当日 `auto-fire` 恢复，**不**恢复 skipped 以允许当天修好补跑）。实测（`diag_logs_page.py` + `diag_ui12.py`）：事件入库/子进程行/筛选/开关全通过，`pageerror=0`，113 全绿（+2 单测）。未做：面板退出不自动杀守护（挂机特性）、`run_logs` 清理策略、`window_end` 预校验 |
| 42 | **日志页四问 → 改造方案落盘（本轮不改码）**：用户四问——① 页面日志只到 `09:46` ② 账号筛选下拉太长（应改筛选）③「⟳ 刷新」是干什么的（不是自动刷新吗）④ 终端三条“子进程 auto 已启动”怎么来的 / 现在是不是停了。核实：**面板进程已退出（8300 无监听）、守护 pid 13512 已死并留残留 `auto.json`** → 已重启面板（77288 字符）、清残留、`jobs={}`；四次 auto 启动（`11:34:42/11:39:16/11:42:22/11:43:21`）**全部是 `diag_auto_live.py`**——用户确认未点，靠 `auto_live_out.txt` mtime `11:43:08` 与“23:50/23:55 只有脚本会写”反推，已删残留 `app_config['auto.plan']`；**教训已进 AGENTS：诊断脚本须留痕/幂等/清干净副作用**；`/api/logs` 只读 `run_logs`，而面板 INFO 写 `xxt.log`、守护 print 写 `sub-auto-*.log` → **今日 auto/session 事件 0 条**，故页面停在 `09:46:51`。另解释“只做了代码审查”：守护缺课预核与 `--force` 分支需真起会话才走得到，实跑即违反最小访问原则，留待实况。改造四项用户勾选（守护事件入库 / 系统日志视图 / 账号筛选可搜索 / 刷新改自动刷新）但**时机定在下午**，方案已写进 devlog 14 §4（含去重与验收步骤）。顺带登记：孤儿守护（面板退出不 terminate）、`run_logs` 无清理策略、`taskkill` 仅 Windows |
| 41 | **「批次」改名「学习安排」+ 补两个真漏洞 + 修启动器**：用户四问（没建批次能否刷 / 在批次里的号手动启动会不会双开 / “批次优先”是什么 / 名称是否不妥）。答：① 功能本就可选——无启用学习安排时手动 ▶ 可指定任意课、定时守护按进度轮转；② 面板 ▶ 与守护共走 `/api/study/start`（`_JOBS` + `data/run/<id>.json`）**不会双开**，实测伪造标记即被拒；**但 `xxt mlearn` 只写标记不查标记 = 真漏洞**，已加 `busy_sessions` 守卫（`--force` 可跳过，`learn` 同步支持）；③ 新增缺课保护——面板直接拒（错误含“没有这门课”，守护据此转 `skipped`）、守护侧本地先核（`plans.account_has_course`，零平台访问），不再白耗当日机会；④ 名称按用户拍板改「学习安排」，tab/弹窗/空状态补“可选功能…不建也能刷”。**环境**：实测先打到了旧代码（`{"ok": true}` 本应被拒）→ 查出 `.venv\Scripts\xxt.exe` 从旧项目拷来、指向 `E:\code\xxt` 的 python（页面 39901 vs 77288 字符对比证实），`pip install -e . --no-deps` 重生成修好；后续会话重启面板前请先比页面字符/关键文案。111 全绿、guard/缺课实测通过、diag_ui11 pageerror=0。**追加**：用户问“定时守护怎么轮转挑课/会不会随便刷”→ 干跑 + 真起守护实测（把计划时刻临时推到 23:50/23:55）：日志逐号打印“今日计划 xx:xx 开刷”、`jobs={}`、零平台访问，`auto.plan` 落库为 `{"1":"23:50","2":"23:55"}`；权重=优先级×未完成度（`core/rotation.py`）。顺带发现并修：`_spawn` 未加 `-u` 导致守护 print 被缓冲吞掉（日志只剩一行“守护启动”），已加 `-u`+`PYTHONUNBUFFERED=1` |
| 40 | **补画像实跑：抓取点已失效 → 同页 DOM 兜底**：用户发“补画像”后按约定串行跑 `scripts/fill_profiles.py`，两号均报 `页面没有返回 stu-base-info`。只读诊断（一次访问）证实：会话**有效**（`final_url=…/base`、title 个人空间、正文有学校/姓名），但 51 条非静态响应**无 stu-base-info**——该接口在新版个人空间已不发，即会话 33 上线的功能实际不可用。改：接口优先 + 同页 DOM 兜底（`.header` 首行=学校、`.user`=姓名；`_looks_like_school` 4~30 字/无数字/院校后缀，宁漏不错），仍**一次访问零额外请求**；返回加 `source`（`stu-base-info`/`space-dom`）标明来源。实跑两号 `回填 name/school`，`school=泉州信息工程学院`；**院系/班级/学号该页无**（0 个个人资料链接），按“绝不猜”留空。新脚本 `scripts/fill_profiles.py`（串行、45~90s 随机间隔、不自动重登、`--dry-run`）；新增 7 条纯函数测试（111 全绿，diag_ui9 pageerror=0）。院系/班级/学号平台该页不提供 → **用户拍板后期手工补**（不再为此找入口、不增加访问）。**追加**：用户问“定时任务为何没启动”→ 核实卡片「定时 hh:mm」只是 `start_at` 计划时刻，执行者是独立守护 `xxt auto`（不随面板自启；今日 `auto.json` 不存在、`auto.plan` 停在 10-24、无 `session-start`）；已落地守护未运行时卡片红字「⏱ 调度未启动」（diag_ui10 pageerror=0） |
| 39 | **选择账号筛选栏加 学校/院系/班级 + （未填X）反选**：用户截图问"搜索栏后的筛选栏能否加学校名称，或平台上能区分的字段，注意防封"。查证：平台 `stu-base-info` 可给 姓名/学号/学校/院系/班级，且早已入库 → **筛选全在本地，零新增平台访问**（补字段才需每号一次只读，串行不并发，本次未执行）。落地：新增 `PICKFILTERS`（`[id, profile键, 名称]` × 5）、`fillPickFilters()`/`pickVisible()` 通用化（原先硬编码 grade/major），每个下拉末位追加「（未填X）」（`__empty__`）→ 一键筛出画像不全的号去补。验证：5 下拉齐全、注入 `school=测大` 后精确筛选与未填反选均正确、1500px 下仍一行、pageerror=0（104 全绿） |
| 38 | **批次选课改「先勾账号 → 课程按账号过滤」**：用户追问三事——① 有 pid 仍不知课给哪个账号 ② 不同学校会不会混 ③ "N 个账号有此课"会变（今天 2 明天 3）→ "再创建又是新任务不合理"。读库求证：`courses` 表无学校字段、两号 `school/dept/cls` 全空 → 界面无料可显示；pid 是平台课程实例 ID 不串校，但**看不出归属**；N 只是参考值，批次锁的是 `course_pid + 账号名单`，**新增账号不需重建**。用户拍板：效果顺序改为**先勾人再选课**。落地：弹窗重排 ① 账号 ② 课程；`buildCourseOptions` 按 `PLSEL` 过滤（未勾列全部）、选项 `《课名》 · 学校 · 你选的 N/M 有`、**删全局 N**、pid 退到 `data-pid`；`courseSchool` 空值写「学校未填」；缺课提示列账号名；`renderPlans` 加「N 个账号缺此课」徽标；`doPlan` 从 `dataset.pid` 兜底防洗掉 pid；overview 补发 `school/dept/cls`（104 全绿，diag_ui6/7 pageerror=0） |
| 37 | **批次课程按平台课程ID锚定 + 弹窗可缩放/列宽优化**：用户问"不同年级/专业课名可能一样但内容不同，批次课程名怎么定"。读码定位运行时链路（面板 POST 课名 → `xxt mlearn <课名>` → `_pick_course` 在**账号#1** `pid=? OR name LIKE` **LIMIT 1** 选种子 → `run_accounts` 按 pid 匹配）与三个风险：R1 种子只从 #1 取（批次含 #2/#3 且课不在 #1 → 整轮失败）、R2 同账号同名多门选错、R3 跨账号同名不同 pid 静默跳过。方案 A 落地：`plans` 增 `course_pid`，面板课程改**列表选择器**（按 pid 聚合 + "N 个账号有此课 · pid="，同名不同班分开）+ 缺课账号提示；`mlearn --course-pid` + `_pick_course_in`（pid→课名全等→模糊，范围=批次账号）修 R1/R2；老批次无 pid 仍按名匹配 + "建议补选课程ID"徽标。另：`dialog{resize:both}` + `#planDlg/#pickDlg` 加宽 + 选择器列宽调窄/表头缩短/修 grid 撑破。踩坑：迁移**并发首触** `duplicate column name` → `_ensure_col` 幂等（104 全绿，headless pageerror=0） |
| 36 | **画像区/选择账号表格化 + 平台画像字段扩展 + 搜索字段对齐**：用户提"账号列表做成表格 + 画像尽量从平台取 + 搜索字段对齐 + 备注名实相符"。① `account_profiles` 增 `school/dept/cls`（只展示不进统计维度），`stats.py` get/set/merge/list 全纳入，平台读取后**回显"平台返回字段：…"清单**（键名候选来自 `profile_read.py`，首次实跑核对）；年级/专业/学期仍手补 ② 两处表格化（列头一致：账号｜备注名｜姓名｜平台昵称｜学号｜年级｜专业｜学期｜学校｜院系｜班级｜来源/操作）——画像区 8 列可编辑、选择器只读+复选 ③ `profQ`/`pk_q` 搜索集与 placeholder 统一。踩坑：迁移懒触发（仅 `/` 静态请求不落列）（97 全绿） |
| 35 | **新建批次默认停用 + 趋势图横向条 + 登录按钮弱化 + 删除按钮加字**：用户实测反馈 4 条——① 已登录账号无需重新登录，卡片按钮改低调 `.ghost`「🔑 会话」（未登录才 `.primary`「🔑 登录」）② 新建批次默认启用不合理 → `POST /api/plans` 的 `enabled` 缺省 True→False（`create_plan` 默认不动，测试依赖其冲突检查语义），弹窗按钮改「创建（默认停用）」③ 删除按钮一直存在只是图标难认 → 改「🗑 删除」④ 趋势图"双短胖"（仅 2 天数据、`flex` 撑满）→ 改**横向条形**（每天一行：日期｜条｜分钟·视频数）。新增 `test_plan_created_disabled_by_default`（96 全绿，headless 实渲染 pageerror=0） |
| 34 | **修批次回归 + 账号一键登录 + 趋势图重做 + 画像显示姓名**：修上个会话改名漏改 `let PLANSEL` 声明（点「＋ 新建批次」抛 `PLSEL is not defined`、弹窗开不了）；新增账号一键登录——CLI `login --account`（多账号人工登录首次可用）+ 面板 `POST /api/accounts/{id}/login`（先无头 `auto_relogin`、失败回中文原因+建议、可转手动子进程 `xxt login --account N` 过验证码）+ 卡片「🔑 登录」与 3 秒轮询；趋势图重做（峰值/合计/视频数摘要 + 柱顶分钟标注 + 空状态）；画像页显示 `display_name`（`#1 default`→`#1 姓名（default）`）且姓名框预填当前生效姓名。踩坑：`_PAGE` 非原始串，JS `'\n'` 被 Python 转真换行致整脚本挂（`check_js` 查不出）（95 全绿） |
| 33 | **姓名画像 + 批次选择器重做 + 编辑既有批次 + 修两 bug**：`account_profiles` 加 `name`，`display_name` = 手填姓名 or 平台昵称（卡片/选择器/统计搜索统一）；新建批次弹窗重做（已选清单 + ＋选择账号 → 弹窗内关键字/只看未排/年级专业筛选 + 多选，`PLSEL/PKSEL` Set 与筛选解耦）；批次行加「✎ 编辑」→ 后端 `update_plan`（改名/换课程/增删账号，冲突校验排除自身）；平台画像读取 `adapters/chaoxing/profile_read.py` + `POST .../profile_from_platform`（只读一次个人中心、复用 `stu-base-info`、只补空不覆盖手填）；修日志账号筛选（初始化顺序）与画像保存无提示两个 bug（95 全绿） |
| 32 | **日志独立成页 + 批次多账号操作 + 学号画像 + 课程维度/趋势**：日志改第 4 个标签页「📜 日志」并补上真正的账号筛选（`#logAcc` 此前只在 JS 里被读、HTML 里没有）；批次弹窗加搜索/只看未排/全选清空/已选计数，勾选状态存 `PLSEL` Set 与筛选解耦（否则搜索会静默丢掉已选账号）；`account_profiles` 加 `student_no`/`source`，卡片显示「🎓 学号 · 年级/专业/学期」；`/api/stats?q=` 服务端关键字过滤；统计加「课程」维度（作业行不归组，因 `work-start` 详情无课程名）与每日趋势柱（85 全绿） |
| 31 | **面板三标签页 + 数据可视页**：`📇 账号卡片 / 📚 批次 / 📈 统计`（`switchView` + localStorage 记忆，搜索框自动切回账号页）；新增 `account_profiles(account_id, grade, major, term)` 与 `core/stats.py`（`aggregate` 按年级/专业/学期/账号 × 今日/7/30/全部聚合，时长复用 `quota.sum_done_seconds`，`account_id=0` 全局日志不计入）；面板加 `GET /api/stats`、`GET/POST /api/profiles` 与统计 UI（CSS 条形图 + 画像内联编辑）；全程读本地 SQLite，不访问平台（81 全绿） |
| 30 | **批次（plans）**：`plans` + `plan_accounts` 两表（**不含时间字段**——开跑仍按各账号 `start_at`/窗口随机错峰）；`core/plans.py` 只回答"该账号今天刷哪门课"；`auto` 守护先问批次再回落课程轮转；面板新增「📚 批次」区（建/启停/删 + 卡片批次行），同账号只能在一个启用批次里（冲突点名拒绝）；防多开复核：批次与手动 ▶ 共用 `/api/study/start` 同一把锁（`_JOBS` + `data/run/<id>.json`），并补上 spawn 后立即写 marker 的 1~2 秒空窗（73 全绿） |
| 29 | **答案按账号隔离**：`questions` 只留题目本体（共享），新增 `answers(question_hash, account_id)`；`bank_lookup/bank_record/bank_record_wrong/solve/handle_quiz` 全部强制 `account_id` 关键字参数，`account_id<=0` 直接拒绝；9 条历史全局答案迁到 `account_id=0`（永不自动复用）；判分回读改三态（correct/wrong/pending/unknown，**解析不到不再当答错**）+ qid 精确匹配 + 分数正则收紧；不做"跨账号共享答案"开关（66 全绿） |
| 28 | 同步/异步解耦：`astudy.py` 不再 import `study.py`，抽出 `pagejs.py`（纯数据，通用）+ `pagesync.py`（同步专用），`study.py` 瘦身 97 行；`tests/test_module_boundaries.py` 4 条边界守护；时段窗口检查前移到"点下一节"之前（省一次页面推进+一个间隔）、账号窗口"只填一侧"改为保存即拒；顺带修"✔ 读判分"相对导入少一个点（此前必然 500）（62 全绿） |
| 27 | 修"0 秒假完成：上一节 watcher 残留 tick 被当成新视频播完"——注入前后清事件队列、新增 `core/playback.is_plausible_completion`（需见过真实进度或耗时匹配才记额度）、"到末尾"改查 `chapters` 表；被污染的 1 条假 `video-done` 与 1 个章节状态已复原（57 全绿） |
| 26 | 修会话崩溃：异步引擎误用同步 `hardened_context`（`coroutine … add_init_script`）；`_spawn` 输出从 DEVNULL 改为落盘 `sub-*.log`（本次排查的最大盲区）；昵称截获改异步处理器；`TaskGroup` 加 per-account 异常兜底（一个号崩不连累兄弟）；源码级测试守护 sync/async 不得混用（52 全绿） |
| 25 | 账号级额度核查：面板/mlearn/auto 链路本就正确（含"只覆盖一项、另一项跟随全局"）；修 `xxt learn` 漏用账号级额度与时段窗口（`StudyRunner` 增 quota/pace 参数）；端到端实测 #1 设 1 个任务 → 26 秒零访问拦截（49 全绿） |
| 24 | 每日**时长**额度改跨会话（读 `run_logs` 累计秒数），两次实测：22:13 任务数拦截、22:22 时长拦截，均零平台访问；加日志捞出 P0——`llm.base_url`/`llm.model` 裸串写入被 `json.loads` 丢弃，切 Provider 从来没生效过（48 全绿） |
| 23 | 指纹加固方案 A（UA/plugins/mimeTypes/userAgentData/webdriver/AutomationControlled，12 项 9 项对齐真机）；额度与待学章节检查前置（被拒时零平台访问）；面板卡片显示"今日 x / 上限 y（全局｜账号）"；额度实证生效（46 全绿） |
| 22 | 实况监控发现并修 3 缺陷：全局额度被账号 0 覆盖、切节按钮误判致提前结束、日志无账号标识（46 全绿） |
| 21 | 修复 P0-1（login 崩溃）/ P0-2（venv 指向）；push_check 增第 3 道语法门禁；新增全模块导入冒烟测试（45 全绿） |
| 20 | 全面检查：发现 2 个 P0 + P1 清单；建立本状态文件；双目录/分支约定 |
| 19 | 技术债与待办全面登记（双运行器统一方案 A 已设计待点名） |
| 18 | 撤销系统环境变量 Key → 加密落库；Key 状态提示改迁移引导 |

## 6. 文档导航

| 文件 | 职责 |
|---|---|
| `STATUS.md` | 当前状态（本文件，唯一真相） |
| `AGENTS.md` | 工作约定：会话前后动作、门禁、跨会话/跨目录规则 |
| `docs/PLAN.md` | 总体方案：架构、选型、模块设计、里程碑 |
| `docs/devlog/*.md` | 每次会话记录（含交接块），按日期序号 |
| `docs/BUILD.md` / `docs/DEPLOY_LINUX.md` | 构建与部署 |

## 7. 给任何开发工具的三条规则

1. **进门**：`git status` 必须干净才开始；不干净说明上一位没做完，先看最近 devlog。
2. **提交**：只 `git add` 本会话改动（严禁 `git add -A`）；只推 `main` 的是 `E:\xxt`，其它目录推自己的分支。
3. **离开**：写 devlog → 更新本文件第 3 节 4 个字段 → commit（`session: 主题`）。
