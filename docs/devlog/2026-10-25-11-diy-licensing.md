# 会话记录 2026-10-25-11 — 自研分发授权（machine-id + lic 全流程）+ Linux 部署教程

## 本次目标

用户问 machine-id/lic 是什么、如何处理（前轮方案研究后追问），并要 Linux 小主机部署教程。

## 结果

✅ 全部完成（45add21 + 1b4aa23，测试 43/43）：

1. **Linux 部署教程** `docs/DEPLOY_LINUX.md`：Docker 推荐（装 Docker→项目入 /opt/xxt→Windows
   先登后拷 data/→compose up→面板访问，含运维/升级/备份/FAQ）+ 源码直跑（venv+playwright
   install-deps+systemd 自启）
2. **自研授权系统**（解释即落地，trial 版即可全流程）：
   - `xxt machine-id`：显示硬件指纹（Win=MachineGUID/Linux=/etc/machine-id/兜底MAC，sha1 格式化）
   - `scripts/make_lic.py --mid X --days N`：发牌（HMAC-SHA1 签名 payload）
   - `cli.main` 启动校验：**无 lic=自用直通**（零影响）；有 lic→异机/篡改/过期三路径拒绝（exit 403）
   - **三条拒绝路径全部实测通过**；自用回归正常（progress 跑通）
   - 分发前须知写入 BUILD.md：**必须换 `_SIGN_KEY`**（默认密钥公开在源码，懂行者可自签）
3. 概念答复：单文件≠安全（形态而已，pyinstxtractor 十秒解包），安全靠混淆；lic 是
   "机器码+到期"的签名锁，与 PyArmor 正式版可叠加（lic 校验在混淆 core 内，改它先脱壳）

## 改动文件

- `core/licensing.py`（新）、`cli.py`（machine-id 命令+启动校验）、`scripts/make_lic.py`（新）、
  `docs/DEPLOY_LINUX.md`（新）、`docs/BUILD.md`（分发授权节）、本记录

## 交接块

- **进行中**：授权系统就绪（自用无感）；Linux 部署文档交付
- **下一步第一个动作**：用户 Linux 小主机按教程部署（Docker 优先）；作业链路实测仍待
- **阻塞点**：无
