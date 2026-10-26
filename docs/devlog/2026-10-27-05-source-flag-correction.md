# 2026-10-27-05 来源标识纠偏：脚本伪装 `source=ui` 不再冒充「面板按钮」

## 一、目标与问题

会话 45 给守护启停加了来源标识（`_auto_who`），但用户 2026-10-27 在日志页贴出 8 条
`auto-request … 来源=面板按钮` 并追问“是你调试还是自动跑的”——**全部是诊断脚本**。

根因（`panel/app.py` 首版 `_auto_who`）：

```python
if source == "ui":
    return "面板按钮"      # 直接返回，UA 都不看
```

而诊断脚本为模拟真实点击，正是带 `?source=ui` 调 `/api/auto/start` → 脚本与真人点击在
`run_logs` 里**完全相同**。会话 45 的 `--via-ui` 开关不但没解决，反而制造了“看起来像用户点的”假象。

## 二、改动：自称 + 证据双重校验

| 来路 | 判据（按优先级） | 记为 | 级别 |
|---|---|---|---|
| 诊断脚本 | 请求头 `X-Diag: <脚本名>`（最高优先，UA 可伪装成浏览器也无所谓） | `诊断脚本(q_readback.py)` | INFO |
| 诊断脚本 | `?source=diag` | `诊断脚本` | INFO |
| 伪装的面板点击 | `?source=ui` 但 UA 含 httpx/requests/curl/python-/testclient… 或 UA 为空 | `API(python-httpx/0.28.1)（自称 UI）` | **WARNING** |
| 真面板点击 | `?source=ui` 且 UA 含 `Mozilla` 且无脚本特征 | `面板按钮` | INFO |
| 其它 | 其余 UA 特征 | `API(httpx)` / `API(requests)` / `API(浏览器)` / `API(无UA)` | INFO |

- 新增 `_SCRIPT_UA_HINTS` + `_looks_script_ua()`（空 UA 也算脚本：真人浏览器必带 Mozilla）；
- 新增 `_who_level()`：含“自称 UI”即 WARNING，伪装在黄色行里一眼看见；
- **被拒**（已在运行）仍固定 WARNING —— 那是事件本身该报警，不看来源；
- `auto_stop` 的 detail 同规则；诊断脚本批量补 `X-Diag` 头（`data/debug/*.py` 7 个）。

## 三、实测（真面板 + 真 HTTP，零平台访问）

`data/debug/q_who_label.py`（三原则：留痕 `diag-who-label` / 文件锁 / finally 还原），
开始前把两号 `start_at` 推到 23:50 → 守护只打印计划，绝不触发开刷：

```
原始 start_at = {1: '10:14', 2: '09:20'}｜auto.plan=None
PASS ① source=ui + httpx UA｜[WARNING] 启动请求已受理（pid=5044）｜来源=API(python-httpx/0.28.1)（自称 UI）
PASS ② X-Diag + 浏览器 UA｜[INFO] 启动请求已受理（pid=23880）｜来源=诊断脚本(q_who_label.py)
PASS ②b stop 也记诊断脚本｜[INFO] 面板停止定时调度（来源=诊断脚本(q_who_label.py)；…）
PASS ③ source=ui + 浏览器 UA｜[INFO] 启动请求已受理（pid=19232）｜来源=面板按钮
已还原 start_at = {1: '10:14', 2: '09:20'}｜auto.plan = None
```

收尾核对：`data/run` 空、`busy_sessions={}`、无孤儿 `xxt auto` 进程、`auto.plan=None`；
面板已用新代码重启（pid=11800）；`pytest` **135 passed**（+3）。

## 四、文件清单

| 文件 | 变化 |
|---|---|
| `src/xxt/panel/app.py` | `_SCRIPT_UA_HINTS` / `_looks_script_ua` / `_who_level`；`_auto_who` 改为 X-Diag → diag → ui+UA 三重判断；`auto_start` 受理与 `auto_stop` 的 level 随来源 |
| `tests/test_panel.py` | `UI_HEADERS` 浏览器 UA 常量；`_start_once` / `_last_auto_request` 辅助；新增 3 条（ui+httpx UA 记“自称 UI”且 WARNING / X-Diag 优先 / `source=diag`）；原「面板按钮」用例补浏览器 UA |
| `docs/devlog/2026-10-27-02-auto-start-traceability.md` | `--via-ui` 处加纠偏注记（指向本文） |
| `docs/devlog/2026-10-27-03-open-questions-audit.md` | D 条补“标识已纠偏、鉴权仍未做” |
| `data/debug/*.py`（不入库） | 7 个诊断脚本补 `X-Diag` 头（`q_who_label.py` 新增验证脚本） |

**部署文件**：本次只改面板内部标识，`deploy/Dockerfile`、`docker-compose.yml`、
`deploy/README.md`、`docs/DEPLOY_LINUX.md` **均无需变动**（无新依赖/环境变量）。

## 五、交接块

- **进行中**：无。纠偏已落地并实测 4/4 PASS。
- **下一步第一个动作**：`Ctrl+F5` →「📜 日志 → 业务事件」，确认历史里那 8 条仍显示「面板按钮」（旧数据不会改写），
  新触发的脚本请求应显示「诊断脚本(…)」或「API(httpx)（自称 UI）」。
- **阻塞点**：无。
- **边界（备忘）**：`/api/auto/start|stop` **仍无鉴权**——本次只是让日志说实话，不是防伪；
  面板只监听 `127.0.0.1` 的前提下维持该边界（Docker 暴露 0.0.0.0 时须先补鉴权，见 STATUS §4 P1）。
- **同类未处理**：`POST /api/study/start` 的 `body["source"]` 仍由调用方自由声明（`定时`/`学习安排`），
  脚本伪装同样无法区分；如需一致处理可在后续会话按同一思路加 `X-Diag`。
