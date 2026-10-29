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
from xxt.adapters.gxks import login as glogin  # noqa: E402
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
    ap.add_argument("--no-relogin", action="store_true",
                    help="禁用自动重登（即使 data/account.local.json 已配好）")
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

    # 自动重登：只读本地凭据文件（gitignore），占位符/缺失 → None = 不启用（掉线即停）
    cred = None if args.no_relogin else glogin.load_credentials()
    print(f"自动重登：{'已启用（凭据来自本地文件，不回显）' if cred else '未启用（掉线即停，等人工）'}")

    def do_relogin(why: str) -> bool:
        """**只试一次**。验证码闸门与会失败一律停，不循环试（连试 = 撞库特征）。"""
        if cred is None:
            print(f"{why} → 凭据未配置，停")
            return False
        print(f"{why} → 尝试自动重登（只此一次）")
        if not gdriver.at_login_page(page.url):
            try:
                page.goto(selectors.LOGIN_PAGE, wait_until="commit", timeout=60000)
                page.wait_for_timeout(2000)
            except Exception:                              # noqa: BLE001
                print("  打不开登录页 → 停")
                return False
        out = glogin.login(page, cred)
        extra = f"（{out.get('reason')}）" if out.get("reason") else ""
        print(f"  重登结果：{out['result']}{extra}")
        if out["result"] == "need_verify":
            print("  ⚠ 服务端要验证码 → 必须人工登录，脚本不再尝试")
        return out["result"] == "ok"

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
            if do_relogin("页面已落到登录页"):
                state = ensure_list(page, drv)
        if state == "login":
            print("\n[DYING] 仍未登录 → 停。请在该 Chrome 窗口人工登录后重跑本命令")
            code = 4
            break
        if not state:
            print("\n[FAIL] 列表读不到行 → 停")
            code = 2
            break
        # **逐页找节**（Q27）：列表 1/4 共 36 节，只在当前页挑会误报“没有未完成的节”。
        # 找到后 find_section 会停在该节所在页——`read_row` 的回读靠当前页。
        row = drv.find_section(wanted)
        if row is None:
            if wanted:
                print("[DONE] 指定节都不在列表里了")
            else:
                print("\n[DONE] 列表里没有未完成的节了（已翻完所有页）")
            break
        if wanted:
            wanted.remove(str(row["platform_chapter_id"]))
        if gdriver.section_status(row) == "done":
            print(f"[SKIP] {row['platform_chapter_id']} 已达标，跳过")
            continue

        section = str(row["platform_chapter_id"])
        target = row["required_minutes"] + args.margin
        print(f"\n----- 第 {idx} 节 {section} {row['name']}：{row['done_minutes']}/{row['required_minutes']}"
              f"（{row['state']}）；达标线 {target} -----")

        # 已经停在就是这节的播放页 → **不要重点「开始学习」**（那会让播放页重载：
        # `m_time` 归 0、`updates` 归 0，视频按 cookie 续播。学时不会丢（Q12/Q13），
        # 但没必要：重启接手、断点续跑时白换一次会话。
        frame = drv.player_frame()
        if frame is None or f"Id={section}" not in (frame.url or ""):
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
              f"重载 {out['reloads']} / 重建 {out['rebuilds']} / 补播 {out.get('replays', 0)}；列表 "
              f"{final.get('done_minutes')}/{final.get('required_minutes')} {final.get('state')}")

        if out["result"] == "session_lost":
            # 掉登录：重登一次成功后**重新挑节**（本节未完成，下一轮会重新挑到它）
            if do_relogin("守播中掉登录"):
                print("重登成功 → 本节未达标，下一轮重新挑它继续播")
                continue
            print("⚠ 会话失效且未能重登 → 停")
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
