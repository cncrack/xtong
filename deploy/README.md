# Docker 部署（M4.5）

适用：长期挂机的服务器/NAS（Windows 主力机用 exe/源码形态即可，见 [docs/BUILD.md](../docs/BUILD.md)）。

## 首次部署

```bash
cd xxt/deploy
docker compose up -d --build        # 构建并启动（面板 http://服务器IP:8300）
```

## 账号登录（容器无显示器，两种方式）

**方式 A（推荐）——Windows 先登再迁**：
1. Windows 上 `xxt login` 完成登录（生成 `data/accounts/<id>/state.json`）；
2. 把整个 `data/` 目录放到服务器 `xxt/data/`（compose 已挂载）；
3. 容器自动使用该会话（滚动续期在容器内继续）。

**方式 B——凭据自动登录**：面板添加账号时填账号密码（加密存 data/），会话失效时自动重登
（预算制内；如遇验证码需回方式 A 重新生成 state）。

## 日常

- 面板：`http://服务器IP:8300`（▶ 刷课 / ⏱ 定时 / 作业 / 账号管理全功能）
- 日志：`docker logs -f xxt` 或 `data/logs/xxt.log`
- 升级：`git pull && docker compose up -d --build`
- 备份：整个 `data/` 目录（含数据库/会话/题库/密钥——**含敏感信息，勿外传**）

## 注意

- 容器内全部 headless（无窗口）；反检测（UA/webdriver）已内建
- 同一账号避免 Windows 与 Docker **同时**挂机（会话互踢+IP 跳变）；迁移期间停一边
- 出口 IP：服务器 IP；如需与家庭 IP 一致，配 `environment.HTTP_PROXY`（compose 注释示例）
- LLM Key：`deploy/.env` 写 `XXT_LLM_API_KEY=sk-...`（.env 已被 gitignore），或面板 Provider 中心填（加密落 data/）
