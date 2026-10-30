"""gxks 播放驱动的**纯判定**回归测试（T3-b，2026-10-29）。

只测 `driver.py` 上半部分的纯函数：浏览器调用那层不做单测（要真页面，属集成测试）。
每条用例都对应一次实测踩到的情形，注释里写明出处，避免以后被“优化”掉。
"""

from __future__ import annotations

import pytest

from xxt.adapters.gxks import driver

SECTION = {"platform_chapter_id": "1289", "idx": 0, "required_minutes": 45,
           "done_minutes": 46, "state": "done", "name": "1.1 甲课程"}
LEARNING = {"platform_chapter_id": "1290", "idx": 1, "required_minutes": 42,
            "done_minutes": 21, "state": "learning", "name": "1.2 乙课程"}
PENDING = {"platform_chapter_id": "1291", "idx": 2, "required_minutes": 45,
           "done_minutes": 45, "state": "learning", "name": "1.3 丙课程"}
FRESH = {"platform_chapter_id": "1292", "idx": 3, "required_minutes": 45,
         "done_minutes": 0, "state": "not_started", "name": "1.4 丁课程"}
NO_ID = {"platform_chapter_id": None, "idx": 4, "required_minutes": 20,
         "done_minutes": 0, "state": "not_started", "name": "1.5 无名"}


# ---------------------------------------------------------------- classify


def test_classify_normal_playing():
    prev = {"t": 100, "d": 2871, "paused": False, "hidden": False}
    cur = {"t": 130, "d": 2871, "paused": False, "hidden": False}
    assert driver.classify(cur, prev, elapsed=30) == "ok"


def test_classify_rebuild_prefers_over_paused():
    """Q13/C7：重建时常常同时 paused=True、d=None，必须先判重建（该重载），不能去 play。"""
    prev = {"t": 1552, "d": 2871, "paused": False, "hidden": False}
    cur = {"t": 0, "d": None, "paused": True, "hidden": False}
    assert driver.classify(cur, prev, elapsed=30) == "rebuild"


def test_classify_rebuild_by_backstep_even_if_duration_survives():
    """第二次实测重建时 d 还在，但 t 归零 → 靠回退量识别。"""
    prev = {"t": 1239, "d": 2871, "paused": False, "hidden": False}
    cur = {"t": 1, "d": 2871, "paused": False, "hidden": False}
    assert driver.classify(cur, prev, elapsed=30) == "rebuild"


def test_classify_small_backstep_is_not_rebuild():
    prev = {"t": 100, "d": 2871}
    assert driver.classify({"t": 97, "d": 2871}, prev, elapsed=30) == "ok"


def test_classify_loading_when_never_ready():
    """刚进节时 d=None：这是“还没就绪”，不是重建——否则会无休止重载。"""
    assert driver.classify({"t": 0, "d": None, "paused": True}, None) == "loading"


def test_classify_hidden_ended_paused():
    assert driver.classify({"t": 100, "d": 2871, "hidden": True}) == "hidden"
    assert driver.classify({"t": 100, "d": 2871, "ended": True}) == "ended"
    assert driver.classify({"t": 100, "d": 2871, "paused": True}) == "paused"


def test_classify_stalled_when_progress_too_slow():
    """实测卡顿时：30 秒只前进 15 秒（用户听到音频断续），此时不该当成正常。"""
    prev = {"t": 1337, "d": 2871}
    assert driver.classify({"t": 1352, "d": 2871}, prev, elapsed=30) == "stalled"


def test_classify_mild_lag_is_still_ok():
    """30 秒前进 20 秒属于网络抖动，不报卡顿，避免巡检刷屏。"""
    prev = {"t": 1337, "d": 2871}
    assert driver.classify({"t": 1357, "d": 2871}, prev, elapsed=30) == "ok"


def test_classify_empty_sample_is_unknown():
    assert driver.classify({}) == "unknown"
    assert driver.classify(None) == "unknown"


# ---------------------------------------------------------------- plan_action


def test_plan_action_mapping():
    assert driver.plan_action("ok") == "none"
    assert driver.plan_action("loading") == "wait"
    assert driver.plan_action("paused") == "play"
    assert driver.plan_action("hidden") == "foreground"
    assert driver.plan_action("stalled") == "wait"
    assert driver.plan_action("ended") == "escalate"     # 站点在 ended 只停表，不自动重播
    assert driver.plan_action("rebuild", reloads=0, seconds_since_reload=999) == "reload"


