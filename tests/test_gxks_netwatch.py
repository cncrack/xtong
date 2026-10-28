"""域名白名单回归（Q5）。

下面的“真实清单”取自 2026-10-29 抓包（只保留主机与路径，查询串全部去掉），
用来做**回归**：本轮实测出现过的域名必须全部放行；再注入一个假域名验证告警真的会响。
"""

from __future__ import annotations

from xxt.adapters.gxks.netwatch import host_of, is_allowed_host, unknown_hosts

# 2026-10-29 实测出现过的全部域名类型（已去掉查询串与凭据）
OBSERVED_URLS = [
    "http://www.gaoxiaokaoshi.com/Study/LibraryStudyList.aspx",
    "http://www.gaoxiaokaoshi.com/Study/updateTime.ashx",
    "http://vod.gaoxiaokaoshi.com/xxx/xxx-ld-m3u8-encrypt-stream-00014.ts",
    "https://g.alicdn.com/de/prismplayer/2.9.11/aliplayer-min.js",
    "https://g.alicdn.com/de/prismplayer/2.9.11/hls/aliplayer-hls-min.js",
    "https://videocloud.cn-hangzhou.log.aliyuncs.com/logstores/newplayer/track",
    "blob:http://www.gaoxiaokaoshi.com/23691cbb-6c11-426f-ba2b-a171e8a3788f",
    "about:blank",
]


def test_observed_urls_all_allowed():
    """回归：本轮实跑出现过的域名，白名单一个都不能漏（否则真机一跑就误报）。"""
    assert unknown_hosts(OBSERVED_URLS) == []


def test_unknown_domain_is_reported():
    found = unknown_hosts(OBSERVED_URLS + ["https://t.example.com/collect", "https://cdn.evil.net/x.js"])
    assert found == ["t.example.com", "cdn.evil.net"]


def test_suffix_match_not_fooled_by_lookalike():
    """`evilaliyuncs.com` 不是 `*.aliyuncs.com`：带前导点匹配才安全。"""
    assert is_allowed_host("evilaliyuncs.com") is False
    assert is_allowed_host("a.aliyuncs.com") is True
    assert is_allowed_host("notgaoxiaokaoshi.com") is False


def test_non_network_urls_ignored():
    assert host_of("blob:http://www.gaoxiaokaoshi.com/uuid") is None
    assert host_of("data:text/html;base64,AAAA") is None
    assert host_of("about:blank") is None
    assert host_of("") is None
    assert unknown_hosts(["blob:http://x/uuid", "about:blank"]) == []


def test_dedupe_keeps_order():
    urls = ["https://b.example.com/1", "https://a.example.com/2", "https://b.example.com/3"]
    assert unknown_hosts(urls) == ["b.example.com", "a.example.com"]
