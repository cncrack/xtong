"""节列表缓存的**路径规则**（面板与脚本共用，一处定义）。

为什么单独一个模块：`sections_<label>.json` 这个文件名被三处拼接
（`scripts/gxks_sections.py` 写、面板 `_account_cards` 读、面板 `/api/state` 读）。
散着拼，加课程维度时必然有一处漏改，表现为"脚本说写了、面板说没有"。

## 命名规则（2026-10-30 B1）

| 情形 | 文件名 |
|---|---|
| 有课程 value | `sections_<代号>_<课程>.json`，如 `sections_default_32.json` |
| 没有课程（单课程 / 旧数据） | `sections_<代号>.json`，如 `sections_default.json` |

**旧文件不是垃圾**：升级前所有缓存都是不带课程的那种。所以读的时候按
「先找带课程的，再回落到不带课程的」两段查找 —— 用户升级后打开面板不会突然"没有节列表"。

⚠ 课程 value 必须**只含 `[0-9A-Za-z_-]`**：它会进文件名，而课程 value 来自页面 DOM，
属于外部输入。非法值一律退回"不带课程"的名字，绝不拼进路径。
"""

from __future__ import annotations

import re
from pathlib import Path

_SAFE_COURSE = re.compile(r"^[0-9A-Za-z_-]{1,32}$")


def safe_course(course: str | int | None) -> str:
    """课程 value 归一成可安全放进文件名的片段；不合法/为空 → `""`。"""
    if course is None:
        return ""
    text = str(course).strip()
    if not text:
        return ""
    return text if _SAFE_COURSE.match(text) else ""


def filename(label: str, course: str | int | None = None) -> str:
    """缓存文件名。课程合法才带后缀。"""
    token = safe_course(course)
    return f"sections_{label}_{token}.json" if token else f"sections_{label}.json"


def path(debug_dir: Path, label: str, course: str | int | None = None) -> Path:
    """带课程时的缓存路径（课程不合法就等于不带课程）。"""
    return Path(debug_dir) / filename(label, course)


def legacy_path(debug_dir: Path, label: str) -> Path:
    """升级前的老文件名（不带课程）。只用于**读**，不用于写。"""
    return Path(debug_dir) / f"sections_{label}.json"


def read_paths(debug_dir: Path, label: str, course: str | int | None = None) -> list[Path]:
    """读取时的候选路径，**按优先级排序**：先带课程，再回落老文件。

    去重（课程为空时两者相同），避免调用方重复读同一个文件。
    """
    out: list[Path] = []
    for p in (path(debug_dir, label, course), legacy_path(debug_dir, label)):
        if p not in out:
            out.append(p)
    return out


def list_all(debug_dir: Path, label: str) -> list[Path]:
    """某账号的**全部**节列表缓存（含老文件、含各课程），按修改时间倒序。"""
    directory = Path(debug_dir)
    if not directory.is_dir():
        return []
    hits = [p for p in directory.glob(f"sections_{label}*.json")
            if p.name == f"sections_{label}.json"
            or p.name.startswith(f"sections_{label}_")]
    return sorted(hits, key=lambda p: p.stat().st_mtime, reverse=True)


