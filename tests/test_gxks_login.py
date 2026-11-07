"""登录 / 凭据 / 会话相关纯函数测试（零网络、零真实凭据）。

守的是三条纪律：
1. 占位符绝不当时凭据（否则会拿“在这里填密码”去撞登录）；
2. 验证码闸门一律转人工，不猜不试；
3. 凭据文件坏掉/缺字段 → 当作未配置，不是抛异常。
"""

from __future__ import annotations

import json

from xxt.adapters.gxks import login as glogin


def _write(tmp_path, payload) -> str:
    p = tmp_path / "account.local.json"
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return str(p)


def test_load_credentials_ok(tmp_path):
    path = _write(tmp_path, {"name": "S1001", "pw": "secret-value"})
    cred = glogin.load_credentials(path)
    assert cred == {"name": "S1001", "pw": "secret-value"}
    assert glogin.is_configured(path) is True


def test_placeholder_is_not_configured(tmp_path):
    """模板文件原样保存时必须判“未配置”——拿占位符登录等于用错密码撞库。"""
    path = _write(tmp_path, {"name": "在这里填学号", "pw": "在这里填密码"})
    assert glogin.load_credentials(path) is None
    assert glogin.is_configured(path) is False


def test_missing_broken_or_incomplete_file(tmp_path):
    assert glogin.load_credentials(tmp_path / "nope.json") is None

    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert glogin.load_credentials(str(bad)) is None

    assert glogin.load_credentials(_write(tmp_path, {"name": "S1001"})) is None
    assert glogin.load_credentials(_write(tmp_path, {"name": "  ", "pw": "x"})) is None
    assert glogin.load_credentials(_write(tmp_path, ["not", "a", "dict"])) is None


def test_is_need_verify_signals():
    """两个信号任一成立即转人工：URL 带 needVerify=1，或验证码行可见。"""
    assert glogin.is_need_verify("http://x/Loginb.aspx?needVerify=1") is True
    assert glogin.is_need_verify("http://x/Loginb.aspx", verify_row_visible=True) is True
    assert glogin.is_need_verify("http://x/Loginb.aspx") is False
    assert glogin.is_need_verify("") is False


# ---------------------------------------------------------------- 按账号取凭据
#
# 2026-11-07 修 P0：多账号同时跑时，原来所有脚本都读**全局** `account.local.json`，
# 于是「B 掉登录 → 拿 A 的密码登 B」→ A 被顶号、B 还是没登上。
# 下面这些用例守的就是「谁的凭据归谁」。


def _settings(tmp_path, accounts: list[dict]):
    """造一个带账号表的 Settings（`data_dir` 落在 tmp_path/data）。"""
    import os

    from xxt.config import Settings

    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    (data / "accounts.local.json").write_text(
        json.dumps({"accounts": accounts}, ensure_ascii=False), encoding="utf-8")
    os.environ["XXT_ROOT"] = str(tmp_path)
    try:
        return Settings(root=tmp_path)
    finally:
        pass


def _acc(**kw):
    """最小 Account（不依赖 accounts 模块的重型构造）。"""
    from xxt.adapters.gxks.accounts import Account

    return Account(**kw)


def test_per_account_credentials_win(tmp_path):
    """账号表里填了就用它，**完全不看**全局文件。"""
    settings = _settings(tmp_path, [
        {"label": "jia", "port": 9222, "name": "acc-A", "pw": "pw-A"},
        {"label": "yi", "port": 9223, "name": "acc-B", "pw": "pw-B"},
    ])
    (settings.data_dir / "account.local.json").write_text(
        json.dumps({"name": "GLOBAL", "pw": "global-pw"}), encoding="utf-8")
    assert glogin.load_credentials_for(_acc(label="jia", name="acc-A", pw="pw-A"),
                                       settings) == {"name": "acc-A", "pw": "pw-A"}
    assert glogin.load_credentials_for(_acc(label="yi", name="acc-B", pw="pw-B"),
                                       settings) == {"name": "acc-B", "pw": "pw-B"}


def test_multi_account_never_falls_back_to_global(tmp_path):
    """**这是 P0 的核心**：≥2 个账号时，没填凭据的账号一律 None。

    回落到全局文件等于让它用别人的账号密码登录（顶号），所以宁可"未配置"。
    """
    settings = _settings(tmp_path, [
        {"label": "jia", "port": 9222, "name": "acc-A", "pw": "pw-A"},
        {"label": "yi", "port": 9223},
    ])
    (settings.data_dir / "account.local.json").write_text(
        json.dumps({"name": "acc-A", "pw": "pw-A"}), encoding="utf-8")
    assert glogin.load_credentials_for(_acc(label="yi"), settings) is None
    assert glogin.is_configured_for(_acc(label="yi"), settings) is False


def test_single_account_may_fall_back(tmp_path):
    """只有一个账号时允许回落（老环境：账号表没填 name/pw，凭据在旧文件里）。"""
    settings = _settings(tmp_path, [{"label": "default", "port": 9222}])
    (settings.data_dir / "account.local.json").write_text(
        json.dumps({"name": "acc-A", "pw": "pw-A"}), encoding="utf-8")
    assert glogin.load_credentials_for(_acc(label="default"), settings) == {
        "name": "acc-A", "pw": "pw-A"}


def test_fallback_never_when_settings_missing(tmp_path):
    """不给 settings 时（读不到账号表）不得回落 —— 不知道是哪个号，就不能试凭据。"""
    assert glogin.load_credentials_for(_acc(label="x")) is None


def test_placeholder_in_account_table_is_not_configured(tmp_path):
    """账号表里存着模板占位符 → 按未配置处理（绝不拿它去撞库）。"""
    settings = _settings(tmp_path, [
        {"label": "jia", "port": 9222, "name": "在这里填学号", "pw": "在这里填密码"}])
    assert glogin.load_credentials_for(_acc(label="jia", name="在这里填学号",
                                            pw="在这里填密码"), settings) is None


def test_half_filled_credentials_not_usable(tmp_path):
    """只填了账号没填密码（或反之）→ 未配置，不能拿半份去试。"""
    settings = _settings(tmp_path, [
        {"label": "jia", "port": 9222, "name": "acc-A"},
        {"label": "yi", "port": 9223, "pw": "pw-B"},
    ])
    assert glogin.load_credentials_for(_acc(label="jia", name="acc-A"), settings) is None


def test_non_string_credential_field_is_ignored(tmp_path):
    """鸭子类型/测试替身可能让 `getattr(acc, 'name')` 拿到非字符串 —— 不能当凭据。"""
    settings = _settings(tmp_path, [{"label": "jia", "port": 9222}])

    class _Fake:
        label = "jia"
        name = object()          # 任意对象，str() 出来非空
        pw = object()

    assert glogin.load_credentials_for(_Fake(), settings) is None
