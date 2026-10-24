"""账号池管理（PLAN §5.6 / v0.13）：增删查、凭据加密、三层配置合并。

三层配置：settings.toml 全局默认 ← DB accounts 账号级覆盖（可空回落）← CLI/面板仅编辑入口。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from ..config import Settings
from ..store.db import utcnow
from .crypto import decrypt, encrypt

_ACCOUNT_PUBLIC_COLS = (
    "id, label, status, created_at, window_start, window_end, start_at, "
    "daily_max_minutes, daily_max_tasks, proxy, priority, platform_username, "
    "note, valid_until, renew_count, last_expired_at, work_mode"
)


def add_account(conn: sqlite3.Connection, key: bytes, label: str, username: str,
                password: str, proxy: str | None = None, priority: float = 1.0) -> int:
    cur = conn.execute(
        "INSERT INTO accounts(label, status, created_at, username_enc, password_enc,"
        " proxy, priority) VALUES(?, 'active', ?, ?, ?, ?, ?)",
        (label, utcnow(), encrypt(key, username), encrypt(key, password), proxy, priority),
    )
    conn.commit()
    return int(cur.lastrowid)


def list_accounts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """账号列表（绝不返回明文凭据）。"""
    return conn.execute(f"SELECT {_ACCOUNT_PUBLIC_COLS} FROM accounts ORDER BY id").fetchall()


def find_account(conn: sqlite3.Connection, needle: str) -> sqlite3.Row | None:
    return conn.execute(
        f"SELECT {_ACCOUNT_PUBLIC_COLS} FROM accounts WHERE id = ? OR label = ? LIMIT 1",
        (needle if needle.isdigit() else -1, needle),
    ).fetchone()


def remove_account(conn: sqlite3.Connection, needle: str) -> bool:
    row = find_account(conn, needle)
    if row is None:
        return False
    conn.execute("DELETE FROM accounts WHERE id = ?", (row["id"],))
    conn.commit()
    return True


def set_overrides(conn: sqlite3.Connection, needle: str, **fields: Any) -> bool:
    """更新账号级覆盖（None 值清除该覆盖，回落全局默认）。"""
    row = find_account(conn, needle)
    if row is None:
        return False
    sets, vals = [], []
    for name in ("window_start", "window_end", "start_at", "proxy", "note",
                 "valid_until", "label", "work_mode"):
        if name in fields:
            sets.append(f"{name} = ?")
            vals.append(fields[name])
    for name in ("daily_max_minutes", "daily_max_tasks"):
        if name in fields:
            sets.append(f"{name} = ?")
            v = fields[name]
            vals.append(int(v) if v is not None else None)
            if "priority" in fields:
                sets.append("priority = ?")
                vals.append(float(fields["priority"]) if fields["priority"] is not None else 1.0)
            if not sets:
                return False
    if not sets:
        return True
    vals.append(row["id"])
    conn.execute(f"UPDATE accounts SET {', '.join(sets)} WHERE id = ?", vals)
    conn.commit()
    return True


def get_credentials(conn: sqlite3.Connection, key: bytes, account_id: int) -> tuple[str, str] | None:
    row = conn.execute(
        "SELECT username_enc, password_enc FROM accounts WHERE id = ?", (account_id,)
    ).fetchone()
    if row is None or not row["username_enc"]:
        return None
    return decrypt(key, row["username_enc"]), decrypt(key, row["password_enc"])


def account_state_path(settings: Settings, account_id: int) -> Path:
    return settings.data_dir / "accounts" / str(account_id) / "state.json"


# ---------- 生命周期（v6：有效期 / 续期 / 失效记录） ----------

def is_valid(row: sqlite3.Row) -> bool:
    """账号是否在有效期内（valid_until 空 = 永久有效）。"""
    vu = row["valid_until"] if "valid_until" in row.keys() else None
    if not vu:
        return True
    import datetime as _dt

    today = _dt.date.today().isoformat()
    return vu >= today


def days_left(row: sqlite3.Row) -> int | None:
    """距有效期剩余天数（永久 = None）。"""
    vu = row["valid_until"] if "valid_until" in row.keys() else None
    if not vu:
        return None
    import datetime as _dt

    try:
        return (_dt.date.fromisoformat(vu) - _dt.date.today()).days
    except ValueError:
        return None


def renew_account(conn: sqlite3.Connection, needle: str, days: int = 30) -> int | None:
    """续期：valid_until 自 max(今天, 现值) 起延长 days 天，renew_count+1。返回新天数余量。"""
    import datetime as _dt

    row = find_account(conn, needle)
    if row is None:
        return None
    base = max(_dt.date.today().isoformat(), row["valid_until"] or "")
    new_until = (_dt.date.fromisoformat(base) + _dt.timedelta(days=days)).isoformat()
    conn.execute(
        "UPDATE accounts SET valid_until=?, renew_count=renew_count+1 WHERE id=?",
        (new_until, row["id"]),
    )
    conn.commit()
    return (_dt.date.fromisoformat(new_until) - _dt.date.today()).days


def mark_expired(conn: sqlite3.Connection, account_id: int) -> None:
    """探测到会话失效时打点（面板"上次失效时间"）。"""
    conn.execute(
        "UPDATE accounts SET last_expired_at=? WHERE id=?",
        (utcnow(), account_id),
    )
    conn.commit()


def effective_pace(settings: Settings, row: sqlite3.Row) -> dict[str, Any]:
    """账号级作息覆盖合并进全局 pace（v0.6 五字段中的窗口两项）。"""
    pace = dict(settings.pace)
    if row["window_start"] and row["window_end"]:
        pace["daily_window"] = [row["window_start"], row["window_end"]]
    return pace


def effective_quota(settings: Settings, row: sqlite3.Row) -> dict[str, Any]:
    quota = dict(settings.quota)
    if row["daily_max_minutes"] is not None:
        quota["daily_minutes"] = row["daily_max_minutes"]
    if row["daily_max_tasks"] is not None:
        quota["daily_tasks"] = row["daily_max_tasks"]
    return quota
