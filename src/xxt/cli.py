"""命令行入口：xxt init / xxt login / xxt status。"""

from __future__ import annotations

import argparse
import sys

from .config import Settings
from .log import get_logger, setup_logging

log = get_logger(__name__)


def _settings(args: argparse.Namespace) -> Settings:
    s = Settings()
    setup_logging(s.log_dir, verbose=getattr(args, "verbose", False))
    return s


def cmd_init(args: argparse.Namespace) -> int:
    from .store.db import connect

    s = _settings(args)
    s.data_dir.mkdir(parents=True, exist_ok=True)
    conn = connect(s.db_path)
    conn.close()
    log.info("初始化完成")
    print(f"[OK] 数据目录：{s.data_dir}")
    print(f"[OK] 数据库：{s.db_path}")
    return 0


def cmd_login(args: argparse.Namespace) -> int:
    from .adapters import get_adapter

    s = _settings(args)
    adapter = get_adapter(s)
    if adapter.login(s.default_state_path):
        log.info("登录成功，会话已保存：%s", s.default_state_path)
        print(f"[OK] 会话已保存：{s.default_state_path}")
        return 0
    print("[FAIL] 登录超时或未完成，请重新执行 xxt login")
    return 1


def cmd_status(args: argparse.Namespace) -> int:
    from .adapters import get_adapter
    from .store.db import connect

    s = _settings(args)
    adapter = get_adapter(s)
    state, platform_name = adapter.probe_session(s.default_state_path)
    conn = connect(s.db_path)
    try:
        if platform_name:  # 抓到平台用户名即落库（面板"刷的是谁"）
            conn.execute("UPDATE accounts SET platform_username=? WHERE id=1", (platform_name,))
            conn.commit()
        if state == "invalid":  # 会话失效打点（面板"上次失效时间"）
            from .core.accounts import mark_expired

            mark_expired(conn, 1)
    finally:
        conn.close()
    if state == "invalid":
        # 自动重登（§5.1）：有凭据且预算允许则尝试，成功后重探
        from .core import accounts as acc
        from .session.relogin import maybe_auto_relogin

        conn = connect(s.db_path)
        try:
            row = acc.find_account(conn, "1")
            outcome = maybe_auto_relogin(s, conn, row) if row else None
        finally:
            conn.close()
        if outcome == "ok":
            print("[OK] 会话失效已自动重登成功")
            state, _ = adapter.probe_session(s.default_state_path)
        elif outcome is not None:
            print(f"[!!] 自动重登未成功（{outcome}），请人工执行 xxt login")
            return 1
    who = f"（{platform_name}）" if platform_name else ""
    text = {
        "valid": f"[OK] 会话有效{who}",
        "invalid": f"[!!] 会话已失效且无法自动重登（未存凭据），请 xxt login 或 xxt account add 存凭据",
        "no_state": "[..] 尚未登录，请先执行 xxt login",
    }[state]
    print(text)
    return 0 if state == "valid" else 1


def _save_username(conn, adapter, account_id: int = 1) -> None:
    """把页面流程截获的平台昵称落到账号表（面板"刷的是谁"）。"""
    if getattr(adapter, "last_username", None):
        conn.execute(
            "UPDATE accounts SET platform_username=? WHERE id=?",
            (adapter.last_username, account_id),
        )
        conn.commit()


def cmd_courses(args: argparse.Namespace) -> int:
    from .adapters import get_adapter
    from .core.accounts import account_state_path
    from .store.db import connect, utcnow

    s = _settings(args)
    adapter = get_adapter(s)
    conn = connect(s.db_path)
    account_id = _account_id_of(conn, getattr(args, "account", None))
    state_path = account_state_path(s, account_id)
    if not state_path.exists():
        print(f"[FAIL] 账号#{account_id} 尚未登录（无会话文件），先登录再抓课程")
        return 1
    try:
        courses = adapter.fetch_courses(state_path)
    except RuntimeError as exc:
        print(f"[FAIL] {exc}")
        return 1
    try:
        for c in courses:
            conn.execute(
                """
                INSERT INTO courses(account_id, platform_course_id, clazz_id, name, url)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(account_id, platform_course_id)
                DO UPDATE SET clazz_id=excluded.clazz_id,
                              name=excluded.name,
                              url=excluded.url
                """,
                (account_id, c["platform_course_id"], c["clazz_id"], c["name"], c["url"]),
            )
        _save_username(conn, adapter, account_id)
        conn.commit()
        who = conn.execute(
            "SELECT platform_username FROM accounts WHERE id=?", (account_id,)
        ).fetchone()[0] or ""
        rows = conn.execute(
            "SELECT platform_course_id, clazz_id, name FROM courses WHERE account_id=? ORDER BY id",
            (account_id,),
        ).fetchall()
    finally:
        conn.close()
    print(f"账号#{account_id} {who}：共 {len(rows)} 门课程（已入库，时间 {utcnow()}）：")
    for r in rows:
        print(f"  [{r['platform_course_id']}] {r['name']}")
    return 0


