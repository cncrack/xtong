# P3 构建与部署（加密打包）

## 构建（开发机）

```bash
.venv/Scripts/python.exe scripts/build_exe.py
```

流程：复制源码树 → **PyArmor 混淆 `core/` 与 `ai/`（核心策略与 AI 层不可读，壳层可用）** →
PyInstaller onedir 打包 → 产物 `dist/xxt/xxt.exe`。

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
