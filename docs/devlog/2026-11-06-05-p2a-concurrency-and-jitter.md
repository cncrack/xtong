# 2026-11-06-05 P2a：并发闸 + 启动错峰（做成面板可配页项）

## 一、用户提问与前置查证

用户原话：「并发上限 + 启动随机错峰，这个并发上限，弄成在面板中设置成可配置页项，已经有方案了吗」

**答：有方案**，且面板里**已经有「面板设置」卡片**可以放（现在放着「热重载」「刷新间隔」）。
但**方案与代码之间有缺口**，逐项核对如下：

| 方案要求（`PLAN-GXKS.md`） | 落地前实际 | 本次 |
|---|---|---|
| `runtime.max_concurrency` 全局配置 | **完全没有**（全模块搜不到） | ✅ 新增，且**做进面板页** |
| 超额时排队（§11.5） | 勾几个就同时起几个 | ⏸ 本次做**软闸**（拒绝），队列留 P2b |
| 启动随机错峰（§7.3） | `run_gxks.py` 有 `--start-jitter`，但批量没传 | ✅ 接线 |
| 面板可配 | 面板设置卡片存在，只有 2 项 | ✅ 加 2 项 |

方案原文要点（都已遵守）：

- 并发上限是**全局**（`runtime.max_concurrency`），与「账号日额度」是两回事；
- **双限速**：本机资源 + 出口 IP；
- 风险页明确「配置写 10，但**首日先手动压到 2~3** 跑通，确认无 503/验证码再放开」；
- D12：核实期只能 1 个会话 —— **本站并发面完全未验证**；
- 明确**不做**：不为试探并发上限而故意多开。

## 二、与用户确认的范围

| 问题 | 用户选择 |
|---|---|
| P2 做到哪层 | **P2a：软并发闸 + 随机错峰**（不做完整队列） |
| 默认并发上限 | **2** |

## 三、实现

### `src/xxt/store/gxks_panel_settings.py`

新增两项配置（进 `DEFAULTS` + `load` 解析 + `save` 白名单）：

| 键 | 默认 | 范围 | 语义 |
|---|---|---|---|
| `max_concurrency` | **2** | 1~20 | 全局同时跑几个**学习**任务 |
| `start_jitter` | **30** | 0~120 | 批量时每个账号随机等 0~N 秒 |

⚠ **`max_concurrency` 不许 0**（钳成 1）：在额度语境里 0 = 不限，但这一项的意思是「限几个」，
语义反过来等于闸门失效 —— 是最危险的一种「看起来配了」。

### `src/xxt/panel_gxks/app.py`

1. **`_live_study_count()`** —— 只数 `kind == "study"` 且 `poll() is None` 的任务。
   节列表刷新、练习作答虽是子进程，但属**短任务**且受「该账号已有任务在跑」约束，
   不占学习并发位；若计入，用户点一次「刷新节列表」就可能把学习挤掉。
2. **`_concurrency_limit()` / `_start_jitter()`** —— 读配置，异常时回**保守值**（2 / 30）。
3. **`_check_concurrency()`** —— 满了返回 `{blocked, concurrency_full, live, limit, error}`，
   错误文案**给出解决路径**（先停一个 / 去面板设置调高），不只说「不行」。
4. **`_study_one()`** 加两个参数：
   - `check_concurrency`：批量时关掉逐个判定（批量要先算配额）；
   - `jitter`：传给子进程 `--start-jitter`。
   ⚠ 错峰**必须在子进程里等**，不能在面板里 `sleep` —— 否则整个 api 被卡，
   批量 5 个就是几分钟无响应。
   单账号「学习」也受并发闸约束，否则用户绕开批量就能突破上限。
5. **批量 `start` 按配额起**：`room = 上限 - 已在跑`，`started_n >= room` 的标 `blocked`
   （不是全起、也不是全不起 —— 能起几个起几个）。
6. **错峰只在批量 ≥2 个时加**：单个账号不该凭空等 30 秒。
7. **`/api/state` 新增** `limit_concurrency` / `live_study` / `start_jitter`
   —— 前端批量栏要显示「并发 N/M」。
8. 汇总文案修正：`blocked` 有两种来源（**并发已满** vs **有任务在跑需先停**），
   必须分开说，否则用户会为「并发满」白找一圈「哪个任务在跑」。

### `src/xxt/panel_gxks/page.py`