def _pick_course(conn, needle: str, account_id: int = 1):
    """按课程名模糊或 courseid 精确找课程行（限定账号）。"""
    row = conn.execute(
        "SELECT * FROM courses WHERE account_id = ? AND (platform_course_id = ? OR name LIKE ?) "
        "ORDER BY id LIMIT 1",
        (account_id, needle, f"%{needle}%"),
    ).fetchone()
    return row


def _account_id_of(conn, needle: str | None) -> int:
    """解析 --account 参数（id/标签，缺省 1=default）。"""
    if not needle:
        return 1
    from .core import accounts as acc

    row = acc.find_account(conn, needle)
    if row is None:
        raise SystemExit(f"[FAIL] 未找到账号：{needle}")
    return row["id"]


def cmd_chapters(args: argparse.Namespace) -> int:
    from .adapters import get_adapter
    from .core.accounts import account_state_path
    from .store.db import connect

    s = _settings(args)
    conn = connect(s.db_path)
    try:
        account_id = _account_id_of(conn, getattr(args, "account", None))
        course = _pick_course(conn, args.course, account_id)
        if course is None:
            print(f"[FAIL] 未找到课程：{args.course}（账号#{account_id}，先跑 xxt courses --account）")
            return 1
        adapter = get_adapter(s)
        try:
            chapters = adapter.fetch_chapters(dict(course), account_state_path(s, account_id))
        except RuntimeError as exc:
            print(f"[FAIL] {exc}")
            return 1
        _save_username(conn, adapter, account_id)
        for ch in chapters:
            conn.execute(
                """
                INSERT INTO chapters(course_id, platform_chapter_id, name, idx, status)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(course_id, platform_chapter_id)
                DO UPDATE SET name=excluded.name, idx=excluded.idx
                """,
                (course["id"], ch["platform_chapter_id"], ch["name"], ch["idx"],
                 "done" if ch["done_on_platform"] else "pending"),
            )
        conn.commit()
        rows = conn.execute(
            "SELECT name, status FROM chapters WHERE course_id=? ORDER BY idx", (course["id"],)
        ).fetchall()
        done = sum(1 for r in rows if r["status"] == "done")
        print(f"《{course['name']}》共 {len(rows)} 个知识点（平台已完成 {done}）：")
        for r in rows:
            mark = "✓" if r["status"] == "done" else " "
            print(f"  [{mark}] {r['name']}")
    finally:
        conn.close()
    return 0


def cmd_learn(args: argparse.Namespace) -> int:
    import random as _random

    from .adapters.chaoxing.study import StudyRunner
    from .core.rotation import pick_courses
    from .store.db import connect

    s = _settings(args)
    conn = connect(s.db_path)
    try:
        if args.course:
            course = _pick_course(conn, args.course)
            if course is None:
                print(f"[FAIL] 未找到课程：{args.course}（先跑 xxt courses）")
                return 1
        else:
            # v0.8 课程轮换：优先级 × 未完成度 加权随机（一次会话主学 1 门）
            picks = pick_courses(conn, _random.Random(), s.rotation, count=1)
            if not picks:
                print("[..] 没有可学的课程（章节库为空或已全部完成，先跑 xxt courses / chapters）")
                return 1
            course = picks[0]
            print(f"[轮换选课] 今日主学：《{course['name']}》")
        runner = StudyRunner(s, s.default_state_path, conn)
        stats = runner.run(dict(course), limit=args.limit, ignore_window=args.ignore_window)
        summary = f"学习会话结束：视频 {stats['videos']} 个；原因：{stats['stop_reason'] or '达到 limit'}"
        get_logger("cli").info("%s", summary)
        print(summary)
        return 0
    finally:
        conn.close()


