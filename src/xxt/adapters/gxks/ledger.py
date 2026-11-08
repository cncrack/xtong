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


def recent_totals(path: str | Path | None = None, account: str = "",
                  hours: int = 24) -> dict:
    """**近 N 小时**的合计（显示用，与「今日」口径并存）。

    ## 为什么需要它（2026-11-03 用户提的）

    刷课常在夜里连续跑，**跨零点**时用户会觉得「今日 2 节」不对
    （实测：22:44 开跑，1301 学完于 23:20 算 11-02，1302/1303 算 11-03 → 显示 2 节，
    但用户心里是「我这一趟学了 3 节」）。

    ## 为什么**记账**不能改成会话口径

    额度是安全刹车，改成「整趟记一天」会**虚高额度** → 可能真的超刷 → 账号风险：
    - 全记开始日：11-02 那天的额度只扣了后半天学的，早上的额度白给 → 11-02 还能再刷 10 节；
    - 全记结束日：11-02 学的 1 节记到 11-03 → 11-02 一节没扣 → 更危险。

    所以**记账仍按墙钟日**（`today_totals`），这里只提供**展示口径**。

    ## 安全性

    近 24 小时 ⊇ 今日（按墙钟日）→ 展示的「已学」只会**偏多**，
    换算成剩余额度就是**偏保守**，不会让人多刷。

    ⚠ 24 小时是滑动窗口，与「自然日」不同口径，**不可直接相加**（同一节可能两边都算到）。
    """
    hours = max(1, int(hours or 24))
    now = datetime.now()
    since = now.timestamp() - hours * 3600
    keys: set[tuple[str, str]] = set()
    ids: set[str] = set()
    minutes = 0.0
    for row in read_rows(path):
        if account and row.get("account") != account:
            continue
        try:
            at = datetime.fromisoformat(str(row.get("at") or ""))
        except ValueError:
            continue
        if at.timestamp() < since:
            continue
        row_course = str(row.get("course") or "")
        if row.get("result") == "done":
            keys.add((row_course, str(row.get("section_id"))))
            ids.add(str(row.get("section_id")))
        try:
            minutes += float(row.get("minutes") or 0)
        except (TypeError, ValueError):
            continue
    return {"hours": hours, "sections": len(keys), "minutes": round(minutes, 1),
            "section_ids": sorted(ids)}


def batch_totals(path: str | Path | None = None, accounts: list[str] = (),
                 day: str | None = None, hours: int = 24) -> dict[str, dict]:
    """**一次**读全量账本、按账号聚合「今日 + 近 N 小时」（面板卡片用）。

    为什么（2026-11-08 T4）：`app._account_cards` 原来对每个账号各调
    `today_totals` + `recent_totals`，而两者都各自 `read_rows()` —— 把整个
    jsonl 读进内存逐行解析。这是 O(账号数 × 行数)：100 账号 × 每 4 秒轮询
    = 每 4 秒 200 次全量读，面板会卡。这里**只读一次**、单遍按账号分桶，
    复杂度降到 O(行数)。

    返回 `{account: {"today": {...}, "recent": {...}}}`，两个子结构与
    `today_totals` / `recent_totals` 的返回**逐字段同口径**：今日认 `date`、
    近 N 小时认 `at`；去重键 `course + section_id`；分钟数解析失败的行
    跳过分钟累计但节数照算（与单账号版一致）。
    """
    day = day or _today()
    hours = max(1, int(hours or 24))
    since = datetime.now().timestamp() - hours * 3600
    labels = [str(a) for a in accounts]
    buckets: dict[str, dict] = {
        a: {"t_keys": set(), "t_ids": set(), "t_min": 0.0, "t_pc": {},
            "r_keys": set(), "r_ids": set(), "r_min": 0.0}
        for a in dict.fromkeys(labels)          # 去重但保持顺序
    }
    for row in read_rows(path):
        acc = row.get("account")
        if acc not in buckets:
            continue
        b = buckets[acc]
        # —— 今日（墙钟日，额度口径）
        if row.get("date") == day:
            row_course = str(row.get("course") or "")
            if row.get("result") == "done":
                b["t_keys"].add((row_course, str(row.get("section_id"))))
                b["t_ids"].add(str(row.get("section_id")))
            try:
                mins = float(row.get("minutes") or 0)
            except (TypeError, ValueError):
                continue
            b["t_min"] += mins
            pc = b["t_pc"].setdefault(row_course, {"sections": set(), "minutes": 0.0})
            pc["minutes"] += mins
            if row.get("result") == "done":
                pc["sections"].add(str(row.get("section_id")))
        # —— 近 N 小时（滑动窗口，展示口径）
        try:
            at = datetime.fromisoformat(str(row.get("at") or ""))
        except ValueError:
            continue
        if at.timestamp() < since:
            continue
        if row.get("result") == "done":
            b["r_keys"].add((str(row.get("course") or ""), str(row.get("section_id"))))
            b["r_ids"].add(str(row.get("section_id")))
        try:
            b["r_min"] += float(row.get("minutes") or 0)
        except (TypeError, ValueError):
            continue
    return {
        a: {
            "today": {
                "date": day, "account": a, "course": None,
                "sections": len(b["t_keys"]),
                "minutes": round(b["t_min"], 1),
                "section_ids": sorted(b["t_ids"]),
                "courses": {k: {"sections": len(v["sections"]),
                                "minutes": round(v["minutes"], 1)}
                            for k, v in sorted(b["t_pc"].items())},
            },
            "recent": {
                "hours": hours,
                "sections": len(b["r_keys"]),
                "minutes": round(b["r_min"], 1),
                "section_ids": sorted(b["r_ids"]),
            },
        }
        for a, b in buckets.items()
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
