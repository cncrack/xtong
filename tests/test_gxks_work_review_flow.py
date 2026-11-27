"""G-6 人工复核交卷（2026-11-25 用户拍板）。

用户要求：「填题 AI 做 -> 填完停下来给你看逐题核对单 -> 你确认没问题 ->
你点交卷 -> 脚本收到「你交了」的信号 -> 立刻回收判分页入库」。

设计：
- `gxks.work_review` 开关（默认关）+ `gxks.work_review_timeout` 超时（默认 600s）
- 脚本填完 -> 写 `work_review_<label>_<ex>.json`（phase=waiting，存 raw_rows/type_map）
  -> 断开 CDP（否则吞 user 手动交卷的 confirm）-> 驻留轮询 phase
- 面板 `POST /api/work/submit` 把 phase 改 confirm
- 脚本收到 -> 重连 CDP -> 等判分页 -> 回收入库
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

SCRIPT = (Path(__file__).resolve().parents[1] / "scripts" / "gxks_work.py").read_text(
    encoding="utf-8")
APP = (Path(__file__).resolve().parents[1] / "src" / "xxt" / "panel_gxks" / "app.py"
       ).read_text(encoding="utf-8")
PAGE = (Path(__file__).resolve().parents[1] / "src" / "xxt" / "panel_gxks" / "page.py"
        ).read_text(encoding="utf-8")


class TestWorkReviewScript(unittest.TestCase):
    def test_review_fn_exists(self):
        self.assertIn("def _await_confirm_and_reclaim(", SCRIPT)

    def test_reads_both_config_keys(self):
        self.assertIn('gxks_cfg.get("work_review"', SCRIPT)
        self.assertIn('gxks_cfg.get("work_review_timeout"', SCRIPT)

    def test_disconnects_before_waiting(self):
        """驻留前必须断开 CDP，否则吞掉用户手动交卷的 confirm（Q31）。"""
        seg = SCRIPT.split("if work_review and result.get(\"filled\"):", 1)[1][:600]
        close_at = seg.index("session.close()")
        call_at = seg.index("_await_confirm_and_reclaim(")
        self.assertLess(close_at, call_at, "必须先 close 再驻留等待")

    def test_signal_has_raw_rows(self):
        """raw_rows/type_map 必须存进信号文件（重连后拿不到答题帧）。"""
        seg = SCRIPT.split("def _await_confirm_and_reclaim(", 1)[1][:2000]
        self.assertIn('"raw_rows": raw_rows', seg)
        self.assertIn('"type_map": type_map', seg)

    def test_cancel_phase_exits_cleanly(self):
        seg = SCRIPT.split("def _await_confirm_and_reclaim(", 1)[1][:3000]
        self.assertIn('phase == "cancel"', seg)

    def test_cleans_signal_on_exit(self):
        seg = SCRIPT.split("def _await_confirm_and_reclaim(", 1)[1][:3000]
        self.assertIn("signal.unlink(missing_ok=True)", seg)

    def test_only_when_filled(self):
        """reference 模式不动页面，不该驻留。"""
        self.assertIn('work_review and result.get("filled")', SCRIPT)


class TestWorkReviewPanel(unittest.TestCase):
    def test_submit_endpoint(self):
        self.assertIn("def work_submit(", APP)

    def test_submit_writes_confirm(self):
        seg = APP.split("def work_submit(", 1)[1][:1200]
        self.assertIn('data["phase"] = "confirm"', seg)
        self.assertIn("work_review_", seg)

    def test_state_exposes_review(self):
        self.assertIn("def _work_review_state(", APP)
        self.assertIn('"work_review": _work_review_state(', APP)

    def test_state_only_waiting(self):
        seg = APP.split("def _work_review_state(", 1)[1][:900]
        self.assertIn('!= "waiting"', seg)

    def test_frontend_button(self):
        self.assertIn("confirmWorkSubmit", PAGE)
        self.assertIn("确认交卷并入库", PAGE)
        self.assertIn("/api/work/submit", PAGE)

    def test_button_only_when_review_pending(self):
        seg = PAGE.split("const rv = state.work_review;", 1)[1][:400]
        self.assertIn("rv.exercise_id", seg)


class TestWorkReviewStateHelper(unittest.TestCase):
    def test_returns_none_without_file(self):
        from xxt.panel_gxks import app as panel_app
        from xxt.config import Settings
        with tempfile.TemporaryDirectory() as d:
            s = Settings(root=Path(d))
            self.assertIsNone(panel_app._work_review_state(s, "nobody"))

    def test_returns_waiting(self):
        from xxt.panel_gxks import app as panel_app
        from xxt.config import Settings
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            s = Settings(root=root)
            (root / "data" / "tmp").mkdir(parents=True, exist_ok=True)
            (root / "data" / "tmp" / "work_review_a1_15263.json").write_text(
                json.dumps({"label": "a1", "exercise_id": "15263",
                            "phase": "waiting", "result_file": "x.json"}),
                encoding="utf-8")
            got = panel_app._work_review_state(s, "a1")
            self.assertIsNotNone(got)
            self.assertEqual(got["exercise_id"], "15263")

    def test_confirm_not_returned(self):
        from xxt.panel_gxks import app as panel_app
        from xxt.config import Settings
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            s = Settings(root=root)
            (root / "data" / "tmp").mkdir(parents=True, exist_ok=True)
            (root / "data" / "tmp" / "work_review_a1_15263.json").write_text(
                json.dumps({"label": "a1", "exercise_id": "15263",
                            "phase": "confirm"}),
                encoding="utf-8")
            self.assertIsNone(panel_app._work_review_state(s, "a1"))



class TestGradedWaitTimeout(unittest.TestCase):
    """回归：等判分页的 60 秒写死，实拍超时（2026-11-27）。

    用户点「确认交卷」后要切到浏览器窗口手动点交卷 + 确认弹窗，
    原来 wait_graded 默认 60 秒 -> 日志报
    [WARN] 交卷后没等到判分页（viewExam.aspx）。
    修：新增配置 work_review_graded_timeout（默认 300 秒）并传给 wait_graded。
    """

    def test_config_key_exists(self):
        self.assertIn("work_review_graded_timeout", SCRIPT)

    def _fn_body(self):
        """取 _await_confirm_and_reclaim 整个函数体（到下一个顶层 def 为止）。"""
        seg = SCRIPT.split("def _await_confirm_and_reclaim(", 1)[1]
        return seg.split(chr(10) + "def ", 1)[0]

    def test_waits_via_http_not_cdp(self):
        """等判分页必须用**纯 HTTP 只读轮询**，不能提前连 CDP（Q31：会吞掉交卷 confirm）。

        2026-11-27 实拍：用户「点了交卷没有反映、没有弹窗」——因为原实现收到确认后
        立刻 connect_over_cdp，Playwright 连着就自动 dismiss 了 confirm。
        """
        seg = self._fn_body()
        self.assertIn("page_urls(", seg)
        poll_at = seg.index("page_urls(")
        cdp_at = seg.index("open_session(")
        self.assertLess(poll_at, cdp_at, "必须先 HTTP 轮询 URL，确认判分页出现后才连 CDP")

    def test_graded_timeout_used_in_poll(self):
        seg = self._fn_body()
        self.assertIn("deadline = time.monotonic() + graded_timeout", seg)

    def test_default_is_300(self):
        seg = SCRIPT.split("work_review_graded_timeout", 1)[1][:40]
        self.assertIn("300", seg)

    def test_signature_accepts_it(self):
        seg = SCRIPT.split("def _await_confirm_and_reclaim(", 1)[1][:400]
        self.assertIn("graded_timeout", seg)


if __name__ == "__main__":
    unittest.main()
