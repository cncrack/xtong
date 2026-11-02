"""第二平台的 AI 配置存取（存 **gxks 自己的库**，与超星完全不共享）。

## 为什么要单独写，而不复用超星那套

超星面板 `panel/app.py` 的 provider 实现连着 `data/xxt.db` 的 `app_config` 表，
而 **gxks 库里根本没有 `app_config` 表**（只有 `meta`，见 `schema_gxks.py`）。
实测确认过：照抄会得到"配置能存、但 `ai/llm.py` 永远读不到 Key"的假功能。

## Key 按**厂商**分开存（2026-11-02 用户要求）

用户原话：「如果配置了 Key，这个配置要能够保存，下次在不同厂商切换也能直接调用，
不用再填写」。所以：
- 键名 → `ai.key.<厂商名>`，每个厂商一把
- 切到 DeepSeek 用 DeepSeek 的 Key，切到 Kimi 用 Kimi 的，互不影响
- 来回切换不用重填

**兼容旧数据**：老版本只有一个 `ai.key_enc`。若某厂商没有自己的 Key，
会退化到旧的那把（避免升级后 Key 失效）；保存新 Key 时顺手清掉旧的。

## 安全约定

- **Key 不入库、不回显**：读出来只用于发请求；给前端的一律只有"有没有配置"这个布尔值。
- 不读环境变量（分发版数据封闭于自身 `data/`）。

## 关于 `meta` 表的键

统一前缀 `ai.`，避免和题库自己的 key（如 `gxks_schema_version`）混在一起。
"""

from __future__ import annotations

import base64
import json
import sqlite3
from pathlib import Path
from typing import Any

from ..ai.providers import FIELDS, as_list, merge, remove, upsert
from .schema_gxks import open_question_bank

K_PROVIDERS = "ai.providers"
K_CURRENT = "ai.current"
K_TIMEOUT = "ai.timeout"
K_KEY_PREFIX = "ai.key."
K_KEY_LEGACY = "ai.key_enc"


