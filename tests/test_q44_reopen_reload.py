"""Q44（2026-10-28）：配置热重载 + 终态重算。

用户拍板的两条语义：
  1. 跑着的会话参数不变（停掉再启才用新值），但**调度器**要能读到新配置；
  2. 纸条（终态）必须服从当前配置——没达到要求就有权继续跑，除非人工停。
"""

import sqlite3

from xxt.cli import (_db_readable, _prio, _reopen_verdict, _sched_sig,
                     _terminal_kind)


def test_terminal_kind_replay():
    """回放事件 → 认出是哪张纸条；重新确认开跑后不再是终态。"""
    assert _terminal_kind([("t1", "auto-try"), ("t2", "auto-done")]) == "done"
    assert _terminal_kind([("t1", "auto-fire"), ("t2", "auto-hardstop")]) == "hardstop"
    # 硬停后又回读确认在跑 → 纸条失效
    assert _terminal_kind([("t1", "auto-hardstop"), ("t2", "auto-fire")]) == ""
    # 从没写过终态
    assert _terminal_kind([("t1", "auto-try"), ("t2", "auto-suspend")]) == ""


def test_reopen_when_quota_raised():
    """额度调大、今天还没达成 → 解除，允许继续跑。"""
    ok, why = _reopen_verdict(
        kind="done", n_try=1, cap=4, in_skipped=False, running=False,
        reached=False, facts="2 个 / 38 分钟（额度下限 30 个 / 60 分钟）")
    assert ok is True
    assert "还没达到" in why


def test_keep_when_still_reached():
    """额度改小（或没改）：今天已播够了 → 纸条继续贴着，不再白跑一次。"""
    ok, why = _reopen_verdict(
        kind="done", n_try=1, cap=4, in_skipped=False, running=False,
        reached=True, facts="3 个 / 60 分钟（额度下限 2 个 / 30 分钟）")
    assert ok is False
    assert "仍然达标" in why


def test_keep_when_running():
    """会话正在跑 → 绝不开第二个（跑着的会话按启动时参数跑到结束）。"""
    ok, why = _reopen_verdict(
        kind="done", n_try=1, cap=4, in_skipped=False, running=True,
        reached=False, facts="")
    assert ok is False
    assert "正在跑" in why


def test_keep_when_tries_exhausted():
    """次数已用尽 → 额度调大也不解除（除非把「单日硬上限」也调大）。"""
    ok, why = _reopen_verdict(
        kind="hardstop", n_try=4, cap=4, in_skipped=False, running=False,
        reached=False, facts="")
    assert ok is False
    assert "单日硬上限 4" in why
    # 上限调大后同一个号就放行了
    ok2, _ = _reopen_verdict(
        kind="hardstop", n_try=4, cap=10, in_skipped=False, running=False,
        reached=False, facts="")
    assert ok2 is True


def test_keep_when_manual_stopped():
    """今天被手动停过 → 今日让位，即使额度没达成也不自动补。"""
    ok, why = _reopen_verdict(
        kind="done", n_try=2, cap=4, in_skipped=True, running=False,
        reached=False, facts="")
    assert ok is False
    assert "手动停" in why


def test_sched_sig_detects_change():
    """配置指纹：只有调度相关三段参与，改一个值就能被认出来。"""
    a = {"auto": {"hard_tries_cap": 4}, "quota": {"daily_tasks": 3},
         "pace": {"daily_window": ["08:00", "23:00"]},
         "llm": {"model": "x"}}
    b = {"auto": {"hard_tries_cap": 10}, "quota": {"daily_tasks": 3},
         "pace": {"daily_window": ["08:00", "23:00"]},
         "llm": {"model": "y"}}
    sa, sb = _sched_sig(a), _sched_sig(b)
    assert sa["auto.hard_tries_cap"] != sb["auto.hard_tries_cap"]
    assert sa["quota.daily_tasks"] == sb["quota.daily_tasks"]
    assert "llm.model" not in sa          # 换 AI 模型不该触发“调度配置已重载”


def test_db_readable(tmp_path):
    """热重载保护①：库读不了就整轮跳过，免得分额被回退成内置默认值。"""
    missing = tmp_path / "nope.db"
    assert _db_readable(missing) is False

    good = tmp_path / "ok.db"
    conn = sqlite3.connect(good)
    try:
        conn.execute("CREATE TABLE app_config(key TEXT PRIMARY KEY, value TEXT)")
        conn.commit()
    finally:
        conn.close()
    assert _db_readable(good) is True


def test_prio_parse_and_order():
    """Q43 优先级：数字大的先跑；空/非法按 1.0，不抛异常。"""
    assert _prio(None) == 1.0
    assert _prio("") == 1.0
    assert _prio("oops") == 1.0
    assert _prio(2.5) == 2.5
    rows = [{"id": 1, "priority": 1.0}, {"id": 2, "priority": 3.0},
            {"id": 3, "priority": None}]
    rows.sort(key=lambda r: (-_prio(r["priority"]), r["id"]))
    assert [r["id"] for r in rows] == [2, 1, 3]
