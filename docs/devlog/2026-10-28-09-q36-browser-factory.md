# 会话 69：Q36 驱动工厂接线（12 处 launch 收口）+ Q37 复核

- 日期：2026-10-28 晚
- 用户口令：「那就做 Q36,37，现在做合适吗，可以就做。更新。这个解释一下是什么意思」
- 前置澄清：**Q37 今天上午（会话 63）已完成**，本会话只做**复核**；真正新做的是 **Q36**。

## 一、目标与结果

**目标**：把 `[stealth] headless` 从"死配置"变成真配置，并把散落的浏览器启动收成一个入口。

**结果**：✅ 已落地。`pytest` **275 全绿**（266 → 275，+9）；端到端探针证明
「面板开关 → `app_config` → `Settings()` → 启动判定」全链路可用；Q37 复扫 **0 命中**。

**为什么判断"现在做合适"**：默认值 `[stealth].headless = true` 不变、`LAUNCH_ARGS` 不变、
人工登录仍强制有头 → **默认行为零变化**，是一次等价重构 + 新增可选开关，
不依赖实机就能判定正确性；实机只需明天顺手看一眼。

## 二、关键取证（先量后改）

| 事实 | 证据 |
|---|---|
| `[stealth] headless` **零消费**（真死配置） | 全仓 grep：只有 `config.py` 的 DEFAULTS、`settings.toml`、面板 `CFG_CN` 的中文名，**没有任何读取点** |
| 裸 `chromium.launch(` 不是台账写的 **9 处，实际 12 处** | `astudy.py:442` / `study.py:228` / `profile_read.py:82` / `adapters/chaoxing/__init__.py:67,101` / `login.py:30,91` / `relogin.py:92` / `panel/app.py:3108,3265` / `cli.py:1774,1790` |
| 只有两处是"必须这样"的显式语义 | `login.py:30` 人工登录必须可见；`cli.py:1790` 半自动作业核对必须留窗（`headed = not args.submit`） |
| 台账数字偏低的成因 | 会话 59 审核时按"文件"数，同一文件两处只算一次；`profile_read.py` 当时漏看 |

## 三、落地内容（4 项）

### 1. 新增 `src/xxt/core/browser.py`（单一入口）

```python
resolve_headless(settings, override)   # 三级优先
launch_kwargs(settings, headless=, extra_args=)
launch(p, settings, ...)               # 同步
async_launch(p, settings, ...)         # 异步
```

**三级优先（顺序不可换）**：

| 优先级 | 来源 | 用途 |
|---|---|---|
| ① 显式 `override` | 调用点写死 | 人工登录 `headless=False`；半自动作业 `headless=headed` |
| ② 配置 `[stealth].headless` | `app_config` 覆盖层 → toml → 内置默认 | 面板「⑥ 浏览器形态」开关 |
| ③ 内置默认 `True` | 兜底 | 配置缺失 / 读取异常时不阻断启动 |

读取配置时**优先复用调用方已有的 `Settings`**（`self.settings` / 面板的 `settings`），
只有 `profile_read`、`probe_session`、`relogin` 这类没有 settings 的模块才自己 `Settings()` 读一次（1~3ms）。

### 2. 12 处全部改调工厂

| 文件 | 处数 | 传参 |
|---|---|---|
| `adapters/chaoxing/astudy.py` | 1 | `async_launch(p, self.settings)` |
| `adapters/chaoxing/study.py` | 1 | `launch(p, self.settings)` |
| `adapters/chaoxing/__init__.py` | 2 | `launch(p, self.settings)` |
| `adapters/chaoxing/profile_read.py` | 1 | `launch(p)`（无 settings，工厂自读） |
| `session/login.py` | 2 | 人工登录 `headless=False`；会话探测 `launch(p)` |
| `session/relogin.py` | 1 | `launch(p)` |
| `panel/app.py` | 2 | `launch(p, settings)` |
| `cli.py` | 2 | 判题回读 `launch(p, s)`；作业 `launch(p, s, headless=headed)` |

### 3. 面板「⑥ 浏览器形态」（tab0 新增第 6 组）

- 开关 `s_hl`「无头模式」，默认开；说明写明：**关掉后所有刷课/抓取/作业都会弹窗，
  仅调试用；人工登录始终有头，不受影响；保存后下一次启动浏览器生效**；
- `GET /api/settings` 返回 `stealth_headless`；`PUT` 布尔校验 → `upsert("stealth.headless")`；
- `CFG_CN` 早就有 `"stealth.headless": "无头模式"`，这次终于真的会被写进审计。

