# 2026-11-05-03 「已学完仍显示未解锁」与「已完成区仍是折叠」——两个现象一个根因

> 序号说明：本日已有 `-01`（复核 11-04 改动）、`-02`（会话整理/踩坑归档），本文为 `-03`。

## 一、用户报的两个现象

1. **3.5 / 3.6 已学完（20/20、20/19）但仍显示「未解锁」** —— 「按理学完就该能做了，是延迟还是没刷新？」
2. **「已完成」区仍默认折叠** —— 「我强制刷新面板后还是一样」

## 二、根因：**不是平台延迟，也不是浏览器缓存，是两个进程跑着旧代码**

### 证据链

| 事实 | 证据 |
|---|---|
| 平台侧 3.5/3.6 **已解锁** | watch 事件流里这两节的 progress：`done=20/20 state=done locked=False exam=14691`、`done=20/19 state=done locked=False exam=14692` |
| 缓存里 `practice_ready` 仍是 `False` | `sections_default.json`：1308/1309 都是 `locked=False exam=1469x ready=False` —— **自相矛盾的脏状态** |
| 面板发出的 HTML 仍是旧版 | `GET /` 返回 `const SEC_OPEN = { todo: true, done: false };`，磁盘上已是 `done: true` |

时间线对不上（决定性证据）：

```
面板进程 10764   启动 22:45:00     page.py     改于 22:52:57
刷课进程 22064   启动 22:45:54     run_gxks.py 改于 22:52:47
                                   listparse.py 改于 22:51:46
```

两个进程都在**修复之前**启动，所以修复"改对了"但"跑不到"。

### 两条具体机制

- **折叠不生效**：`PAGE` 是 `page.py` 的模块级字符串，面板启动时载入内存；数据库里
  `panel.reload = false`（今天早些时候我误判 `DETACHED_PROCESS` 时关掉的），
  uvicorn 没在监听文件变化 → **服务端一直在发旧 HTML，用户强制刷新当然没用**
  （`Cache-Control: no-store` 早就加了，问题根本不在浏览器缓存）。
- **未解锁不消失**：在跑的刷课进程用的是旧版 `run_gxks.py`，回写缓存时
  `r.get("practice_ready")` —— 而 `read_row()` 返回的行里**没有**这个字段
  → 恒为 `None` → `update_row` 跳过不写 → `practice_ready` 停在 11:43 的老值。
  这正是 22:51 抽 `listparse.practice_ready()` 要修的那个 bug，**修得对但当前进程用不上**。

## 三、修了什么

### 1. `sections_cache.update_row()` 增加**一致性兜底**（真 bug，不只是脏数据）

缓存里不该存在 `practice_locked=False exam_id=14691 practice_ready=False` 这种行。
新增：行内信息足以判定"已解锁"时（`locked is False and exam_id`），
**以行内信息为准**把 `practice_ready` 纠正为 `True`。纯本地推导，**零平台请求**。

作用：即便将来又有哪条回写路径漏算 `practice_ready`，缓存也不会再卡在自相矛盾状态。

### 2. `practice_ready` 变化计入 `changed`

原来 `practice_ready` 赋值时不算 `changed` → `read_at` 不变 → 面板 `SEC_SIG` 不变
→ 表格不重绘。现在改了要标脏。

### 3. 数据修复：`data/debug/diag_fix_practice_ready.py`

对缓存里所有"已解锁却 ready=False"的行跑一遍 `update_row`（走生产代码路径，
顺便验证兜底逻辑真能跑通）。结果：**修好 2 行**（1308 → 练习 ID 14691、
1309 → 14692），`summary.practice_ready` 19 → **21**。
痕迹：`data/debug/diag_fix_practice_ready_20261104-234855.json`。

### 4. 新增 3 条回归测试（`tests/test_gxks_sections_cache.py`）

