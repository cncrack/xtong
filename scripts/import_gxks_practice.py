"""离线导入已采集的练习；只累加入库，不重新开卷，不导出 Excel。"""

from __future__ import annotations

import argparse
import hashlib
import json
from contextlib import closing
from pathlib import Path

from xxt.adapters.gxks.practice import parse_capture
from xxt.store.gxks_questions import bank_counts, import_bundle
from xxt.store.schema_gxks import open_question_bank


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-dir", required=True, type=Path, help="已有采集文件目录")
    parser.add_argument("--db", required=True, type=Path, help="新站专用题库路径，不使用账号业务库")
    parser.add_argument("--account-label", required=True, help="账号代号，不填姓名或学号")
    parser.add_argument("--dry-run", action="store_true", help="仅解析校验，不创建或修改数据库")
    args = parser.parse_args()
    names = {"questions_doc": "exam_questions.json", "stage1": "practice_stage1.json",
             "stage2": "practice_stage2.json", "review_meta": "view_exam.json",
             "review_html": "exam_html/viewExam.html"}
    try:
        source = {k: (args.capture_dir / v).read_bytes() for k, v in names.items()}
        parsed = {k: json.loads(v.decode("utf-8")) if k != "review_html" else v.decode("utf-8")
                  for k, v in source.items()}
        bundle = parse_capture(account_label=args.account_label, **parsed)
        # 只存来源文件名及指纹，不把整页姓名/账号/ViewState 等复制到正式题库。
        bundle["source_files"] = {names[k]: hashlib.sha256(v).hexdigest() for k, v in source.items()}
        summary = {"questions": len(bundle["questions"]), "got_score": bundle["got_score"],
                   "standards": [q["answer"]["standard"] for q in bundle["questions"]]}
        if args.dry_run:
            print(json.dumps({"dry_run": True, **summary}, ensure_ascii=True))
            return 0
        with closing(open_question_bank(args.db)) as conn:
            result = import_bundle(conn, bundle)
            counts = bank_counts(conn)
        print(json.dumps({**result, **summary, "counts": counts}, ensure_ascii=True))
    except (ValueError, KeyError, OSError) as exc:
        parser.exit(2, f"导入未完成：{exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
