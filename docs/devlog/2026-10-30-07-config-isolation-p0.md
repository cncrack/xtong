# 2026-10-30-07 P0 配置隔离落地：第二平台配置搬出被跟踪文件

> 接 `2026-10-30-06` 交接块的「下一步第一个动作」。本场只做 P0，P1~P3 未动。
> 平台事实见 `docs/gxks/gaoxiaokaoshi.md`，规划与矩阵见 `docs/gxks/PLAN-GXKS.md` §十三。

## 1. 目标与结果（全部达成）

| 目标 | 结果 |
|---|---|
| `config/settings.toml` 还原成超星原样 | ✅ 与 `main` **零 diff**（`platform=chaoxing`、`[reconcile]` 段回来、`float_ratio=[0.85,1.0]`） |
| 第二平台差异有地方放 | ✅ 新增 `config/settings.local.toml`（**不入库**）+ 入库模板 `settings.local.example.toml` |
| `Settings` 支持本机覆盖层 | ✅ 顺序：内置默认值 → `settings.toml` → **本机覆盖层** → DB `app_config` → 环境变量 |
| 容器可用（绑 `0.0.0.0`） | ✅ `XXT_PANEL_HOST` / `XXT_PANEL_PORT` 覆盖 `[panel]`，不设时行为不变 |
| 测试守住这一层 | ✅ `tests/test_config_local_override.py` 7 条；连同 m0/panel/gxks_panel 共 **56 passed** |
| 超星侧零影响 | ✅ `git -C E:\xxt status --short` 为空 |

## 2. 关键决策

### D1 用「本机覆盖层」而不是分仓（延续 06 场的 A 方案）

`config/settings.toml` 是两个平台 worktree 共用的**被跟踪文件**，平台差异写进去，合并回 main 时必然顶掉另一边（06 场已查实：本分支缺 `[reconcile]`、`float_ratio` 是旧值）。把差异搬到 `config/settings.local.toml`（`.gitignore` 已忽略）后，本分支对该文件**零 diff**，"合并会改到超星"这条路被物理切断。

代价写清楚了：本分支不再自带第二平台配置，**新克隆/换机器要先 copy 模板**（已写进 PLAN-GXKS §13.6 的 ⚠ 段）。

### D2 覆盖层解析失败只告警，不静默、不中断

沿用本项目已有的教训（DB 覆盖层那次静默 `pass` 让人排查十分钟）：坏文件不该让整程序起不来，但也**绝不能一声不响地当没看见**——那会表现成"改了配置不生效"。所以 `except` 里必打 `WARNING`，测试用 `assertLogs` 盯住。

### D3 环境变量放最高优先级

容器里必须绑 `0.0.0.0`（`127.0.0.1` 时 `-p 8301:8301` 转不进去）。这一层放在 DB 覆盖层**之后**，因为它属于"部署环境"而不是"用户设置"。
**不设这两个变量时，`Settings` 的行为与改动前完全一致**——不改既有键语义，只多一层可选覆盖（符合 06 场定的"只新增不改"硬规矩）。

### D4 模板入库、真值不入库

`settings.local.example.toml` 入库（含说明与示例值），`settings.local.toml` 被忽略。这样别人 clone 后知道该建什么、又不至于把本机端口/profile 带进仓库。

## 3. 改动文件清单

| 类型 | 文件 | 说明 |
|---|---|---|
| 新增（入库） | `config/settings.local.example.toml` | 模板：平台段/面板段/`[gxks]` 段 + 用法与加载顺序说明 |
| 新增（**不入库**） | `config/settings.local.toml` | 本机真值（gxks / 8301 / `[gxks]`） |
| 新增 | `tests/test_config_local_override.py` | 7 条：覆盖生效、无覆盖层、坏覆盖层、env 覆盖、env 建段、坏 env 值 |
| 修改 | `src/xxt/config.py` | docstring 改成分层说明；新增本机覆盖层；新增 `XXT_PANEL_HOST/PORT` |
| 修改 | `.gitignore` | 忽略 `config/settings.local.toml` |
| 修改 | `config/settings.toml` | **还原成 main 原样**（`git checkout main -- config/settings.toml`） |
| 修改 | `docs/gxks/PLAN-GXKS.md` | §九 变更记录 +1 条；§13.6 P0 标 ✅ 并补"P0 已落地"与 ⚠ 副作用 |

## 4. 验收证据（原文）

```
$ .venv\Scripts\python.exe -c "...Settings()..."
platform=gxks panel={'host': '127.0.0.1', 'port': 8301}
gxks_keys=['allowed_types', 'auto_submit', 'port_base', 'work_mode'] reconcile=True

$ $env:XXT_PANEL_HOST="0.0.0.0"; $env:XXT_PANEL_PORT="8399"; ...
panel={'host': '0.0.0.0', 'port': 8399}
--- bad value (XXT_PANEL_PORT=abc) ---
panel={'host': '127.0.0.1', 'port': 8301}      ← 非法值被忽略，回落到配置文件

$ serve() 打桩 uvicorn.run
[OK] 第二平台面板：http://127.0.0.1:8301（Ctrl+C 停止）
bind = {'host': '127.0.0.1', 'port': 8301}

$ pytest tests/test_config_local_override.py tests/test_m0.py tests/test_panel.py tests/test_gxks_panel.py -q
56 passed, 1 warning

$ git -C E:\xxt status --short
(空)
```

`git status` 里 `config/settings.local.toml` **不出现**——证明忽略规则生效。

## 5. 交接块

- **进行中**：无，P0 已闭环。
- **下一步第一个动作**：做 **P1 浏览器后端抽象** —— `[gxks] browser_backend = "cdp" | "managed"`，新增统一入口（`cdp` 走 `connect_over_cdp` 保持现状；`managed` 复用 `core/browser.py:125 persistent_launch`，profile 落 `data/profiles/<label>`），只新增函数、不改 `launch()`/`hardened_context()` 的既有行为。
- **阻塞点**：无。仍有一项待用户点头（P3 才用得上）：根目录新增 `.dockerignore`。

## 6. 用户下一步操作清单

1. **换机器/新克隆时**先 `copy config\settings.local.example.toml config\settings.local.toml`，否则本机会按超星配置跑（端口退回缺省、`[gxks]` 段缺失）。
2. **确认要不要现在做 P1**（浏览器后端，约 2 小时）：做完之后"面板自己起浏览器"就有了地基，但界面还没变（那是 P2）。
3. **`.dockerignore` 何时加**：可以跟 P3 一起，也可以现在加（它对超星构建也是好事，但要你点头）。
