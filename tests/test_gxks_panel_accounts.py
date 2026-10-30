"""面板账号管理接口（`/api/accounts/save|toggle|delete`）的测试。

守四条边界：

① **回包永不含凭据**：`name` / `pw` 一个字都不能出（面板拿不到，也就没法回显）；
② **在跑的账号不许改/停/删**：改端口会让子进程的归属当场错位；
③ **不能把最后一个账号删光**：删完面板就空了，用户连“新增”按钮都得靠猜；
④ **报错要能读**：端口冲突、代号非法等一律原样回给用户，不吞成 500。

全程替身：临时根目录 + 假账号表，不碰真实 `data/`、不连浏览器、不访问平台。
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from xxt.adapters.gxks import accounts as gaccounts
from xxt.config import Settings
from xxt.panel_gxks import app as panel_app
from xxt.panel_gxks.app import create_app

FAKE_ACCOUNTS = {
    "accounts": [
        {"label": "jia", "display": "甲同学", "name": "s1001", "pw": "pw-A-123",
         "port": 9222, "daily_sections": 8, "daily_minutes": 400},
        {"label": "yi", "display": "乙同学", "port": 9223},
    ]
}


class TestAccountAdmin(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "data").mkdir(parents=True)
        self.path = self.root / "data" / "accounts.local.json"
        self.path.write_text(json.dumps(FAKE_ACCOUNTS, ensure_ascii=False), encoding="utf-8")
        self._old_root = os.environ.get("XXT_ROOT")
        os.environ["XXT_ROOT"] = str(self.root)
        self.settings = Settings(root=self.root)
        self.client = TestClient(create_app(self.settings))
        self._p_alive = mock.patch.object(panel_app.gchrome, "port_alive",
                                          return_value=False)
        self._p_alive.start()

    def tearDown(self):
        mock.patch.stopall()
        panel_app._JOBS.clear()
        if self._old_root is None:
            os.environ.pop("XXT_ROOT", None)
        else:
            os.environ["XXT_ROOT"] = self._old_root
        self.tmp.cleanup()

    # ---------------------------------------------------------------- 工具
    def _rows(self) -> list[dict]:
        return json.loads(self.path.read_text(encoding="utf-8"))["accounts"]

    def _save(self, **body) -> dict:
        return self.client.post("/api/accounts/save", json=body).json()

    # ---------------------------------------------------------------- 新增
    def test_add_account(self):
        body = self._save(label="bing", display="丙同学", port=9224,
                          daily_sections=6, daily_minutes=360)
        self.assertTrue(body["ok"])
        self.assertEqual(body["account"]["label"], "bing")
        self.assertFalse(body["account"]["has_credentials"])     # 没给凭据
        self.assertEqual([r["label"] for r in self._rows()], ["jia", "yi", "bing"])

    def test_add_account_with_credentials_but_never_echoes_them(self):
        body = self._save(label="bing", port=9224, name="s3003", pw="pw-C-secret")
        self.assertTrue(body["ok"])
        self.assertTrue(body["account"]["has_credentials"])
        raw = json.dumps(body, ensure_ascii=False)
        for secret in ("s3003", "pw-C-secret", '"pw"', '"name"'):
            self.assertNotIn(secret, raw)

    def test_duplicate_port_is_rejected_with_readable_message(self):
        body = self._save(label="bing", port=9222)
        self.assertFalse(body["ok"])
        self.assertIn("9222", body["error"])
        self.assertIn("jia", body["error"])
        self.assertEqual(len(self._rows()), 2)                   # 没写进去

    def test_bad_label_is_rejected(self):
        body = self._save(label="甲同学", port=9225)
        self.assertFalse(body["ok"])
        self.assertIn("代号", body["error"])

    def test_out_of_range_quota_is_rejected_not_clamped(self):
        body = self._save(label="bing", port=9224, daily_sections=999)
        self.assertFalse(body["ok"])
        self.assertIn("日节数上限", body["error"])          # 报错用中文名，不用内部键名

    def test_add_writes_ops_log(self):
        self._save(label="bing", port=9224)
        rows = self.client.get("/api/ops").json()["rows"]
        self.assertEqual(rows[0]["action"], "account-add")
        self.assertIn("bing", rows[0]["detail"])

    # ---------------------------------------------------------------- 修改
    def test_edit_account_keeps_credentials_when_password_blank(self):
        body = self._save(label="jia", original_label="jia", port=9222,
                          daily_sections=12, daily_minutes=700, name="", pw="")
        self.assertTrue(body["ok"])
        row = self._rows()[0]
        self.assertEqual(row["daily_sections"], 12)
        self.assertEqual(row["name"], "s1001")                   # 空 = 不改
        self.assertEqual(row["pw"], "pw-A-123")
        self.assertTrue(body["account"]["has_credentials"])

    def test_edit_writes_ops_log_as_edit(self):
        self._save(label="jia", original_label="jia", port=9222, daily_sections=5)
        rows = self.client.get("/api/ops").json()["rows"]
        self.assertEqual(rows[0]["action"], "account-edit")

    def test_edit_log_records_old_to_new(self):
        """旧值必须在写之前取：写完再读到的就是新值，“旧→新”会永远显示没变。"""
        self._save(label="jia", original_label="jia", port=9222, daily_sections=5,
                   daily_minutes=700)
        detail = self.client.get("/api/ops").json()["rows"][0]["detail"]
        self.assertIn("日节数 8 → 5", detail)            # 夹具里 jia 原来是 8 节 / 400 分
        self.assertIn("日时长 400 → 700", detail)

    def test_edit_unknown_account_is_rejected(self):
        body = self._save(label="ghost", original_label="ghost", port=9226)
        self.assertFalse(body["ok"])
        self.assertIn("ghost", body["error"])

    # ---------------------------------------------------------------- 启停 / 删除
    def test_toggle(self):
        body = self.client.post("/api/accounts/toggle",
                                json={"label": "yi", "enabled": False}).json()
        self.assertTrue(body["ok"])
        self.assertFalse(self._rows()[1]["enabled"])
        rows = self.client.get("/api/ops").json()["rows"]
        self.assertEqual(rows[0]["action"], "account-toggle")

    def test_delete_requires_confirm_flag(self):
        body = self.client.post("/api/accounts/delete", json={"label": "yi"}).json()
        self.assertFalse(body["ok"])
        self.assertIn("确认", body["error"])
        self.assertEqual(len(self._rows()), 2)

    def test_delete_last_account_is_refused(self):
        self.client.post("/api/accounts/delete", json={"label": "yi", "confirm": True})
        body = self.client.post("/api/accounts/delete",
                                json={"label": "jia", "confirm": True}).json()
        self.assertFalse(body["ok"])
        self.assertIn("最后一个", body["error"])
        self.assertEqual([r["label"] for r in self._rows()], ["jia"])

    def test_delete_ok(self):
        body = self.client.post("/api/accounts/delete",
                                json={"label": "yi", "confirm": True}).json()
        self.assertTrue(body["ok"])
        self.assertEqual([r["label"] for r in self._rows()], ["jia"])
        rows = self.client.get("/api/ops").json()["rows"]
        self.assertEqual(rows[0]["action"], "account-del")

    # ---------------------------------------------------------------- 在跑保护
    def test_all_writes_refused_while_job_running(self):
        proc = mock.Mock(pid=1)
        proc.poll.return_value = None
        panel_app._JOBS["jia"] = {"proc": proc, "label": "jia", "kind": "study",
                                  "since": "12:00:00", "log": Path("nope.log"),
                                  "detail": ""}
        for resp in (
            self._save(label="jia", original_label="jia", port=9222, daily_sections=9),
            self.client.post("/api/accounts/toggle",
                             json={"label": "jia", "enabled": False}).json(),
            self.client.post("/api/accounts/delete",
                             json={"label": "jia", "confirm": True}).json(),
        ):
            self.assertFalse(resp["ok"])
            self.assertIn("任务在跑", resp["error"])
        self.assertEqual(len(self._rows()), 2)

    # ---------------------------------------------------------------- 额度口径
    def test_card_left_is_null_when_sections_unlimited(self):
        """日节数写 0 = 不限：卡片必须回 `left_sections: null`，不能回 0（会被读成“用完”）。"""
        self._save(label="jia", original_label="jia", port=9222, daily_sections=0,
                   daily_minutes=0)
        with mock.patch.object(panel_app.gchrome, "port_alive", return_value=False):
            cards = self.client.get("/api/state").json()["cards"]
        jia = next(c for c in cards if c["label"] == "jia")
        self.assertIsNone(jia["today"]["left_sections"])
        self.assertEqual(jia["limits"]["sections"], 0)

    def test_card_has_by_course_breakdown(self):
        from xxt.adapters.gxks import ledger as gledger
        p = self.settings.data_dir / "gxks_ledger.jsonl"
        gledger.record(p, account="jia", section_id="1289", minutes=45, course="32")
        gledger.record(p, account="jia", section_id="2001", minutes=30, course="33")
        with mock.patch.object(panel_app.gchrome, "port_alive", return_value=False):
            cards = self.client.get("/api/state").json()["cards"]
        jia = next(c for c in cards if c["label"] == "jia")
        self.assertEqual(jia["today"]["sections"], 2)          # 账号合计仍是 2
        self.assertEqual(set(jia["today"]["by_course"]), {"32", "33"})

    def test_state_rows_expose_credentials_boolean_only(self):
        rows = self.client.get("/api/state").json()["accounts"]
        self.assertTrue(rows[0]["has_credentials"])
        self.assertFalse(rows[1]["has_credentials"])
        raw = json.dumps(rows, ensure_ascii=False)
        self.assertNotIn("pw-A-123", raw)
        self.assertNotIn("s1001", raw)


class TestAccountErrorSurfaces(unittest.TestCase):
    """`AccountError` 必须变成可读回包，而不是 500。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "data").mkdir(parents=True)
        (self.root / "data" / "accounts.local.json").write_text(
            json.dumps(FAKE_ACCOUNTS, ensure_ascii=False), encoding="utf-8")
        self._old_root = os.environ.get("XXT_ROOT")
        os.environ["XXT_ROOT"] = str(self.root)
        self.client = TestClient(create_app(Settings(root=self.root)))

    def tearDown(self):
        if self._old_root is None:
            os.environ.pop("XXT_ROOT", None)
        else:
            os.environ["XXT_ROOT"] = self._old_root
        self.tmp.cleanup()

    def test_oserror_is_reported_as_error_not_500(self):
        with mock.patch.object(gaccounts, "save_account",
                               side_effect=OSError("磁盘满了")):
            r = self.client.post("/api/accounts/save",
                                 json={"label": "bing", "port": 9224})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["ok"])
        self.assertIn("磁盘满了", r.json()["error"])


if __name__ == "__main__":
    unittest.main()
