"""新站题库独立迁移；不调用超星建表/补列，不修改共享版本号。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

GXKS_SCHEMA_VERSION = 3
# 1 → 2（2026-10-30）：仅新增 `gx_q_items.fingerprint` 索引。
#   跨节同题（题 ID 不同、内容相同）靠 fingerprint 归并，导出时不必全表扫。
# 2 → 3（2026-10-30）：新增 `gx_ops` 操作日志表。面板要“记录操作记录”
#   （用户原话），而超星的 `run_logs` 是账号 + 章节维度、绑 `accounts` 表，
#   新站账号用 `account_label`，两边表结构不同，因此单独建表而不是复用。
#   建表语句全部 IF NOT EXISTS，旧库（版本 1/2）可原地升到 3，不重建数据。

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS gx_q_types (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS gx_q_items (
    id INTEGER PRIMARY KEY,
    platform_qid TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    type_code TEXT NOT NULL REFERENCES gx_q_types(code),
    stem TEXT NOT NULL,
    stem_html TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    UNIQUE(platform_qid, fingerprint)
);
CREATE TABLE IF NOT EXISTS gx_q_parts (
    item_id INTEGER NOT NULL REFERENCES gx_q_items(id),
    ordinal INTEGER NOT NULL,
    kind TEXT NOT NULL,
    part_key TEXT NOT NULL,
    content_json TEXT NOT NULL,
    PRIMARY KEY(item_id, ordinal)
);
CREATE TABLE IF NOT EXISTS gx_q_papers (
    id INTEGER PRIMARY KEY,
    account_label TEXT NOT NULL,
    exam_start_id TEXT NOT NULL,
    exercise_id TEXT NOT NULL,
    course_id TEXT NOT NULL,
    section_id TEXT NOT NULL,
    section_name TEXT NOT NULL,
    paper_label TEXT NOT NULL,
    start_time TEXT NOT NULL,
    UNIQUE(account_label, exam_start_id)
);
CREATE TABLE IF NOT EXISTS gx_q_paper_items (
    paper_id INTEGER NOT NULL REFERENCES gx_q_papers(id),
    ordinal INTEGER NOT NULL,
    item_id INTEGER NOT NULL REFERENCES gx_q_items(id),
    max_score REAL,
    PRIMARY KEY(paper_id, ordinal)
);
CREATE TABLE IF NOT EXISTS gx_q_imports (
    id INTEGER PRIMARY KEY,
    paper_id INTEGER NOT NULL REFERENCES gx_q_papers(id),
    digest TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    UNIQUE(paper_id, digest)
);
CREATE TABLE IF NOT EXISTS gx_q_answers (
    import_id INTEGER NOT NULL REFERENCES gx_q_imports(id),
    ordinal INTEGER NOT NULL,
    picked_json TEXT,
    picked_source TEXT,
    standard_json TEXT,
    standard_source TEXT,
    score_got REAL,
    is_correct INTEGER,
    explanation_html TEXT NOT NULL,
    PRIMARY KEY(import_id, ordinal)
);
CREATE INDEX IF NOT EXISTS gx_q_papers_classify
    ON gx_q_papers(account_label, course_id, section_id, exercise_id);
CREATE INDEX IF NOT EXISTS gx_q_items_classify ON gx_q_items(type_code);
CREATE INDEX IF NOT EXISTS gx_q_items_fingerprint ON gx_q_items(fingerprint);
CREATE TABLE IF NOT EXISTS gx_ops (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    account_label TEXT NOT NULL,
    action TEXT NOT NULL,
    result TEXT NOT NULL,
    section_id TEXT,
    section_name TEXT,
    exercise_id TEXT,
    detail TEXT NOT NULL,
    code TEXT,
    payload_json TEXT
);
CREATE INDEX IF NOT EXISTS gx_ops_by_account ON gx_ops(account_label, id);
CREATE INDEX IF NOT EXISTS gx_ops_by_action ON gx_ops(action, id);
"""


def migrate(conn: sqlite3.Connection) -> None:
    """仅由新站入口显式调用；不降级未来版本，不提交调用者的未完成事务。"""
    if conn.in_transaction:
        raise ValueError("迁移前连接不能存在未提交事务")
    has_meta = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'"
    ).fetchone()
    if has_meta:
        row = conn.execute("SELECT value FROM meta WHERE key='gxks_schema_version'").fetchone()
        if row and int(row[0]) > GXKS_SCHEMA_VERSION:
            raise ValueError("题库版本高于当前程序，不能降级打开")
    conn.execute("PRAGMA foreign_keys=ON")
    # 拆分固定 SQL，在一个事务内建表与更新版本；不使用隐式提交的 executescript。
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        for statement in _SCHEMA.split(";"):
            if statement.strip():
                conn.execute(statement)
        conn.execute(
            "INSERT INTO meta(key,value) VALUES('gxks_schema_version',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(GXKS_SCHEMA_VERSION),),
        )


def open_question_bank(path: Path) -> sqlite3.Connection:
    """打开专用题库；拒绝已有超星业务库，防止路径选错污染旧库。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='accounts'"
        ).fetchone():
            raise ValueError("请选择新站专用题库文件，不能直接使用既有账号业务库")
        migrate(conn)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn
    except Exception:
        conn.close()
        raise
