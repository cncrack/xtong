# 2026-11-03-01 运行日志独立成块 + 学习进度动态更新

## 本次目标与结果

用户在 2026-11-02 23:00 起连续提出 6 个问题，逐条处理：

| # | 用户原话 | 结果 |
|---|---|---|
| 1 | 「黑色的刷课进程记录，要不要一起放在操作记录这块区域中单独一块，操作记录是不是要改为日志记录」 | ✅ 「操作记录」→「**日志记录**」，内含「运行日志」「操作记录」两个子块 |
| 2 | 「完的记录要动态反应在账号列表，以及节进度表中，待学与已学完也要随学习动态更新」 | ⚠️ **首次实现有缺陷**（见下），本会话补齐 |
| 3 | 「待学/已完成内容太长了，加滚动条还是分页？」 | ✅ **加滚动条**（不分页：4 秒轮询重绘会重置页码） |
| 4 | 「为什么会经常出现旧面板跑新代码，不能一次性解决吗」 | ✅ **面板热重载默认开** |
| 5 | 「把节进度改为学习进度」「页面上要添加上配置项」 | ✅ 改名 + 页面新增「面板设置」区 |
| 6 | 「运行日志多用户时什么状态？」 | ✅ 显示当前账号日志 + 其他账号任务折叠提示 |

**测试**：914 全绿（`pytest tests/`），`push_check` PASS。

## 关键决策及理由

### 决策 1：滚动条而非分页

节进度每 4 秒轮询重绘（`SEC_SIG` 变才重建）。分页需要维护「当前第几页」状态，
轮询重绘会把页码打回第一页 —— 体验比滚动条差得多。滚动条零状态、移动端原生可用。

高度取 `min(52vh, 420px)`：并排两列时各占约半屏，窄屏堆叠时整页也不被拉长。

### 决策 2：面板热重载默认开，用户不用手动重启

**根因**：Python 进程启动时把 `page.PAGE` 载入内存，之后改磁盘文件**不会**刷新已运行进程。
每次改代码都必须手动重启面板，否则看到的永远是旧页面 —— 这就是用户反复遇到的现象。

**修法**：uvicorn `reload=True`（`panel.reload` 配置项，默认 `True`）。
对挂机刷课**零影响**：刷课是独立子进程（`_spawn_script` 起的），面板重启不碰它；
日志是追加写文件，重连后尾巴照旧。

**踩坑记录**（重要）：
1. uvicorn reload 用 multiprocessing **spawn**，入口必须写 `if __name__ == "__main__":`
   否则子进程重新 import 时递归启动自己 → `forgotten to use the proper idiom`。
2. reload 只能传**导入字符串**且工厂必须**无参**。`create_app(settings)` 需要参数 →
   报 `create_app() missing 1 required positional argument`。为此新增 `app_factory()`。
3. reload 进程是「父 + spawn 子进程」两层，`Get-Process` 查不到父进程（权限视角问题），
   要用 `netstat -ano` 看 LISTENING 的 PID，再杀它的 **spawn 子进程**才能真正释放端口。

### 决策 3：节缓存回写要按**课程 value** 分文件（关键修正）

首次实现只做「更新已存在缓存里的已有行」，结果**对新课程完全失效**：
- 刷课脚本写 `sections_default_32.json`（课程 value=32）
- 面板读 `sections_default.json`（老文件，无课程后缀）
- 两边永远对不上 → 用户看到「改了代码数据还是不动」

**修正**：
- `driver.current_course()` 新增：读当前选中的课程 value（`list_courses()` 里 selected 那条）
- 刷课脚本自动取课程 value 回写（用户没指定 `--course` 时从页面读）
- `sections_cache.update_row()` 支持**新建/追加**：该节不在任何缓存里时新建一份
- 面板新增**课程切换器**：列出该账号所有课程缓存（value/中文名/节数/进度），可切换

### 决策 4：空态提示要说清是哪种空

原来统一显示「待学区已清空——这门课全部学完了」，但实际往往是「**这份缓存是另一门课的**」。
用户看到一头雾水。现在区分两种空，并提示「点刷新节列表换成当前课」。

### 决策 5：面板设置存 `meta` 表，不抄超星的 `app_config`

