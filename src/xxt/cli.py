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
    from .core.accounts import account_state_path
    from .store.db import connect

    s = _settings(args)
    adapter = get_adapter(s)
    conn = connect(s.db_path)
    try:
        account_id = _account_id_of(conn, getattr(args, "account", None))
    finally:
        conn.close()
    state_path = account_state_path(s, account_id)
    if not adapter.login(state_path):
        print(f"[FAIL] 账号#{account_id} 登录超时或未完成，请重试：xxt login --account {account_id}")
        return 1
    log.info("账号#%s 登录成功，会话已保存：%s", account_id, state_path)
    print(f"[OK] 账号#{account_id} 会话已保存：{state_path}")

    # 登录后**顺带**同步课程列表（2026-10-30 轻档）。
    # 省一次真实访问：`fetch_courses` 与 `xxt status` 的探测访问的是同一个学习空间页、
    # 听的是同一批响应（前者还多截获 courselistdata），分开跑等于白导航一次。
    # 只同步**课程名单**，不抓章节树——哪门课要刷由人决定，没点过的课一次都不碰
    # （最小访问原则；体育课/免修课这类"不需要刷"的课零成本）。
    conn = connect(s.db_path)
    try:
        try:
            courses = adapter.fetch_courses(state_path)
        except RuntimeError as exc:
            # 会话已存住，同步失败不该把登录判成失败（退出码仍 0），给出手动补跑的命令即可
            print(f"[!!] 会话已保存，但课程列表同步失败：{exc}"
                  f"（可稍后重试：xxt courses --account {account_id}）")
            return 0
        for c in courses:
            conn.execute(COURSES_UPSERT, (account_id, c["platform_course_id"],
                                          c["clazz_id"], c["name"], c["url"]))
        _save_username(conn, adapter, account_id)
        conn.commit()
        # 顺带告诉用户有几门还没章节树：新登录的号必然全是，刷之前要先点「⬇ 抓章节」
        no_tree = conn.execute(
            "SELECT COUNT(*) AS c FROM courses a LEFT JOIN chapters h ON h.course_id=a.id"
            " WHERE a.account_id=? AND h.id IS NULL", (account_id,)).fetchone()["c"]
        hint = ""
        if no_tree:
            hint = (f"（其中 {no_tree} 门还没有章节树：刷之前在该课程行点「⬇ 抓章节」，"
                    "进课程页 1 次，不消耗当日额度）")
        print(f"[OK] 课程列表已同步：{len(courses)} 门{hint}")
    finally:
        conn.close()
    return 0


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


# 课程列表入库语句（模块级常量：cmd_login 顺带同步与 cmd_courses 共用同一份 SQL，
# 避免两处各写一套 ON CONFLICT 口径）。课程名单只按平台事实覆盖，不做删除——
# 删课会连带章节树与已学进度，属人工决定（面板「✎ 管理」里做）。
COURSES_UPSERT = """
    INSERT INTO courses(account_id, platform_course_id, clazz_id, name, url)
    VALUES(?, ?, ?, ?, ?)
    ON CONFLICT(account_id, platform_course_id)
    DO UPDATE SET clazz_id=excluded.clazz_id,
                  name=excluded.name,
                  url=excluded.url
"""


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
            conn.execute(COURSES_UPSERT, (account_id, c["platform_course_id"],
                                          c["clazz_id"], c["name"], c["url"]))
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


def _pick_course_in(conn, *, account_ids, name: str | None = None, pid: str | None = None):
    """在**给定账号集合**里找课程行（学习安排路径用）。

    顺序：pid 精确（平台课程ID，跨账号唯一锚点）→ 课名全等 → 课名模糊。
    这样同名多门时优先命中 pid 指定的那门；不同账号的同名课也不会串。
    """
    ids = [int(a) for a in account_ids or []]
    if not ids:
        return None
    ph = ",".join("?" * len(ids))
    if pid:
        row = conn.execute(
            f"SELECT * FROM courses WHERE platform_course_id = ? AND account_id IN ({ph})"
            " ORDER BY account_id LIMIT 1", (pid, *ids)).fetchone()
        if row is not None:
            return row
    if name:
        for sql in (f"SELECT * FROM courses WHERE name = ? AND account_id IN ({ph})"
                    " ORDER BY account_id LIMIT 1",
                    f"SELECT * FROM courses WHERE name LIKE ? AND account_id IN ({ph})"
                    " ORDER BY account_id LIMIT 1"):
            arg = name if "name = ?" in sql else f"%{name}%"
            row = conn.execute(sql, (arg, *ids)).fetchone()
            if row is not None:
                return row
    return None


def _account_id_of(conn, needle: str | None) -> int:
    """解析 --account 参数（id/标签，缺省 1=default）。"""
    if not needle:
        return 1
    from .core import accounts as acc

    row = acc.find_account(conn, needle)
    if row is None:
        raise SystemExit(f"[FAIL] 未找到账号：{needle}")
    return row["id"]


# 章节树入库语句（模块级常量：cmd_chapters 与 tests 共用同一份 SQL，避免测试测的是复刻品）。
#
# 职责边界：本语句只**建行与更新元数据**——新行的 status 取平台当次事实，
# 已存在的行**不在此处改 status**；完成状态的对齐（升级/降级）统一由
# `xxt.core.reconcile.reconcile_local` 一处处理，避免同一规则在两个地方各写一套。
CHAPTERS_UPSERT = """
    INSERT INTO chapters(course_id, platform_chapter_id, name, idx, status)
    VALUES(?, ?, ?, ?, ?)
    ON CONFLICT(course_id, platform_chapter_id)
    DO UPDATE SET name=excluded.name, idx=excluded.idx
"""


def cmd_chapters(args: argparse.Namespace) -> int:
    from .adapters import get_adapter
    from .core.accounts import account_state_path
    from .core.reconcile import guardrails, reconcile_local
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

        if not chapters:
            # 抓到 0 个叶子必须显式留痕（2026-10-30 轻档）：不入库时，库里状态与"从没抓过"
            # 完全同形（面板都是 0/0），用户与下个会话都无从判断这门课到底是真没任务点、
            # 还是页面改版导致解析失效。写成 WARNING 事件进面板日志页。
            import time as _t

            with conn:
                conn.execute(
                    "INSERT INTO run_logs(ts, level, event, detail, account_id) VALUES(?,?,?,?,?)",
                    (_t.strftime("%Y-%m-%dT%H:%M:%S"), "WARNING", "chapters-empty",
                     f"《{course['name']}》章节树解析到 0 个知识点（未入库）"
                     "——要么这门课确实没有任务点，要么页面改版令选择器失效"
                     "（重跑 scripts/recon_course.py 核对）",
                     account_id))
            print(f"[WARN] 《{course['name']}》解析到 0 个知识点，未入库。"
                  "若该课确有视频/作业，多半是页面改版，需重跑侦察脚本核对选择器")
            return 1

        for ch in chapters:
            conn.execute(CHAPTERS_UPSERT, (course["id"], ch["platform_chapter_id"],
                                           ch["name"], ch["idx"],
                                           "done" if ch["done_on_platform"] else "pending"))
        conn.commit()

        if getattr(args, "no_sync", False):
            res: dict[str, list | str] = {"promoted": [], "demoted": [], "demote_blocked": None}
        else:
            # --demote 是一次性授权，覆盖配置 `[reconcile].demote`（默认关）
            res = reconcile_local(
                conn, course["id"], chapters, account_id=account_id,
                course_name=course["name"],
                **guardrails(s, allow_demote=True if args.demote else None))

        rows = conn.execute(
            "SELECT name, status FROM chapters WHERE course_id=? ORDER BY idx", (course["id"],)
        ).fetchall()
        done = sum(1 for r in rows if r["status"] == "done")
        print(f"《{course['name']}》共 {len(rows)} 个知识点（本地记已完成 {done}）：")
        if getattr(args, "no_sync", False):
            print("[..] --no-sync：只更新了名称与序号，已有行的完成状态未对齐")
        if res["promoted"]:
            print(f"[OK] 对账升级 {len(res['promoted'])} 节（待学/已访问→已完成）")
        if res["demoted"]:
            print(f"[OK] 对账降级 {len(res['demoted'])} 节（已完成→待学，以平台标识为准）")
        if res["demote_blocked"]:
            print(f"[WARN] 降级未执行：{res['demote_blocked']}"
                  "（确认要按平台打回可加 --demote，护栏不过仍会拦）")
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
        # 账号级覆盖对 CLI 同样生效：learn 固定用 #1 的会话文件，故按 #1 的账号行合并。
        # 2026-10-25 修：此前 StudyRunner 直接吃 settings.quota/pace，完全忽略账号管理里的设置。
        from .core.accounts import effective_pace, effective_quota, find_account
        from .core.runmark import clear_marker, live_session, write_marker

        # 同账号单进程：learn 固定用 #1，启动前先看有没有别的会话（面板/定时/另开的 CLI）。
        # 2026-10-25 修：此前 learn 不写标记，面板看不见它 → 再点"开始"就会双开。
        running = live_session(s.data_dir, 1)
        if running and not getattr(args, "force", False):
            print(f"[FAIL] 账号 #1 已有会话在跑（{running.get('course')}，pid {running.get('pid')}）"
                  "——先停掉再启动，避免同账号双开；确认无影响可加 --force")
            return 1

        acc_row = find_account(conn, "1")
        runner = StudyRunner(
            s, s.default_state_path, conn, account_id=1,
            quota=effective_quota(s, acc_row) if acc_row else None,
            pace=effective_pace(s, acc_row) if acc_row else None,
        )
        write_marker(s.data_dir, 1, course["name"])
        try:
            stats = runner.run(dict(course), limit=args.limit, ignore_window=args.ignore_window)
        finally:
            clear_marker(s.data_dir, 1)
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
    from .core.runmark import clear_marker, write_marker
    from .store.db import connect

    s = _settings(args)
    conn = connect(s.db_path)
    marked: list[int] = []  # 已写标记的账号（finally 只清自己写过的）
    try:
        # 先定账号：种子课程要从"本学习安排账号"里解析，而非默认账号#1。
        # （修 2026-10-26 的 R1：学习安排只含 #2/#3 时，旧逻辑在 #1 找不到课会整轮失败）
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
        # 同账号单进程（2026-10-26 补）：面板/定时/另开的 CLI 都可能已在跑这个号。
        # 此前只有 `xxt learn` 有守卫，`mlearn` 直接写标记不检查 → 面板在跑时再跑一次就双开。
        if not getattr(args, "force", False):
            from .core.runmark import busy_sessions

            busy = busy_sessions(s.data_dir, [int(r["id"]) for r in rows])
            if busy:
                for aid, info in busy.items():
                    print(f"[FAIL] 账号 #{aid} 已有会话在跑（{info.get('course')}，"
                          f"pid {info.get('pid')}）——先停掉再启动，避免同账号双开")
                print("       若确认无影响、必须强制并行，加 --force")
                return 1
        ids = [int(r["id"]) for r in rows]
        # 课程解析：pid 精确优先（跨账号唯一锚点）→ 课名；范围限定本学习安排账号
        course = _pick_course_in(conn, account_ids=ids,
                                 pid=getattr(args, "course_pid", None), name=args.course)
        if course is None:
            want = getattr(args, "course_pid", None) or args.course
            print(f"[FAIL] 在本学习安排账号里未找到课程：{want}"
                  "（确认账号已抓取该课程；或在面板重新选择课程）")
            return 1
        print(f"[mlearn] 课程匹配：《{course['name']}》pid={course['platform_course_id']}")
        # 会话标记文件：面板据此显示/停止本会话（跨面板重启仍可见）
        for row in rows:
            write_marker(s.data_dir, int(row["id"]), course["name"])
            marked.append(int(row["id"]))
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
        for aid in marked:  # 清理本会话标记（只清自己写过的）
            try:
                clear_marker(s.data_dir, aid)
            except Exception:
                pass
        conn.close()