### 4. 测试（`tests/test_browser_launch.py`，9 例）

`test_default_true_without_any_config` / `test_follows_config_value` / `test_override_beats_config` /
`test_missing_stealth_section_defaults_true` / `test_launch_kwargs_carry_stealth_args` /
`test_extra_args_appended_without_duplicate` / `test_sync_launch_passes_kwargs` /
`test_async_launch_passes_kwargs` / **`test_no_direct_launch_outside_factory`**。

最后一条是**源码级守护**：除了 `browser.py`，任何地方再出现裸 `chromium.launch(` 就红——
与 `tests/test_async_api.py` 同一思路，防止以后又散回去。

## 四、**没做**的部分（说清楚，不假装）

| 原方案里的 | 处理 | 理由 |
|---|---|---|
| `engine` 档位（chromium / 真 Chrome `channel="chrome"`） | **未做** | 换真 Chrome 要重对齐一整套指纹（UA 版本、WebGL、`outerHeight`），属独立一项；且当前无此需求 |
| `viewport` 收进配置 | **未做** | 现在 1280×800 写在 `hardened_context` 里且与指纹一致，做成配置容易被改坏 |
| UA 做成配置项 | **不做** | UA 必须跟内核版本走（见 `stealth.py` 注释），随手改等于自曝自动化特征 |

→ 已登记 **Q45**（真 Chrome 档位），等有需求再开。

## 五、改动文件清单

| 文件 | 改动 |
|---|---|
| `src/xxt/core/browser.py` | **新建**（91 行）：`resolve_headless` / `launch_kwargs` / `launch` / `async_launch` |
| `src/xxt/adapters/chaoxing/astudy.py`、`study.py`、`profile_read.py`、`__init__.py` | 共 5 处改调工厂 |
| `src/xxt/session/login.py`、`relogin.py` | 共 3 处改调工厂（人工登录显式有头） |
| `src/xxt/panel/app.py` | 2 处改调工厂；tab0 新增「⑥ 浏览器形态」；`s_hl` 加载/保存；GET/PUT 支持 `stealth_headless` |
| `src/xxt/cli.py` | 2 处改调工厂（判题回读跟随配置、作业按 `--submit` 覆盖） |
| `src/xxt/config.py`、`config/settings.toml` | `[stealth]` 注释改写：说明真生效、可面板改、下次启动生效 |
| `tests/test_browser_launch.py` | **新建** 9 例 |
| `data/debug/_prove_q36.py`、`_prove_q37.py` | 新建探针（`data/` 不入库） |

## 六、验证

| 项 | 结果 |
|---|---|
| 全量 `pytest` | **275 passed**（42.97s） |
| 残留检查 | `grep chromium.launch src/xxt` → 只剩 `browser.py` 内 2 处实现 + `stealth.py` 1 行注释 |
| 端到端探针 `_prove_q36.py` | 默认 True → PUT false → `app_config` = false → `Settings()` = False → `resolve_headless` = False（人工登录仍 False=有头）→ PUT true 恢复 |
| Q37 复核 `_prove_q37.py` | 词表 3 条，tracked 文本**命中 0** |

## 七、复盘

1. **台账数字要"先量后改"**：`9 处`→`12 处`。会话 59 按文件数、同文件两处只算一次。
   以后登记"散落 N 处"必须写清"按出现次数还是按文件数"，并给出文件:行号。
2. **自己顺手加了无意义代码**：改 `cli.py` 时曾插入 `if not headed: pass` 一段空壳注释，
   自审当场删除。教训：替换时只写"必要的"，不顺手加"看起来像个照应"的废话。
3. **等价重构也要给默认值护栏**：本次能判断"今晚就做"，前提是默认值与旧行为逐项相同
   （headless=True / args 不变 / 人工登录不变）。若默认值会变，就必须等实机窗口。

## 八、交接块

- **进行中**：无。
- **下一步第一个动作**：用户**重启面板**（新 HTML + 新 `/api/settings` 字段）后，打开
  「⚙ 全局设置 → ⏱ 守护与节级策略」看第 **⑥ 组「浏览器形态」**是否出现；
  **保持默认开**，无需改动。刷课/守护不需要重启也能用（子进程每次会话重新读配置）。
- **阻塞点**：无。
- **已知边界**：`engine`（真 Chrome）与 `viewport` 未收进配置（见 §四，登记 Q45）。
