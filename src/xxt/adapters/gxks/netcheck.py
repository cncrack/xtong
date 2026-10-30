"""刷课前置的网络自检（2026-10-30，B9）。

## 要防的是什么

实测（真实 Chrome，非 curl）：系统代理（v2rayN/xray）开着时，到目标站点
**单次请求 10 秒以上，浏览器甚至超时打不开**；绕过代理是 **0.33 秒**。
而站点每 60 秒上报一次学习进度（`updateTime.ashx`）——上报节奏崩掉，
**学时真丢，但页面看起来"在播"**，这是最难发现的一类故障。

## 为什么用"实测耗时"而不是"读注册表"

`ProxyEnable=1` 只说明"系统设置了代理"，**不代表到站点的流量走了代理**——
取决于代理软件的路由规则（本机实测就是"站点直连、境外走代理"）。
所以判据必须是**真实测一次到站点的耗时**，注册表值只能当提示信息。

## 与 Chrome 启动参数的关系

[chrome.py](chrome.py) 的 `_CHROME_ARGS` 里有 `--no-proxy-server`：
那个 Chrome 自己绕过代理，所以本模块测的是**直连**。直连慢 = 真慢，拦下来
是正确行为；而不是"代理开着所以不该刷"这种会被误判的规则。

## 用法

    python -c "from xxt.adapters.gxks.netcheck import preflight; print(preflight())"

    → {"ok": True, "secs": 0.33, "advice": ""}
    → {"ok": False, "secs": 11.2, "advice": "...怎么修..."}

阈值 3 秒：站点直连正常是 0.1~0.3 秒，超过 3 秒说明链路有问题。
"""

from __future__ import annotations

import time
import urllib.request

#: 目标站点的探针页：公开、无凭据、不触发业务逻辑。
#: 用 **HTTP** 不是 HTTPS —— 与 `selectors.LOGIN_PAGE` 同源同协议，避免被重定向到别的域。
from . import selectors  # noqa: E402  （放函数外，避免循环导入）

PROBE_URL = selectors.LOGIN_PAGE

#: 超过这个秒数就拒绝起跑
THRESHOLD_SECS = 3.0

#: 测几次取最快（网络抖动会误判，重试 3 次取最好成绩）
_TRIES = 3


def _probe(url: str, timeout: float = 20.0) -> tuple[float, int]:
    """强制直连（`ProxyHandler({})` 显式绕过系统代理）打一次页面。

    返回 `(耗时秒, 字节数)`；失败返回 `(耗时秒, 0)`，调用方用"最快一次且有内容"判断。
    """
    handlers = [urllib.request.ProxyHandler({})]
    opener = urllib.request.build_opener(*handlers)
    t0 = time.monotonic()
    try:
        with opener.open(url, timeout=timeout) as resp:
            body = resp.read()
        return time.monotonic() - t0, len(body)
    except Exception:                                      # noqa: BLE001
        return time.monotonic() - t0, 0


def measure() -> dict:
    """测一次到站点的实际耗时，返回 `{"secs": 最快秒数, "ok": 有内容, "url": ...}`。

    单独暴露，便于测试注入替身、也便于以后从面板直接调用展示。
    """
    best: float | None = None
    got_content = False
    for _ in range(_TRIES):
        secs, size = _probe(PROBE_URL)
        if size > 0:
            got_content = True
        if best is None or secs < best:
            best = secs
    return {"secs": round(best if best is not None else 0.0, 2),
            "ok": got_content, "url": PROBE_URL, "tries": _TRIES}


def advice() -> str:
    """超时时要给人的、可照做的修法（不是一句"失败了"）。"""
    return (
        f"到 {PROBE_URL} 的请求超过 {THRESHOLD_SECS:g} 秒——学时会上报失败，"
        "**但页面可能看起来还在播**。\n"
        "修法（任选其一）：\n"
        "  1. v2rayN / 代理软件里把规则改成**直连**大陆站点（或切「路由模式」）；\n"
        "  2. 临时关掉系统代理：设置 → 网络和 Internet → 代理 → 关掉「使用代理服务器」；\n"
        "  3. 确认代理软件正在运行（端口没监听时系统代理会让请求直接失败）。"
    )


def preflight() -> dict:
    """刷课前置自检。返回 `{"ok": bool, "secs": float, "advice": str}`。

    ⚠ **只读**：不登录、不提交、不改任何状态；只 GET 一个公开页。
    """
    got = measure()
    ok = got["ok"] and got["secs"] <= THRESHOLD_SECS
    return {"ok": ok, "secs": got["secs"],
            "content_ok": got["ok"],
            "advice": "" if ok else advice(),
            "url": got["url"], "threshold_secs": THRESHOLD_SECS}


def format_report(result: dict) -> str:
    """把自检结果排成人可读的几行（终端/面板日志用）。"""
    if result["ok"]:
        return (f"[OK] 到站点 {result['url']} 耗时 {result['secs']}s"
                f"（阈值 {result['threshold_secs']:g}s）→ 可起跑")
    return (f"[FAIL] 到站点 {result['url']} 耗时 {result['secs']}s，"
            f"超过阈值 {result['threshold_secs']:g}s\n"
            f"{result['advice']}")
