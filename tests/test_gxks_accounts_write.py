"""账号表**写入**（面板增删改）的测试。

守五条（都是会真的弄坏用户账号表的路径）：

① **原子写**：先写临时文件再 `os.replace`，中途崩了不留半张表（半张表 = 账号凭空消失）；
② **端口唯一**：一账号一端口是硬约束，撞了必须报错，不能静默写进去；
③ **越界报错不静默钳制**：静默钳制会让人以为“改成功了”；
④ **凭据只认显式给值**：空字符串 = 不改（面板不回显密码，回填即泄露）；
⑤ **只改目标那一条**：其它账号（含用户手写的额外字段）原样保留。

全程 tmp_path，不碰真实 `data/`。
"""

from __future__ import annotations

import json

import pytest

from xxt.adapters.gxks import accounts as acc


@pytest.fixture(autouse=True)
def _isolate_root(tmp_path, monkeypatch):
    """把 `XXT_ROOT` 指到临时目录。

    **必须有**：`save_account` 在新表不存在时会把 legacy `account.local.json` 折进来
    （防"新增即丢号"）。不隔离的话，跑测试就会读到开发机真实的 legacy 账号，
    表现为"端口 9222 已被 default 占用"这种看不懂的失败。
    """
    monkeypatch.setenv("XXT_ROOT", str(tmp_path))
    return tmp_path


def _seed(tmp_path, rows: list[dict]) -> "object":
    p = tmp_path / "accounts.local.json"
    p.write_text(json.dumps({"accounts": rows}, ensure_ascii=False), encoding="utf-8")
    return p


def _read(p) -> list[dict]:
    return json.loads(p.read_text(encoding="utf-8"))["accounts"]


# ---------------------------------------------------------------- 新增

def test_save_creates_new_account(tmp_path) -> None:
    p = _seed(tmp_path, [{"label": "jia", "port": 9222}])
    got = acc.save_account({"label": "yi", "display": "乙同学", "port": 9223,
                            "daily_sections": 6, "daily_minutes": 400,
                            "name": "s2002", "pw": "pw-B"}, path=p)
    assert got.label == "yi" and got.port == 9223
    assert got.daily_sections == 6 and got.daily_minutes == 400
    assert got.configured is True
    rows = _read(p)
    assert [r["label"] for r in rows] == ["jia", "yi"]
    # 写出来的表必须能被 parse_accounts 解析回来（否则等于写坏）
    assert [a.label for a in acc.parse_accounts({"accounts": rows})] == ["jia", "yi"]


def test_save_creates_file_when_missing(tmp_path, monkeypatch) -> None:
    """新表不存在时也能新增（并把 XXT_ROOT 指到临时目录，避免读到真实 legacy 账号）。"""
    monkeypatch.setenv("XXT_ROOT", str(tmp_path))
    p = tmp_path / "accounts.local.json"
    acc.save_account({"label": "first", "port": 9222}, path=p)
    assert [a.label for a in acc.load_accounts(p, tmp_path / "nope.json")] == ["first"]


def test_save_rejects_bad_label(tmp_path) -> None:
    p = _seed(tmp_path, [])
    # 注意：`save_account` 先把代号 `.lower()` 再校验，所以 "A-B" 是合法的（存成 a-b）
    for bad in ("甲同学", "a b", "", "-lead", "x" * 30, "a/b"):
        with pytest.raises(acc.AccountError):
            acc.save_account({"label": bad, "port": 9222}, path=p)
    assert _read(p) == []


def test_label_is_lowercased(tmp_path) -> None:
    p = _seed(tmp_path, [])
    got = acc.save_account({"label": "JIA", "port": 9222}, path=p)
    assert got.label == "jia"
    assert _read(p)[0]["label"] == "jia"


def test_save_rejects_duplicate_port(tmp_path) -> None:
    p = _seed(tmp_path, [{"label": "jia", "port": 9222}])
    with pytest.raises(acc.AccountError) as ei:
        acc.save_account({"label": "yi", "port": 9222}, path=p)
    assert "端口 9222" in str(ei.value) and "jia" in str(ei.value)
    assert len(_read(p)) == 1                     # 没写进去


def test_save_rejects_duplicate_label(tmp_path) -> None:
    p = _seed(tmp_path, [{"label": "jia", "port": 9222}])
    with pytest.raises(acc.AccountError):
        acc.save_account({"label": "jia", "port": 9223}, path=p)


