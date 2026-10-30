"""第二平台的 AI 配置存取（存 **gxks 自己的库**，与超星完全不共享）。

## 为什么要单独写，而不复用超星那套

超星面板 `panel/app.py` 的 provider 实现连着 `data/xxt.db` 的 `app_config` 表，
而 **gxks 库里根本没有 `app_config` 表**（只有 `meta`，见 `schema_gxks.py`）。
实测确认过：照抄会得到"配置能存、但 `ai/llm.py` 永远读不到 Key"的假功能。

所以这里：
- 厂商列表 / 当前选中 → 存 gxks 库的 `meta` 表；
- API Key → 用 `core.crypto` 加密后存 `meta`（密钥是 `data/secret.key`，随目录隔离）。

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
K_KEY_ENC = "ai.key_enc"
K_TIMEOUT = "ai.timeout"


def _read(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return None if row is None else str(row["value"])


def _write(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def _saved_list(conn: sqlite3.Connection) -> list[dict[str, str]]:
    raw = _read(conn, K_PROVIDERS)
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [x for x in data if isinstance(x, dict)]


def load(db_path: Path, *, data_dir: Path) -> dict[str, Any]:
    """读一份可直接给面板的 AI 配置。

    返回：
        {
          "providers": [...],        # 内置 + 自定义（同名以自定义为准）
          "fields": [...],           # 每项配置的中文说明
          "current": {"name","base_url","model"},
          "has_key": bool,           # 是否已配 Key（**永远不回显 Key 本身**）
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
        # 没选过就默认第一家（DeepSeek）
        name = str(current.get("name") or "").strip()
        if not name:
            name = providers[0]["name"] if providers else ""
            current = {"name": name}
        try:
            timeout = int(_read(conn, K_TIMEOUT) or 60)
        except (TypeError, ValueError):
            timeout = 60
        has_key = _read(conn, K_KEY_ENC) is not None
    finally:
        conn.close()

    merged = merge({"provider": current.get("name")}, saved)
    return {
        "providers": providers,
        "fields": list(FIELDS),
        "current": {
            "name": current.get("name", ""),
            "base_url": str(merged.get("base_url") or ""),
            "model": str(merged.get("model") or ""),
        },
        "has_key": bool(has_key),
        "timeout": timeout,
    }


def save_provider(db_path: Path, item: Any) -> tuple[list[dict[str, str]], str]:
    """新增或覆盖一家厂商。返回 `(新的自定义列表, 错误说明)`。"""
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
    conn = open_question_bank(db_path)
    try:
        saved = _saved_list(conn)
        new_list = remove(saved, name)
        with conn:
            _write(conn, K_PROVIDERS, json.dumps(new_list, ensure_ascii=False))
        # 删掉的正好是当前选中 → 回落到第一家
        cur_raw = _read(conn, K_CURRENT)
        if cur_raw:
            try:
                cur = json.loads(cur_raw)
                if str(cur.get("name") or "").strip() == str(name).strip():
                    first = as_list(new_list)[0]["name"]
                    _write(conn, K_CURRENT, json.dumps({"name": first},
                                                        ensure_ascii=False))
            except (TypeError, ValueError):
                pass
        return new_list
    finally:
        conn.close()


def save_current(db_path: Path, name: str, *, base_url: str = "",
                 model: str = "", timeout: int | None = None) -> tuple[dict[str, Any], str]:
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
                                             if k != "name"}, }, saved)
        return {"name": want, "base_url": str(merged.get("base_url") or ""),
                "model": str(merged.get("model") or "")}, ""
    finally:
        conn.close()


def save_key(db_path: Path, *, data_dir: Path, api_key: str) -> tuple[bool, str]:
    """加密保存 API Key。空串表示**不修改**（面板不回显，无法区分"没填"和"清空"）。"""
    key = str(api_key or "").strip()
    if not key:
        return False, "Key 为空，未做修改"
    try:
        from ..core.crypto import encrypt, master_key
        blob = encrypt(master_key(data_dir), key)
    except Exception as exc:                                  # noqa: BLE001
        return False, f"加密失败：{type(exc).__name__}"
    conn = open_question_bank(db_path)
    try:
        with conn:
            # 裸 base64（不 json.dumps）：`_load_key` 直接 b64decode 这一行
            _write(conn, K_KEY_ENC, base64.b64encode(blob).decode("ascii"))
        return True, ""
    finally:
        conn.close()


def load_key(db_path: Path, *, data_dir: Path) -> str | None:
    """解密取出 API Key（只用于发请求，绝不回显到界面）。"""
    conn = open_question_bank(db_path)
    try:
        raw = _read(conn, K_KEY_ENC)
    finally:
        conn.close()
    if not raw:
        return None
    try:
        from ..core.crypto import decrypt, master_key
        return decrypt(master_key(data_dir), base64.b64decode(raw))
    except Exception:                                         # noqa: BLE001
        return None
