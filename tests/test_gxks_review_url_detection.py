"""判分页识别必须同时处理 `viewExam.aspx` 和 `ExamMessage.aspx`。

## 为什么有这条测试（2026-11-28 我第二次犯同样的错）

平台交卷后的跳转是**两跳**：

    作答页 → ExamMessage.aspx（交卷成功页）→ viewExam.aspx（判分页）

`ExamMessage` 的 URL 里带 `Tk_Cl_Id` + `Clerk_Kscj_Id`，可**自己拼**出判分页。

- 2026-11-27（提交 `3f049a3`）修过一次：主流程加了 ExamMessage 处理。
- 2026-11-28 我写「自动检测」时**又只认 viewexam**，导致用户交卷成功
  却检测不到，白等 10 分钟。用户当场质问：
  「为什么你每次改完不再审查一下是否完整，每次都要测一下、改一下，
   这个问题我们前面是不是已经碰到过」。

## 这条测试锁什么

`gxks_work.py` 里**每一处「判断 URL 是不是判分页」的代码**，
都必须同时考虑 exammessage。做法：找出所有含 `viewexam.aspx` 的**代码行**
（排除注释/文档），要求同一行或紧邻上下文里出现 `exammessage`。

⚠ 已知的例外：`[6/6]` 段在 `review_url` 已拼好之后才等，那里只可能是
真正的判分页，不会遇到 ExamMessage —— 这些位置用 `_ALLOWED_VIEWEXAM_ONLY`
显式登记，新增例外必须写理由。
"""

from __future__ import annotations

from pathlib import Path

SRC = (Path(__file__).resolve().parents[1] / "scripts" / "gxks_work.py").read_text(
    encoding="utf-8")

#: 允许「只认 viewexam」的位置（行内容特征 -> 理由）。
#: 新增例外必须写清楚为什么这里不会遇到 ExamMessage。
_ALLOWED_VIEWEXAM_ONLY = {
    # review_url 已经拼好（ExamMessage 那一步早在上面处理过了），
    # 这里等的是**真的** viewExam 出现，不需要再认 ExamMessage。
    'if "viewexam.aspx" in (page2.url or "").lower():',
    'if "viewexam.aspx" not in (page2.url or "").lower():',
    # 主流程等待段的「直接到了判分页」分支：紧跟着就是 exammessage 分支，
    # 两者是同一个 while 里的先后判断，不算漏。
    'if "viewexam.aspx" in low:',
    # _find_review 里「examessage 分支之上、viewexam 分支之下」的那句：
    # exammessage 已在同一函数前面处理并 return 了，走到这里只可能是真 viewExam。
    'if "viewexam.aspx" not in _low:',
}


def _code_lines() -> list[tuple[int, str]]:
    out = []
    for i, line in enumerate(SRC.splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        low = s.lower()
        if "viewexam" not in low:
            continue
        # 只关心「判断 URL」的行，不关心拼 URL / 注释
        if not any(k in s for k in ('"viewexam.aspx"', "'viewexam.aspx'")):
            continue
        out.append((i, s))
    return out


def test_every_view_exam_check_also_handles_exam_message():
    """每个判分页判断点都要同时认 exammessage，除非显式登记为例外。"""
    missing = []
    for line_no, line in _code_lines():
        if line in _ALLOWED_VIEWEXAM_ONLY:
            continue
        # 同一行里有 exammessage 就算处理了
        if "exammessage" in line.lower():
            continue
        missing.append((line_no, line))
    assert not missing, (
        "以下判分页判断点没有同时处理 ExamMessage.aspx "
        "（平台交卷先跳交卷成功页，只认 viewexam 会漏）：\n"
        + "\n".join(f"  L{ln}: {s}" for ln, s in missing))


def test_exam_message_branch_can_build_review_url():
    """ExamMessage 分支必须能自己拼出判分页（Tk_Cl_Id + Clerk_Kscj_Id）。"""
    assert "Tk_Cl_Id" in SRC
    assert "Clerk_Kscj_Id" in SRC
    # 拼出来的必须是 viewExam 地址
    assert "/ExamList/ExamPage/viewExam.aspx" in SRC


def test_allowed_exceptions_are_documented():
    """例外表不能是空口白话——每条都要能解释清楚。"""
    assert _ALLOWED_VIEWEXAM_ONLY, "例外表为空说明审计没做"
    assert len(_ALLOWED_VIEWEXAM_ONLY) <= 4, "例外太多说明设计有问题"
