"""SQLite 存储层。P0 单用户单库，P1 在此基础上扩展账号维度。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 8

# v7：账号作业策略——ai（AI代答semi）/ owner（号主自理，默认）/ reference（面板出参考答案人工填）
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
    "note": "TEXT",
    "valid_until": "TEXT",
    "renew_count": "INTEGER DEFAULT 0",
    "last_expired_at": "TEXT",
    "work_mode": "TEXT",  # ai | owner | reference；NULL=跟随全局默认（v0.6 三层模型）
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
    platform_username TEXT,
    note         TEXT,
    valid_until  TEXT,
    renew_count  INTEGER DEFAULT 0,
    last_expired_at TEXT,
    work_mode    TEXT
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
    created_at    TEXT,
    updated_at    TEXT
);
-- 答案按账号隔离（2026-10-26 用户要求：题目可共用，答案不可共用，只能各自用 AI 生成）。
-- account_id=0 保留给"历史遗留答案"：只展示、不参与自动作答。
CREATE TABLE IF NOT EXISTS answers (
    question_hash TEXT NOT NULL,
    account_id    INTEGER NOT NULL,
    answer        TEXT,               -- JSON：该账号的候选/已验证答案
    correct_count INTEGER NOT NULL DEFAULT 0,
    wrong_count   INTEGER NOT NULL DEFAULT 0,
    source        TEXT,               -- llm | manual | legacy
    updated_at    TEXT,
    PRIMARY KEY (question_hash, account_id)
);
CREATE TABLE IF NOT EXISTS app_config (
    key   TEXT PRIMARY KEY,   -- 点分键，如 quiz.enabled / pace.daily_window
    value TEXT NOT NULL       -- JSON 值
);
CREATE TABLE IF NOT EXISTS run_logs (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts     TEXT NOT NULL,
    level  TEXT NOT NULL,
    event  TEXT NOT NULL,
    detail TEXT,
    account_id INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS work_questions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    cache_key  TEXT NOT NULL,
    idx        INTEGER NOT NULL,
    qid        TEXT,
    qtype      TEXT,
    qtype_cn   TEXT,
    stem       TEXT,
    options    TEXT,
    updated_at TEXT,
    UNIQUE (cache_key, idx)
);
-- 批次（2026-10-26）：把"某门课"统一排给一批账号。**不含时间字段**——
-- 开跑时刻仍由每账号自己的 start_at / 学习窗口决定，天然错峰。
CREATE TABLE IF NOT EXISTS plans (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    course_name TEXT NOT NULL,
    enabled     INTEGER NOT NULL DEFAULT 1,
    extra_json  TEXT,
    created_at  TEXT
);
CREATE TABLE IF NOT EXISTS plan_accounts (
    plan_id    INTEGER NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    PRIMARY KEY (plan_id, account_id)
);
-- 账号画像（2026-10-26 用户要求统计能按"年级/专业/开学期"分组）：
-- 单独成列而不是塞进 note，否则没法按维度聚合。
CREATE TABLE IF NOT EXISTS account_profiles (
    account_id INTEGER PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
    student_no TEXT,
    grade      TEXT,
    major      TEXT,
    term       TEXT,
    source     TEXT,
    updated_at TEXT
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
    pcols = {r["name"] for r in conn.execute("PRAGMA table_info(account_profiles)")}
    for name, decl in (("student_no", "TEXT"),      # 学号（2026-10-26 用户要求）
                       ("grade", "TEXT"), ("major", "TEXT"), ("term", "TEXT"),
                       ("source", "TEXT")):          # source: manual | platform
        if name not in pcols:
            conn.execute(f"ALTER TABLE account_profiles ADD COLUMN {name} {decl}")
    with conn:
        # 答案按账号隔离（2026-10-26）：老库 questions 里的全局 answer 迁到
        # answers(account_id=0) 后清空——归不到具体账号的旧答案不得再被自动复用，
        # 只作历史展示。此迁移幂等（清空后 answer 全为 NULL，下次不会再触发）。
        qcols = {r["name"] for r in conn.execute("PRAGMA table_info(questions)")}
        if "answer" in qcols:
            legacy = conn.execute(
                "SELECT COUNT(*) FROM questions WHERE answer IS NOT NULL").fetchone()[0]
            if legacy:
                conn.execute(
                    "INSERT OR IGNORE INTO answers"
                    "(question_hash, account_id, answer, source, updated_at)"
                    " SELECT question_hash, 0, answer, 'legacy', datetime('now')"
                    " FROM questions WHERE answer IS NOT NULL")
                conn.execute("UPDATE questions SET answer = NULL WHERE answer IS NOT NULL")
        conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        # 单用户阶段固定使用 id=1 的默认账号
        conn.execute("INSERT OR IGNORE INTO accounts(id, label, created_at) VALUES(1, 'default', ?)", (utcnow(),))
    return conn