def cmd_progress(args: argparse.Namespace) -> int:
    import time as _time

    from .store.db import connect

    s = _settings(args)
    conn = connect(s.db_path)
    try:
        sql = """
            SELECT c.name,
                   (SELECT COUNT(*) FROM chapters h WHERE h.course_id = c.id) AS total,
                   (SELECT COUNT(*) FROM chapters h WHERE h.course_id = c.id
                      AND h.status = 'done') AS done
            FROM courses c WHERE c.account_id = 1 ORDER BY c.id
        """
        params: tuple = ()
        if args.course:
            row = _pick_course(conn, args.course)
            if row is None:
                print(f"[FAIL] 未找到课程：{args.course}")
                return 1
            sql = ("SELECT c.name, "
                   "(SELECT COUNT(*) FROM chapters h WHERE h.course_id = c.id) AS total, "
                   "(SELECT COUNT(*) FROM chapters h WHERE h.course_id = c.id AND h.status = 'done') AS done "
                   "FROM courses c WHERE c.id = ?")
            params = (row["id"],)
        rows = conn.execute(sql, params).fetchall()
        today = _time.strftime("%Y-%m-%d")
        today_videos = conn.execute(
            "SELECT COUNT(*) AS c FROM run_logs WHERE event='video-done' AND ts LIKE ?",
            (f"{today}%",),
        ).fetchone()["c"]
        print(f"今日已完成视频：{today_videos} 个")
        for r in rows:
            total = r["total"]
            pct = (r["done"] / total * 100) if total else 0.0
            print(f"  《{r['name']}》 {r['done']}/{total}（{pct:.0f}%）")
    finally:
        conn.close()
    return 0


def cmd_account(args: argparse.Namespace) -> int:
    import getpass

    from .core import accounts as acc
    from .core.crypto import master_key
    from .store.db import connect

    s = _settings(args)
    conn = connect(s.db_path)
    try:
        if args.account_cmd == "add":
            key = master_key(s.data_dir)
            password = getpass.getpass(f"请输入 {args.label} 的密码（输入不回显）：")
            if not password:
                print("[FAIL] 密码为空")
                return 1
            account_id = acc.add_account(
                conn, key, args.label, args.user, password,
                proxy=args.proxy, priority=args.priority or 1.0,
            )
            print(f"[OK] 账号已添加：#{account_id} {args.label}（凭据已加密存储）")
        elif args.account_cmd == "list":
            rows = acc.list_accounts(conn)
            print(f"共 {len(rows)} 个账号：")
            for r in rows:
                over = []
                if r["window_start"]:
                    over.append(f"窗口{r['window_start']}-{r['window_end']}")
                if r["daily_max_minutes"] is not None:
                    over.append(f"{r['daily_max_minutes']}分钟")
                if r["proxy"]:
                    over.append("代理")
                print(f"  #{r['id']} [{r['status']}] {r['label']} "
                      f"优先级{r['priority']}" + (f"｜{'/'.join(over)}" if over else ""))
        elif args.account_cmd == "rm":
            ok = acc.remove_account(conn, args.account)
            print("[OK] 已删除" if ok else f"[FAIL] 未找到账号：{args.account}")
            return 0 if ok else 1
        elif args.account_cmd == "set":
            fields: dict[str, object] = {}

            def put(name: str, val, cast) -> None:
                if val == "" or val is None:
                    return  # 未指定 = 不动
                fields[name] = None if str(val).lower() == "none" else cast(val)  # none = 清除回落

            put("window_start", args.window_start, str)
            put("window_end", args.window_end, str)
            put("start_at", args.start_at, str)
            put("proxy", args.proxy, str)
            put("note", args.note, str)
            put("valid_until", args.valid_until, str)
            put("daily_max_minutes", args.max_minutes, int)
            put("daily_max_tasks", args.max_tasks, int)
            put("priority", args.priority, float)
            if not fields:
                print("[..] 未指定任何修改项")
                return 0
            ok = acc.set_overrides(conn, args.account, **fields)
            print("[OK] 已更新" if ok else f"[FAIL] 未找到账号：{args.account}")
            return 0 if ok else 1
        elif args.account_cmd == "renew":
            days = acc.renew_account(conn, args.account, days=args.days)
            if days is None:
                print(f"[FAIL] 未找到账号：{args.account}")
                return 1
            print(f"[OK] 已续期 {args.days} 天，剩余有效期 {days} 天")
        else:
            print(f"[FAIL] 未知子命令：{args.account_cmd}")
            return 1
    finally:
        conn.close()
    return 0


