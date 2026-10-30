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
