# Linux 小主机部署教程（指定目录）

两种方式：**A. Docker（推荐**，隔离好、开机自愈、升级一条命令**）** / B. 源码直跑（轻量）。
假设小主机为 Ubuntu/Debian（其他发行版命令微调），目标目录以 **`/opt/xxt`** 为例（可任选）。

---

## 方式 A：Docker（推荐）

### ① 装 Docker（已装跳过）

```bash
curl -fsSL https://get.docker.com | sh      # 官方脚本，国内网络一般可达
sudo usermod -aG docker $USER               # 免 sudo（执行后重新登录一次生效）
docker --version                            # 验证
```

### ② 项目放到指定目录

```bash
sudo mkdir -p /opt/xxt && sudo chown $USER /opt/xxt
# 任选其一：
git clone <你的仓库地址> /opt/xxt            # 有 git 远端
# 或从 Windows 整目录拷贝（Git Bash 下）：
#   scp -r /e/code/xxt 用户名@小主机IP:/opt/xxt
```

### ③ 登录态迁移（关键一步）

容器无显示器，账号会话在 **Windows 上先登录再拷过去**：

```bash
# Windows（Git Bash）：
cd /e/code/xxt && .venv/Scripts/xxt.exe login     # 每个账号各登录一次
scp -r /e/code/xxt/data 用户名@小主机IP:/opt/xxt/data   # 整个 data/（含会话/数据库/题库）
```

> 之后的滚动续期在容器内自动进行；会话真失效且有存凭据时可自动重登（遇验证码才需回 Windows 重登一次）。

### ④ 启动

```bash
cd /opt/xxt/deploy
cp /dev/null .env 2>/dev/null; echo "XXT_LLM_API_KEY=你的Key" > .env   # 可选：LLM Key（或面板里填）
docker compose up -d --build
```

首次构建约 5~15 分钟（拉镜像+装依赖）；之后访问 **`http://小主机IP:8300`** 即面板。

### ⑤ 日常运维

```bash
docker compose -f /opt/xxt/deploy/docker-compose.yml logs -f   # 看日志
docker compose -f /opt/xxt/deploy/docker-compose.yml restart   # 重启
cd /opt/xxt && git pull && cd deploy && docker compose up -d --build   # 升级
tar czf /root/xxt-backup.tar.gz -C /opt/xxt data               # 备份（含敏感会话，勿外传）
```

开机自启：`restart: always` 已配，宿主机开机容器自动拉起。

---

## 方式 B：源码直跑（不装 Docker）

```bash
# 1. Python 3.11 + 工具
sudo apt update && sudo apt install -y python3.11 python3.11-venv git
# 2. 项目到指定目录（同方式A②），然后：
cd /opt/xxt
python3.11 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/playwright install chromium          # 下载浏览器内核
.venv/bin/playwright install-deps chromium     # 系统依赖（无 root 时让管理员执行）
# 3. 登录态迁移同方式A③（Windows 登录后拷 data/）
# 4. 启动面板：
.venv/bin/python -m xxt.cli panel
```

**开机自启（systemd）**——`/etc/systemd/system/xxt.service`：

```ini
[Unit]
Description=XXT panel
After=network-online.target
[Service]
WorkingDirectory=/opt/xxt
ExecStart=/opt/xxt/.venv/bin/python -m xxt.cli panel
Restart=always
[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now xxt
```

---

## 常见问题

| 现象 | 处置 |
|------|------|
| 容器起但面板打不开 | `docker compose logs xxt` 看报错；确认 8300 未被占（改 compose 端口映射 `8301:8300`） |
| 提示浏览器缺失 | 方式A 不应出现（镜像自带）；方式B 重跑 `playwright install chromium` |
| 会话失效循环 | Windows 重登该账号 → 只把 `data/accounts/<id>/state.json` 拷回容器 data |
| Windows 和小主机都挂机 | **禁止同一账号双端同时挂**（互踢+IP 跳变）——迁移完停掉 Windows 端 |
