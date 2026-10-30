# 2026-10-30-16 B9 刷课前置网络自检 + Chrome 绕开代理

## 一、背景

用户说「我现在有开代理」，问"能不能用代理"。实测（真实 Chrome，非 curl）：

| 方式 | 到站点 | 内容 |
|---|---|---|
| 系统代理（默认） | **超过 60 秒，超时** | — |
| `--no-proxy-server` | **0.33 秒** | 正常 |
| 强制直连（urllib） | 0.11 秒 | sha 一致 |

站点每 60 秒上报一次学习进度（`updateTime.ashx`）→ 链路慢/打不开会让**学时真丢，
但页面可能看起来在播**。

同时确认：**平台并不拒绝代理**（代理返回的内容与直连 sha 相同），只是慢到不可靠。
所以原设计"检测到代理就拒绝起跑"是错的——它会误拦"代理开着但站点直连"的正常情况。

## 二、改动

### 1. `chrome.py`：给专用 Chrome 加 `--no-proxy-server`

```python
_CHROME_ARGS = [
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-blink-features=AutomationControlled",
    "--no-proxy-server",   # 2026-10-30
]
```

**效果**：这个专用 Chrome 自己绕过代理，**不碰系统代理设置**（别的软件照常用代理）。
用户不用每次记着切模式。

### 2. `netcheck.py`：开跑前实测到站点的耗时

关键设计：**判据是实测耗时，不是注册表 `ProxyEnable`**。

原因：`ProxyEnable=1` 只说明"系统设了代理"，不代表到站点的流量走了代理
（本机实测就是"站点直连、境外走代理"）。所以必须真的 GET 一次。

```python
preflight() -> {"ok": bool, "secs": float, "advice": str}
```

- 测 3 次取**最快**（网络抖动会误判，取平均会被一次抖动拖死）；
- 阈值 **3 秒**（直连正常 0.1~0.3 秒）；
- 超过阈值 → `ok=False`，并给可照做的修法（v2rayN 改直连 / 临时关系统代理 / 确认代理在跑）；
- **不自动改代理设置**（系统级改动，只报警不改）。

### 3. `run_gxks.py`：自检接到浏览器启动之前

```python
if not args.no_netcheck:
    net = netcheck.preflight()
    print(netcheck.format_report(net))
    if not net["ok"]:
        print("\n[SKIP] 网络自检未通过 → 不打开浏览器、不访问平台")
        return 4
```

位置在 `gbrowser.open_session()` **之前**，符合既有的"被拒时零浏览器零请求"原则
（devlog 23 已经因为类似问题把额度检查前置过）。

## 三、验证

| 项 | 结果 |
|---|---|
| `pytest` 全量 | ✅ **669 passed**（653 → 669，+16） |
| `test_gxks_netcheck.py` | ✅ 新增 12 条：测 3 次取最快 / 失败恢复 / 超时拒绝 / 边界值 / 不自动改代理 / 直连探针绕过代理 |
| `test_run_gxks_netcheck.py` | ✅ 新增 4 条：没过→返回 4 且不打开浏览器 / `--no-netcheck` 跳过 / 过了才继续往下走 |
| `test_gxks_chrome.py` | ✅ +1 条：`launch_argv` 必须含 `--no-proxy-server` |
| 实跑 `netcheck.preflight()` | ✅ `[OK] 到站点 0.14s（阈值 3s）→ 可起跑` |
| 实测 `--no-proxy-server`（Playwright + 真实 Chrome） | ✅ 0.33 秒打开；不带该参数则**超时 60 秒** |
| 边界 | ⚠ 未验：站点**真正慢**时的拒绝路径（本机直连是 0.14 秒，没触发）；未验：`run_gxks.py --no-netcheck` 在浏览器里的真实行为 |

## 四、关键决策及理由

1. **用 Chrome 启动参数绕开代理，而不是让用户去关代理**：
   关代理是系统级改动，用户可能不记得切回来；`--no-proxy-server` 只影响这一个专用 Chrome。
2. **判据是实测耗时，不是 `ProxyEnable`**：注册表值看不出"站点是否被绕出去"，会误判。
3. **测 3 次取最快**：网络抖动会误判，取平均会被一次抖动拖死。
4. **阈值 3 秒**：直连正常 0.1~0.3 秒，3 秒是宽松上限，避免误拦。
5. **不自动改代理设置**：系统级改动，只报警并给修法。
6. **返回 4 而不是 1**：与既有退出码语义区分开（0 成功 / 1 异常 / 2 连不上浏览器 / 3 掉登录 / 4 被自检拦下）。

## 五、改动文件清单

| 文件 | 改了什么 |
|---|---|
| `src/xxt/adapters/gxks/chrome.py` | `_CHROME_ARGS` 加 `--no-proxy-server`（+注释说明为什么） |
| `src/xxt/adapters/gxks/netcheck.py` | **新增**：`measure()` / `preflight()` / `advice()` / `format_report()` |
| `scripts/run_gxks.py` | 新增 `--no-netcheck` 参数；自检接到 `open_session()` 之前，没过返回 4 |
| `tests/test_gxks_netcheck.py` | **新增 12 条** |
| `tests/test_run_gxks_netcheck.py` | **新增 4 条** |
| `tests/test_gxks_chrome.py` | **+1 条**：`--no-proxy-server` 必须在 `launch_argv` 里 |
| `docs/gxks/TODO-GXKS.md` | B9 勾掉 |
| `docs/gxks/gaoxiaokaoshi.md` | §7.3 补"平台不拒绝代理，只是慢到不可靠" |

## 六、已知边界

1. **站点真正慢时的拒绝路径未实跑**（本机直连 0.14 秒，没触发阈值）；
2. **`--no-netcheck` 的真实行为未验**（只验了"会跳过自检"这个分支）；
3. **代理模式下的真实刷新节列表 / 刷课链路未验**（Chrome 9222 没开）；
4. **登录 IP 跳变风险未验证**（没做过登录态下直连 vs 代理的 IP 对比实验）。
