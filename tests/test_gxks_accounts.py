"""账号表：多账号解析、回落单账号、代号校验。"""

from __future__ import annotations

import json

from xxt.adapters.gxks import accounts as acc


def test_parse_multi_accounts() -> None:
    data = {
        "accounts": [
            {"label": "jia", "name": "u1", "pw": "p1", "port": 9222,
             "courses": [32, "33"], "daily_sections": 8, "daily_minutes": 480},
            {"label": "yi", "name": "u2", "pw": "p2", "port": 9223},
        ]
    }
    got = acc.parse_accounts(data)
    assert [a.label for a in got] == ["jia", "yi"]
    assert got[0].courses == (32, 33)
    assert got[0].daily_sections == 8
    assert got[1].port == 9223
    assert got[1].daily_sections == acc.DEFAULT_SECTIONS_PER_DAY
    assert all(a.configured for a in got)


def test_parse_skips_bad_entries() -> None:
    data = {"accounts": [
        {"label": "甲同学", "name": "u", "pw": "p"},      # 中文代号不合规
        {"label": "a b", "name": "u", "pw": "p"},          # 空格不合规
        {"label": "dup", "name": "u", "pw": "p", "port": 9222},
        {"label": "dup", "name": "u2", "pw": "p2", "port": 9224},   # 代号重复
        {"label": "sameport", "name": "u3", "pw": "p3", "port": 9222},  # 端口重复
        {"label": "ok", "name": "u4", "pw": "p4", "port": 9225},
    ]}
    got = acc.parse_accounts(data)
    assert [a.label for a in got] == ["dup", "ok"]


def test_placeholder_credentials_cleared() -> None:
    got = acc.parse_accounts([{"label": "x", "name": "在这里填学号", "pw": "p"}])
    assert got[0].name == ""
    assert not got[0].configured


def test_accepts_bare_list_and_single_object() -> None:
    assert [a.label for a in acc.parse_accounts([{"label": "solo"}])] == ["solo"]
    assert [a.label for a in acc.parse_accounts({"label": "one", "name": "u", "pw": "p"})] == ["one"]
    assert acc.parse_accounts("nonsense") == []


def test_load_prefers_new_table(tmp_path) -> None:
    new = tmp_path / "accounts.local.json"
    new.write_text(json.dumps({"accounts": [
        {"label": "jia", "name": "u", "pw": "p", "port": 9222}]}), encoding="utf-8")
    old = tmp_path / "account.local.json"
    old.write_text(json.dumps({"name": "legacy", "pw": "pw"}), encoding="utf-8")
    got = acc.load_accounts(new, old)
    assert [a.label for a in got] == ["jia"]


def test_load_falls_back_to_legacy(tmp_path) -> None:
    old = tmp_path / "account.local.json"
    old.write_text(json.dumps({"name": "legacy", "pw": "pw"}), encoding="utf-8")
    got = acc.load_accounts(tmp_path / "missing.json", old)
    assert len(got) == 1
    assert got[0].label == "default" and got[0].port == acc.DEFAULT_PORT


def test_load_returns_empty_when_nothing_configured(tmp_path) -> None:
    assert acc.load_accounts(tmp_path / "a.json", tmp_path / "b.json") == []


def test_summary_never_leaks_credentials() -> None:
    got = acc.parse_accounts([{"label": "jia", "name": "secretuser", "pw": "secretpw",
                              "port": 9222}])
    text = acc.summary(got)
    assert "jia" in text and "9222" in text
    assert "secretuser" not in text and "secretpw" not in text


def test_find_by_label_case_insensitive() -> None:
    got = acc.parse_accounts([{"label": "jia"}])
    assert acc.find_by_label(got, "JIA") is got[0]
    assert acc.find_by_label(got, "nope") is None
