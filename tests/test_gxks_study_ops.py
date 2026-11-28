"""看视频也要写操作记录（2026-11-28 用户要求）。

## 背景

用户：「为何操作记录里面第二个账号的日志比较少，基本都是第一个账号的」。

查证：`work-fill` / `work-review` / `sections-read` / `browser-start` 都写
`gx_ops`，但**学视频全程一个记录点都没有**。所以 test（只看视频的账号）
在操作记录里只有一条 `browser-start`，看着像什么都没干。

修：`run_gxks.py` 补三个节点 —— 开始学习 / 每节学完 / 收工汇总。
"""

from __future__ import annotations

from pathlib import Path

RUN = (Path(__file__).resolve().parents[1] / "scripts" / "run_gxks.py").read_text(
    encoding="utf-8")


def test_has_log_op_helper():
    assert "def _log_op(" in RUN


def test_logs_study_start():
    assert '"study-start"' in RUN


def test_logs_each_section():
    """每节学完要有一条，用户才能看出「这账号学了哪几节」。"""
    assert '"study-section"' in RUN


def test_logs_study_done():
    assert '"study-done"' in RUN


def test_logging_failure_never_breaks_study():
    """写记录是附加收益，失败必须吞掉，绝不能影响刷课。"""
    seg = RUN.split("def _log_op(", 1)[1][:800]
    assert "except Exception" in seg
    assert "pass" in seg


def test_does_not_touch_player_behavior():
    """只加记录，不许碰播放/上报逻辑（G 系列铁律）。"""
    seg = RUN.split("def _log_op(", 1)[1][:800]
    for bad in ("fast_forward", "currentTime", "updateTime", "submit("):
        assert bad not in seg, f"_log_op 里不该出现 {bad}"