def cmd_mlearn(args: argparse.Namespace) -> int:
    import asyncio
    import json as _json
    import os as _os

    from .adapters.chaoxing.astudy import run_accounts
    from .core import accounts as acc
    from .store.db import connect

    s = _settings(args)
    conn = connect(s.db_path)
    try:
        course = _pick_course(conn, args.course) if args.course else None
        if course is None:
            print("[FAIL] 未找到课程（mlearn 需显式指定课程）")
            return 1
        if args.accounts:
            rows = []
            for needle in args.accounts.split(","):
                row = acc.find_account(conn, needle.strip())
                if row is None:
                    print(f"[FAIL] 未找到账号：{needle}")
                    return 1
                rows.append(row)
        else:
            rows = [r for r in acc.list_accounts(conn) if r["status"] == "active"]
        if not rows:
            print("[FAIL] 无可用账号（xxt account add / list 查看）")
            return 1
        # 会话标记文件：面板据此显示/停止本会话（跨面板重启仍可见）
        run_dir = s.data_dir / "run"
        run_dir.mkdir(parents=True, exist_ok=True)
        for row in rows:
            (run_dir / f"{row['id']}.json").write_text(_json.dumps({
                "pid": _os.getpid(), "course": course["name"],
                "since": __import__("time").strftime("%H:%M"),
            }), encoding="utf-8")
        results = asyncio.run(run_accounts(s, conn, rows, dict(course),
                                           limit=args.limit,
                                           max_concurrency=args.concurrency))
        for r in results:
            print(f"  [{r.get('account')}] 视频 {r.get('videos', 0)} 个；"
                  f"{r.get('stop_reason') or '完成'}")
            try:  # 会话结束动态入日志（面板"最近日志"可见开始→进展→结束全过程）
                log.info("[账号#%s] session-end｜%s", r.get("account_id", 1),
                         f"{course['name']}｜{r.get('stop_reason') or '完成'}｜视频 {r.get('videos', 0)} 个")
                conn.execute(
                    "INSERT INTO run_logs(ts, level, event, detail, account_id)"
                    " VALUES(?,?,?,?,?)",
                    (__import__("time").strftime("%Y-%m-%dT%H:%M:%S"), "INFO",
                     "session-end",
                     f"{course['name']}｜{r.get('stop_reason') or '完成'}｜视频 {r.get('videos', 0)} 个",
                     r.get("account_id", 1)))
                conn.commit()
            except Exception:
                pass
        return 0
    finally:
        for row in rows:  # 清理本会话标记
            try:
                (s.data_dir / "run" / f"{row['id']}.json").unlink(missing_ok=True)
            except Exception:
                pass
        conn.close()


def cmd_panel(args: argparse.Namespace) -> int:
    import uvicorn

    from .panel.app import create_app

    s = _settings(args)
    app = create_app(s)
    host = s.raw.get("panel", {}).get("host", "127.0.0.1")
    port = int(s.raw.get("panel", {}).get("port", 8300))
    print(f"[OK] 面板启动：http://{host}:{port}（Ctrl+C 停止）")
    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0


