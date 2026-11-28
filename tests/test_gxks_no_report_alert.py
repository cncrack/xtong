"""`no_report` 告警的判定与文案（2026-11-28 用户实拍误报）。

用户看到日志里一堆：
  [test·32] [14:45:27] no_report 已 90s 无上报（updates=8）→ 查网络，学时没在入账
问「是真的没有计学时入账吗」。

## 实测结论（test 账号 14:27~14:46 的真实日志）

  14:27:47  progress 0/36 learning
  14:33:01  progress 4/36 learning
  14:38:14  progress 10/36 learning
  14:43:27  progress 14/36 learning      <- 已完成节数在涨

上报计数（updates）的真实间隔是 **1分43秒 ~ 2分13秒**，而阈值写的是 75 秒
（注释还写着"上报是 60 秒一拍"）-> 每 75 秒必然假报一次。

所以两处都要改：
  ① 阈值 75 -> 300 秒（贴近实际，5 分钟没动才提示）
  ② 日志**不许断言**「学时没在入账」，改陈述事实 + 带上 `done_minutes` 对比
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DRIVER = (ROOT / "src" / "xxt" / "adapters" / "gxks" / "driver.py").read_text(
    encoding="utf-8")
WATCH = (ROOT / "scripts" / "watch_gxks.py").read_text(encoding="utf-8")


def test_threshold_is_adaptive_not_fixed():
    """阈值必须**自适应**，不能写死。

    ## 为什么（两次实测结论不同）

    2026-10-29 定 75 秒时依据是「clock=2166s 时 updates=36」⇒ 上报约 60 秒一次。
    历史日志证实：**11-08 下午平均 69.8 秒、当晚平均 61.0 秒** —— 75 秒合适。

    但 2026-11-28 同账号同平台实测平均 **119.3 秒**（103~133 秒）。
    写死 75 秒就会一直假报；写死 300 秒又会让 60 秒节奏的场景迟钝 5 倍。

    所以改成按观察到的间隔自适应（`report_stale_threshold`）。
    """
    # 只看代码行（注释里提到旧常量名是解释历史，允许）
    code = chr(10).join(
        ln for ln in DRIVER.splitlines() if not ln.strip().startswith("#"))
    assert "NO_REPORT_AFTER_SECONDS" not in code, (
        "代码里不该再有写死的 NO_REPORT_AFTER_SECONDS")
    assert "def report_stale_threshold(" in DRIVER


def test_adaptive_threshold_with_real_intervals():
    """用三份真实日志的间隔验证阈值。"""
    import sys
    sys.path.insert(0, str(ROOT / "src"))
    from xxt.adapters.gxks.driver import report_stale_threshold

    # 无样本 -> 保守回落到 10-29 的 75 秒
    assert report_stale_threshold([]) == 75

    # 11-08 节奏（60 秒上下）-> 比 75 宽但仍及时
    t_old = report_stale_threshold([60, 60, 60, 60, 65])
    assert 75 <= t_old <= 200, f"11-08 节奏下阈值 {t_old} 不合理"

    # 11-28 节奏（120 秒上下，最大 133）-> 必须 > 133，否则仍误报
    t_new = report_stale_threshold([120, 133, 120, 120, 103])
    assert t_new > 133, f"今天最大间隔 133s，阈值 {t_new} 仍会误报"
    assert t_new <= 600, "阈值不该超过 10 分钟（真断网要能发现）"


def test_evidence_helper_exists():
    """要有 `_no_report_evidence`，用 done_minutes 有没有涨来判断。"""
    assert "def _no_report_evidence(" in DRIVER


def test_evidence_three_states():
    """三种情况：涨了 / 没涨 / 无对比。"""
    import sys
    sys.path.insert(0, str(ROOT / "src"))
    from xxt.adapters.gxks.driver import _no_report_evidence

    cur = {"done_minutes": 10, "required_minutes": 45}
    old = {"done_minutes": 4, "required_minutes": 45}
    assert _no_report_evidence(cur, old)["done_changed"] is True

    same = {"done_minutes": 4, "required_minutes": 45}
    assert _no_report_evidence(same, old)["done_changed"] is False

    assert _no_report_evidence(cur, None)["done_changed"] is None


def test_no_report_event_carries_evidence():
    """事件里要带 done_minutes/required_minutes/done_changed。

    `ev = _no_report_evidence(...)` 在 emit **之前**算好再 `**ev` 展开，
    所以要看 emit 之前的 300 字符，不是之后。
    """
    head, tail = DRIVER.split('emit("no_report"', 1)
    assert "_no_report_evidence" in head[-400:], "emit 之前应先算好证据"
    assert "**ev" in tail[:200], "emit 要把证据展开进去"


def test_log_text_does_not_claim_no_credit():
    """实际输出的日志**不许**再写死「学时没在入账」——那是错误结论。

    ⚠ 注释里可以提到这句话（解释为什么改），但真正的 `extra = ...` 文案不行。
    """
    # 只看赋值给 extra 的那些行（排除注释）
    lines = [ln for ln in WATCH.splitlines()
             if not ln.strip().startswith("#")]
    code = chr(10).join(lines)
    assert "学时没在入账" not in code, (
        "实际日志文案里还写着「学时没在入账」：实测上报慢但学时在涨，"
        "这句话会误导用户以为白跑了")


def test_log_text_reports_evidence():
    """要按证据说话：涨了=正常 / 没涨=真查网络 / 无数据=说明白。"""
    assert "学时在涨" in WATCH
    assert "学时没涨" in WATCH
    assert "无对比数据" in WATCH


def test_bootstrap_does_not_false_alarm():
    """启动阶段（还没有任何上报样本）不能在第 75 秒就喊断网。

    实测 11-28 首次上报要 **120 秒**；若启动阶段硬套 75 秒阈值，
    第一个 no_report 必然误报（日志里 14:29:18 那条就是）。

    修：无样本时阈值按「已等时长 × 1.5」渐进放宽（封顶 240 秒）。
    """
    import sys
    sys.path.insert(0, str(ROOT / "src"))
    from xxt.adapters.gxks.driver import report_stale_threshold

    # 已等 75 秒时，阈值要 > 75（否则此刻就报）
    th75 = report_stale_threshold([], started_at=0, now=75)
    assert th75 > 75, f"已等 75s 阈值仍 {th75}，会误报"

    # 已等 120 秒时（首次上报的典型时刻），阈值要 > 120
    th120 = report_stale_threshold([], started_at=0, now=120)
    assert th120 > 120, f"已等 120s 阈值仍 {th120}，首次上报前就误报"

    # 但也不能无限放宽：真的完全没上报，最终必须报出来
    th999 = report_stale_threshold([], started_at=0, now=9999)
    assert th999 <= 240, f"启动阶段阈值封顶应 <=240，实际 {th999}"


def test_bootstrap_then_switch_to_interval_rule():
    """一旦有了真实间隔，就切回「最近样本最大 × 2.5」。"""
    import sys
    sys.path.insert(0, str(ROOT / "src"))
    from xxt.adapters.gxks.driver import report_stale_threshold

    # 有样本时 started_at/now 应被忽略
    with_iv = report_stale_threshold([60, 60, 60], started_at=0, now=9999)
    assert with_iv == 150, f"60s 节奏应得 150s，实际 {with_iv}"
