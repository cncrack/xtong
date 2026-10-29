# -*- coding: utf-8 -*-
"""失败归类：把子进程的停止原因翻成「用户看得懂 + 能处置」的类别与档位。

背景（2026-10-27 用户提出 Q20/Q19）：
- Q20：以前打不开只报一句「连续 N 节打不开」就收工，不给原因、不给入口，
  人只能去翻日志；
- Q19：额度/任务数是最高优先级，**不能被"重试次数"一碰就判今天收工**——
  打不开、网络抖这类可恢复原因应记为「挂起」，退避后自动恢复；
  真正该停的（达成、时段外、需人工登录、硬上限用尽）才「硬停」。

四档语义（面板卡片警示条按此显示）：
- `achieved`   达成：今日额度/任务数已达标，或本地已无待学章节 → 不用再跑
- `suspended`  挂起：可自动恢复（打不开/网络/风控/未归类）→ 退避后自动重试
- `needs_you`  挂起（等你操作）：登录失效 → 点「重新登录」，不自动耗次数
- `hard_stop`  硬停：时段外或硬上限用尽 → 今日不再自动，面板给「立即补跑」

设计边界：
- 只做**文本归类**，不发起任何平台访问（零额外请求，风控第一）；
- 关键词以通用描述为主，但 `structure` 条下仍含少量超星 UI 用词（“知识点/章节树”）；
  彻底的做法是**由适配层在写失败原因时打稳定标签**（如 `[structure] 连续 3 节打不开`），
  `classify()` 先读标签、再退到关键词——已记入 STATUS 台账 Q27，本轮不动（避免半改）；
- 归类不命中返回 `unknown`——宁可说不出，也不乱猜；unknown 按"可挂起"处理
  （退避后再试一次），因为误判"今天没戏"的代价比多试一次大得多。
"""

from __future__ import annotations

# code -> (中文名, 一句话解释)
CATEGORIES: dict[str, tuple[str, str]] = {
    "quota": ("今日目标达成", "额度或任务数已达标（含本会话内达成），今天不用再跑"),
    "done": ("今日无待学", "本地已没有待学章节，可等明天或补抓章节后再跑"),
    "login": ("登录失效", "会话文件过期或被平台踢下线，需要重新登录这个账号"),
    # 2026-10-30（轻档）：「这门课从未抓过章节树」与「本地真学完了」以前共用
    # 一句“无待学章节”，用户看不到区别，因而不知道该点哪个按钮。拆成独立类型。
    "notree": ("未抓章节", "这门课本地还没有章节树：在该课程行点「⬇ 抓章节」"
                          "（进课程页 1 次，不消耗当日额度），抓完再刷"),
    "risk": ("疑似风控", "出现验证码或异常访问提示，建议先停一段时间再试"),
    "structure": ("页面打不开", "任务页/下一节按钮异常，多为页面结构变化或 iframe 失效"),
    # 2026-10-28（Q28）：`video-interrupt` 是 B3 自愈事件（播放中断→重载任务页续播），
    # 与「页面打不开」不是一回事。以前面板把它硬归 structure，卡片就写着
    # 「⚠ 页面打不开·已停手」而课其实在播——用户截图问的第 1 个账号报错就是它。
    "interrupt": ("播放中断", "本节视频播放被打断（会话活着会自动重载续播）"),
    "network": ("网络异常", "连接超时或被重置，多为网络/代理波动"),
    "crash": ("会话异常中止", "浏览器或事件管道自保退出（不是平台拒访），重开会话即可恢复"),
    "window": ("学习时段外", "已超出每日学习窗口，明天窗口开启后再跑"),
    "unknown": ("未归类", "原因不在已知类型里，请点「看日志」看原文"),
}

# 档位：达成 / 挂起 / 挂起（等你操作） / 硬停
TIERS: dict[str, str] = {
    "achieved": "达成",
    "suspended": "挂起",
    "needs_you": "挂起·等你操作",
    "hard_stop": "硬停",
}