- 面板设置加两个字段：
  - 「同时学习上限」（默认 2，`min=1 max=20`）+ 说明（含「为什么默认不给大」）；
  - 「启动错峰」（默认 30，`min=0 max=120`）+ 说明（只在批量 ≥2 生效）；
- `loadPanelSettings` / `savePanelSettings` 读写接线（含 JS 侧钳制）；
- 批量栏加「**并发 N/M**」标签，满了用红色（`t-low`），
  并在「开始学习」的 `title` 里写「本次最多再起 N 个」或「⚠ 并发已满，点了会被拒」。

## 四、过程中查出的一个**真 bug**（值钱）

### `save()` 静默失败 —— 面板设置改了没效果

`gxks_panel_settings.save()` 里 `_write` 直接 `INSERT`，而**库里没有 `meta` 表**时
抛 `no such table: meta`，被 `except sqlite3.Error: pass` **静默吞掉**。

后果：接口回「已保存」、`load()` 却还是默认值 —— 表现为
**「面板上改了设置、保存也说成功，但一点效果都没有」**。

我是在给 P2a 写测试时才撞到的（`_set(max_concurrency=3)` 之后读出来还是 2，
一开始误以为是测试的临时目录分叉，查到根上才发现是 `save` 根本没写进去）。

修法：`save()` 里先 `CREATE TABLE IF NOT EXISTS meta`（幂等），再做写入。
已补回归测试 `test_save_creates_meta_table_when_missing`。

> 这个 bug 不只影响 P2a：「热重载」「刷新间隔」两个老设置**在空库环境下同样存不下来**。
> 只是真实库早就有 `meta` 表，所以一直没暴露。

## 五、验证

- `pytest -q`：**1120 passed**（15 subtests）
- 新增 `tests/test_gxks_concurrency.py`（21 条）：配置钳制（含 0→1）、
  只数 study、死任务不计、闸门满/不满、单账号也受闸、
  `/api/state` 字段、批量按配额起、已在跑占配额、全满全拒、
  错峰只批量传、0 不传、页面控件与接线、批量栏并发标签；
- `test_gxks_panel_settings.py` +2：`save` 建表、新键落盘
- 页面 JS `node --check` 通过
- **实机**（重启面板后）：
  ```
  DB: {..., 'max_concurrency': 2, 'start_jitter': 30}
  /api/state: limit_concurrency=2  live_study=0  start_jitter=30.0
  页面: f-concurrency / f-jitter / 「并发 」标签 / save 里带 max_concurrency  全部 OK
  拒绝路径（模拟 live=2）：blocked=True full=True live/limit=2/2
      error: 同时学习上限是 2 个，现在已经有 2 个在跑。| 要么先「停止」一个，要么到「账号设置 → 面板设置」把上限调高。
  ```

## 六、改动文件清单

- `src/xxt/store/gxks_panel_settings.py`（+2 配置项、`save` 建表、解析/钳制）
- `src/xxt/panel_gxks/app.py`（`_live_study_count` / `_concurrency_limit` /
  `_start_jitter` / `_check_concurrency` / `_study_one` 两参 / 批量配额 / state 字段）
- `src/xxt/panel_gxks/page.py`（面板设置 2 项 + 批量栏并发标签 + 接线）
- `tests/test_gxks_concurrency.py`（**新增 21 条**）
- `tests/test_gxks_panel_settings.py`（+2）
- `data/debug/diag_p2a_concurrency.py`（新增，只读）

## 七、交接

**进行中**：无。

**下一步第一个动作**：用户 `Ctrl+F5` 后到「账号设置 → 面板设置」看两项新配置；
批量栏应显示「并发 0/2」。

**阻塞点**：无。

**未做的部分（P2b，等用户点名）**：

- **队列**：`PLAN-GXKS.md` §11.5 写的是「否则按并发上限排队」。现在是**拒绝**不是排队。
  做队列需要：常驻调度循环 + 面板重启后重建队列（与孤儿任务认领同类问题）。
- **`fleet_daily_minutes`**（全部账号合计的日总量闸）：方案里有、目前没做，也没接面板。

**已知边界**：

- `live_study` 只数 `kind == "study"`。若以后把「练习作答」也做成长时间任务，
  这个口径要重新考虑（现在练习是几分钟的短任务，且被账号锁挡住）。
- 并发闸是**进程内**判定（`_JOBS`），面板重启后靠 `adopt_orphan_jobs` 认领恢复计数；
  认领失败时计数会归零 → 有可能实际在跑 2 个、面板以为 0 个。
  这是软闸的固有局限，真做队列时要一起解决。
