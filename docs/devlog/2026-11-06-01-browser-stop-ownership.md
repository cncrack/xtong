# 2026-11-06-01 「停止浏览器」跨面板重启认不出自己的 Chrome

## 一、本次目标

用户问：「这两处的停止按钮他们的功能有什么区别？」

问的是两件事（截图里被红框圈出来的）：

- 账号表**行内**的「停止」按钮；
- 「浏览器与会话」卡片里的「停止」按钮。

顺带在排查中发现一个真 bug：卡片那个「停止」**跨面板重启就失效**。
用户拍板「可以修」，于是本次做了这条链的加固。

## 二、两处「停止」的区别（回答用户）

| | 行内「停止」 | 卡片「停止」 |
|---|---|---|
| 接口 | `POST /api/stop` | `POST /api/browser/stop` |
| 后端 | `_stop_one()` | `browser_stop()` |
| 杀谁 | 该账号**所有**在跑的任务子进程 | **Chrome 浏览器进程** |
| 实现 | `taskkill /T /F` 杀 python 进程树 | `taskkill /T /F` 杀 Chrome pid |
| Chrome 结局 | 留着 | 关掉 |
| 学习任务结局 | 停 | 因 CDP 断连而崩 |
| 显示条件 | 该账号有任务在跑（`c.job`） | 浏览器是面板起的（`started_by_panel`） |

关键：Chrome 由**计划任务**（`xxt-gxks-chrome`）拉起，不是 python 的子进程，
所以行内「停止」杀 python 不会带走浏览器；反过来卡片「停止」杀 Chrome 会
**让正在跑的刷课任务当场断连**。

## 三、发现的问题

### 问题 1：跨面板重启，自己的 Chrome 认不出来

`_BROWSERS` 是**纯内存**表（`app.py:64`）。面板一重启表就空：

- 界面：`started_by_panel=false` → 「停止」按钮**灰着点不动**；
- 接口：`browser_stop()` 查不到登记 → 返回「这个浏览器不是面板起的，不敢盲杀」；
- 结果：明明是面板自己 20 秒前拉起的 Chrome，用户只能手动关窗口。

用户当前这个 Chrome 正是这种状态（09:13:59 起，面板 09:13:59 也重启了）。

### 问题 2（顺带）：`started_by_panel` 会撒谎

Chrome 被关掉后，`_BROWSERS` 里的登记还留着 → 界面继续显示
「面板启动 · 09:13:59」，但端口早已没监听。判断条件是裸的 `bool(own)`。

## 四、第一版方案翻车（重要教训）

想到的"最不可伪造"的证据是 profile 里的 `DevToolsActivePort`：里面有
`端口号` + `/devtools/browser/<uuid>`，跟端口 `/json/version` 报的 uuid 对撞，
就能证明「这个端口的 Chrome 用的就是我们这份 profile」。

代码写完了、单测也绿了。**上真机一跑，全部是空**：

```
default: active_uuid=(none) 匹配端口=- owner_pid=-
jia:     active_uuid=(none) ...
```

根因：`DevToolsActivePort` **只在 `--remote-debugging-port=0`（随机端口）时才写**。
本工具用的是**显式端口**（9222、9223…），那个文件根本不存在。

这正是约定 ⑤「改对了 ≠ 跑得到」的现场版：单测全替身所以全绿，
实机的文件系统事实完全不同。

### 换成什么证据

改用 profile 的 **`lockfile`**（Chrome 单实例锁）+ `runmark.pid_owns_file`：

1. 端口确实有人在听 → `listening_pid()`（ctypes `GetExtendedTcpTable`，
   不起子进程，毫秒级）；
2. 听的人**是 Chrome** → `process_image()` 取镜像名（防 pid 复用）；
3. 它**占着这份 profile** → `lockfile` 存在，且进程创建时间不晚于该文件 mtime
   （`runmark.pid_owns_file`，与任务认领同一套判据）。

实机验证（只读，未启动/关闭任何进程）：

```
9222: pid=33852 image=chrome.exe
      进程创建=09:13:58  profile锁=09:13:59  lock存在=True
      owner_pid=33852  profile=data/profiles/default
```

用户日常 Chrome 用的是**自己的** profile，第 3 条过不去 → 永远动不到它。

## 五、改了什么

