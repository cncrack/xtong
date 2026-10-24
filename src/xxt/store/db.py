"""SQLite 存储层。P0 单用户单库，P1 在此基础上扩展账号维度。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 4

# v4：accounts.platform_username（平台昵称，探测时自动抓取）；run_logs.account_id（日志归属账号）
_ACCOUNT_COLS = {
    "username_enc": "BLOB",
    "password_enc": "BLOB",
    "window_start": "TEXT",
    "window_end": "TEXT",
    "start_at": "TEXT",
    "daily_max_minutes": "INTEGER",
    "daily_max_tasks": "INTEGER",
    "proxy": "TEXT",
    "priority": "REAL DEFAULT 1.0",
    "platform_username": "TEXT",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS accounts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    label      TEXT NOT NULL DEFAULT 'default',
    status     TEXT NOT NULL DEFAULT 'active',   -- active | paused | banned
    created_at TEXT NOT NULL,
    username_enc BLOB,
    password_enc BLOB,
    window_start TEXT,
    window_end   TEXT,
    start_at     TEXT,
    daily_max_minutes INTEGER,
    daily_max_tasks  INTEGER,
    proxy        TEXT,
    priority     REAL DEFAULT 1.0,
    platform_username TEXT
);
CREATE TABLE IF NOT EXISTS courses (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id         INTEGER NOT NULL REFERENCES accounts(id),
    platform_course_id TEXT,
    clazz_id           TEXT,
    name               TEXT NOT NULL,
    url                TEXT,
    UNIQUE (account_id, platform_course_id)
);
CREATE TABLE IF NOT EXISTS chapters (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id           INTEGER NOT NULL REFERENCES courses(id),
    platform_chapter_id TEXT,
    name                TEXT NOT NULL,
    idx                 INTEGER NOT NULL DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'pending',  -- pending | learning | done | failed
    UNIQUE (course_id, platform_chapter_id)
);
CREATE TABLE IF NOT EXISTS questions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    question_hash TEXT NOT NULL UNIQUE,
    stem          TEXT NOT NULL,
    type          TEXT NOT NULL,      -- single | multi | judge | fill
    options       TEXT,               -- JSON 数组
    answer        TEXT,               -- JSON：已验证的正确答案
    correct_count INTEGER NOT NULL DEFAULT 0,
    wrong_count   INTEGER NOT NULL DEFAULT 0,
    updated_at    TEXT
);
CREATE TABLE IF NOT EXISTS run_logs (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts     TEXT NOT NULL,
    level  TEXT NOT NULL,
    event  TEXT NOT NULL,
    detail TEXT,
    account_id INTEGER DEFAULT 1
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path: Path) -> sqlite3.Connection:
    """打开（必要时创建）数据库并确保 schema 就绪。"""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    # 轻量迁移：老库补列
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(courses)")}
    if "clazz_id" not in cols:
        conn.execute("ALTER TABLE courses ADD COLUMN clazz_id TEXT")
    acols = {r["name"] for r in conn.execute("PRAGMA table_info(accounts)")}
    for name, decl in _ACCOUNT_COLS.items():
        if name not in acols:
            conn.execute(f"ALTER TABLE accounts ADD COLUMN {name} {decl}")
    lcols = {r["name"] for r in conn.execute("PRAGMA table_info(run_logs)")}
    if "account_id" not in lcols:
        conn.execute("ALTER TABLE run_logs ADD COLUMN account_id INTEGER DEFAULT 1")
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        # 单用户阶段固定使用 id=1 的默认账号
        conn.execute("INSERT OR IGNORE INTO accounts(id, label, created_at) VALUES(1, 'default', ?)", (utcnow(),))
    return conn
