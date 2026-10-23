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

    s = _settings(args)
    adapter = get_adapter(s)
    state = adapter.probe_session(s.default_state_path)
    text = {
        "valid": "[OK] 会话有效",
        "invalid": "[!!] 会话已失效，请重新执行 xxt login",
        "no_state": "[..] 尚未登录，请先执行 xxt login",
    }[state]
    print(text)
    return 0 if state == "valid" else 1


def cmd_courses(args: argparse.Namespace) -> int:
    from .adapters import get_adapter
    from .store.db import connect, utcnow

    s = _settings(args)
    adapter = get_adapter(s)
    try:
        courses = adapter.fetch_courses(s.default_state_path)
    except RuntimeError as exc:
        print(f"[FAIL] {exc}")
        return 1
    conn = connect(s.db_path)
    try:
        for c in courses:
            conn.execute(
                """
                INSERT INTO courses(account_id, platform_course_id, clazz_id, name, url)
                VALUES(1, ?, ?, ?, ?)
                ON CONFLICT(account_id, platform_course_id)
                DO UPDATE SET clazz_id=excluded.clazz_id,
                              name=excluded.name,
                              url=excluded.url
                """,
                (c["platform_course_id"], c["clazz_id"], c["name"], c["url"]),
            )
        conn.commit()
        rows = conn.execute(
            "SELECT platform_course_id, clazz_id, name FROM courses WHERE account_id=1 ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    print(f"共 {len(rows)} 门课程（已入库，时间 {utcnow()}）：")
    for r in rows:
        print(f"  [{r['platform_course_id']}] {r['name']}")
    return 0


def _pick_course(conn, needle: str):
    """按课程名模糊或 courseid 精确找课程行。"""
    row = conn.execute(
        "SELECT * FROM courses WHERE platform_course_id = ? OR name LIKE ? ORDER BY id LIMIT 1",
        (needle, f"%{needle}%"),
    ).fetchone()
    return row


def cmd_chapters(args: argparse.Namespace) -> int:
    from .adapters import get_adapter
    from .store.db import connect

    s = _settings(args)
    conn = connect(s.db_path)
    try:
        course = _pick_course(conn, args.course)
        if course is None:
            print(f"[FAIL] 未找到课程：{args.course}（先跑 xxt courses）")
            return 1
        adapter = get_adapter(s)
        try:
            chapters = adapter.fetch_chapters(dict(course), s.default_state_path)
        except RuntimeError as exc:
            print(f"[FAIL] {exc}")
            return 1
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
    from .adapters.chaoxing.study import StudyRunner
    from .store.db import connect

    s = _settings(args)
    conn = connect(s.db_path)
    try:
        course = _pick_course(conn, args.course)
        if course is None:
            print(f"[FAIL] 未找到课程：{args.course}（先跑 xxt courses）")
            return 1
        runner = StudyRunner(s, s.default_state_path, conn)
        stats = runner.run(dict(course), limit=args.limit, ignore_window=args.ignore_window)
        summary = f"学习会话结束：视频 {stats['videos']} 个；原因：{stats['stop_reason'] or '达到 limit'}"
        get_logger("cli").info("%s", summary)
        print(summary)
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
    sub.add_parser("courses", help="抓取课程列表并入库（复用页面自身接口）")

    p_ch = sub.add_parser("chapters", help="抓取指定课程的章节树并入库")
    p_ch.add_argument("course", help="课程名（支持模糊）或 courseid")

    p_learn = sub.add_parser("learn", help="自动学习指定课程（视频真实播放，拟人节奏）")
    p_learn.add_argument("course", help="课程名（支持模糊）或 courseid")
    p_learn.add_argument("--limit", type=int, default=None, help="本次最多播放的视频数")
    p_learn.add_argument("--ignore-window", action="store_true",
                         help="忽略每日学习时段限制（仅限验收测试）")

    args = parser.parse_args(argv)
    handlers = {
        "init": cmd_init,
        "login": cmd_login,
        "status": cmd_status,
        "courses": cmd_courses,
        "chapters": cmd_chapters,
        "learn": cmd_learn,
    }
    try:
        return handlers[args.command](args)
    except KeyboardInterrupt:
        print("\n已中断")
        return 130


if __name__ == "__main__":
    sys.exit(main())