def _read(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return None if row is None else str(row["value"])


def _write(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def _delete(conn: sqlite3.Connection, key: str) -> None:
    conn.execute("DELETE FROM meta WHERE key=?", (key,))


def _key_name(provider: str) -> str:
    """每个厂商一把 Key 的存储键名。"""
    return K_KEY_PREFIX + str(provider or "").strip()


def _saved_list(conn: sqlite3.Connection) -> list[dict[str, str]]:
    raw = _read(conn, K_PROVIDERS)
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [x for x in data if isinstance(x, dict)]


def _decrypt(blob_b64: str, data_dir: Path) -> str | None:
    try:
        from ..core.crypto import decrypt, master_key
        return decrypt(master_key(data_dir), base64.b64decode(blob_b64))
    except Exception:                                         # noqa: BLE001
        return None


def _encrypt(api_key: str, data_dir: Path) -> str:
    from ..core.crypto import encrypt, master_key
    return base64.b64encode(encrypt(master_key(data_dir), api_key)).decode("ascii")


def load_key_for(db_path: Path, *, data_dir: Path, provider: str) -> str | None:
    """取某个厂商的 Key。该厂商没配就退化到旧的全局 Key（若有）。"""
    conn = open_question_bank(db_path)
    try:
        raw = _read(conn, _key_name(provider))
        legacy = _read(conn, K_KEY_LEGACY)
    finally:
        conn.close()
    if raw:
        return _decrypt(raw, data_dir)
    if legacy:
        return _decrypt(legacy, data_dir)
    return None


def has_key_for(db_path: Path, provider: str, *, data_dir: Path) -> bool:
    return bool(load_key_for(db_path, data_dir=data_dir, provider=provider))


def _own_key_status(conn: sqlite3.Connection,
                    providers: list[dict[str, str]]) -> dict[str, bool]:
    """每个厂商「自己有没有配 Key」。只看自己那把，不算退化 ——
    否则所有厂商都会显示"已配置"，用户分不清哪家真的配了。"""
    return {p["name"]: _read(conn, _key_name(p["name"])) is not None
            for p in providers}


def load(db_path: Path, *, data_dir: Path) -> dict[str, Any]:
    """读一份可直接给面板的 AI 配置。

    返回：
        {
          "providers": [{name, base_url, model, has_key}, ...],
          "fields": [...],
          "current": {"name","base_url","model","has_key"},
          "has_key": bool,        # 当前厂商是否能用（含旧 Key 退化）
          "legacy_key": bool,     # 当前厂商靠的是不是那把旧的全局 Key
          "timeout": int,
        }
    """
    conn = open_question_bank(db_path)
    try:
        saved = _saved_list(conn)
        cur_raw = _read(conn, K_CURRENT) or ""
        current: dict[str, Any] = {}
        if cur_raw:
            try:
                current = json.loads(cur_raw)
            except (TypeError, ValueError):
                current = {"name": cur_raw}
        providers = as_list(saved)
        own = _own_key_status(conn, providers)
        legacy = _read(conn, K_KEY_LEGACY) is not None
        name = str(current.get("name") or "").strip()
        if not name:
            name = providers[0]["name"] if providers else ""
            current = {"name": name}
        try:
            timeout = int(_read(conn, K_TIMEOUT) or 60)
        except (TypeError, ValueError):
            timeout = 60
    finally:
        conn.close()

    for p in providers:
        p["has_key"] = bool(own.get(p["name"]))
    merged = merge({"provider": name}, saved)
    cur_has = bool(own.get(name)) or legacy
    return {
        "providers": providers,
        "fields": list(FIELDS),
        "current": {
            "name": name,
            "base_url": str(merged.get("base_url") or ""),
            "model": str(merged.get("model") or ""),
            "has_key": cur_has,
        },
        "has_key": cur_has,
        "legacy_key": legacy and not own.get(name, False),
        "timeout": timeout,
    }


def save_provider(db_path: Path, item: Any) -> tuple[list[dict[str, str]], str]:
    """新增或覆盖一家厂商。返回 (新的自定义列表, 错误说明)。"""
    conn = open_question_bank(db_path)
    try:
        saved = _saved_list(conn)
        new_list, err = upsert(saved, item)
        if err:
            return saved, err
        with conn:
            _write(conn, K_PROVIDERS, json.dumps(new_list, ensure_ascii=False))
        return new_list, ""
    finally:
        conn.close()


def delete_provider(db_path: Path, name: str) -> list[dict[str, str]]:
    """删厂商：连它那把 Key 一起删（否则会留下孤儿加密数据）。"""
    want = str(name or "").strip()
    conn = open_question_bank(db_path)
    try:
        saved = _saved_list(conn)
        new_list = remove(saved, want)
        with conn:
            _write(conn, K_PROVIDERS, json.dumps(new_list, ensure_ascii=False))
            _delete(conn, _key_name(want))
        cur_raw = _read(conn, K_CURRENT)
        if cur_raw:
            try:
                cur = json.loads(cur_raw)
                if str(cur.get("name") or "").strip() == want:
                    first = as_list(new_list)[0]["name"]
                    _write(conn, K_CURRENT, json.dumps({"name": first},
                                                        ensure_ascii=False))
            except (TypeError, ValueError):
                pass
        return new_list
    finally:
        conn.close()


def save_current(db_path: Path, name: str, *, base_url: str = "",
                 model: str = "", timeout: int | None = None
                 ) -> tuple[dict[str, Any], str]:
    """切换当前厂商。允许只给名字（地址/模型按厂商列表补）。"""
    conn = open_question_bank(db_path)
    try:
        saved = _saved_list(conn)
        want = str(name or "").strip()
        if not want:
            return {}, "厂商名不能为空"
        cur = {"name": want}
        if base_url:
            cur["base_url"] = str(base_url).strip().rstrip("/")
        if model:
            cur["model"] = str(model).strip()
        with conn:
            _write(conn, K_CURRENT, json.dumps(cur, ensure_ascii=False))
            if timeout:
                try:
                    _write(conn, K_TIMEOUT, str(int(timeout)))
                except (TypeError, ValueError):
                    pass
        merged = merge({"provider": want, **{k: v for k, v in cur.items()
                                             if k != "name"}}, saved)
        return {"name": want, "base_url": str(merged.get("base_url") or ""),
                "model": str(merged.get("model") or "")}, ""
    finally:
        conn.close()


def save_key(db_path: Path, *, data_dir: Path, provider: str,
             api_key: str) -> tuple[bool, str]:
    """加密保存**指定厂商**的 Key。空串表示不修改。"""
    who = str(provider or "").strip()
    if not who:
        return False, "要先选一个厂商"
    key = str(api_key or "").strip()
    if not key:
        return False, "Key 为空，未做修改"
    try:
        blob = _encrypt(key, data_dir)
    except Exception as exc:                                  # noqa: BLE001
        return False, f"加密失败：{type(exc).__name__}"
    conn = open_question_bank(db_path)
    try:
        with conn:
            _write(conn, _key_name(who), blob)
            _delete(conn, K_KEY_LEGACY)     # 已有按厂商的 Key，旧的清掉
        return True, ""
    finally:
        conn.close()


def delete_key(db_path: Path, *, provider: str) -> bool:
    """删掉某厂商的 Key（"清除"操作用）。"""
    conn = open_question_bank(db_path)
    try:
        with conn:
            _delete(conn, _key_name(str(provider or "").strip()))
        return True
    finally:
        conn.close()
