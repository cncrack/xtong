"""记账对账（reconcile）：本地章节完成状态以平台标识为准。

覆盖两层：
1. `xxt.cli.CHAPTERS_UPSERT`——入库语句只管建行与元数据，**不碰已有行的 status**
   （状态对齐统一由 core.reconcile 一处做，避免两处各写一套规则）。
2. `xxt.core.reconcile.reconcile_local`——升级无条件、降级须显式开启且过两道护栏。

背景：旧写回只更 name/idx，平台"已完成"同步不进来，本地长期挂着虚假"待学"
（实测账号#2《知行合一》平台 70/70 已完成、本地仍记 29 节待学），调度因而白跑真实页面。
"""

from __future__ import annotations

import types

from xxt.cli import CHAPTERS_UPSERT
from xxt.core.reconcile import guardrails, pre_session_enabled, reconcile_local
from xxt.store.db import connect

COURSE_ID = 1


def _db(tmp_path):
    conn = connect(tmp_path / "x.db")
    conn.execute(
        "INSERT INTO courses(id, account_id, platform_course_id, clazz_id, name, url)"
        " VALUES(?, 1, '266339100', '9', '示例课程', 'https://example.invalid/c')"
        " ON CONFLICT(id) DO NOTHING",
        (COURSE_ID,),
    )
    conn.commit()
    return conn


def _insert(conn, kid: str, name: str, idx: int, done_on_platform: bool):
    """走真实的入库语句：模拟 `xxt chapters` 抓到一个节点。"""
    conn.execute(CHAPTERS_UPSERT, (COURSE_ID, kid, name, idx,
                                   "done" if done_on_platform else "pending"))
    conn.commit()


def _status(conn, kid: str) -> str:
    return conn.execute(
        "SELECT status FROM chapters WHERE course_id=? AND platform_chapter_id=?",
        (COURSE_ID, kid),
    ).fetchone()["status"]


def _leaf(kid: str, done: bool, idx: int = 0) -> dict:
    return {"platform_chapter_id": kid, "name": f"节{kid}", "idx": idx,
            "done_on_platform": done, "job_count": 0}


# ---------------------------------------------------------------- 入库语句职责

def test_upsert_new_row_takes_platform_fact(tmp_path):
    """本地没有的节：按平台当次事实建行。"""
    conn = _db(tmp_path)
    _insert(conn, "1001", "第一章｜1.1", 0, True)
    assert _status(conn, "1001") == "done"
    _insert(conn, "1002", "第一章｜1.2", 1, False)
    assert _status(conn, "1002") == "pending"


def test_upsert_keeps_existing_status_and_updates_meta(tmp_path):
    """已存在的行：入库语句绝不改 status（状态归 reconcile_local 一处管），只改名称序号。"""
    conn = _db(tmp_path)
    _insert(conn, "1003", "旧名", 5, True)
    _insert(conn, "1003", "新名", 2, False)
    row = conn.execute(
        "SELECT name, idx, status FROM chapters WHERE course_id=? AND platform_chapter_id=?",
        (COURSE_ID, "1003")).fetchone()
    assert (row["name"], row["idx"], row["status"]) == ("新名", 2, "done")


# ---------------------------------------------------------------- 升级（无条件）

def test_reconcile_promotes_pending_to_done(tmp_path):
    """平台标已完成、本地记待学 → 归位为 done（本次要解决的核心缺口）。"""
    conn = _db(tmp_path)
    _insert(conn, "2001", "第二章｜2.1", 0, False)
    res = reconcile_local(conn, COURSE_ID, [_leaf("2001", True)],
                          account_id=1, course_name="示例课程")
    assert res["promoted"] == ["2001"]
    assert _status(conn, "2001") == "done"


def test_reconcile_promotes_visited_marker_too(tmp_path):
    """visited（非视频节的收尾标记）同样按平台事实归位——账号#2 那 29 节差异就出在这。"""
    conn = _db(tmp_path)
    _insert(conn, "2002", "第二章｜2.2", 1, False)
    conn.execute("UPDATE chapters SET status='visited' WHERE course_id=? AND platform_chapter_id='2002'",
                 (COURSE_ID,))
    conn.commit()
    res = reconcile_local(conn, COURSE_ID, [_leaf("2002", True)])
    assert res["promoted"] == ["2002"] and _status(conn, "2002") == "done"


def test_reconcile_writes_audit_event(tmp_path):
    """有变化必须留痕（run_logs 机器码 reconcile），否则事后无从知道状态被谁改过。"""
    conn = _db(tmp_path)
    _insert(conn, "2003", "第二章｜2.3", 0, False)
    reconcile_local(conn, COURSE_ID, [_leaf("2003", True)], account_id=2,
                    course_name="示例课程")
    row = conn.execute(
        "SELECT event, detail, account_id FROM run_logs WHERE event='reconcile'"
        " ORDER BY id DESC LIMIT 1").fetchone()
    assert row is not None and row["account_id"] == 2
    assert "升级 1 节" in row["detail"] and "示例课程" in row["detail"]