@pytest.mark.parametrize("field,value", [
    ("daily_sections", 41), ("daily_sections", -1),
    ("daily_minutes", 1441), ("daily_minutes", -5),
    ("port", 80), ("port", 70000),
])
def test_save_rejects_out_of_range_instead_of_clamping(tmp_path, field, value) -> None:
    p = _seed(tmp_path, [])
    body = {"label": "jia", "port": 9222, field: value}
    with pytest.raises(acc.AccountError):
        acc.save_account(body, path=p)
    assert _read(p) == []                         # 报错就别写


def test_zero_means_unlimited_and_is_accepted(tmp_path) -> None:
    """`0` = 不限（PLAN-GXKS §11.6 已定口径），不能被当成非法值回落到默认 10。"""
    p = _seed(tmp_path, [])
    got = acc.save_account({"label": "jia", "port": 9222,
                            "daily_sections": 0, "daily_minutes": 0}, path=p)
    assert got.daily_sections == 0 and got.daily_minutes == 0
    # 读回来也必须还是 0（原实现 `secs if secs > 0` 会把 0 悄悄改回 10）
    again = acc.load_accounts(p, tmp_path / "nope.json")[0]
    assert again.daily_sections == 0 and again.daily_minutes == 0


# ---------------------------------------------------------------- 修改

def test_edit_keeps_other_accounts_and_unknown_fields(tmp_path) -> None:
    p = _seed(tmp_path, [
        {"label": "jia", "port": 9222, "note": "用户手写的字段"},
        {"label": "yi", "port": 9223, "courses": [32]},
    ])
    acc.save_account({"label": "jia", "port": 9222, "daily_sections": 12,
                      "daily_minutes": 700}, path=p, original_label="jia")
    rows = _read(p)
    assert rows[0]["daily_sections"] == 12
    assert rows[0]["note"] == "用户手写的字段"     # 不认识的字段不能被吃掉
    assert rows[1] == {"label": "yi", "port": 9223, "courses": [32]}   # 别人一个字没动


def test_edit_missing_account_is_reported(tmp_path) -> None:
    p = _seed(tmp_path, [{"label": "jia", "port": 9222}])
    with pytest.raises(acc.AccountError):
        acc.save_account({"label": "jia", "port": 9223}, path=p, original_label="nobody")


def test_edit_port_conflicts_with_other_account(tmp_path) -> None:
    p = _seed(tmp_path, [{"label": "jia", "port": 9222},
                         {"label": "yi", "port": 9223}])
    with pytest.raises(acc.AccountError):
        acc.save_account({"label": "jia", "port": 9223}, path=p, original_label="jia")


def test_edit_keeps_own_port_when_unchanged(tmp_path) -> None:
    """改自己的其它字段时，端口跟它自己比不算重复（常见 off-by-one 陷阱）。"""
    p = _seed(tmp_path, [{"label": "jia", "port": 9222}])
    acc.save_account({"label": "jia", "port": 9222, "daily_sections": 3},
                     path=p, original_label="jia")
    assert _read(p)[0]["daily_sections"] == 3


# ---------------------------------------------------------------- 凭据

def test_empty_credentials_do_not_wipe_existing(tmp_path) -> None:
    """面板不回显密码：空字符串必须是“不改”，不能把已有凭据清掉。"""
    p = _seed(tmp_path, [{"label": "jia", "port": 9222, "name": "s1", "pw": "p1"}])
    acc.save_account({"label": "jia", "port": 9222, "name": "", "pw": "",
                      "daily_sections": 5}, path=p, original_label="jia")
    row = _read(p)[0]
    assert row["name"] == "s1" and row["pw"] == "p1"
    assert row["daily_sections"] == 5


def test_explicit_credentials_overwrite(tmp_path) -> None:
    p = _seed(tmp_path, [{"label": "jia", "port": 9222, "name": "s1", "pw": "p1"}])
    acc.save_account({"label": "jia", "port": 9222, "name": "s2", "pw": "p2"},
                     path=p, original_label="jia")
    row = _read(p)[0]
    assert row["name"] == "s2" and row["pw"] == "p2"


def test_clear_credentials_flag(tmp_path) -> None:
    p = _seed(tmp_path, [{"label": "jia", "port": 9222, "name": "s1", "pw": "p1"}])
    got = acc.save_account({"label": "jia", "port": 9222, "clear_credentials": True},
                           path=p, original_label="jia")
    assert got.configured is False
    assert "pw" not in _read(p)[0]


