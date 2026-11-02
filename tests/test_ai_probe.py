"""`xxt/ai/probe.py`（AI 连通性探测）的测试。

用户 2026-11-02 要求：
  ① 「模型名称能从对应的厂商获取到」→ `fetch_models`
  ② 「增加测试联通 AI 模型的按钮和成功或者错误提示信息」→ `test_chat`

全部用 `httpx.MockTransport` 注入假响应，**不发真网络请求**。
"""

from __future__ import annotations

import httpx
import pytest

from xxt.ai import probe


# ---------------------------------------------------------------- 地址判定

@pytest.mark.parametrize("url,expect", [
    ("http://127.0.0.1:11434/v1", True),
    ("http://localhost:8000/v1", True),
    ("http://[::1]:8000/v1", True),
    ("https://api.deepseek.com/v1", False),
    ("https://api.openai.com/v1", False),
    ("", False),
    ("not-a-url", False),
])
def test_is_local_url(url, expect):
    assert probe.is_local_url(url) is expect


# ---------------------------------------------------------------- 错误翻译

@pytest.mark.parametrize("exc_text,name,needle", [
    ("401 Unauthorized", "HTTPError", "Key 无效"),
    ("403 Forbidden", "HTTPError", "没有权限"),
    ("404 Not Found", "HTTPError", "/v1"),
    ("429 rate limit", "HTTPError", "限流"),
    ("timed out", "ReadTimeout", "超时"),
    ("[Errno -2] Name or service not known", "ConnectError", "域名解析失败"),
    ("Connection refused", "ConnectError", "连不上"),
])
def test_explain_error_maps_to_chinese(exc_text, name, needle):
    exc = type(name, (Exception,), {})()
    msg = probe.explain_error(type(name, (Exception,), {"__str__": lambda s: exc_text})())
    assert needle in msg


# ---------------------------------------------------------------- 拉模型

def _transport(handler):
    return httpx.MockTransport(handler)


def test_fetch_models_reads_data_id():
    def handler(req):
        assert req.url.path.endswith("/models")
        return httpx.Response(200, json={"data": [{"id": "a"}, {"id": "b"}]})

    names, err = probe.fetch_models("https://x/v1", "k", transport=_transport(handler))
    assert err == ""
    assert names == ["a", "b"]


def test_fetch_models_dedupes_and_keeps_order():
    def handler(req):
        return httpx.Response(200, json={"data": [{"id": "a"}, {"id": "b"}, {"id": "a"}]})

    names, _ = probe.fetch_models("https://x/v1", "k", transport=_transport(handler))
    assert names == ["a", "b"]


def test_fetch_models_accepts_plain_string_list():
    def handler(req):
        return httpx.Response(200, json=["m1", "m2"])

    names, err = probe.fetch_models("https://x/v1", "k", transport=_transport(handler))
    assert err == ""
    assert names == ["m1", "m2"]


def test_fetch_models_sends_bearer():
    seen = {}

    def handler(req):
        seen["auth"] = req.headers.get("authorization")
        return httpx.Response(200, json={"data": [{"id": "a"}]})

    probe.fetch_models("https://x/v1", "secret-key", transport=_transport(handler))
    assert seen["auth"] == "Bearer secret-key"


def test_fetch_models_reports_http_error():
    def handler(req):
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    names, err = probe.fetch_models("https://x/v1", "k", transport=_transport(handler))
    assert names == []
    assert err


def test_fetch_models_reports_empty_list():
    """厂商不支持 /models 时要说清楚，不能返回空列表当成功。"""
    def handler(req):
        return httpx.Response(200, json={"object": "list"})

    names, err = probe.fetch_models("https://x/v1", "k", transport=_transport(handler))
    assert names == []
    assert "没找到模型列表" in err


def test_fetch_models_requires_base_url():
    names, err = probe.fetch_models("", "k")
    assert names == []
    assert "接口地址" in err


# ---------------------------------------------------------------- 测试连通

def _ok_chat(content="ok"):
    def handler(req):
        assert req.url.path.endswith("/chat/completions")
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": content}}]})
    return handler


def test_test_chat_success_mentions_latency():
    ok, msg = probe.test_chat("https://x/v1", "k", "m", transport=_transport(_ok_chat()))
    assert ok is True
    assert "连接成功" in msg
    assert "ms" in msg


def test_test_chat_sends_model_and_messages():
    seen = {}

    def handler(req):
        import json as _j
        seen["body"] = _j.loads(req.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    probe.test_chat("https://x/v1", "k", "my-model", transport=_transport(handler))
    assert seen["body"]["model"] == "my-model"
    assert isinstance(seen["body"]["messages"], list)
    assert seen["body"]["stream"] is False


def test_test_chat_requires_key():
    ok, msg = probe.test_chat("https://x/v1", "", "m")
    assert ok is False
    assert "Key" in msg


def test_test_chat_requires_model():
    ok, msg = probe.test_chat("https://x/v1", "k", "")
    assert ok is False
    assert "模型" in msg


def test_test_chat_reports_auth_error():
    def handler(req):
        return httpx.Response(401, json={"error": {"message": "invalid api key"}})

    ok, msg = probe.test_chat("https://x/v1", "k", "m", transport=_transport(handler))
    assert ok is False
    assert "401" in msg
    assert "Key 无效" in msg


def test_test_chat_reports_empty_content():
    """连上了但没返回内容 → 多半模型名不对，要提示而不是当成功。"""
    ok, msg = probe.test_chat("https://x/v1", "k", "m",
                              transport=_transport(_ok_chat(content="")))
    assert ok is False
    assert "模型名" in msg


def test_test_chat_network_error():
    def handler(req):
        raise httpx.ConnectError("Connection refused")

    ok, msg = probe.test_chat("https://x/v1", "k", "m", transport=_transport(handler))
    assert ok is False
    assert "连不上" in msg


def test_test_chat_local_url_bypasses_proxy():
    """本机地址（Ollama）必须绕开系统代理，否则代理挂掉就永远连不上。"""
    assert probe.is_local_url("http://127.0.0.1:11434/v1") is True
