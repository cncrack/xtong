# XXT 项目状态（唯一状态入口）

> ⚠ **本文件只放「当前状态」，目标 < 5,000 字符，每轮会话收尾覆盖写。**
> - 未修问题 → [`docs/BACKLOG.md`](docs/BACKLOG.md)
> - 已归档问题 → [`docs/BACKLOG-archive-index.md`](docs/BACKLOG-archive-index.md)
> - 历史记录 → [`docs/devlog/`](docs/devlog/)
>
> 2026-11-29 架构调整：原来本文件 93K 字符（一个月 +42%），因为「当前状态 / 问题清单 / 历史记录」三件事挤在一起。现已按职责拆分。

**最近更新**：**2026-11-29（STATUS 拆分 + 模板抽取）**——把 93K 的 STATUS 拆成「STATUS（当前状态）/ BACKLOG（问题清单）/ 归档索引 / devlog（历史）」四层；§4 的 80 条已修全部归档，45 条未修进 BACKLOG，**77 个编号零丢失**；同时抽出可复用模板 `docs/templates/`。

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

---

## 3. 交接字段（每次会话收尾必更，**覆盖写不追加**）

- **进行中**：**无（2026-11-29 01:35 收工）**。STATUS 拆分 + 模板抽取**已提交并推送**
  （`eefc0db`），工作区干净、待推送 0、临时脚本已清。
- **下一步第一个动作**：继续做 gxks 题库 —— 下一节 **3.1 军事思想概述**
  （`exam_id=14672`）。需先确认面板/Chrome 已启动。
- **阻塞点**：无
- **等用户**：无

> ⚠ 这四项是「当下」不是历史。上一轮的值已进 devlog，这里每次整体替换。
> 曾有 28710 字符的历史长河堆在这一节——不要再犯。

---

## 4. 汇总数字

| 项 | 数量 | 位置 |
|---|---|---|
| 未修问题 | **45 条** | [`docs/BACKLOG.md`](docs/BACKLOG.md) |
| 已归档问题 | **80 条** | [`docs/BACKLOG-archive-index.md`](docs/BACKLOG-archive-index.md) |
| 最近一次会话 | devlog `2026-11-29-01`（STATUS 拆分 + 模板） | `docs/devlog/` |
| 测试 | **1516 全绿** | `.venv/Scripts/python.exe -m pytest -q` |
| 题库（gxks） | **14 份答卷 / 36 题 / 42 答案** | `data/gxks_questions.db` |

---

## 5. 文档导航

| 文件 | 职责 | 体量目标 |
|---|---|---|
| `STATUS.md` | 当前状态（**本文件，短**） | < 5,000 字符 |
| `AGENTS.md` | 工作约定（规矩，不放状态） | — |
| `docs/BACKLOG.md` | 未修问题 + 最近已修 10 条 | < 20,000 |
| `docs/BACKLOG-archive-index.md` | 已归档问题索引（可 grep） | 一行一条 |
| `docs/BACKLOG-archive-2026-11.md` | 归档详情 | 不限 |
| `docs/devlog/*.md` | 每轮会话记录（五要素 + 交接块） | 不限 |
| `docs/PLAN.md` | 超星侧总体方案 | — |
| `docs/gxks/PLAN-GXKS.md` | gxks 侧方案（唯一事实来源） | — |
| `docs/gxks/gaoxiaokaoshi.md` | 平台实测事实库（写 gxks 代码前必查） | — |
| `docs/templates/` | **可复用到其它项目的模板** | — |
| `docs/BUILD.md` / `docs/DEPLOY_LINUX.md` | 构建与部署 | — |

---

## 6. 给任何开发工具的三条规则

1. **进门**：读本文件（30 秒）→ `docs/BACKLOG.md` → 最近 2~3 篇 devlog；`git status` 必须干净。
2. **提交**：只 `git add` 本会话改动；commit message 按五要素写（原因/根因/改法）。
3. **离开**：写 devlog（五要素+交接块）→ **覆盖写**本文件 §3 → 更新 BACKLOG → commit。
