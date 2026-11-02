"""AI 厂商连通性探测：拉模型列表 + 试一次最小对话。

## 为什么单独一层

面板要两个功能（用户 2026-11-02）：
1. 「模型名称能从对应的厂商获取到」→ 调 `{base_url}/models`
2. 「增加测试联通 AI 模型的按钮和成功或者错误提示信息」→ 发一次最小 chat 请求

这两件事都是**纯网络**、与平台无关，所以单独放这里：
面板只负责把结果渲染成人话，判断逻辑可单测（注入假 transport 即可）。

## 代理与本地地址

- `127.0.0.1` / `localhost`（Ollama 本地模型）→ **绕开系统代理**：
  本机地址走代理本身就是错的，代理挂掉还会让探测直接失败。
- 外网厂商 → 走系统环境变量里的代理（有些网络必须走代理才能出去）。
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlparse

__all__ = ["fetch_models", "test_chat", "is_local_url", "explain_error"]

#: 探测用的最小输入：只要求模型回一个词，省 token、快。
_PING_PROMPT = "Reply with the single word: ok"


def is_local_url(base_url: str) -> bool:
    """是不是本机地址（决定要不要绕代理）。"""
    try:
        host = (urlparse(str(base_url or "")).hostname or "").lower()
    except ValueError:
        return False
    return host in {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


def explain_error(exc: Exception) -> str:
    """把底层异常翻成用户能看懂的一句话（面板直接显示）。"""
    name = type(exc).__name__
    text = str(exc) or ""
    low = text.lower()
    if "401" in text or "unauthorized" in low or "invalid api key" in low:
        return "认证失败：API Key 无效或已过期"
    if "403" in text or "forbidden" in low:
        return "没有权限：这个 Key 不能访问该模型"
    if "404" in text:
        return "地址不对：接口地址可能少了 /v1 或路径写错"
    if "429" in text or "rate limit" in low:
        return "被限流：请求太频繁，稍后再试"
    if "timeout" in low or name in {"ConnectTimeout", "ReadTimeout", "TimeoutException"}:
        return "超时：网络慢或地址不通"
    if "name or service not known" in low or "getaddrinfo" in low:
        return "域名解析失败：接口地址写错了"
    if "connection refused" in low or "connecterror" in low:
        return "连不上：服务没启动或端口不对"
    if "certificate" in low or "ssl" in low:
        return "证书错误：HTTPS 校验失败"
    return f"{name}: {text[:160]}" if text else name


def _client(base_url: str, timeout: float, transport: Any = None):
    """建 httpx 客户端：本机地址绕代理，外网走环境代理。

    	`transport` 允许测试注入 httpx.MockTransport，不发真请求。
    """
    import httpx

    trust_env = not is_local_url(base_url)
    kw: dict[str, Any] = {"timeout": timeout, "trust_env": trust_env,
                          "follow_redirects": True}
    if transport is not None:
        kw["transport"] = transport
    return httpx.Client(**kw)


def _headers(api_key: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    if api_key:
        h["Authorization"] = f"Bearer {api_key}"
    if extra:
        h.update(extra)
    return h


def fetch_models(base_url: str, api_key: str, *, timeout: float = 20.0,
                 transport: Any = None) -> tuple[list[str], str]:
    """调 `GET {base_url}/models` 取模型名列表。

    返回 `(模型名列表, 错误说明)`。成功时错误说明是空串。
    不同厂商返回结构不完全一致（多数是 `{"data":[{"id": ...}]}`），
    这里把几种常见形状都兜住；实在认不出就报错而不是瞎猜。
    """
    url = str(base_url or "").strip().rstrip("/")
    if not url:
        return [], "还没填接口地址"
    try:
        with _client(url, timeout, transport) as cli:
            r = cli.get(url + "/models", headers=_headers(api_key, {"Accept": "application/json"}))
            if r.status_code >= 400:
                return [], f"HTTP {r.status_code}：{explain_error(Exception(r.text[:200]))}"
            data = r.json()
    except Exception as exc:                                  # noqa: BLE001
        return [], explain_error(exc)

    items = []
    if isinstance(data, dict):
        for key in ("data", "models", "items", "result"):
            v = data.get(key)
            if isinstance(v, list):
                items = v
                break
        if not items and isinstance(data.get("data"), dict):
            inner = data["data"]
            for key in ("models", "items", "list"):
                if isinstance(inner.get(key), list):
                    items = inner[key]
                    break
    elif isinstance(data, list):
        items = data

    names: list[str] = []
    for it in items or []:
        if isinstance(it, str):
            names.append(it)
        elif isinstance(it, dict):
            for k in ("id", "model", "name"):
                v = it.get(k)
                if isinstance(v, str) and v.strip():
                    names.append(v.strip())
                    break
    # 去重保序
    seen: set[str] = set()
    out = [n for n in names if not (n in seen or seen.add(n))]
    if not out:
        return [], "接口返回里没找到模型列表（该厂商可能不支持 /models）"
    return out, ""


def test_chat(base_url: str, api_key: str, model: str, *,
              timeout: float = 30.0, transport: Any = None) -> tuple[bool, str]:
    """发一次最小对话，验证「地址 + Key + 模型」三者是否可用。

    返回 `(是否成功, 给人看的说明)`。
    成功说明里带耗时，便于判断网络质量。
    """
    url = str(base_url or "").strip().rstrip("/")
    model = str(model or "").strip()
    if not url:
        return False, "还没填接口地址"
    if not model:
        return False, "还没填模型名"
    if not api_key:
        return False, "这个厂商还没配 API Key"
    body = {
        "model": model,
        "messages": [{"role": "user", "content": _PING_PROMPT}],
        "max_tokens": 8,
        "temperature": 0,
        "stream": False,
    }
    began = time.monotonic()
    try:
        with _client(url, timeout, transport) as cli:
            r = cli.post(url + "/chat/completions",
                         headers=_headers(api_key), json=body)
            elapsed = int((time.monotonic() - began) * 1000)
            if r.status_code >= 400:
                detail = ""
                try:
                    err = r.json()
                    detail = str((err.get("error") or {}).get("message")
                                 or err.get("message") or "")[:160]
                except Exception:                             # noqa: BLE001
                    detail = r.text[:160]
                hint = explain_error(Exception(f"{r.status_code} {detail}"))
                return False, f"失败（HTTP {r.status_code}）：{hint}" + (
                    f"｜{detail}" if detail else "")
            data = r.json()
    except Exception as exc:                                  # noqa: BLE001
        return False, f"失败：{explain_error(exc)}"

    # 解析回包：正常是 choices[0].message.content
    text = ""
    try:
        choices = data.get("choices") or []
        if choices:
            msg = choices[0].get("message") or {}
            text = str(msg.get("content") or "").strip()
    except Exception:                                         # noqa: BLE001
        pass
    if not text:
        # 有回包但没有内容：多半是模型名不对或该模型不支持 chat
        return False, ("连上了，但模型没有返回内容 —— 可能模型名不对，"
                       "或该模型不支持 chat/completions 接口")
    return True, f"连接成功（{elapsed} ms）· 模型回：{text[:40]}"
