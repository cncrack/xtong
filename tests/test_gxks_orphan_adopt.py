"""面板启动时**认领孤儿任务**（2026-11-05）。

## 为什么要做这个功能

用户 11-05 遇到一个两难：面板「已完成」区默认折叠的修复**必须重启面板**才生效，
但重启会丢掉 `_JOBS`（内存表）→ 正在跑的刷课任务变成**孤儿**：
它继续跑到自然结束，可面板看不见、也停不掉。

`CREATE_NO_WINDOW` 让子进程脱离了面板进程树（面板重启不杀它，这是对的），
代价就是这个「认领」问题。用户因此选了「等 5 节跑完再重启」——不该让用户
为了一个展示 bug 去等一小时。

## 判活三道闸（少一道就会出事）

1. **pid 活着** —— 否则认领一个死任务；
2. **pid 归属对得上** —— `runmark.pid_owns_file` 比进程创建时间与日志 mtime。
   ⚠ 这条最关键：Windows 会**复用 pid**，只看「活着」会把一个恰好复用旧 pid
   的无关进程认成刷课，更糟的是「停止」时把它 taskkill 掉
   （2026-10-27 盘点 B 踩过同样的坑）；
3. **日志文件还在** —— 没有日志就无法展示，认领了也是空壳。

## 还要保证的

- 认领失败/已死的登记要**顺手清掉**，不能越积越多；
- 本进程刚起过同名任务时**不许被登记表覆盖**（否则新任务被旧登记顶掉）；
- 认领回来的任务必须能被 `_jobs_snapshot` 显示、被 `_stop_one` 停掉
  —— 靠 `_OrphanProc` 提供 `poll()` / `pid` 两个接口，上层一行不用改。
"""

from __future__ import annotations

import json
import os
import unittest

from xxt.panel_gxks import app as A


def _settings(tmp):
    """最小 settings 替身：`adopt_orphan_jobs` 只用到 `data_dir`。"""

    class _S:
        data_dir = tmp

    return _S()


class _Proc:
    def __init__(self, alive=True, pid=4242):
        self._alive = alive
        self.pid = pid

    def poll(self):
        return None if self._alive else 0


