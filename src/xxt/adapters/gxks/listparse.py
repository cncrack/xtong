"""「我的课程」列表解析 —— **纯函数，不碰浏览器**（T3 只读部分，2026-10-29）。

为什么不能照抄超星：超星走 `courselistdata` JSON 接口 + 章节树接口，拿到的就是结构化数据；
本站是 WebForms 回发，数据在 iframe 里的 HTML 表格里，翻页也只能 `__doPostBack`。
所以这里只做“把 HTML 变成 dict”，页面驱动另写（`fetch_*` 仍待实现）。

三条判定口径（证据见 `docs/gxks/gaoxiaokaoshi.md` §2.8）：

1. **节 ID 在 `onclick="showframe('名称', 1289)"` 的第二个参数**——跨次会话稳定，
   名称会变、ID 不会，所以主键用 ID；
2. **学时单位就是分钟**；`45分钟` 是学分口径，**不等于视频实际时长**
   （实测 1.1 要求 45 分钟、视频 47:51），所以不能拿 `video.duration` 当达标线；
3. **未解锁时「参加练习」的 onclick 是 `alert('请先完成学习！')`**——读 onclick 就能判门禁，
   **不必点击试探**（既满足“不硬闯风控”，也拿得到解锁状态）；达标后同一位置变成
   `showExam(15263)`，括号里是练习 ID（2026-10-29 1.1 实测）。
"""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

from . import selectors

# showframe('1.1 国防概述', 1289) —— 引号单双都可能，名字里可能带空格
_SECTION_ID_RE = re.compile(r"showframe\(\s*['\"][^'\"]*['\"]\s*,\s*(\d+)\s*\)")
# showExam(15263) —— 达标后练习入口的 onclick
_EXAM_ID_RE = re.compile(r"showExam\(\s*(\d+)\s*\)")
_DIGITS_RE = re.compile(r"(\d+)")
_PAGER_RE = re.compile(r"(\d+)\s*/\s*(\d+)")
# 表头判据用**列名关键词**，不能用 `th`：实测本站表头用的是 `<td>`，
# 按 `th` 判会把表头当数据行（Q9 真实页面踩到）。
_HEADER_LABELS = set(selectors.LIST_COLUMNS.values())


def to_minutes(text: str | None) -> int | None:
    """`"45分钟"` → 45；`"0分钟"` → 0；空/无数字 → None。

    不用 float：平台这一列只出现过整分钟，出现小数说明改版了，宁可返回 None 让人发现。
    """
    if not text:
        return None
    m = _DIGITS_RE.search(text)
    return int(m.group(1)) if m else None


def parse_section_id(onclick: str | None) -> int | None:
    """从「开始学习」的 onclick 里取平台节 ID。取不到返回 None（**不要退回用名字**）。"""
    if not onclick:
        return None
    m = _SECTION_ID_RE.search(onclick)
    return int(m.group(1)) if m else None


def is_practice_locked(onclick: str | None) -> bool:
    """「参加练习」是否仍锁着。

    判据：onclick 是 `alert(...)`（实测就是 `alert('请先完成学习！')`），
    或直接含锁定提示语。达标后实测变为 `showExam(<练习ID>)`，按未锁处理。
    没有 onclick 时按“锁着”处理：误判成解锁只会提前去读题，误判成锁定会白等一整轮。
    """
    if not onclick:
        return True                      # 没有 onclick：当作拿不到门禁信息，按锁处理
    lowered = onclick.lower()
    return "alert(" in lowered or selectors.PRACTICE_LOCK_HINT in onclick


def parse_exam_id(onclick: str | None) -> int | None:
    """从「参加练习」的 onclick 里取练习/考试 ID；锁着时返回 None。

    达标后实测形态：`showExam(15263)`。拿不到就返回 None——**不要拿节 ID 去凑**。
    """
    if not onclick or selectors.EXAM_ONCLICK_PREFIX not in onclick:
        return None
    m = _EXAM_ID_RE.search(onclick)
    return int(m.group(1)) if m else None


