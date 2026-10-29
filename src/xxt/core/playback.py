"""播放完成的可信判定（纯函数，便于测试）。

2026-10-25 事故：账号#2 点"下一节"后 **0 秒** 就报 `495/495s` 并写了一条
`正确认识自我（下）｜494s` 的 video-done——那 494s 其实是**上一节的时长**。
链条：watcher 每 3s 发 tick，点"下一节"后旧文档还在发，拟人间隔期间残留事件堆在
队列里；新一轮 `_play_video` 一 drain 就把"上一节已播完"当成了新视频的状态。

本模块只回答一个问题：**这次"完成"值不值得记账**。
调用方据此决定是否写 video-done（额度依据），因此宁可少记，不可虚记。
"""

from __future__ import annotations


def is_plausible_completion(*, saw_progress: bool, wall_seconds: float,
                            duration: int, rate: float) -> bool:
    """判断"播放完成"是否可信。

    可信的两种情形：
      1. 本视频确实在播——watcher 上报过 `cur` 明显小于 `dur`（saw_progress）；
      2. 没见到进度，但实际耗时与"时长/倍速"基本匹配（推断是续播，不是残留状态）。

    `duration` 或 `rate` 缺失时按可信处理（信息不足，不阻断正常流程）。
    """
    if saw_progress:
        return True
    if duration <= 0 or rate <= 0:
        return True
    expected = duration / rate
    # 门槛同时封顶 20 秒：长视频不必等满一半，短视频也不会被 20 秒卡住
    return wall_seconds >= min(expected * 0.5, 20.0)


# 单节播放等待（B，2026-10-29）。旧实现把 `max_wait` 写死 600 秒，凡是
# 「视频时长 ÷ 倍速 > 600 秒」的一节必然在 79%~96% 被砍一刀——实测账号#1 一天
# 6 次中断全是这一类（644/816s、710/741s、716/796s），白跑一次“中断→重载”。
WAIT_INIT_SECONDS = 600     # 还没收到第一个进度 tick（不知道时长）时的起步上限
WAIT_SLACK_SECONDS = 120    # 弹性上限的余量：缓冲/加载/弹题耗时都从这里扣
WAIT_CEIL_SECONDS = 1800    # 绝对上限 30 分钟：再长的视频也不无限等，防卡死

# watcher 注入重试次数（A，2026-10-29）：注入失败最常见的原因是“上一节播完时
# iframe 刚重建”，等一下再试基本就能成。实测 21:33:19 一次失败直接导致整节重播。
WATCHER_INJECT_TRIES = 2


def next_wait_limit(*, elapsed: float, remaining_content: float, rate: float) -> float:
    """算出“这一节还应给多久”。两个引擎共用同一份算法，不各写一套。

    = 已过时间 + 剩余内容÷倍速 + 余量，再封顶 `WAIT_CEIL_SECONDS`。
    倍速未知（<=0）时按 1.0 算，宁可多等也不误砍正常播放。
    """
    r = rate if rate and rate > 0 else 1.0
    return min(WAIT_CEIL_SECONDS, elapsed + remaining_content / r + WAIT_SLACK_SECONDS)