def cmd_auto(args: argparse.Namespace) -> int:
    """常驻调度：各账号在计划时刻（start_at 或窗口内随机）自动开始当日学习会话。

    生效约束：账号 active 且在有效期内、已登录、全局/账号窗口、每日额度（会话自然停机）。
    每账号每天只自动启动一次；Ctrl+C 退出守护（已启动的学习进程自行跑到额度停机）。
    """
    import random as _random
    import subprocess as _sp
    import sys as _sys
    import time as _time
    from pathlib import Path as _Path

    from .core import accounts as acc
    from .core.accounts import is_valid
    from .core.rotation import pick_courses
    from .store.db import connect

    s = _settings(args)
    planned: dict[int, str] = {}   # account_id → 今日计划 HH:MM（全天保留供面板显示）
    fired: set[int] = set()        # 今日已触发（含手动已开）
    skipped: set[int] = set()      # 今日跳过（未登录/无课程）
    plan_date = _time.strftime("%Y-%m-%d")
    panel_port = int(s.raw.get("panel", {}).get("port", 8300))
    get_logger("cli").info("auto 守护启动（每 30 秒巡检；Ctrl+C 退出）")
    print("[auto] 守护启动。到点后经面板启动学习会话（面板可见、可 ■ 停止）。Ctrl+C 退出。")

    while True:
        try:
            today = _time.strftime("%Y-%m-%d")
            if today != plan_date:  # 跨日重置
                planned, plan_date = {}, today
            conn = connect(s.db_path)
            try:
                rows = [r for r in acc.list_accounts(conn)
                        if r["status"] == "active" and is_valid(r)]
            finally:
                conn.close()
            for row in rows:
                aid = row["id"]
                if aid in fired or aid in skipped:
                    continue
                if aid not in planned:
                    if row["start_at"]:
                        planned[aid] = row["start_at"]
                    else:
                        ws = row["window_start"] or s.pace["daily_window"][0]
                        try:
                            h, m = map(int, ws.split(":"))
                            off = _random.randint(0, 120)
                            planned[aid] = f"{(h * 60 + m + off) // 60:02d}:{(h * 60 + m + off) % 60:02d}"
                        except Exception:
                            planned[aid] = "09:00"
                    print(f"[auto] 账号#{aid} {row['label']} 今日计划 {planned[aid]} 开刷")
                if _time.strftime("%H:%M") < planned[aid]:
                    continue
                state = s.data_dir / "accounts" / str(aid) / "state.json"
                if not state.exists():
                    skipped.add(aid)
                    print(f"[auto] 账号#{aid} 未登录，今日跳过")
                    continue
                conn = connect(s.db_path)
                try:
                    picks = pick_courses(conn, _random.Random(), s.rotation,
                                         account_id=aid, count=1)
                finally:
                    conn.close()
                if not picks:
                    skipped.add(aid)
                    print(f"[auto] 账号#{aid} 无可学课程（先抓课程/章节），今日跳过")
                    continue
                course_name = picks[0]["name"]
                # 会话统一交面板管理（面板可见/可停）；面板未启动则本轮重试
                try:
                    import httpx as _httpx

                    r = _httpx.post(
                        f"http://127.0.0.1:{panel_port}/api/study/start",
                        json={"account_id": aid, "course": course_name,
                              "source": "定时"}, timeout=10,
                    )
                    ok = bool(r.json().get("ok"))
                    reason = r.json().get("error", "")
                except Exception:
                    ok, reason = False, "面板未响应（保持面板运行）"
                if ok:
                    fired.add(aid)
                    print(f"[auto] 已触发 账号#{aid} {row['label']} →《{course_name}》")
                elif "已有进行中的会话" in str(reason):
                    fired.add(aid)  # 手动已开（面板▶），视为今日已学
                    print(f"[auto] 账号#{aid} 已有会话（手动开的），今日不再触发")
                else:
                    print(f"[auto] 账号#{aid} 触发失败：{reason}（30 秒后重试）")
            # 计划落库：面板"今日定时"显示（auto 分配的时间对用户可见）
            try:
                import json as _json

                conn = connect(s.db_path)
                with conn:
                    conn.execute(
                        "INSERT INTO app_config(key, value) VALUES('auto.plan', ?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (_json.dumps({"date": today, "times": planned}),),
                    )
                conn.close()
            except Exception:
                pass
            _time.sleep(30)
        except KeyboardInterrupt:
            print("\n[auto] 守护退出（学习进程继续自行运行至额度停机）")
            return 0