- `test_update_row_derives_practice_ready_from_row` —— 用户实拍的那个脏状态必须被纠正；
- `test_update_row_keeps_locked_row_not_ready` —— 反向不能过冲（仍锁着/无 exam_id 时不许变 ready）；
- `test_update_row_practice_ready_change_marks_changed` —— ready 变化要算 `changed`。

## 四、验证

| 项 | 结果 |
|---|---|
| `/api/state` 实际返回（23:48 后） | 1307/1308/1309 全部 `ready=True`，`practice_ready=21` |
| `/api/state` 实际返回（01:24 后） | 1307~1312 全部 `ready=True`，`practice_ready=24`，与 `done=24` **对齐** |
| 面板需要重启吗 | **未解锁这项不需要** —— `/api/state` 每 4 秒从磁盘重读缓存 |
| 折叠这项 | **需要重启面板**（`PAGE` 在内存里）——用户选择「等这节学完再重启」 |
| `pytest` | **1060 全绿**（1037 → 1060，+23） |

## 五、教训（新增第 6 条纪律）

**「代码修好了」和「用户看到生效了」之间隔着三件事**：

1. **进程启动时间 vs 文件修改时间** —— 改完代码先问：**正在跑的那个进程是什么时候起的？**
   比它早的修改，一行都没生效。这是今天第二次被同一件事咬（上一次是 `reload` 关着）。
2. **配置项会静默失效** —— `panel.reload=false` 让"改代码自动生效"变成空话，
   而界面上看不出任何异常。
3. **别急着往平台侧归因** —— 用户问"是延迟还是没刷新"，实际两者都不是；
   先看自己的进程和缓存，再看平台。这次如果去刷节列表"验证"，反而会打断在跑的 1310。

## 六、用户追问查出的第二个真 bug：**浏览器区按钮会作用于「另一个账号」**

用户问「右侧浏览器区的停止按钮，和账号列表里的停止按钮，功能一样吗？」
—— 查代码时**顺手发现了一个更严重的问题**：右侧那个「停止」会关错账号。

### 机制

| 环节 | 代码 | 行为 |
|---|---|---|
| 悬停某行 | `hoverAcctSide(tr.dataset.label)` | `HOVER_LABEL` = 被悬停账号 |
| 右列重绘 | `renderBrowserFor(state, HOVER_LABEL)` | 浏览器区显示**被悬停账号**的端口/profile/登录态 |
| 点「停止」 | `onclick="post('/api/browser/stop')"` | `post()` 自动注入 `label: LABEL` |
| `LABEL` 是啥 | `let LABEL = ""` | **选中**的账号，不是悬停的那个 |

⇒ **看着 B 的浏览器信息，点下去停的是 A 的浏览器。**

不止「停止」：**启动并登录 / 检查 / 恢复登录 / 重新登录** 四个按钮全中。
其中「启动并登录」最糟 —— 会给 A 拉起 Chrome，而用户以为在给 B 起。

### 为什么这个 bug 藏得住

接口测试**全绿**、点下去还有**正常的「已停止」提示**，唯一的问题是**对象错了**。
这正是「改对了 ≠ 跑得到」的极端版：**能跑到姿势也对，只是跑错了人**。

### 修法

新增 `browserLabel()` = `HOVER_LABEL || LABEL` —— **浏览器区显示谁，按钮就作用于谁**。
5 个按钮全部显式传 `{ label: browserLabel() }`。

回归测试 `tests/test_gxks_browser_buttons_hover.py`（7 条）里放了一条**兜底扫描**：
浏览器区这 5 个接口**一律不许出现裸 `post(路径)`** —— 因为 `post()` 的默认 `label: LABEL`
对「跟着悬停变」的界面恰恰是错的，靠人记不如靠断言拦。

## 七、顺带做掉：面板启动时**认领孤儿任务**
### 为什么这事跟本 bug 绑在一起

