"""多账号串行调度（无人值守）：读账号表 → 逐账号查额度 → 逐账号调 `run_gxks.py`。

为什么是**串行**（并发 1）：PLAN-GXKS D12 —— 同一出口 IP 上多账号同时刷，是最容易被
风控关联的特征；`--max-rounds` 轮转（10 分钟一轮）已能把多账号摊开，不需要并发。

分工：

- `run_gxks.py`：负责**一个**账号（挑节 → 播 → 记账 → 歇 → 下一节）；
- 本脚本：负责**账号与课程的外层循环**、额度预检、失败隔离、汇总。

失败隔离：任何一个账号返回非 0（掉登录 / 读不到列表 / 学时不涨），只记下游、**不影响**
后面的账号——这样一个人掉线不会拖住所有人。

用法：

    .venv\\Scripts\\python.exe scripts/run_gxks_multi.py --dry-run    # 只看计划
    .venv\\Scripts\\python.exe scripts/run_gxks_multi.py              # 正式跑
    .venv\\Scripts\\python.exe scripts/run_gxks_multi.py --only jia   # 只跑一个
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from xxt.adapters.gxks import accounts as gaccounts  # noqa: E402
from xxt.adapters.gxks import ledger as gledger  # noqa: E402

RUN_SCRIPT = ROOT / "scripts" / "run_gxks.py"
CHROME_HINT = ('chrome.exe --remote-debugging-port={port} '
               '--user-data-dir="{profile}"')


def port_alive(port: int, timeout: float = 2.0) -> bool:
    """CDP 端口是否在线（只读 `/json/version`，不碰页面）。"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version",
                                    timeout=timeout) as r:
            return 200 <= getattr(r, "status", 200) < 300
    except Exception:                                      # noqa: BLE001
        return False


def run_one(acc: gaccounts.Account, *, course: int | None, max_sections: int,
            max_total_hours: float) -> int:
    """调子进程跑一个账号（可选一门课），实时带前缀转发输出。返回退出码。"""
    cmd = [sys.executable, str(RUN_SCRIPT),
           "--port", str(acc.port),
           "--label", acc.label,
           "--account-label", acc.label,
           "--max-sections", str(max_sections),
           "--max-total-hours", f"{max_total_hours:g}"]
    if course is not None:
        cmd += ["--course", str(course)]
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.Popen(cmd, cwd=str(ROOT), env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace")
    assert proc.stdout is not None
    for line in proc.stdout:
        print(f"[{acc.label}] {line.rstrip()}", flush=True)
    return proc.wait()


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--accounts", default=str(gaccounts.default_path()), help="账号表路径")
    ap.add_argument("--only", default="", help="只跑指定代号")
    ap.add_argument("--max-sections", type=int, default=0,
                    help="每账号本次最多几节（0=按日额度自动算）")
    ap.add_argument("--max-total-hours", type=float, default=6.0, help="每账号单次总时长上限")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不跑")
    args = ap.parse_args()

    accounts = gaccounts.load_accounts(args.accounts)
    if not accounts:
        print(f"[FAIL] 账号表为空：{args.accounts}\n"
              f"  要么建账号表（见 docs/gxks/PLAN-GXKS.md），"
              f"要么保留 data/account.local.json 走单账号")
        return 2
    if args.only:
        accounts = [a for a in accounts if a.label == args.only.strip().lower()]
        if not accounts:
            print(f"[FAIL] 账号表里没有 {args.only}")
            return 2

    print(f"=== 多账号调度 {datetime.now():%H:%M:%S} ===")
    print(f"账号：{gaccounts.summary(accounts)}")

    plan: list[tuple[gaccounts.Account, int | None, int]] = []
    for acc in accounts:
        if not acc.enabled:
            print(f"- {acc.label}：已停用，跳过")
            continue
        q = gledger.quota_left(acc.label, sections_limit=acc.daily_sections,
                               minutes_limit=acc.daily_minutes)
        if q["exhausted"]:
            print(f"- {acc.label}：今日额度已用完（{q['sections']} 节 / "
                  f"{q['minutes']:.0f} 分钟），跳过")
            continue
        alive = port_alive(acc.port)
        if not alive:
            hint = CHROME_HINT.format(port=acc.port, profile=acc.profile or "<该账号专用目录>")
            print(f"- {acc.label}：: {acc.port} 无 CDP 响应 → 跳过\n    先这样起浏览器：{hint}")
            continue
        want = min(args.max_sections or q["left_sections"], q["left_sections"])
        if want <= 0:
            print(f"- {acc.label}：剩余额度不足一节，跳过")
            continue
        courses = list(acc.courses) if acc.courses else [None]
        for course in courses:
            plan.append((acc, course, want))
            label = f"课程{course}" if course is not None else "当前课程"
            print(f"- {acc.label}（:{acc.port}）：{label}，计划 {want} 节"
                  f"（今日已 {q['sections']} 节 / {q['minutes']:.0f} 分钟）")

    if not plan:
        print("\n没有可跑的账号 → 收工")
        return 0
    if args.dry_run:
        print("\n[dry-run] 不执行。去掉 --dry-run 才真跑。")
        return 0

    results: list[dict] = []
    for i, (acc, course, want) in enumerate(plan, 1):
        print(f"\n===== [{i}/{len(plan)}] {acc.label} "
              f"{('课程' + str(course)) if course is not None else '当前课程'} =====")
        # 每门课开跑前重算额度（上一门可能已消耗）
        q = gledger.quota_left(acc.label, sections_limit=acc.daily_sections,
                               minutes_limit=acc.daily_minutes)
        n = min(want, q["left_sections"])
        if n <= 0 or q["exhausted"]:
            print(f"  {acc.label} 额度已用完，跳过这门课")
            continue
        code = run_one(acc, course=course, max_sections=n,
                       max_total_hours=args.max_total_hours)
        results.append({"label": acc.label, "course": course, "code": code})
        if code != 0:
            print(f"  ⚠ {acc.label} 退出码 {code}（掉登录/读不到列表/学时不涨）→ "
                  f"记下，继续下一个账号")

    print("\n=== 调度汇总 ===")
    for r in results:
        tag = "OK" if r["code"] == 0 else f"FAIL({r['code']})"
        course = f"课程{r['course']}" if r["course"] is not None else "当前课程"
        print(f"  {r['label']:12s} {course:10s} {tag}")
    left = [a.label for a in accounts if not gledger.quota_left(
        a.label, sections_limit=a.daily_sections,
        minutes_limit=a.daily_minutes)["exhausted"]]
    if left:
        print(f"  今日额度未用完：{', '.join(left)}")
    print(f"总耗时：{datetime.now():%H:%M:%S}（整个批次）")
    return 0 if all(r["code"] == 0 for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
