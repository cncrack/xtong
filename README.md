# XXT — 网课自动化学习助手

模拟登录学习平台，自动完成课程视频学习，视频中弹题自动调用 AI 作答。

## 四阶段路线

- **P0 单用户**：学习通账号密码登录 → 自动刷视频闭环
- **P1 多账号**：账号池统一管理、多账号同时学习、管理面板
- **P2 弹题 AI**：弹题计分项的 AI 识别作答（题库 + 半自动校准，后置功能，开关默认关）
- **P3 保护**：核心代码加密（PyArmor）、授权机制、打包 exe

部署形态：本机 Windows 长期挂机 或 Docker，双形态支持（Windows 先行）。

## 文档索引

| 文档 | 内容 |
|------|------|
| [docs/PLAN.md](docs/PLAN.md) | 总体方案：架构、技术选型、模块设计、里程碑、风险 |
| [docs/devlog/](docs/devlog/) | 每次开发会话的记录（进展、决策、待办） |
| [AGENTS.md](AGENTS.md) | AI 会话工作约定（每次 AI 会话开始前必读） |

## 快速开始（M0 骨架，Windows）

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .
playwright install chromium   # 首次需要，下载浏览器内核

xxt init      # 初始化数据目录与数据库
xxt login     # 打开浏览器，人工完成学习通登录（账号密码/验证码），自动保存会话
xxt status    # 检查会话是否有效
```

开发测试：`python -m unittest discover -s tests -v`

运行数据（账号会话、数据库、日志）都在 `data/` 目录，已被 git 忽略，不会泄露账号信息。

## 风险提示

自动化学习违反多数平台用户协议，存在账号被限制的风险；AI 作答不保证正确。
详见 [docs/PLAN.md](docs/PLAN.md) §8。请仅用于自己拥有或获得授权的账号。
