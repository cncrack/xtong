# 会话记录 2026-10-24-08 — 代码审查三级门禁落地

## 本次目标

用户确认采纳"分层代码审查"方案：commit 轻量自审 / push 脚本门禁 / 里程碑 AI 深审，要求落地。

## 结果

✅ 完成。`scripts/push_check.py` 就绪并实测通过；三级门禁写入 AGENTS.md。

## 关键决策及理由

1. **push 门禁做成脚本而非靠自觉**：敏感信息扫描靠人眼迟早漏，脚本每次必查——
   ① 待推送 diff 扫描：敏感赋值形态（`password = "..."` 等，正则拦截）、邮箱、手机号；
   额外会读本机 `data/git-remote.md` 提取真实账号值作为精确拦截词（该文件不入库，脚本只读本机）；
   ② tracked 文件防护：`data/`、`*.db`、`.env`、`storage_state*.json` 不得被 git 跟踪；
   ③ `pytest` 全绿。任一失败 exit 1。
2. **敏感词分两档**：赋值形态/邮箱/手机号 = 硬拦截；`cookie/storage_state/authorization/bearer`
   裸词 = 仅告警（代码中合法引用多，硬拦会误伤）。
3. **三级分工**：commit 级 AI 自审不加人力；push 级脚本强制；里程碑级派只读审查会话出报告、
   用户只看结论拍板——人工注意力放在风险决策而非找 bug。
4. **收尾流程同步修订**：AGENTS.md"收尾原子化"增加"推送前必须 push_check 全绿"。

## 改动文件

- 新增 `scripts/push_check.py` — push 门禁脚本（纯标准库，Windows/Docker 双形态可用）
- 修改 `AGENTS.md` — 新增"代码审查三级门禁"专节；"收尾原子化"补推送门禁前置条件
- 修改 `pyproject.toml` — 新增 `dev` 依赖组（`pytest>=8`）
- 新增 `docs/devlog/2026-10-24-08-push-gate-and-review-tiers.md` — 本记录

## 门禁首跑发现并修复的两个问题

1. **脚本 bug**：pytest 失败原先未计入拦截项（会错误输出 PASS）——已修，测试不过即 FAIL；
2. **环境缺陷**：`.venv` 未安装 pytest（此前"测试 2/2"是用非 venv 解释器跑的）——已 `pip install pytest` 入 venv，并写入 pyproject dev 组，新环境用 `pip install -e .[dev]` 可复现。
   门禁用 venv 解释器跑测试，从今往后测试结果以 venv 为准。

## 交接块

- **进行中**：P0；主对话正在登录诊断（见 devlog 07-login-diag），本侧聊未触碰 src/
- **下一步第一个动作**：任何会话推送前先跑 `.venv/Scripts/python.exe scripts/push_check.py`；主对话继续按其交接块推进登录验证
- **阻塞点**：登录验证仍等用户实机反馈（不变）
