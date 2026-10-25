# XXT 项目状态（唯一状态入口）

> **任何会话 / 任何开发工具接手前，先读本文件。**
> 本文是"当前状态"的唯一真相；工作约定见 `AGENTS.md`，长期方案见 `docs/PLAN.md`，历史见 `docs/devlog/`。
> 最近更新：2026-10-26（会话 35：**新建批次默认停用**（`POST /api/plans` 的 `enabled` 缺省 True→False，须手动「▶ 启用」）+ **趋势图改横向条形**（每天一行：日期｜条｜分钟·视频数，不再"两根胖柱撑满屏"）+ **登录按钮弱化**（已登录→低调「🔑 会话」，未登录→醒目「🔑 登录」）+ **删除按钮加文字**（`🗑`→`🗑 删除`）；96 全绿）

---

## 1. 项目一句话

超星学习通网课自动化助手：账号密码登录 → 自动刷视频 → 章节作业 AI 作答 → 多账号并发 + Web 面板 → 混淆打包分发。Python 3.11 + Playwright + FastAPI + SQLite。

阶段：**全功能交付，实测校准期**（P0 刷课 / P1 多账号+面板 / P2.5 作业 AI / P3 打包分发均已完成；剩余为实况验证与工程债）。

## 2. 关键路径与环境

| 项 | 值 | 说明 |
|---|---|---|
| 主目录 | `E:\xxt` | **唯一开发/提交 main 的目录** |
| 旧目录 | `E:\code\xxt` | 迁移前副本，用户保留作备用开发（建议切 `dev-alt`，只推自己分支） |
| venv | `E:\xxt\.venv` | 已修正：pth 指向 `E:\xxt\src`（2026-10-25，见 devlog 21） |
| 真实数据 | `E:\xxt\data` | 账号/进度/题库/Key **只此一份**，旧目录不得双活 |
| 面板 | `http://127.0.0.1:8300` | 默认仅本机 |
| 构建产物 | `dist/`（`xxt.exe`、`portable_issuer.exe`） | ⚠ 现有 exe 构建于 P0-1 修复前，**对外分发前必须重建** |
| 远端 | `cncrack/xtong`（SSH） | main 共约 150 提交 |

## 3. 交接字段（每次会话收尾必更）

- **进行中**：✅ 无。批次默认停用 + 趋势图横向条 + 登录按钮弱化 + 删除按钮加字全部完成（96 全绿；headless 实渲染 pageerror=0）
- **下一步第一个动作**：用户 `Ctrl+F5` 强刷面板 → ①「📈 统计」看横向趋势条 ②「📚 批次」试「＋ 新建批次」→ 确认按钮「创建（默认停用）」、建完是「已停用」、点「▶ 启用」生效 ③ 批次行「🗑 删除」 ④「📇 账号卡片」已登录账号显示低调「🔑 会话」
- **阻塞点**：无
- **等用户**：① 横向趋势图观感 ② 新建批次默认停用是否符合预期 ③ 10-27 实测：批次错峰 / 平台画像读取 / 作业判分校准 ④ 弹题选择器实况 ⑤ 指纹加固后的账号异常观察 ⑥ ≥3 账号并发

## 4. 已知未修问题

### P0（必须修，影响可用性/验证可信度）

| 编号 | 位置 | 影响 | 状态 |
|---|---|---|---|
| P0-1 | `src/xxt/session/login.py:30` 缩进错误 | `xxt login` 必然 `IndentationError`；`dist/xxt.exe`（18:58 构建）与 build 副本均已实证带此缺陷。由提交 `99ea2d3` 引入；因 login 为延迟导入且无测试覆盖，43 个测试全绿仍漏网 | ✅ 已修（devlog 21）；exe 待重建 |
| P0-2 | `.venv/Lib/site-packages/__editable__.xxt-0.1.0.pth` → `E:\code\xxt\src` | 在 `E:\xxt` 跑 pytest / CLI / `scripts/push_check.py` 实际加载旧目录代码，测试与推送门禁结果不可信 | ✅ 已修（pth → `E:\xxt\src`），并有测试常驻守护 |

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
