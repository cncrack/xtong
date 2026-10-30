"""`scripts/run_gxks.py` 的网络自检接线（B9，2026-10-30）。

钉死三件事：

① **自检没过 → 返回 4，且浏览器根本没被打开**（不访问平台）；
② `--no-netcheck` 跳过自检（调试用），直接去开浏览器；
③ 自检过了才往下走——所以"浏览器连不上"返回 2 而不是 4，
   说明自检那一步确实放行了。

用替身：`netcheck.preflight` 打桩、`gbrowser.open_session` 打桩。
`XXT_ROOT` 指到临时目录，不碰真实 `data/`。
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import run_gxks  # noqa: E402

from xxt.adapters.gxks import browser as gbrowser  # noqa: E402

OK = {"ok": True, "secs": 0.2, "content_ok": True, "advice": "",
      "url": "u", "threshold_secs": 3.0}
BLOCKED = {"ok": False, "secs": 11.0, "content_ok": True, "advice": "改直连",
           "url": "u", "threshold_secs": 3.0}


class TestRunGxksNetcheck(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._old_root = os.environ.get("XXT_ROOT")
        os.environ["XXT_ROOT"] = str(self.root)

    def tearDown(self):
        if self._old_root is None:
            os.environ.pop("XXT_ROOT", None)
        else:
            os.environ["XXT_ROOT"] = self._old_root
        self.tmp.cleanup()

    # ---------------------------------------------------------------- 用例

    def test_blocked_returns_4_and_never_opens_browser(self):
        """自检没过 → 返回 4，**不打开浏览器、不访问平台**。"""
        with mock.patch.object(sys, "argv",
                               ["run_gxks.py", "--label", "test", "--no-relogin"]), \
             mock.patch.object(run_gxks.netcheck, "preflight", return_value=BLOCKED), \
             mock.patch.object(run_gxks.gbrowser, "open_session") as open_session:
            rc = run_gxks.main()
        self.assertEqual(rc, 4)
        open_session.assert_not_called()

    def test_no_netcheck_skips_the_check(self):
        """`--no-netcheck` 时跳过自检，直接去开浏览器（浏览器失败返回 2）。"""
        with mock.patch.object(sys, "argv",
                               ["run_gxks.py", "--label", "test", "--no-relogin",
                                "--no-netcheck"]), \
             mock.patch.object(run_gxks.netcheck, "preflight") as preflight, \
             mock.patch.object(run_gxks.gbrowser, "open_session",
                               side_effect=gbrowser.SessionError("no_browser", "连不上")):
            rc = run_gxks.main()
        self.assertEqual(rc, 2)
        preflight.assert_not_called()

    def test_passing_check_proceeds_to_open_browser(self):
        """自检过了 → 继续往下走（浏览器连不上所以返回 2，**不是 4**）。"""
        with mock.patch.object(sys, "argv",
                               ["run_gxks.py", "--label", "test", "--no-relogin"]), \
             mock.patch.object(run_gxks.netcheck, "preflight", return_value=OK), \
             mock.patch.object(run_gxks.gbrowser, "open_session",
                               side_effect=gbrowser.SessionError("no_browser", "连不上")):
            rc = run_gxks.main()
        self.assertEqual(rc, 2)          # 不是 4 ⇒ 自检放行了


if __name__ == "__main__":
    unittest.main()
