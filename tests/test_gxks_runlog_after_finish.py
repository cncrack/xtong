"""任务结束后仍能回看日志（2026-11-03 用户要求）。

## 背景

用户问「运行日志怎么没有了」。查明：任务一结束后端就 `_JOBS.pop()`，页面立刻
变成「当前没有运行中的任务」——**刚跑完的日志当场消失**，而那正是用户最需要
回看的时刻（成功没？哪节没达标？为什么提前停？）。

## 两条必须同时成立

1. **看得到**：`/api/state` 要带上刚结束那次的日志尾巴（`alive: false`）。
2. **不能误判**：`_stop` / 额度计算 / 「有无任务在跑」的判断**必须**仍然把
   已结束的任务当「没有」——否则停止按钮会去杀一个已死的 pid，
   还会挡住新一轮启动。
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from xxt.config import Settings
from xxt.panel_gxks import app as panel_app
from xxt.panel_gxks.app import create_app

FAKE_ACCOUNTS = {
    "accounts": [
        {"label": "jia", "display": "甲同学", "name": "s1001", "pw": "pw-A-123",
         "port": 9222, "profile": "D:/profiles/jia"},
    ]
}


class _Proc:
    """假子进程：`alive=False` 表示已退出。"""

    pid = 4242

    def __init__(self, alive: bool):
        self._alive = alive

    def poll(self):
        return None if self._alive else 0


class TestFinishedJobRetained(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "data").mkdir(parents=True)
        (self.root / "data" / "accounts.local.json").write_text(
            json.dumps(FAKE_ACCOUNTS, ensure_ascii=False), encoding="utf-8")
        self._old = os.environ.get("XXT_ROOT")
        os.environ["XXT_ROOT"] = str(self.root)
        self.settings = Settings(root=self.root)
        self.client = TestClient(create_app(self.settings))
        panel_app._JOBS.clear()
        panel_app._LAST_JOBS.clear()

    def tearDown(self):
        panel_app._JOBS.clear()
        panel_app._LAST_JOBS.clear()
        if self._old is None:
            os.environ.pop("XXT_ROOT", None)
        else:
            os.environ["XXT_ROOT"] = self._old
        self.tmp.cleanup()

    def _put_job(self, alive: bool, log_text: str = "[OK] 开播: True\n"
                                                   "[22:22:57] progress 3/38 learning\n"):
        log = self.root / "data" / "logs" / "x.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(log_text, encoding="utf-8")
        # ⚠ 键是 `label|kind`（2026-11-04 改）：同一账号的 study/sections 各占一条
        panel_app._JOBS[panel_app._job_key("jia", "study")] = {
            "proc": _Proc(alive), "kind": "study", "label": "jia",
            "since": "22:22:01", "log": log, "detail": "--max-sections 3"}

    # ------------------------------------------------------------------ 看得到

    def test_finished_job_log_still_visible_in_state(self):
        self._put_job(alive=False)
        body = self.client.get("/api/state?label=jia").json()
        jobs = body["jobs"]
        self.assertEqual(len(jobs), 1, "刚结束的任务要留在回包里")
        self.assertFalse(jobs[0]["alive"])
        self.assertTrue(any("progress" in ln for ln in jobs[0]["tail"]),
                        "日志尾巴要能看到")
        self.assertIn("ended_at", jobs[0], "要带结束时间，便于和正在跑区分")

    def test_finished_job_survives_multiple_polls(self):
        """4 秒轮询不该把它清掉（第二次、第三次仍要能看）。"""
        self._put_job(alive=False)
        self.client.get("/api/state?label=jia")
        self.client.get("/api/state?label=jia")
        jobs = self.client.get("/api/state?label=jia").json()["jobs"]
        self.assertEqual(len(jobs), 1)
        self.assertTrue(jobs[0]["tail"])

    def test_new_job_replaces_finished_one(self):
        """同一账号跑新任务时，旧的那条要被替换（不能越积越多）。"""
        self._put_job(alive=False)
        self.client.get("/api/state?label=jia")          # 让旧任务进 _LAST_JOBS
        self._put_job(alive=True, log_text="[OK] 新任务开播\n")
        jobs = self.client.get("/api/state?label=jia").json()["jobs"]
        self.assertEqual(len(jobs), 1, "同账号只留一条")
        self.assertTrue(jobs[0]["alive"])

    # ------------------------------------------------------------------ 不能误判

    def test_default_snapshot_excludes_finished(self):
        """默认调用（判断有没有在跑）不能带上已结束的。"""
        self._put_job(alive=False)
        self.assertEqual(len(panel_app._jobs_snapshot()), 0)

    def test_stop_does_not_find_finished_job(self):
        """已结束的任务不能被「停止」当成活的去杀。"""
        self._put_job(alive=False)
        body = self.client.post("/api/stop", json={"label": "jia"}).json()
        self.assertFalse(body.get("ok"), "已结束的任务不该能停")

    def test_running_job_not_duplicated(self):
        """正在跑的任务只出现一次（不能既在 _JOBS 又在 _LAST_JOBS 里重复）。"""
        self._put_job(alive=True)
        jobs = panel_app._jobs_snapshot(include_finished=True)
        self.assertEqual(len(jobs), 1)


class TestRunLogRendersFinished(unittest.TestCase):
    """前端要能渲染「已结束」态（不只是后端给数据）。"""

    def test_page_has_finished_branch(self):
        from xxt.panel_gxks.page import PAGE
        self.assertIn("runlog-done", PAGE, "要有「任务已结束」提示条样式")
        self.assertIn("任务已结束", PAGE)
        # 必须优先挑 alive 的，没活的再回退
        body = PAGE.split("function renderRunLog(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("j.alive", body)
        # 「已结束 → 结束时刻」这段在 2026-11-07 抽到了 `logMetaHtml`（两个渲染路径共用），
        # 所以断言看那里
        helper = PAGE.split("function logMetaHtml(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("ended_at", helper)

    def test_other_running_jobs_still_listed(self):
        from xxt.panel_gxks.page import PAGE
        body = PAGE.split("function renderRunLog(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("还有其他任务在跑", body)


if __name__ == "__main__":
    unittest.main()
