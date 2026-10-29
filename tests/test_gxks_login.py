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
