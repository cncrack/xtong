# 2026-10-30-03 新站第 2 节练习入库、代理根因定位、页面形态漂移与"不靠名字认页面"

## 1. 本次目标与结果

**目标**：把第 2 节（2.1 国家安全概述）的课后练习走完整链路——用户人工点开、作答、交卷，我方全程采集并入库；顺带查清中途出现的 503。

**结果：全部达成。**

| 项 | 结果 |
|---|---|
| 练习页抓取 | 成功，3 帧全量落盘（含 `tm_arr` 与 15 个函数源码） |
| 用户作答 | `b / a / d`，**交卷 100 分、3 题全对** |
| 交卷报文 | `ExamSave.aspx`，**54 个字段**，`confirm('确认提交答卷吗？')` ret=True |
| 入库 | `paper_id=2`、`import_id=2`、`inserted=true`；计数 `{papers:2, question_versions:6, attempt_questions:6, snapshots:2}` |
| 分类字段 | **自动填对**：`course_id=军事理论（字幕版）` / `section_id=1298` / `section_name=2.1 国家安全概述` / `paper_label=2.01` |
| 503 根因 | **本机系统代理**（v2rayN/xray `127.0.0.1:10808`），非站点、非时间 |
| 测试 | `pytest` **402 passed**（`test_gxks_practice.py` 16 条） |

## 2. 关键决策及理由

### 2.1 503 的排查顺序（结论：先验本机出口）

现象是点「参加练习」后页面变成 Chrome 错误页，一度怀疑"时间不对"。实际是 `chrome-error://chromewebdata/`——**网络层失败**，不是服务端返回的 503 页面。

双向计时探针（`data/debug/_probe_proxy.py`）给出决定性证据：

```
直连   200   304ms   /
走代理 200 24039ms   /
直连   200   279ms   Login.aspx
走代理 200 23517ms   Login.aspx
```

慢 80 倍。代理来自系统设置（`ProxyEnable=1`，`ProxyServer=127.0.0.1:10808`，`ProxyOverride` 只含局域网），进程是 `xray.exe`（v2rayN 自带）。国内站点被绕到境外节点 → `ExamCreate.ashx` 写库超时 → 网络层失败。

用户按"清除系统代理"修好后复验：`ProxyEnable=0`、`Login.aspx` 直连 **323ms**。

**确立的排查顺序**：先验本机出口（直连 vs 代理计时对比）→ 再看页面错误码 → 最后才怀疑站点。

⚠ **对刷课影响更大**：`updateTime.ashx` 每 60 秒上报一次，每次拖 24 秒会让上报节奏崩掉、学时真丢。**刷课前必须确认该域名直连**，这不是练习通道独有的坑。

### 2.2 两阶段监听（Q31 的唯一安全解法）

用户要求"点之前就开始盯着"，但 Q31 规定：Playwright 一旦 `connect_over_cdp` 连上浏览器就会自动 dismiss 所有 confirm，提前连会让用户"点了没反应"。

解法：
- **阶段一**：纯 HTTP 轮询 `http://127.0.0.1:9222/json/list`，完全不碰浏览器 → confirm 正常弹
- **阶段二**：新窗口出现后才 `connect_over_cdp`，抓完**立刻 `browser.close()`** → 交卷时的 confirm 也不受影响

### 2.3 不再维护 URL 白名单（本次最大方法论收获）

当晚监听**两次没触发**：

1. 第一次：新窗口是 `ExamCreate.ashx`，名单里没有 → 补上、重启
2. 第二次：落地页是 `ExamTmStepMain.aspx`，名单里还是没有 → 只能手动补抓

查证根因（用户问"是不是可以从网页端代码里找出对应代码"）：

列表页 `showExam()` 源码（`data/debug/showexam_src.json`）：

```js
function showExam(p_ExamId) {
  if (p_ExamId == 0) { alert("没有课后练习！"); return; }
  if (confirm("确定要参加这场练习吗?")) {
    var w = window.open("../ExamList/chkExam.aspx?ExamID=" + p_ExamId
                       + "&SiteType=2&IsContinue=0", 'exam', ...);
    if (!w) alert("弹出窗口被阻止，请更改浏览器设置，以便正常使用本功能!");
  }
}
```

**`ExamTmStepMain` 在全站前端代码里 0 命中**。它是 `chkExam.aspx` → 服务端 `ExamCreate.ashx` 建卷后 302 出去的落地点。前端只写第一跳，终点在服务端。

⇒ "从网页代码穷举练习页 URL"**做不到**。改为：

- 判据从"URL 命中白名单"改成"**任何新开的 page 都抓**"，只排除已知非练习页（`Login.aspx` / `MainPage.aspx` / `LibraryStudyList.aspx` / `Error.aspx`）
- 新增 `settle()`：建卷中间态（`chkExam.aspx` / `ExamCreate.ashx`）URL 还会再跳一次，等它稳定再抓
- 抓取选址从"找含 `ExamMain` 帧的 page"改成 `_pick_page()`：先按 URL 前缀对，再退回"帧最多的 page"（frameset 一定比普通页多）

