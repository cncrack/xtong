"""运行账本（本地 jsonl）：日额度与“今天学了哪几节”的唯一依据。

为什么需要它：站点列表**只给累计学时，不给“今天”**，也没有任何接口能查当日进度。
所以“每账号每天最多 N 节 / M 分钟”只能靠本地记账——每跑完一节追加一行，
调度器开跑前先读当日合计，够了就跳过该账号。

三条纪律（诊断脚本三原则的同款要求）：

1. **只追加不重写**：并发/重跑最多多一行，不会把别人的记录覆盖掉；
2. **幂等标记**：同一 `account + section_id + date` 只算一次（重复跑不会重复计额度）；
3. **单条损坏不影响整体**：坏行跳过，不抛异常。

文件放 `data/`（gitignore），只记代号与节 ID / 分钟数，**不记凭据**。
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path

DEFAULT_PATH = Path("data") / "gxks_ledger.jsonl"


def _today() -> str:
    return date.today().isoformat()


def record(path: str | Path = DEFAULT_PATH, *, account: str, section_id: str,
           name: str = "", minutes: float = 0.0, result: str = "done",
           at: datetime | None = None) -> dict:
    """追加一条账；返回写入的记录（便于调用方打日志）。"""
    at = at or datetime.now()
    row = {
        "at": at.isoformat(timespec="seconds"),
        "date": at.date().isoformat(),
        "account": str(account),
        "section_id": str(section_id),
        "name": name,
        "minutes": round(float(minutes), 1),
        "result": result,
    }
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
    return row


def read_rows(path: str | Path = DEFAULT_PATH) -> list[dict]:
    """读全部账；坏行跳过。文件不存在 → 空列表。"""
    p = Path(path)
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


def today_totals(path: str | Path = DEFAULT_PATH, account: str = "",
                 day: str | None = None) -> dict:
    """当日合计：`sections`（去重后的节数）/ `minutes` / `section_ids`。"""
    day = day or _today()
    ids: set[str] = set()
    minutes = 0.0
    for row in read_rows(path):
        if row.get("date") != day:
            continue
        if account and row.get("account") != account:
            continue
        if row.get("result") == "done":
            ids.add(str(row.get("section_id")))
        try:
            minutes += float(row.get("minutes") or 0)
        except (TypeError, ValueError):
            continue
    return {"date": day, "account": account, "sections": len(ids),
            "minutes": round(minutes, 1), "section_ids": sorted(ids)}


def quota_left(account: str, *, sections_limit: int, minutes_limit: int,
               path: str | Path = DEFAULT_PATH, day: str | None = None) -> dict:
    """还剩多少额度（可为 0，不会为负）。调度器据此决定“跳过 / 还能跑几节”。"""
    t = today_totals(path, account, day)
    left_sections = max(0, int(sections_limit) - int(t["sections"]))
    left_minutes = max(0.0, float(minutes_limit) - float(t["minutes"]))
    return {**t, "left_sections": left_sections, "left_minutes": round(left_minutes, 1),
            "exhausted": left_sections <= 0 or left_minutes <= 0}