### `src/xxt/adapters/gxks/chrome.py`

新增「归属判定」一节（`DevToolsActivePort` 那段已删，并留下"为什么不能用"的注记）：

- `listening_pid(port)` — 谁在听这个端口；
- `process_image(pid)` — 进程完整路径；
- `owner_pid(profile, port)` — 三道证据的合取，任一不成立返回 0。

### `src/xxt/panel_gxks/app.py`

- 新增 `_browser_registry_path/_read_browser_registry/_save_browser_registry`
  → 落盘 `data/gxks_browsers.json`（与 `gxks_jobs.json` 同一思路）；
- 新增 `_remember_browser/_forget_browser` — 起浏览器登记、停浏览器清登记；
- 新增 `_owned_pid` — 只看现场证据判断归属；
- 新增 `_browser_registered_alive` — 「登记过 + pid 还活着 + 镜像名是 chrome」；
- 新增 `adopt_orphan_browsers(settings)` — 启动时认领（`create_app` 调用）：
  ① 按落盘登记认；② 对**没登记**的账号再用现场证据兜底
  （用户当前那个 Chrome 就是这种）；
- `browser_stop()` 改为 `_browser_owner_pid()` 判定 + 杀完清登记；
- `_browser_state()` 的 `started_by_panel` 改为 `_browser_registered_alive(own)`
  （登记残留但进程已死 → 显示「没启动」，不再谎报）。

### `src/xxt/panel_gxks/page.py`

- 卡片「停止」按钮从内联 `post(...)` 改成 `stopBrowser()`；
- 新增 `stopBrowser()`：**有学习任务在跑时先弹确认**，写明「这会杀 Chrome、
  任务会断连」并指路「只想停刷课就先点行内停止」。这是用户问的核心区别，
  直接在操作现场提示，比写在文档里有用。

### 测试

- `tests/test_gxks_browser_ownership.py`（新增，17 条）：
  证据链三道各自失效、登记跨重启认领、没登记时凭证据补认、
  端口没听就不探、pid 复用不杀、`started_by_panel` 不撒谎、前端确认与文案；
- `tests/test_gxks_panel_browser.py`：`started_by_panel` 语义变了（要求进程活着），
  原测试的合成 pid 777 不存在 → 补 `pid_alive` / `process_image` 替身；
- `tests/test_gxks_browser_buttons_hover.py`：按钮改走 `stopBrowser()`，
  断言点从「按钮内联 `browserLabel()`」改为「`stopBrowser()` 体内用 `browserLabel()`」，
  约定（不许用 `LABEL`）不变。

## 六、验证

- `pytest -q`：**1077 passed**（15 subtests）；
- `data/debug/diag_browser_owners.py`（新增，**只读**诊断）：
  实机 9222 → `owner_pid=33852`，其余 6 个端口未监听；
- 逐账号 `_browser_owner_pid`：`default=33852`，其余全 0 —— 与现场一致。

## 七、改动文件清单

- `src/xxt/adapters/gxks/chrome.py`（+归属判定）
- `src/xxt/panel_gxks/app.py`（+落盘登记/认领/归属判定）
- `src/xxt/panel_gxks/page.py`（`stopBrowser()` + 确认）
- `tests/test_gxks_browser_ownership.py`（新增）
- `tests/test_gxks_panel_browser.py`
- `tests/test_gxks_browser_buttons_hover.py`
- `data/debug/diag_browser_owners.py`（新增，不入库）

## 八、交接

**进行中**：无。

**下一步第一个动作**：等用户重启面板（`schtasks /run /tn xxt-gxks-panel`）+ Ctrl+F5，
验收卡片上「停止」按钮对当前那个 Chrome **可点**（`started_by_panel=true`）。

**阻塞点**：无。

**注意**：`adopt_orphan_browsers()` 的「现场证据兜底」路径只在**面板启动时**跑一次。
如果面板启动时 Chrome 还没起来，之后手动起的（未经面板）不会被认领 —— 这是对的
（人工登录是既定工作流，不该被当成"面板起的"随手杀掉）。

**遗留（未做，等用户拍板）**：`_BROWSERS`/登记表都不记录「Chrome 是谁按了什么起的」。
如果以后要区分「面板自动起的」vs「用户手动起的」，得在 `start_chrome.ps1`
写结果时带一个来源标记。