class TestAdoptOrphanJobs(unittest.TestCase):
    def setUp(self):
        A._JOBS.clear()
        A._LAST_JOBS.clear()

    def tearDown(self):
        A._JOBS.clear()
        A._LAST_JOBS.clear()

    def _registry(self, tmp, entries):
        p = tmp / "gxks_jobs.json"
        p.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
        return p

    def _entry(self, tmp, label="default", kind="study", pid=None, log=None):
        log = log or (tmp / f"gxks-{kind}-{label}-20261105-000000.log")
        log.write_text("x\n", encoding="utf-8")
        return {"label": label, "kind": kind,
                "pid": os.getpid() if pid is None else pid,
                "log": str(log), "detail": "--max-sections 5",
                "since": "23:00:00"}

    def test_no_registry_file_is_noop(self, ):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            self.assertEqual(A.adopt_orphan_jobs(_settings(tmp)), [])
            self.assertEqual(A._JOBS, {})

    def test_adopts_live_job(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            # 用**当前进程**当"活着的子进程"：它必然活着，且创建时间早于日志 mtime
            entry = self._entry(tmp)
            self._registry(tmp, {"default|study": entry})
            got = A.adopt_orphan_jobs(_settings(tmp))
            self.assertEqual(len(got), 1)
            self.assertEqual(got[0]["pid"], os.getpid())
            self.assertIn("default|study", A._JOBS)
            self.assertTrue(A._JOBS["default|study"].get("adopted"))

    def test_adopted_job_visible_in_snapshot(self):
        """认领回来的任务必须能被 `_jobs_snapshot` 当成**在跑**的显示出来。"""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            self._registry(tmp, {"default|study": self._entry(tmp)})
            A.adopt_orphan_jobs(_settings(tmp))
            snap = A._jobs_snapshot()
            self.assertEqual(len(snap), 1)
            self.assertTrue(snap[0]["alive"])
            self.assertEqual(snap[0]["kind"], "study")

    def test_dead_pid_is_dropped_from_registry(self):
        """死 pid 不许认领，且登记表里要清掉（否则越积越多）。"""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            # 一个几乎不可能存在的 pid
            self._registry(tmp, {"default|study": self._entry(tmp, pid=999999)})
            self.assertEqual(A.adopt_orphan_jobs(_settings(tmp)), [])
            self.assertEqual(A._JOBS, {})
            left = json.loads((tmp / "gxks_jobs.json").read_text(encoding="utf-8"))
            self.assertEqual(left, {})

    def test_missing_log_is_dropped(self):
        """日志没了就不认领 —— 认领了也是空壳（看不见任何输出）。"""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            entry = self._entry(tmp)
            Path(entry["log"]).unlink()
            self._registry(tmp, {"default|study": entry})
            self.assertEqual(A.adopt_orphan_jobs(_settings(tmp)), [])

    def test_existing_job_not_overwritten(self):
        """本进程刚起过同名任务时，登记表不许把它顶掉。"""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            self._registry(tmp, {"default|study": self._entry(tmp)})
            mine = _Proc(True, pid=111)
            A._JOBS["default|study"] = {
                "proc": mine, "kind": "study", "label": "default",
                "since": "23:59", "log": "mine.log", "detail": ""}
            A.adopt_orphan_jobs(_settings(tmp))
            self.assertIs(A._JOBS["default|study"]["proc"], mine,
                          "新任务不能被登记表里的旧条目覆盖")

    def test_pid_reuse_is_rejected(self):
        """pid 归属校验：进程创建时间晚于日志 mtime = pid 被复用 → 不许认领。

        这里直接打桩 `runmark.pid_owns_file` 返回 False 模拟该情形。
        """
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            self._registry(tmp, {"default|study": self._entry(tmp)})
            orig = A.runmark.pid_owns_file
            A.runmark.pid_owns_file = lambda path, pid: False
            try:
                self.assertEqual(A.adopt_orphan_jobs(_settings(tmp)), [])
            finally:
                A.runmark.pid_owns_file = orig


class TestOrphanProc(unittest.TestCase):
    def test_poll_reflects_liveness(self):
        p = A._OrphanProc(os.getpid())
        self.assertIsNone(p.poll())
        self.assertEqual(p.pid, os.getpid())

    def test_poll_goes_dead_when_pid_vanishes(self):
        p = A._OrphanProc(999999)
        self.assertEqual(p.poll(), 0)

    def test_wait_returns_immediately(self):
        """`_kill` 里会调 `proc.wait(timeout=15)`；孤儿没有 Popen，必须立即返回。"""
        self.assertEqual(A._OrphanProc(os.getpid()).wait(timeout=15), 0)


class TestRegistryCleanup(unittest.TestCase):
    def test_drop_removes_only_that_kind(self):
        """停掉 study 不能把同账号的 sections 登记一起删掉。"""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            p = tmp / "gxks_jobs.json"
            p.write_text(json.dumps({
                "default|study": {"pid": 1},
                "default|sections": {"pid": 2},
            }), encoding="utf-8")
            A._drop_job_registry(tmp, "default", "study")
            left = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(set(left), {"default|sections"})

    def test_drop_missing_file_is_safe(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as d:
            A._drop_job_registry(Path(d), "default", "study")   # 不抛异常即通过

    def test_write_registers_and_preserves_others(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            s = _settings(tmp)
            log = tmp / "a.log"
            log.write_text("x", encoding="utf-8")
            A._write_job_registry(s, "default", "study", 111, log, "--max-sections 5")
            A._write_job_registry(s, "default", "sections", 222, log, "")
            data = json.loads((tmp / "gxks_jobs.json").read_text(encoding="utf-8"))
            self.assertEqual(set(data), {"default|study", "default|sections"})
            self.assertEqual(data["default|study"]["pid"], 111)


if __name__ == "__main__":
    unittest.main()
