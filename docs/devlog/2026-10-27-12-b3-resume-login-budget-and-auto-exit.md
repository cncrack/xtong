# 2026-10-27-12 B3 续播 + 登录预算配置化 + 守护异常出口 + 额度口径澄清

## 一、用户本轮三项决定（全部已落地）

| 用户原话 | 对应改动 |
| --- | --- |
| 「登录每日 2 次上限（也可以配上）」 | `[learn] login_budget_per_day = 2` 可配，钳 1~5 |
| 「B3 修」 | 中断（`ended=False`）且已播 ≥60% 时，重载任务页**续播一次** |
| 「P0-13 做」 | `cmd_auto` 补 `except Exception` → 写 `auto-exit`（ERROR，含异常摘要与运行量） |

## 二、两个必须先说清的事实（用户提问触发的核对）

### 1. 今天 #2 的额度不是 240 分钟，是 174 分钟

`data/logs/sub-mlearn-20261027-083206.log` 第 3 行：

```
[#2] 当日剩余额度：174 分钟 / 45 个（0 即不限；今日已学 0 分钟 0 个）
```

`[quota] daily_minutes = 240` 只是**上限**，实际每日额度在上限的 `float_ratio = [0.7, 1.0]` 区间内随机抽样
（今天抽到 174，即 0.725）。

时间对得上：08:35:00 开跑 → 11:38:46 结束 = **184 分钟** > 174 分钟 → `session-end｜时长额度达标`。

**但这里有一个口径缺陷**（本轮只登记，未改）：

| 口径 | 用在哪 | 今天 #2 的值 |
| --- | --- | --- |
| 墙钟（`monotonic - study_start`） | 额度判定「是否达标」 | **184 分钟** |
| 视频秒数（`video-done` 累计） | 面板显示「今日已学」 | **104 分钟** |

两者差 80 分钟（拟人间隔 + 视频卡顿）。结果就是用户看到「今日 104/240 分」，却被告知"额度达标"——看着像没刷够。

面板显示的分子/分母也不配套：分子是**今日视频秒数**，分母是**配置上限 240**，而真正的今日额度 174 根本没显示。

建议方案（待用户拍板，见 §六）。

### 2. 更正上一轮的错误说法：重启守护**不会**重置今日尝试次数

上一轮我写「点停止→再启（重置 #1 今日尝试次数）」是**错的**。
`auto_tries_today = _cnt("auto-try")` —— 从 `run_logs` 按当日计数（`cli.py`），进程重启不改变它。

实测今日 #1 的计数：

```
#1  auto-try 2   auto-fire 3   auto-skip 2   session-start 3   session-end 2   video-done 0
```

→ **#1 今日 2/2 已用尽**，下午重启守护也会被 `auto-skip` 跳过。要让它跑只能走 §十 的方案 A/B。

## 三、B3：中断续播（用户批准）

- `_play_video` 新增 `self._last_progress`（本节最高进度），结束日志改为 `播放结束（ended=%s）%.0f/%.0fs`
- 新增 `AsyncStudyRunner._partial_ratio()` → 已播比例
- 主循环在 `played is False` 时判断：`partial_retries > 0` 且该节本会话未续播过 且 `ratio ≥ partial_retry_ratio_pct`
  → `_enter_chapter(kid)` 重进 + 重新找 `<video>` + 再播一次
- 只用一次（`partial_tried` 集合），避免无限循环
- 配置：`[learn] partial_retries = 1`（0=关闭，最大 3）、`partial_retry_ratio_pct = 60`（30~95）
- 启动日志增加一段 `｜中断续播 N 次（≥M% 才续）`

依据：`#1` 在 441/551s（80%）被判"播放结束"，旧逻辑直接换节 → 这一节永远学不完；
续播后若平台侧已在 441s 记进度，会从该点继续。

## 四、登录预算配置化（用户「也可以配上」）

| 位置 | 改动 |
| --- | --- |
| `config/settings.toml` `[learn]` | 新增 `login_budget_per_day = 2`（注释写明 1~5、失败尝试也算次数） |
| `config.py` DEFAULTS | 同步补默认值 |
| `session/relogin.py` | 新增 `login_budget_per_day(settings)`（钳 1~5，**不允许设 0**）；`budget_left()` / `auto_relogin()` 加可选 `per_day` 参数；`maybe_auto_relogin()` 传入 |
| `panel/app.py` | 面板触发重登时同样传入配置值（此前用默认 2） |

