# 会话记录 2026-10-25-10 — 单文件 EXE（--onefile）+ Docker 交付物（M4.5）

## 本次目标

用户点名：Docker 化 + 单文件 EXE，并问先前的单文件方案。

## 结果

✅ 完成（6524f99，测试 43/43）：

1. **单文件 EXE**：build_exe.py 加 `--onefile`——产物 `dist/xxt.exe`（64MB，PyArmor 混淆同）。
   实测：--help 全命令 ✓ / XXT_ROOT 外挂数据跑 progress 读真实库 ✓（22/70）
2. **形态选型写入 BUILD.md**：自用挂机=onedir（快/低误报），分发=onefile（便携）
3. **Docker（M4.5）**：deploy/ 三件套——官方 Playwright 镜像（免装内核）+ compose（8300 面板/
   data 与 config 卷/XXT_LLM_API_KEY 注入/restart always/TZ 上海）+ 部署文档（含容器无头的
   两种登录方案：Windows 先登迁 state / 凭据自动重登；升级/备份/同账号双端告诫）
4. **如实说明**：本机无 Docker——镜像构建验证待有 Docker 环境（配置按官方镜像标准写，风险低）
5. 答复"先前单文件方案"：PLAN §5.7 定稿 = PyArmor 混淆 + PyInstaller（M6 验收时用 onedir），
   本次切 onefile 形态，混淆/授权路径不变

## 改动文件

- `scripts/build_exe.py`（onefile）、`deploy/{Dockerfile,docker-compose.yml,README.md}`（新）、
  `docs/BUILD.md`、`.gitignore`（deploy/.env）、本记录

## 交接块

- **进行中**：Docker 实机构建待用户环境（`cd deploy && docker compose up -d --build`）
- **下一步第一个动作**：用户明天实测作业链路；Docker 有环境时构建反馈
- **阻塞点**：本机无 Docker（仅验证受限，非代码问题）