def cmd_work(args: argparse.Namespace) -> int:
    """P2.5：进入指定账号的章节作业页并作答（默认半自动：只填不交）。"""
    from playwright.sync_api import sync_playwright

    from .adapters.chaoxing.study import find_frame_with, open_chapter_frame
    from .adapters.chaoxing.work import answer_work
    from .core import accounts as acc
    from .core.accounts import account_state_path
    from .store.db import connect

    s = _settings(args)
    conn = connect(s.db_path)
    try:
        account_id = _account_id_of(conn, args.account)
        state = account_state_path(s, account_id)
        if not state.exists():
            print("[FAIL] 该账号未登录")
            return 1
        course = _pick_course(conn, args.course or "知行合一", account_id)
        if course is None:
            print(f"[FAIL] 未找到课程：{args.course}")
            return 1
        ch = conn.execute(
            "SELECT * FROM chapters WHERE course_id=? AND (name LIKE ? OR idx=?) LIMIT 1",
            (course["id"], f"%{args.chapter}%",
             int(args.chapter) if args.chapter.isdigit() else -1)).fetchone()
        if ch is None:
            print(f"[FAIL] 未找到章节：{args.chapter}")
            return 1
        print(f"目标：《{course['name']}》{ch['name']}（kid={ch['platform_chapter_id']}）")

        def _open_work_page(browser):
            from .core.stealth import hardened_context
            context = hardened_context(browser, storage_state=str(state))
            page = context.new_page()
            tree = open_chapter_frame(page, course["url"])
            tree.locator(f"#cur{ch['platform_chapter_id']}").first.click(timeout=15_000)
            work_frame = find_frame_with(page, "div.singleQuesId", timeout_s=25)
            return context, page, work_frame

        with sync_playwright() as p:
            if args.review:  # 判题回读：已提交的作业页读每题得分 → 题库转正/清空
                from .adapters.chaoxing.work import review_work

                browser = p.chromium.launch(headless=True)
                try:
                    context, page, work_frame = _open_work_page(browser)
                    if work_frame is None:
                        print("[FAIL] 未找到作业页")
                        return 1
                    results = review_work(work_frame, conn, account_id)
                    for r in results:
                        print(f"  [{r['status']}] {r['stem'][:36]}（得分 {r['score']}）")
                    context.storage_state(path=str(state))
                finally:
                    browser.close()
                return 0

            # 作答：半自动=有头留窗核对；自动提交=无头
            headed = not args.submit
            browser = p.chromium.launch(headless=headed)
            try:
                context, page, work_frame = _open_work_page(browser)
                if work_frame is None:
                    print("[FAIL] 未找到作业页（该章节可能不是作业任务点）")
                    return 1
                stats = answer_work(work_frame, s, conn, account_id,
                                    course["name"], submit=args.submit,
                                    chapter_name=ch["name"], force=args.force)
                print(f"作业作答：{stats}")
                if stats.get("status") == "semi":
                    import threading

                    print(">> 浏览器已保留：请核对已填答案——满意可直接在该窗口点提交，"
                          "完成后【关闭浏览器窗口】结束本命令。")
                    closed = threading.Event()
                    context.on("close", closed.set)
                    while not closed.is_set() and browser.is_connected():
                        try:
                            page.wait_for_timeout(1500)
                        except Exception:
                            break
                    print(">> 窗口已关闭。若刚提交过：稍后跑 xxt work 账号 章节 --review 回读判题结果。")
                context.storage_state(path=str(state))  # 滚动续期
            finally:
                browser.close()
        return 0
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="xxt", description="网课自动化学习助手（超星学习通）")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="初始化数据目录与数据库")
    sub.add_parser("login", help="打开浏览器人工登录学习通，保存会话")
    sub.add_parser("status", help="检查已保存会话是否有效")
    p_cs = sub.add_parser("courses", help="抓取课程列表并入库（复用页面自身接口）")
    p_cs.add_argument("--account", default=None, help="账号 id/标签（缺省 default）")

    p_ch = sub.add_parser("chapters", help="抓取指定课程的章节树并入库")
    p_ch.add_argument("course", help="课程名（支持模糊）或 courseid")
    p_ch.add_argument("--account", default=None, help="账号 id/标签（缺省 default）")

    p_learn = sub.add_parser("learn", help="自动学习（不带课程名则按轮换策略自动选课）")
    p_learn.add_argument("course", nargs="?", default=None, help="课程名（支持模糊）或 courseid，可省略")
    p_learn.add_argument("--limit", type=int, default=None, help="本次最多播放的视频数")
    p_learn.add_argument("--ignore-window", action="store_true",
                         help="忽略每日学习时段限制（仅限验收测试）")

    p_prog = sub.add_parser("progress", help="查看各课程完成进度与今日学习量")
    p_prog.add_argument("course", nargs="?", default=None, help="课程名或 courseid（可省略）")

    p_acc = sub.add_parser("account", help="多账号池管理（增删/覆盖配置）")
    acc_sub = p_acc.add_subparsers(dest="account_cmd", required=True)
    p_add = acc_sub.add_parser("add", help="添加账号（凭据 AES-GCM 加密存储）")
    p_add.add_argument("label", help="账号备注名（唯一）")
    p_add.add_argument("--user", required=True, help="学习通账号/手机号")
    p_add.add_argument("--proxy", default=None, help="绑定代理（P1 启用，P0 家宽留空）")
    p_add.add_argument("--priority", type=float, default=None, help="课程调度优先级（默认 1.0）")
    acc_sub.add_parser("list", help="列出账号（不显示凭据）")
    p_rm = acc_sub.add_parser("rm", help="删除账号")
    p_rm.add_argument("account", help="账号 id 或备注名")
    p_set = acc_sub.add_parser("set", help="设置账号级覆盖（不设即回落全局默认，传 none 清除）")
    p_set.add_argument("account", help="账号 id 或备注名")
    p_set.add_argument("--window-start", default="", help="覆盖学习窗口开始（如 09:00；none 清除）")
    p_set.add_argument("--window-end", default="", help="覆盖学习窗口结束（如 22:00；none 清除）")
    p_set.add_argument("--start-at", default="", help="覆盖每日启动时刻（P1 调度用）")
    p_set.add_argument("--max-minutes", default="", help="覆盖每日时长额度")
    p_set.add_argument("--max-tasks", default="", help="覆盖每日任务数额度")
    p_set.add_argument("--proxy", default="", help="绑定代理（none 清除）")
    p_set.add_argument("--note", default="", help="备注（none 清除）")
    p_set.add_argument("--valid-until", default="", help="有效期至（YYYY-MM-DD，none 清除=永久）")
    p_set.add_argument("--priority", default="", help="优先级")
    p_renew = acc_sub.add_parser("renew", help="续期（valid_until 延长 N 天，次数自动累计）")
    p_renew.add_argument("account", help="账号 id 或备注名")
    p_renew.add_argument("--days", type=int, default=30, help="续期天数（默认 30）")

    p_ml = sub.add_parser("mlearn", help="多账号并发学习（async，M4 实验性）")
    p_ml.add_argument("course", help="课程名或 courseid")
    p_ml.add_argument("--accounts", default=None, help="账号（id/标签，逗号分隔；缺省=全部 active）")
    p_ml.add_argument("--limit", type=int, default=None, help="每账号本次最多视频数")
    p_ml.add_argument("--concurrency", type=int, default=2, help="全局同时学习账号数上限")

    sub.add_parser("panel", help="启动 Web 管理面板（浏览器查看进度/账号/日志）")
    sub.add_parser("auto", help="常驻调度守护：各账号定时自动刷课（start_at/窗口内随机，额度停机）")

    p_wk = sub.add_parser("work", help="章节作业 AI 作答（P2.5，默认半自动只填不交）")
    p_wk.add_argument("account", help="账号 id/标签")
    p_wk.add_argument("chapter", help="章节关键词（如 2.12）")
    p_wk.add_argument("--course", default="知行合一", help="课程名（缺省知行合一）")
    p_wk.add_argument("--submit", action="store_true",
                      help="自动提交（默认只填不交，人工核对后自提）")
    p_wk.add_argument("--review", action="store_true",
                      help="判题回读：读已提交作业的每题得分，回写题库（答对转正/答错清空）")
    p_wk.add_argument("--force", action="store_true",
                      help="跳过 work_enabled 开关（面板主动触发时使用）")

    args = parser.parse_args(argv)
    handlers = {
        "init": cmd_init,
        "login": cmd_login,
        "status": cmd_status,
        "courses": cmd_courses,
        "chapters": cmd_chapters,
        "learn": cmd_learn,
        "progress": cmd_progress,
        "account": cmd_account,
        "mlearn": cmd_mlearn,
        "panel": cmd_panel,
        "auto": cmd_auto,
        "work": cmd_work,
    }
    try:
        return handlers[args.command](args)
    except KeyboardInterrupt:
        print("\n已中断")
        return 130


if __name__ == "__main__":
    sys.exit(main())
