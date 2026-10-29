# 2026-10-30-06 第二平台：部署形态、提交确认与超星兼容边界（纯设计会话）

> 本场**没有改任何代码**。产出全部是判定与规划：先把"能不能、怎么拆、会不会撞超星"问清，明天照 §5 的顺序动手。
> 平台事实见 `docs/gxks/gaoxiaokaoshi.md`；排期与治理见 `docs/gxks/PLAN-GXKS.md`（本次新增 §十三）。本文不复制那两份的内容。

## 1. 本次目标与结果

用户连问三层，逐条给了结论：

| # | 用户问题 | 结论 |
|---|---|---|
| 1 | 回填没有保存，要么直接提交要么退出 —— 能不能"容器回填、本机交卷"？ | **不能拆**。平台侧无草稿（详见 §2 D1），练习作答必须单一环境闭环 |
| 2 | 刷课 Docker、答案外面起浏览器？ | **不是这个意思**。刷课与练习是两件独立的事，各自闭环：刷课进容器、练习留本机 |
| 3 | 自动提交前能不能在面板上先显示内容让人确认"你提交呢"？ | **能，且推荐**（形态 C：面板二次确认 → 脚本自己点交卷），见 §2 D2 |
| 4 | 容器能不能对外提供操作面板、和本机一样用？ | **能，同一套代码同一套 UI**；差别只在"有没有人看得见浏览器窗口"，见 §2 D3 |
| 5 | 和超星会有兼容性问题吗？ | 分 8 层逐条核过：**代码层可零影响**，真正的耦合点是**共享的 `config/settings.toml`** 与**共享的 git 历史**，见 §3 |

## 2. 关键决策

### D1 练习作答不拆环境（依据：平台无草稿）

用户原话：「这个平台里面的回填是没有保存的，要么直接提交，要么退出」。

代码侧对得上：草稿路径 `SaveMode=0` 只存在于 `btnCancel_onclick()`，**界面上没有保存按钮**（Q28 已实测）。所以不存在"存草稿换环境再交"的桥。

⇒ 作废本场之前我提的「Docker 回填 + 本机交卷」。练习只有两种合法形态（A 半自动 / B 全自动），本场又加了第三种：

| 形态 | 谁点交卷 | 环境 | 前提 / 风险 |
|---|---|---|---|
| A 半自动 | 人（在同一窗口里核对后点） | 本机有头 Chrome | 脚本填完必须**断开 CDP**，否则吞交卷 confirm（Q31） |
| B 全自动 | 脚本 | 容器/无头均可 | `auto_submit = true`，答错风险自负 |
| **C 面板确认（新增，推荐）** | 脚本，但**由人在面板点"确认提交"触发** | 本机或容器均可 | 回填后断开 CDP → 面板展示整卷 → 人点确认 → 脚本重连、注册 dialog accept、点交卷、读分入库 |

C 的要点：**脚本自己 accept confirm**（所以不受 Q31 影响），人只在面板上做"看一眼 + 点一下"，不需要在 Chrome 窗口里操作。比 B 安全（有人看过），比 A 少一步（不用去 Chrome 里点）。

C 的两个硬约束：
1. **窗口期内页面不能关、不能刷新**——无草稿，关了答案就没了；
2. 点确认后必须**先回读校验**（radio 是否仍被选中），对不上就重新回填再交，不把"填了但丢了"当成功。

### D2 容器对外提供同一套面板（可行，3 个前置条件）

同源：容器内跑 `python -m xxt.cli gxks-panel`，宿主机映射 8301，浏览器打开就是**同一个 UI、同一套 API**。

三个必须改的点（都在 P0/P3 里）：

1. **监听地址**：`serve()` 读 `[panel] host`，现在是 `127.0.0.1` → 容器里只绑回环，`-p 8301:8301` **转不进去**。必须 `0.0.0.0`，且要能由环境变量覆盖（`XXT_PANEL_HOST`），否则又得改共享的 settings.toml。
2. **浏览器归谁管**：容器里没有宿主 Chrome，`connect_over_cdp(127.0.0.1)` 连的是容器自己 → 必须切 `managed` 后端（程序自己 `persistent_launch`，profile 落在挂卷的 `data/profiles/<label>`）。
3. **"看得见的窗口"**：无头容器里没人能点交卷 → 只能走 B 或 C。真要窗口就得上 noVNC（本场不排期）。

### D3 刷课与练习分开部署（不是一件事劈两半）

- **刷课**：播放 → 达标 → 记账 → 歇，无人中间态 → 天然适合容器。
- **练习**：有"人点交卷"这个必须中间态 → 按 A/B/C 三选一。

顺带一个容器带来的**好处**：`U7`（宿主机 v2rayN 重启会写回系统代理，把上报拖成 503）在容器里**天然消失**——容器有自己的网络栈，不继承 Windows 系统代理。这条要记在部署文档里。

### D4 兼容性：代码层"只新增不改"是硬规矩

`core/browser.py` / `core/stealth.py` / `config.py` 是两平台共享的：

- `persistent_launch()` / `persistent_launch_kwargs()` **已存在但全仓库零调用**（`grep` 只有定义处），本来就是给第二平台留的 → 用它属纯新增；
- `hardened_context(browser, **kw)` 签名可不动；
- **禁止**改 `launch()` / `hardened_context()` 的默认行为，**禁止**改变 `Settings` 已有键的语义。

