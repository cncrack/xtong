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
    """读全部面板设置（缺的用默认值）。**读不到就返回默认值**，不报错。"""
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
