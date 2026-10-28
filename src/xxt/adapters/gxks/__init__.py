"""高校考试网（第二平台）适配器 —— **T1 骨架**，2026-10-29。

本包只放平台相关的东西，三条硬规矩（PLAN-GXKS §11.2 / §11.13）：

1. **平台细节不出本包**：URL、选择器、接口名全部来自 `selectors.py`；
   核心层不得出现站点字符串（有 `tests/test_gxks_boundaries.py` 守着）。
2. **浏览器不自造**：一律经 `core/browser.py` 的 `persistent_launch*`，
   每账号一个 profile 目录（登录态与续播 cookie 都在里面）。
3. **完成判定只认平台回读**：本平台计时是页面墙钟 + 每 60 秒一次 `updateTime.ashx`，
   本地计时只能用来判断"还在不在走"，不能用来判"学完了"（`gaoxiaokaoshi.md` §2.10）。

当前状态（2026-10-29）：
- 已完成：列表页**纯解析**（`listparse.py`，HTML → dict，不碰网络/浏览器），
  用合成夹具可离线回归（`tests/test_gxks_listparse.py`）；
- 未实现：`login` / 会话探活（T2）、`fetch_courses` / `fetch_chapters` 的**页面驱动**（T3-b）。
未实现就先抛 `NotImplementedError`，避免"以为跑通了其实是空实现"。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..base import PlatformAdapter
from . import listparse, selectors


class GxksAdapter(PlatformAdapter):
    """高校考试网适配器（骨架）。"""

    name = "gxks"

    def __init__(self, settings: Any) -> None:
        self.settings = settings

    # ------------------------------------------------------------ 元信息

    @property
    def login_url(self) -> str:
        """登录页地址（实测只有 HTTP，传输无加密已由用户知悉）。"""
        return selectors.LOGIN_PAGE

    @property
    def update_time_path(self) -> str:
        """进度上报路径：仅用于**判断上报是否按时发生**，禁止主动伪造请求。"""
        return selectors.UPDATE_TIME_PATH

    # ------------------------------------------------------------ 只读解析（已完成）

    def parse_course_page(self, html: str) -> dict[str, Any]:
        """一页列表 HTML → `{rows, pager}`（纯函数，无副作用）。"""
        return listparse.parse_course_page(html)

    # ------------------------------------------------------------ 未实现项

    @staticmethod
    def _todo(what: str, batch: str) -> None:
        raise NotImplementedError(f"gxks 适配器尚未实现：{what}（计划批次 {batch}）")

    def login(self, state_path: Path) -> bool:
        self._todo("登录", "T2")

    def probe_session(self, state_path: Path) -> tuple[str, str | None]:
        """会话探活的正解是"真访问一次轻量页看落不落登录页"。

        ⚠ 不能读 cookie 过期时间判断：ASP.NET 的 `.ASPXAUTH` 是持久 cookie，
        但 `ASP.NET_SessionId` 关浏览器就失效，两者不一致。
        """
        self._todo("会话探活", "T2")

    def fetch_courses(self, state_path: Path) -> list[dict[str, Any]]:
        """课程/节读取的**驱动部分**（T3-b）：打开列表页 → 逐页解析 → 合并。

        与超星的区别：那边是一个 JSON 接口一次拿全；这里是 iframe 里的表格 +
        `__doPostBack` 翻页，必须逐页回发（实测 4 页 × 9 行 = 36 节）。
        """
        self._todo("课程读取的页面驱动", "T3-b")

    def fetch_chapters(self, course_row: dict[str, Any], state_path: Path) -> list[dict[str, Any]]:
        """节读取（T3-b）：本平台“课程”与“节”共用一个列表，不再需要二次下钻。"""
        self._todo("节读取的页面驱动", "T3-b")