def cmd_panel(args: argparse.Namespace) -> int:
    import threading

    import uvicorn

    from .panel.app import create_app

    s = _settings(args)
    app = create_app(s)
    host = s.raw.get("panel", {}).get("host", "127.0.0.1")
    port = int(s.raw.get("panel", {}).get("port", 8300))
    url = f"http://{host}:{port}"
    print(f"[OK] 面板启动：{url}（Ctrl+C 停止）")
    # exe 双击场景：服务就绪后自动打开默认浏览器
    if getattr(sys, "frozen", False):
        import webbrowser

        threading.Timer(2.5, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0


def _random_start(ws: str, we: str) -> str:
    """在 [ws, we] 窗口内随机开刷时刻（HH:MM），不越窗、不产生 24:xx。

    2026-10-27：原实现无条件加 0~120 分钟，短窗口（如 23:00-23:30）会随机到 23:59
    这种窗口外时刻 → 到点被子进程判“超出学习时段”秒退，白耗一次当日机会。
    """
    import random as _r

    def _min(s: str) -> int:
        h, m = s.split(":")
        return int(h) * 60 + int(m)

    try:
        start, end = _min(ws), _min(we)
    except Exception:
        return "09:00"
    span = (1440 - start + end) if end <= start else (end - start)  # 跨午夜
    span = max(0, span - 1)  # 留 1 分钟余量，不压线到窗口结束
    t = (start + _r.randint(0, min(120, span))) % 1440
    return f"{t // 60:02d}:{t % 60:02d}"


def _course_ref(pick) -> tuple[str, str | None]:
    """从选课结果里取（课名, 平台课程ID）。

    2026-10-27 实测踩坑：学习安排返回 dict（键 `pid`），课程轮转返回 `sqlite3.Row`
    （列名 `platform_course_id`，没有 `pid`）。原来统一用 `.get("pid")` → 轮转路径
    抛 `AttributeError: 'sqlite3.Row' object has no attribute 'get'`，**守护一触发就崩**，
    且因为它之前只验证过“学习安排”路径而没被发现。
    """
    if isinstance(pick, dict):
        return pick["name"], pick.get("pid")
    keys = pick.keys()  # sqlite3.Row
    return pick["name"], (pick["platform_course_id"] if "platform_course_id" in keys else None)


def _epoch(ts: str) -> float:
    """`2026-10-27T08:11:14` → epoch 秒；解析不了返回 0。"""
    import time as _t

    try:
        return _t.mktime(_t.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return 0.0


def _hhmm_in(minutes: int) -> str:
    """当前时刻 + N 分钟 → HH:MM（失败退避用）。"""
    import time as _t

    return _t.strftime("%H:%M", _t.localtime(_t.time() + minutes * 60))


def _load_retry_after(db_path, day: str) -> dict[int, str]:
    """读回当日的退避时刻表（守护重启后不能把“正在退避”忘掉）。

    2026-10-27 实测隐患：退避只存内存，重启即清零 → 刚失败的账号被立刻重新发起，
    既违反“随机退避”的设计，也平白多一次访问（风控第一）。跨日自动作废。
    """
    import json as _json

    from .store.db import connect

    try:
        conn = connect(db_path)
        try:
            row = conn.execute(
                "SELECT value FROM app_config WHERE key='auto.retry_after'").fetchone()
        finally:
            conn.close()
        if not row:
            return {}
        info = _json.loads(row["value"])
        if info.get("date") != day:
            return {}
        return {int(k): v for k, v in (info.get("times") or {}).items() if v}
    except Exception:
        return {}


def _persist_retry_after(db_path, day: str, mapping: dict[int, str]) -> None:
    """把退避时刻表写进 app_config（同 `auto.plan` 的做法），供重启续跑与面板展示。"""
    import json as _json

    from .store.db import connect

    live = {aid: hm for aid, hm in mapping.items() if hm}
    try:
        conn = connect(db_path)
        try:
            with conn:
                conn.execute(
                    "INSERT INTO app_config(key, value) VALUES('auto.retry_after', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (_json.dumps({"date": day, "times": {str(k): v for k, v in live.items()},
                                  "updated": _strftime_now()},),))
        finally:
            conn.close()
    except Exception:
        pass   # 退避落库失败只影响重启续跑，绝不影响调度本体


def _strftime_now(fmt: str = "%Y-%m-%dT%H:%M:%S") -> str:
    import time as _t

    return _t.strftime(fmt, _t.localtime())


def _replay_auto_events(events) -> tuple[bool, int]:
    """回放某账号当天的 auto-* 事件 → （今日是否已确认开跑, 今日触发尝试次数）。

    2026-10-27 方案 A：旧版只要写过 `auto-fire` 就算“今天已触发”，哪怕子进程 30 秒后
    就秒退——08:11 的 #2 与 01:40 的 #1 就是这样白掉一整天的。现在语义改为：
      - `auto-try`：确实发起了会话（触发成功，等回读确认）；
      - `auto-fire`：回读确认会话真在跑（成功标记，今天不再重复）；
      - `auto-error`：该轮失败（可退避后重试，直到尝试次数上限）。
      - `auto-revoke`：撤回“今日已学”（会话早退、今日 0 个视频）——
        旧版这条也写成 `auto-error`，导致用户 2026-10-27 看到日志**以为是新一次失败**。
      - `auto-suspend`：挂起（打不开/网络/风控）→ 退避后自动恢复，语义同撤回。
      - `auto-done`：今日目标达成（额度/任务数达标、无待学章节）→ 今天不再自动发起。
      - `auto-hardstop`：硬停（次数/退避都用尽，或时段已过）→ 今天不再自动发起。
        2026-10-28（B）从 `auto-error` 拆出：硬停借用 `auto-error` 时，回放只认
        “5 分钟内紧跟 auto-fire”，秒退型硬停重启后又撞一次平台。
    老数据没有 `auto-try`，用“`auto-fire` 后 5 分钟内紧跟 `auto-error`”识别为失败。
    """
    events = list(events)
    has_try = any(ev == "auto-try" for _, ev in events)
    tries = sum(1 for _, ev in events
                if ev == ("auto-try" if has_try else "auto-fire"))
    ok, last_fire = False, 0.0
    for ts, ev in events:
        if ev == "auto-try":
            ok = False  # 新一轮尝试开始，上一轮的成败不再算今日已跑
        elif ev == "auto-fire":
            ok, last_fire = True, _epoch(ts)
        elif ev in ("auto-revoke", "auto-suspend"):
            ok, last_fire = False, 0.0  # 撤回/挂起：无论隔多久都算今日未学
        elif ev in ("auto-done", "auto-hardstop"):
            # 达成 / 硬停：今天都真的不用再跑（重启后也不许再撞一次）
            ok, last_fire = True, _epoch(ts)
        elif ev == "auto-error" and last_fire and _epoch(ts) - last_fire <= 300:
            ok, last_fire = False, 0.0  # 秒退（历史数据兼容）：这条 auto-fire 不算成功
    return ok, tries


def _terminal_kind(events) -> str:
    """回放当天 auto-* 事件 → 终态类型（"done" / "hardstop" / ""）。

    【Q44】只回答一个问题：这个号今天是不是被贴了“别再管了”的纸条、贴的是哪张。
      - `auto-done`     → "done"（额度/任务数达成）
      - `auto-hardstop` → "hardstop"（单日发起次数用尽）
      - 之后又有 `auto-fire`（重新确认开跑）→ 清空：已经在跑了，不再是终态
    """
    kind = ""
    for _ts, ev in events:
        if ev == "auto-done":
            kind = "done"
        elif ev == "auto-hardstop":
            kind = "hardstop"
        elif ev == "auto-fire":
            kind = ""
    return kind


