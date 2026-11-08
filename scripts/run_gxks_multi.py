"""多账号调度（无人值守）：读账号表 → 逐账号查额度 → 并发调 `run_gxks.py`。

## 并发口径（2026-11-08 用户拍板 G-4 = A）

三处数字曾经打架：本脚本原 docstring 写死「串行（并发 1）」并引 D12、
面板 11-06 加的并发闸默认 2、PLAN §11.6 配置草案写 10。

**D12 是核实期约束**：同一出口 IP 多账号同时刷，是最容易被风控关联的特征，
所以核实期先串行跑稳。PLAN §7.3.1 明确规定「并发>1 必须先用零风险实验验掉
Q7/C6（多窗口是否都满足 hidden=false）」。该实验 **11-07 已实测通过**
（3 个独立窗口、故意完全重叠、90 秒 hidden 全程 False，见 PLAN L306）
→ **核实期结束**，现在统一成「一个可配的并发数」。

权威来源 = 面板设置 `max_concurrency`（`[runtime] max_concurrency`，可配改，默认 2）：
- 本脚本缺省读它，与面板完全同口径；
- 命令行 `--max-concurrency` 可临时覆盖；
- 传 1 仍可退回串行（B 方案的行为），但**默认不再是 1**。

⚠ 并发会让多个 Chrome 同时起。Q7/C6 已验通过；若实机出现卡顿/风控迹象，
优先把 `max_concurrency` 调小（不要自动降，避免静默改用户配置）。

分工：

- `run_gxks.py`：负责**一个**账号（挑节 → 播 → 记账 → 歇 → 下一节）；
- 本脚本：负责**账号与课程的外层循环**、额度预检、失败隔离、并发控制、汇总。

失败隔离：任何一个账号返回非 0（掉登录 / 读不到列表 / 学时不涨），只记下游、**不影响**
后面的账号——这样一个人掉线不会拖住所有人。

用法：

    .venv\\Scripts\\python.exe scripts/run_gxks_multi.py --dry-run    # 只看计划
    .venv\\Scripts\\python.exe scripts/run_gxks_multi.py              # 正式跑（并发默认读面板设置）
    .venv\\Scripts\\python.exe scripts/run_gxks_multi.py --max-concurrency 1   # 退回串行
    .venv\\Scripts\\python.exe scripts/run_gxks_multi.py --only jia   # 只跑一个
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from xxt.adapters.gxks import accounts as gaccounts  # noqa: E402
from xxt.adapters.gxks import chrome as gchrome  # noqa: E402
from xxt.adapters.gxks import ledger as gledger  # noqa: E402

RUN_SCRIPT = ROOT / "scripts" / "run_gxks.py"
CHROME_HINT = ('chrome.exe --remote-debugging-port={port} '
               '--user-data-dir="{profile}"')

# 端口探活统一用 `gchrome.port_alive`：这里原来有一份自己的副本，
# 而它**没绕过系统代理** —— 代理开着时每个账号白等 2 秒超时（5 个账号 10 秒）。
# 一处定义、多处复用，才不会再出现"改了一边忘了另一边"。
port_alive = gchrome.port_alive


def resolve_concurrency(explicit: int | None) -> int:
    """G-4 A：并发数与面板**同一个权威来源**，默认 2。

    优先级：命令行 `--max-concurrency` > 面板设置 `max_concurrency` > 默认 2。
    钳到 1~20（与 `_concurrency_limit()` 同口径）。读不到 DB 不报错，
    回 2 —— 闸宁严勿松。
    """
    if explicit is not None:
        return max(1, min(20, int(explicit)))
    try:
        from xxt.config import Settings, find_project_root
        from xxt.store import gxks_panel_settings as gps

        settings = Settings(root=find_project_root())
        n = int(gps.load(settings.data_dir / "gxks_questions.db")
                .get("max_concurrency", 2))
        return max(1, min(20, n))
    except Exception:                                      # noqa: BLE001
        return 2


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
    ap.add_argument("--max-concurrency", type=int, default=None,
                    help="同时跑几个账号（1~20）；缺省读面板设置 max_concurrency（默认 2）。"
                         "传 1 = 退回串行")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不跑")
    args = ap.parse_args()
    concurrency = resolve_concurrency(args.max_concurrency)

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
        # `left_sections=None` = 节数不限（账号表写 0）；此时用 --max-sections 或默认 3
        left = q["left_sections"]
        cap = args.max_sections or 3
        want = cap if left is None else min(cap, left)
        if want <= 0:
            print(f"- {acc.label}：剩余额度不足一节，跳过")
            continue
        courses = list(acc.courses) if acc.courses else [None]
        for course in courses:
            # ⚠ 额度按**账号合计**（A1）：多门课共用同一个池子。所以这里每门课都给 `want`，
            # **但真正的节数在下面循环里每门课开跑前用 `quota_left` 重算**（`min(want, left)`）
            # —— 上一门课用掉的额度会当场从下一门扣掉，不会跑成 N 倍。
            plan.append((acc, course, want))
            label = f"课程{course}" if course is not None else "当前课程"
            print(f"- {acc.label}（:{acc.port}）：{label}，计划 {want} 节"
                  f"（今日已 {q['sections']} 节 / {q['minutes']:.0f} 分钟）")

    if not plan:
        print("\n没有可跑的账号 → 收工")
        return 0
    if args.dry_run:
        print(f"\n[dry-run] 不执行。去掉 --dry-run 才真跑。并发数：{concurrency}")
        return 0

    # G-4 A（2026-11-08）：不同**账号**之间并发，同一账号的多门课仍串行。
    # 为什么不能把 plan 里每一项都丢进线程池：一账号 = 一 Chrome = 一 profile，
    # 同账号两门课并发会争同一个浏览器；且「多门课共用一个额度池（A1）」要求
    # 后一门课开跑前按前一门已用掉的重算，并发会算重导致超额。
    # 所以按 label 分组，每组内部维持原来的串行 + 逐课重算额度语义。
    groups: dict[str, list[tuple[gaccounts.Account, int | None, int]]] = {}
    for entry in plan:
        groups.setdefault(entry[0].label, []).append(entry)

    def run_group(label: str, entries: list) -> list[dict]:
        got: list[dict] = []
        for i, (acc, course, want) in enumerate(entries, 1):
            tag = f"课程{course}" if course is not None else "当前课程"
            print(f"\n===== [{label}] {i}/{len(entries)} {tag} =====", flush=True)
            # 每门课开跑前重算额度（上一门可能已消耗）。额度按账号合计，多门课共用。
            q = gledger.quota_left(acc.label, sections_limit=acc.daily_sections,
                                   minutes_limit=acc.daily_minutes)
            n = want if q["left_sections"] is None else min(want, q["left_sections"])
            if n <= 0 or q["exhausted"]:
                print(f"  {acc.label} 额度已用完，跳过这门课", flush=True)
                continue
            code = run_one(acc, course=course, max_sections=n,
                           max_total_hours=args.max_total_hours)
            got.append({"label": acc.label, "course": course, "code": code})
            if code != 0:
                print(f"  ⚠ {acc.label} 退出码 {code}（掉登录/读不到列表/学时不涨）→ "
                      f"记下，继续下一个账号", flush=True)
        return got

    print(f"\n并发数：{concurrency}（不同账号并行；同一账号多门课仍串行）", flush=True)
    results: list[dict] = []
    if concurrency <= 1:
        for label, entries in groups.items():
            results.extend(run_group(label, entries))
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {pool.submit(run_group, label, entries): label
                       for label, entries in groups.items()}
            for fut in as_completed(futures):
                try:
                    results.extend(fut.result())
                except Exception as exc:                 # noqa: BLE001
                    print(f"⚠ {futures[fut]} 组异常：{type(exc).__name__}: {exc}", flush=True)
                    results.append({"label": futures[fut], "course": None, "code": 1})

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
