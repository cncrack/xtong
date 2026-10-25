# P3 构建与部署（加密打包）

## 构建（开发机）

```bash
.venv/Scripts/python.exe scripts/build_exe.py              # onedir：dist/xxt/ 目录（启动快）
.venv/Scripts/python.exe scripts/build_exe.py --onefile   # 单文件：dist/xxt.exe（64MB，便携）
```

流程：复制源码树 → **PyArmor 混淆 `core/` 与 `ai/`（核心策略与 AI 层不可读，壳层可用）** →
PyInstaller 打包 → 产物见上。

两种形态怎么选：**自用挂机选 onedir**（启动快、杀软误报率低）；**分发给别人选 onefile**
（单文件拷走即用，首次启动稍慢）。混淆与授权路径两形态相同。

## Docker 部署（服务器/NAS 长挂）

见 [deploy/README.md](../deploy/README.md)——官方 Playwright 镜像，`docker compose up -d --build`
即起，`data/` 卷持久化，`restart: always` 自愈。

## 部署到目标机

1. 拷贝整个 `dist/xxt/` 目录（不是单文件）；
2. 目标机首次运行前装浏览器内核：任何机器上执行过
   `pip install playwright && playwright install chromium` 即可（内核在用户目录共享），或把
   `%LOCALAPPDATA%\ms-playwright` 一并拷贝；
3. （可选）外挂数据目录：设环境变量 `XXT_ROOT=<数据目录>`——exe 优先使用该目录的
   `config/settings.toml` 与 `data/`；不设则用 exe 旁边的目录；
4. 运行 `xxt.exe init / login / panel` 等，与开发机一致。

## 目录语义

| 位置 | 用途 |
|------|------|
| exe 目录（或 XXT_ROOT） | `data/`（数据库/会话/密钥/日志）、`config/` |
| `settings.toml.template` | 出厂配置模板（首次 init 可参考复制为 settings.toml） |

## 授权与限制（现状与升级路径）

- 当前使用 **PyArmor 试用版**：混淆有效，但带 trial 标记，且有非商用限制——**自用防拷贝足够**；
- 对外分发前需：购买 PyArmor 授权（启用 RFT/BCC 更强混淆 + 到期/机器码锁定），或按 PLAN §5.7
  将 core/ai 上云（客户端只剩壳，最强保护）；
- 敏感数据（凭据密文、会话、API Key 环境变量）本就不随 exe 分发。

## 分发授权（自研 lic，已内置）

分发时的完整流程（PyArmor 之外多一道自研锁，trial 下即可用）：

```
① 对方：xxt.exe machine-id        → 显示机器码（如 6187-E9A8-4156-7ABE）发给你
② 你：  scripts/make_lic.py --mid 对方机器码 --days 30 [--note 买家名]
        → 生成 xxt.lic 发回
③ 对方：xxt.lic 放 exe 同目录      → 启动自动校验（异机/篡改/过期均拒绝运行 exit 403）
```

- 自用不受影响：**无 xxt.lic = 自用直通**（放行），有 lic 才校验；
- **分发前必做**：更换 `core/licensing.py` 的 `_SIGN_KEY` 为你自己的随机串（否则懂行的人可自签）；
- 续期=再发一张新 lic；吊销=不再发即可（到期自动停）；
- 与 PyArmor 正式版叠加：lic 校验逻辑在混淆的 core 内，改校验需先脱壳——双层成本。

