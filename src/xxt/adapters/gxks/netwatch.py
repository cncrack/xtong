"""第三方资源域名白名单 —— 让“站点偷偷加脚本/上报域”变成可告警的事（Q5）。

为什么值得写这几行：本轮“无指纹 SDK、无空闲检测”的结论是靠**人工统计域名**得出的，
而这类结论会随改版失效。白名单把“人工看过一次”变成“每节都在看”。

⚠ 发现未知域名时的正确动作是：**先停下来人工看一眼**（在平台档案里登记，再改白名单），
而不是把新域名加进去让告警消失 —— 告警本身就是产物。
"""

from __future__ import annotations

from urllib.parse import urlparse

from . import selectors

# 这些不是网络资源，不参与域名比对
_IGNORED_SCHEMES = ("blob", "data", "about", "javascript", "file", "")


def host_of(url: str) -> str | None:
    """取主机名；非网络资源（`blob:`/`data:`/`about:`）返回 None。"""
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme.lower() in _IGNORED_SCHEMES:
        return None
    return (parsed.hostname or "").lower() or None


def is_allowed_host(host: str | None) -> bool:
    if not host:
        return True                     # 非网络资源，放行
    if host in selectors.ALLOWED_HOSTS:
        return True
    # 带前导点做后缀匹配：`x.aliyuncs.com` 命中，`evilaliyuncs.com` 不命中
    return any(host.endswith(suffix) for suffix in selectors.ALLOWED_HOST_SUFFIXES)


def unknown_hosts(urls: list[str]) -> list[str]:
    """返回**未登记**的主机名（去重保序，便于直接进日志/告警）。"""
    seen: dict[str, None] = {}
    for url in urls:
        host = host_of(url)
        if host and not is_allowed_host(host):
            seen.setdefault(host, None)
    return list(seen)
