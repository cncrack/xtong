"""`gx_ops` 操作日志表与写入/读取函数（2026-10-30 新增，schema v3）。"""

from __future__ import annotations

import sqlite3
from contextlib import closing

from xxt.store import gxks_ops
from xxt.store.schema_gxks import GXKS_SCHEMA_VERSION, migrate


def _conn(tmp_path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "bank.db")
    conn.row_factory = sqlite3.Row
    migrate(conn)
    return conn


def test_schema_version_is_three_and_has_ops_table(tmp_path):
    with closing(_conn(tmp_path)) as conn:
        ver = conn.execute(
            "SELECT value FROM meta WHERE key='gxks_schema_version'").fetchone()[0]
        assert int(ver) == GXKS_SCHEMA_VERSION == 3
        cols = {r[1] for r in conn.execute("PRAGMA table_info(gx_ops)")}
        assert {"at", "account_label", "action", "result", "detail",
                "payload_json"} <= cols


def test_migrate_is_idempotent(tmp_path):
    """重复迁移不能报错，也不能把已有记录冲掉（面板每次启动都会调）。"""
    with closing(_conn(tmp_path)) as conn:
        gxks_ops.record_op(conn, account_label="甲同学", action="study-start",
                           detail="2.4 开始学习")
        migrate(conn)
        assert len(gxks_ops.recent_ops(conn)) == 1


def test_record_and_recent_ops_roundtrip(tmp_path):
    with closing(_conn(tmp_path)) as conn:
        gxks_ops.record_op(
            conn, account_label="甲同学", action="work-fill",
            detail="2.2 已回填 3 题（题库1/AI2）",
            section_id="1299", section_name="2.2 国家安全形势",
            exercise_id="14670", at="2026-10-30 10:32:00",
            payload={"items": [{"ordinal": 1, "answer": ["B"], "source": "bank"}]},
        )
        rows = gxks_ops.recent_ops(conn)
        assert len(rows) == 1
        row = rows[0]
        assert row["at"] == "2026-10-30 10:32:00"
        assert row["result"] == "ok"                      # 默认值
        assert row["section_name"] == "2.2 国家安全形势"
        assert row["payload"]["items"][0]["answer"] == ["B"]
        assert "payload_json" not in row                  # 已解析成 payload


def test_recent_ops_filter_by_account_and_order(tmp_path):
    with closing(_conn(tmp_path)) as conn:
        gxks_ops.record_op(conn, account_label="甲同学", action="study-start",
                           detail="2.4")
        gxks_ops.record_op(conn, account_label="乙同学", action="study-start",
                           detail="1.1")
        gxks_ops.record_op(conn, account_label="甲同学", action="study-done",
                           detail="2.4 达标")
        mine = gxks_ops.recent_ops(conn, account_label="甲同学")
        assert [r["action"] for r in mine] == ["study-done", "study-start"]
        assert len(gxks_ops.recent_ops(conn, account_label="乙同学")) == 1


def test_bad_payload_json_does_not_break_read(tmp_path):
    """库里被外部写坏的行不能让面板日志整页崩。"""
    with closing(_conn(tmp_path)) as conn:
        conn.execute(
            "INSERT INTO gx_ops(at, account_label, action, result, detail,"
            " payload_json) VALUES('2026-10-30 10:00:00','甲同学','error',"
            "'fail','坏行','{not json')")
        conn.commit()
        rows = gxks_ops.recent_ops(conn)
        assert rows[0]["payload"] is None


def test_error_action_keeps_machine_code(tmp_path):
    with closing(_conn(tmp_path)) as conn:
        gxks_ops.record_op(conn, account_label="甲同学", action="error",
                           result="fail", code="structure",
                           detail="找不到答题帧（tm_arr 命中 0 个）")
        row = gxks_ops.recent_ops(conn)[0]
        assert row["result"] == "fail"
        assert row["code"] == "structure"
