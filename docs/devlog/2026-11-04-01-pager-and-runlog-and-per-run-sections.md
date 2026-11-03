# 2026-11-04-01 分页双向翻页 + 一键浏览器 + 本次节数可设

> 接续 `2026-11-03-03`。本篇记录 2026-11-03 21:20 ~ 2026-11-04 01:20 的工作。
> 期间跨零点，故文件名按 11-04 编。

## 本次目标与结果

用户反馈 5 个问题，全部修复：

| # | 用户原话 | 结果 |
|---|---|---|
| 1 | 「你启动开启了浏览器，为何不能自动登录呢？」 | ✅ 一键启动 + 自动登录（分三层） |
| 2 | 「播放的视频是单独有个小窗口…刚才可不是这样」 | ✅ 查明是**我的调试残留**，非代码问题 |
| 3 | 「运行日志怎么没有了」 | ✅ 任务结束后**保留日志**可回看 |
| 4 | 「怎么停了，只学习1节吗」 | ✅ 修掉 `goto_page` 不能往前翻的**老 bug** |
| 5 | 「如果把节设置多些呢？…现在可以改」 | ✅ 「本次跑几节」行内可填 |

**测试**：`pytest` **988 全绿**（新增 21 例）。

## 关键决策及理由

### 决策 1（问题 1）：Chrome 必须由**交互式计划任务**起

**实测对比**（同一组参数、同一 chrome.exe）：

| 由谁起 | 结果 |
|---|---|
| 面板（后台计划任务，非交互会话） | 进程出现，但**调试端口永不响应** |
| 交互式 shell（有桌面） | 端口 2 秒就绪，100% 成功 |

Windows **会话隔离**：非交互会话（Session 0）拿不到用户桌面，Chrome 静默失败。

**做法**：注册 `-LogonType Interactive` 计划任务 `xxt-gxks-chrome`，跑
`scripts/start_chrome.ps1`。面板只做「写参数 → `schtasks /run` → 轮询端口 → 读结果」。
副产品：Chrome 不再是面板的子进程，面板重启不会带走它。

### 决策 2（问题 1）：登录态**三层兜底**

cookie 全是 **session 级**（`expires=-1`），关窗即失效 → 昨天的快照今天必然不认。
`_chrome_start_message()` 按序：① 注入快照 → ② 票过期则自动用账号密码重登 →
③ 遇验证码立刻停、转人工（试错=撞库特征）。

### 决策 3（问题 1）：登录页是**异步渲染**的（真 bug）

`relogin` 永远返回 `no_form`，但手动登录完全正常。实测：

| 等待 | `input` 个数 | `#frmLogin` |
|---|---|---|
| `goto(load)` 后 2 秒 | **0** | 不存在 |
| 5 秒后 | 5 | 存在 |

`_find_frame()` 只 `query_selector` **一次**就判 `no_form` → 改成轮询等待
（`login(form_wait_ms=15000)`）。

### 决策 4（问题 3）：任务结束后**保留日志**

原来 `_JOBS.pop()` 一执行，页面立刻变「当前没有运行中的任务」，**刚跑完的日志当场消失**。
改法：结束时把快照存进 `_LAST_JOBS`，`/api/state` 用
`_jobs_snapshot(include_finished=True)` 带上（`alive: false` + `ended_at`）。

⚠ **默认仍不带**：停止按钮、额度计算、「有无任务在跑」的判断必须只认真活的，
否则会去杀一个已死的 pid（被测试抓到两次）。

### 决策 5（问题 4）：`goto_page` 必须**双向**翻页（老 bug，10-29 埋下）

**现象**：用户「怎么停了，只学习1节吗」。

**根因**（`bade44a` / 2026-10-29 引入，一直没变）：

```python
if cur < target and not pager.get("has_next"):
    return False
if not self.next_page():        # ← 只会往后翻
    return False
```

刷完 1304 后列表停在**第 4 页**，而目标在第 1 页 → `cur > target` 不匹配任何分支 →
仍去点「下一页」→ 4/4 已是末页 → 失败 → `find_section()` 里
`if not self.goto_page(1): return None` → **误判「没有未完成的节」**而收工。

实测当时第 4 页明明有 **9 节「未学习」**。

**为什么前几天没暴露**：需要同时满足「列表停在第 4 页」+「要回第 1 页」。
- 11-02~11-03 刷 1301~1303 时列表只有**第 1 页**（另一个 bug），走不到这个分支；
- 11-03 下午修好「读全 4 页」后，**第一次**走完整的 4 页扫描流程 → 才暴露。

**不是本次改坏的**，但本次的另一个修复把它**激活**了。

**修法**：新增 `prev_page()`（`#PageSplit1_BtnPrevious`），`goto_page` 按
`cur < target` / `cur > target` 双向选择。实测：第 4 页 → `goto_page(1)` → True，
`find_section` 挑到 1305 ✅。

### 决策 6（问题 5）：两层额度不是一回事

| | 字段 | 含义 |
|---|---|---|
| **日上限** | `daily_sections` | 一天**总共**最多几节（硬刹车，一直生效） |
| **本次上限** | `max_sections` | **这一次**点下去跑几节（原来写死 3） |

脚本侧 `run_gxks.py:181` 取两者最小值 → **把「本次」调大也只会跑到日上限，不会超刷**。

**做法**：账号行加 `.sec-n` 数字输入框（1 ~ min(12, 剩余额度)），批量开始弹 `prompt` 询问；
后端仍兜一层 `max(1, min(n, 12))`。

## 事故与教训

### ① 我的 ps1 脚本漏了两个 Chrome 参数 → **视频播不了**

