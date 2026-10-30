"""运行账本（本地 jsonl）：日额度与“今天学了哪几节”的唯一依据。

为什么需要它：站点列表**只给累计学时，不给“今天”**，也没有任何接口能查当日进度。
所以“每账号每天最多 N 节 / M 分钟”只能靠本地记账——每跑完一节追加一行，
调度器开跑前先读当日合计，够了就跳过该账号。

三条纪律（诊断脚本三原则的同款要求）：

1. **只追加不重写**：并发/重跑最多多一行，不会把别人的记录覆盖掉；
2. **幂等标记**：同一 `account + course + section_id + date` 只算一次（重复跑不会重复计额度）；
3. **单条损坏不影响整体**：坏行跳过，不抛异常。

文件放 `data/`（gitignore），只记代号与节 ID / 分钟数，**不记凭据**。

## 额度口径（2026-10-30 A1 定案）

- **日额度按「账号合计」**：`daily_sections` / `daily_minutes` 是账号属性，多门课**共用**一个池子。
  理由：额度是刹车，而封号按账号/IP 维度发生；按课程分额度等于把同一账号的天花板乘上课程数。
- **`course` 字段只做统计与去重，不参与额度判定**：`today_totals()` 缺省仍只按 `account` 过滤。
  加它是为了两件事：① 多课程时按课统计（`today_totals(course=...)`）；② **防止跨课程节 ID 撞车**
  ——不同学习计划复用同一个 `section_id` 是常见做法，只按 `section_id` 去重会少记额度、账号反而超刷。
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path

from xxt.config import find_project_root


def default_path() -> Path:
    """账本默认位置：跟着**项目根**（`XXT_ROOT` / exe 目录 / 仓库根），不跟当前工作目录。

    早先写成相对路径 `data/gxks_ledger.jsonl`，从别的目录启动就会写出第二份账本，
    同一账号的当日额度被拆到两处，额度判定直接失效（2026-10-30 修正）。
    """
    return find_project_root() / "data" / "gxks_ledger.jsonl"


def _resolve(path: str | Path | None) -> Path:
    return Path(path) if path is not None else default_path()


def _today() -> str:
    return date.today().isoformat()


def record(path: str | Path | None = None, *, account: str, section_id: str,
           name: str = "", minutes: float = 0.0, result: str = "done",
           course: str = "", at: datetime | None = None) -> dict:
    """追加一条账；返回写入的记录（便于调用方打日志）。

    `course` 是**可选**的课程标识（学习计划 value 或课程名）。老账本没有这个字段，
    读的时候按 `""` 处理，所以新旧数据混在一起也不会炸。
    """
    at = at or datetime.now()
    row = {
        "at": at.isoformat(timespec="seconds"),
        "date": at.date().isoformat(),
        "account": str(account),
        "course": str(course or ""),
        "section_id": str(section_id),
        "name": name,
        "minutes": round(float(minutes), 1),
        "result": result,
    }
    p = _resolve(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
    return row


def read_rows(path: str | Path | None = None) -> list[dict]:
    """读全部账；坏行跳过。文件不存在 → 空列表。"""
    p = _resolve(path)
    if not p.exists():
        return []
    rows: list[dict] = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:                                  # noqa: BLE001
            continue
        if isinstance(row, dict) and row.get("account") and row.get("date"):
            rows.append(row)
    return rows


def today_totals(path: str | Path | None = None, account: str = "",
                 day: str | None = None, course: str | None = None) -> dict:
    """当日合计：`sections`（去重后的节数）/ `minutes` / `section_ids` / `courses`。

    - `account` 为空 = 所有账号合计（面板「今日合计」用）；
    - `course` 给值 = 只统计这门课；**缺省不按课程过滤**（额度按账号合计，见模块 docstring）；
    - 去重键是 `course + section_id`：同一账号两门课出现同一个节 ID 时算两节，
      否则会把额度少记、账号反而超刷。
    """
    day = day or _today()
    keys: set[tuple[str, str]] = set()
    ids: set[str] = set()
    minutes = 0.0
    per_course: dict[str, dict] = {}
    for row in read_rows(path):
        if row.get("date") != day:
            continue
        if account and row.get("account") != account:
            continue
        row_course = str(row.get("course") or "")
        if course is not None and row_course != course:
            continue
        if row.get("result") == "done":
            keys.add((row_course, str(row.get("section_id"))))
            ids.add(str(row.get("section_id")))
        try:
            mins = float(row.get("minutes") or 0)
        except (TypeError, ValueError):
            continue
        minutes += mins
        bucket = per_course.setdefault(row_course, {"sections": set(), "minutes": 0.0})
        bucket["minutes"] += mins
        if row.get("result") == "done":
            bucket["sections"].add(str(row.get("section_id")))
    return {
        "date": day, "account": account, "course": course,
        "sections": len(keys),
        "minutes": round(minutes, 1),
        "section_ids": sorted(ids),
        "courses": {k: {"sections": len(v["sections"]), "minutes": round(v["minutes"], 1)}
                    for k, v in sorted(per_course.items())},
    }


def quota_left(account: str, *, sections_limit: int, minutes_limit: int,
               path: str | Path | None = None, day: str | None = None,
               course: str | None = None) -> dict:
    """还剩多少额度（可为 0，不会为负）。调度器据此决定“跳过 / 还能跑几节”。

    ⚠ **额度按账号合计**：`course` 只是把统计范围缩小到某门课（给“这门课还能学几节”用），
    真正的额度判定必须用**不带 course** 的调用。传了 course 得到的 `left_*` 只反映该课已学，
    拿它当账号额度会重复给额度。

    ⚠ **`0` = 不限**（PLAN-GXKS §11.6 已定口径）：`sections_limit=0` 或
    `minutes_limit=0` 表示该项不设上限，此时对应的 `left_*` 返回 `None`（不是 0——
    返回 0 会被调用方读成“额度用完”，这正是超星 `reached_locally()` 的老毛病）。
    """
    t = today_totals(path, account, day, course)
    if int(sections_limit) > 0:
        left_sections: int | None = max(0, int(sections_limit) - int(t["sections"]))
    else:
        left_sections = None                       # 不限
    if float(minutes_limit) > 0:
        left_minutes: float | None = max(0.0, float(minutes_limit) - float(t["minutes"]))
    else:
        left_minutes = None                        # 不限
    exhausted = ((left_sections is not None and left_sections <= 0)
                 or (left_minutes is not None and left_minutes <= 0))
    return {**t, "left_sections": left_sections,
            "left_minutes": (round(left_minutes, 1) if left_minutes is not None else None),
            "unlimited": {"sections": left_sections is None,
                          "minutes": left_minutes is None},
            "exhausted": exhausted}
