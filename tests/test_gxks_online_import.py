"""G-7 在线入库 + 已做标记（2026-11-24 用户方案 A）。

## 用户定案

> 「平台判分页的标准答案 = 平台官方答案，对的，这个更好」

> 「客观题的题目和答案要入，主观题，题目要入，答案暂不考虑入，因为每个人不一样」

> 「我建议 A（主观题只存题目，picked 和 standard 都不写）」→ 按 A

## 守什么

1. **客观题**：题目 + 答案（`standard_source='site'`）都入
2. **主观题**：**只入题目**，`picked`/`standard` 一律 NULL
3. 在线解析要比离线更严（额外用 qid 逐题对齐）
4. 入库失败不能影响交卷结果
5. 平台标准答案到了 → `gx_pending` 里对应题标 `VERIFIED`
6. 已做过的节在列表里看得出来
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from xxt.adapters.gxks import practice
from xxt.store import gxks_pending
from xxt.store import gxks_questions
from xxt.store.schema_gxks import open_question_bank


def _row(qid: str, type_code: str, stem: str, opts: list[str], score: str = "10") -> list:
    """造一行答题页 `tm_arr`（列含义见 practice._answer_rows）。"""
    return [qid, type_code, stem, score, str(len(opts)), ";".join(opts),
            "", "0", "1475128", "", "z", "0", "", "1"]


def _review_html(*blocks: str) -> str:
    return ('<html><body><div>总得分 10 分</div><div>试卷总分 10 分</div>'
            + "".join(blocks) + "</body></html>")


def _block(no: int, stem: str, opts: list[str], picked: str, standard: str,
           score: str = "10", kind: str = "tmda") -> str:
    """造判分页里的一题（`div#ddTm_N`）。

    `kind` 手动填进 span 的 class：`tmda` 是正常标注；传 `""` 可造"没有标准答案"的题。
    """
    opt_html = "".join(
        f'<label for="tm_{no}_{chr(97 + i)}">{chr(65 + i)}、{o}</label>'
        for i, o in enumerate(opts))
    return (
        f'<div id="ddTm_{no}"><dl><dt><b>{no}.</b>{stem}</dt>'
        f"<dd>{opt_html}</dd>"
        f'<dd><span class="{kind}">考生答案</span><span>{picked}</span></dd>'
        f'<dd><span class="{kind}">标准答案</span><span>{standard}</span></dd>'
        f'<dd><span class="{kind}">考生得分</span><span>{score}分</span></dd>'
        "</dl></div>")


class TestParseReviewOnline(unittest.TestCase):
    """在线判定页解析（比离线更严：额外用 qid 逐题对齐）。"""

    def _parse(self, html: str, rows: list, **kw):
        return practice.parse_review_online(
            account_label="default",
            review_url=kw.pop("url", "http://x/viewExam.aspx?ExamID=14669&Clerk_kscj_ID=1"),
            review_html=html, raw_rows=rows,
            type_map={"2": "单选题", "1": "填空题"},
            exercise_id=kw.pop("exercise_id", "14669"), **kw)

    def test_objective_question_parses_standard_answer(self):
        rows = [_row("9385778", "2", "总体国家安全观的核心是___。", ["一个总体", "十个坚持"])]
        html = _review_html(_block(1, "总体国家安全观的核心是___。",
                                   ["一个总体", "十个坚持"], "A", "A"))
        got = self._parse(html, rows)
        self.assertEqual(len(got["questions"]), 1)
        q = got["questions"][0]
        self.assertEqual(q["qid"], "9385778")
        self.assertEqual(q["type_code"], "2")
        self.assertEqual(q["answer"]["standard"]["keys"], ["a"])
        self.assertEqual(q["answer"]["standard_source"], "site")

    def test_exercise_id_mismatch_rejected(self):
        """判分页的练习 ID 与本次不一致 → 拒绝（防把 A 卷答案配到 B 卷）。"""
        rows = [_row("1", "2", "题干", ["甲", "乙"])]
        html = _review_html(_block(1, "题干", ["甲", "乙"], "A", "A"))
        with self.assertRaises(ValueError) as ctx:
            self._parse(html, rows, url="http://x/viewExam.aspx?ExamID=99999&Clerk_kscj_ID=1")
        self.assertIn("练习 ID", str(ctx.exception))

    def test_missing_clerk_id_rejected(self):
        rows = [_row("1", "2", "题干", ["甲", "乙"])]
        html = _review_html(_block(1, "题干", ["甲", "乙"], "A", "A"))
        with self.assertRaises(ValueError) as ctx:
            self._parse(html, rows, url="http://x/viewExam.aspx?ExamID=14669")
        self.assertIn("Clerk_kscj_ID", str(ctx.exception))

    def test_question_count_mismatch_rejected(self):
        """判分页少一题 → 拒绝（漏题入库比不入库更糟）。"""
        rows = [_row("1", "2", "甲题", ["A", "B"]), _row("2", "2", "乙题", ["C", "D"])]
        html = _review_html(_block(1, "甲题", ["A", "B"], "A", "A"))
        with self.assertRaises(ValueError) as ctx:
            self._parse(html, rows)
        self.assertIn("题数与交卷前不一致", str(ctx.exception))

    def test_stem_mismatch_rejected(self):
        """题干对不上 → 拒绝（这是防错配答案的核心校验）。"""
        rows = [_row("1", "2", "甲题", ["A", "B"])]
        html = _review_html(_block(1, "完全不同的题干", ["A", "B"], "A", "A"))
        with self.assertRaises(ValueError) as ctx:
            self._parse(html, rows)
        self.assertIn("题干不一致", str(ctx.exception))


class TestSubjectivityRule(unittest.TestCase):
    """主观题：题目要入，答案不入（用户 2026-11-24 方案 A）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = open_question_bank(Path(self.tmp.name) / "q.db")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def _bundle(self, type_code: str, with_standard: bool) -> dict:
        picked = {"keys": ["a"], "kind": "single", "text": "甲"}
        standard = picked if with_standard else None
        return {
            "format_version": 1, "account_label": "default",
            "exam_start_id": "23555186", "exercise_id": "14669",
            "course_id": "", "section_id": "14669", "section_name": "2.1",
            "paper_label": "", "start_time": "", "result_id": "1",
            "total_score": 10.0, "got_score": 10.0, "type_map": {"2": "单选题", "1": "填空题"},
            "questions": [{
                "ordinal": 1, "qid": "9385778", "type_code": type_code,
                "type_name": "单选题" if type_code == "2" else "填空题",
                "stem": "某题干", "stem_html": "某题干", "max_score": 10.0,
                "parts": [{"kind": "option", "key": "a", "text": "甲", "html": "甲"},
                          {"kind": "option", "key": "b", "text": "乙", "html": "乙"}],
                "extra": {},
                "answer": {"picked": picked,
                           "picked_source": "site" if with_standard else None,
                           "standard": standard,
                           "standard_source": "site" if with_standard else None,
                           "score_got": 10.0, "is_correct": with_standard,
                           "explanation_html": ""},
            }],
            "source_rows": [],
        }

    def test_objective_saves_item_and_answer(self):
        gxks_questions.import_bundle(self.db, self._bundle("2", True))
        items = self.db.execute("SELECT COUNT(*) FROM gx_q_items").fetchone()[0]
        ans = self.db.execute(
            "SELECT standard_source FROM gx_q_answers").fetchone()
        self.assertEqual(items, 1, "客观题必须入库")
        self.assertEqual(ans[0], "site", "客观题的标准答案必须入库")

    def test_subjective_saves_item_but_not_answer(self):
        """主观题：题目入，答案（picked/standard）都不写。"""
        gxks_questions.import_bundle(self.db, self._bundle("1", True))
        items = self.db.execute("SELECT COUNT(*) FROM gx_q_items").fetchone()[0]
        row = self.db.execute(
            "SELECT picked_json, picked_source, standard_json, standard_source "
            "FROM gx_q_answers").fetchone()
        self.assertEqual(items, 1, "主观题的**题目**必须入库")
        self.assertIsNone(row[0], "主观题不能存考生答案（每人不同）")
        self.assertIsNone(row[1])
        self.assertIsNone(row[2], "主观题不能存标准答案")
        self.assertIsNone(row[3])

    def test_subjective_not_reusable_across_accounts(self):
        """主观题入库后不能被其它账号当答案复用。"""
        gxks_questions.import_bundle(self.db, self._bundle("1", True))
        hit = gxks_questions.lookup_answer(
            self.db, stem="某题干",
            options=["甲", "乙"])
        self.assertIsNone(hit, "主观题不该被 lookup_answer 命中")