def test_plan_action_reload_cooldown_and_cap():
    assert driver.plan_action("rebuild", reloads=1, seconds_since_reload=10) == "wait"
    assert driver.plan_action(
        "rebuild", reloads=driver.MAX_RECOVERIES_PER_SECTION, seconds_since_reload=999
    ) == "escalate"


# ---------------------------------------------------------------- 达标口径


def test_quota_gap():
    assert driver.quota_gap(SECTION) == 0
    assert driver.quota_gap(LEARNING) == 21
    assert driver.quota_gap({"done_minutes": None, "required_minutes": 45}) is None
    assert driver.quota_gap({}) is None


def test_section_status_pending_is_not_done():
    """实测出现过 42/42 仍「学习中」：分钟够了但状态没变，只能算 pending，不能算完成。"""
    assert driver.section_status(SECTION) == "done"
    assert driver.section_status(PENDING) == "pending"
    assert driver.section_status(LEARNING) == "in_progress"
    assert driver.section_status(FRESH) == "not_started"
    assert driver.section_status({"state": "unknown"}) == "unknown"


def test_enough_with_margin_requires_overrun():
    """2026-10-29 用户要求：“已完成学时要比学时要求多一点，不能是刚刚好”。

    停在刚刚好（42/42）时，最后一分钟的 updateTime 一旦碰上 503/断网就不会入账，
    列表会卡在 41/42，表现为“视频播完了却还显示学习中”。
    """
    exact = {"done_minutes": 42, "required_minutes": 42}
    assert driver.enough_with_margin(exact) is False          # 默认余量 2
    assert driver.enough_with_margin(exact, 0) is True        # 显式要“刚刚好”才退化为等号
    assert driver.enough_with_margin({"done_minutes": 43, "required_minutes": 42}) is False
    assert driver.enough_with_margin({"done_minutes": 44, "required_minutes": 42}) is True
    assert driver.enough_with_margin({"done_minutes": 48, "required_minutes": 42}) is True
    assert driver.enough_with_margin({"done_minutes": None, "required_minutes": 42}) is False
    assert driver.enough_with_margin({}) is False


# ---------------------------------------------------------------- 会话失效（Q20）


def test_at_login_page_recognizes_all_login_pages():
    """2026-10-29 12:49 实测：掉登录时整页落到 **Loginb.aspx**（不是 Login.aspx）。

    2026-10-30 实测补充：未登录访问学习入口、以及 `Login.aspx` 自己，**都 302 到
    `Default.aspx`**（200 / 3717 字节，就是登录页本体）→ 少于三个都认，
    “停在 Default.aspx 的未登录态” 会被当成已登录（P2 的「检查会话」首跑就撞上）。
    """
    assert driver.at_login_page("http://www.gaoxiaokaoshi.com/Login.aspx") is True
    assert driver.at_login_page("http://www.gaoxiaokaoshi.com/Loginb.aspx") is True
    assert driver.at_login_page("http://www.gaoxiaokaoshi.com/loginb.aspx?x=1") is True
    assert driver.at_login_page("http://www.gaoxiaokaoshi.com/Default.aspx") is True
    # 未登录被踢时的真实形态：带 ReturnUrl 且保留原 query
    assert driver.at_login_page("http://www.gaoxiaokaoshi.com/Default.aspx"
                                "?ReturnUrl=%2fHomew%2fMainPage.aspx%3fmenu%3d3&menu=3") is True
    assert driver.at_login_page("http://www.gaoxiaokaoshi.com/default.aspx") is True   # 大小写
    assert driver.at_login_page("http://www.gaoxiaokaoshi.com/Study/LibraryStudy.aspx?Id=1") is False
    assert driver.at_login_page("http://www.gaoxiaokaoshi.com/HidLogin.aspx") is False   # 子串会误伤
    # 登录后的学习入口本身**不是**登录页（只做前缀匹配就会把它一起误杀）
    assert driver.at_login_page("http://www.gaoxiaokaoshi.com/Homew/MainPage.aspx"
                                "?menu=3&subMenu=4") is False
    assert driver.at_login_page("") is False


def test_shorten_url_strips_rnd():
    url = "http://x/List.aspx?Id=1&rnd=123456"
    assert "rnd" not in driver.shorten_url(url)
    assert driver.shorten_url(url).endswith("Id=1")
    assert driver.shorten_url("http://x/List.aspx?rnd=9").endswith("List.aspx")


