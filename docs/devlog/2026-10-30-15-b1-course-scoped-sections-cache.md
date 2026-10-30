# 2026-10-30-15 B1 落地：节列表缓存按课程分文件 + 界面现状测量

## 一、本次目标

用户问「我们前面说的界面再优化，要现在开工，还是要下一步 B1？」。
我先把**界面现状量了一遍**（不靠感觉），再让用户选，用户选：

- **先做 B1**（而不是先改界面）；
- **B1 按课程分文件**（`sections_<代号>_<课程>.json`）。

## 二、界面测量结果（回答"要不要现在改界面"）

用 Playwright 在 1440×900 / 1024×768 / 760×900 三个宽度各量一次
（`data/debug/_measure_panel.py`，只读，不点按钮、不访问平台）：

| 项 | 1440 | 1024 | 760 |
|---|---|---|---|
| 横向滚动 | 无 | 无 | 无 |
| 卡片按钮 | 5 个 / **1 行** | 5 个 / 1 行 | 5 个 / 1 行 |
| 按钮溢出 | 无 | 无 | 无 |
| 文字截断 | 0 处 | 0 处 | 0 处 |
| 文档总高 | 1656 | 1656 | 1735 |
| 字号梯度 | body 15 / h1 18 / h2 15 / table 14 / kv 13 / 重点数字 16 | 同左 | 同左 |

**结论：当前版式没有硬伤**（无溢出、无截断、按钮不换行、字号有梯度）。

但量出**一个真问题**：`section{grid-column:span 12}` 让**每个区块都占满整行**，
所以「账号区」和「浏览器区」**从来没有并排过**——devlog 11 记录的"宽屏 6+6 并排"
在后续改版里丢了，只剩 12 栏容器的壳。后果：文档 1656px，
「本次作答」「操作记录」都在首屏之外（`sectionsBelowFold` 实测）。

⇒ 记入台账，**不在本轮改**（用户选了先做 B1）。改动很小（一个 `grid-column` + 一条 `@media`），
等用户真用出别扭再动，避免猜着改。

## 三、B1 落地

### 3.1 为什么先收敛"文件名规则"

`sections_<label>.json` 这个文件名原来被**三处各拼各的**：

| 位置 | 干什么 |
|---|---|
| `scripts/gxks_sections.py:102` | 写 |
| `panel_gxks/app.py` `_account_cards` | 读（卡片） |
| `panel_gxks/app.py` `/api/state` | 读（节进度表） |

散着拼，加课程维度时**必然有一处漏改**，表现就是"脚本说写了、面板说没有"。
所以先新建 `adapters/gxks/sections_cache.py`，把命名规则收成一处。

### 3.2 命名规则

| 情形 | 文件名 |
|---|---|
| 有课程 value | `sections_<代号>_<课程>.json`（如 `sections_default_32.json`） |
| 无课程 / 单课程 / 旧数据 | `sections_<代号>.json`（如 `sections_default.json`） |

**外部输入不进路径**：课程 value 来自页面 DOM（`ddlClass` 的 option value），属外部输入。
`safe_course()` 只放行 `[0-9A-Za-z_-]{1,32}`，中文 / 斜杠 / `..` / 空格 / 点 一律退回"无课程"的名字。
测试用 7 个恶意样例钉死（`test_unsafe_course_never_enters_the_path`）。

### 3.3 老文件必须还能读（升级不能"一升级就空白"）

升级前所有缓存都不带课程。读的时候按两段查找：

```
先 sections_<代号>_<课程>.json  →  再回落 sections_<代号>.json
```

实测：改造后打开面板，`sections.total=9` 照常显示（读的就是那份 13:04 的老文件）。

### 3.4 ⚠ 读取优先级**不能**交给 mtime

第一版实现是"取该账号 mtime 最新的一份"。测试当场抓到反例：
两个文件同一秒写入时，**老文件可能赢**，把新数据盖住。
"老文件盖住新数据"比"读不到"难发现得多。

改成**先按文件名是否带课程分两档，档内才比 mtime**：

1. 面板明确指定课程 → 精确读那一份；
2. **带课程的缓存优先于老文件**，同类里取 mtime 最新；
3. 最后才回落老文件。

### 3.5 为什么不做"合并所有课程"

节 ID 在不同课程间可能撞车（与账本 `course + section_id` 去重是同一个教训）。
合并出来的表会把两门课的节混在一屏，比"只显示最近一门"更误导。
卡片上给了 `courses_cached` 数字（"共 N 门课有缓存"），需要看别的课就指定 course。

### 3.6 改动点

- `scripts/gxks_sections.py`：新增 `--course`（切课失败直接停，不猜切换方式）；
  写文件走 `sections_cache.path()`；`--cache-only` 也按同一套规则回落；
  `gx_ops` 的 detail 带上"课程 N"。
- `panel_gxks/app.py`：新增 `_pick_sections_cache()`；`/api/state` 增 `course` 入参与出参；
  `/api/sections/refresh` 接受 `course`；卡片增 `course_value` / `courses_cached`。
