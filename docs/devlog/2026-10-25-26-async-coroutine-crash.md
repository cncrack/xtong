# 26 · 会话崩溃复盘：异步引擎误用同步 API（coroutine add_init_script）

日期：2026-10-25 22:30~22:55
触发：用户「2026-10-25T22:35:13 session-start … 界面上没有显示刷课中」→「日志出现意外提醒了」

## 一、目标与结果

| 目标 | 结果 |
|---|---|
| 定位「点了开始、界面无反应、进程已死」 | ✅ 根因是**上一轮我自己引入的**：异步引擎调用了同步版 `hardened_context` |
| 修复 | ✅ `core/stealth.py` 新增 `async_hardened_context`，`astudy.py` 改用它 |
| 补上"崩溃信息进黑洞"的盲区 | ✅ `_spawn` 输出从 `DEVNULL` 改为落盘 `data/logs/sub-<cmd>-<时间>.log` |
| 同源的第二种 sync/async 混用 | ✅ 昵称截获 `username_sniffer` 在异步下 `resp.json()` 永不 await，改为异步处理器 |
| 单账号崩溃不连累兄弟账号 | ✅ 每个 `supervise()` 任务加 `try/except` + `log.error(exc_info=True)` |
| 回归防护 | ✅ `tests/test_async_api.py`（52 passed） |

## 二、根因（一句话）

`async Playwright` 的 `browser.new_context()` 返回**协程**，必须先 `await`。
方案 A 指纹加固时我把建 context 的逻辑收进 `core/stealth.py::hardened_context()`，
但它是**同步写法**（`ctx = browser.new_context(**kw)` 后直接 `ctx.add_init_script(...)`）。
异步引擎照抄调用 → `ctx` 是个协程 → `AttributeError: 'coroutine' object has no attribute 'add_init_script'`。
崩在浏览器刚建立、还没访问任何页面的位置，所以日志只有额度行就没了。

## 三、为什么第 2 个账号没事（不是假象，是"跑的是旧代码"）

| 事实 | 证据 |
|---|---|
| #2 会话进程启动于 **21:33:40** | `Get-CimInstance Win32_Process -Filter "ProcessId=6512"` → `2026年10月25日 21:33:40` |
| 出事代码提交于 **22:08** | `git log` → `a258a88 10-25 22:08 session: 指纹加固方案A…` |
| Python 进程不会热重载改过的 .py | 模块在 21:33 启动时已编译进内存，之后改盘上文件不影响它 |

所以 #2 至今跑的是**改造前**的 `astudy.py`：没有 `LAUNCH_ARGS`、没有 `hardened_context`、
没有额度检查前置——它走的是原来那条能用的老路。它不是"假成功"：今日 `run_logs` 里有 7 条
真实的 `video-done` 记录（进度秒数逐条递增），平台侧也确实在推进度。

反过来说：**如果 #2 是 22:08 之后启动的，它会以完全相同的方式崩掉**。
换句话说，本次是"同一份代码，凡是在改动后新起的会话都会挂"，与账号本身无关。

## 四、兄弟账号会不会被连累

会，机制在 `asyncio.TaskGroup`：任一子任务抛未捕获异常 → 整个 TaskGroup 取消其余任务。
原先 `supervise()` 里的 `runner.run()` 没有兜底，所以 #1 崩会**顺带掐断 #2**。
本次已加：

```python
try:
    stats = await runner.run(dict(course), limit=limit)
except Exception as exc:
    # TaskGroup 里任一任务抛异常会取消兄弟任务：一个账号崩不能连累其他账号。
    log.error("[#%s] 会话异常终止：%r", row["id"], exc, exc_info=True)
    results.append({... "stop_reason": f"异常终止：{exc!r}"})
    return
```

边界（诚实说明）：兜底只覆盖 `runner.run()` 内部。若崩在 `AsyncStudyRunner(...)` 构造
（如 `effective_quota` 读库报错）之前，仍会连坐——后续可把这整段也裹进去。

## 五、同源的第二个 bug（顺手修）

