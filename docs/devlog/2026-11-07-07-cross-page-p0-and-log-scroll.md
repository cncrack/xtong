# 2026-11-07-07 跨页回读 P0（整节误判「学时不涨」）+ 日志/复选框三处修正

> 用户 2026-11-07 深夜贴出 default 账号的完整日志：
> 「第一个账号的视频没有在播。查一下原因」「日志里面也看不出是谁的日志」
> 「学习进度区里面正在学习的这节没有显示为正在学习」「我标出来的这个点，你能看出来吗」
>
> 前三条都查到了根因，其中**第一条是一个代价很大的 P0**。

---

## 一、🔴 P0：平台记上了，本地误判「学时不涨」→ 整节作废

### 用户看到的现象

```
[23:02:11] ended
[23:02:11] ended_short 视频播完但列表只 None/None（快照可能过期）→ 立即 fresh 回读，仍差再等 240s
[23:04:13] ended
结果 escalate；用时 24.3 分钟；重载 0 / 重建 0 / 补播 0；列表 None/None None
⚠ 本节非正常收场（学时不涨）→ 停，不再换节硬跑
```

但同一份日志里，`tick 学习中（页面计时 1167 秒）`一路涨到 1167 秒（要求 19 分钟 = 1140 秒），
且**没有 `no_report` 告警**（`updateTime.ashx` 每 60 秒上报正常）。

### 只读核实平台后（这步是关键）

写 `data/debug/diag_find1317.py`（只读：只 GET 列表、翻页、解析 DOM，不点「开始学习」）：

```
=== 1317 平台真实数据 ===
{"platform_chapter_id": "1317", "name": "4.6人工智能重塑未来战场",
 "required_minutes": 19, "done_minutes": 19, "state": "done", "done_on_platform": true}
section_status = done
达标(含余量)   = False        ← 只差「余量」那 2 分钟，但「要求」已满足
```

**结论：视频播完了、平台也记上了 19/19 分钟、状态已完成。**
本地报「学时不涨」是**假警报**，代价是这一节被作废、任务停下（用户以为「视频没有在播」）。

### 根因：两个 bug 叠加

**① `read_row()` 只读当前页**

```python
for row in self.refresh_here()["rows"]:   # ← 只有当前页
```

1317 在**第 5 页**（课程共 6 页）→ 永远返回 `None` → `列表 None/None`。

**② 分页按钮用 `elem.click()` 会卡满 60 秒超时**

按钮是 `<a href="javascript:__doPostBack('PageSplit1$BtnPrevious','')">`。
实测：

```
click 抛 TimeoutError: ElementHandle.click: Timeout 60000ms exceeded
改用页内 eval 那个 javascript: 表达式 → 点后 pager: 6 → 5   （立刻成功）
```

`click()` 触发整页回发，Playwright 等 action 完成时页面已走 → 超时 → `prev_page()` 返回 False
→ `refresh_here()` 里「上一页→下一页」的绕缓存动作失效、停在错页。

两个叠加才致命：**翻不动 + 只读当页 = 回读永远失败**。

### 修法

1. 新增 `_click_pager(button_id)`：在页内 `eval` 那个 `javascript:__doPostBack(...)` 表达式
   （`href` 里没有 `__doPostBack` 时才退回 `click()`，兼容站点改版）。
   `next_page` / `prev_page` 都改成调它。
2. `read_row()` 改成走 `find_section([id])`（逐页找、找到后停在那一页），
   **保留**「再试一次当前页」的兜底（翻页偶发失败不至于让整节作废）。

### 已有的教训为什么没挡住这次

devlog `2026-11-05-01` 第 ④ 条**记过同一个坑**（`read_row` 只读当前页），
但那次只修了**收尾的练理解锁回读**（`run_gxks.py` 里改成 `find_section`），
**`watch()` 里的进度回读没改** —— 而它才是每节都在跑的那条路。

⇒ **教训：修「某个函数有缺陷」时，必须把所有调用点都找出来**，不能只修眼前那一处。
`read_row` 就是这个函数：改它本身（而不是改调用方）才是一次修干净的。

---