gxks 库里**没有 `app_config` 表**（只有 `meta`），照抄会得到「能存但读不到」的假功能
（`gxks_ai_config` 当初踩过同样的坑）。新建 `src/xxt/store/gxks_panel_settings.py`。

`panel.reload` 是唯一需要重启才生效的（它决定 uvicorn 启动方式），
`panel.poll_seconds` 即改即用。

## 改动文件清单

| 文件 | 改什么 |
|---|---|
| `src/xxt/panel_gxks/page.py` | 「操作记录」→「日志记录」+ 运行日志子块；节进度→学习进度；滚动条 CSS；课程切换器；面板设置控件；`SEC_SIG` 覆盖分钟数与每节指纹；`load()` 带 course |
| `src/xxt/panel_gxks/app.py` | `app_factory()`（无参工厂，reload 用）；`serve()` 开 reload + 读 DB 设置；`_list_course_caches()`；`/api/panel/settings` GET/POST；`/api/state` 加 `courses_cached` |
| `src/xxt/adapters/gxks/sections_cache.py` | `update_row()` 支持新建/追加该课程缓存；带 `name/required_minutes/idx/category` |
| `src/xxt/adapters/gxks/driver.py` | `current_course()` 读当前课程 value |
| `src/xxt/store/gxks_panel_settings.py` | **新文件**：面板设置读写（`meta` 表） |
| `scripts/run_gxks.py` | 自动取课程 value 回写；progress 事件即回写 |
| `config/settings.local.example.toml` | 注释说明 `panel.reload` |
| `tests/test_gxks_sections_writeback.py` | **新文件**：7 例（新建/追加/去重/回写标记） |
| `tests/test_gxks_course_switch.py` | **新文件**：10 例（改名/切换器/签名/空态/后端清单） |
| `tests/test_gxks_panel_settings.py` | **新文件**：14 例（设置存取/页面控件/多账号日志/课程 value） |

## 事故与修复

- **01:13 刷新节列表失败**：`TargetClosedError: Target page, context or browser has been closed`
  —— **用户手动关了 Chrome 窗口**（用户后来说明）。脚本崩在 `wait_for_timeout`，缓存没写成。
  → 这就是「面板数据还是不对」的直接原因。**不是代码 bug**。
- **账号设置三栏被破坏**：加「面板设置」时用了 `.fcard`，导致 `test_three_cards` 失败
  （该视图断言恰好 3 个 fcard）。→ 改成独立 `section`，测试恢复。
- **僵尸 LISTENING socket**：杀掉 uvicorn 父进程后端口仍 LISTENING 且能应答 ——
  `Get-Process` 查不到、`TerminateProcess` 返回 False。真相是**父进程还活着**，
  只是 CIM 查不到；杀掉它的 spawn 子进程才真正释放。

## 交接块

### 进行中

无。面板已用新代码在 8301 运行（PID 9136，`reload=True`）。

### 下一步第一个动作

**让用户点一次「刷新节列表」并保持 Chrome 窗口不关**（约 1 分钟），
生成 `sections_default_32.json`（军事理论全 4 页，含 2.4~2.6 公开课），
之后课程切换器里能切到完整课程，学习进度才会随刷课实时更新。

### 阻塞点

- **推送曾一度不通**（SSH `Connection reset by 20.205.243.160 port 443`，间歇性）。
  最终**直连重试成功**（`b76281d..e23f13f`）。走 `scripts/ssh_proxy.py` 代理会**无限挂起**，
  必须先 `Stop-Process` 掉 ssh 再回退直连。

### 等用户

1. **验收学习进度动态更新**：点「刷新节列表」（别关 Chrome）→ 再刷一次课 →
   「待学/已完成」应随分钟数实时变化。
2. **验收课程切换器**：刷新出完整课程后，右上角下拉应出现多门课可切。
3. **验收面板设置**：账号设置 → 面板设置，热重载开关与刷新间隔可改可存。
4. **确认 1301/1302/1303 与 1.1~1.9 同属「军事理论（字幕版）」**：
   实测 driver 注释记的是「32 / 军事理论」，而两批节的 `category` 都是「军事理论（字幕版）」，
   章节号不同（1.x vs 2.x）→ **同一门课**。但旧缓存只存了第 1 章（1.1~1.9，4 页只读了第 1 页），
   所以看到的是「另一门课」的错觉。