def parse_state(state_text: str | None) -> str:
    """把状态文案归一成 `done` / `learning` / `not_started` / `unknown`。

    三个文案都已实测（2026-10-29）：未学习 / 学习中 / 已完成。
    命中不了就返回 `unknown`，让上层显式处理，而不是猜成“没学完”然后无限重试。
    """
    text = (state_text or "").strip()
    if not text:
        return "unknown"
    if any(h in text for h in selectors.STATE_DONE_HINTS):
        return "done"
    if selectors.STATE_LEARNING in text:
        return "learning"
    if selectors.STATE_NOT_STARTED in text:
        return "not_started"
    return "unknown"


def _pick_table(soup: BeautifulSoup) -> Any:
    """优先 `table.table`；找不到就退而取“行最多的表”，避免改个 class 就整页读不出。"""
    table = soup.select_one(selectors.LIST_TABLE)
    if table is not None:
        return table
    best = None
    best_rows = 0
    for cand in soup.find_all("table"):
        n = len(cand.find_all("tr"))
        if n > best_rows:
            best, best_rows = cand, n
    return best


def parse_course_rows(html: str) -> list[dict[str, Any]]:
    """解析表格行 → 节记录列表（按页面顺序，`idx` 从 0 开始）。

    返回字段与 PLAN §1.2 对齐：
    `platform_chapter_id / name / idx / required_minutes / done_minutes / state_text /
    state / practice_locked / exam_id / category / done_on_platform`。
    """
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    table = _pick_table(soup)
    if table is None:
        return []

    rows: list[dict[str, Any]] = []
    for tr in table.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        # 表头行没有 td（全是 th），或列数明显不够
        if not any(c.name == "td" for c in cells) or len(cells) < 5:
            continue
        texts = [c.get_text(" ", strip=True) for c in cells]
        if texts and texts[0] in _HEADER_LABELS:      # 表头行（本站用 td 也能写表头）
            continue
        anchors = tr.find_all("a")
        start_onclick = anchors[0].get("onclick") if len(anchors) > 0 else None
        prac_onclick = anchors[1].get("onclick") if len(anchors) > 1 else None

        state_text = texts[4] if len(texts) > 4 else ""
        required = to_minutes(texts[2] if len(texts) > 2 else None)
        done = to_minutes(texts[3] if len(texts) > 3 else None)
        state = parse_state(state_text)
        section_id = parse_section_id(start_onclick)

        rows.append(
            {
                "platform_chapter_id": str(section_id) if section_id is not None else None,
                "name": texts[0],
                "idx": len(rows),
                "category": texts[1] if len(texts) > 1 else "",
                "required_minutes": required,
                "done_minutes": done,
                "state_text": state_text,
                "state": state,
                # ⚠ 只认状态文案，**不用 done>=required 推断**：实测出现过 42/42 仍是「学习中」
                "done_on_platform": state == "done",
                "practice_locked": is_practice_locked(prac_onclick),
                "exam_id": parse_exam_id(prac_onclick),
            }
        )
    return rows


def parse_pager(html: str) -> dict[str, Any]:
    """解析分页条 → `{current, total, has_next, has_prev}`。

    翻页是 ASP.NET 回发（`__doPostBack`），**不能用 URL 参数**，所以这里只提供“现在第几页/
    还有没有下一页”，真正点下一页由驱动层做。
    """
    out: dict[str, Any] = {"current": None, "total": None, "has_next": None, "has_prev": None}
    if not html:
        return out
    soup = BeautifulSoup(html, "html.parser")

    label = soup.select_one(selectors.PAGER_LABEL)
    if label is not None:
        m = _PAGER_RE.search(label.get_text(" ", strip=True).replace(" ", ""))
        if m:
            out["current"], out["total"] = int(m.group(1)), int(m.group(2))

    nxt = soup.select_one(selectors.PAGER_NEXT)
    if nxt is not None:
        out["has_next"] = not _is_disabled(nxt)
    prev = soup.select_one(selectors.PAGER_PREV)
    if prev is not None:
        out["has_prev"] = not _is_disabled(prev)
    return out


def _is_disabled(tag: Any) -> bool:
    """WebForms 的禁用写法不止一种：`disabled` 属性 / `disabled=""` / class 带 disabled。"""
    if tag.has_attr("disabled"):
        return True
    classes = " ".join(tag.get("class") or []).lower()
    return "disabled" in classes


def parse_course_page(html: str) -> dict[str, Any]:
    """一页完整解析：`{rows, pager}`。驱动层每翻一页调一次。"""
    return {"rows": parse_course_rows(html), "pager": parse_pager(html)}
