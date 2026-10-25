# XXT 项目状态（唯一状态入口）

> **任何会话 / 任何开发工具接手前，先读本文件。**
> 本文是"当前状态"的唯一真相；工作约定见 `AGENTS.md`，长期方案见 `docs/PLAN.md`，历史见 `docs/devlog/`。
> 最近更新：2026-10-25（会话 21：修复 P0-1/P0-2，门禁加固；测试 45 全绿）

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

- **进行中**：无（2 个 P0 已修，测试 45 全绿）
- **下一步第一个动作**：重建 exe 验证 `login` 恢复；或按用户点名进入 P1 工程债
- **阻塞点**：无
- **等用户**：① 作业全链路实测与判分校准（最优先）② 弹题选择器实况 ③ 反检测效果观察 ④ ≥3 账号并发 ⑤ Docker 实机构建

## 4. 已知未修问题

### P0（必须修，影响可用性/验证可信度）

| 编号 | 位置 | 影响 | 状态 |
|---|---|---|---|
| P0-1 | `src/xxt/session/login.py:30` 缩进错误 | `xxt login` 必然 `IndentationError`；`dist/xxt.exe`（18:58 构建）与 build 副本均已实证带此缺陷。由提交 `99ea2d3` 引入；因 login 为延迟导入且无测试覆盖，43 个测试全绿仍漏网 | ✅ 已修（devlog 21）；exe 待重建 |
| P0-2 | `.venv/Lib/site-packages/__editable__.xxt-0.1.0.pth` → `E:\code\xxt\src` | 在 `E:\xxt` 跑 pytest / CLI / `scripts/push_check.py` 实际加载旧目录代码，测试与推送门禁结果不可信 | ✅ 已修（pth → `E:\xxt\src`），并有测试常驻守护 |

### P1（工程债，影响承诺兑现或安全边界）

| 位置 | 问题 |
|---|---|
| `adapters/chaoxing/astudy.py` + `study.py` | 每日**时长**额度仅在单次会话内累计（`study_start` 计时），多次启动可突破 `[quota].daily_minutes`；任务数额度已按当日 `run_logs` 正确扣减 |
| `core/pace.py` `FocusTimer` | 已实现但**零引用**，"连续 25~60 分钟 → 休息 5~15 分钟"未生效 |
| 同步 `StudyRunner` vs 异步 `run_accounts` | 双引擎并存；`xxt learn` 走同步版且**不写 `data/run/<id>.json` 标记** → 可绕过"同账号绝不双开"硬约束（面板无法感知） |
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