# 可自动恢复的类别：失败不判"今日收工"，退避后自动重试
_SUSPENDABLE = frozenset({"structure", "network", "risk", "crash", "unknown",
                         "interrupt"})

# 关键词表：按顺序匹配，先命中先返回（顺序即优先级）
# 注意顺序：login 必须早于 structure（"打不开章节树…请重新登录"两句同时出现）；
#          structure 必须早于 done（"连续 3 节打不开；本地已无待学章节"应判打不开）；
#          notree 必须早于 structure（它的文案含“章节树”，否则被误判成“页面打不开”）。
_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("risk", ("验证码", "风控", "captcha", "异常访问", "访问过于频繁", "操作频繁")),
    ("login", ("重新登录", "登录态", "未登录", "登录已失效", "登录失效", "state.json",
               "验证过期", "被踢")),
    ("notree", ("未抓章节", "章节未抓", "未抓过章节")),
    ("window", ("学习时段", "学习窗口", "超出每日", "窗口外")),
    ("quota", ("额度达标", "任务数达标", "额度已满", "额度已用尽", "已达标")),
    ("structure", ("打不开", "进不去", "任务页", "frame was detached", "frame detached",
                   "下一节", "找不到", "选择器", "知识点", "章节树")),
    ("network", ("err_", "timeout", "timed out", "超时", "connection", "连接被重置",
                 "net::", "proxy", "代理", "网络")),
    # crash 只收“本地会话自身挂了”这类专用词；不用「异常中止/异常终止」这种通用词，
    # 否则会把更具体的网络/页面原因盖掉（17:17 #2 就是「异常终止 + net::」同句）。
    ("crash", ("事件管道", "管道失效", "浏览器已关闭", "target closed", "page crashed",
               "browser has been closed", "盲切快进")),
    # 2026-10-30（轻档）补"已全部完成"：文案按成因分流后，“真学完”那句不再带
    # “无待学”字样；不收它就会落到 unknown → 守护按"可恢复"退避重试，白烧一次会话。
    ("done", ("无待学", "没有待学", "待学章节为空", "已全部完成")),
]


def classify(text: str) -> str:
    """停止原因文本 → 类别 code（永不抛异常）。"""
    low = (text or "").lower()
    if not low.strip():
        return "unknown"
    for code, words in _RULES:
        for w in words:
            if w in low:
                return code
    return "unknown"


def describe(code: str) -> tuple[str, str]:
    """类别 code → (中文名, 解释)；未知 code 退回 unknown。"""
    return CATEGORIES.get(code, CATEGORIES["unknown"])


def is_suspendable(code: str) -> bool:
    """该类别失败是否应"挂起后自动恢复"（而不是判今日收工）。"""
    return code in _SUSPENDABLE


def tier_for(code: str) -> str:
    """类别 code → 档位（achieved / suspended / needs_you / hard_stop）。"""
    if code in ("quota", "done"):
        return "achieved"
    if code in ("login", "notree"):
        # 两者都要人先做一个动作才能继续（登录 / 点抓章节），自动重试没意义
        return "needs_you"
    if is_suspendable(code):
        return "suspended"
    return "hard_stop"


def actions_for(code: str) -> list[str]:
    """该类别下**真的能落地**的处置动作（面板按此渲染按钮）。

    刻意只给已有接口支撑的动作（看日志 / 重新登录 / 立即补跑），
    不做「暂停 2 小时」「跳过本章」这类尚无实现的按钮——点了没反应比没有更糟。
    风控类不给「立即补跑」：风控期内补跑等于送人头（P0 风控 > P1 额度）。
    """
    if code == "login":
        return ["重新登录", "看日志"]
    if code in ("structure", "network", "unknown", "crash", "interrupt"):
        return ["立即补跑", "看日志"]
    if code == "notree":
        # 只给已有支撑的动作：抓章节的入口在课程行上，警示条里没有这个按钮，
        # 不能写一个点了没反应的动作（与文件头的设计边界一致）
        return ["看日志"]
    return ["看日志"]