## 二、日志「4 秒刷新、翻历史被打断」——第二次反馈，这次才是真修

### 为什么上次没修好

上次修的是「最新在上 + 记住 `scrollTop`」。但**新行是插在顶部的**：

```
你正看第 100 行  →  来 1 行新日志（插在顶部）  →  scrollTop 不变
但 100px 处的内容已经变成"更早一行"  →  每 30 秒（tick）滑一次
```

**只记像素偏移解决不了「内容整体位移」**。

### 现在

- 记**锚点行号**（`LOG_ANCHOR`：视口顶部那行在数组里的索引），重绘后按
  `anchor × 行高` 回滚；
- 用户往下滚离开顶部（>40px）→ `RUNLOG_FOLLOW = false`，**暂停跟随**，
  标题栏出现「**↑ 回到最新（已暂停跟随）**」按钮；
- 滚回顶部 → 自动恢复跟随。

### 「看不出是谁的日志」

原来只在 meta 里一行小字 `· default（study 起于 22:41）`。现在标题栏给
**带底色的账号徽标**（显示名优先、代号作小字）+ 任务类型标签 + 状态。
抽成 `logMetaHtml(job, running)`，`renderLogBox` 与 `renderRunLog` 两个渲染路径共用
（避免两处写法再次分家 —— 这次改就发现它们各写了一份 meta）。

---

## 三、「正在学的这节显示未学习」

### 根因

1. 平台**每 60 秒**才上报一次学时，列表回读默认**每 5 分钟**一次
   → 刚开播那几分钟，回读到的行仍是 `state = not_started / done_minutes = 0`；
2. `run_gxks.py` 的 `_on_event` **只处理 `progress`**
   → `tick`（30 秒一次、**零平台开销**的页面内墙钟心跳）不触发缓存回写。

### 修法

- `sections_cache.update_row(..., playing=True)`：把该节写成 `state = "learning"`。
  **纯展示标记**——`done_minutes` 仍以平台回读为准、`done_on_platform` 仍看真实 state，
  **不伪造平台数据**；
- `run_gxks.py` 的 `_on_event` 也处理 `tick`，传 `playing=True`；切节/结束时传 `False` 撤标记；
- ⚠ 踩到一个小坑：第一版写成 `if not r.get("state_text"): r["state_text"] = "学习中"`，
  而缓存里本来就有平台给的「未学习」→ 留下 `state=learning` 但文字「未学习」的**自相矛盾**。
  改成**无条件覆盖**（测试 `test_playing_marks_learning` 当场抓到）。

---

## 四、那个「小点」：终于定位了

用户圈了两次，我前两次都只扫源码（`<th>` 文本、行内片段、CSS `content:`、四个可疑字符、
th-vs-td 列数）→ **全部 0 命中**，因为**它根本不是字符**。

最后用 **Playwright 真渲染 + 4 倍放大截图 + `getComputedStyle`** 才看清：

```
第一列声明 width:28px
td padding:6px 8px  → 内容区只剩 ~19.5px
Chrome 默认复选框 ~13px + text-align:center 余量 → 溢出
全局规则 th,td{overflow:hidden;text-overflow:ellipsis} → 溢出部分渲染成「…」
在 7px 空间里，那个「…」看起来就是**一个孤立的小圆点**
```

**修法**：给复选框列单独的 class `.c-pick`：

```css
table.acct-tbl th.c-pick,table.acct-tbl td.c-pick{
  overflow:visible;text-overflow:clip;white-space:nowrap;padding:6px 4px}
table.acct-tbl th.c-pick{width:34px}
```

实测（4 倍放大重截 + computed style）：
`textOverflow: "clip"`、`overflow: "visible"`、`cbFits: true`、列宽 39.9px → **小点消失**。

**教训**：源码里找不到的"视觉异常"，别再继续扫源码 ——
**真渲染出来、量几何、看计算样式**。渲染诊断脚本：
`data/debug/verify_checkbox_fix.py`（临时端口 8396 + 临时数据目录，只读）。

---

## 五、验证