| 参数 | 昨天 | 我的脚本 | 后果 |
|---|---|---|---|
| `--no-proxy-server` | ✅ | ❌ 漏 | 系统代理（10808）劫持视频 CDN |
| `--disable-blink-features=AutomationControlled` | ✅ | ❌ 漏 | 自动化特征暴露 |

实测（同一节 1304）：修复前 `readyState=0`、`0×0`、`dur=NaN`、`waiting`；
修复后 `readyState=4`、`850×478`、`dur=2381`、`playing` ✅。

**教训**：`start_chrome.ps1` 的参数必须与 `chrome._CHROME_ARGS` **保持一致** →
已加测试 `test_chrome_args_match_adapter` 逐条比对，漏一个就测试失败。

### ② `start_chrome.ps1` 丢 BOM → 静默失败

Windows PowerShell 5.1 把无 BOM 的 .ps1 按系统 ANSI（中文 GBK）读 → 中文注释乱码
→ **解析失败** → 计划任务返回码 1、**无任何输出**（面板只报「端口仍未响应」）。

**教训**：编辑该文件后必须确认 BOM 还在（239,187,191）。已加测试
`test_has_utf8_bom` + `test_parses_if_powershell_available`。

### ③ 我误杀了用户的刷课进程

调试 Chrome 时执行 `Stop-Process python`，把用户正在跑的刷课进程一起终止 →
日志末尾 `KeyboardInterrupt`，台账没有记录（因为只学到 3/38 未达标）。

**教训**：调试时**只杀自己起的进程**（按命令行匹配 profile 路径），不要按进程名杀。

### ④ 往大文件中间插函数，吞掉了整批路由

把 `_chrome_start_message` 插到 `create_app` 中间 → 后面所有 `@app.post` 变成它的
**内部函数** → `create_app` 返回 None → 面板全 500。

**教训**：插入点先确认是**模块级**（缩进 0），插完用 AST 打印 `col_offset` 复查。

### ⑤ `git checkout --` 冲掉了本轮未提交改动

为修 ④ 用了 `git checkout -- app.py`，结果 `need_browser` 与交互式任务全丢，重做一遍。

**教训**：改动多时先 `git diff > backup.patch` 再回退。

### ⑥ 我的调试把播放页塞进了网页（用户以为「不能移动」）

我用 `page.goto()` 直达播放页，**绕过** `showframe()` → `easyDialog` 弹窗流程，
播放页被渲染在页面里且不可拖动。平台正常流程是：
`showframe()` → `parent.showframe(name, url, 810, 560)` → `easyDialog.open()`
→ **810×560 可拖动浮窗**（标题栏 `cursor:move` + ✕ 关闭）。已清理残留恢复。

**教训**：验证平台行为要走**正常入口**，别用 `goto` 抄近路。

## 改动文件清单

| 文件 | 改什么 |
|---|---|
| `src/xxt/adapters/gxks/login.py` | `_find_frame(wait_ms)` 轮询等表单；`login(form_wait_ms)` |
| `src/xxt/adapters/gxks/driver.py` | **新增 `prev_page()`**；`goto_page` 改双向 |
| `src/xxt/adapters/gxks/browser.py` | `no_page` 错误文案改得更可操作 |
| `src/xxt/panel_gxks/app.py` | `_start_chrome_interactive()`（新）、`_chrome_start_message()`（新）、`_LAST_JOBS` + `include_finished`；`browser_start` 重写；`refresh_sections` 加端口预检；`_stop_one` 忽略已结束任务 |
| `src/xxt/panel_gxks/page.py` | 「启动并登录」按钮；`showNotice` 持久提示条（含 `grid-column:1/-1` 修竖排）；`renderRunLog` 支持已结束态；**「本次跑几节」输入框** |
| `scripts/run_gxks.py` | 中断时补记 `partial`（不计节数） |
| `scripts/start_chrome.ps1` | **新文件**：交互式会话起 Chrome（必须带 BOM） |
| `scripts/run_panel.py` | **新文件**：带 `__main__` 保护的启动入口 |
| `tests/test_gxks_start_chrome_script.py` | **新**：BOM / 参数一致性 / URL 共 7 例 |
| `tests/test_gxks_runlog_after_finish.py` | **新**：结束后保留日志共 8 例 |
| `tests/test_gxks_pager_bidirectional.py` | **新**：双向翻页共 6 例 |
| `tests/test_gxks_per_run_sections.py` | **新**：本次节数可设共 8 例 |

## 交接块

### 进行中

**刷课正在跑**：账号 `default`，00:54 起，1305（3.2 外国军事思想）已到 25/48。
用户填了「本次跑几节」= ？（00:54 起的那次是旧按钮，按当时剩余额度推算约 3~5 节）。

### 下一步第一个动作

**等用户回来确认刷课结果**。重点看：
① 1305 是否达标（48+2=50 分钟）；② 能否**自动切到下一节**（`prev_page` 修复的真正验证）；
③ 任务结束后运行日志是否仍在（绿色「✓ 任务已结束」条）。

### 阻塞点

**无技术阻塞**。

### 等用户

1. **确认刷课是否连着跑了多节**（问题 4 的真正验收）
2. **验收「本次跑几节」输入框**（刷新面板后可见）
3. **待拍板**：多账号运行日志是否做成**可切换页签**（当前是「当前账号日志 + 其他账号一行提示」）
4. **开机请关掉睡眠**：面板/Chrome 需要常驻

### 维护提醒（下次改 `start_chrome.ps1` 必看）

改完必须确认 **BOM 还在**（否则 PowerShell 5.1 按 GBK 读 → 解析失败 → 任务码 1 无输出）。
自查：

```powershell
$b=[System.IO.File]::ReadAllBytes("E:\xxt-gxks\scripts\start_chrome.ps1"); $b[0..2]
# 必须是 239,187,191
```