def update_row(debug_dir: Path, label: str, course: str | int | None,
               section_id: str | int, *, done_minutes: int | float | None = None,
               state: str | None = None, state_text: str | None = None,
               practice_ready: bool | None = None,
               practice_locked: bool | None = None,
               exam_id: int | str | None = None,
               name: str = "", required_minutes: int | float | None = None,
               idx: int | None = None, category: str = "",
               playing: bool | None = None) -> bool:
    """把某一节的最新状态**本地回写**进缓存，返回是否写成功。

    为什么要它（用户 2026-11-02）：节列表缓存只有 `gxks_sections.py`（人工点
    「刷新节列表」）会写，刷课脚本播完不回写 → 面板的节进度表停在几天前的旧数据，
    「待学 / 已完成」两个分区不会随学习动。

    原则：
    - **零平台请求** —— 只改本地文件，符合最小访问原则；
    - 优先更新**已存在**的缓存文件里的**已有行**；
    - 该节不在任何缓存里时（2026-11-03 用户踩到：今天刷的是**另一门课**，
      老缓存里根本没有这些节 → 全部静默跳过、面板永远停在 10-30 的旧数据），
      **新建/追加**一份该课程的缓存。否则"动态更新"对新课程完全无效；
    - 新建时只写本次刷到的节，`summary` 按已有行算，不伪造未知节；
    - 原子写（先 .tmp 再 replace），面板 4 秒一轮询不会读到半截 JSON；
    - `read_at` 追加「本地回写」标记：它同时是面板 `SEC_SIG` 的一部分，
      变了面板才会重建表格（动态更新的开关）。

    ## `playing`（2026-11-07 用户：「正在学习的这节没有显示为正在学习」）

    平台只在**每 60 秒**上报一次学时、列表回读又要 5 分钟，所以刚开始学那一节时，
    回读到的行仍然是 `state = not_started / done_minutes = 0` → 面板显示「未学习」，
    可它明明正在播。

    上层（`scripts/run_gxks.py`）现在会在 `tick` 心跳里传 `playing=True`，
    这里据此把该节写成 `state = "learning"` —— **纯本地展示标记**，
    不伪造平台数据（`done_minutes` 仍以平台回读为准，`done_on_platform` 仍看真实 state）。
    任务结束/切下一节时传 `playing=False`，把标记撤掉。

    ## learning 标记的防覆盖与心跳（2026-11-08 用户：「进度区显示不稳定」）

    - **progress 回读不许回降 learning**：平台列表页的 `state` 翻转比学时更滞后
      （学时 60 秒上报、列表 state 刷新更慢），progress 每 5 分钟回读到
      `not_started` 是常态 —— 原代码无条件照写，把 tick 刚标的 learning 覆盖掉，
      面板上「学习中」一闪一闪（实测 1319/1295 在 learning↔not_started 间震荡）。
      现在只有平台明确 `done`（终态）才允许覆盖 learning；`not_started` 不回降。
    - **`learning_at` 心跳时间戳**：每次 `playing=True` 都刷新。任务中断
      （手动停/硬停/崩溃）后 tick 不再来，行会一直停在 learning —— 读方
      （面板 `/api/state` 的 `app._normalize_stale_learning`）按「超 90 秒无心跳」
      降级展示（按 `done_minutes` 推导 done/not_started）。**只读方降级，
      本文件保留原始 learning + learning_at 作为证据**，任务恢复后 tick 会继续刷新。
    """
    import json
    import os
    from datetime import datetime

    sid = str(section_id)
    debug_dir = Path(debug_dir)
    now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    target: Path | None = None
    data: dict | None = None
    for cand in read_paths(debug_dir, label, course):
        if not cand.exists():
            continue
        try:
            loaded = json.loads(cand.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rows = loaded.get("rows") or []
        if any(str(r.get("platform_chapter_id")) == sid for r in rows):
            target, data = cand, loaded
            break

    if target is None or data is None:
        # 该节不在任何缓存里 → 追加到「本课程」那份；连文件都没有就新建。
        # 只写本次已知的这一行（不伪造其他节），summary 由这一行算出。
        target = path(debug_dir, label, course)
        data = {"ok": True, "label": label, "read_at": "",
                "course": str(course or ""), "rows": [], "summary": {}}
        if target.exists():
            try:
                data = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {"ok": True, "label": label, "read_at": "",
                        "course": str(course or ""), "rows": [], "summary": {}}
        data.setdefault("rows", [])
        if name:                       # 没有节名就不写行（宁可空着也不写无意义数据）
            new_row = {
                "idx": idx if idx is not None else len(data["rows"]),
                "name": name,
                "platform_chapter_id": sid,
                "required_minutes": int(required_minutes or 0),
                "done_minutes": int(done_minutes or 0),
                "state": state or "learning",
                "state_text": state_text or "",
                "practice_locked": False,
                "done_on_platform": state == "done",
                "category": category or str(course or ""),
                "practice_ready": bool(practice_ready),
            }
            if new_row["state"] == "learning":
                new_row["learning_at"] = now_iso
            data["rows"].append(new_row)

    rows = data["rows"]
    changed = False
    for r in rows:
        if str(r.get("platform_chapter_id")) != sid:
            continue
        if done_minutes is not None and r.get("done_minutes") != int(done_minutes):
            r["done_minutes"] = int(done_minutes)
            changed = True
        # —— state：平台列表页的 state 翻转比学时**滞后**（学时 60 秒上报一次、
        # 列表缓存更久），progress 回读到 not_started 是常态。2026-11-08 实测：
        # 1319/1295 在 learning↔not_started 间来回闪（tick 标 learning → progress
        # 用 not_started 覆盖）。规则改成：
        #   ① 平台明确 done（终态）→ 无条件写；
        #   ② 本地是 learning（tick 正在标这一节）→ 平台 not_started/learning
        #      都**不回降**（progress 事件本身证明 driver 还在播这一节）；
        #   ③ 本地不是 learning → 按平台值正常同步。
        if state == "done":
            if r.get("state") != "done":
                r["state"] = "done"
                changed = True
        elif state and r.get("state") != "learning":
            if r.get("state") != state:
                r["state"] = state
                changed = True
        if practice_ready is not None and r.get("practice_ready") != bool(practice_ready):
            r["practice_ready"] = bool(practice_ready)
            changed = True
        # ⚠ **练习解锁状态也要回写**（2026-11-04 用户实拍：「已完成但显示未解锁」）：
        # 平台在达标后会**异步**开放练习（onclick 从 `alert('请先完成学习！')`
        # 变成 `showExam(<id>)`）。回读的行里本来就带 `practice_locked` / `exam_id`，
        # 但原来只写 `practice_ready` → 缓存里「未解锁」永远不消失，
        # 而且 `exam_id` 一直是 None → 面板的「做练习」按钮永远不显示。
        if practice_locked is not None and r.get("practice_locked") != bool(practice_locked):
            r["practice_locked"] = bool(practice_locked)
            changed = True
        # `exam_id` 统一按字符串比较：调用方可能传 int（回读的行）也可能传 str
        # （命令行参数），直接 `!=` 会把「30 vs "30"」当成变了、白写一次文件。
        if exam_id is not None and str(r.get("exam_id")) != str(exam_id):
            r["exam_id"] = exam_id
            changed = True
        # 正在播这一节（纯展示标记，见 docstring 的 `playing` 一段）：
        # 平台回读的 state 还是 not_started 时，靠它让面板显示「学习中」。
        # ⚠ `playing=None`（调用方不关心标记，如 SKIP 回写）必须**原样跳过**
        # 这个块 —— 不能把 None 当 False 走撤回（2026-11-08 自审抓出的回归）。
        if playing is True:
            if r.get("state") != "done":
                if r.get("state") != "learning":
                    r["state"] = "learning"
                    changed = True
                # ⚠ `state_text` 由循环末尾的对齐块统一写「学习中」。
                # 心跳时间戳：tick 每 30 秒刷新一次。任务中断（手动停/硬停/崩溃）
                # 后不再有心跳，行会一直停在 learning —— 读方（面板 /api/state）
                # 据此判「超 90 秒无心跳 = 已中断」并降级展示，见
                # app._normalize_stale_learning（只影响展示，不改本文件）。
                r["learning_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                changed = True
        elif playing is False and r.get("state") == "learning" and not state:
            # 撤掉标记（只在**读不到平台行**时走：此时无任何平台信息可按，
            # 回到平台给过的 `done_minutes` 决定的状态）。
            # 注意：progress 读到了平台值（哪怕是滞后的 not_started）时不走这里——
            # 那是上面②的情况，learning 要保留。
            r["state"] = "done" if (int(r.get("done_minutes") or 0) >=
                                    int(r.get("required_minutes") or 0) > 0) \
                else "not_started"
            r["state_text"] = "已完成" if r["state"] == "done" else "未学习"
            changed = True
        # **一致性兜底**（2026-11-05 用户实拍「已学完却仍显示未解锁」查出的脏数据）：
        # 缓存里出现过 `practice_locked=False exam_id=14691 practice_ready=False`
        # 这种**自相矛盾**的行 —— 平台已解锁、练习 ID 都有，唯独 ready 是旧值。
        # 成因是回写方漏算 `practice_ready`（见 `listparse.practice_ready` 的注释）。
        # 这里按同一口径重新推导一次：只要行内信息足以判定"已解锁"，就以行内信息为准，
        # 不让一个陈旧布尔挡住「做练习」按钮。**不新增平台请求**，纯本地推导。
        if r.get("practice_locked") is False and r.get("exam_id"):
            if not r.get("practice_ready"):
                r["practice_ready"] = True
                changed = True
        # 离开 learning 后心跳时间戳没用了，清掉避免读方误判
        if r.get("state") != "learning":
            r.pop("learning_at", None)
        # state_text 最终与 state 对齐：learning 一律「学习中」（平台滞后的
        # 「未学习」不许写进来）；其余用平台回读值。
        if r.get("state") == "learning":
            if r.get("state_text") != "学习中":
                r["state_text"] = "学习中"
                changed = True
        elif state_text is not None:
            r["state_text"] = state_text
        break

    # summary 与 gxks_sections.py 的口径保持一致
    data["summary"] = {
        "sections": len(rows),
        "done": sum(1 for r in rows if r.get("state") == "done"),
        "practice_ready": sum(1 for r in rows if r.get("practice_ready")),
        "practice_locked": sum(1 for r in rows if r.get("practice_locked")),
        "minutes_done": sum(int(r.get("done_minutes") or 0) for r in rows),
        "minutes_required": sum(int(r.get("required_minutes") or 0) for r in rows),
    }
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    base = str(data.get("read_at") or "")
    data["read_at"] = f"{base.split('（本地回写')[0].strip()}（本地回写 {stamp}）" if base else f"本地回写 {stamp}"

    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, target)
    return True
