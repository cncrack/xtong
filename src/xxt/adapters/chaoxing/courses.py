"""学习通课程列表解析。

数据源：学习空间页面自身发起的 mooc2-ans visit/courselistdata 接口（返回 HTML 片段，
2026-10-24 实况核对）。卡片结构：

    <div class="course ... " id="c_266339100">
      <input type="hidden" class="courseId" value="266339100"/>
      <input type="hidden" class="clazzId" value="154181801"/>
      <input type="hidden" class="curPersonId" value="..."/>
      <a href="https://mooc1.chaoxing.com/visit/stucoursemiddle?courseid=..&clazzid=..">..</a>
      ... <span class="course-name">课程名</span> ...
    </div>
"""

from __future__ import annotations

from typing import Any

from bs4 import BeautifulSoup


def parse_course_cards(fragment: str) -> list[dict[str, Any]]:
    """解析 courselistdata HTML 片段 → [{platform_course_id, clazz_id, person_id, name, url}]。"""
    soup = BeautifulSoup(fragment, "html.parser")
    courses: list[dict[str, Any]] = []
    for card in soup.select("div.course[id^=c_]"):
        def hidden(cls: str) -> str:
            el = card.select_one(f"input.{cls}")
            return (el.get("value") or "").strip() if el else ""

        course_id = hidden("courseId")
        if not course_id:
            continue
        name_el = card.select_one(".course-name")
        link = card.select_one("a[href]")
        courses.append(
            {
                "platform_course_id": course_id,
                "clazz_id": hidden("clazzId"),
                "person_id": hidden("curPersonId"),
                "name": name_el.get_text(strip=True) if name_el else f"课程{course_id}",
                "url": (link.get("href") or "").strip() if link else "",
            }
        )
    return courses
