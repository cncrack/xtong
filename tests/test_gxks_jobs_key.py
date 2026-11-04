"""`_JOBS` 的键必须是 `label|kind`（2026-11-04 修的真 bug）。

## 现象

12:03 的定时汇报里，**刷课任务明明在跑**（日志每分钟写 `progress`），
但面板的 `/api/state` 里 **jobs 只有那条已结束的「刷新节列表」** —— study 不见了。
用户看到的面板也是「没有任务在跑」。

## 根因

`_JOBS` 原来**只用 `label` 做键**：

```python
_JOBS[label] = {...}          # 起任务
...
_JOBS.pop(label, None)        # 清理死任务
```

于是：

| 时间 | 发生什么 |
|---|---|
| 11:42 | 起 `sections` 任务 → `_JOBS["default"] = sections` |
| 11:50 | 起 `study` 任务 → **覆盖**成 `_JOBS["default"] = study` |
| 12:03 | `_jobs_snapshot()` 发现 sections 已死 → **`pop("default")`** → **study 被一起弹掉** |

`study` 进程其实还在跑（它是独立进程），只是**面板看不见它**了：
「停止」按钮失效、运行日志不显示、卡片状态不对。

## 修法

键改成 `label|kind`（`_job_key()`）：
- 同一账号的 `study` / `sections` / `work` 各占一条；
- 清理时**按 key 删**，不会连带删掉别的在跑任务；
- `_stop_one` 改成「停该账号**所有**还在跑的任务」（用户点停止的意思就是这个）。

**连带要改的**：`_log_signature()` 与 SSE 监视循环的指纹字典 —— 原来也按 label，
同账号两个任务会互相覆盖 → 推送会漏。
"""

from __future__ import annotations

import inspect
import unittest

from xxt.panel_gxks import app as A


class _FakeProc:
    def __init__(self, alive: bool = True):
        self._alive = alive
        self.pid = 4242

    def poll(self):
        return None if self._alive else 0


class TestJobsKeyedByLabelAndKind(unittest.TestCase):
    def setUp(self):
        A._JOBS.clear()
        A._LAST_JOBS.clear()

    def tearDown(self):
        A._JOBS.clear()
        A._LAST_JOBS.clear()

    def test_job_key_combines(self):
        self.assertEqual(A._job_key("default", "study"), "default|study")
        self.assertNotEqual(A._job_key("default", "study"),
                            A._job_key("default", "sections"))

    def test_two_kinds_coexist(self):
        """同一账号的 study 与 sections 能同时登记。"""
        A._JOBS[A._job_key("default", "sections")] = {
            "proc": _FakeProc(True), "kind": "sections", "label": "default",
            "since": "11:42", "log": "x.log", "detail": ""}
        A._JOBS[A._job_key("default", "study")] = {
            "proc": _FakeProc(True), "kind": "study", "label": "default",
            "since": "11:50", "log": "y.log", "detail": ""}
        self.assertEqual(len(A._JOBS), 2)
        snap = A._jobs_snapshot()
        self.assertEqual(len(snap), 2)
        self.assertEqual({j["kind"] for j in snap}, {"study", "sections"})

    def test_finished_one_does_not_evict_the_other(self):
        """核心断言：一条结束**不能**把同账号另一条在跑的踢掉。

        （原 bug：`_JOBS.pop(label)` 把还在跑的 study 一起弹了。）
        """
        A._JOBS[A._job_key("default", "sections")] = {
            "proc": _FakeProc(False), "kind": "sections", "label": "default",
            "since": "11:42", "log": "x.log", "detail": ""}
        A._JOBS[A._job_key("default", "study")] = {
            "proc": _FakeProc(True), "kind": "study", "label": "default",
            "since": "11:50", "log": "y.log", "detail": ""}

        snap = A._jobs_snapshot()
        self.assertEqual(len(snap), 1, "只剩还在跑的那条")
        self.assertEqual(snap[0]["kind"], "study")
        # 还在跑的那条必须还在 _JOBS 里（没被误删）
        self.assertIn(A._job_key("default", "study"), A._JOBS)

    def test_log_signature_uses_key(self):
        """日志指纹也要按 label|kind（同账号两个任务不能互相覆盖）。"""
        src = inspect.getsource(A._log_signature)
        self.assertNotIn('out[job["label"]]', src,
                         "指纹必须按 key 记，否则同账号两个任务会互相覆盖")
        self.assertIn('out[key]', src)


if __name__ == "__main__":
    unittest.main()