# 【Q44】守护关心的配置项中文名（“配置已重载”日志用，避免日志里冒出英文 key）。
# 只列影响调度的项；面板「🕘 最近配置变更」用的全量词表在 panel/app.py::CFG_CN。
_SCHED_CN = {
    "auto.max_tries": "每日最多自动发起次数",
    "auto.retry_minutes": "退避基数(分钟)",
    "auto.confirm_seconds": "开跑回读(秒)",
    "auto.retry_jitter_pct": "退避随机浮动(%)",
    "auto.retry_max_minutes": "退避封顶(分钟)",
    "auto.hard_tries_cap": "单日硬上限(次)",
    "auto.reopen_run_now": "解除收工后立刻开跑",
    "quota.daily_minutes": "每日时长额度(分钟)",
    "quota.daily_tasks": "每日任务额度(个)",
    "quota.float_ratio": "额度随机系数",
    "pace.daily_window": "学习时段",
}


def _reopen_verdict(*, kind: str, n_try: int, cap: int, in_skipped: bool,
                    running: bool, reached: bool, facts: str) -> tuple[bool, str]:
    """【Q44】今日终态该不该解除 → (是否解除, 原因文案)。

    纯函数，顺序就是五道闸（2026-10-28 用户拍板）：
      ① 正在跑 → 不解除（一号一进程；跑着的会话按启动时参数，绝不开第二个）
      ② 今日已跳过（手动停/未登录）→ 不解除（手动停最高优先级，今日让位）
      ③ 今日发起次数已达硬上限 → 不解除（额度再大也没次数；调大上限后自然放行）
      ④ auto-done：按当前额度重判；仍达标 → 不解除
      ⑤ 其余（含 auto-hardstop 过了③）→ 解除
    """
    if running:
        return False, "会话正在跑（绝不开第二个）"
    if in_skipped:
        return False, "今日已跳过（手动停/未登录）"
    if cap and n_try >= cap:
        return False, f"今日已发起 {n_try} 次，已达「单日硬上限 {cap}」"
    if kind == "done":
        if reached:
            return False, f"仍然达标（{facts}）"
        return True, f"额度已调大但今天还没达到（{facts}）"
    return True, f"今日已发起 {n_try} 次，未到「单日硬上限 {cap}」"


def _prio(val) -> float:
    """账号优先级转浮点（【Q43】数字大 = 先跑；空/非法按 1.0 历史默认值）。"""
    try:
        return float(val) if val not in (None, "") else 1.0
    except Exception:
        return 1.0


def _sched_sig(raw: dict | None) -> dict[str, str]:
    """取调度相关配置（[auto]/[quota]/[pace]）的指纹，变没变一比自己。

    会话72：跳过守护自己回写的状态键 `auto.plan`、`auto.retry_after`——它们是守护
    持久化到 app_config 的运行态（会被 Settings 并进 raw），若纳入指纹，守护每自写
    一次退避/计划→下轮巡检就自检测到“配置已重载”→日志噪声且措辞误导（C）。
    用户可调的 [auto] 参数（max_tries/retry_minutes…）与 [quota]/[pace] 仍全量监控。
    """
    import json

    _daemon_owned = frozenset({"auto.plan", "auto.retry_after"})
    sig: dict[str, str] = {}
    for sec in ("auto", "quota", "pace"):
        node = (raw or {}).get(sec)
        if isinstance(node, dict):
            for k, v in node.items():
                if f"{sec}.{k}" in _daemon_owned:
                    continue
                sig[f"{sec}.{k}"] = json.dumps(v, sort_keys=True, ensure_ascii=False)
    return sig


def _db_readable(db_path) -> bool:
    """轻量探活：库能不能读（只查一行）。

    【Q44 保护①】`Settings.__init__` 会吞掉 DB 异常并回退到内置默认值——
    那会让额度瞬间变回 240/60 从而误判“今日已达成”。所以热重载前先探活，
    读不了就整轮跳过（继续用旧值）。
    """
    import sqlite3

    try:
        conn = sqlite3.connect(db_path, timeout=1.0)
        try:
            conn.execute("SELECT 1 FROM app_config LIMIT 1").fetchall()
        finally:
            conn.close()
        return True
    except Exception:
        return False


def _auto_limits(raw: dict | None) -> tuple[int, int, int, int, int, int]:
    """读 `[auto]` 配置 → (次数上限, 退避基数, 回读秒, 抖动%, 退避封顶, 单日硬上限)。

    2026-10-27 用户要求：别把次数写死在代码里。默认 2 次 / 15 分钟 / 25 秒；
    `max_tries = 0` 表示不限（其余项仍受上下限约束，防手滑写坏）。
    同日追加：退避不能是固定节奏（固定间隔最像机器人），加抖动系数与封顶。
    同日追加（Q19）：次数用尽**不等于今天收工**——可恢复的失败转“长退避挂起”
    继续努力，直到 `hard_tries_cap`（单日绝对发起上限）才硬停。
    """
    cfg = raw or {}

    def _int(key: str, default: int, lo: int, hi: int) -> int:
        try:
            val = int(cfg.get(key, default))
        except Exception:
            return default
        return max(lo, min(hi, val))

    return (_int("max_tries", 2, 0, 10),
            _int("retry_minutes", 15, 1, 240),
            _int("confirm_seconds", 25, 15, 300),
            _int("retry_jitter_pct", 40, 0, 100),
            _int("retry_max_minutes", 90, 15, 480),
            _int("hard_tries_cap", 4, 1, 20))


def _backoff_minutes(base: int, n_fail: int, jitter_pct: int, cap: int) -> int:
    """第 n 次失败后的退避分钟数：base × 2^(n-1)，上下浮动 jitter%，封顶 cap。

    2026-10-27 用户要求“可以设置随机，模拟人的行为”：固定 15/30/60 的等差节奏
    与真人差异太大，改为指数退避 + 随机抖动（默认 ±40%）。纯本地计算，零平台请求。
    例：base=15, jitter=40 → 第一次 9~21 分钟；第二次 18~42；封顶 90。
    """
    import random as _r

    wait = max(1, base) * (2 ** max(0, min(n_fail, 6) - 1))
    if jitter_pct > 0:
        wait *= 1 + _r.uniform(-jitter_pct, jitter_pct) / 100.0
    return max(1, min(cap or 480, int(round(wait))))


def _try_label(n_try: int, max_tries: int, hard_cap: int) -> str:
    """发起次数的说法（2026-10-27 实测：“第 4/3 次发起”分母比序号小，看着像 bug）。

    短退避内说「第 n/max 次」；超出后不能再写 `/max`，改说“走长退避，硬上限 N 轮”，
    与 Q19 的分档一致（额度未完成时次数用尽不等于今日收工）。
    """
    if not max_tries or n_try <= max_tries:
        return f"第 {n_try}/{max_tries or '不限'} 次发起"
    return (f"第 {n_try} 次发起（短退避 {max_tries} 次已用尽，"
            f"转长退避继续，硬上限 {hard_cap} 轮）")


def _failure_action(code: str, n_try: int, max_tries: int, n_suspend: int,
                    hard_cap: int, retry_minutes: int,
                    jitter_pct: int, max_minutes: int) -> tuple[str, int]:
    """失败归类 → (处置, 等待分钟)。纯函数，便于单测。

    处置只有两种：
      - `suspend`：挂起，退避后自动恢复（可恢复的失败不判“今天收工”）；
      - `hard_stop`：今日不再自动发起（达成 / 需人工 / 时段外 / 硬上限用尽）。
    用户 2026-10-27（Q19）：额度与任务数是最高优先级，高于重试次数——
      ① 次数未到 `max_tries`：短退避（指数 + 抖动）；
      ② 次数已到但挂起次数未到 `hard_tries_cap`：长退避（按封顶间隔），
         继续在当天窗口里找机会，而不是判“今天没戏”；
      ③ 两者都用尽：硬停，面板留「立即补跑」入口。
    """
    from .core.diagnose import is_suspendable

    if code in ("quota", "done", "login", "window"):
        return ("hard_stop", 0)   # 达成 / 需人工 / 时段外：自动重试没意义
    if not is_suspendable(code):
        return ("hard_stop", 0)
    if not max_tries or n_try < max_tries:
        return ("suspend",
                _backoff_minutes(retry_minutes, n_try, jitter_pct, max_minutes))
    if n_suspend < max(1, hard_cap):
        return ("suspend",
                _backoff_minutes(max_minutes, 1, jitter_pct, max_minutes))
    return ("hard_stop", 0)


def _session_end_reason(conn, account_id: int, since_iso: str) -> str:
    """回读触发之后该账号的 `session-end` 详情 → 子进程自报的停止原因。

    2026-10-27 用户要求“失败要说清原因”：以前 auto-error 只写“多为窗口外或额度
    已满”这种猜测（8:11 的真实原因是防双开误判），现在把子进程写的
    `课程｜停止原因｜视频 N 个` 后两段原样抄进告警。
    """
    try:
        row = conn.execute(
            "SELECT detail FROM run_logs WHERE account_id=? AND event='session-end'"
            " AND ts>=? ORDER BY id DESC LIMIT 1", (account_id, since_iso)).fetchone()
    except Exception:
        return ""
    if not row or not row["detail"]:
        return ""
    parts = [p.strip() for p in str(row["detail"]).split("｜") if p.strip()]
    return "｜".join(parts[-2:]) if len(parts) >= 3 else "｜".join(parts)