# ---------------------------------------------------------------- 视频播完的边界


def test_meets_required_ignores_margin():
    """视频播完时只能按「要求」判，不能按「要求+余量」。

    2026-10-29 1.3 实测：视频 47 分 53 秒，播完列表 46（要求 45，余量线 47）——
    够不着余量是正常边界（没东西可再播），必须按达标收工，否则每节都报 escalate。
    """
    assert driver.meets_required({"done_minutes": 46, "required_minutes": 45}) is True
    assert driver.meets_required({"done_minutes": 45, "required_minutes": 45}) is True
    assert driver.meets_required({"done_minutes": 44, "required_minutes": 45}) is False
    assert driver.meets_required({"done_minutes": None, "required_minutes": 45}) is False
    assert driver.meets_required({}) is False

    # 与余量判定是两条线：46 过“要求”，但过不了“要求+2”
    row = {"done_minutes": 46, "required_minutes": 45}
    assert driver.meets_required(row) is True
    assert driver.enough_with_margin(row, 2) is False


def test_should_replay_only_when_short_and_budget_left():
    """视频播完却连“要求”都没到 → 补播一次（Q26）。

    2026-10-29 1.7 实测：要求 20 分钟的视频播到 `ended`（m_time 1295），列表只 19/20——
    最后一分钟的 `updateTime` 没发出去。重播是增量记账（Q12），补上那一分钟即可。
    但**缺数据不补**：判不了就不动，免得在异常页上瞎转。
    """
    short = {"done_minutes": 19, "required_minutes": 20}
    assert driver.should_replay(short, 0) is True
    assert driver.should_replay(short, 1) is False             # 默认只补一次
    assert driver.should_replay(short, 0, max_replays=0) is False   # 显式关掉

    assert driver.should_replay({"done_minutes": 20, "required_minutes": 20}, 0) is False
    assert driver.should_replay({"done_minutes": 21, "required_minutes": 20}, 0) is False
    assert driver.should_replay({"done_minutes": None, "required_minutes": 20}, 0) is False
    assert driver.should_replay({}, 0) is False
    assert driver.should_replay(None, 0) is False


# ---------------------------------------------------------------- 上报状态（Q20 取证）


def test_report_summary_counts_by_status():
    """会话失效时靠“上报返回什么状态”分辨要不要人工介入：200 = 服务端照收，302 = 会话已死。"""
    reports = [{"at": 60, "status": 200}, {"at": 120, "status": 200}, {"at": 180, "status": 302}]
    s = driver.report_summary(reports)
    assert s["count"] == 3
    assert s["by_status"] == {"200": 2, "302": 1}
    assert s["last"] == [200, 200, 302]
    assert driver.report_summary([]) == {"count": 0, "by_status": {}, "last": []}


# ---------------------------------------------------------------- 切节


def test_pick_next_section_skips_done_and_starts_after_current():
    rows = [SECTION, LEARNING, PENDING, FRESH]
    assert driver.pick_next_section(rows, "1289")["platform_chapter_id"] == "1290"
    assert driver.pick_next_section(rows, None)["platform_chapter_id"] == "1290"


def test_pick_next_section_returns_none_when_all_done():
    assert driver.pick_next_section([SECTION], "1289") is None


def test_pick_next_section_skips_row_without_id():
    """取不到节 ID 的行点不开，跳过而不是拿名字去猜（名称会变）。"""
    assert driver.pick_next_section([SECTION, NO_ID], "1289") is None


# ---------------------------------------------------------------- 防缓存


def test_cache_busted_replaces_rnd():
    url = "http://example/List.aspx?Id=1&rnd=100"
    out = driver.GxksDriver.cache_busted(url)
    assert "rnd=100" not in out and "rnd=" in out and out.startswith("http://example/List.aspx?Id=1&rnd=")


def test_cache_busted_appends_when_absent():
    out = driver.GxksDriver.cache_busted("http://example/List.aspx?Id=1")
    assert "?Id=1&rnd=" in out


@pytest.mark.parametrize("name", ["watch", "read_row", "read_page", "refresh_here", "current_pager",
                                  "next_page", "prev_page", "goto_page", "find_section",
                                  "open_section", "reload_player",
                                  "sample_player", "mute", "pause", "leave_player"])
def test_driver_exposes_thin_io_methods(name):
    assert callable(getattr(driver.GxksDriver, name, None))
