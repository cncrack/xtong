"""守一节：盯到学时达标为止（保活 / 重建恢复 / 回读判定 / 达标即停）。

用法（面板已开、Chrome 已登录、9222 在）：

    .venv\\Scripts\\python.exe scripts/watch_gxks.py                # 自动挑下一个未完成的节
    .venv\\Scripts\\python.exe scripts/watch_gxks.py --section 1290  # 指定节
    .venv\\Scripts\\python.exe scripts/watch_gxks.py --max-hours 1.5 # 兜底超时

多账号（一账号 = 一份 profile = 一个 Chrome 实例 = 一个调试端口）时，每个账号各跑一个本脚本：

    .venv\\Scripts\\python.exe scripts/watch_gxks.py --port 9301 --label 甲    # 甲同学的窗口
    .venv\\Scripts\\python.exe scripts/watch_gxks.py --port 9302 --label 乙    # 乙同学的窗口

`--label` 只用来给日志命名、区分输出，**不得写真实姓名**（AGENTS.md：代号制）。

判定与动作全部来自 `xxt.adapters.gxks.driver` 的纯函数（`classify/plan_action/section_status`），
本脚本只负责：连浏览器、挑节、打日志、收尾。约束见 PLAN-GXKS §11.13 C1~C9，要点：

- 只认**列表回读**判达标（C4）；页面秒表只用来判断“还在不在走”；
- 媒体重建 → **重载播放页**（C7/Q13），不是继续挂；
- 反复被判 paused → 拉回最多 `MAX_PAUSE_RECOVERIES` 次就转人工（C2）；
- **达标立即停表**（C9：学时按累计播放时长计，多播就是异常数据）；
- 断网不报错、但会停账：`no_report` 事件 = 超过 75 秒没有任何上报，需要人工看网络（Q16）。

日志：`data/debug/watch_gxks_<标签>_<节ID>_<时间戳>.jsonl`（gitignore 内，不进库）。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

from xxt.adapters.gxks import driver as gdriver
from xxt.adapters.gxks import selectors

CDP = "http://127.0.0.1:9222"
LOUD_KINDS = {"give_up", "progress", "rebuild", "hidden", "no_report", "margin_reached"}
QUIET_STATES = {"ok"}


def connect_page(cdp: str = CDP):
    browser = sync_playwright().start().chromium.connect_over_cdp(cdp)
    for ctx in browser.contexts:
        for page in ctx.pages:
            if any(selectors.LIST_PATH in (f.url or "") for f in page.frames):
                return browser, page
    return browser, None


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--section", default=None, help="节 ID；缺省自动挑下一个未完成的")
    ap.add_argument("--interval", type=float, default=30.0, help="巡检间隔秒")
    ap.add_argument("--max-hours", type=float, default=2.0, help="兜底超时（小时）")
    ap.add_argument("--progress-every", type=float, default=300.0, help="回读列表间隔秒")
    ap.add_argument("--margin", type=int, default=gdriver.OVERRUN_MARGIN_MINUTES,
                    help="超跑余量：学时 ≥ 要求+余量 才停（默认 2 分钟，不要“刚刚好”）")
    ap.add_argument("--port", type=int, default=9222, help="Chrome 调试端口（多账号时一账号一个）")
    ap.add_argument("--label", default="", help="日志标签（如 甲/帐户1）；不得写真实姓名")
    args = ap.parse_args()

    browser, page = connect_page(f"http://127.0.0.1:{args.port}")
    if page is None:
        print("[FAIL] 找不到含列表帧的页面（Chrome 是否已登录？）")
        return 2
    drv = gdriver.GxksDriver(page)

    rows = drv.read_page(min_rows=1, tries=6)["rows"]
    if not rows:
        print("[FAIL] 列表读不到行")
        return 2

    section = args.section
    if section is None:
        frame = drv.player_frame()
        playing = None
        if frame is not None:
            for r in rows:
                if f"Id={r['platform_chapter_id']}" in (frame.url or ""):
                    playing = r
                    break
        # 播放帧里挂着的那节如果**已经达标**，绝不能选它（C9）——
        # 否则每次自动跑都会因为“上一节的播放帧还在”而 SKIP 退出。退回到“下一个未完成”。
        if playing is not None and gdriver.section_status(playing) == "done":
            print(f"播放帧里是已达标的 {playing['platform_chapter_id']}，改挑下一个未完成节")
            playing = None
        picked = playing or gdriver.pick_next_section(rows)
        if picked is None:
            print("列表里没有未完成的节了")
            return 0
        section = str(picked["platform_chapter_id"])

    row = next((r for r in rows if str(r["platform_chapter_id"]) == str(section)), None)
    if row is None:
        print(f"[FAIL] 列表里找不到节 {section}")
        return 2
    status = gdriver.section_status(row)
    target = row["required_minutes"] + args.margin
    print(f"目标节 {row['name']}：{row['done_minutes']}/{row['required_minutes']} 分钟，状态 {row['state']}（{status}）；"
          f"达标线 {target} 分钟（要求 + 余量 {args.margin}）")
    if status == "done":
        # C9：已达标节再播只会把学时堆成异常数据
        print("[SKIP] 该节已达标，不播。换一节或指定别的 --section。")
        return 3

    frame = drv.player_frame()
    if frame is None or f"Id={section}" not in (frame.url or ""):
        print(f"打开 {section} …")
        if not drv.open_section(section):
            print("[FAIL] 打不开播放页")
            return 2
    drv.mute()
    if drv.sample_player().get("paused") is not False:
        print("开播:", drv.play())

    tag = f"{args.label}_" if args.label else ""
    log_path = Path("data/debug") / f"watch_gxks_{tag}{section}_{datetime.now():%Y%m%d-%H%M%S}.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fh = log_path.open("a", encoding="utf-8")
    last = {"state": None}

    def on_event(ev: dict) -> None:
        fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
        fh.flush()
        kind = ev.get("kind")
        # 只打印有意义的变化，避免每 30 秒刷一行
        if kind in LOUD_KINDS or (kind not in QUIET_STATES and kind != last["state"]):
            stamp = datetime.now().strftime("%H:%M:%S")
            extra = ""
            if kind == "progress" and ev.get("row"):
                r = ev["row"]
                extra = f" {r.get('done_minutes')}/{r.get('required_minutes')} {r.get('state')}"
            elif kind == "no_report":
                extra = f" 已 {ev.get('stale')}s 无上报（updates={ev.get('updates')}）→ 查网络，学时没在入账"
            elif kind == "margin_reached":
                extra = (f" 已 {ev.get('done')}/{ev.get('need')} 分钟 ≥ 要求+{ev.get('margin')}"
                         f" → 达标收工（不卡“刚刚好”）")
            print(f"[{stamp}] {kind}{extra}", flush=True)
        last["state"] = kind

    print(f"开始守播（间隔 {args.interval:.0f}s，回读 {args.progress_every:.0f}s，日志 {log_path.name}）")
    out = drv.watch(section, interval=args.interval, max_seconds=args.max_hours * 3600,
                    read_progress_every=args.progress_every, margin_minutes=args.margin,
                    on_event=on_event)

    fh.close()
    final = out.get("last_row") or {}
    print("\n=== 收尾 ===")
    print(f"结果 {out['result']}；用时 {out['seconds'] / 60:.1f} 分钟；重载 {out['reloads']} 次；"
          f"重建 {out['rebuilds']} 次")
    print(f"列表：{final.get('done_minutes')}/{final.get('required_minutes')} {final.get('state')}")
    if out["result"] == "done":
        # C9/C10：达标必须**真正**停住。`pause()` 只保证此刻停了——本站脚本里
        # `visibilitychange` 会 `player.play()`，且媒体重建后播放器会自己恢复，
        # 两者都会把 `m_time` 从原值接着累加（2026-10-29 12:32 实测 43→48）。
        before = drv.sample_player().get("paused")
        drv.pause()
        page.wait_for_timeout(1500)
        after = drv.sample_player().get("paused")
        print(f"停表检查：paused {before} → {after}")
        drv.leave_player()
        print("已离开播放页（计时链销毁）→ 学时不会再多记（C9/C10）")
    browser.close()
    return 0 if out["result"] == "done" else 1


if __name__ == "__main__":
    raise SystemExit(main())