class TestPendingVerifiedBySiteAnswer(unittest.TestCase):
    """平台判分页给了标准答案 → 待确认项自动标 VERIFIED。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = open_question_bank(Path(self.tmp.name) / "q.db")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def _add(self, ordinal: int) -> int:
        return gxks_pending.add(
            self.db, account_label="default", course_id="",
            section_id="14669", section_name="2.1", exercise_id="14669",
            question_ordinal=ordinal, type_code="2", stem="题干",
            reason="ai_used", risk={"reasons": ["ai_used"]})

    def test_marks_only_listed_ordinals(self):
        self._add(1)
        self._add(2)
        n = gxks_pending.verify_by_site_answer(
            self.db, account_label="default", exercise_id="14669", ordinals=[1])
        self.assertEqual(n, 1)
        # `status=None` 才取全部状态（默认只查 pending，标成 verified 的会查不到）
        rows = {r["question_ordinal"]: r
                for r in gxks_pending.list_pending(self.db, status=None)}
        self.assertEqual(rows[1]["status"], gxks_pending.VERIFIED)
        self.assertEqual(rows[1]["decided_by"], "site-answer")
        self.assertEqual(rows[2]["status"], gxks_pending.PENDING)

    def test_does_not_override_human_decision(self):
        """人工已决定过的不能被平台答案覆盖（终态保护）。"""
        pid = self._add(1)
        gxks_pending.decide(self.db, pid, status=gxks_pending.SKIPPED,
                            decided_by="human", note="人工跳过")
        n = gxks_pending.verify_by_site_answer(
            self.db, account_label="default", exercise_id="14669", ordinals=[1])
        self.assertEqual(n, 0)
        row = gxks_pending.list_pending(self.db, status=None)[0]
        self.assertEqual(row["status"], gxks_pending.SKIPPED)
        self.assertEqual(row["decided_by"], "human")

    def test_empty_ordinals_is_noop(self):
        self._add(1)
        self.assertEqual(gxks_pending.verify_by_site_answer(
            self.db, account_label="default", exercise_id="14669",
            ordinals=[]), 0)


class TestWorkScriptWiring(unittest.TestCase):
    """`gxks_work.py` 的交卷后入库接线。"""

    @classmethod
    def setUpClass(cls):
        src = Path(__file__).resolve().parents[1] / "scripts" / "gxks_work.py"
        cls.SRC = src.read_text(encoding="utf-8")

    def test_waits_for_graded_page_then_imports(self):
        self.assertIn("gwork.wait_graded(", self.SRC)
        self.assertIn("parse_review_online(", self.SRC)
        self.assertIn("gxks_questions.import_bundle(", self.SRC)

    def test_import_never_breaks_submit(self):
        """入库整段必须在 try 里 —— 交卷是既成事实，入库只是附加收益。"""
        seg = self.SRC.split("gwork.submit(paper[\"frame\"])", 1)[1]
        body = seg.split("else:", 1)[0]
        self.assertIn("try:", body)
        self.assertIn("except Exception", body)
        self.assertIn("交卷本身已成功", body)

    def test_submit_happens_before_import(self):
        """顺序不能反：没交卷就没有判分页。

        ⚠ 2026-11-25：新增的 `_await_confirm_and_reclaim` 里也有 `parse_review_online`，
        在文件**前面**（早于主流程的 submit）。所以不能看整个文件的 index 顺序，
        要看 **`def main()` 内部**：main 里 submit 必须先于 parse_review_online。
        """
        seg = self.SRC.split("def main()", 1)[1]
        self.assertLess(seg.index("gwork.submit(paper[\"frame\"])"),
                        seg.index("parse_review_online("))


class TestAttemptAnnotation(unittest.TestCase):
    """已做过的节要在列表里看得出来（用户 2026-11-24）。"""

    @classmethod
    def setUpClass(cls):
        from xxt.panel_gxks import app as panel_app
        cls.SRC = Path(panel_app.__file__).read_text(encoding="utf-8")

    def test_state_annotates_attempt_counts(self):
        self.assertIn("_annotate_attempts(", self.SRC)
        seg = self.SRC.split("def _annotate_attempts(", 1)[1].split(
            "def ", 1)[0]
        self.assertIn("attempt_count", seg)
        self.assertIn("attempt_last_at", seg)
        self.assertIn("gx_q_papers", seg)

    def test_annotation_does_not_write_cache(self):
        """只影响展示，不改缓存文件（缓存职责是存平台原始数据）。"""
        seg = self.SRC.split("def _annotate_attempts(", 1)[1].split(
            "def ", 1)[0]
        for bad in ("write_text", "json.dump", "sections_cache.update"):
            self.assertNotIn(bad, seg)

    def test_page_shows_attempt_count(self):
        from xxt.panel_gxks.page import PAGE
        seg = PAGE.split("function practiceCell(row)", 1)[1].split(
            "function ", 1)[0]
        self.assertIn("attempt_count", seg)
        self.assertIn("已做 ", seg)
        self.assertIn("可重做", seg)


if __name__ == "__main__":
    unittest.main()