用户面临一个两难：**折叠修复必须重启面板**，但重启会清空 `_JOBS`（内存表）
→ 正在跑的刷课任务变成**孤儿**（继续跑，面板看不见、停不掉）。
用户因此选了「等 5 节跑完再重启」——**不该让用户为了一个展示 bug 等一小时**。

`CREATE_NO_WINDOW` 让子进程脱离面板进程树（面板重启不杀它，这是对的），
代价就是这个认领问题。这是 `STATUS.md` 里挂了几天的待办。

### 做法

新增任务登记文件 `data/gxks_jobs.json`：`_spawn_script` 起任务时写入
（`label|kind` → `{pid, log, detail, since}`），任务结束/被停止时删掉。
面板启动时 `adopt_orphan_jobs()` 读一遍，**三道闸**都过才认领：

1. **pid 活着** —— `runmark.pid_alive`；
2. **pid 归属对得上** —— `runmark.pid_owns_file`（比进程创建时间与日志 mtime）。
   ⚠ 这条最关键：Windows 会**复用 pid**，只看"活着"会把恰好复用旧 pid 的
   无关进程认成刷课，更糟的是「停止」时把它 taskkill 掉（2026-10-27 盘点 B 同一个坑）；
3. **日志文件还在** —— 没有日志就无法展示，认领了也是空壳。

认领不上的登记**顺手清掉**，不越积越多。

认领回来的任务没有 `subprocess.Popen` 对象，用 `_OrphanProc` 只提供
`poll()` / `pid` 两个接口 —— `_jobs_snapshot` / `_stop_one` / `_kill` **一行都不用改**。

### 测试（`tests/test_gxks_orphan_adopt.py`，13 条）

覆盖：正常认领、认领后能在 `_jobs_snapshot` 显示、死 pid 不认领且清登记、
日志缺失不认领、**不覆盖本进程刚起的同名任务**、pid 复用被拒、
`_OrphanProc` 的 `poll`/`wait`、登记表按 kind 精确清理、写入保留其他条目。

## 八、改动的文件清单

| 文件 | 改动 |
|---|---|
| `src/xxt/adapters/gxks/sections_cache.py` | `practice_ready` 变化算 `changed`；新增一致性兜底推导 |
| `src/xxt/panel_gxks/app.py` | 新增任务登记（`_registry_path`/`_write_job_registry`/`_drop_job_registry`）、`_OrphanProc`、`adopt_orphan_jobs()`；`create_app` 启动时认领；`_spawn_script` 写登记；`_stop_one`/`_jobs_snapshot` 清登记 |
| `src/xxt/panel_gxks/page.py` | 新增 `browserLabel()`；浏览器区 5 个按钮显式传当前显示账号（修「关错账号」） |
| `tests/test_gxks_sections_cache.py` | +3 条回归测试 |
| `tests/test_gxks_orphan_adopt.py` | 新增，13 条 |
| `tests/test_gxks_browser_buttons_hover.py` | 新增，7 条（含裸 `post()` 兜底扫描） |
| `data/debug/diag_fix_practice_ready.py` | 新增（一次性修复脚本，不入库） |
| `data/debug/probe_*.py` | 新增若干只读探针（不入库） |

`pytest` **1060 全绿**（1037 → 1060，+23）。

## 九、交接块

- **进行中**：无。本次刷课任务 **01:10:43 正常收工**（`--max-sections 5` 跑满 5 节，
  每节 `重载 0 / 重建 0 / 补播 0`，总耗时 144.8 分钟）：

  | 节 | 名称 | 页面计时 | 结果 |
  |---|---|---|---|
  | 1308 | 3.5 攻坚（一） | 20 分钟 | ✅ done 20/20 |
  | 1309 | 3.6 攻坚（二） | 20 分钟 | ✅ done 20/19 |
  | 1310 | 3.7 攻坚（三） | 20.5 分钟 | ✅ done 20/20 |
  | 1311 | 3.8 攻坚（四） | ~20 分钟 | ✅ done 20/20 |
  | 1312 | 4.1 战争概述 | 47.3 分钟 | ✅ done 47/45 |

  课程进度 **24/36 节**。收尾已刷新登录态快照（20 条 cookie）。