最坏情况是多抓一个无关页面，成本为零；新形态再多也不会漏。

### 2.4 帧名不可靠，字段名才可靠

| 层 | 普通练习 | 单题步骤练习（本次） | 是否可用作锚点 |
|---|---|---|---|
| 落地页 | `ExamMain.aspx` | `ExamTmStepMain.aspx` | ❌ 会变 |
| 帧名 | `ExamMain`/`ExamHeader`/`ExamDo` | `ExamTmStepMain`/`ExamTmStepHeader`/`ExamTmStepDo` | ❌ 已变过一次 |
| 字段名 | `tm_arr` / `tmda_N` / `ksid_N` | 完全相同 | ✅ |
| 交卷目标 | `ExamSave.aspx` | 完全相同 | ✅ |

`parse_capture` 原来硬编码 `stage1["frames"]["ExamDo"]["tm_arr"]`，新帧名一来直接 KeyError。改为 `_answer_rows()`：遍历所有帧找"有非空 `tm_arr`"的那个；命中 0 个或多个都 raise（宁可拒收也不猜）。

### 2.5 两处"合法答卷被判非法"的误拒

| 现象 | 根因 | 修法 |
|---|---|---|
| "答卷回顾与交卷成功页不匹配" | 交卷后**不再经** `ExamMessage.aspx`，直跳 `viewExam.aspx?ExamID=…&Clerk_kscj_ID=…`；回顾页把练习 ID 写成 **`ExamID`** 而非 `Tk_Cl_Id` | `_exercise_id()` 同时认两者 |
| "答卷开始时间不一致" | 凌晨时段练习页写 `StartTime=2026/10/30 0:54:42`，回顾页写 `00:54:42`（**小时不补零**） | `_norm_time()` 归一化后再比 |

两处都补了回归测试。这类"误拒"比"误收"更隐蔽——数据没错，是判据太窄。

### 2.6 `import_gxks_practice.py` 的 `KeyError: 'course_doc'`

上一轮加"可选输入 `practice_meta.json`"时，`source_files` 的映射还写的是 `{names[k]: ...}`，`course_doc` 只在 `optional` 里不在 `names` 里 → 一跑就 KeyError。改为 `files = {**names, **optional}` 统一映射。

## 3. 改动的文件清单

**代码**
- `src/xxt/adapters/gxks/practice.py`——新增 `_exercise_id()` / `_norm_time()` / `_answer_rows()`
- `scripts/import_gxks_practice.py`——`files = {**names, **optional}` 修 KeyError
- `tests/test_gxks_practice.py`——新增 5 条（共 16 条）：ExamID 别名、时间不补零、帧名自适应、双答题帧拒收

**诊断脚本（`data/debug/`，不入库）**
- `_watch_practice.py`——去白名单，改"任何新 page 都抓" + `settle()` + `_pick_page()`
- `_grab_now.py`——去掉 `PAGE_HINTS`，复用 `_pick_page()`
- 新建：`_probe_proxy.py`、`_grab_now.py`、`_grab_review.py`、`_grab_course_list.py`、`_build_capture_2_1.py`、`_read_503.py`、`_read_errpage.py`

**文档**
- `docs/gxks/gaoxiaokaoshi.md`——§2.11 新增第 16~19 条 + "形态会再变"三原则；§7.3 补"另一类 503 来自本机"；§八 变更记录
- `docs/gxks/PLAN-GXKS.md`——§九 变更记录

**数据（`data/` 已 gitignore）**
- `data/debug/stage_2_1/`（标准导入目录）、`practice_latest.json/.txt`、`view_exam_2.json`、`ops_capture.json`、`proxy_probe.json`
- ⚠ `view_exam_2.json` 含考生真实姓名（答卷回顾页），只留本地，不入代码/文档/测试卡具

## 4. 交接块

**进行中**：无。第 2 节已闭环入库。

**下一步第一个动作**：
1. 用户确认第 3 节（2.2 国家安全形势）现在做还是并入自动刷课。若现在做 → 起 `python data/debug/_watch_practice.py`（**已去白名单，任何新页面都会抓**），用户点"参加练习"即可，不用再报页面形态
2. 正式刷课前必须确认 `www.gaoxiaokaoshi.com` **直连**（`ProxyEnable=0`），否则 `updateTime` 每 60 秒上报会被代理拖死、学时白刷

**阻塞点**：无。

**等用户**：
- 是否继续第 3 节练习（一次交卷 = 一份新考卷，代价见 `gaoxiaokaoshi.md` §2.11 第 15 条）
- 刷课窗口时间（建议先确认代理已清）

## 5. 用户下一步操作清单

1. **确认代理状态**：设置 → 网络和 Internet → 代理 → "自动检测设置"开、"使用代理服务器"关（或 v2rayN 用路由模式绕过大陆）。可在浏览器打开站点看是否秒开。
2. **决定第 3 节**：说"继续第 3 节"即可，我会先起监听再告诉你去点。
3. **刷课**：要正式跑时长时提前说，我给出启动命令并确认直连。
