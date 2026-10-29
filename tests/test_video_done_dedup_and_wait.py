"""播放等待上限（B）与 video-done 去重（C）——2026-10-29 由账号#1 实测日志倒推。

背景（两类中断，只有一类是真 bug）：

* **长视频被硬顶砍断**：`max_wait` 写死 600 秒，凡「时长 ÷ 倍速 > 600 秒」的一节
  必然在 79%~96% 报一次"播放中断"（实测 644/816s、710/741s、716/796s、632/818s），
  白花一次"中断 → 重载"。修法是把上限随剩余时长弹性延长（`next_wait_limit`）。
* **watcher 注入失败谎报 100%**：注入失败提前 return 时进度快照还是上一节的值，
  中断日志因而显示 `346/346s（100%）`；100% ≥ 续播门槛 → 整节重播 → 同一节写出两条
  `video-done`（21:33:15 与 21:39:16）。额度的两个口径都直接数 video-done 行，
  重复写一条就是白扣一份额度。修法：快照先归零（A）+ 记账前判重（C）。
"""

from __future__ import annotations

import time

import pytest

from xxt.core.playback import (WAIT_CEIL_SECONDS, WAIT_INIT_SECONDS,
                               WAIT_SLACK_SECONDS, next_wait_limit)
from xxt.core.quota import video_done_counted
from xxt.store.db import connect

COURSE = "示例课程"


# ---------- B：弹性等待上限 ----------

def test_long_video_no_longer_cut_at_600s():
    """816s 的视频播到 600 秒时，上限必须已经延长到足以等完剩余内容。"""
    limit = next_wait_limit(elapsed=600.0, remaining_content=172.0, rate=1.08)
    assert limit > 600, "旧实现就是在这里把长视频砍断的"
    assert limit == pytest.approx(600 + 172 / 1.08 + WAIT_SLACK_SECONDS)


def test_limit_is_capped_to_prevent_hanging():
    """异常卡住时不能无限等：绝对封顶 WAIT_CEIL_SECONDS。"""
    limit = next_wait_limit(elapsed=1700.0, remaining_content=5000.0, rate=1.0)
    assert limit == WAIT_CEIL_SECONDS


def test_short_video_settles_below_init():
    """短视频按"剩余+余量"给时间，比起步上限更早收兵（保留原有的早退精神）。"""
    limit = next_wait_limit(elapsed=30.0, remaining_content=100.0, rate=1.0)
    assert limit == 30.0 + 100.0 + WAIT_SLACK_SECONDS < WAIT_INIT_SECONDS


def test_unknown_rate_falls_back_to_1x():
    """倍速缺失（<=0）时按 1.0 算：宁可多等，也不因除零/误算把正常播放砍掉。"""
    assert next_wait_limit(elapsed=0.0, remaining_content=100.0, rate=0) == 100.0 + WAIT_SLACK_SECONDS


# ---------- C：同一节今天不重复计额度 ----------

@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "x.db")
    yield c
    c.close()


def _log_done(c, title, secs=346, day=None, account=1, event="video-done"):
    c.execute(
        "INSERT INTO run_logs(ts, level, event, detail, account_id) VALUES(?,?,?,?,?)",
        (f"{day or time.strftime('%Y-%m-%d')}T21:33:15", "INFO", event,
         f"{COURSE}｜{title}｜{secs}s", account))
    c.commit()


def test_counted_after_first_record(conn):
    _log_done(conn, "创业政策保驾双创实践")
    assert video_done_counted(conn, account_id=1, course_name=COURSE,
                              title="创业政策保驾双创实践") is True


def test_not_counted_for_other_chapter_or_account_or_day(conn):
    _log_done(conn, "创业政策保驾双创实践")
    # 别的节
    assert video_done_counted(conn, account_id=1, course_name=COURSE,
                              title="创业精神激励自我成长") is False
    # 别的账号（额度按账号×天算，不能串）
    assert video_done_counted(conn, account_id=2, course_name=COURSE,
                              title="创业政策保驾双创实践") is False
    # 昨天记的（今天重播该记）
    assert video_done_counted(conn, account_id=1, course_name=COURSE,
                              title="创业政策保驾双创实践", day="2000-01-01") is False


def test_other_events_do_not_block_accounting(conn):
    """只有 video-done 算"已记账"：chapter-start 同样带节名，不能误挡。"""
    _log_done(conn, "创业政策保驾双创实践", event="chapter-start")
    assert video_done_counted(conn, account_id=1, course_name=COURSE,
                              title="创业政策保驾双创实践") is False


def test_like_metachars_in_title_are_matched_exactly(conn):
    """节名里的 % / _ 是 LIKE 元字符；判重用 instr，必须仍能精确命中。"""
    title = "50%_冲刺（上）"
    _log_done(conn, title)
    assert video_done_counted(conn, account_id=1, course_name=COURSE, title=title) is True
    # 且不会把另一节误判成已记
    assert video_done_counted(conn, account_id=1, course_name=COURSE,
                              title="50x9冲刺（上）") is False


def test_broken_db_counts_as_not_recorded(conn):
    """查库异常时按"没记过"处理：宁可多记一次，也别把真播完的一节丢掉不记账。"""
    conn.execute("DROP TABLE run_logs")
    conn.commit()
    assert video_done_counted(conn, account_id=1, course_name=COURSE, title="任意一节") is False
