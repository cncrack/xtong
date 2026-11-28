# 2026-11-28-08 自动检测交卷 + 学习记录 + 日志区加高

> 用户 2026-11-28 三条要求：
> ① 「会存在忘记点的情况，如何解决？」→ 做了自动检测交卷
> ② 「为何操作记录里面第二个账号的日志比较少」→ 看视频补记 `gx_ops`
> ③ 「把运行日志区域的显示高度增加一些，方便查看」→ 52vh → 72vh
> ④ 「学习入口页为何会时不时自动刷新」→ 排查结论：设计行为，保持不变

---

## 一、自动检测交卷（解决「忘记点面板按钮」）

### 问题

流程要人做两个动作：

```
① 浏览器点「交卷」  ② 面板点「确认交卷并入库」
```

漏第 ② 步 → 答卷已提交但没入库 → 只能重做。

### 解法

脚本在「等确认」期间，**每秒**用 `chrome.page_urls()`（**纯 HTTP 只读**，
不建 CDP 连接 → 不会吞掉交卷的 confirm，Q31）检查判分页有没有出现。
一旦发现本次练习的判分页，就当「已确认」继续入库，**不必点面板**。

### 最大的坑：同节的旧判分页

判分页 URL 只有 `ExamID` + `Clerk_kscj_ID`，**没有 exam_start_id**。
只按 `ExamID` 认，会把「上一次做同一节留下的判分页」当成新的——
1.8 做第二次就会踩到，直接把旧答卷当新数据入库。

用**基线排除**：等待开始前已经存在的 `Clerk_kscj_ID` 记进 `baseline_clerk`，
检测时跳过它们。五种场景离线验证：

```
① 旧判分页(已在基线) -> 正确排除
② 新判分页           -> 识别
③ 别的节的判分页      -> 正确忽略
④ 非判分页           -> 忽略
⑤ 旧+新混合          -> 找到新的
```

### 一个副作用要记住

`_await_confirm_and_reclaim` 的函数体因此变长，
`test_gxks_work_review_flow.py` 里两处「取前 3000 字符」的断言够不到后半段了，
已放宽到 8000 并注明原因。

## 二、学习记录补写 `gx_ops`

### 问题

用户：「为何操作记录里面第二个账号的日志比较少，基本都是第一个账号的」。

查证（真实数据）：

```
gx_ops 按账号：default 103 条 / test 19 条
今天(11-28)：default 12 条 / test 1 条（只有 browser-start）
动作分布：browser-start 48 / sections-read 20 / work-fill 15 / work-review 12
```

**根本没有 `study-*` 这类动作** —— 学视频全程一个记录点都没有。
test 只看视频，所以记录里看着像什么都没干。

### 解法

`run_gxks.py` 补三个节点（只记事实，不改任何播放行为）：

| 动作 | 时机 | 内容 |
|---|---|---|
| `study-start` | 开始连播 | 最多几节 / 几小时 |
| `study-section` | 每节学完 | 节名 + 已学/要求分钟 |
| `study-done` | 收工 | 完成几节 + 总耗时 |

**写失败一律吞掉**（`except Exception: pass`）——记录是附加收益，不能影响刷课。
有测试锁住「`_log_op` 里不许出现 `currentTime` / `updateTime` / `submit(` 等
播放相关调用」。

## 三、运行日志区加高

`#runlog` 高度：`min(52vh, 520px)` → **`min(72vh, 900px)`**；
移动端（≤760px）`46vh` → `56vh`。

1080p 屏能显示约 500px 内容（≈26 行），比原来多一倍。仍保留内部滚动，
不把整页撑爆。

## 四、学习入口页「时不时自动刷新」——排查结论：**设计行为，不改**

用户问的，查了代码：

- `watch()` 循环里 `read_row()` → `refresh_here()` 会刷新**课程列表帧**
- 频率：默认 `read_progress_every=600` 秒；距达标 ≤10 分钟时降到 60 秒
- **列表帧与播放帧是两个独立 iframe**（`LibraryStudyList` vs `LibraryStudy`），
  刷新列表**不影响视频播放**（实测 `currentTime` 连续在涨）

**用户决定：保持不变。** 因为回读是判断「学时够没够」的唯一依据，
降低频率会导致跑过头或提前停。

## 五、测试

| 新增 | 条数 |
|---|---|
| `test_gxks_auto_detect.py` | 6 |
| `test_gxks_study_ops.py` | 6 |

另按新契约改 3 处（`work_review_flow` 两处窗口、`runlog_display` 两处高度断言）。
`pytest` 1498 → **1512 全绿**。

## 六、改动的文件

- `scripts/gxks_work.py`（自动检测判分页 + 基线排除）
- `scripts/run_gxks.py`（`_log_op` + 三个节点）
- `src/xxt/panel_gxks/page.py`（`#runlog` 高度）
- `tests/test_gxks_auto_detect.py`、`tests/test_gxks_study_ops.py`（新增）
- `tests/test_gxks_work_review_flow.py`、`tests/test_gxks_runlog_display.py`（按新契约）

## 交接块

- **进行中**：无
- **下一步第一个动作**：重启面板（前端高度变了）→ 做一节练习验证
  「交卷后不点面板也能自动入库」
- **阻塞点**：无
