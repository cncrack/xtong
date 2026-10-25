# 2026-10-25-23 指纹加固（方案 A）+ 额度检查前置 + 面板额度显示

## 一、本次目标与结果

用户四个诉求：① 解释"额度达标却视频 0 个" ② 额度到底生效没有、再测一次 ③ 同步版/异步版是否已排除、能否只留异步版 ④ 面板卡片显示每账号的有效额度。另：用户批准**当晚**执行方案 A（指纹加固，不等明天）。

| 目标 | 结果 |
|---|---|
| 额度是否生效 | ✅ 实证生效：`21:51:28 [账号#1] session-end｜任务数额度达标（今日已完成 2）｜视频 0 个` |
| 指纹加固方案 A | ✅ 完成，12 项指纹中 9 项已对齐真机（详见第三节对比表） |
| "视频 0 个却仍访问课程页" | ✅ 已修：额度与待学章节检查**全部前置到浏览器启动之前**，不达标 = 零平台访问 |
| 面板卡片显示额度 | ✅ 每账号显示"今日 x / 上限 y（全局｜账号）" |
| 同步版是否保留 | ⏸ 结论见第五节，等用户拍板（默认保留） |

## 二、关键决策与理由

1. **UA 版本号对齐内核**：写死 `Chrome/131` 而 Playwright 1.64 内核是 `156.0.8078.4`，差 25 个大版本是最刺眼的矛盾 → 改 `Chrome/156.0.0.0`。
2. **`navigator.webdriver` 返回 `false` 而非 `undefined`**：真机是 `false`；`undefined` 本身就是"被覆盖过"的特征。
3. **plugins / mimeTypes / userAgentData 按真机形状伪造**：原先是 `[1,2,3,4,5]` 数字假数组（`plugin0` 是数字），比"没有"更容易被识别；现改为带 `name/filename` 的对象数组 + 正确原型 + `instanceof PluginArray` 通过。
4. **所有 `chromium.launch` 统一带 `LAUNCH_ARGS`**：`--disable-blink-features=AutomationControlled` 从内核层去掉自动化标记（11 处调用点全覆盖）。
5. **viewport/screen 改 1280x800**：避免默认 1280x720（outer==inner 的组合特征之一）。
6. **额度检查前置（本次最重要的安全改进）**：原来 `_open_chapter_frame()` 先打开课程页、再判断额度——"被额度拒绝"仍然产生一次真实访问。现在时段/待学章节/额度三项全在 `with playwright` 之前判定，被拒时零浏览器、零请求。
7. **不加 `sec-ch-ua` HTTP 头**：真实 Chrome 只对 HTTPS 特定资源发该头，全量注入反而制造新的不一致；留待后续按需评估。
8. **残留（headless 固有，不在方案 A 范围内）**：WebGL 仍是 `SwiftShader` 软件渲染、`outerHeight == innerHeight`。要彻底消除需换本机真实 Chrome（`channel="chrome"`）或有头模式，见第七节。

## 三、指纹前后对比（实测，仅 about:blank，零平台请求）

| 检测项 | 旧（默认 headless） | 新（加固后） |
|---|---|---|
| `userAgent` | `HeadlessChrome/156.0.8078.4` ❌ | `Chrome/156.0.0.0` ✅ |
| `userAgentData.brands` | `null` ❌ | Chromium/Google Chrome/Not=A?Brand ✅ |
| `navigator.webdriver` | `true` ❌ | `false` ✅ |
| `plugins.length` | `0` ❌ | `5` ✅ |
| `plugins[0]` 类型 | `undefined` ❌ | `object`（PDF Viewer）✅ |
| `navigator.mimeTypes.length` | `0` ❌ | `2` ✅ |
| `languages` | `["zh-CN"]` | `["zh-CN","zh","en"]` ✅ |
| `window.chrome.runtime` | 不存在 ❌ | `object` ✅ |
| `windowSize` | `1280x720`（outer==inner） | `1280x800`（outer==inner）⚠ |
| WebGL renderer | `SwiftShader` | `SwiftShader` ⚠ 未消除 |
| timezone / hardware | `Asia/Shanghai` / 16核 | 同 ⚠ 与真机可能有差异 |

复现命令：`.venv/Scripts/python.exe data/debug/probe_fp.py`

## 四、改动文件