def test_reconcile_no_change_writes_no_event(tmp_path):
    """完全一致时不刷日志，避免每次会话都堆一条无信息量记录。"""
    conn = _db(tmp_path)
    _insert(conn, "2004", "第二章｜2.4", 0, True)
    res = reconcile_local(conn, COURSE_ID, [_leaf("2004", True)])
    assert res["promoted"] == [] and res["demote_blocked"] is None
    assert conn.execute("SELECT COUNT(*) n FROM run_logs WHERE event='reconcile'").fetchone()["n"] == 0


# ---------------------------------------------------------------- 降级（须显式 + 护栏）

def test_demote_skipped_by_default(tmp_path):
    """默认不降级：平台标未完成、本地记已完成时只报警，不动库。"""
    conn = _db(tmp_path)
    _insert(conn, "3001", "第三章｜3.1", 0, True)
    res = reconcile_local(conn, COURSE_ID, [_leaf("3001", False)], course_name="示例课程")
    assert _status(conn, "3001") == "done"
    assert res["demote_candidates"] == ["3001"] and res["demoted"] == []
    assert res["demote_blocked"] and "未开降级" in res["demote_blocked"]


def test_demote_applies_when_authorized(tmp_path):
    """显式授权（面板开关或 --demote）后，按平台标识打回待学——防“虚标完成静默漏刷”。

    样本用 10 节只降级此中 1 节（10%）：护栏二默认上限 20%，单节课降级 1/1 会撞
    上限被判成“图标未渲染”——那是真保护，不是缺陷。
    """
    conn = _db(tmp_path)
    leaves = []
    for i in range(10):
        _insert(conn, f"310{i}", f"第三章｜3.{i}", i, True)
        leaves.append(_leaf(f"310{i}", i != 0, i))   # 只 3100 平台标未完成，其余已完成
    res = reconcile_local(conn, COURSE_ID, leaves, allow_demote=True)
    assert res["demoted"] == ["3100"] and _status(conn, "3100") == "pending"
    assert _status(conn, "3101") == "done"


def test_demote_blocked_when_coverage_too_low(tmp_path):
    """护栏一：只抓到半棵树就禁止降级（否则会把没抓到的节全部误判为未完成）。"""
    conn = _db(tmp_path)
    for i in range(10):
        _insert(conn, f"400{i}", f"第四章｜4.{i}", i, True)
    res = reconcile_local(conn, COURSE_ID,
                          [_leaf("4000", False, 0), _leaf("4001", False, 1)],
                          allow_demote=True, min_coverage_pct=90)
    assert res["demoted"] == [] and _status(conn, "4000") == "done"
    assert "覆盖度下限" in (res["demote_blocked"] or "")


def test_demote_blocked_when_ratio_too_high(tmp_path):
    """护栏二：一次要打的节数占比过高 → 视为图标未渲染/页面改版，只报警。"""
    conn = _db(tmp_path)
    leaves = []
    for i in range(10):
        _insert(conn, f"500{i}", f"第五章｜5.{i}", i, True)
        leaves.append(_leaf(f"500{i}", False, i))
    res = reconcile_local(conn, COURSE_ID, leaves, allow_demote=True,
                          max_demote_ratio_pct=20)
    assert res["demoted"] == [] and _status(conn, "5000") == "done"
    assert "页面改版" in (res["demote_blocked"] or "")


def test_empty_tree_never_touches_local(tmp_path):
    """解析结果为空（页面没加载出来）时绝不做任何对齐——这条保护直接挡住"整门课被打回"。"""
    conn = _db(tmp_path)
    _insert(conn, "6001", "第六章｜6.1", 0, True)
    res = reconcile_local(conn, COURSE_ID, [], allow_demote=True, course_name="示例课程")
    assert _status(conn, "6001") == "done"
    assert res["promoted"] == [] and res["demoted"] == []
    assert "解析结果为空" in (res["demote_blocked"] or "")


# ---------------------------------------------------------------- 配置作用范围

def test_guardrails_read_from_config():
    """护栏参数只从 [reconcile] 读一处，且 --demote 这类一次性授权可覆盖配置。"""
    fake = types.SimpleNamespace(reconcile={
        "pre_session": False, "demote": False,
        "demote_min_coverage_pct": 95, "demote_max_ratio_pct": 10})
    assert guardrails(fake) == {"allow_demote": False, "min_coverage_pct": 95,
                                "max_demote_ratio_pct": 10}
    assert guardrails(fake, allow_demote=True)["allow_demote"] is True
    assert pre_session_enabled(fake) is False


def test_reconcile_defaults_are_conservative():
    """内置默认：刷前对账开、降级关——与"默认不动库、要动须显式授权"的口径一致。"""
    from xxt.config import DEFAULTS

    rec = DEFAULTS["reconcile"]
    assert rec["pre_session"] is True and rec["demote"] is False
    assert rec["demote_min_coverage_pct"] == 90 and rec["demote_max_ratio_pct"] == 20
    # 额度浮动默认抬高下限：仍随机，但每天至少刷到额度的 85%
    assert DEFAULTS["quota"]["float_ratio"] == [0.85, 1.0]