def _last_auto_start(conn, account_id: int, day: str) -> str:
    """今日该账号最近一次“守护发起”的时刻（auto-try / auto-fire）→ 本轮起点。

    2026-10-28（P1）：`_revive_if_dead` 以前拿“今天最后一条 session-end”当本轮原因，
    根本分不清是哪一轮——上一轮打不开 → 复活白撞；上一轮额度达标 → 今天就钉死了。
    """
    try:
        row = conn.execute(
            "SELECT ts FROM run_logs WHERE account_id=?"
            " AND event IN ('auto-try','auto-fire') AND ts LIKE ?"
            " ORDER BY id DESC LIMIT 1", (account_id, day + "%")).fetchone()
    except Exception:
        return ""
    return str(row["ts"]) if row else ""


def _session_end_after(conn, account_id: int, since_iso: str):
    """`since_iso` 之后该账号最后一条 `session-end`（ts + detail）；没有则 None。"""
    try:
        return conn.execute(
            "SELECT ts, detail FROM run_logs WHERE account_id=? AND ts>=?"
            " AND event='session-end' ORDER BY id DESC LIMIT 1",
            (account_id, since_iso)).fetchone()
    except Exception:
        return None


def _manual_stopped(conn, account_id: int, since_iso: str) -> bool:
    """`since_iso` 之后有人手动停过、且此后**没有**再开新会话 → True。

    Q29（2026-10-27 用户拍板）：手动停要能区分于“秒退”——但又不能把
    “手动停一下 → 又手动 ▶ → 这次会话自己退出”也当成手动停（否则整天让位）。
    """
    try:
        stop = conn.execute(
            "SELECT ts FROM run_logs WHERE account_id=? AND event='session-stop'"
            " AND ts>=? ORDER BY id DESC LIMIT 1",
            (account_id, since_iso)).fetchone()
        if not stop:
            return False
        again = conn.execute(
            "SELECT 1 FROM run_logs WHERE account_id=? AND event='session-start'"
            " AND ts>? LIMIT 1", (account_id, stop["ts"])).fetchone()
    except Exception:
        return False
    return again is None


def _current_activity(conn, account_id: int, since_iso: str) -> str:
    """回读“已开跑”时回答：现在到底在学哪一节、本次已播完几个。

    2026-10-27 用户反馈：`auto-fire 已确认开跑（回读会话进程存活）` 看了不明白，
    想知道正在跑哪个章节任务。章节事件由 `astudy._rec()` 写入：
    `chapter-start`（开始）/ `video-done`（完成）/ `video-interrupt`（中断）/
    `chapter-fail`（打不开）。detail 统一为 `课程｜章节｜其它`。
    """
    try:
        row = conn.execute(
            "SELECT event, detail FROM run_logs WHERE account_id=? AND ts>=?"
            " AND event IN ('chapter-start','video-done','video-interrupt','chapter-fail')"
            " ORDER BY id DESC LIMIT 1", (account_id, since_iso)).fetchone()
        n_done = conn.execute(
            "SELECT COUNT(*) n FROM run_logs WHERE account_id=? AND ts>=?"
            " AND event='video-done'", (account_id, since_iso)).fetchone()["n"]
    except Exception:
        return ""
    tail = f"（本次已播 {n_done} 个）" if n_done else ""
    if not row:
        return "会话已起，尚未开始播" if n_done == 0 else tail.strip("（）")
    parts = [p.strip() for p in str(row["detail"] or "").split("｜") if p.strip()]
    chapter = parts[1] if len(parts) >= 2 else ""
    label = {"chapter-start": "正在学", "video-done": "刚播完",
             "video-interrupt": "播放中断", "chapter-fail": "打不开"}.get(row["event"], "")
    if chapter and label:
        extra = ""
        if row["event"] == "video-interrupt" and len(parts) >= 3:
            extra = f"（{parts[2]}）"
        return f"{label}：{chapter}{extra}{tail}"
    return tail.strip("（）")


