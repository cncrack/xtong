"""连续播多节（无人值守）：挑下一节 → 播到达标 → 停表离开 → 歇一会儿 → 下一节。

用法（面板已开、Chrome 已登录、9222 在）：

    .venv\\Scripts\\python.exe scripts/run_gxks.py                  # 默认连播 3 节
    .venv\\Scripts\\python.exe scripts/run_gxks.py --max-sections 6 --max-total-hours 8
    .venv\\Scripts\\python.exe scripts/run_gxks.py --sections 1291,1292   # 指定顺序

与 `watch_gxks.py` 的分工：那边守**一节**、适合盯现场；这边负责**串起来跑**，适合离开电脑。
每节仍走同一个 `GxksDriver.watch()`（同样的达标判定 C4/C11、重建恢复 C7、离开播放页 C10）。

安全边界（无人值守时必须守住，宁可停也不硬闯）：

- **掉登录**（`session_lost`）→ 立即停，返回码 4，等人工重登；不做自动重登（T2 未做）。
- **任何非 `done` 收场**（超时 / 反复 pause / 反复未知）→ 立即停，返回码 1，因为它意味着
  “页面在但学时不涨”，继续换节只会把问题带下去、浪费时间。
- **总时长上限** `--max-total-hours`：防止异常情况下通宵跑。
- 每节之间随机歇 25~60 秒：既是拟人化，也给服务端最后一次上报留出落账时间。

日志：每节一个 `data/debug/watch_gxks_<标签>_<节ID>_<时间戳>.jsonl`（gitignore 内）。
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import watch_gxks as wg  # noqa: E402  复用连浏览器 / 事件打印

from xxt.adapters.gxks import driver as gdriver  # noqa: E402
from xxt.adapters.gxks import selectors  # noqa: E402


def ensure_list(page, drv, tries: int = 3):
    """确保拿到列表行：掉登录返回 "login"，拿不到返回 []。

    上一节达标后会 `leave_player()` 导航到 `about:blank`，列表帧随之消失，
    所以每节开始前都要**回到学习入口页**再读。
    """
    for _ in range(tries):
        if gdriver.at_login_page(page.url):
            return "login"
        if drv.list_frame() is None:
            try:
                page.goto(selectors.MAIN_PAGE, wait_until="commit", timeout=60000)
                page.wait_for_timeout(3000)
            except Exception:                              # noqa: BLE001  网络抖动交给下一轮重试
                time.sleep(2)
                continue
        rows = drv.read_page(min_rows=1, tries=6)["rows"]
        if rows:
            return rows
    return []


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-sections", type=int, default=3, help="本次最多播几节")
    ap.add_argument("--max-total-hours", type=float, default=10.0, help="总时长上限（小时）")
    ap.add_argument("--sections", default=None, help="逗号分隔的节 ID 列表；缺省自动挑")
    ap.add_argument("--interval", type=float, default=30.0, help="巡检间隔秒")
    ap.add_argument("--max-hours", type=float, default=2.0, help="单节兜底超时（小时）")
    ap.add_argument("--progress-every", type=float, default=300.0, help="回读列表间隔秒")
    ap.add_argument("--margin", type=int, default=gdriver.OVERRUN_MARGIN_MINUTES,
                    help="超跑余量：学时 ≥ 要求+余量 才停")
    ap.add_argument("--gap-min", type=float, default=25.0, help="节间最短歇息秒")
    ap.add_argument("--gap-max", type=float, default=60.0, help="节间最长歇息秒")
    ap.add_argument("--port", type=int, default=9222, help="Chrome 调试端口")
    ap.add_argument("--label", default="", help="日志标签（如 甲）；不得写真实姓名")
    args = ap.parse_args()

    started = time.monotonic()
    browser, page = wg.connect_page(f"http://127.0.0.1:{args.port}")
    if page is None:
        print("[FAIL] 找不到含列表帧的页面（Chrome 是否已登录？）")
        return 2
    drv = gdriver.GxksDriver(page)

    wanted = [s.strip() for s in args.sections.split(",")] if args.sections else None
    tag = f"{args.label}_" if args.label else ""
    finished: list[dict] = []

    print(f"=== 连续播放开始 {datetime.now():%H:%M:%S}"
          f"（最多 {args.max_sections} 节 / {args.max_total_hours:.1f} 小时）===")

    code = 0
    for idx in range(1, args.max_sections + 1):
        spent_h = (time.monotonic() - started) / 3600
        if spent_h >= args.max_total_hours:
            print(f"\n[STOP] 已达总时长上限 {args.max_total_hours:.1f} 小时，收工")
            break

        state = ensure_list(page, drv)
        if state == "login":
            print("\n[DYING] 页面已落到登录页 → 停。请在该 Chrome 窗口重新登录后重跑本命令")
            code = 4
            break
        if not state:
            print("\n[FAIL] 列表读不到行 → 停")
            code = 2
            break
        rows = state

        if wanted:
            row = next((r for r in rows if str(r["platform_chapter_id"]) in wanted), None)
            if row is None:
                print("[DONE] 指定节都不在列表里了")
                break
            wanted.remove(str(row["platform_chapter_id"]))
        else:
            row = gdriver.pick_next_section(rows)
            if row is None:
                print("\n[DONE] 列表里没有未完成的节了")
                break
        if gdriver.section_status(row) == "done":
            print(f"[SKIP] {row['platform_chapter_id']} 已达标，跳过")
            continue

        section = str(row["platform_chapter_id"])
        target = row["required_minutes"] + args.margin
        print(f"\n----- 第 {idx} 节 {section} {row['name']}：{row['done_minutes']}/{row['required_minutes']}"
              f"（{row['state']}）；达标线 {target} -----")

        if not drv.open_section(section):
            print("[FAIL] 打不开播放页 → 停")
            code = 2
            break
        drv.mute()
        if drv.sample_player().get("paused") is not False:
            print("开播:", drv.play())

        log_path = Path("data/debug") / f"watch_gxks_{tag}{section}_{datetime.now():%Y%m%d-%H%M%S}.jsonl"
        on_event, close_log = wg.make_printer(log_path)
        out = drv.watch(section, interval=args.interval, max_seconds=args.max_hours * 3600,
                        read_progress_every=args.progress_every, margin_minutes=args.margin,
                        on_event=on_event)
        close_log()

        final = out.get("last_row") or {}
        print(f"结果 {out['result']}；用时 {out['seconds'] / 60:.1f} 分钟；"
              f"重载 {out['reloads']} / 重建 {out['rebuilds']}；列表 "
              f"{final.get('done_minutes')}/{final.get('required_minutes')} {final.get('state')}")

        if out["result"] == "session_lost":
            print("⚠ 会话失效（Q20）→ 停。重登后重跑本命令即从下一节续播")
            code = 4
            break

        # C10：**任何收场都离开播放页**（不只达标）——`ended`/`escalate` 时计时链还挂着
        before = drv.sample_player().get("paused")
        drv.pause()
        page.wait_for_timeout(1500)
        after = drv.sample_player().get("paused")
        print(f"停表检查：{section} paused {before} → {after}")
        drv.leave_player()

        if out["result"] != "done":
            print("⚠ 本节非正常收场（学时不涨）→ 停，不再换节硬跑")
            code = 1
            break

        finished.append({"section": section, "name": row["name"], "done": final.get("done_minutes")})

        if idx < args.max_sections:
            gap = random.uniform(args.gap_min, args.gap_max)
            print(f"歇 {gap:.0f} 秒再切下一节 …")
            time.sleep(gap)

    print("\n=== 本次汇总 ===")
    if finished:
        for f in finished:
            print(f"  ✅ {f['section']} {f['name']} → {f['done']} 分钟")
    else:
        print("  （没有完成任何节）")
    print(f"总耗时 {(time.monotonic() - started) / 60:.1f} 分钟")
    browser.close()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
