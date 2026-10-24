"""自动重登（PLAN §5.1）：凭据解密 → 代填登录 → 登录预算制 → 验证码/错误转人工。

登录预算制（安全底线，硬编码——v0.8 参数三层分工）：每账号每 24h（按自然日）真实登录
尝试 ≤2 次；失败即冷却转人工，绝不连续尝试。所有尝试/结果记 run_logs（event=login-*）。

⚠️ passport2 表单选择器为通行结构，标注"待核对"——首次实况验证时修正（与人工登录页同一页面）。
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

from ..core.crypto import decrypt
from ..log import get_logger

log = get_logger("session.relogin")

LOGIN_BUDGET_PER_DAY = 2  # 安全底线，硬编码不上配置

# 2026-10-24 两次实况核对（匿名公开页）：#phone/#pwd/#loginBtn 已验证；
# 验证码容器为常驻空 div#captcha（有子元素=验证码出现）；错误提示是无样式 <p> 文本"…密码错误"
_FORM = {
    "username": "input#phone",
    "password": "input#pwd",
    "submit": "#loginBtn",
    "captcha_has_content": "#captcha *",      # 容器非空 → 验证码出现，转人工
    "error_text": "密码错误",                   # 平台明文错误文案（wrong-password 判定）
}


def login_attempts_today(conn: sqlite3.Connection, account_id: int) -> int:
    today = time.strftime("%Y-%m-%d")
    return conn.execute(
        "SELECT COUNT(*) AS c FROM run_logs WHERE event='login-attempt' AND ts LIKE ? "
        "AND detail LIKE ?",
        (f"{today}%", f"account:{account_id}%"),
    ).fetchone()["c"]


def budget_left(conn: sqlite3.Connection, account_id: int) -> int:
    return max(0, LOGIN_BUDGET_PER_DAY - login_attempts_today(conn, account_id))


def _log_login(conn: sqlite3.Connection, account_id: int, event: str, detail: str) -> None:
    conn.execute(
        "INSERT INTO run_logs(ts, level, event, detail) VALUES(?,?,?,?)",
        (time.strftime("%Y-%m-%dT%H:%M:%S"),
         "INFO" if event == "login-success" else "WARNING",
         event, f"account:{account_id}｜{detail}"),
    )
    conn.commit()


def auto_relogin(conn: sqlite3.Connection, key: bytes, login_url: str, state_path: Path,
                 account_id: int, username_enc: bytes, password_enc: bytes) -> str:
    """尝试自动重登。返回 ok | budget | captcha | wrong-password | no-cred | unknown。

    ok：登录成功且已回写 storage_state；其余均为转人工场景（绝不重试，由调用方告警）。
    """
    if budget_left(conn, account_id) <= 0:
        _log_login(conn, account_id, "login-blocked", "当日登录预算耗尽，冷却转人工")
        return "budget"
    _log_login(conn, account_id, "login-attempt", "自动重登开始")

    from playwright.sync_api import sync_playwright

    username = decrypt(key, username_enc)
    password = decrypt(key, password_enc)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        from ..core.stealth import hardened_context
        context = hardened_context(browser)
        page = context.new_page()
        outcome = "unknown"
        try:
            page.goto(login_url, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(2_000)
            page.locator(_FORM["username"]).first.fill(username, timeout=10_000)
            page.locator(_FORM["password"]).first.fill(password, timeout=10_000)
            page.locator(_FORM["submit"]).first.click(timeout=10_000)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                cookies = context.cookies()
                if any(c["name"] in ("uid", "_uid", "vc3") and c["value"] for c in cookies):
                    outcome = "ok"
                    break
                if page.locator(_FORM["captcha_has_content"]).count() > 0:
                    outcome = "captcha"
                    break
                if page.locator(f"text={_FORM['error_text']}").count() > 0:
                    outcome = "wrong-password"
                    break
                page.wait_for_timeout(1_000)
            if outcome == "ok":
                context.storage_state(path=str(state_path))
        except Exception as exc:
            log.error("自动重登异常：%r", exc)
            outcome = "unknown"
        finally:
            browser.close()
    _log_login(conn, account_id, f"login-{outcome}", "自动重登结束")
    return outcome


def maybe_auto_relogin(settings, conn: sqlite3.Connection, account_row) -> str | None:
    """status/learn 探测失效后的统一入口：有凭据且预算允许才尝试。"""
    if not account_row["username_enc"]:
        return None  # 未存凭据（如 default 手工登录账号），转人工 xxt login
    from ..core.crypto import master_key
    key = master_key(settings.data_dir)
    return auto_relogin(
        conn, key, settings.platform["login_url"],
        settings.data_dir / "accounts" / str(account_row["id"]) / "state.json",
        account_row["id"], account_row["username_enc"], account_row["password_enc"],
    )
