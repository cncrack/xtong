# 会话记录 2026-10-24-18 — M4 启动：多账号地基（加密/表扩展/CLI）

## 本次目标

白天窗口内并行推进：M3 验收实跑进行中（5 视频，独立进程），同时按 v0.13 决策开工 M4 代码层（不碰账号）。

## 结果

✅ M4 第一块完成，测试 23/23：

1. **`core/crypto.py`**：AES-256-GCM 凭据加密；主密钥 = 环境变量 `XXT_MASTER_KEY`（口令 sha256）或 `data/secret.key`（首次自动生成，不入库）
2. **schema v3**：accounts 表扩展 `username_enc/password_enc`（BLOB 加密）、v0.6 五字段（`window_start/window_end/start_at/daily_max_minutes/daily_max_tasks`，可空回落）、`proxy`、`priority`；老库自动 ALTER 迁移
3. **`core/accounts.py`**：增删查、凭据加解密（list 永不明文）、**三层配置合并**（`effective_pace/effective_quota`：toml ← 账号覆盖）
4. **CLI `xxt account add/list/rm/set`**：set 语义"未指定=不动、none=清除回落"；add 用 getpass 输密码
5. 依赖 +cryptography

## 关键决策及理由

1. nonce(12B)+密文 直存 BLOB，无外部信封格式——SQLite 内自包含，P1 面板直接读
2. set 命令空串/none 语义：避免"改一个覆盖项清空其他项"的误操作（开发中自查发现的 UX 缺陷）
3. 凭据解密只在运行时按需进行（自动重登用），列表/日志永不出明文

## 改动文件

- 新增：`src/xxt/core/crypto.py`、`src/xxt/core/accounts.py`、`tests/test_accounts.py`
- 修改：`src/xxt/store/db.py`（v3 迁移）、`src/xxt/cli.py`（account 命令组）、`pyproject.toml`

## 交接块

- **进行中**：M3 验收实跑进行中（2.2 接近完成，5 视频预计 11:45 完）；M4 剩余 = **async 移植**（astudy.py，按 devlog 17 纪律：同步版保留至 async 过验收）+ 调度器（TaskGroup/Semaphore/错峰）+ 面板
- **下一步第一个动作**：async 移植 StudyRunner（复用 quota/pace/rotation 纯逻辑，仅换 async Playwright API）
- **阻塞点**：无（async 移植不碰账号；多账号实跑验收需用户提供小号）