`_open_chapter_frame` 复用了同步版的 `username_sniffer()`。异步下 `response.json()` 是协程，
而事件回调是同步的 → 永不 await → 每次起会话都打一条
`RuntimeWarning: coroutine 'Response.json' was never awaited`，昵称永远抓不到。
改成在 `astudy.py` 内定义异步处理器（`await response.json()`），并加源码级测试守护。

## 六、本次还修的两个"哑故障放大器"

1. **`_spawn` 输出进 DEVNULL**：子进程的 traceback 完全不可见，这是我上一轮排查耗时的直接原因。
   现在每次拉起都留 `data/logs/sub-<cmd>-<YYYYmmdd-HHMMSS>.log`，并显式设
   `PYTHONIOENCODING=utf-8/PYTHONUTF8=1`（否则 cp936 下中文乱码）。
2. **`_spawn` 里 `get_logger` 未导入**：改成落盘后引入的 500 错误，已补函数内 import。
3. **章节树拿不到时不再 AttributeError**：`_open_chapter_frame` 返回 None 时原代码直接
   `.locator()` → 抛 `AttributeError`，现在改成明确的
   `"打不开章节树（登录态可能已失效，或课程链接过期）——请重新登录该账号"`（同步版对齐）。

## 七、实测证据（修复后）

```
22:44:29  面板拉起 mlearn pid=1452，输出 → sub-mlearn-20261025-224429.log
22:44:29  账号#1 default：错峰 123 秒后启动
22:46:33  [#1] 当日剩余额度：213 分钟 / 47 个（今日已学 17 分钟 2 个）
22:46:49  [#1] 进入知识点：第三章｜4.5 增强自我信心（上）（1227271610）
22:46:54  [#1][增强自我信心（上）] 开始播放：倍速 1.07
22:47:46  [#1][增强自我信心（上）] 播放结束（ended=True）
```

`/api/study/status` → `jobs={'1': {running: True}, '2': {running: True, external: True}}`，
界面恢复"刷课中"。同一时刻 #2 在 `正确认识自我（上）` 正常推进（379/495s），未被 #1 的崩溃路径影响。

## 八、文件清单

| 文件 | 改动 |
|---|---|
| `src/xxt/core/stealth.py` | 抽出 `_context_kwargs()`；新增 `async def async_hardened_context()`（必须 await 版本），同步版保留 |
| `src/xxt/adapters/chaoxing/astudy.py` | 改用 `await async_hardened_context(...)`；昵称截获改异步处理器；`supervise()` 加 per-account 异常兜底；章节树 None 给出可读原因 |
| `src/xxt/adapters/chaoxing/study.py` | 章节树 None 同样给出可读原因（与异步版对齐） |
| `src/xxt/panel/app.py` | `_spawn` 输出落盘 + UTF-8 环境变量 + 补 `get_logger` import |
| `src/xxt/cli.py` | `cmd_learn` 启动前检查同账号会话（配合新增的 runmark）；mlearn 的标记清理只清自己写过的 |
| `src/xxt/core/runmark.py`（新增） | 运行标记的唯一实现：`marker_path/pid_alive/live_session/write_marker/clear_marker`，面板与 CLI 共用 |
| `tests/test_async_api.py`（新增） | 异步 context 必须 await 且注入指纹；源码级守护：astudy 不得出现同步 `hardened_context(`、不得复用同步 `username_sniffer` |

## 九、教训

**同步/异步混用是本项目最容易复发的一类 bug**：`study.py`（同步）与 `astudy.py`（异步）
共用 `chaoxing/` 目录，helper 很容易被对面直接 import。凡是"碰 page/response/context/browser"
的函数，两边都不能复用；只复用纯字符串/纯数据函数（`WATCHER_JS`、`match_chapter_id`）。
已用源码级测试把这条规则钉进 CI。

另一个教训：**子进程输出绝不能进 DEVNULL**。这次定位耗时的一半，花在"日志里什么都没有"上。

## 十、交接块

- **进行中**：#1 于 22:44 重启后正常刷课（第三章 4.5 已完成，进入拟人间隔）；#2 继续跑（21:33 起，老代码，不受本次改动影响）
- **下一步第一个动作**：观察 #1 是否连续跑完多个视频且 `sub-mlearn-*.log` 无新的 RuntimeWarning；下一个会话启动时确认昵称截获生效
- **阻塞点**：无
