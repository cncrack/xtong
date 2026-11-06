"""面板设置的读写（gxks 库的 `meta` 表）。

## 为什么单独一个模块（2026-11-03 用户问「panel.reload 配置项在页面哪里」）

用户要在**页面上**能改，而不是只能编辑 `config/settings.local.toml`。
所以这些开关必须能**运行时改、立即生效**（不像 toml 要重启才读）。

## 为什么不用超星那套 `app_config`

gxks 库里**没有 `app_config` 表**，只有 `meta`（见 `schema_gxks.py`）。
照抄超星的 `app_config` 会得到"能存但读不到"的假功能 —— 与
`gxks_ai_config` 当初踩的坑一模一样，所以这里独立一个模块。

## 存哪

`meta` 表，键前缀 `panel.`：

| 键 | 含义 | 默认 |
|---|---|---|
| `panel.reload` | 改代码后面板自动重启 | `true` |
| `panel.poll_seconds` | 前端轮询间隔（秒） | `4` |

⚠ `panel.reload` **改完要重启面板才生效** —— 它决定的是 uvicorn 启动时
用不用 reload，不能热改自己。这是唯一的例外，其余都是即改即用。

## 关于 `reload` 的一个坑（2026-11-04 查明又修好）

11-03 把它设为默认开，理由是「刷课是独立子进程，面板重启不碰它」——
**这条假设当时是错的**：Windows 上父进程被强杀会**连带终止整棵进程树**
（`_spawn_script` 没给 `DETACHED_PROCESS`），刷课脚本一起死
（日志末尾 `KeyboardInterrupt`），任务中断且**不会自动切下一节**。

今天从根上修好了：`app._spawn_script()` 给子进程加了 `DETACHED_PROCESS`
⇒ 面板重启/热重载不再影响正在跑的刷课 ⇒ 热重载可以安全地默认开着。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

PREFIX = "panel."

DEFAULTS: dict[str, Any] = {
    # ⚠ 2026-11-04：**改回默认开**（今天早些时候曾改成关）。
    #
    # 背景：11-03 设成默认开，理由是「刷课是独立子进程，面板重启不碰它」——
    # **这条假设当时是错的**（Windows 上父进程死 → 整棵进程树死 → 刷课被误杀，
    # 2026-11-04 连续踩到两次）。
    #
    # 现在从根上修好了：`_spawn_script` 给子进程加了 `DETACHED_PROCESS`
    # （脱离父进程的 Job Object）⇒ **面板重启/热重载不再影响正在跑的刷课**。
    # 所以热重载可以安全地默认开着 —— 用户改代码后不用手动重启面板。
    "reload": True,
    "poll_seconds": 4,
}


def _read(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return None if row is None else str(row[0])


def _write(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO meta(key,value) VALUES(?,?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, value))


def load(db_path: Path) -> dict[str, Any]:
    """读全部面板设置（缺的用默认值）。**读不到就返回默认值**，不报错。

    ⚠ 这个"读不到就回默认值"的语义是为了**面板能起来**（配置坏了不该让整个面板不可用），
    但它让调用方**无法区分**"用户没配"和"库读不到/路径传错" —— 2026-11-06 我就因此
    把目录当 db 路径传进去，拿到默认值 `reload=True`，误判成"开着却没生效"的 bug。
    需要区分时请用 `load_strict()`（诊断/排查用）。
    """
    out = dict(DEFAULTS)
    try:
        conn = sqlite3.connect(str(db_path), timeout=5)
    except sqlite3.Error:
        return out
    try:
        for name in DEFAULTS:
            raw = _read(conn, PREFIX + name)
            if raw is None:
                continue
            if name == "reload":
                out[name] = raw.strip().lower() in ("1", "true", "yes", "on")
            elif name == "poll_seconds":
                try:
                    out[name] = max(1, min(60, int(raw)))
                except ValueError:
                    pass
    except sqlite3.Error:
        pass
    finally:
        conn.close()
    return out


def load_strict(db_path: Path) -> dict[str, Any]:
    """诊断专用：**分清"没配"和"读不到"**，不静默回默认值。

    返回 `{"ok", "settings", "present", "error"}`：
    - `ok=False` 表示库打不开（路径错 / 文件不在 / 不是 sqlite），`settings` 仍是可用的默认值；
    - `present` 是**数据库里真实存在的键**（如 `{"panel.reload": "false"}`），
      `load()` 回默认值时就是这些键缺失。

    只给诊断脚本与日志用；面板主路径仍走 `load()`（不能让配置问题把面板挡在门外）。
    """
    import os

    result: dict[str, Any] = {"ok": False, "settings": dict(DEFAULTS),
                              "present": {}, "error": "",
                              "db": str(db_path),
                              "db_exists": os.path.exists(str(db_path))}
    # ⚠ 文件不存在就**不要去连**：`sqlite3.connect` 会**新建**一个空库文件，
    # 一个「只读诊断」脚本因此留下副作用（诊断脚本三原则：清干净全部副作用）。
    if not result["db_exists"]:
        result["error"] = f"文件不存在：{db_path}"
        return result
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error as exc:
        result["error"] = f"打不开库：{exc}"
        return result
    try:
        rows = conn.execute(
            "SELECT key, value FROM meta WHERE key LIKE ?", (PREFIX + "%",)
        ).fetchall()
        result["present"] = {str(k): str(v) for k, v in rows}
        result["settings"] = load(db_path)     # 复用同一套解析，避免两处口径
        result["ok"] = True
    except sqlite3.Error as exc:
        result["error"] = f"meta 表读不了：{exc}"
    finally:
        conn.close()
    return result


def save(db_path: Path, items: dict[str, Any]) -> dict[str, Any]:
    """保存面板设置（只认白名单键），返回保存后的完整设置。

    写失败**不抛** —— 设置存不下不该让面板整个不可用，返回原值即可。
    """
    try:
        conn = sqlite3.connect(str(db_path), timeout=5)
    except sqlite3.Error:
        return load(db_path)
    try:
        if "reload" in items:
            _write(conn, PREFIX + "reload",
                   "true" if items["reload"] else "false")
        if "poll_seconds" in items:
            try:
                sec = max(1, min(60, int(items["poll_seconds"])))
            except (TypeError, ValueError):
                sec = int(DEFAULTS["poll_seconds"])
            _write(conn, PREFIX + "poll_seconds", str(sec))
        conn.commit()
    except sqlite3.Error:
        pass
    finally:
        conn.close()
    return load(db_path)
