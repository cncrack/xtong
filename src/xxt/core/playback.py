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