| 文件 | 改动 |
|---|---|
| `src/xxt/core/stealth.py` | UA→156；webdriver→false；plugins/mimeTypes/userAgentData 按真机伪造；新增 `LAUNCH_ARGS`；`hardened_context` 固定 viewport/screen 1280x800 |
| `src/xxt/adapters/chaoxing/astudy.py` | 待学章节 + 额度检查前置到浏览器之前；launch 带 `LAUNCH_ARGS`；context 统一走 `hardened_context` |
| `src/xxt/adapters/chaoxing/study.py` | 同上（同步版保持一致） |
| `src/xxt/adapters/chaoxing/__init__.py` | 2 处 launch 带 `LAUNCH_ARGS` |
| `src/xxt/session/login.py` / `relogin.py` | launch 带 `LAUNCH_ARGS` |
| `src/xxt/cli.py` | work 路径 2 处 launch 带 `LAUNCH_ARGS` |
| `src/xxt/panel/app.py` | overview 用 fresh Settings + `effective_quota` 输出 `quota_minutes/quota_tasks/quota_from_global`；卡片 chips 改"今日 x / 上限 y（全局｜账号）"；2 处 launch 带 `LAUNCH_ARGS` |
| `data/debug/*`（不入库） | `set_quota.py`、`panel_api.py`（UTF-8 调面板 API）、`probe_fp.py`（指纹对比）、`monitor.py` |

## 五、同步版 vs 异步版（待用户决定，默认保留）

- 现状：**两版都已修**下一节按钮的 frame 失效问题，行为一致。
- 使用面：同步 `StudyRunner` **只被** `xxt learn`（单账号 CLI）调用；面板 / `mlearn` / `auto` 全走异步 `run_accounts`。
- 结论建议：**保留同步版**，理由：① 它是无 asyncio 依赖的兜底路径；② 删除会牵动 `tests/` 里基于它的解析测试；③ 代价只是小改动要同步两处（本次已同步）。
- 若要统一（方案 A）：把 `learn` 改为调用异步版 → 删 `StudyRunner` → 顺带解决 P1"`xxt learn` 不写 `data/run/<id>.json` 标记、可绕过同账号双开"的问题。工作量约 0.5~1 小时 + 一次验证。**这需要用户点头才做。**

## 六、顺带发现并修掉一个红线隐患：面板重启误删运行标记

- 现象：22:06 重启面板后，`data/run/` 被清空，但 #2 的 `mlearn` 进程（PID 6512）**仍在跑**。
- 后果（严重）：面板 `/api/study/start` 只查内存 `_JOBS` 与 `data/run/*.json` —— 两者都空 → 面板认为 #2 空闲 → 用户再点一次"开始刷课"就会**同账号双开**（违反 AGENTS 硬约束）。
- 根因：`_startup_cleanup()` 无条件 `unlink` 所有 `run/*.json`，假设"面板重启 = 旧会话已死"。但会话子进程由 `subprocess.Popen` 独立拉起，父进程（面板）退出后子进程照常运行。
- 修复：清理时读 marker 里的 `pid`，用 `_pid_alive()` 判断——**活着的保留**（会话继续可见/可停），只清孤儿。`auto.json`（定时守护）同样受益。
- 验证：重启后面板日志不再打印"启动自检清理"（run_markers=0），`/api/study/status` 返回 `jobs={'2': {..., running: True, external: True}}`。

## 七、实跑证据（今晚）

| 时间 | 账号 | 事件 |
|---|---|---|
| 21:41:40 | #1 | `video-done｜学会悦纳自己｜571s` |
| 21:42:14 | #2 | `video-done｜如何做好人生规划（上）｜492s` ← 会话 22 的修复实证 |
| 21:48:01 | #1 | 我手动停止（验证额度） |
| 21:48:12 | #1 | 启动失败（PowerShell 把中文 course 编成 `????`）→ 子进程"未找到课程"退出，**未访问平台** |
| 21:48:58 | #1 | UTF-8 重发成功 |
| 21:51:28 | #1 | **额度拒绝**（今日已完成 2）→ 实证额度链路 |
| 22:06 起 | #2 | `如何做好学业规划（上）` 正常播放（第 3 个视频） |

> 教训：PowerShell 的 `Invoke-RestMethod` 会把中文 body 编码成 `?`，调面板 API 一律用 `data/debug/panel_api.py`（UTF-8）。

## 八、交接块

- **进行中**：#2 已跑到第 4 个视频（今日 4 个 / 33 分钟），面板 22:12 重启并已正确识别为 `external` 会话；#1 按额度停机（今日 2 个）
- **下一步第一个动作**：看下一轮真实会话是否仍有登录异常提示（指纹加固效果），并等用户拍板"同步版是否统一到异步"
- **阻塞点**：无。
- **用户下一步操作清单**：
  1. 刷新面板页面（Ctrl+F5），确认卡片变成"今日 2 / 60 个（全局）"、"今日 17 / 240 分"样式；
  2. 手机端/App 不要登这两个号；
  3. 若同意统一双引擎，说一声"做同步版统一"，我按方案 A 执行。
