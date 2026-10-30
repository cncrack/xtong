"""批量补齐画像（串行 + 随机间隔）：把平台 `stu-base-info` 里的
姓名 / 学号 / 学校 / 院系 / 班级 回填到 `account_profiles`。

纪律（用户 2026-10-26 明确要求）：
- **一次访问**：每个账号只打开学习空间一次，复用页面自身发出的 `stu-base-info`（零额外请求）
- **串行不并发**：跑完一个才跑下一个，两个账号之间 `sleep` 随机间隔，模拟人
- **只补空**：已手填的字段不覆盖（由后端 `merge_platform_profile` 保证）
- **不登录**：只复用已有 `state.json`；会话失效就如实报告，绝不自动重登

用法（需面板在跑，走与页面按钮完全相同的后端路径）：
    .venv\\Scripts\\python.exe scripts/fill_profiles.py
    .venv\\Scripts\\python.exe scripts/fill_profiles.py --accounts 1 2 --min-gap 45 --max-gap 90
    .venv\\Scripts\\python.exe scripts/fill_profiles.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
import urllib.request

API = "http://127.0.0.1:8300"
SHOW = ("school", "dept", "cls")  # 只回显这三项（学号等属个人信息，不回显）

# 打的是**本机**面板，必须绕过系统代理：代理（v2rayN/xray）开着时 `urllib` 会把
# `http://127.0.0.1:...` 也发给代理，每次调用白等 timeout（本文件是 20 秒）。
_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _get(url: str) -> dict:
    with _DIRECT.open(url, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def _post(url: str, timeout: int = 180) -> dict:
    req = urllib.request.Request(url, method="POST", data=b"{}",
                                 headers={"Content-Type": "application/json"})
    with _DIRECT.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description="串行补齐账号画像（每号只访问平台一次）")
    ap.add_argument("--accounts", nargs="*", type=int, default=None, help="只跑这些账号 id（默认：所有已登录账号）")
    ap.add_argument("--min-gap", type=int, default=45, help="账号之间的最小间隔秒（默认 45）")
    ap.add_argument("--max-gap", type=int, default=90, help="账号之间的最大间隔秒（默认 90）")
    ap.add_argument("--api", default=API, help=f"面板地址（默认 {API}）")
    ap.add_argument("--dry-run", action="store_true", help="只列出要跑的账号，不访问平台")
    a = ap.parse_args()

    try:
        ov = _get(f"{a.api}/api/overview")
    except Exception as exc:  # 面板没起 / 端口不通
        print(f"[FAIL] 读不到面板概览：{exc}\n       请先启动面板：.venv\\Scripts\\python.exe -m xxt.cli panel")
        return 2

    pool = [x for x in ov.get("accounts", []) if x.get("has_session")]
    if a.accounts:
        want = set(a.accounts)
        pool = [x for x in pool if x["id"] in want]

    if not pool:
        print("[跳过] 没有可跑账号（需已登录：data/accounts/<id>/state.json 存在）")
        return 1

    print(f"计划：{len(pool)} 个账号，串行执行，账号间随机间隔 {a.min_gap}~{a.max_gap}s")
    for x in pool:
        print(f"  - #{x['id']} {x.get('label') or ''}")
    if a.dry_run:
        print("[dry-run] 未访问平台")
        return 0

    done: list[tuple] = []
    for i, x in enumerate(pool):
        if i:  # 账号之间才等（第一个立刻跑）
            gap = random.uniform(a.min_gap, a.max_gap)
            print(f"... 间隔 {gap:.0f}s 后再跑下一个（模拟人，别连环访问）")
            time.sleep(gap)
        aid, label = x["id"], (x.get("label") or "")
        print(f"[{i + 1}/{len(pool)}] 读取 #{aid} {label} …")
        try:
            d = _post(f"{a.api}/api/accounts/{aid}/profile_from_platform")
        except Exception as exc:
            print(f"    [FAIL] 请求失败：{exc}")
            done.append((aid, label, "请求失败", ""))
            continue
        if not d.get("ok"):
            print(f"    [FAIL] {d.get('error') or '未知'}")
            done.append((aid, label, d.get("error") or "失败", ""))
            continue
        merged = d.get("merged") or {}
        filled = merged.get("filled") or []
        skipped = merged.get("skipped") or []
        prof = d.get("profile") or {}
        got = " ".join(f"{k}={prof.get(k) or '—'}" for k in SHOW)
        print(f"    ✔ 回填 {filled or '无'}｜保留手填 {skipped or '无'}｜{got}")
        print(f"      平台返回字段：{'/'.join(d.get('fields') or []) or '（无）'}")
        done.append((aid, label, "回填 " + ("/".join(filled) if filled else "无"), got))

    print("\n=== 汇总 ===")
    for aid, label, res, got in done:
        print(f"#{aid} {label}：{res}{('｜' + got) if got else ''}")
    bad = [r for r in done if "失败" in r[2] or "FAIL" in r[2]]
    if bad:
        print(f"\n⚠ {len(bad)} 个账号失败——若提示会话失效，需你先手动登录该号一次再重跑本脚本")
        return 1
    print("\n全部完成。面板「📈 统计 → 画像设置」可核对；批次课程下拉里的学校名会自动更新。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