安全边界保留：即使配置写 `99` 也钳到 5；写 `0` 钳到 1——登录尝试本身就是风控信号，不给关。

## 五、P0-13：守护异常出口（27-03 E 项，挂了 4 天）

```python
except Exception as exc:   # 落在 except KeyboardInterrupt 之后
    mins = int((_time.monotonic() - started_at) // 60)
    n_fired = len(locals().get("fired") or ())
    n_skip = len(locals().get("skipped") or ())
    print(f"\n[auto] 守护异常退出：{type(exc).__name__}: {exc}")
    log.error("守护异常退出", exc_info=True)
    _ev(0, "auto-exit", f"异常退出：{...}｜运行 {mins} 分钟｜今日触发 {n_fired} 个｜跳过 {n_skip} 个", "ERROR")
    return 1
```

用 `locals().get()` 取 `fired/skipped`，因为异常可能发生在它们初始化之前。

## 六、新登记：额度口径与面板显示不一致（待拍板）

| 项 | 现状 | 建议 |
| --- | --- | --- |
| 达标判定 | 墙钟（含拟人间隔/卡顿） | 改成「视频秒数」与展示一致；或双口径都写进 `session-end` |
| 面板「今日 104/240 分」 | 分子=视频秒数，分母=配置上限 | 会话启动时把今日抽样额度（如 174）落到 `app_config`，面板显示 `今日 104/174 分`，hover 说明「上限 240 × 今日系数 0.725」 |

影响：不改则用户永远无法从面板判断"今天还能学多久"，且会误以为刷得比实际少。

## 七、改动文件清单

| 文件 | 改动 |
| --- | --- |
| `src/xxt/adapters/chaoxing/astudy.py` | `_last_progress` / `_partial_ratio()` / 中断续播分支 / `partial_retries` `partial_retry_ratio_pct` 读取与启动日志 |
| `src/xxt/session/relogin.py` | `login_budget_per_day()`；`budget_left` / `auto_relogin` 加 `per_day`；`maybe_auto_relogin` 接线 |
| `src/xxt/cli.py` | `cmd_auto` 补 `except Exception` → `auto-exit` |
| `src/xxt/panel/app.py` | 重登调用传 `per_day` |
| `src/xxt/config.py`、`config/settings.toml` | `[learn]` 四项（partial_retries / partial_retry_ratio_pct / login_budget_per_day，及上轮三项） |
| `tests/test_astudy_policy.py` | +3（`_partial_ratio`） |
| `tests/test_relogin.py` | +2（per_day 覆盖 / 配置钳制） |

## 八、验证

- `py_compile` 0；`pytest` **191 passed**（186 → 191，净增 5）
- 今日数据核对（只读脚本 `data/debug/q_today_quota.py`）：#1 `auto-try=2 (2/2 用尽)`、`video-done=0`；#2 `video-done=13`、结束原因「时长额度达标」

## 九、交接块

- **进行中**：B1/B2/B3、登录预算配置化、P0-13、徽标 hover、数据回补 全部落盘；**额度口径缺陷已登记未改**（等用户拍板）
- **下一步第一个动作**：用户下午手动点 #1 的「▶ 刷这门课」（不占守护次数）或临时把 `[auto] max_tries` 改 3 后重启守护
- **阻塞点**：#1 今日 `auto-try` 已 2/2 用尽，**重启守护不会重置**（计数在库里）；不手动点就只能等明天 08:55
- **待用户拍板**：① 额度口径是否改成「按视频秒数达标」 ② 面板是否显示今日真实额度（174）而不是上限（240）
- **提醒**：`dist/xxt.exe` 仍是 10-25 旧构建，会话 49~54 的改动都没进 exe

## 十、用户下一步操作清单

1. 下午测 #1：面板点 #1 卡片上的「▶ 刷这门课」（最省事，不消耗守护次数，也不受 2/2 限制）
   - 若想测**守护链路**：把 `config/settings.toml` 的 `[auto] max_tries` 临时改 `3` → 重启守护 → 测完改回 `2`
2. 观察日志新增两行：`节级策略：…｜中断续播 1 次（≥60% 才续）`、以及中断时的 `播放中断于 xx%…重载任务页续播一次`
3. 若 5.1 仍卡在 440s 左右：说明是平台对这个号限速，需要换时段或把 `[pace] speed_range` 降到 1.0~1.1
