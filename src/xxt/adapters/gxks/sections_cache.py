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
               name: str = "", required_minutes: int | float | None = None,
               idx: int | None = None, category: str = "") -> bool:
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
    """
    import json
    import os
    from datetime import datetime

    sid = str(section_id)
    debug_dir = Path(debug_dir)
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
            data["rows"].append({
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
            })

    rows = data["rows"]
    changed = False
    for r in rows:
        if str(r.get("platform_chapter_id")) != sid:
            continue
        if done_minutes is not None and r.get("done_minutes") != int(done_minutes):
            r["done_minutes"] = int(done_minutes)
            changed = True
        if state and r.get("state") != state:
            r["state"] = state
            changed = True
        if state_text is not None:
            r["state_text"] = state_text
        if practice_ready is not None:
            r["practice_ready"] = bool(practice_ready)
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
