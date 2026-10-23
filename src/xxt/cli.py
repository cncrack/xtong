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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="xxt", description="网课自动化学习助手（超星学习通）")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="初始化数据目录与数据库")
    sub.add_parser("login", help="打开浏览器人工登录学习通，保存会话")
    sub.add_parser("status", help="检查已保存会话是否有效")

    args = parser.parse_args(argv)
    handlers = {"init": cmd_init, "login": cmd_login, "status": cmd_status}
    try:
        return handlers[args.command](args)
    except KeyboardInterrupt:
        print("\n已中断")
        return 130


if __name__ == "__main__":
    sys.exit(main())
