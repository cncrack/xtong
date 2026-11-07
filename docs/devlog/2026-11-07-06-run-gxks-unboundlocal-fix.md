# 2026-11-07-06 修「启动并学习」启动即崩（我上次改动引入的 UnboundLocalError）

> 用户验收时说：「点了『启动并学习』，还是要我再对每个账号去点学习呢。
> 面板上的学习按钮还是可点的，说明没有开始学习」。
>
> **用户判断完全正确**。日志里是两个进程各活了 4 秒就死了。这是**我自己上一轮
> （devlog `2026-11-07-04`）改出来的 bug**，不是"功能没实现"。

---

## 一、事故与根因

### 现象

面板点「启动并学习」→ 提示「浏览器就绪 → 开始刷课」→ **4 秒后进程消失** →
界面上的「学习」按钮又变回可点（看起来"根本没开始"）。

两条日志（`gxks-study-default-20261107-221826.log` / `-test-...-221837.log`）内容一致：

```
[OK] 到站点 ... 耗时 0.17s（阈值 3s）→ 可起跑
[OK] 已注入登录态快照（22 条 cookie，...）
Traceback (most recent call last):
  File "scripts/run_gxks.py", line 207, in main
    secs_limit = acc.daily_sections if acc else gaccounts.DEFAULT_SECTIONS_PER_DAY
UnboundLocalError: cannot access local variable 'acc' where it is not associated with a value
```

### 根因（我的改动）

上一轮修「凭据按账号取（P0）」时，我把 `acc = find_by_label(...)` 从额度判定
**之后**移到了「自动重登」那一段，而**额度判定仍在它前面**并且要用 `acc.daily_sections`。

Python 的局部变量作用域只看"函数内有没有赋值"，不看行号顺序 —— 一旦函数里某处
赋值给 `acc`，整个函数的 `acc` 都变成局部变量；在赋值语句执行前读取 → `UnboundLocalError`。

### 为什么 1185 条单测全绿却没抓到

`pytest` 的用全是**打桩调子函数**（`_study_one` / `_browser_start_one` / …），
**没有任何一条真的跑一遍 `run_gxks.py::main()` 的完整流程**。
而这类"变量顺序"问题**只在真跑时才炸**。

这与 STATUS 第 7 条纪律是同一类：

> 「改对了」≠「跑得到」—— 每写一个条件分支都要问"真实数据下这个分支会被执行到吗"。

这里是它的变体：**"单测全绿" ≠ "进程跑得起来"**。凡是**入口脚本**（`scripts/*.py`），
单测覆盖的是它们的子函数，入口本身从来没被真正执行过。

---

## 二、修法与两层回归

### 修复

把 `acc = find_by_label(...)` **提前到额度判定之前**，并在那里写了注释说明为什么必须在前。

### 新增 `tests/test_gxks_run_order.py`（3 条，两层）

**第一层｜静态（AST）**

1. `test_acc_is_assigned_before_any_use` —— 扫描 `main()`，`acc` 的**第一次出现必须是
   Store（赋值）而不是 Load（读取）**。顺序一颠倒立刻红。
2. `test_no_other_use_before_assign_in_main` —— 同样的检查覆盖 `acc` / `cred` / `q`
   三个"在多个分支里赋值"的变量（做完整控制流分析会误报推导式/with/except 变量，
   所以只挑已知的关键名）。

**第二层｜动态（真跑 `main()`）**

3. `test_reaches_open_session` —— 网络自检、浏览器、账本全打桩，**真的调用 `main()`**，
   断言它走到 `open_session` 并因打桩异常返回 2（而不是抛 `UnboundLocalError`）。
   这一层才是真正抓住本案的那条，因为它是唯一**执行到出问题那一行**的测试。

⚠ 打桩时踩了一个小坑：真 `SessionError` 有 `.hint` 属性，用裸 `Exception` 打桩会在
`if exc.hint` 处报 `AttributeError` —— 所以要造一个带 `hint = ""` 的假异常类。

