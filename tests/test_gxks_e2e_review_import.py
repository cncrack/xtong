"""端到端：判分页 -> 解析 -> 入库 -> 节列表状态可见（2026-11-27）。

## 为什么要这条测试

用户批评：「你能不能一次性把链条全部搞定，一次修一个问题还要来回折腾？
你要全局考虑，要有远见。」

之前的测试都是**分段**的：`parse_review_online` 单独测、`import_bundle` 单独测、
`practiceCell` 单独测。但没有一条测试**把整条链接起来跑**，于是：

- `parse_review_online` 输出的 `exam_start_id` 是空串
- `import_bundle._validate` 要求它非空
- → 真实链路在入库前就断了，而所有分段测试全绿。

这条测试用**真实的 raw_rows 形状**造判分页 HTML，一路走到
「`_annotate_attempts` 能看到 attempt_count」，任何一环断了都会红。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from xxt.adapters.gxks import practice
from xxt.store import gxks_questions
from xxt.store.schema_gxks import open_question_bank

# 真实 raw_rows 形状（取自 2026-11-27 实跑日志的 tm_arr）
RAW_ROWS = [
    ["9385769", "2", "___本质上是解决军队“举什么旗”“走什么路”等根本问题，是强军胜战的导航仪。",
     "33", "4", "政治建军;改革强军;科技强军;人才强军", "", "0", "1475120", "", "z", "0", "", "1"],
    ["9385770", "2", "___基本功能是保证国家高度集中地控制军权，平时对国防建设和战备活动实施领导和管理。",
     "34", "4", "国防领导体制;国防战略;国防政策;国防成就", "", "0", "1475118", "", "z", "0", "", "1"],
    ["9385771", "2", "___是指导国防建设的政治策略，反映国防建设的指导思想和实现途径。",
     "33", "4", "国防领导体制;国防战略;国防政策;国防成就", "", "0", "1475117", "", "z", "0", "", "1"],
]


def _review_html() -> str:
    blocks = []
    for no, row in enumerate(RAW_ROWS, 1):
        stem = row[2]
        opts = row[5].split(";")
        picked = "A" if no < 3 else "C"
        opt_html = "".join(
            '<label for="tm_%d_%d">%s、%s</label>' % (no, i, chr(65 + i), o)
            for i, o in enumerate(opts))
        blocks.append(
            '<div id="ddTm_%d"><dl><dt><b>%d.</b>%s</dt><dd>%s</dd>'
            '<dd><span class="tmda">考生答案</span><span>%s</span></dd>'
            '<dd><span class="tmda">标准答案</span><span>%s</span></dd>'
            '<dd><span class="tmda">考生得分</span><span>%s分</span></dd>'
            "</dl></div>" % (no, no, stem, opt_html, picked, picked, row[3]))
    return ("<html><body><div>总得分 100 分</div><div>试卷总分 100 分</div>"
            + "".join(blocks) + "</body></html>")


REVIEW_URL = ("http://www.gaoxiaokaoshi.com/ExamList/ExamPage/viewExam.aspx"
              "?ExamID=14666&Clerk_kscj_ID=23445377")


class TestEndToEndReviewImport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "data").mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "data" / "gxks_questions.db"
        self.db = open_question_bank(self.db_path)

    def tearDown(self):
        try:
            self.db.close()
        except Exception:
            pass
        self.tmp.cleanup()

    def test_full_chain_reaches_attempt_count(self):
        """判分页 -> 解析 -> 入库 -> _annotate_attempts 看到次数。"""
        bundle = practice.parse_review_online(
            account_label="default", review_url=REVIEW_URL,
            review_html=_review_html(), raw_rows=RAW_ROWS,
            type_map={"2": "单选题"}, exercise_id="14666",
            section_id="1291", section_name="1.3 国防建设",
            exam_start_id="24726784", start_time="2026-11-27 16:40:10")

        # ① 解析出来的 bundle 必须能通过 import_bundle 的校验
        self.assertEqual(bundle["exam_start_id"], "24726784")
        self.assertEqual(len(bundle["questions"]), 3)

        # ② 入库
        saved = gxks_questions.import_bundle(self.db, bundle)
        self.assertTrue(saved["inserted"])
        self.assertEqual(saved["questions"], 3)

        # ③ 节列表状态可见（用户最终要看的那一项）
        from xxt.config import Settings
        from xxt.panel_gxks import app as panel_app
        settings = Settings(root=self.root)
        sections = {"rows": [{"exam_id": 14666, "practice_ready": True}]}
        out = panel_app._annotate_attempts(settings, "default", sections)
        row = out["rows"][0]
        self.assertEqual(row["attempt_count"], 1,
                         "入库后节列表必须显示「已做 1 次」")
        self.assertEqual(row["attempt_last_at"], "2026-11-27 16:40:10")

    def test_missing_exam_start_id_is_rejected(self):
        """回归：`exam_start_id` 为空时入库必须明确报错（2026-11-27 真实断点）。

        真实链路里脚本原来没传它 → bundle 带空串 → `_validate` 拒绝 →
        整条链静默失败。这里把它固化成断言，防止再退回去。
        """
        bundle = practice.parse_review_online(
            account_label="default", review_url=REVIEW_URL,
            review_html=_review_html(), raw_rows=RAW_ROWS,
            type_map={"2": "单选题"}, exercise_id="14666",
            section_id="1291", section_name="1.3 国防建设",
            exam_start_id="", start_time="")
        with self.assertRaises(ValueError) as ctx:
            gxks_questions.import_bundle(self.db, bundle)
        self.assertIn("exam_start_id", str(ctx.exception))

    def test_script_extracts_exam_start_id_from_landed_url(self):
        """脚本必须从答题页 URL 里提取 ExamStartId 并传到解析/入库链路。"""
        src = (Path(__file__).resolve().parents[1] / "scripts" / "gxks_work.py"
               ).read_text(encoding="utf-8")
        self.assertIn("ExamStartId", src)
        self.assertIn("exam_start_id=exam_start_id", src)


if __name__ == "__main__":
    unittest.main()