def test_saved_table_never_contains_placeholder_credentials(tmp_path) -> None:
    p = _seed(tmp_path, [])
    got = acc.save_account({"label": "jia", "port": 9222,
                            "name": "在这里填学号", "pw": "p"}, path=p)
    assert got.configured is False                # 占位符照样按“未配置”处理


# ---------------------------------------------------------------- 课程 / 启停 / 删除

def test_courses_accept_comma_string_and_empty_clears(tmp_path) -> None:
    p = _seed(tmp_path, [{"label": "jia", "port": 9222}])
    acc.save_account({"label": "jia", "port": 9222, "courses": "32, 33"},
                     path=p, original_label="jia")
    assert _read(p)[0]["courses"] == [32, 33]
    acc.save_account({"label": "jia", "port": 9222, "courses": ""},
                     path=p, original_label="jia")
    assert "courses" not in _read(p)[0]           # 空 = 跑该账号能看到的全部课程


def test_edit_without_courses_field_keeps_them(tmp_path) -> None:
    """只改额度的编辑不能把 `courses: [32]` 悄悄清成“跑全部课程”（多跑课 = 多耗额度）。"""
    p = _seed(tmp_path, [{"label": "jia", "port": 9222, "courses": [32]}])
    acc.save_account({"label": "jia", "port": 9222, "daily_sections": 6},
                     path=p, original_label="jia")
    assert _read(p)[0]["courses"] == [32]


def test_edit_without_display_field_keeps_it(tmp_path) -> None:
    """只改端口的编辑不能把显示名洗成空。"""
    p = _seed(tmp_path, [{"label": "jia", "port": 9222, "display": "甲同学"}])
    got = acc.save_account({"label": "jia", "port": 9225}, path=p, original_label="jia")
    assert got.display == "甲同学"
    # 显式传空才清空
    acc.save_account({"label": "jia", "port": 9225, "display": ""},
                     path=p, original_label="jia")
    assert _read(p)[0]["display"] == ""


def test_edit_without_enabled_field_keeps_it(tmp_path) -> None:
    """编辑表单没带 `enabled` 时，不能把用户停用的账号悄悄重新启用。"""
    p = _seed(tmp_path, [{"label": "jia", "port": 9222, "enabled": False}])
    got = acc.save_account({"label": "jia", "port": 9222, "daily_sections": 4},
                           path=p, original_label="jia")
    assert got.enabled is False
    acc.save_account({"label": "jia", "port": 9222, "enabled": True},
                     path=p, original_label="jia")
    assert _read(p)[0]["enabled"] is True


def test_edit_without_quota_fields_keeps_existing_values(tmp_path) -> None:
    """只改端口的编辑不能把用户设的额度悄悄重置回默认 10/600。"""
    p = _seed(tmp_path, [{"label": "jia", "port": 9222,
                          "daily_sections": 3, "daily_minutes": 180}])
    got = acc.save_account({"label": "jia", "port": 9225}, path=p, original_label="jia")
    assert got.port == 9225
    assert got.daily_sections == 3 and got.daily_minutes == 180


def test_toggle_enabled_only_touches_that_flag(tmp_path) -> None:
    p = _seed(tmp_path, [{"label": "jia", "port": 9222, "daily_sections": 7}])
    assert acc.set_enabled("jia", False, path=p) is True
    row = _read(p)[0]
    assert row["enabled"] is False and row["daily_sections"] == 7
    assert acc.set_enabled("nobody", True, path=p) is False


def test_delete_removes_only_target(tmp_path) -> None:
    p = _seed(tmp_path, [{"label": "jia", "port": 9222},
                         {"label": "yi", "port": 9223}])
    assert acc.delete_account("jia", path=p) is True
    assert [r["label"] for r in _read(p)] == ["yi"]
    assert acc.delete_account("jia", path=p) is False


def test_write_is_atomic_leaves_no_tmp(tmp_path) -> None:
    p = _seed(tmp_path, [{"label": "jia", "port": 9222}])
    acc.save_account({"label": "yi", "port": 9223}, path=p)
    assert not (tmp_path / "accounts.local.json.tmp").exists()
    json.loads(p.read_text(encoding="utf-8"))     # 文件必须是完整 JSON