def _revive_plan(now_epoch: float, end_ts: str, videos_today: int, tries_today: int,
                 auto_tries_today: int, max_tries: int,
                 retry_minutes: int, *, force: bool = False) -> tuple[bool, int]:
    """已 `fired` 的账号会话早退后 → (是否允许当日补跑, 还需等几分钟)。

    2026-10-27 补漏：回读只在 `confirm_seconds` 那一刻看一次，只能抓“秒退”。
    #1 今早 09:10:43 确认在跑、09:13:59 才退出（视频 0 个），仍被算作今日已学。
    收回“今日已学”的三个前提：今日 0 个视频、这轮确由守护发起（有 `auto-try`）、
    尝试次数未用尽。已退出超过 `retry_minutes` 就立刻补跑（#1 那种隔了一小时的
    不必再空等），否则按剩余分钟向上取整。

    `force=True`（Q19，可恢复的失败）：即使今天已经学了一些，只要还没达标就
    继续补跑——额度/任务数优先于“学过了就算完”，否则一碰到打不开就白掉半天。
    """
    if videos_today > 0 and not force:
        return (False, 0)   # 今天真学过，会话退出属正常停机（额度/超窗/做完）
    if auto_tries_today <= 0:
        return (False, 0)   # 不是守护发起的（面板 ▶ / 手动），不代用户补跑
    if max_tries and tries_today >= max_tries:
        return (False, 0)   # 次数用尽，今日不再自动重试
    end = _epoch(end_ts) if end_ts else 0.0
    if not end:
        return (True, 0)    # 没留 session-end（崩溃），当已隔够久
    import math

    gap = (now_epoch - end) / 60.0
    if gap >= retry_minutes:
        return (True, 0)
    return (True, max(1, int(math.ceil(retry_minutes - gap))))


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
    from .core.accounts import effective_pace, is_valid
    from .core.pace import hm_waiting, within_daily_window
    from .core.rotation import pick_courses
    from .store.db import connect

    s = _settings(args)
    planned: dict[int, str] = {}   # account_id → 今日计划 HH:MM（全天保留供面板显示）
    fired: set[int] = set()        # 今日已触发（含手动已开）
    skipped: set[int] = set()      # 今日跳过（未登录/无课程/手动停）
    pending: dict[int, tuple[float, str]] = {}  # 已触发待回读：aid → (monotonic, ISO)
    tries: dict[int, int] = {}      # 今日触发尝试次数（auto-try 计数，含老数据 auto-fire）
    suspend_n: dict[int, int] = {}   # 今日挂起次数（Q19：短退避用尽后的长退避轮次）
    retry_after: dict[int, str] = {}  # 失败退避：早于该时刻不再重试
    # 【Q44】今日终态：aid → "done"（额度达成）/ "hardstop"（次数用尽）。
    # 只回答“冰箱上贴的是哪张纸条”，重算入口靠它（不靠文案匹配）。
    terminal_kind: dict[int, str] = {}
    # 次数/退避/回读等待做成配置（config/settings.toml 的 [auto]，2026-10-27 用户要求）
    (max_tries, retry_minutes, confirm_seconds,
     retry_jitter_pct, retry_max_minutes, hard_tries_cap) = _auto_limits(s.raw.get("auto"))
    # 【Q44】解除“今日收工”后立刻开跑？默认关 = 仍按定时/学习窗口排队。
    reopen_run_now = bool(s.raw.get("auto", {}).get("reopen_run_now", False))
    # 【Q44】调度配置指纹：每轮重读后与它比对，真变了才写一条日志（不刷屏）。
    last_sched_sig = _sched_sig(s.raw)
    wait_noted: set[int] = set()   # 已提示过“等学习窗口开启”的账号（不重复写日志）
    noted: set[int] = set()        # 已写过“达成/硬停”结论的账号（巡检每 30 秒，不刷屏）
    plan_date = _time.strftime("%Y-%m-%d")
    panel_port = int(s.raw.get("panel", {}).get("port", 8300))
    get_logger("cli").info("auto 守护启动（每 30 秒巡检；Ctrl+C 退出）")
    print("[auto] 守护启动。到点后经面板启动学习会话（面板可见、可 ■ 停止）。Ctrl+C 退出。")

    def _ev(aid: int, event: str, detail: str, level: str = "INFO",
            code: str = "") -> None:
        """守护决策写入 run_logs → 面板「📜 日志」直接可见（aid=0 为系统级）。

        只写“状态变化”（首次算出计划 / 触发 / 跳过 / 退出），不随 30 秒巡检重复写，
        否则一天就刷出上千行把真事件顶掉（2026-10-26 用户要求“日志页能看到守护做了什么”）。
        `code`：判定用的机器码（core.diagnose 的类别），面板优先读它——
        以前面板拿人话文案二次归类，文案里带引号/重复词就归错（2026-10-28 Q30）。
        """
        try:
            conn = connect(s.db_path)
            with conn:
                conn.execute(
                    "INSERT INTO run_logs(ts, level, event, detail, account_id, code)"
                    " VALUES(?,?,?,?,?,?)",
                    (_time.strftime("%Y-%m-%dT%H:%M:%S"), level, event, detail, aid,
                     code or None),
                )
            conn.close()
        except Exception:  # 日志失败绝不影响调度
            pass

    err_once: set[tuple[int, str]] = set()  # 同账号同原因只记一次，避免重试刷屏

    def _stopped_since(account_id: int, iso_ts: str) -> bool:
        """触发之后是否有人手动停过会话（语义见 `_manual_stopped`）。"""
        try:
            conn = connect(s.db_path)
            try:
                return _manual_stopped(conn, account_id, iso_ts)
            finally:
                conn.close()
        except Exception:
            return False

    def _local_quota_reached(aid: int, day: str) -> tuple[bool, str]:
        """今日本地进度是否已够额度**下限** → (达标?, 一句人话的现状)。

        纯本地（run_logs + 账号额度覆盖），零平台访问。额度在上限内随机浮动、
        且子会话**每次启动重抽一次**，所以“够不够”只能拿下限比（core.quota.budget_floor），
        与子进程同一套口径。
        """
        from .core.accounts import effective_quota
        from .core.quota import budget_floor, reached_locally, sum_done_seconds

        try:
            conn_q = connect(s.db_path)
            try:
                row_a = conn_q.execute(
                    "SELECT * FROM accounts WHERE id=?", (aid,)).fetchone()
                rows_v = conn_q.execute(
                    "SELECT detail FROM run_logs WHERE event='video-done'"
                    " AND account_id=? AND ts LIKE ?", (aid, day + "%")).fetchall()
            finally:
                conn_q.close()
        except Exception:
            return (True, "")   # 查不到就别改判：交给原来的“信子进程”逻辑
        n_v = len(rows_v)
        secs = sum_done_seconds(r["detail"] or "" for r in rows_v)
        q = effective_quota(s, row_a) if row_a is not None else dict(s.quota)
        m_lo, t_lo = budget_floor(q)
        facts = (f"{n_v} 个 / {secs // 60} 分钟"
                 f"（额度下限 {t_lo or '不限'} 个 / {m_lo or '不限'} 分钟）")
        return (reached_locally(q, n_v, secs), facts)

    def _revive_if_dead(aid: int, day: str) -> bool:
        """已 `fired` 但会话已退出 → 判是否收回“今日已学”（返回是否收回）。

        2026-10-27 补漏：回读只抓“秒退”；#1 今早确认开跑后跑了 3 分 46 秒才退（视频 0 个），
        旧逻辑就把它算作今日已学，一整天不再试。这里在进程真的消失后再判一次。
        纯本地（读标记 + 查库），零平台访问。
        """
        from .core.runmark import marker_process_alive

        since = ""
        try:
            if marker_process_alive(s.data_dir, aid):
                return False  # 还在跑（或刚结束还没来得及清理，下一轮再说）
        except Exception:
            return False
        try:
            conn_r = connect(s.db_path)
            try:
                def _cnt(event: str) -> int:
                    return conn_r.execute(
                        "SELECT COUNT(*) n FROM run_logs WHERE account_id=?"
                        " AND event=? AND ts LIKE ?",
                        (aid, event, day + "%")).fetchone()["n"]

                videos_today = _cnt("video-done")
                auto_tries_today = _cnt("auto-try")
                # P1（2026-10-28）：只有“本轮发起之后”的 session-end 才是这一轮的停止原因
                # （旧实现整天取最后一条，会把上一轮的原因当成本轮）。
                since = _last_auto_start(conn_r, aid, day) or (day + "T00:00:00")
                row_end = _session_end_after(conn_r, aid, since)
            finally:
                conn_r.close()
        except Exception:
            return False
        # Q29（2026-10-27 用户拍板）：用户手动停是最高优先级——不算失败、不占次数、
        # 今日让位（明天按定时自动恢复）。2026-10-27 18:21:17 手动停、18:21:40 就被
        # 写成「硬停（今日已试 4/4 轮）」，就是漏了这一条：回读分支挡住了，这里没挡。
        if since and _stopped_since(aid, since):
            fired.discard(aid)
            skipped.add(aid)
            retry_after.pop(aid, None)
            _persist_retry_after(s.db_path, day, retry_after)
            if aid not in noted:
                noted.add(aid)
                _ev(aid, "auto-skip",
                    "检测到手动停止：本次不算失败、不设退避，今日不再自动发起"
                    "（明日按定时恢复；要现在跑可点卡片 ▶）")
            return False
        from .core.diagnose import classify, describe, is_suspendable, tier_for

        n_aid = tries.get(aid, 0)
        why = str(row_end["detail"] or "") if row_end else ""
        # 无 session-end（子进程连收尾都没写出来 = 长空闲里被硬杀/外部带走）
        # → 直接归 crash（会话异常中止），不再拿占位句去 classify 落到 unknown/未归类
        # （会话72：未归类“原因不在已知类型里”误导，crash 才是准确且仍会自动重试的中性态）
        code = classify(why) if why else "crash"
        label, _hint = describe(code)
        tier = tier_for(code)
        # P1（2026-10-28）：达成不能只信子进程一句话。子进程报「额度达标」时，
        # 若本地今日总量连额度**下限**都没到，说明这轮是提前停的（额度每次启动重抽），
        # 以本地为准按未达标补跑——否则用户把额度调大也看不出任何变化。
        quota_short = False
        if tier == "achieved" and code == "quota":
            ok_local, facts = _local_quota_reached(aid, day)
            if not ok_local:
                quota_short = True
                label = "子进程报额度达标、本地未达下限"
                if aid not in noted:
                    noted.add(aid)
                    _ev(aid, "auto-suspend",
                        f"子进程报「今日目标达成」，但本地今日只播 {facts}，未到额度下限"
                        " → 以本地为准，按未达标继续补跑", "WARNING", code="quota")
        if tier == "achieved" and not quota_short:
            # 达成（额度/任务数达标、本地无待学）→ 今天真的不用再跑。
            # 注意：fired 必须**保留**——2026-10-27 实测写错成 discard，
            # 结果 #2 刚记完“已达成”又立刻被重新发起（第 3/3 次），白跑一趟。
            if aid not in noted:
                noted.add(aid)
                terminal_kind[aid] = "done"   # 【Q44】贴上“今日已收工”纸条
                # 【Q44】处方式文案：说清“已播多少 / 要今天继续改哪里”
                _ev(aid, "auto-done",
                    f"今日已收工（{label}）：{why}"
                    "｜要今天继续：调大「每日时长额度」或「每日任务数」（账号✎或全局），"
                    "30 秒内自动生效", "INFO",
                    code=code)
            return False
        if tier == "needs_you":
            # 登录失效：自动重试只会白撞，停下并给「重新登录」按钮（重启守护可重置）
            fired.discard(aid)
            planned.pop(aid, None)
            skipped.add(aid)
            _ev(aid, "auto-suspend",
                f"挂起·等你操作（{label}）：上次发起后会话已退出"
                " → 点账号卡片「重新登录」，登录后手动 ▶ 或重启守护即可补跑",
                "WARNING", code=code)
            return False
        if tier == "hard_stop" and videos_today > 0 and not quota_short:
            # 今天已经学过一些，只是时段到/额度到 → 正常收工，不再折腾
            return False
        # 可恢复的失败：次数用尽也先转长退避（硬上限 hard_tries_cap 才真硬停）；
        # force=True 表示“今天学过但没达标也要接着补”，不让一次打不开白掉半天。
        revive, wait_m = _revive_plan(
            _time.time(), row_end["ts"] if row_end else "", videos_today,
            n_aid, auto_tries_today, hard_tries_cap or max_tries, retry_minutes,
            force=is_suspendable(code) or quota_short)
        if not revive:
            if (is_suspendable(code) or quota_short) and aid not in noted:
                noted.add(aid)
                terminal_kind[aid] = "hardstop"   # 【Q44】贴上“今日已硬停”纸条
                # 【Q44】处方式文案：先说清“几次 / 上限多少”，再说“要今天继续该改哪里”
                _ev(aid, "auto-hardstop",
                    f"硬停（{label}）：今日已发起 {n_aid} 次，已达「单日硬上限 "
                    f"{hard_tries_cap}」"
                    + (f"；{why}" if why else "")
                    + "｜要今天继续：① 把「单日硬上限(次)」调大 "
                      "② 或点卡片「立即补跑」；否则明天自动恢复", "WARNING",
                    code=code)
            return False
        fired.discard(aid)
        planned.pop(aid, None)
        if wait_m:
            # 等待时长也要随机（与回读失败那条同一套退避），否则重启路径又变固定节奏
            wait_m = _backoff_minutes(retry_minutes, max(1, n_aid),
                                      retry_jitter_pct, retry_max_minutes)
        retry_after[aid] = _hhmm_in(wait_m) if wait_m else ""
        _persist_retry_after(s.db_path, day, retry_after)
        # 2026-10-27 用户困惑「为什么是 auto-error」：这条其实是“撤回今日已学”，
        # 不是“本轮发起失败”。旧事件名把两件事揉成一个词，排查时极易误读。
        # Q19 起语义再细分：可恢复的失败写 auto-suspend（挂起后自动恢复）。
        # ⚠ 文案必须写**实际**已播个数：17:17:38 那条照抄“今日 0 个视频”，
        #   而 #1 当天已播 18 个——数字一假，用户就再也读不懂这行（用户 16:09 的原话）。
        _ev(aid, "auto-suspend",
            f"挂起（{label}）：会话已退出，今日已播 {videos_today} 个"
            f"｜短退避已用 {n_aid}/{max_tries or '不限'} 次，硬上限 {hard_tries_cap} 轮"
            + (f"｜{why}" if why else "")
            + " → "
            + (f"{wait_m} 分钟后自动恢复（约 {retry_after[aid]}）"
               if wait_m else "下一轮即恢复"),
            "WARNING", code=code)
        return True

    # 重启续跑：今日计划与「已确认开跑」从库里恢复——否则守护一重启就重新随机时刻，
    # 已刷完的号还会被再刷一次（“每账号每天只自动启动一次”会被打破）。
    try:
        conn = connect(s.db_path)
        try:
            row = conn.execute(
                "SELECT value FROM app_config WHERE key='auto.plan'").fetchone()
            if row:
                import json as _json_t

                info = _json_t.loads(row["value"])
                if info.get("date") == plan_date:
                    planned = {int(k): v for k, v in (info.get("times") or {}).items()}
            # 2026-10-27 方案 A：只有“回读确认在跑”的 auto-fire 算今日已学；
            # 失败轮次（auto-try + auto-error，老数据是 auto-fire + 紧随的 auto-error）
            # 不计入 fired，允许补跑（旧逻辑会把秒退当“今天已触发”，白掉一整天）。
            ev_rows = conn.execute(
                "SELECT account_id, ts, event FROM run_logs WHERE ts LIKE ?"
                " AND account_id > 0"
                " AND event IN ('auto-try','auto-fire','auto-error','auto-revoke',"
                "               'auto-suspend','auto-done','auto-hardstop') ORDER BY id",
                (plan_date + "%",)).fetchall()
            by_aid: dict[int, list[tuple[str, str]]] = {}
            for r in ev_rows:
                by_aid.setdefault(int(r["account_id"]), []).append((r["ts"], r["event"]))
                if r["event"] == "auto-suspend":
                    # 重启后要接上“已挂起几次”，否则长退避的硬上限形同虚设
                    suspend_n[int(r["account_id"])] = \
                        suspend_n.get(int(r["account_id"]), 0) + 1
            for aid_r, evs in by_aid.items():
                ok_r, n_r = _replay_auto_events(evs)
                tries[aid_r] = n_r
                if ok_r:
                    fired.add(aid_r)
                    # 【Q44】重启后也要知道纸条是哪张，否则额度调大后无法重算
                    kind_r = _terminal_kind(evs)
                    if kind_r:
                        terminal_kind[aid_r] = kind_r
            # 退避时刻表也要接上（2026-10-27 实测：只存内存，重启归零 → 刚挂起的
            # 账号会被立刻重新发起，白撞一次）；已过时刻的条目直接丢弃。
            _ra = _load_retry_after(s.db_path, plan_date)
            _now_hm = _time.strftime("%H:%M")
            retry_after.update({k: v for k, v in _ra.items()
                                if hm_waiting(_now_hm, v)})
            for k in [k for k, v in _ra.items() if not hm_waiting(_now_hm, v)]:
                _ra.pop(k, None)
            if set(_ra) != set(retry_after):
                _persist_retry_after(s.db_path, plan_date, retry_after)
            # 刻意不恢复 skipped：未登录/缺课这类原因当天可修（登进去、补抓课程），
            # 修好后重启守护应能补跑。
        finally:
            conn.close()
    except Exception:
        pass

    def _recompute_terminal(day: str) -> None:
        """【Q44】终态重算：配置改过之后，“今日已收工/已硬停”的结论必须重新算。

        2026-10-28 用户拍板：纸条（终态）必须服从当前配置；没达到要求就有权继续跑。
        五道闸，顺序不可换：
          ① 进程还活着 → 跳过（一号一进程；跑着的会话仍按启动时参数，绝不开第二个）
          ② 今天被手动停过（在 skipped 里）→ 跳过（Q29：手动停最高优先级，今日让位）
          ③ 今日发起次数已达「单日硬上限」→ 跳过（复用现有安全阀，不新增计数器）
          ④ auto-done：按**当前**额度重算还达不达标；auto-hardstop：过了③就有余额 → 解除
          ⑤ 解除：写 auto-reopen → fired.discard → 清 noted/wait_noted
                  → 重排计划（默认等定时；reopen_run_now=开 则视为现在到点）
        纯本地：只读库 + 读运行标记，零平台访问。
        """
        from .core.runmark import marker_process_alive

        # 状态漂移自愈：已不在 fired 里的号不该再算终态
        for a in [a for a in terminal_kind if a not in fired]:
            terminal_kind.pop(a, None)
        if not terminal_kind:
            return

        cap = hard_tries_cap or max_tries
        for aid in sorted(terminal_kind):
            kind = terminal_kind[aid]
            try:
                running = marker_process_alive(s.data_dir, aid)
                n_try = tries.get(aid, 0)
                # 只有 auto-done 需要查库重算额度；其余闸口先短路，避免无意义的读取
                reached, facts = True, ""
                if kind == "done" and not (
                        running or aid in skipped or (cap and n_try >= cap)):
                    reached, facts = _local_quota_reached(aid, day)
                ok_open, why = _reopen_verdict(
                    kind=kind, n_try=n_try, cap=cap, in_skipped=aid in skipped,
                    running=running, reached=reached, facts=facts)
            except Exception:
                continue
            if not ok_open:
                continue
            # ⑤ 解除
            terminal_kind.pop(aid, None)
            fired.discard(aid)
            noted.discard(aid)
            wait_noted.discard(aid)
            retry_after.pop(aid, None)
            _persist_retry_after(s.db_path, day, retry_after)
            if reopen_run_now:
                planned[aid] = _time.strftime("%H:%M")   # 视为“现在就到点”
                tail = "→ 立刻开跑（下一轮巡检发起）"
            else:
                planned.pop(aid, None)                   # 按原定时/学习窗口重排
                tail = "→ 重新排队（按定时与学习窗口）"
            print(f"[auto] 账号#{aid} 解除今日收工：{why} {tail}")
            _ev(aid, "auto-reopen", f"解除今日收工：{why} {tail}")

    def _plan_brief() -> str:
        """计划摘要进 auto-start：10-26 的 23:50/23:55 缓存残留就是靠这行一眼认出来的。"""
        if not planned:
            return "｜今日计划待首轮巡检确定"
        return "｜今日计划 " + "、".join(f"#{k} {planned[k]}" for k in sorted(planned))

    _ev(0, "auto-start", "守护启动（每 30 秒巡检）" + _plan_brief()
        + f"｜短退避 {max_tries or '不限'} 次 / 基数 {retry_minutes} 分钟（±{retry_jitter_pct}%，"
          f"封顶 {retry_max_minutes} 分钟）｜长退避封顶 {hard_tries_cap} 轮后才硬停"
        + (f"｜沿用退避 " + "、".join(f"#{k} {retry_after[k]}" for k in sorted(retry_after))
           if retry_after else ""))
    started_at = _time.monotonic()  # 供退出时汇报运行时长
    first_pass = True  # 首轮：缓存计划与当前定时不一致时按 WARNING 记（便于追溯残留）

    while True:
        try:
            today = _time.strftime("%Y-%m-%d")
            if today != plan_date:  # 跨日重置（fired/skipped 必须一并清，否则次日永不触发）
                planned, plan_date = {}, today
                fired.clear()
                skipped.clear()
                err_once.clear()
                wait_noted.clear()
                tries.clear()
                suspend_n.clear()
                noted.clear()
                retry_after.clear()
                _persist_retry_after(s.db_path, today, retry_after)
                terminal_kind.clear()
            # 【Q44 热重载】面板改完额度/上限/退避后 30 秒内生效，不必重启守护。
            # 三重保护：① 先探活数据库——`Settings()` 内部会吞掉 DB 异常并回退到
            # 内置默认值（额度瞬间变回 240/60 会误判“今日已达成”），读不了就整轮跳过；
            # ② 数据目录/库路径变了拒绝应用（运行中换库 = 把今天的记录拆到两个库）；
            # ③ 出任何异常都保留旧配置，守护不崩。
            # 注意：不能用 _settings()（它会再 setup_logging 一次 → handler 重复叠加）。
            try:
                if _db_readable(s.db_path):
                    s_new = Settings()
                    if s_new.db_path == s.db_path and s_new.data_dir == s.data_dir:
                        s = s_new
                        (max_tries, retry_minutes, confirm_seconds,
                         retry_jitter_pct, retry_max_minutes,
                         hard_tries_cap) = _auto_limits(s.raw.get("auto"))
                        reopen_run_now = bool(
                            s.raw.get("auto", {}).get("reopen_run_now", False))
                        sig_now = _sched_sig(s.raw)
                        changed = [k for k in sorted(set(sig_now) | set(last_sched_sig))
                                   if sig_now.get(k) != last_sched_sig.get(k)]
                        if changed:
                            last_sched_sig = sig_now
                            _ev(0, "auto-reload", "配置已重载："
                                + "、".join(_SCHED_CN.get(k, k) for k in changed)
                                + " → 本轮起按新值判断")
                    elif (0, "data_dir") not in err_once:
                        err_once.add((0, "data_dir"))
                        _ev(0, "auto-reload",
                            f"数据目录改成了 {s_new.data_dir}，运行中不生效"
                            "（需重启守护），继续用当前库", "WARNING")
            except Exception as exc:
                if (0, "reload") not in err_once:
                    err_once.add((0, "reload"))
                    _ev(0, "auto-reload", f"配置重载失败，继续用旧值：{exc!r}", "WARNING")
            # 【Q44】重算终态：额度调大/上限调大后，今天就有权继续跑
            try:
                _recompute_terminal(today)
            except Exception as exc:
                if (0, "recompute") not in err_once:
                    err_once.add((0, "recompute"))
                    _ev(0, "auto-reload", f"终态重算失败（不影响本轮巡检）：{exc!r}",
                        "WARNING")
            # 回读上一轮已发起的账号（2026-10-27 方案 A 重写）：以前“发起成功”就当作
            # 今日已触发，子进程秒退也不回滚 → 一次失败白掉一整天。现在只有回读确认
            # 会话真在跑才记 auto-fire（成功）；秒退记 auto-error 并退避 15 分钟重试，
            # 每号每天最多 max_tries 次，避免连续访问触发风控。
            if pending:
                from .core.runmark import marker_process_alive

                for aid_p, (ts_p, iso_p) in list(pending.items()):
                    if _time.monotonic() - ts_p < confirm_seconds:  # 至少跑完一轮再回读
                        continue
                    pending.pop(aid_p, None)
                    if marker_process_alive(s.data_dir, aid_p):
                        if aid_p not in fired:
                            fired.add(aid_p)
                            # 带上“正在学哪一节、已播几个”，不要只写一句空话。
                            act = ""
                            try:
                                conn_a = connect(s.db_path)
                                try:
                                    act = _current_activity(conn_a, aid_p, iso_p)
                                finally:
                                    conn_a.close()
                            except Exception:
                                act = ""
                            _ev(aid_p, "auto-fire",
                                "已确认开跑（回读会话进程存活）"
                                + (f"｜{act}" if act else ""))
                        continue  # 会话真在跑 → 今日已学
                    if _stopped_since(aid_p, iso_p):
                        # Q29（2026-10-27 用户拍板）：手动停 → 不占次数、不设退避、今日让位。
                        # 以前只 `continue`，fired 还留着 → 下一轮被 `_revive_if_dead`
                        # 当“异常退出”又复活一次（18:21 那 23 秒的错账就是这么来的）。
                        fired.discard(aid_p)
                        skipped.add(aid_p)
                        retry_after.pop(aid_p, None)
                        _persist_retry_after(s.db_path, today, retry_after)
                        _ev(aid_p, "auto-skip",
                            "检测到手动停止：本次不算失败、不设退避，今日不再自动发起"
                            "（明日按定时恢复；要现在跑可点卡片 ▶）")
                        continue  # 手动停的，不算异常
                    n_p = tries.get(aid_p, 1)
                    mins_p = max(1, int((_time.monotonic() - ts_p) // 60))
                    # 失败原因透传（2026-10-27 P0-3d）：抄子进程 session-end 的停止原因，
                    # 不再写“多为窗口外或额度已满”这种猜测。
                    why = ""
                    try:
                        conn_w = connect(s.db_path)
                        try:
                            why = _session_end_reason(conn_w, aid_p, iso_p)
                        finally:
                            conn_w.close()
                    except Exception:
                        why = ""
                    tail = f"；子进程报：{why}" if why else ""
                    from .core.diagnose import classify, describe, tier_for

                    # Q19（2026-10-27 用户：“额度/任务数是最高优先级，高于重试次数”）：
                    # 先把失败归类成四档，再决定挂起还是硬停——不能一碰次数就收工。
                    code = classify(why or "会话已退出且无停止说明")
                    label, _hint = describe(code)
                    tier = tier_for(code)
                    # 前缀已经写了「挂起（原因）」，这里不再重复 label；次数用
                    # `_try_label` 说清是短退避还是长退避（避免“第 4/3 次”）。
                    head = (f"触发后 {mins_p} 分钟会话已退出"
                            f"（今日{_try_label(n_p, max_tries, hard_tries_cap)}）")
                    if tier == "achieved":
                        fired.add(aid_p)   # 达成：今天真的不用再跑
                        _ev(aid_p, "auto-done",
                            f"今日目标达成（{label}）{tail} → 今天不再自动发起；"
                            "要再刷可在账号卡片点 ▶", code=code)
                        continue
                    if tier == "needs_you":
                        # 等人工登录：自动重试只会白撞（不耗次数，重启守护可重置）
                        skipped.add(aid_p)
                        _ev(aid_p, "auto-suspend",
                            f"挂起·等你操作（{label}）{head}{tail} → "
                            "点账号卡片「重新登录」，登录后手动 ▶ 或重启守护即可补跑",
                            "WARNING", code=code)
                        continue
                    action, wait_p = _failure_action(
                        code, n_p, max_tries, suspend_n.get(aid_p, 0),
                        hard_tries_cap, retry_minutes, retry_jitter_pct,
                        retry_max_minutes)
                    if action == "suspend":
                        # 退避随机化（2026-10-27 用户要求“模拟人的行为”）：
                        # 固定 15/30/60 的等差节奏太像机器，改为指数 + ±jitter% 抖动。
                        suspend_n[aid_p] = suspend_n.get(aid_p, 0) + 1
                        retry_after[aid_p] = _hhmm_in(wait_p)
                        _persist_retry_after(s.db_path, today, retry_after)
                        planned.pop(aid_p, None)  # 下一轮按 start_at 重新排
                        skipped.discard(aid_p)
                        _ev(aid_p, "auto-suspend",
                            f"挂起（{label}）{head}{tail}，{wait_p} 分钟后自动恢复"
                            f"（约 {retry_after[aid_p]}）；今日挂起 "
                            f"{suspend_n[aid_p]}/{hard_tries_cap} 次", "WARNING", code=code)
                    else:
                        fired.add(aid_p)  # 硬停：今日不再自动发起，面板留「立即补跑」
                        terminal_kind[aid_p] = "hardstop"   # 【Q44】贴上硬停纸条
                        if code == "window":
                            _ev(aid_p, "auto-hardstop",
                                f"硬停（{label}）{head}{tail}：今日学习时段已过"
                                "｜要现在跑：改学习时段或点「立即补跑」；否则明天自动恢复",
                                "WARNING", code=code)
                        else:
                            _ev(aid_p, "auto-hardstop",
                                f"硬停（{label}）{head}{tail}：今日已发起 "
                                f"{n_p} 次，短退避与长退避都已用尽"
                                "｜要今天继续：① 把「单日硬上限(次)」调大 "
                                "② 或点卡片「立即补跑」；否则明天自动恢复",
                                "WARNING", code=code)
            new_plan: list[str] = []
            conn = connect(s.db_path)
            try:
                rows = [r for r in acc.list_accounts(conn)
                        if r["status"] == "active" and is_valid(r)]
                # 【Q43】优先级：数字大的先跑（面板悬停已写明“数字大=先跑”）；
                # 放在守护侧排序而不改 SQL——面板卡片仍按 id 稳定排序，作用域隔离。
                rows.sort(key=lambda r: (-_prio(r["priority"]), r["id"]))
            finally:
                conn.close()
            for row in rows:
                aid = row["id"]
                if aid in pending:
                    continue  # 本轮已发起、还在等回读（confirm_seconds），不要重复触发
                if aid in fired or aid in skipped:
                    # 已触发/已跳过的号也要让缓存跟上当前定时：否则面板「今日定时」会
                    # 一整天显示旧时刻（2026-10-27 实测：#2 改回 09:20 后仍显示 08:11，
                    # 次日跨日才自愈）。只同步展示值，不再触发、不写决策日志。
                    if row["start_at"]:
                        planned[aid] = row["start_at"]
                    else:
                        planned.pop(aid, None)
                    if aid in fired:
                        # 2026-10-27 补漏：回读抓不到“确认开跑后中途退出”，这里补判一次；
                        # 收回了今日已学就下一轮按计划（或退避后）重试。
                        _revive_if_dead(aid, today)
                    continue
                if aid in planned and not row["start_at"]:
                    # 定时被清空时不能沿用缓存计划（否则“改成随机”等于没改）——2026-10-27
                    old = planned.pop(aid)
                    _ev(0, "auto-replan",
                        f"#{aid} {row['label']} 定时已清空 → 丢弃缓存计划 {old}，改按学习窗口随机")
                if aid in planned and row["start_at"] and planned[aid] != row["start_at"]:
                    # 以当前 start_at 为准：缓存可能来自上次运行/诊断脚本（10-26 的 23:5x
                    # 残留就是这么来的）；运行中改定时也应生效（2026-10-27 加固）。
                    old = planned[aid]
                    planned[aid] = row["start_at"]
                    if first_pass:
                        _ev(0, "auto-plan-stale",
                            f"#{aid} {row['label']} 缓存计划 {old} ≠ 当前定时 {planned[aid]}"
                            "（已按当前定时执行；缓存多来自上次运行或诊断脚本）", "WARNING")
                    else:
                        _ev(0, "auto-replan",
                            f"#{aid} {row['label']} 定时改为 {planned[aid]}（原 {old}）")
                if aid not in planned:
                    if row["start_at"]:
                        planned[aid] = row["start_at"]
                    else:
                        ws = row["window_start"] or s.pace["daily_window"][0]
                        we = row["window_end"] or s.pace["daily_window"][1]
                        planned[aid] = _random_start(ws, we)
                    print(f"[auto] 账号#{aid} {row['label']} 今日计划 {planned[aid]} 开刷")
                    new_plan.append(f"#{aid} {row['label']} {planned[aid]}")
                now_hm = _time.strftime("%H:%M")
                if aid in retry_after:
                    if hm_waiting(now_hm, retry_after[aid]):
                        continue  # 上一轮失败，退避中（环形比较，跨零点安全）
                    retry_after.pop(aid, None)
                    _persist_retry_after(s.db_path, today, retry_after)
                if now_hm < planned[aid]:
                    continue
                # 窗口预校验（2026-10-27 加固）：start_at 落在窗口外时子进程会立刻
                # “超出每日学习时段”退出，而面板已回 ok=True、auto-fire 把当日机会
                # 记掉 → 静默白耗。本地先判（零平台访问）：窗口未到就等，已过才跳过。
                pace_a = effective_pace(s, row)
                if not within_daily_window(pace_a):
                    ws, we = pace_a["daily_window"]
                    if ws > we or now_hm < ws:  # 今天还会开窗口 → 等，不消耗机会
                        if aid not in wait_noted:
                            wait_noted.add(aid)
                            _ev(aid, "auto-skip",
                                f"定时 {planned[aid]} 早于学习窗口 {ws}–{we}，等窗口开启后触发")
                        continue
                    skipped.add(aid)
                    _ev(aid, "auto-skip",
                        f"已过学习窗口 {ws}–{we}（当前 {now_hm}），今日不再自动启动"
                        "（未消耗机会；改窗口/定时后重启守护可补跑）", "WARNING")
                    continue
                state = s.data_dir / "accounts" / str(aid) / "state.json"
                if not state.exists():
                    skipped.add(aid)
                    print(f"[auto] 账号#{aid} 未登录，今日跳过")
                    _ev(aid, "auto-skip", "未登录（会话文件缺失，先点登录）", "WARNING")
                    continue
                conn = connect(s.db_path)
                plan = None
                try:
                    # 学习安排优先：启用中的学习安排指定了课程 → 用它；否则走课程轮转。
                    # 学习安排不含时间字段，开跑时刻仍由本账号 start_at/窗口决定（天然错峰）。
                    from .core import plans as _plans

                    plan = _plans.course_for_account(conn, aid)  # {"name","pid"} | None
                    picks = ([plan] if plan
                             else pick_courses(conn, _random.Random(), s.rotation,
                                               account_id=aid, count=1))
                finally:
                    conn.close()
                if not picks:
                    skipped.add(aid)
                    print(f"[auto] 账号#{aid} 无可自动刷课程（无启用的学习安排，且没有课纳入自动刷），今日跳过")
                    _ev(aid, "auto-skip",
                        "无可自动刷课程：请在「学习安排」排课，或在面板课程行点「🔄 自动刷」把它纳入（v10）", "WARNING")
                    continue
                course_name, course_pid = _course_ref(picks[0])
                src_cn = "学习安排" if plan else "轮转"
                # 缺课保护（2026-10-26）：学习安排指定的课该号没抓过时，spawn 后必然
                # `[FAIL] 未找到课程` 退出，而面板已回 ok=True → 记为“今日已触发”，
                # 当日机会白白消耗。本地先核（零平台访问），缺课直接跳过。
                conn = connect(s.db_path)
                try:
                    has_course = _plans.account_has_course(
                        conn, aid, pid=course_pid, name=course_name)
                finally:
                    conn.close()
                if not has_course:
                    skipped.add(aid)
                    print(f"[auto] 账号#{aid} 缺课《{course_name}》→ 今日跳过"
                          "（不消耗当日机会，补抓课程后可手动▶）")
                    _ev(aid, "auto-skip", f"缺课《{course_name}》｜不消耗当日机会（补抓后可手动▶）",
                        "WARNING")
                    continue
                # 会话统一交面板管理（面板可见/可停）；面板未启动则本轮重试。
                # ⚠ 与账号卡片 ▶ 走的是同一个 /api/study/start，共用防双开锁
                #   （进程内 _JOBS + data/run/<id>.json 标记），因此不会重复多开。
                try:
                    import httpx as _httpx

                    r = _httpx.post(
                        f"http://127.0.0.1:{panel_port}/api/study/start",
                        json={"account_id": aid, "course": course_name,
                              "course_pid": course_pid,
                              "source": "学习安排" if plan else "定时"}, timeout=10,
                    )
                    ok = bool(r.json().get("ok"))
                    reason = r.json().get("error", "")
                except Exception:
                    ok, reason = False, "面板未响应（保持面板运行）"
                if ok:
                    # 只是“发起成功”，还不能算今日已学：`confirm_seconds` 秒后回读会话进程，
                    # 真在跑才补写 auto-fire（2026-10-27 方案 A）。
                    tries[aid] = tries.get(aid, 0) + 1
                    pending[aid] = (_time.monotonic(), _time.strftime("%Y-%m-%dT%H:%M:%S"))
                    print(f"[auto] 已触发 账号#{aid} {row['label']} →《{course_name}》"
                          f"（{_try_label(tries[aid], max_tries, hard_tries_cap)}，待回读）")
                    _ev(aid, "auto-try",
                        f"《{course_name}》｜来源={src_cn}｜"
                        f"{_try_label(tries[aid], max_tries, hard_tries_cap)}"
                        f"，{confirm_seconds} 秒后回读")
                elif "已有进行中的会话" in str(reason):
                    fired.add(aid)  # 别的路径已开（面板 ▶/手动），视为今日已学
                    print(f"[auto] 账号#{aid} 已有会话在跑，今日不再触发")
                    _ev(aid, "auto-fire",
                        f"《{course_name}》｜已有会话在跑（面板 ▶ 或另一路启动），今日不再触发")
                elif "没有这门课" in str(reason):
                    skipped.add(aid)
                    print(f"[auto] 账号#{aid} 面板侧判定缺课：{reason}（今日跳过）")
                    _ev(aid, "auto-skip", f"面板判定缺课：{reason}", "WARNING")
                else:
                    print(f"[auto] 账号#{aid} 触发失败：{reason}（30 秒后重试）")
                    if (aid, str(reason)) not in err_once:  # 重试不刷屏，同因只记首次
                        err_once.add((aid, str(reason)))
                        _ev(aid, "auto-error", f"触发失败：{reason}（30 秒后重试）", "WARNING")
            # 计划落库：面板"今日定时"显示（auto 分配的时间对用户可见）
            if new_plan:
                _ev(0, "auto-plan", "、".join(new_plan))
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
            first_pass = False
            _time.sleep(30)
        except KeyboardInterrupt:
            mins = int((_time.monotonic() - started_at) // 60)
            print("\n[auto] 守护退出（学习进程继续自行运行至额度停机）")
            _ev(0, "auto-stop",
                f"运行 {mins} 分钟｜今日触发 {len(fired)} 个｜跳过 {len(skipped)} 个")
            return 0
        except Exception as exc:  # P0-13（2026-10-27 用户批准）
            # 正文异常（DB 锁/网络/子进程读取）以前直接崩：run_logs 里只有 auto-start、
            # 没有收尾事件，面板/rlogs 看起来“还在跑”，真因只能去 sub-auto-*.log 翻栈。
            # 现在补一条 auto-exit，把异常类型、摘要与运行量一次写清。
            mins = int((_time.monotonic() - started_at) // 60)
            n_fired = len(locals().get("fired") or ())
            n_skip = len(locals().get("skipped") or ())
            print(f"\n[auto] 守护异常退出：{type(exc).__name__}: {exc}")
            log.error("守护异常退出", exc_info=True)
            try:
                _ev(0, "auto-exit",
                    f"异常退出：{type(exc).__name__}: {exc}｜运行 {mins} 分钟"
                    f"｜今日触发 {n_fired} 个｜跳过 {n_skip} 个", "ERROR")
            except Exception:
                pass
            return 1


def cmd_work(args: argparse.Namespace) -> int:
    """P2.5：进入指定账号的章节作业页并作答（默认半自动：只填不交）。"""
    from playwright.sync_api import sync_playwright

    from .adapters.chaoxing.pagesync import find_frame_with, open_chapter_frame
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
            from .core.browser import launch

            if args.review:  # 判题回读：已提交的作业页读每题得分 → 题库转正/清空
                from .adapters.chaoxing.work import review_work

                browser = launch(p, s)  # 判题回读：跟随 [stealth].headless（Q36）
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
            browser = launch(p, s, headless=headed)
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


def cmd_machine_id(args: argparse.Namespace) -> int:
    """显示本机机器码（授权用：发给发牌方生成 xxt.lic）。"""
    from .core.licensing import check_license, machine_id

    s = _settings(args)
    lic = s.root / "xxt.lic"
    ok, msg = check_license(lic)
    print(f"机器码：{machine_id()}")
    print(f"授权状态：{msg or '未安装授权文件（自用模式）'}")
    print("（授权流程：把机器码发给发牌方 → 收到 xxt.lic 放到程序目录）")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    # exe 双击（无参数）：默认启动面板并自动开浏览器——分发友好
    if argv is None and not sys.argv[1:]:
        argv = ["panel"]
    parser = argparse.ArgumentParser(prog="xxt", description="网课自动化学习助手（超星学习通）")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    sub = parser.add_subparsers(dest="command", required=True)

    # 授权校验：xxt.lic 存在即校验（伪造/过期/异机拒绝）；无 lic=自用直通
    from .core.licensing import check_license
    from .config import find_project_root

    _ok, _msg = check_license(find_project_root() / "xxt.lic")
    if not _ok:
        print(f"[授权失败] {_msg}")
        return 403

    sub.add_parser("init", help="初始化数据目录与数据库")
    p_login = sub.add_parser("login", help="打开浏览器人工登录学习通，保存会话")
    p_login.add_argument("--account", default=None, help="账号 id/标签（缺省 1=default）")
    sub.add_parser("status", help="检查已保存会话是否有效")
    p_cs = sub.add_parser("courses", help="抓取课程列表并入库（复用页面自身接口）")
    p_cs.add_argument("--account", default=None, help="账号 id/标签（缺省 default）")

    p_ch = sub.add_parser("chapters", help="抓取指定课程的章节树并入库")
    p_ch.add_argument("course", help="课程名（支持模糊）或 courseid")
    p_ch.add_argument("--account", default=None, help="账号 id/标签（缺省 default）")
    p_ch.add_argument("--no-sync", action="store_true",
                      help="只更新名称与序号，不对齐已有行的完成状态（新抓的节仍按平台状态入库）")
    p_ch.add_argument("--demote", action="store_true",
                      help="本次允许“平台标未完成 → 本地打回待学”（覆盖配置 reconcile.demote；仍受两道护栏限制）")

    p_learn = sub.add_parser("learn", help="自动学习（不带课程名则按轮换策略自动选课）")
    p_learn.add_argument("course", nargs="?", default=None, help="课程名（支持模糊）或 courseid，可省略")
    p_learn.add_argument("--limit", type=int, default=None, help="本次最多播放的视频数")
    p_learn.add_argument("--ignore-window", action="store_true",
                         help="忽略每日学习时段限制（仅限验收测试）")
    p_learn.add_argument("--force", action="store_true",
                         help="跳过同账号双开检查（确认无影响时用）")

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
    p_ml.add_argument("--course-pid", default=None,
                      help="平台课程ID（学习安排锚点，优先于课名精确匹配）")
    p_ml.add_argument("--accounts", default=None, help="账号（id/标签，逗号分隔；缺省=全部 active）")
    p_ml.add_argument("--limit", type=int, default=None, help="每账号本次最多视频数")
    p_ml.add_argument("--concurrency", type=int, default=2, help="全局同时学习账号数上限")
    p_ml.add_argument("--force", action="store_true",
                      help="跳过同账号双开检查（确认无影响时用）")

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

    p_mid = sub.add_parser("machine-id", help="显示本机机器码与授权状态（对外分发授权用）")

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
        "machine-id": cmd_machine_id,
    }
    try:
        return handlers[args.command](args)
    except KeyboardInterrupt:
        print("\n已中断")
        return 130


if __name__ == "__main__":
    sys.exit(main())
