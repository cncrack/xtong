"""自动重登预算制测试（不触网）。"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from xxt.session.relogin import (
    LOGIN_BUDGET_PER_DAY,
    budget_left,
    login_attempts_today,
    login_budget_per_day,
)
from xxt.store.db import connect


class TestLoginBudget(unittest.TestCase):
    def test_budget_depletes_per_account_per_day(self):
        with tempfile.TemporaryDirectory() as td:
            conn = connect(Path(td) / "t.db")
            try:
                self.assertEqual(budget_left(conn, 1), LOGIN_BUDGET_PER_DAY)
                for i in range(LOGIN_BUDGET_PER_DAY):
                    conn.execute(
                        "INSERT INTO run_logs(ts, level, event, detail) VALUES(?,?,?,?)",
                        (time.strftime("%Y-%m-%dT") + "10:00:00", "INFO",
                         "login-attempt", "account:1｜自动重登开始"))
                conn.commit()
                self.assertEqual(login_attempts_today(conn, 1), LOGIN_BUDGET_PER_DAY)
                self.assertEqual(budget_left(conn, 1), 0)
                self.assertEqual(budget_left(conn, 2), LOGIN_BUDGET_PER_DAY)  # 账号隔离
            finally:
                conn.close()

    def test_per_day_override_counts_toward_budget(self):
        """2026-10-27：上限可从 [learn] 覆盖，但默认/钳制仍守住安全线。"""
        with tempfile.TemporaryDirectory() as td:
            conn = connect(Path(td) / "t.db")
            try:
                conn.execute(
                    "INSERT INTO run_logs(ts, level, event, detail) VALUES(?,?,?,?)",
                    (time.strftime("%Y-%m-%dT") + "10:00:00", "INFO",
                     "login-attempt", "account:1｜自动重登开始"))
                conn.commit()
                self.assertEqual(budget_left(conn, 1, per_day=1), 0)
                self.assertEqual(budget_left(conn, 1, per_day=3), 2)
            finally:
                conn.close()

    def test_login_budget_per_day_from_config(self):
        class S:
            def __init__(self, learn):
                self.learn = learn

        self.assertEqual(login_budget_per_day(S({})), LOGIN_BUDGET_PER_DAY)
        self.assertEqual(login_budget_per_day(S({"login_budget_per_day": 3})), 3)
        self.assertEqual(login_budget_per_day(S({"login_budget_per_day": 99})), 5)  # 上限钳制
        self.assertEqual(login_budget_per_day(S({"login_budget_per_day": 0})), 1)   # 不允许关成 0
        self.assertEqual(login_budget_per_day(S({"login_budget_per_day": "x"})),
                         LOGIN_BUDGET_PER_DAY)                                    # 写错回落默认


if __name__ == "__main__":
    unittest.main()
