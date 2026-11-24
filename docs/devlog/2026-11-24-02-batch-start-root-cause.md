# 2026-11-24-02 批量启动排查：真因是点错按钮 + 批量动作补留痕

> 承接 `2026-11-23-07`（两阶段并行）。用户 2026-11-24 上午连测三轮，
> 反复报「选了 2 个账号只启动一个」。本篇是**排查过程与结论**。

---

## 一、最终结论：并行代码没问题，问题在「点的是单账号按钮」

### 结论一句话

用户点的**右侧小面板「启动并登录」**是**单账号**按钮
（`startBrowser()` → `browserLabel()` = `HOVER_LABEL || LABEL`，永远只指一个）。
左侧表格上方的「启动浏览器」才是批量的。两个按钮**名字几乎一样、同屏可见**。

### 逐步排除的证据链

| 怀疑点 | 验证方法 | 结论 |
|---|---|---|
| 两阶段并行没生效 | 我直接调 API 发 2 个 label 计时 | ❌ 排除。两个 spec **同秒写入**、两个计划任务**同秒触发**，总耗时 10.2 秒 |
| 后端只处理了 1 个 | 查 `data/tmp/chrome_start_*.json` | ❌ 排除。API 测试时两个 spec 都在 |
| test 端口探测有误 | `/api/state` 看 `port_alive` | ❌ 排除。`test port_alive=false`，应会被启动 |
| test 计划任务坏了 | `schtasks /query` | ❌ 排除。`Last Result: 0`，Ready |
| test 启动了又崩 | 查 profile / 崩溃转储 / 锁文件 | ❌ 排除。`Last Browser` 正常、无 dmp、无锁 |
| **前端只提交了 1 个** | 查操作记录 + spec 时间戳 + 任务 Last Run Time | ✅ **成立** |

**决定性证据**（09:40、09:45、10:05 三次）：
- `chrome_start_default.json` 只有 1 个，时间与用户点击吻合
- 操作记录只有 1 条 `default | browser-start`
- `test` 的计划任务 `Last Run Time` 一直停在 09:14:54 —— **压根没被触发**
- `test` 的 spec 文件一直是 09:14:54 —— **完全没被碰过**

**最终确认**（10:05 那次）：操作记录只有
`10:05:07 | default | browser-start | ok`，且**没有 `batch` 记录**
→ 说明走的不是批量接口，而是单账号接口。

---

## 二、我犯的三个错（记录下来）

### 1. 一开始就断定「并行没生效」，没先确认用户点的是哪个按钮

用户第一次说「只启动一个」时，我直接去查后端并行逻辑，查了两轮才想到问操作路径。
**教训**：多账号问题先确认「动作是从哪个入口发的」，比读代码快得多。

### 2. 把「Chrome 启动后又退出」误判成程序 bug

09:14 那次 test03 报 `ok:true` 但端口没了，我判断成「脚本成功判据有缺陷」。
用户后来说「启动了窗口，我关闭了」——**是我漏了「用户手动关窗口」这个可能**。
正确的排查顺序应该是：先问/先查有没有关窗口痕迹（profile 的 `Local State` 时间），
再怀疑程序。

### 3. 没意识到日志缺一条是排查困难的根因

原来的设计只有**成功启动的账号**各记一条 `browser-start`，**批量动作本身不留痕**。
所以「勾了 2 个只动了 1 个」时，日志里完全看不出第二个是被跳过还是没提交。
用户说「你这个启动在日志里面也没有显示记录。要有显示比较合理吧」——**说得对**。

---

## 三、本次改动

### 1. 批量动作留痕（用户要求）

`gxks_ops.ACTIONS` 新增 `batch`；`/api/accounts/batch` 在返回前写一条汇总：

```json
{
  "account_label": "",                    // 批量不归属单账号，避免污染单账号视图
  "action": "batch",
  "result": "ok" | "error",
  "detail": "批量「start-browser」提交 2 个：成功 1（default）、跳过 1。原因：test=端口 9233 已在运行",
  "payload": {"action": ..., "labels": [...], "results": [{label, ok, message, ...}]}
}
```

- 用 `record_op(payload=...)` 存**逐账号完整结果**，排查时不用再靠猜
- 留痕失败用 `try/except` 吞掉 —— **不能反过来弄挂批量本身**
- 面板 `ACTION_CN` 加 `batch: "批量操作"`

### 2. 右侧「启动并登录」按勾选数切换行为（用户选方案 C）

```js
async function startBrowser() {
  const picked = [...SEL];
  if (picked.length >= 2) return batchBrowserAction("start-browser");  // 批量
  // 否则原行为：只起当前账号
}
```

**文案同步变**（`renderBrowserBatch`）：
- 勾 0~1 个 → 「启动并登录」
- 勾 ≥2 个 → **「启动 N 个」**，`title` 说明「会为它们全部拉起 Chrome」

这样「两个按钮名字撞车」的问题从**行为和文案两侧**同时解决。

### 3. 诊断落盘（排查用，长期保留）

`/api/accounts/batch` 把收到的 `labels` 写 `data/tmp/batch_last.json`。
之所以落盘而不是 `print`：**面板在用户的 PowerShell 窗口里跑，stdout AI 读不到**。

---

## 四、验证

| 项 | 结果 |
|---|---|
| 新增测试 | `TestStartBrowserButtonScope` 3 条 + 批量留痕 3 条 |
| 全量 `pytest` | **1391 passed** |
| 页面指纹 | `PAGE len` 158785 → **159928** |

### 顺手修的两个测试

- `test_disable_writes_batch_note_in_ops`：原来断言 `rows[0]`，被新的 `batch` 记录挤掉
  → 改成按 `action` 找
- `test_batch_summary_records_each_label`：`/api/ops` 把 `payload_json` 解成了 `payload`
  → 改用 `payload`

---

## 五、你要做的

1. **页面 `Ctrl+F5`**（新 `ACTION_CN` 与按钮文案要重新加载）
2. 勾选 **2 个账号**（建议两个都没在跑的）
3. 看右侧按钮是否变成 **「启动 2 个」**
4. 点它 → 应**两个窗口几乎同时出现**
5. 面板「操作记录」应出现一条 **「批量操作」**，写着 `提交 2 个：成功 …`

如果右侧按钮**没变成「启动 2 个」**，说明页面没刷到新代码，再 `Ctrl+F5`。

---

## 改动的文件

- `src/xxt/store/gxks_ops.py`（`ACTIONS` 加 `batch`）
- `src/xxt/panel_gxks/app.py`（批量汇总留痕 + `batch_last.json` 诊断落盘）
- `src/xxt/panel_gxks/page.py`（`startBrowser()` 按勾选数分流、右侧按钮文案、`ACTION_CN`）
- `tests/test_gxks_panel_batch.py`（+3 条留痕测试，修 2 条旧断言）
- `tests/test_gxks_page.py`（+3 条 `TestStartBrowserButtonScope`）

## 交接块

- **进行中**：无（代码完成、测试全绿、**未提交待验证**）
- **下一步第一个动作**：用户 `Ctrl+F5` 后勾 2 个账号，确认右侧按钮变「启动 2 个」且两个都起
- **阻塞点**：无
