"""给「面板设置（页面上的 reload 开关）+ 多账号运行日志 + 课程 value 回写」补测试。"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from xxt.panel_gxks.page import PAGE
from xxt.store import gxks_panel_settings


def _make_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.commit()
    conn.close()


class TestPanelSettingsStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "t.db"
        _make_db(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def test_defaults_when_empty(self):
        s = gxks_panel_settings.load(self.db)
        self.assertIs(s["reload"], True)
        self.assertEqual(s["poll_seconds"], 4)

    def test_save_and_reload(self):
        gxks_panel_settings.save(self.db, {"reload": False, "poll_seconds": 10})
        s = gxks_panel_settings.load(self.db)
        self.assertIs(s["reload"], False)
        self.assertEqual(s["poll_seconds"], 10)

    def test_poll_seconds_clamped(self):
        gxks_panel_settings.save(self.db, {"poll_seconds": 9999})
        self.assertEqual(gxks_panel_settings.load(self.db)["poll_seconds"], 60)
        gxks_panel_settings.save(self.db, {"poll_seconds": 0})
        self.assertEqual(gxks_panel_settings.load(self.db)["poll_seconds"], 1)

    def test_missing_db_returns_defaults(self):
        s = gxks_panel_settings.load(Path(self.tmp.name) / "nope.db")
        self.assertIs(s["reload"], True)


class TestPanelSettingsPage(unittest.TestCase):
    """页面上的设置入口（用户：「这个设置配置项了，在页面上哪里？」）。"""

    def test_controls_exist(self):
        self.assertIn('id="f-reload"', PAGE)
        self.assertIn('id="f-poll"', PAGE)

    def test_save_handler_exists(self):
        self.assertIn("function savePanelSettings(", PAGE)
        self.assertIn("onclick=\"savePanelSettings()\"", PAGE)

    def test_settings_api_used(self):
        self.assertIn("/api/panel/settings", PAGE)

    def test_poll_interval_is_configurable(self):
        """轮询间隔可配 → 定时器要能重建，不能写死 setInterval(load, 4000)。"""
        self.assertIn("function setPoll(", PAGE)
        self.assertIn("POLL_TIMER = setInterval(load, POLL_MS)", PAGE)

    def test_panel_settings_block_is_not_an_fcard(self):
        """账号设置是三栏布局，多一张 fcard 会破坏它。"""
        view = PAGE.split('id="view-account"', 1)[1].split('id="view-ai"', 1)[0]
        self.assertEqual(view.count('class="fcard"'), 3)


class TestMultiAccountRunLog(unittest.TestCase):
    """多账号时的运行日志：能看到别的账号在跑什么。"""

    def test_runlog_lists_other_jobs(self):
        body = PAGE.split("function renderRunLog(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("还有其他任务在跑", body)
        self.assertIn("jobs.length > 1", body)

    def test_job_snapshot_has_tail_for_each_job(self):
        from xxt.panel_gxks import app
        import inspect
        src = inspect.getsource(app._jobs_snapshot)
        self.assertIn("tail", src)

    def test_jobs_snapshot_includes_all_labels(self):
        """jobs 要含所有账号的任务（不只是当前账号），才能看到别人在跑什么。"""
        from xxt.panel_gxks import app
        import inspect
        src = inspect.getsource(app._jobs_snapshot)
        self.assertIn("_JOBS", src)
        self.assertIn("for", src)


class TestCourseValueWriteback(unittest.TestCase):
    """回写必须按**当前课程 value** 分文件（否则面板与脚本写不同文件）。"""

    def test_driver_has_current_course(self):
        from xxt.adapters.gxks import driver
        self.assertTrue(hasattr(driver.GxksDriver, "current_course"))

    def test_run_script_reads_current_course(self):
        text = Path("scripts/run_gxks.py").read_text(encoding="utf-8")
        self.assertIn("drv.current_course()", text)
        # 回写必须用 course_value，不能用可能为空的 args.course
        self.assertIn("gcache.update_row(Path(\"data/debug\"), acc_label, course_value,", text)
