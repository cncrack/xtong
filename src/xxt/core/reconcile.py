"""记账对齐（对账）：以平台标识为准修正本地章节完成状态。

为什么有这个模块（2026-10-29 用户要求"刷之前先和平台对账"）：
本地 `chapters.status` 过去只有一个写入来源——"程序自己播完了一节"。平台侧的完成
标识（号主自己看完、或早期会话没记上）**没有任何通道同步进来**，于是缺口永不自愈：
实测账号#2《知行合一》平台 70/70 已完成，本地仍记 29 节待学，调度因而反复白跑真实
页面——这正是"不能账号被封"最怕的无意义重复访问。

分工（避免两处各写一套状态规则）：

* 适配器层（`adapters/<平台>/chapters.py`）：把页面解析成抽象字段
  `[{platform_chapter_id, name, idx, done_on_platform}]`。
* 本模块：拿抽象字段与本地库对齐，**不含任何平台细节**，因此第二个平台可直接复用。

配置见 `config.py` 的 `[reconcile]` 段（每项都注明作用对象），读取一律走
`guardrails()`，不要在调用方各写一份 `settings.raw.get(...)` 链。
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any

from ..config import Settings

# run_logs 机器码：面板日志页按它筛选
EVENT = "reconcile"


def guardrails(settings: Settings, *, allow_demote: bool | None = None) -> dict[str, Any]:
    """从配置取对账参数（配置键名只在这里出现一次）。

    `allow_demote` 显式传入时覆盖配置——用于命令行 `--demote` 这种一次性授权。
    """
    cfg = settings.reconcile or {}
    return {
        "allow_demote": bool(cfg.get("demote", False)) if allow_demote is None
        else bool(allow_demote),
        "min_coverage_pct": int(cfg.get("demote_min_coverage_pct", 90)),
        "max_demote_ratio_pct": int(cfg.get("demote_max_ratio_pct", 20)),
    }


def pre_session_enabled(settings: Settings) -> bool:
    """学习会话开刷前要不要对账（配置 `[reconcile].pre_session`，默认开）。"""
    return bool((settings.reconcile or {}).get("pre_session", True))


def reconcile_local(
    conn: sqlite3.Connection,
    course_id: int,
    leaves: list[dict[str, Any]],
    *,
    account_id: int | None = None,
    allow_demote: bool = False,
    min_coverage_pct: int = 90,
    max_demote_ratio_pct: int = 20,
    course_name: str = "",
    log_event: bool = True,
) -> dict[str, Any]:
    """以平台章节树的完成标识为准对齐本地记账。

    三处共用同一份实现：`xxt chapters`、同步学习器、异步学习器——避免"只改一条
    路径、另一条文本报错"。

    两个方向规则**不对称**（有意为之）：

    * **升级**（平台已完成 → 本地置 `done`）：**无条件**。平台是唯一事实来源，
      不升级的代价是把已刷完的节当待学反复白跑真实页面。
    * **降级**（平台未完成 → 本地打回 `pending`）：**默认不做**，需 `allow_demote`。
      不降级的代价是"本地虚标 done"会静默漏刷、丢学分；但降级可能引发整门课重刷，
      所以只在 `chapters`（专门抓树、页面等待充分）里提供，学习路径不启用。

    降级两道护栏（任一不过则只报警、不写库）：

    1. 覆盖度：本次 `leaves` 节数 < 本地已记录节数 × `min_coverage_pct`% → "树没抓全"。
    2. 比例：一次要打回的节数 > 本次节数 × `max_demote_ratio_pct`% → "图标未渲染/页面改版"。

    本地不存在的行不由本函数处理（那是调用方 UPSERT 建行的职责）。
    `leaves` 为空时直接原样返回，绝不把整门课判成未完成。
    """
    if not leaves:
        local_total = conn.execute(
            "SELECT COUNT(*) FROM chapters WHERE course_id=?", (course_id,)).fetchone()[0]
        return {
            "promoted": [], "demoted": [], "demote_candidates": [],
            "demote_blocked": "平台章节树解析结果为空，本次不做任何状态对齐" if local_total else None,
            "platform_total": 0, "local_total": local_total,
            "local_done": conn.execute(
                "SELECT COUNT(*) FROM chapters WHERE course_id=? AND status='done'",
                (course_id,)).fetchone()[0],
        }

    plat_all: set[str] = set()
    plat_done: set[str] = set()
    for le in leaves:
        kid = str(le["platform_chapter_id"])
        plat_all.add(kid)
        if le["done_on_platform"]:
            plat_done.add(kid)

    local = {r["platform_chapter_id"]: r["status"] for r in conn.execute(
        "SELECT platform_chapter_id, status FROM chapters WHERE course_id=?", (course_id,))}

    # 升级：平台说完成就完成，包括本地标 visited（非视频节的收尾标记）的行的归位
    promoted = sorted(k for k in plat_done if k in local and local[k] != "done")
    demote_cands = sorted(k for k in plat_all if local.get(k) == "done" and k not in plat_done)

    demote: list[str] = []
    demote_blocked: str | None = None
    if demote_cands:
        if not allow_demote:
            demote_blocked = f"有 {len(demote_cands)} 节平台标未完成但本地记已完成，未开降级"
        elif len(leaves) * 100 < min_coverage_pct * len(local):
            demote_blocked = (f"本次抓到 {len(leaves)}/{len(local)} 节，低于覆盖度下限"
                              f"{min_coverage_pct}%，视为树没抓全，不降级")
        elif len(demote_cands) * 100 > max_demote_ratio_pct * len(leaves):
            demote_blocked = (f"单次要打回 {len(demote_cands)} 节，超过本次节数的"
                              f"{max_demote_ratio_pct}%，视为完成图标未渲染或页面改版，不降级")
        else:
            demote = demote_cands

    for kid in promoted:
        conn.execute(
            "UPDATE chapters SET status='done' WHERE course_id=? AND platform_chapter_id=?",
            (course_id, kid))
    for kid in demote:
        conn.execute(
            "UPDATE chapters SET status='pending' WHERE course_id=? AND platform_chapter_id=?",
            (course_id, kid))
    if promoted or demote:
        conn.commit()

    done_now = conn.execute(
        "SELECT COUNT(*) FROM chapters WHERE course_id=? AND status='done'",
        (course_id,)).fetchone()[0]

    if log_event and (promoted or demote or demote_blocked):
        parts = []
        if promoted:
            parts.append(f"升级 {len(promoted)} 节（待学/已访问→已完成）")
        if demote:
            parts.append(f"降级 {len(demote)} 节（已完成→待学）")
        if demote_blocked:
            parts.append(f"降级未执行：{demote_blocked}")
        conn.execute(
            "INSERT INTO run_logs(ts, level, event, detail, account_id) VALUES(?,?,?,?,?)",
            (time.strftime("%Y-%m-%dT%H:%M:%S"),
             "WARNING" if demote_blocked else "INFO", EVENT,
             f"{course_name}｜" + "｜".join(parts) + f"｜本地已完成 {done_now}/{len(local)}",
             account_id))
        conn.commit()

    return {
        "promoted": promoted,
        "demoted": demote,
        "demote_candidates": demote_cands,
        "demote_blocked": demote_blocked,
        "platform_total": len(leaves),
        "local_total": len(local),
        "local_done": done_now,
    }
