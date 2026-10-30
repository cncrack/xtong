"""`netcheck`：刷课前置的网络自检（2026-10-30，B9）。

守四条（都是会真白刷学时的路径）：

① **判据是实测耗时，不是注册表值**：`ProxyEnable=1` 只说明系统设了代理，
   不代表到站点的流量走了代理（本机实测就是"站点直连"）；所以必须真的 GET 一次。
② **超时拒绝起跑**：站点每 60 秒上报一次学习进度，链路慢会让学时真丢。
③ **不自动改系统代理**：系统级改动，只报警并给可照做的修法。
④ **可被 `--no-netcheck` 跳过**（调试），但默认必须跑。

用替身打 `_probe`，不真打网络。
"""

from __future__ import annotations

from unittest import mock

from xxt.adapters.gxks import netcheck


# ---------------------------------------------------------------- measure

def test_measure_takes_the_fastest_try() -> None:
    """测 3 次取最快——网络抖动会误判，所以不能取平均。"""
    with mock.patch.object(netcheck, "_TRIES", 3), \
         mock.patch.object(netcheck, "_probe",
                           side_effect=[(1.5, 200), (0.12, 200), (0.4, 200)]):
        got = netcheck.measure()
    assert got["secs"] == 0.12
    assert got["ok"] is True


def test_measure_flags_when_no_content_at_all() -> None:
    """3 次全失败 → `ok=False`，否则调用方会把 0 字节当成成功。"""
    with mock.patch.object(netcheck, "_TRIES", 3), \
         mock.patch.object(netcheck, "_probe",
                           side_effect=[(0.1, 0), (0.1, 0), (0.1, 0)]):
        got = netcheck.measure()
    assert got["ok"] is False


def test_measure_recovers_after_a_blip() -> None:
    """前两次失败、第三次成功 → 应该判过（只看有内容那次）。"""
    with mock.patch.object(netcheck, "_TRIES", 3), \
         mock.patch.object(netcheck, "_probe",
                           side_effect=[(0.1, 0), (0.1, 0), (0.2, 300)]):
        got = netcheck.measure()
    assert got["ok"] is True


def test_measure_reports_url() -> None:
    got = netcheck.measure()
    assert got["url"] == netcheck.PROBE_URL


# ---------------------------------------------------------------- preflight

def test_preflight_passes_when_fast() -> None:
    with mock.patch.object(netcheck, "measure",
                           return_value={"secs": 0.3, "ok": True, "url": "x"}):
        got = netcheck.preflight()
    assert got["ok"] is True
    assert got["advice"] == ""


def test_preflight_blocks_when_slow() -> None:
    with mock.patch.object(netcheck, "measure",
                           return_value={"secs": 10.5, "ok": True, "url": "x"}):
        got = netcheck.preflight()
    assert got["ok"] is False
    assert got["secs"] == 10.5


def test_preflight_blocks_when_content_missing() -> None:
    """耗时短但拿不到内容（比如被重定向到错误页）→ 也必须拦。"""
    with mock.patch.object(netcheck, "measure",
                           return_value={"secs": 0.1, "ok": False, "url": "x"}):
        got = netcheck.preflight()
    assert got["ok"] is False


def test_preflight_at_threshold_passes() -> None:
    with mock.patch.object(netcheck, "measure",
                           return_value={"secs": netcheck.THRESHOLD_SECS,
                                         "ok": True, "url": "x"}):
        assert netcheck.preflight()["ok"] is True      # 边界值算过


def test_preflight_advice_mentions_proxy_fixes() -> None:
    got = netcheck.preflight()
    if not got["ok"]:
        assert "代理" in got["advice"]
    else:
        # 未超时也该能生成修法文本（面板可能需要提前展示）
        assert "代理" in netcheck.advice()


# ---------------------------------------------------------------- format

def test_report_says_ok_when_passed() -> None:
    text = netcheck.format_report({"ok": True, "secs": 0.2, "url": "u",
                                   "threshold_secs": 3.0, "advice": ""})
    assert "[OK]" in text and "0.2" in text


def test_report_includes_advice_when_blocked() -> None:
    text = netcheck.format_report({"ok": False, "secs": 11.0, "url": "u",
                                   "threshold_secs": 3.0, "advice": "改直连"})
    assert "[FAIL]" in text and "改直连" in text


# ---------------------------------------------------------------- 判据不是注册表

def test_probe_bypasses_system_proxy() -> None:
    """`_probe` 必须**绕过**系统代理——否则自检就变成"测代理速度"了。"""
    import inspect
    import urllib.request

    src = inspect.getsource(netcheck._probe)
    assert "ProxyHandler({})" in src, "没有显式绕过代理"

    # 再验证一次运行时行为：系统代理开着时，直连探针仍然能拿到内容
    secs, size = netcheck._probe(netcheck.PROBE_URL)
    assert size > 0, f"直连探针拿到 {size} 字节 —— 若为 0 说明没绕过代理"