def test_unknown_top_level_keys_survive_a_write(tmp_path) -> None:
    """只替换 `accounts`，别把用户写在顶层的备注顺手丢掉。"""
    p = tmp_path / "accounts.local.json"
    p.write_text(json.dumps({"备注": "自己看的", "accounts": [
        {"label": "jia", "port": 9222}]}, ensure_ascii=False), encoding="utf-8")
    acc.save_account({"label": "yi", "port": 9223}, path=p)
    doc = json.loads(p.read_text(encoding="utf-8"))
    assert doc["备注"] == "自己看的"
    assert [r["label"] for r in doc["accounts"]] == ["jia", "yi"]


def test_broken_file_is_treated_as_empty_and_overwritten(tmp_path) -> None:
    """坏文件（半截 JSON）不能让人永远存不进去：按空表处理并覆盖，同时不崩。"""
    p = tmp_path / "accounts.local.json"
    p.write_text("{ 这不是 JSON", encoding="utf-8")
    acc.save_account({"label": "jia", "port": 9222}, path=p)
    assert [r["label"] for r in _read(p)] == ["jia"]


# ---------------------------------------------------------------- legacy 迁移（实测踩过的坑）
# 2026-10-30 真实故障：`data/accounts.local.json` 不存在 → 面板靠 legacy 回落显示 default 账号。
# 在面板上「新增账号」时以空表为底建新文件 → **legacy 里那个真实账号从面板消失**，
# 而且后续端口冲突检测也查不到它（它已经不在表里了）。

def _seed_legacy(tmp_path, name: str = "2602220131", pw: str = "000000") -> "object":
    p = tmp_path / "account.local.json"
    p.write_text(json.dumps({"name": name, "pw": pw}), encoding="utf-8")
    return p


def test_new_table_starts_from_legacy_account(tmp_path, monkeypatch) -> None:
    """新表还不存在时新增账号，legacy 里那个账号必须被带进来（不能“新增即丢号”）。"""
    monkeypatch.setenv("XXT_ROOT", str(tmp_path))
    (tmp_path / "data").mkdir()
    _seed_legacy(tmp_path / "data")
    new = tmp_path / "data" / "accounts.local.json"
    assert not new.exists()

    acc.save_account({"label": "yi", "port": 9223}, path=new)

    labels = [r["label"] for r in _read(new)]
    assert labels == ["default", "yi"]            # legacy 的账号还在
    rows = _read(new)
    assert rows[0]["port"] == acc.DEFAULT_PORT
    assert rows[0]["name"] == "2602220131"        # 凭据也带过来了


def test_new_account_cannot_steal_legacy_port(tmp_path, monkeypatch) -> None:
    """端口冲突检测必须看得见 legacy 账号（实测那次冲突漏检就是这个原因）。"""
    monkeypatch.setenv("XXT_ROOT", str(tmp_path))
    (tmp_path / "data").mkdir()
    _seed_legacy(tmp_path / "data")
    new = tmp_path / "data" / "accounts.local.json"
    with pytest.raises(acc.AccountError) as ei:
        acc.save_account({"label": "dup", "port": acc.DEFAULT_PORT}, path=new)
    assert "default" in str(ei.value)
    assert not new.exists()                       # 报错就别建文件


def test_delete_can_remove_legacy_account(tmp_path, monkeypatch) -> None:
    """legacy 账号也要能删掉，否则面板上那个「删除」按钮点了没反应。"""
    monkeypatch.setenv("XXT_ROOT", str(tmp_path))
    (tmp_path / "data").mkdir()
    _seed_legacy(tmp_path / "data")
    new = tmp_path / "data" / "accounts.local.json"
    assert acc.delete_account("default", path=new) is True
    assert _read(new) == []


def test_existing_table_does_not_resurrect_legacy(tmp_path, monkeypatch) -> None:
    """新表一旦存在，就**不再**把 legacy 折进来——否则用户删掉的账号会自己回来。"""
    monkeypatch.setenv("XXT_ROOT", str(tmp_path))
    (tmp_path / "data").mkdir()
    _seed_legacy(tmp_path / "data")
    new = tmp_path / "data" / "accounts.local.json"
    new.write_text(json.dumps({"accounts": [{"label": "yi", "port": 9223}]}),
                   encoding="utf-8")
    assert [r["label"] for r in acc._read_raw_with_legacy(new)] == ["yi"]