---

## 三、验证

- `pytest`：**1188 passed**（1185 → 1188，**+3**），20 subtests
- 直接跑 `scripts/run_gxks.py --label default --no-netcheck --max-sections 0`：
  现在会走到 `[FAIL] 连不上 CDP ...`（说明**已经跨过额度判定那一段**），
  而不是第 207 行的 `UnboundLocalError`。
  ⚠ 这条命令在本沙箱里会因为 `EPERM: mkdtemp playwright-artifacts` 失败 ——
  那是沙箱限制（STATUS 其它坑第 7 条），不是代码问题。

---

## 四、改动文件清单

- `scripts/run_gxks.py`：`acc = find_by_label(...)` 移到额度判定之前 + 注释说明原因
- `tests/test_gxks_run_order.py`（新增，3 条：AST 两层 + 真跑 main 一层）

---

## 五、遗留：用户报的「标题和每个账号复选框旁边都有个点」

**未定位**。做过的排查（全部只读）：

| 排查 | 结果 |
|---|---|
| 7 个 `<th>` 的纯文本 | `序号 / 账号 / 今日 / 课程 / 登录 / 操作`，**没有游离字符** |
| 行内 checkbox 单元格原始片段 | 只有 `<td><input class="acct-pick" ...></td>`，无多余字符 |
| 所有 CSS `content:` 规则 | 只有 `h2::before`（标题左侧 4px 竖条）、`details summary` 的 ▸、
  `.acct-detail .dk::after`（冒号）—— 与表格无关 |
| 表头/复选框附近的 `·` `•` `●` `▪` | **0 命中** |
| 表头 th 数 vs 行内 td 数 | 7 : 7，**一致**（没有多出一列） |

**所以它不是源码里的字符。** 最可能是**视觉产物**，两个嫌疑：

1. **28px 列装不下复选框**：`<th style="width:28px">` + `td{padding:6px 8px}`（左右共 16px）
   → 内容区只剩 **12px**，而 Chrome 默认复选框约 **13px** → 被 `overflow:hidden` 裁掉一点。
2. 行选中态 `tr.cur` 的 `box-shadow:inset 3px 0 0 var(--accent)` 在左侧画了一条 3px 竖线，
   在某些缩放下可能被看成点。

**待用户确认**（截图放大或选中那行文字看能不能复制到）：

- 那个点**能不能被鼠标选中/复制**？能 → 是字符，我按文字查；不能 → 是图形，按 CSS 查。
- 「标题」指的是**表头的哪一格**（复选框那格，还是「序号」标题）？
- 浏览器缩放是 100% 吗？

---

## 六、交接

**进行中**：无。

**下一步第一个动作**：面板已重启（8301，跑的是含本次修复的代码）。用户 `Ctrl+F5` 后
**再点一次「启动并学习」** —— 这次应该真的开始播视频。

**阻塞点**：无（那个"小点"待用户补一点信息）。

**用户可照做的验收**：

1. `Ctrl+F5`
2. 表格勾 **林鑫阳 + test** → 点表格上方「**启动并学习（1 个要起浏览器）**」→ 确认 → 填节数
3. 等 10~40 秒（串行起两个 Chrome）→ 两个窗口**都不要最小化**
4. 看：两个账号行内的按钮应从「学习」变成「**停止**」、状态圆点变绿、卡片显示「并发 2/2」
5. 看日志：`data/logs/gxks-study-<代号>-*.log` 里应出现
   `=== 连续播放开始 ... ===` 和 `----- 第 1 节 ... -----`，**不再有 traceback**

**给下一条会话的提醒**：

- 凡是改 `scripts/run_gxks.py` 这类**入口脚本**，改完必须**真跑一次**（哪怕用
  `--no-netcheck --max-sections 0` 跑到"连不上 CDP"为止），单测全绿不算数。
- 本次的 `test_gxks_run_order.py` 就是为此加的，**别删**。
