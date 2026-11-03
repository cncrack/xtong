"""「本次跑几节」可自己设置（2026-11-04 用户要求）。

## 用户问题

「为什么不行，在账号设置页中有个日节数上限这个不起作用吗？如果不能设置，你现在可以改」

## 两层额度不是一回事

| 概念 | 字段 | 谁用 | 含义 |
|---|---|---|---|
| **日上限** | `daily_sections` | `accounts.py` → `run_gxks.py:163` | 一天**总共**最多几节（硬刹车） |
| **本次上限** | `max_sections` | 面板按钮 → `run_gxks.py:74` | **这一次点下去**跑几节 |

原来 `max_sections` 在页面上写死 3，所以用户没法调「这次多跑点」——
但 `daily_sections`（10 节）**一直在起作用**，两者取小：

```python
# run_gxks.py:181
if q["left_sections"] is not None and args.max_sections > q["left_sections"]:
    args.max_sections = int(q["left_sections"])
```

所以把「本次」调大也只会跑到日上限，**不会超刷**。
"""

from __future__ import annotations

import inspect
import re
import unittest

from xxt.panel_gxks.page import PAGE


class TestPerRunSectionCount(unittest.TestCase):
    def test_row_has_number_input(self):
        """账号行要有可输入的节数框（不再是写死的按钮文字）。"""
        self.assertIn('class="sec-n"', PAGE)
        self.assertIn('type="number"', PAGE)
        self.assertIn("本次跑几节", PAGE)

    def test_input_max_capped_by_remaining_quota(self):
        """输入框上限 = 今日剩余额度（再封顶 12），不能被填成超额。"""
        self.assertIn("const cap = left === Infinity ? 12 : Math.min(12, left)", PAGE)
        self.assertIn('max="\' + cap + \'"', PAGE)

    def test_start_reads_input_value(self):
        fn = PAGE.split("function startStudyN(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("box.value", fn)
        self.assertIn("parseInt", fn)
        # 越界钳制
        self.assertIn("n = max", fn)
        self.assertIn("n = 12", fn)

    def test_no_hardcoded_three_left(self):
        """旧的写死 3 必须消失：`Math.min(3, left)` 这种表达不该再出现。"""
        self.assertNotIn("Math.min(3, left)", PAGE,
                         "「本次几节」不能写死 3（日上限另有一层，见模块说明）")

    def test_batch_start_asks_count(self):
        """批量开始也允许自己定（与单账号一致）。"""
        fn = PAGE.split("async function batchAction(", 1)[1].split("\nasync function ", 1)[0]
        self.assertIn("prompt(", fn)
        self.assertIn("max_sections", fn)

    def test_backend_clamps_to_12(self):
        """后端仍兜一层 1~12，防止前端被绕过。"""
        from xxt.panel_gxks import app as A
        src = inspect.getsource(A._study_one)
        self.assertIn("--max-sections", src)
        self.assertIn("min(n, 12)", src, "后端要把本次节数封顶 12")
        self.assertIn("max(1,", src, "后端要把本次节数至少抬到 1")


class TestQuotaStillGuards(unittest.TestCase):
    """调大「本次几节」不会突破日上限（额度刹车仍在）。"""

    def test_script_takes_min_of_run_and_remaining(self):
        from pathlib import Path
        text = Path(__file__).resolve().parents[1].joinpath(
            "scripts", "run_gxks.py").read_text(encoding="utf-8")
        self.assertIn("args.max_sections > q[\"left_sections\"]", text)
        self.assertIn("args.max_sections = int(q[\"left_sections\"])", text)

    def test_daily_sections_is_account_attr(self):
        from xxt.adapters.gxks import accounts as gaccounts
        acc = gaccounts.Account(label="t", port=9222)
        self.assertTrue(hasattr(acc, "daily_sections"))
        self.assertGreater(acc.daily_sections, 0)


if __name__ == "__main__":
    unittest.main()