### D5 配置与 git 历史是真耦合点（P0 必须先做）

本场只读排查查实：

1. **`E:\xxt` 与 `E:\xxt-gxks` 是同一个 git 仓库的两个 worktree**（`git worktree list`：`E:/xxt [main]`、`E:/xxt-gxks [feat/gxks]`）。分支隔离 HEAD 与工作目录，**不隔离文件血统**。
2. `config/settings.toml` 是被跟踪文件 → 本工作树里它"绝大多数行仍是超星的"，因为 `feat/gxks` 就是从超星历史切出来的。我只替换了 `[platform]` 并新增 `[panel]`/`[gxks]`，其余 8 段原样保留。
3. **本分支落后 main 3 个提交**（分叉点 `4e13b9e`），缺 21 个文件的超星新改动（含 `core/reconcile.py`、`tests/test_chapters_status_sync.py`、`cli.py +140`、`panel/app.py +164`）。落到配置上：**整个 `[reconcile]` 段不存在**，`float_ratio` 还是旧值 `[0.7,1.0]`（main 是 `[0.85,1.0]`）。
4. ⇒ **"改到超星"的真实入口不在编辑，在合并**：谁把 `feat/gxks` 合回 `main`，`[platform]`/`[gxks]`/`float_ratio` 就会冲突甚至顶掉超星的刷前对账配置。这就是 P0（配置本地化）必须前置的原因。

## 3. 只读排查查实的其余事实（明天直接可用）

| 事实 | 证据 |
|---|---|
| **超星目录未被本会话改动** | `git -C E:\xxt status --short` 为空；`E:\xxt\config\settings.toml` 修改时间 `2026/10/29 21:52`（早于本会话） |
| **自动登录早已实现**，缺的只是"起浏览器" | `glogin.login()` 填表+点登录；`gxks_work.py:91`、`gxks_sections.py:122`、`run_gxks.py:132` 三处掉登录自动重登 |
| 全仓库**没有 Chrome 路径探测** | 只有一句提示字符串（`run_gxks_multi.py:38`、`gxks_work.py:157`）→ P1/P2 要新写 |
| **"看 cookie 有效期"不可用** | `selectors.py:36` 说 `.ASPXAUTH` 会话级（`expires=-1`），`adapters/gxks/__init__.py:66` 说它是持久 cookie —— **同包两处自相矛盾**；`login.has_auth_ticket()` 注释亦写明"票在 ≠ 服务端认"（Q20 未定） |
| **唯一可信的会话判据** | 访问一次 `MAIN_PAGE` 看是否被踢到 `Login.aspx`/`Loginb.aspx`（`gdriver.at_login_page`，`LOGIN_PAGES` 两页都认）；这也正是 `GxksAdapter.probe_session` 注释里写的正解（该方法目前仍是 `NotImplementedError` 桩） |
| **无 `.dockerignore`** | `Test-Path .dockerignore` → False。`docker build` 会把整个目录（含 `data/` 里的真实凭据、`.venv/`、`.git/`）当构建上下文传给 daemon：凭据被传输、构建明显变慢。加它是**共享改动**（对超星也有好处），需用户点头 |
| 现有 `deploy/` 是超星的 | `Dockerfile`：`EXPOSE 8300` + `CMD xxt.cli panel`；`docker-compose.yml` 只有 `xxt` 一个服务 → 第二平台要单独一份 |

## 4. 改动文件清单

| 类型 | 文件 |
|---|---|
| 新增 | `docs/devlog/2026-10-30-06-gxks-deploy-shape-and-submit-confirm.md`（本文） |
| 修改 | `docs/gxks/PLAN-GXKS.md`（§九 变更记录 +1 条；新增 **§十三**） |
| 未改 | **零代码改动**。`STATUS.md` 也未改——按 `STATUS.md` §3「主题隔离规则」，超星侧文件对第二平台只允许一行指针，进展写在本篇与本目录 |

## 5. 交接块

- **进行中**：无（本场纯设计，已闭环提交）。
- **下一步第一个动作**：做 **P0 配置隔离** —— 新增 `config/settings.local.toml`（gitignored）+ `Settings` 在 toml 之后、DB 覆盖层之前加一层 `_deep_merge`（现成函数直接复用），并把本分支对 `config/settings.toml` 的改动**全部撤回成超星原样**；同时给面板加 `XXT_PANEL_HOST` / `XXT_PANEL_PORT` 环境变量覆盖（容器必须绑 `0.0.0.0`）。
- **阻塞点**：无技术阻塞。只有一项需要用户点头：根目录新增 `.dockerignore`（共享改动，会影响超星构建——是好事，但要用户确认）。

## 6. 用户下一步操作清单

1. **确认明天从 P0 开工**（约 1 小时）：把第二平台配置搬进 `config/settings.local.toml`，`config/settings.toml` 恢复超星原样。做完之后"合并回 main 会改到超星"这条路就被物理切断。
2. **确认练习提交方式选 C**（面板显示整卷 → 你点"确认提交" → 脚本自己点交卷）：如果不选 C，那就只能在 A（你去 Chrome 里点）和 B（全自动、答错自负）之间选。
3. **`.dockerignore` 是否同意加**：不加的话 `docker build` 会把 `data/` 里的真实凭据当构建上下文传出去、构建也慢。