- `pytest`：**1185 → 1210 全绿**（+25），20 subtests
- 新增 `tests/test_gxks_cross_page.py` **8 条**：
  `read_row` 必须跨页 / `next_page`·`prev_page` 禁 `.click(` / `_click_pager` 真调
  `__doPostBack` / 假 frame 行为（成功、no-btn、异常吞掉）
- 新增 `tests/test_gxks_sections_playing.py` **5 条**：
  `playing` 标记 learning / 不把 done 倒回 / 撤标记回 not_started / 学时够就判 done /
  summary 不把 learning 算完成
- `test_gxks_page.py` +3：复选框列 `clip` + HTML 两端带 class + 反证不再写死 28px；
  日志锚点/暂停跟随；账号徽标
- `test_gxks_runlog_display.py` +5：锚点行号（不是像素）/ 暂停跟随 / 回到最新按钮 /
  scroll 监听已绑定 / 账号徽标
- 页面 JS `node --check` 通过（92612 字符）
- **实机只读核实**：1317 平台真实数据 `done_minutes=19/19, state=done`

---

## 六、改动文件清单

- `src/xxt/adapters/gxks/driver.py`
  - 新增 `_click_pager()`（`__doPostBack` 而非 `click`）
  - `next_page` / `prev_page` 改调它
  - `read_row` 改成跨页（`find_section`）+ 当前页兜底
- `src/xxt/adapters/gxks/sections_cache.py`：`update_row` 新增 `playing` 参数与标记逻辑
- `scripts/run_gxks.py`：`_on_event` 处理 `tick`，传 `playing`
- `src/xxt/panel_gxks/page.py`
  - 日志：`LOG_ANCHOR` / `RUNLOG_FOLLOW` / 锚点行号回滚 / scroll 监听 / 「回到最新」按钮
  - 日志标题：`logMetaHtml()` 账号徽标（两个渲染路径共用）
  - 表格：`.c-pick` 列（修 ellipsis 伪影）
- `tests/test_gxks_cross_page.py`（新增 8）、`tests/test_gxks_sections_playing.py`（新增 5）
- `tests/test_gxks_page.py`（+3）、`tests/test_gxks_runlog_display.py`（+5）
- `data/debug/`：`diag_find1317.py`、`diag_prev_page.py`、`diag_list_frame.py`、
  `verify_checkbox_fix.py`、`diag_render_probe.py` 等只读诊断脚本

---

## 七、交接

**进行中**：无。

**下一步第一个动作**：面板需重启（driver 改了，而正在跑的 `test` 任务用的是**旧代码**的
driver —— 上层脚本已启动时不会重载）。用户 `Ctrl+F5` 后重新点「启动并学习」。

**阻塞点**：无。

**用户可照做的验收**：

1. 面板重启 + `Ctrl+F5`（否则页面脚本还是旧的）
2. 表格第一列：复选框右边**不应该再有小点**
3. 勾两个账号 →「启动并学习」→ 两个账号都开始播
4. 学习进度区：正在播的那节应显示「**学习中**」（30 秒内出现，不再是「未学习」）
5. 日志区：
   - 标题栏有**账号徽标**（能一眼看出是谁的）
   - 往下滚看历史 → **不再被新日志挤走**，且出现「回到最新」按钮
   - 切到另一账号行 → 日志跟着切（这是现有的按账号分流）
6. 观察 `progress` 行**有没有数字**了（P0 修好后，跨页的节也能回读到真实学时）

**给下一条会话的提醒**：

- ⚠ **改 `driver.py` 的公共读函数（`read_row`/`read_page`/`refresh_here`）时，
  把所有调用点都找出来**。这次 P0 就是因为 11-05 只修了「收尾回读」那一个调用点，
  `watch()` 的进度回读没改 —— 而它才是每节都在跑的。
- ⚠ **正在跑的子进程用的是它启动时的代码**。改了 `scripts/*.py` 或 `adapters/*`，
  必须重启面板**并重新起任务**才生效（STATUS 纪律第 6 条）。
- ⚠ **源码里找不到的视觉异常，去真渲染**（Playwright + 放大截图 + computed style）。
  这次"小点"扫了三轮源码都是 0 命中，因为它根本不是字符。
