"""`xxt/ai/providers.py`（AI 厂商配置）的测试 —— 为 B5（AI 回填）做准备。

守五条：

① **内置基线可用**：默认列表里每家都必须有 name/base_url/model 三件套；
② **用户能加、能改、能删**，且新增的排在内置之后；
③ **只动用户自定义**：内置的几家不写进 saved（否则升级内置基线时会被旧值盖住）；
④ **缺必填要报错、不静默**：错误说明要能直接给人看；
⑤ `merge` 能补出完整三件套（配置里可能只存了厂商名）。

本文件只测纯逻辑，不碰数据库、不出网络。
"""

from __future__ import annotations

import pytest

from xxt.ai import providers as pv


# ---------------------------------------------------------------- 内置基线

def test_builtin_providers_are_complete():
    assert len(pv.PROVIDERS) >= 5
    for p in pv.PROVIDERS:
        assert p.get("name"), f"厂商缺名字: {p}"
        assert p.get("base_url"), f"{p.get('name')} 缺地址"
        assert p.get("model"), f"{p.get('name')} 缺模型"
        # 结尾不带 /：拼 /chat/completions 时会再处理
        assert not p["base_url"].endswith("/")


def test_builtin_names_are_unique():
    names = [p["name"] for p in pv.PROVIDERS]
    assert len(names) == len(set(names))


def test_as_list_starts_with_builtin():
    got = pv.as_list([])
    assert got[0]["name"] == pv.PROVIDERS[0]["name"]
    assert len(got) == len(pv.PROVIDERS)


def test_none_saved_is_fine():
    assert len(pv.as_list(None)) == len(pv.PROVIDERS)


# ---------------------------------------------------------------- 查

def test_get_by_name():
    hit = pv.get("DeepSeek")
    assert hit is not None
    assert hit["base_url"] == "https://api.deepseek.com/v1"
    assert hit["model"] == "deepseek-chat"


def test_get_unknown_returns_none_instead_of_fallback():
    """找不到就返回 None，不悄悄回落到第一家（静默回落会让用户以为选错了却生效了）。"""
    assert pv.get("不存在的厂家") is None


def test_names():
    assert "DeepSeek" in pv.names()


# ---------------------------------------------------------------- 新增 / 修改 / 删除

def test_upsert_adds_custom_provider():
    saved, err = pv.upsert([], {"name": "自建", "base_url": "https://my.ai/v1/",
                                "model": "my-model"})
    assert err == ""
    assert any(p["name"] == "自建" for p in saved)
    # 结尾的 / 被去掉
    assert pv.get("自建", saved)["base_url"] == "https://my.ai/v1"


def test_upsert_overwrites_same_name():
    saved, _ = pv.upsert([], {"name": "自建", "base_url": "https://a/v1", "model": "m1"})
    saved, _ = pv.upsert(saved, {"name": "自建", "base_url": "https://b/v1", "model": "m2"})
    hits = [p for p in saved if p["name"] == "自建"]
    assert len(hits) == 1
    assert hits[0]["base_url"] == "https://b/v1"

def test_builtin_not_written_into_saved():
    """内置那几家不落 saved —— 否则以后升级内置基线会被旧值盖住。"""
    saved, err = pv.upsert([], {"name": "DeepSeek", "base_url": "https://x/v1",
                                "model": "custom"})
    assert err == ""
    # saved 里有了（因为是用户显式改的），但内置列表本身没被改
    assert pv.get("DeepSeek")["base_url"] == "https://api.deepseek.com/v1"
    assert pv.get("DeepSeek", saved)["base_url"] == "https://x/v1"


def test_remove_only_custom():
    saved, _ = pv.upsert([], {"name": "自建", "base_url": "https://a/v1", "model": "m"})
    saved = pv.remove(saved, "自建")
    assert all(p["name"] != "自建" for p in saved)


def test_remove_unknown_is_noop():
    saved, _ = pv.upsert([], {"name": "自建", "base_url": "https://a/v1", "model": "m"})
    assert len(pv.remove(saved, "没这家")) == 1


# ---------------------------------------------------------------- 校验

@pytest.mark.parametrize("bad", [
    None,
    {},
    {"name": "只名字"},
    {"name": "缺模型", "base_url": "https://a/v1"},
    {"base_url": "https://a/v1", "model": "m"},
])
def test_incomplete_item_rejected(bad):
    got, err = pv.validate(bad)
    assert got is None
    assert err, "必须给出可读的错误说明，不能静默失败"


def test_error_message_names_missing_fields():
    _, err = pv.validate({"name": "缺东西"})
    assert "接口地址" in err
    assert "模型" in err


def test_upsert_keeps_saved_on_error():
    """校验失败时不能把已有的配置清掉。"""
    saved, _ = pv.upsert([], {"name": "ok", "base_url": "https://a/v1", "model": "m"})
    saved2, err = pv.upsert(saved, {"name": "坏的"})
    assert err
    assert saved2 == saved


def test_dirty_saved_is_cleaned():
    """saved 里混进脏数据（缺字段/不是 dict）要能自动滤掉。"""
    saved, err = pv.upsert([{"name": "坏的"}, "不是字典", None],
                           {"name": "ok", "base_url": "https://a/v1", "model": "m"})
    assert err == ""
    assert [p["name"] for p in saved] == ["ok"]


# ---------------------------------------------------------------- merge

def test_merge_fills_base_url_and_model_from_name():
    got = pv.merge({"provider": "DeepSeek"})
    assert got["base_url"] == "https://api.deepseek.com/v1"
    assert got["model"] == "deepseek-chat"


def test_merge_keeps_explicit_values():
    got = pv.merge({"provider": "DeepSeek", "base_url": "https://x/v1",
                    "model": "custom"})
    assert got["base_url"] == "https://x/v1"
    assert got["model"] == "custom"


def test_merge_without_provider_is_untouched():
    got = pv.merge({"base_url": "https://x/v1", "model": "m"})
    assert got == {"base_url": "https://x/v1", "model": "m"}


# ---------------------------------------------------------------- 说明

def test_every_field_has_description():
    for f in pv.FIELDS:
        assert f.get("key") and f.get("cn") and f.get("desc"), f"字段说明不全: {f}"


def test_api_key_field_is_optional():
    key = next(f for f in pv.FIELDS if f["key"] == "api_key")
    assert key["required"] == "0"
