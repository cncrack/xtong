"""多账号卡片（`/api/state` 的 `cards`）测试。

卡片是“一屏看全所有账号”的唯一数据源，所以守四条：
① 每个账号都有一张，顺序与账号表一致，`display` 缺省有兜底；
② 今日额度只认**本地账本**（今天、本账号），跨天/跨账号不能串；
③ 课程进度来自 `sections_<账号>.json` 缓存，没读过就如实说“没有”，不瞎编；
④ 回包里**不得出现凭据**（连 cookie 值也不行）。

全程替身：临时根目录 + 假账号表 + 假账本，不碰真实 `data/`、不连浏览器、不访问平台。
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from xxt.adapters.gxks import browser as gbrowser
from xxt.adapters.gxks import ledger as gledger
from xxt.config import Settings
from xxt.panel_gxks.app import create_app

FAKE_ACCOUNTS = {
    "accounts": [
        {"label": "jia", "display": "甲同学", "name": "s1001", "pw": "pw-A-123",
         "port": 9222, "daily_sections": 8, "daily_minutes": 400},
        {"label": "yi", "display": "乙同学", "port": 9223},          # 没凭据
        # 停用账号也必须带自己的端口：`parse_accounts` 会把端口重复的条目
        # 整条跳过（一账号一端口），不写 port 就会默认 9222 撞上甲同学而消失
        {"label": "bing", "port": 9224, "enabled": False},
    ]
}

SECTIONS = {
    "ok": True, "label": "jia", "read_at": "2026-10-30 13:04:53",
    "pager": {"current": 1, "total": 1},
    "summary": {"sections": 9, "done": 9, "practice_ready": 9, "practice_locked": 0,
                "minutes_done": 543, "minutes_required": 340},
    "rows": [{"idx": 0, "name": "1.1 国防概述", "state": "done",
              "required_minutes": 45, "done_minutes": 236,
              "category": "军事理论（字幕版）"}],
}


class TestAccountCards(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "data" / "debug").mkdir(parents=True)
        (self.root / "data" / "accounts.local.json").write_text(
            json.dumps(FAKE_ACCOUNTS, ensure_ascii=False), encoding="utf-8")
        self._old_root = os.environ.get("XXT_ROOT")
        os.environ["XXT_ROOT"] = str(self.root)
        self.settings = Settings(root=self.root)
        self.client = TestClient(create_app(self.settings))

    def tearDown(self):
        if self._old_root is None:
            os.environ.pop("XXT_ROOT", None)
        else:
            os.environ["XXT_ROOT"] = self._old_root
        self.tmp.cleanup()

    # ---------------------------------------------------------------- 工具
    def _cards(self) -> list[dict]:
        with mock.patch("xxt.panel_gxks.app.gchrome.port_alive", return_value=False):
            body = self.client.get("/api/state").json()
        return body["cards"]

    def _write_sections(self) -> None:
        (self.root / "data" / "debug" / "sections_jia.json").write_text(
            json.dumps(SECTIONS, ensure_ascii=False), encoding="utf-8")

    # ---------------------------------------------------------------- 基本
    def test_every_account_gets_a_card_in_table_order(self):
        cards = self._cards()
        self.assertEqual([c["label"] for c in cards], ["jia", "yi", "bing"])
        self.assertEqual(cards[1]["display"], "乙同学")
        self.assertEqual(cards[2]["display"], "bing")       # 没配 display 就用代号

    def test_disabled_account_is_flagged(self):
        self.assertFalse(self._cards()[2]["enabled"])
        self.assertTrue(self._cards()[0]["enabled"])

    def test_credentials_follow_the_account_not_the_legacy_file(self):
        cards = self._cards()
        self.assertTrue(cards[0]["credentials"])            # 账号表里有自己的 name/pw
        self.assertFalse(cards[1]["credentials"])           # 没配 → 不能假“已配置”

    def test_no_credentials_or_cookie_values_in_payload(self):
        self._write_sections()
        path = gbrowser.state_path(self.settings, "jia")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"cookies": [
            {"name": ".ASPXAUTH", "value": "SECRET-TICKET",
             "domain": "www.gaoxiaokaoshi.com", "path": "/", "expires": -1}],
            "origins": []}), encoding="utf-8")
        with mock.patch("xxt.panel_gxks.app.gchrome.port_alive", return_value=True):
            raw = self.client.get("/api/state").text
        # `"name"` 不列入：`sections` 缓存里的 `rows[].name` 是节名，本来就该出现
        for secret in ("SECRET-TICKET", "pw-A-123", "s1001", '"pw"'):
            self.assertNotIn(secret, raw)

    # ---------------------------------------------------------------- 今日额度
    def test_today_counts_come_from_the_ledger(self):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        gledger.record(account="jia", section_id="1289", minutes=45)
        gledger.record(account="jia", section_id="1289", minutes=45)   # 同节重跑只算一次
        gledger.record(account="jia", section_id="1290", minutes=20)
        gledger.record(account="yi", section_id="3001", minutes=99)    # 别人的不算给甲
        gledger.record(account="jia", section_id="1200", minutes=60,
                       at=datetime.fromisoformat(yesterday + "T10:00:00"))

        jia = self._cards()[0]
        self.assertEqual(jia["today"]["sections"], 2)
        # 节数去重、分钟按实际累加（账本既有口径：看两遍确实花了两次时间）
        self.assertEqual(jia["today"]["minutes"], 110.0)
        self.assertEqual(jia["limits"]["sections"], 8)
        self.assertEqual(jia["today"]["left_sections"], 6)      # 8 - 2

        yi = self._cards()[1]
        self.assertEqual(yi["today"]["sections"], 1)            # 只算自己的

    def test_left_never_goes_negative(self):
        for i in range(10):                                     # 超过 8 节日上限
            gledger.record(account="jia", section_id=str(1000 + i), minutes=10)
        self.assertEqual(self._cards()[0]["today"]["left_sections"], 0)

    # ---------------------------------------------------------------- 课程缓存
    def test_course_progress_comes_from_sections_cache(self):
        self._write_sections()
        cards = self._cards()
        self.assertEqual(cards[0]["course"], "军事理论（字幕版）")
        self.assertEqual(cards[0]["sections"],
                         {"done": 9, "total": 9, "read_at": "2026-10-30 13:04:53"})
        self.assertEqual(cards[1]["course"], "")                # 没读过就不能编

    def test_missing_cache_is_reported_as_zero(self):
        self.assertEqual(self._cards()[1]["sections"],
                         {"done": 0, "total": 0, "read_at": ""})

    # ---------------------------------------------------------------- 序号兜底
    def test_seq_falls_back_without_collision_and_without_writing(self):
        """老账号（从单账号文件迁移的 `default`）没有 seq，表格 `#` 列不能空着。

        三条约束：
        ① 兜底值**不能与用户手填的序号撞车**（实测踩过：`default` 兜底成 1，
           而夹具里 `yi` 手填 1 → 两行都显示 #1）；
        ② 只算**显示值、不写盘**（写盘会把"看一眼"变成"改数据"）；
        ③ 用户手填的序号不许被覆盖。
        """
        p = self.root / "data" / "accounts.local.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        data["accounts"][1]["seq"] = 1          # yi 手填 1
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        rows = self.client.get("/api/state").json()["accounts"]
        seqs = [r["profile_fields"]["seq"] for r in rows]
        self.assertEqual(rows[1]["profile_fields"]["seq"], 1)   # 手填的保留
        self.assertEqual(len(set(seqs)), len(seqs), f"序号重复：{seqs}")
        # 文件里仍然没有给 jia 写入 seq（证明没写盘）
        raw = json.loads(p.read_text(encoding="utf-8"))
        self.assertNotIn("seq", raw["accounts"][0])

    # ---------------------------------------------------------------- B1：按课程分文件
    def test_legacy_cache_still_readable(self):
        """升级前的老文件 `sections_<代号>.json` 必须还能读到，不能“升级即空白”。"""
        self._write_sections()                                  # 写的就是老文件名
        self.assertEqual(self._cards()[0]["sections"]["total"], 9)

    def test_course_cache_preferred_over_legacy(self):
        """带课程的新文件优先于老文件（否则新数据会被旧数据盖住）。"""
        self._write_sections()                                  # 老文件：9 节
        newer = dict(SECTIONS, read_at="2026-10-30 15:00:00",
                     summary={"sections": 12, "done": 3, "practice_ready": 3,
                              "practice_locked": 0, "minutes_done": 0,
                              "minutes_required": 500},
                     course="32")
        (self.root / "data" / "debug" / "sections_jia_32.json").write_text(
            json.dumps(newer, ensure_ascii=False), encoding="utf-8")
        card = self._cards()[0]
        self.assertEqual(card["sections"]["total"], 12)          # 用了新文件
        self.assertEqual(card["sections"]["read_at"], "2026-10-30 15:00:00")
        self.assertEqual(card["course_value"], "32")

    def test_courses_cached_counts_all_files(self):
        self._write_sections()
        for course in ("32", "33"):
            (self.root / "data" / "debug" / f"sections_jia_{course}.json").write_text(
                json.dumps(dict(SECTIONS, course=course), ensure_ascii=False),
                encoding="utf-8")
        self.assertEqual(self._cards()[0]["courses_cached"], 3)  # 老文件 + 2 门课
        self.assertEqual(self._cards()[1]["courses_cached"], 0)

    def test_state_reads_cache_for_requested_course(self):
        for course, total in (("32", 9), ("33", 5)):
            (self.root / "data" / "debug" / f"sections_jia_{course}.json").write_text(
                json.dumps(dict(SECTIONS, course=course,
                                summary=dict(SECTIONS["summary"], sections=total)),
                           ensure_ascii=False), encoding="utf-8")
        with mock.patch("xxt.panel_gxks.app.gchrome.port_alive", return_value=False):
            body = self.client.get("/api/state?label=jia&course=33").json()
        self.assertEqual(body["course"], "33")
        self.assertEqual(body["sections"]["summary"]["sections"], 5)

    # ---------------------------------------------------------------- 运行中
    def test_running_job_is_attached_to_its_own_card(self):
        job = {"label": "yi", "kind": "study", "since": "14:02:14"}
        with mock.patch("xxt.panel_gxks.app._jobs_snapshot", return_value=[job]):
            cards = self._cards()
        self.assertIsNone(cards[0]["job"])
        self.assertEqual(cards[1]["job"], {"kind": "study", "since": "14:02:14"})


if __name__ == "__main__":
    unittest.main()
