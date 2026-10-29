"""学习通新版章节树解析（fanyav3 → mycourse/studentcourse 页 #fanyaChapter）。

2026-10-24 实况核对的结构：

    <div class="chapter_unit">                       ← 章（其首个 .chapter_item 是章标题，无 id）
      <div class="chapter_item" id="cur1227271652"
           onclick="toOld('266339100','1227271652','154181801',0)"
           title="导言">                             ← 叶子（节/知识点）
        <a class="clicktitle"><span class="catalog_sbar">1.1</span> 导言</a>
        <div class="catalog_state icon_yiwanc">…已完成…</div>   ← 已完成标记（可选）
        <input type="hidden" class="knowledgeJobCount" value="1"/>
      </div>
    </div>
"""

from __future__ import annotations

from typing import Any

from bs4 import BeautifulSoup


def parse_chapter_tree(html: str) -> list[dict[str, Any]]:
    """解析章节树 HTML → 叶子节点列表（按页面顺序）。

    返回 [{platform_chapter_id(知识点id), name(含编号与章名), idx, done_on_platform, job_count}]
    """
    soup = BeautifulSoup(html, "html.parser")
    leaves: list[dict[str, Any]] = []
    for unit in soup.select(".chapter_unit"):
        head_span = unit.select_one(".chapter_item .catalog_name span[title]")
        unit_name = (head_span.get("title") or "").strip() if head_span else ""
        for leaf in unit.select("div.chapter_item[id^=cur]"):
            kid = (leaf.get("id") or "")[3:]  # cur1227271652 → 1227271652
            if not kid.isdigit():
                continue
            title_a = leaf.select_one(".clicktitle")
            leaf_name = title_a.get_text(" ", strip=True) if title_a else f"知识点{kid}"
            job_el = leaf.select_one("input.knowledgeJobCount")
            leaves.append(
                {
                    "platform_chapter_id": kid,
                    "name": f"{unit_name}｜{leaf_name}" if unit_name else leaf_name,
                    "idx": len(leaves),
                    "done_on_platform": bool(leaf.select_one(".icon_yiwanc")),
                    "job_count": int(job_el.get("value") or 0) if job_el else 0,
                }
            )
    return leaves

# 本地记账与平台标识的对齐逻辑不在此处：它不含任何平台细节（URL/选择器/接口），
# 属于跨平台通用能力，故放在 `xxt.core.reconcile`；本文件只负责“把页面解析成抽象字段”。