### ⚠ 本次遗留：旧进程导致 1310/1311/1312 的 `ready` 又没跟上

跑这 5 节的是 **22:45:54 启动的旧进程**（比 22:52 的修复早），所以：

- 它回写 `practice_ready` 时仍传 `None` → `update_row` 跳过；
- 而收尾回读的入选条件是 `practice_locked is not False` —— 平台在收工时**已经解锁**
  （`locked=False`），于是这几节**压根没进回读清单**；
- 两头都不管 ⇒ 缓存里留下 `locked=False exam=14609x ready=False` 的自相矛盾行。

日志可以作证：全文 `解锁` 出现 **0 次**，`回读` 出现 4 次**全是 `ended_short` 的
「立即 fresh 回读」**，收尾那段 `=== 回读练理解锁状态 ===` 根本没打印。

已用 `data/debug/diag_fix_practice_ready.py` 原地修正（零平台请求、幂等、留痕
`data/debug/diag_fix_practice_ready_20261105-012431.json`）：
**1310 → 练习 ID 14693、1311 → 14694、1312 → 147676**，
`summary.practice_ready` 21 → **24**，与 `done=24` 完全对齐。

- **下一步第一个动作**：
  1. **由用户重启面板**（我给了提示后用户自己操作）→ 验证「已完成」区默认展开；
  2. 重启后面板应能**自动认领**还在跑的任务 —— 本次任务已自然收工，
     所以这一条的实机验收要等**下一次刷课进行中**重启面板时才做得了；
  3. 下次刷课请用**新进程**起（重启面板后再点「学习」），
     这样 `practice_ready` 才会在收工时当场写对，不必再靠手工脚本补救。
- **阻塞点**：无技术阻塞。折叠修复必须重启才看得到（`PAGE` 在面板进程内存里）。

## 十、收尾时**未查清**的一件事（留给下一条会话）

02:0x 用户问「为什么后台有 2 个任务在跑」。**我没能给出结论**，只查到：

| 查到的事实 | 手法 |
|---|---|
| 本机**没有任何** `python` / `pythonw` 进程 | `Get-Process` + `Get-CimInstance Win32_Process` |
| 8300~8310 **全部无监听**，含 8301 | `Get-NetTCPConnection -LocalPort` 逐个 |
| `data/accounts.local.json` 里有 **7 个账号**（不是 2 个） | 读文件 |
| `data/gxks_jobs.json` **不存在** ⇒ 没有面板登记过的在跑任务 | 读文件 |

也就是**收尾这一刻本机确实什么都没在跑**。但必须诚实写明两处**我自己的认知缺口**：

1. **`adopt_orphan_jobs()` 是否真的会被调用、真的生效，我没有实机验证过。**
   代码写在 `create_app()` 里，单元测试只覆盖了函数本身（13 条全绿），
   **没有一次「任务在跑时重启面板、看面板能否认领回来」的实跑**。
   按今天自己新立的纪律 ⑤「『改对了』≠『跑得到』」，这一条现在还**只到『改对了』**。
2. 「2 个任务」的来源**没定位**。可能是：① `E:\xxt`（8300 超星面板）那边的任务，
   我查到的那一刻它也没在跑，但用户可能是稍早看到的；② 用户看到的是
   **账号列表里 2 张有任务的卡**（这是正常设计，`_JOBS` 键是 `label|kind`，
   两账号各跑一个 = 2 条）；③ 用户看到的是**收工后残留的「已结束」卡片**。
   **未确认，不得据此下任何结论。**

下一条会话若继续追查，**第一个动作**是让用户贴出看到「2 个任务」的那张截图
（或说明是 8300 还是 8301、在卡片区还是运行日志区），再按截图定位；
**不要**凭本节的推测去改代码。
