"""连续播多节（无人值守）：挑下一节 → 播到达标 → 停表离开 → 歇一会儿 → 下一节。

用法（面板已开、Chrome 已登录、9222 在）：

    .venv\\Scripts\\python.exe scripts/run_gxks.py                  # 默认连播 3 节
    .venv\\Scripts\\python.exe scripts/run_gxks.py --max-sections 6 --max-total-hours 8
    .venv\\Scripts\\python.exe scripts/run_gxks.py --sections 1291,1292   # 指定顺序

与 `watch_gxks.py` 的分工：那边守**一节**、适合盯现场；这边负责**串起来跑**，适合离开电脑。
每节仍走同一个 `GxksDriver.watch()`（同样的达标判定 C4/C11、重建恢复 C7、离开播放页 C10）。

## 账号会话锁（A1，2026-11-07）

启动即抢 `data/run/gxks_<label>.json`；**别的活进程**已持有就 `[FAIL]` 秒退（返回码 5），
绝不双开（`PLAN-GXKS.md` §7.3 硬约束第 1 条：同账号两路心跳是最强机器指纹）。
面板起的任务由面板替它写标记，这里认「pid == 自己或父进程」而不重写。

## 凭据按账号取（2026-11-07 修 P0）

原来这里 `load_credentials()` **无参**，读全局 `data/account.local.json` ——
多账号同时跑时「B 掉登录 → 用 A 的密码登 B」，会把 A 顶号。现在按账号取
（`load_credentials_for`），账号表没填才回落全局旧文件。

安全边界（无人值守时必须守住，宁可停也不硬闯）：

- **掉登录**（`session_lost`）→ 立即停，返回码 4，等人工重登；不做自动重登（T2 未做）。
- **任何非 `done` 收场**（超时 / 反复 pause / 反复未知）→ 立即停，返回码 1，因为它意味着
  “页面在但学时不涨”，继续换节只会把问题带下去、浪费时间。
- **总时长上限** `--max-total-hours`：防止异常情况下通宵跑。
- **节间拟人化**（2026-10-29 用户要求）：默认短歇 45~180 秒，每 `--long-gap-every 3` 节插一次
  4~10 分钟长歇；`--start-jitter` 可让每日开跑时间随机错峰。只在**时间轴**上加随机性，
  不动播放行为（不快进、不跳段、不伪造上报）。短歇同时给服务端最后一次上报留出落账时间。

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

from xxt.adapters.gxks import accounts as gaccounts  # noqa: E402
from xxt.adapters.gxks import browser as gbrowser  # noqa: E402
from xxt.adapters.gxks import driver as gdriver  # noqa: E402
from xxt.adapters.gxks import ledger as gledger  # noqa: E402
from xxt.adapters.gxks import listparse as glist  # noqa: E402
from xxt.adapters.gxks import locks as glocks  # noqa: E402
from xxt.adapters.gxks import login as glogin  # noqa: E402
from xxt.adapters.gxks import netcheck  # noqa: E402
from xxt.adapters.gxks import selectors  # noqa: E402
from xxt.adapters.gxks import sections_cache as gcache  # noqa: E402
from xxt.config import Settings, find_project_root  # noqa: E402


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
    ap.add_argument("--gap-min", type=float, default=45.0, help="节间最短歇息秒")
    ap.add_argument("--gap-max", type=float, default=180.0, help="节间最长歇息秒")
    ap.add_argument("--long-gap-every", type=int, default=3,
                    help="每播几节插入一次长歇（0=禁用；拟人化，像人起来喝水）")
    ap.add_argument("--long-gap-min", type=float, default=240.0, help="长歇最短秒")
    ap.add_argument("--long-gap-max", type=float, default=600.0, help="长歇最长秒")
    ap.add_argument("--start-jitter", type=float, default=0.0,
                    help="开跑前随机等 0~N 秒（每日错峰用，默认 0 不等）")
    ap.add_argument("--port", type=int, default=9222, help="Chrome 调试端口")
    ap.add_argument("--label", default="", help="日志标签（如 甲）；不得写真实姓名")
    ap.add_argument("--account-label", default="",
                    help="账本里的账号代号（用于日额度）；缺省用 --label，再缺省 default")
    ap.add_argument("--course", default=None,
                    help="只跑指定课程 value（列表页 ddlClass）；缺省跑当前课程")
    ap.add_argument("--ignore-quota", action="store_true",
                    help="忽略当日额度（调试用）")
    ap.add_argument("--no-relogin", action="store_true",
                    help="禁用自动重登（即使 data/account.local.json 已配好）")
    ap.add_argument("--no-netcheck", action="store_true",
                    help="跳过开跑前的网络自检（调试用；自检会真实访问站点一次）")
    args = ap.parse_args()

    started = time.monotonic()
    # 账号代号先定：managed 后端的 profile 目录由它决定（一账号一 profile）
    acc_label = args.account_label or args.label or "default"

    # ---- 账号会话锁（A1，2026-11-07）----
    # 放在网络自检**之前**：双开时不该先去访问一次平台（最小访问原则）。
    # 「自己人」判据在 locks 模块里（面板替我写的标记 pid == 父进程，那不算双开）。
    _settings = Settings(root=find_project_root())
    held_by = glocks.holder(_settings.data_dir, acc_label)
    if held_by is not None:
        print(f"[FAIL] 账号 {acc_label} 已有会话在跑（{glocks.describe(held_by)}）→ 不重复开")
        print("       同账号两路心跳是最强的机器指纹，也是 PLAN-GXKS §7.3 的第一条硬约束。")
        print("       要重开请先在面板点「停止」，或确认那个进程已经退出。")
        return 5
    _locked, _holder = glocks.acquire(_settings.data_dir, acc_label,
                                      course=str(args.course or ""))
    if not _locked:                                        # 理论上刚查过，防竞态
        print(f"[FAIL] 账号 {acc_label} 的会话锁被别人抢到"
              f"（{glocks.describe(_holder or {})}）→ 不重复开")
        return 5
    # 收尾一律清锁：本函数有十几处 `return`（掉登录/读不到列表/额度用尽/异常…），
    # 逐个补 `release` 必漏。`atexit` 在解释器正常退出时执行，正好对得上
    # 「本进程不再跑了就该释放」这个语义。进程被杀也不会留下死锁 ——
    # `locks.live()` 会因 pid 不存活而忽略陈旧标记。
    import atexit

    atexit.register(glocks.release, _settings.data_dir, acc_label)

    # ---- 开跑前网络自检（在打开浏览器之前）----
    # 站点每 60 秒上报一次学习进度；链路慢会让学时真丢而页面看起来在播。
    # 这里测的是**直连耗时**——因为 chrome.py 给 Chrome 加了 `--no-proxy-server`，
    # 那个浏览器自己绕过代理，所以"直连慢"就是真的慢。
    if not args.no_netcheck:
        net = netcheck.preflight()
        print(netcheck.format_report(net))
        if not net["ok"]:
            print("\n[SKIP] 网络自检未通过 → 不打开浏览器、不访问平台")
            glocks.release(_settings.data_dir, acc_label)
            return 4
    try:
        session = gbrowser.open_session(Settings(root=find_project_root()),
                                        label=acc_label, port=args.port)
    except gbrowser.SessionError as exc:
        print(f"[FAIL] {exc}")
        if exc.hint:
            print(f"       {exc.hint}")
        glocks.release(_settings.data_dir, acc_label)
        return 2
    page = session.page
    drv = gdriver.GxksDriver(page)

    # ---- 自动注入登录态快照（用户 2026-11-02：「记录登录信息，后期自己登陆」）----
    # 本站 cookie 全是会话级，Chrome 一关就清空；靠 data/storage_state_gxks_<标签>.json
    # 把票注回去。零密码、零平台请求。
    _state_file = gbrowser.state_path(_settings, acc_label)
    _snap = gbrowser.state_summary(_state_file)
    _restored = 0
    if _snap.get("saved"):
        try:
            _restored = gbrowser.restore_cookies(session, _state_file)
            print(f"[OK] 已注入登录态快照（{_restored} 条 cookie，"
                  f"{_snap.get('at', '?')} 存的）")
        except Exception as exc:                              # noqa: BLE001
            print(f"[WARN] 注入登录态失败（{type(exc).__name__}: {exc}）→ 按未登录处理")
    else:
        print("[WARN] 没有登录态快照 → 若未登录，请先在面板点「检查会话」存一份")
    if _restored:
        # 注入后**只在本地**判一次：如果还停在登录页，说明票已作废
        try:
            page.goto(selectors.MAIN_PAGE, wait_until="commit", timeout=45000)
            page.wait_for_timeout(2000)
        except Exception:                                     # noqa: BLE001
            pass
        if gdriver.at_login_page(page.url):
            print("[WARN] 注入后仍在登录页 —— 那张票可能已作废，需要在面板「重新登录」")

    wanted = [s.strip() for s in args.sections.split(",")] if args.sections else None
    tag = f"{args.label}_" if args.label else ""
    finished: list[dict] = []
    # 需要在**全部刷完后**统一重读练理解锁状态的节（2026-11-04）：
    # 平台达标后异步开放练习（onclick 从 `alert('请先完成学习！')` 变成 `showExam(<id>)`）。
    # ⚠ **不能在刷课途中重读列表** —— 列表页与播放页抢同一个 iframe，
    # 重读会把播放页冲掉（视频重建、`m_time` 归零）。所以攒起来，结束后一次性做。
    need_practice_check: list[tuple[str, str]] = []

    # ---- 当日额度（本地账本）----
    # 站点只给“累计学时”，查不到“今天”，所以日上限只能靠本地记账（ledger 模块）。
    # 口径（A1）：额度按**账号合计**，多门课共用一个池子；`0` = 该项不限。
    # `acc` 已在上面的「自动重登」处取过（按账号取凭据要用它），这里直接复用。
    secs_limit = acc.daily_sections if acc else gaccounts.DEFAULT_SECTIONS_PER_DAY
    mins_limit = acc.daily_minutes if acc else gaccounts.DEFAULT_MINUTES_PER_DAY
    if not args.ignore_quota:
        q = gledger.quota_left(acc_label, sections_limit=secs_limit, minutes_limit=mins_limit)

        def _fmt(v: int | None) -> str:
            return "不限" if v is None else str(int(v))

        print(f"账号 {acc_label} 今日：已 {q['sections']} 节 / {q['minutes']:.0f} 分钟；"
              f"额度 {_fmt(secs_limit if secs_limit > 0 else None)} 节 / "
              f"{_fmt(mins_limit if mins_limit > 0 else None)} 分钟 → 还剩 "
              f"{_fmt(q['left_sections'])} 节 / "
              f"{_fmt(q['left_minutes'])} 分钟")
        if q["exhausted"]:
            print("[SKIP] 今日额度已用完 → 不跑（明天再继续，或 --ignore-quota 调试）")
            session.close()
            return 0
        # 节数不限（0）时不收敛；限了就收敛到剩余节数
        if q["left_sections"] is not None and args.max_sections > q["left_sections"]:
            print(f"额度限制：本次最多播 {q['left_sections']} 节（原定 {args.max_sections}）")
            args.max_sections = int(q["left_sections"])

    # 自动重登：凭据**按账号**取（账号表 name/pw 优先，缺了才回落全局旧文件）。
    # 占位符/缺失 → None = 不启用（掉线即停）。
    acc = gaccounts.find_by_label(gaccounts.load_accounts(), acc_label)
    cred = (None if args.no_relogin
            else glogin.load_credentials_for(acc, _settings))
    print(f"自动重登：{'已启用（凭据来自本地文件，不回显）' if cred else '未启用（掉线即停，等人工）'}")
    if cred is None and acc is not None and not acc.configured:
        print("       ⚠ 该账号在账号表里没填账号密码，也没有全局 account.local.json —— "
              "多账号时建议在面板给每个账号各填一份，别让两个号共用同一份凭据")

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

    # 拟人化（2026-10-29 用户要求）：节间不再是固定几十秒，改为「每节随机短歇 + 每 N 节一次长歇」。
    # 只在**时间轴**上加随机性，不碰播放行为（不快进、不跳段、不伪造上报）。
    if args.start_jitter > 0:
        jitter = random.uniform(0, args.start_jitter)
        print(f"错峰：随机等 {jitter:.0f} 秒再开播 …")
        time.sleep(jitter)

    code = 0
    course_ready = not args.course        # 只需切一次课程
    # 回写节缓存要用**当前课程 value**（缓存按课分文件：`sections_<代号>_<value>.json`）。
    # 用户没指定 --course 时从页面读当前选中的那门（2026-11-03：不读就会写错文件，
    # 面板读的是 `sections_default_32.json`，脚本却写进 `sections_default.json`，两边永远对不上）。
    course_value = str(args.course or "")
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
        # 多课程：只在第一轮切一次；切不过去就停（不猜切换方式，见 driver.select_course）
        if not course_ready:
            if not drv.select_course(args.course):
                print(f"\n[FAIL] 切不到课程 {args.course}（这个账号可能没有这门课，或站点改了切换方式）→ 停")
                code = 2
                break
            course_ready = True
            print(f"已切到课程 {args.course}")
        if not course_value:
            # 切换后（或本就在这门课时）读当前选中课程 —— 缓存按课分文件，必须对上
            course_value = drv.current_course()
            if course_value:
                print(f"当前课程 value={course_value}（节缓存回写按它分文件）")
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
        # 每次 progress 都**本地回写**节缓存（用户 2026-11-02：节进度要随学习动态更新）。
        # 零平台请求：只改 data/debug/sections_*.json；面板 4 秒轮询自然读到新值。
        # 失败不打断播放（回写只是展示用，真数据在平台与账本里）。
        def _on_event(ev: dict) -> None:
            on_event(ev)
            if ev.get("kind") != "progress":
                return
            r = ev.get("row") or {}
            try:
                # 带上 name/required_minutes/idx/category：新课程的节不在老缓存里时
                # 要靠这些字段**新建**一份缓存行，否则面板永远看不到这节课（2026-11-03）
                gcache.update_row(Path("data/debug"), acc_label, course_value,
                                  section,
                                  done_minutes=r.get("done_minutes"),
                                  state=r.get("state"),
                                  state_text=r.get("state_text"),
                                  # ⚠ `practice_ready` **必须自己算**（2026-11-05 复核发现）：
                                  # `read_row()` 返回的行里**没有** `practice_ready` 字段
                                  # （它只有 `practice_locked` / `exam_id`，`practice_ready`
                                  # 是 `_slim()` 算出来的）→ 直接 `r.get("practice_ready")`
                                  # 永远是 None → `update_row` 跳过不更新 → 缓存里
                                  # 「未解锁」永远不消失（实测 1307 就是这样卡住的）。
                                  practice_ready=glist.practice_ready(
                                      r.get("practice_locked"), r.get("exam_id")),
                                  # 练习解锁状态一起回写（2026-11-04）：
                                  # 平台在达标后**异步**开放练习（onclick 从
                                  # `alert('请先完成学习！')` 变成 `showExam(<id>)`）。
                                  # 不回写这两个字段 → 缓存里「未解锁」永远不消失，
                                  # 而且 `exam_id` 一直是 None → 面板的「做练习」按钮不显示。
                                  practice_locked=r.get("practice_locked"),
                                  exam_id=r.get("exam_id"),
                                  name=str(r.get("name") or row.get("name") or ""),
                                  required_minutes=r.get("required_minutes"),
                                  idx=r.get("idx"),
                                  category=str(r.get("category") or ""))
            except Exception as exc:                          # noqa: BLE001
                print(f"[WARN] 回写节缓存失败（不影响播放）：{type(exc).__name__}")

        # ⚠ 中断也要记账（2026-11-03 用户「计时有没有算有效」）：
        # 原来只有 `result == "done"` 才写台账，中途停掉（Ctrl+C / 面板「停止」）
        # 一分钟都不记 → 用户明明看了 3/38，台帐却一条没有，看起来像"计时无效"。
        # 现在改成 `try/finally`：只要**播过**就补一条 `partial`，分钟数按实际播放时长，
        # 但 `result != "done"` 时**不计入"今日几节"**（节数只认学完的，见 ledger 的
        # `result == "done"` 判定），只累加分钟数 —— 这样额度刹车仍然准。
        _watch_started = time.monotonic()
        try:
            out = drv.watch(section, interval=args.interval,
                            max_seconds=args.max_hours * 3600,
                            read_progress_every=args.progress_every,
                            margin_minutes=args.margin,
                            on_event=_on_event)
        except KeyboardInterrupt:
            played = time.monotonic() - _watch_started
            print(f"\n⚠ 被中断（已播 {played / 60:.1f} 分钟）→ 记一条 partial，不计节数")
            if played >= 30:                       # 半分钟以下不记，避免噪声
                try:
                    gledger.record(account=acc_label, section_id=section,
                                   name=row["name"], minutes=played / 60.0,
                                   result="partial", course=str(args.course or ""))
                    print(f"[OK] 已记 partial：{section} {played / 60:.1f} 分钟（不计入今日节数）")
                except Exception as exc:           # noqa: BLE001
                    print(f"⚠ 记账失败：{exc}")
            raise
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
        # 记账：日额度与“今天学了哪几节”的唯一依据（写失败不打断连播）。
        # `course` 只做统计与去重（A1：额度按账号合计，见 ledger 模块 docstring）。
        try:
            gledger.record(account=acc_label, section_id=section, name=row["name"],
                           minutes=out["seconds"] / 60.0, result="done",
                           course=str(args.course or ""))
        except Exception as exc:                            # noqa: BLE001
            print(f"⚠ 记账失败（不影响播放）：{exc}")

        # **练理解锁状态：不在这里重读**（2026-11-04 我犯的错）。
        #
        # 我原来在这里（每节收工后）回读一次列表，想把平台异步开放的练习解锁状态
        # 同步进缓存。**但列表页和播放页抢同一个 iframe** —— 回读列表 = 把播放页冲掉
        # → 视频重建、`m_time` 归零 → 日志刷 `rebuild` / `paused`。
        # 实测：1307 在 11:00、11:04 各被冲了一次（学时仍上传，但进度被打断）。
        #
        # ⇒ 改成：**全部刷完之后统一重读一次**（那时已经没有播放要保护了）。
        # 需要回读的节记在 `need_practice_check` 里。
        if final.get("practice_locked") is not False:
            need_practice_check.append((section, str(row.get("name") or "")))

        if idx < args.max_sections:
            # 拟人化节奏：默认每 3 节插一次 4~10 分钟长歇；其余节间 45~180 秒随机短歇。
            if args.long_gap_every > 0 and idx % args.long_gap_every == 0:
                gap = random.uniform(args.long_gap_min, args.long_gap_max)
                print(f"长歇 {gap / 60:.1f} 分钟（每 {args.long_gap_every} 节一次）再切下一节 …")
            else:
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
    if not args.ignore_quota:
        q = gledger.quota_left(acc_label, sections_limit=secs_limit, minutes_limit=mins_limit)
        lim = ("不限" if secs_limit <= 0 else secs_limit,
               "不限" if mins_limit <= 0 else mins_limit)
        print(f"今日累计：{q['sections']} 节 / {q['minutes']:.0f} 分钟"
              f"（额度 {lim[0]} 节 / {lim[1]} 分钟）")
    # **全部刷完后**统一重读练理解锁状态（2026-11-04）：
    # 平台达标后异步开放练习，这里回读一次列表把 `practice_locked` / `exam_id`
    # 同步进缓存 → 面板的「做练习」按钮会出现。
    # ⚠ **必须放在最后**：刷课途中重读列表会**冲掉播放页**（视频重建、`m_time` 归零）
    # —— 列表页与播放页抢同一个 iframe。见 devlog `2026-11-04-03` 错误 7。
    if need_practice_check:
        print(f"\n=== 回读练理解锁状态（{len(need_practice_check)} 节）===")
        try:
            page.wait_for_timeout(3000)      # 等平台把练习放出来
            for sid, nm in need_practice_check:
                # ⚠ 用 `find_section([sid])`（逐页找），**不能用 `read_row`**
                # （2026-11-05 复核发现）：`read_row` 只读**当前页**，而这几节可能
                # 分布在不同的页（实测 1306 在第 2 页、1307~1309 在第 3 页）→
                # 当前停在第 3 页时读 1306 会返回 None，静默打印「读不到（跳过）」。
                fresh = drv.find_section([str(sid)])
                if not fresh:
                    print(f"[..] {sid} {nm} 读不到（跳过）")
                    continue
                gcache.update_row(Path("data/debug"), acc_label, course_value, sid,
                                  done_minutes=fresh.get("done_minutes"),
                                  state=fresh.get("state"),
                                  state_text=fresh.get("state_text"),
                                  practice_ready=glist.practice_ready(
                                      fresh.get("practice_locked"), fresh.get("exam_id")),
                                  practice_locked=fresh.get("practice_locked"),
                                  exam_id=fresh.get("exam_id"),
                                  name=str(fresh.get("name") or nm),
                                  required_minutes=fresh.get("required_minutes"),
                                  idx=fresh.get("idx"),
                                  category=str(fresh.get("category") or ""))
                if fresh.get("practice_ready"):
                    print(f"[OK] 练习已解锁：{sid} {nm}（练习 ID {fresh.get('exam_id')}）")
                else:
                    print(f"[..] 练习还没解锁（平台异步开放，稍后再看）：{sid} {nm}")
        except Exception as exc:                              # noqa: BLE001
            print(f"[WARN] 练理解锁重读失败（不影响已完成的学习）：{type(exc).__name__}")

    # 收尾时滚动刷新登录态快照（用户 2026-11-02：「记录登录信息，后期自己登陆」）
    # 票随时可能被服务端换掉；跑完再存一次，下次开跑注入的就是最新那张。
    try:
        _saved = gbrowser.save_state(session, _state_file)
        print(f"[OK] 已刷新登录态快照（{_saved.get('cookies', 0)} 条 cookie）")
    except Exception as exc:                                  # noqa: BLE001
        print(f"[WARN] 刷新登录态快照失败（{type(exc).__name__}）")
    session.close()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