- `panel_gxks/page.py`：`refreshSections()` 带上当前课程；卡片「刷新节列表」传该卡的课程；
  卡片「课程」行在多课程时显示"（共 N 门课有缓存）"。

## 四、验证

| 项 | 结果 |
|---|---|
| `pytest` 全量 | ✅ **653 passed**（632 → 653，+21） |
| `test_gxks_sections_cache.py` | ✅ 新增 **17 条**（命名 / 7 个恶意课程值 / 读取优先级 / 去重 / 不串账号 / mtime 排序） |
| `test_gxks_panel_cards.py` | ✅ **+5 条**（老文件可读 / 带课程优先 / `courses_cached` 计数 / `/api/state?course=` 精确命中） |
| 面板重启 | ✅ pid 22312，读老文件正常（`total=9`，`courses_cached=1`） |
| **端到端**（造一份 `sections_default_32.json`，12 节） | ✅ 三种情形全对：<br>· 不带 `course` → `course='32' total=12`（**优先带课程的**）<br>· `course=32` → 精确命中 `12`<br>· `course=99`（不存在）→ 合理回落 `32 / 12` |
| 清理 | ✅ 删掉测试缓存，恢复为只有 `sections_default.json`（9 节） |
| 未验 | ⚠ **真实刷课落盘未跑**：Chrome 9222 没开（`port_alive=False`），我不擅自起浏览器访问平台。`--course` 的真实切课也未验（Q30：当前账号只有 1 门课） |

## 五、关键决策及理由

1. **文件名规则收敛到一处**：三处各拼必有一处漏改，这类 bug 表现为"数据写对了但读不到"，
   排查成本远高于一开始就收敛。
2. **老文件兼容读、但不兼容写**：写一律走新规则，读才回落。这样升级后不会突然空白，
   也不会一直维护两套写入路径。
3. **非法课程 value 退回"无课程"**：宁可少一个维度，也不让外部输入拼进路径。
4. **优先级不靠 mtime**：见 §3.4，这是测试当场抓出来的。
5. **不合并多课程缓存**：节 ID 可能撞车，合并比单课程视图更误导。

## 六、改动文件清单

| 文件 | 改了什么 |
|---|---|
| `src/xxt/adapters/gxks/sections_cache.py` | **新增**：`safe_course` / `filename` / `path` / `legacy_path` / `read_paths` / `list_all` |
| `scripts/gxks_sections.py` | 新增 `--course`；写/读都走 `sections_cache`；`--cache-only` 回落老文件；detail 带课程 |
| `src/xxt/panel_gxks/app.py` | 新增 `_pick_sections_cache()`（带课程优先、mtime 次之、老文件兜底）；`/api/state` 增 `course` 入参/出参；`/api/sections/refresh` 接受 `course`；卡片增 `course_value` / `courses_cached` |
| `src/xxt/panel_gxks/page.py` | `refreshSections()` 带当前课程；卡片刷新按钮传该卡课程；多课程时「课程」行显示"共 N 门课有缓存" |
| `tests/test_gxks_sections_cache.py` | **新增 17 条** |
| `tests/test_gxks_panel_cards.py` | **+5 条** |
| `data/debug/_measure_panel.py` | **新增**（诊断脚本，不入库）：只读量版面，输出 `shots/layout.json` |
| `data/debug/_shoot_panel.py` | **新增**（诊断脚本，不入库）：只读截图三档宽度 |
| `docs/gxks/TODO-GXKS.md` | B1 勾掉；P3 第 5 条标为已解决 |

## 七、已知边界

1. **真实刷课落盘未验**（Chrome 没开）：`sections_<代号>_<课程>.json` 只在端到端造数据时验过，
   真实 `--all` 跑一遍还没做；
2. **`--course` 真实切课未验**（Q30：当前账号只有 1 门课，`ddlClass` 只有 1 个 option）；
3. **界面并排问题仍在**（§二）：`section{grid-column:span 12}` 让所有区块独占整行，
   首屏看不到「本次作答/操作记录」。已记台账，等用户发话再改。

## 八、交接块

- **进行中**：无。B1 已落地并实跑验收（653 全绿）；
- **下一步第一个动作**：**B9 代理自检**（20 分钟）——它是刷课前置的安全阀：
  v2rayN 重启会写回系统代理，`updateTime.ashx` 每 60 秒上报被拖 24 秒，**学时真丢而页面看起来在播**。
  或在用户要求时先修界面并排（30 分钟）；
- **阻塞点**：无技术阻塞。两个"未验"都需要**Chrome 开着且已登录**（用户配合）。

## 九、用户下一步操作清单

1. **想看 B1 真实效果**：点面板上的「刷新节列表」（Chrome 9222 需已登录），
   之后 `data/debug/` 里会出现 `sections_default_<课程>.json`（带课程 value 的文件名）；
2. **要不要先修界面并排**（30 分钟）：把「账号区」+「浏览器区」改成宽屏 6+6 并排，
   文档高度能降约 300px，「本次作答」能进首屏；
3. **要不要先做 B9 代理自检**（20 分钟）：刷课前读 `ProxyEnable` + 握手计时，超 3 秒拒绝起跑。
