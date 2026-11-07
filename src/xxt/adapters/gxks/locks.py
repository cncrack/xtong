"""第二平台账号级会话锁（A1，2026-11-07）。

`PLAN-GXKS.md` §7.3 硬约束第 1 条：「账号级会话锁：一账号一进程一会话」。
超星侧 2026-10-25 就有了 `core/runmark.py`，第二平台一直没接 —— 双开只能靠面板
内存表 `_JOBS` 挡，下面三种情况全都能绕过：

1. **面板 + 手敲** `scripts/run_gxks.py --label 同一号`；
2. **两个面板**（`E:\\xxt` 与 `E:\\xxt-gxks` 各开一个，换个端口即可）；
3. **面板重启后旧任务还活着** —— 靠 `adopt_orphan_jobs` 认领才有救，认领失败就漏。

同账号两路心跳是**最强的机器指纹**，也正是 `PLAN-GXKS.md` 明令禁止的形态。

## 为什么不直接用 `runmark`

`runmark` 的键是 `account_id: int`（超星账号表主键），第二平台用的是 `label: str`
（`[a-z0-9_-]{1,24}`）。硬塞 int 得先哈希，等于把「甲」和「乙」撞进同一个键的概率
从 0 抬起来 —— 而撞键的后果是**一个号把另一个号挡在门外**（或更糟：放行双开）。

所以这里**沿用同一套判据**（pid 活着 + 进程创建时间不晚于标记文件 mtime，后者防
Windows pid 复用），只换文件名 `data/run/gxks_<label>.json`。判据实现直接调
`runmark`，不复制一份 —— 超星侧踩过的坑（venv 重定向器、pid 复用误杀）不用再踩一遍。

## 「自己人」判据（这是最容易写错的一处）

面板的 `_spawn_script` 是**先 spawn、立刻替子进程写标记**（`pid=proc.pid`）的。
子进程启动后自查会读到这条**自己**的标记 → 若不排除「自己/父进程」，任务会当场
把自己判成「已有会话在跑」并 `[FAIL]` 秒退（超星侧 2026-10-27 就是这么炸的，
表现为「定时触发了但学不了」）。

判据必须含 **ppid**：`.venv\\Scripts\\python.exe` 是 venv 的**重定向器**，
`Popen` 拿到的只是 shim 的 pid，真正跑代码的是它再拉起的孙进程
（实测 shim 5908 / 真进程 22640），而孙进程的 ppid 正是那个 shim。
⇒ 「标记 pid == 自己的 pid 或父 pid」都算自己人。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from ...core import runmark
from . import accounts as gaccounts

#: 标记文件放在 `data/run/` 下，与超星侧同一个目录，但文件名带 `gxks_` 前缀 ——
#: 那里放的是 `<int>.json`，两者永不撞。
PREFIX = "gxks_"


def lock_path(data_dir: str | Path, label: str) -> Path:
    """该账号的会话锁文件。`label` 非法一律拒（绝不回落成某个默认目录）。"""
    key = str(label or "").strip().lower()
    if not gaccounts.is_valid_label(key):
        raise ValueError(f"账号代号 {label!r} 非法：只允许 a-z0-9_- 且 ≤24 字符")
    return Path(data_dir) / "run" / f"{PREFIX}{key}.json"


def read_lock(data_dir: str | Path, label: str) -> dict[str, Any] | None:
    """读标记内容；文件不存在 / 坏文件 → None。"""
    p = lock_path(data_dir, label)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def live(data_dir: str | Path, label: str) -> dict[str, Any] | None:
    """标记存在且**指向的进程真的还活着**（含 pid 归属校验）→ 返回标记内容。

    模糊情形一律偏向「活着」：宁可误拒启动，不可放过双开。
    """
    info = read_lock(data_dir, label)
    if not info:
        return None
    try:
        pid = int(info.get("pid") or 0)
    except (TypeError, ValueError):
        return None
    if pid <= 0 or not runmark.pid_alive(pid):
        return None
    if not runmark.pid_owns_file(lock_path(data_dir, label), pid):
        return None                       # pid 被复用了 → 不是它
    return info


def _is_self(pid: int) -> bool:
    return int(pid) in (os.getpid(), os.getppid())


def holder(data_dir: str | Path, label: str) -> dict[str, Any] | None:
    """**别人**是否正持有该账号的会话锁；自己人返回 None。

    这是所有调用方唯一需要的判据（面板 pre-check、脚本启动自查都用它）。
    """
    info = live(data_dir, label)
    if not info:
        return None
    if _is_self(int(info.get("pid") or 0)):
        return None
    return info


def acquire(data_dir: str | Path, label: str, *, course: str = "",
            pid: int | None = None) -> tuple[bool, dict[str, Any] | None]:
    """抢锁。返回 `(成功?, 占用者信息)`。

    - 已有**别人**的活标记 → `(False, 那条标记)`，调用方据此秒退；
    - 已有**自己人**的活标记（面板替我写的）→ `(True, None)`，且**不重写**文件：
      面板收尾时按它 spawn 时的 pid 释放，重写成真 pid 会让两边对不上；
    - 没有活标记（含进程已死的陈旧标记）→ 覆盖写入。
    """
    info = live(data_dir, label)
    if info:
        if _is_self(int(info.get("pid") or 0)):
            return True, None
        return False, info

    p = lock_path(data_dir, label)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "pid": int(pid or os.getpid()),
        "label": str(label).strip().lower(),
        "course": str(course or ""),
        "since": time.strftime("%Y-%m-%d %H:%M:%S"),
    }, ensure_ascii=False), encoding="utf-8")
    return True, None


def release(data_dir: str | Path, label: str, *,
            pid: int | None = None) -> bool:
    """释放锁，返回**是否真的删掉了**。

    `pid` 给了就做归属校验：只删「pid 是它 / 是自己 / 是父进程」的那条。
    不给则无条件删（脚本自己的 `finally` 用）。

    为什么要校验：面板杀掉子进程后按 spawn 时的 pid 释放，而如果这条锁其实已经
    被**另一个进程**接手（前一个死后被抢），乱删会把新会话的锁抹掉 → 又能双开。
    """
    p = lock_path(data_dir, label)
    if not p.exists():
        return False
    if pid is not None:
        info = read_lock(data_dir, label)
        if info is None:
            return False
        try:
            cur = int(info.get("pid") or 0)
        except (TypeError, ValueError):
            cur = 0
        if not (cur == int(pid) or _is_self(cur)):
            return False
    p.unlink(missing_ok=True)
    return True


def describe(info: dict[str, Any]) -> str:
    """给用户看的一句（**不含任何凭据**）。"""
    pid = info.get("pid") or "?"
    since = info.get("since") or "?"
    course = info.get("course") or ""
    tail = f"，课程 {course}" if course else ""
    return f"pid {pid}，自 {since} 起{tail}"
