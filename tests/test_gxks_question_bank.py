"""合成题目验证累加、版本、来源、平台隔离；不访问真实页面。"""

from contextlib import closing
from copy import deepcopy
import json
import sqlite3

import pytest

from xxt.store.db import connect
from xxt.store.schema_gxks import GXKS_SCHEMA_VERSION, migrate, open_question_bank
from xxt.store.gxks_questions import (backfill_classification, bank_counts, bank_items,
                                     import_bundle, read_attempt)


@pytest.fixture
def conn(tmp_path):
    with closing(open_question_bank(tmp_path / "bank.db")) as db:
        yield db


@pytest.fixture
def bundle():
    return {
        "account_label": "jia", "exam_start_id": "attempt-1", "exercise_id": "exercise-1",
        "section_name": "示例节", "type_map": {"2": "单选题"},
        "questions": [{"ordinal": 1, "qid": "q-1", "type_code": "2", "stem": "示例题干",
                       "stem_html": "<p>示例题干</p>", "max_score": 2.5,
                       "parts": [{"kind": "option", "key": "a", "text": "甲选项"},
                                 {"kind": "option", "key": "b", "text": "乙选项"}],
                       "answer": {"picked": ["a"], "picked_source": "human",
                                  "standard": ["b"], "standard_source": "site", "score_got": 0,
                                  "is_correct": False}}],
    }


def test_repeat_import_is_idempotent(conn, bundle):
    first = import_bundle(conn, bundle)
    again = import_bundle(conn, bundle)
    assert first["inserted"] is True and again["inserted"] is False
    assert first["import_id"] == again["import_id"]
    assert bank_counts(conn) == dict(papers=1, question_versions=1, attempt_questions=1, snapshots=1)


def test_accumulates_attempts_and_sections(conn, bundle):
    import_bundle(conn, bundle)
    second = deepcopy(bundle)
    second.update(exam_start_id="attempt-2", exercise_id="exercise-2", section_name="另一节")
    import_bundle(conn, second)
    assert bank_counts(conn) == dict(papers=2, question_versions=1, attempt_questions=2, snapshots=2)


def test_answer_account_isolation(conn, bundle):
    import_bundle(conn, bundle)
    other = deepcopy(bundle)
    other["account_label"] = "yi"
    other["questions"][0]["answer"]["picked"] = ["b"]
    import_bundle(conn, other)
    for label, expected in (("jia", ["a"]), ("yi", ["b"])):
        got = read_attempt(conn, account_label=label, exam_start_id="attempt-1")
        assert got["questions"][0]["answer"]["picked"] == expected
    assert read_attempt(conn, account_label="unknown", exam_start_id="attempt-1") is None
    assert bank_counts(conn)["question_versions"] == 1
    item = conn.execute("SELECT raw_json FROM gx_q_items").fetchone()[0]
    assert '"standard"' not in item and '"picked"' not in item


@pytest.mark.parametrize("change", ["order", "stem", "type", "media"])
def test_new_question_version_not_overwrite(conn, bundle, change):
    import_bundle(conn, bundle)
    other = deepcopy(bundle)
    other["exam_start_id"] = "attempt-2"
    q = other["questions"][0]
    if change == "order":
        q["parts"].reverse()
    elif change == "stem":
        q["stem"] = "不同题干"
    elif change == "type":
        q["type_code"] = "unknown"
    else:
        q["parts"].append({"kind": "drawing", "key": "image", "paths": [[1, 2], [3, 4]]})
    import_bundle(conn, other)
    assert bank_counts(conn)["question_versions"] == 2


def test_subjective_and_unknown_parts_roundtrip(conn, bundle):
    q = bundle["questions"][0]
    q["type_code"] = "future-type"
    q["parts"] = [{"kind": "sub", "children": [{"kind": "blank", "index": 1}]},
                  {"kind": "audio", "url": "media/example.mp3", "extra": {"locale": "zh"}}]
    q["answer"] = {"picked": {"text": "解答\n第二行", "blanks": ["甲", "乙"]}, "picked_source": "human"}
    import_bundle(conn, bundle)
    assert read_attempt(conn, account_label="jia", exam_start_id="attempt-1") == bundle
    assert conn.execute("SELECT standard_json FROM gx_q_answers").fetchone()[0] is None


def test_new_evidence_preserves_history(conn, bundle):
    import_bundle(conn, bundle)
    newer = deepcopy(bundle)
    newer["questions"][0]["answer"].update(standard=["a"], standard_source="ai")
    import_bundle(conn, newer)
    answers = conn.execute("SELECT standard_json,standard_source FROM gx_q_answers ORDER BY import_id").fetchall()
    assert [(json.loads(r[0]), r[1]) for r in answers] == [(["b"], "site"), (["a"], "ai")]
    assert bank_counts(conn)["attempt_questions"] == 1
    assert bank_counts(conn)["snapshots"] == 2


def test_changed_paper_content_rolls_back_everything(conn, bundle):
    import_bundle(conn, bundle)
    bad = deepcopy(bundle)
    bad["questions"][0]["stem"] = "无法证明是同一份答卷"
    with pytest.raises(ValueError, match="版本"):
        import_bundle(conn, bad)
    assert bank_counts(conn) == dict(papers=1, question_versions=1, attempt_questions=1, snapshots=1)


@pytest.mark.parametrize("error", ["source", "nan", "negative", "overflow", "duplicate", "empty", "account"])
def test_bad_input_leaves_bank_empty(conn, bundle, error):
    if error == "source":
        del bundle["questions"][0]["answer"]["standard_source"]
    elif error == "nan":
        bundle["questions"][0]["max_score"] = float("nan")
    elif error == "negative":
        bundle["questions"][0]["max_score"] = -1
    elif error == "overflow":
        bundle["questions"][0]["answer"]["score_got"] = 3
    elif error == "duplicate":
        bundle["questions"].append(deepcopy(bundle["questions"][0]))
    elif error == "empty":
        bundle["questions"] = []
    else:
        bundle["account_label"] = "甲同学"
    with pytest.raises(ValueError):
        import_bundle(conn, bundle)
    assert bank_counts(conn)["papers"] == 0


def test_migration_separate_version_and_no_chaoxing_change(tmp_path):
    with closing(connect(tmp_path / "old.db")) as old:
        tables = list(old.execute("SELECT name,sql FROM sqlite_master ORDER BY name"))
        version = old.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
    with closing(open_question_bank(tmp_path / "new.db")) as new:
        migrate(new)
        # 不写死版本号：schema 会继续升，断言“用的是新站自己的版本键”即可。
        ver_row = new.execute(
            "SELECT value FROM meta WHERE key='gxks_schema_version'").fetchone()
        assert int(ver_row[0]) == GXKS_SCHEMA_VERSION
        assert new.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone() is None
    with pytest.raises(ValueError, match="业务库"):
        open_question_bank(tmp_path / "old.db")
    with closing(sqlite3.connect(tmp_path / "old.db")) as old:
        assert list(old.execute("SELECT name,sql FROM sqlite_master ORDER BY name")) == [tuple(r) for r in tables]
        assert old.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == version


def test_enrich_classification_without_losing_history(conn, bundle):
    import_bundle(conn, bundle)
    enriched = deepcopy(bundle)
    enriched.update(course_id="course-1", section_id="section-1")
    import_bundle(conn, enriched)
    row = conn.execute("SELECT course_id,section_id FROM gx_q_papers").fetchone()
    assert tuple(row) == ("course-1", "section-1")
    assert bank_counts(conn)["question_versions"] == 1
    # 补分类不是新作答：不能多出快照与重复答案（2026-10-30 修正后的语义）。
    assert bank_counts(conn)["snapshots"] == 1
    changed = deepcopy(enriched)
    changed["section_id"] = "section-wrong"
    with pytest.raises(ValueError, match="归属"):
        import_bundle(conn, changed)
    assert bank_counts(conn)["snapshots"] == 1


def test_backfill_fills_only_empty_classification(conn, bundle):
    import_bundle(conn, bundle)
    assert backfill_classification(conn, account_label="jia", exam_start_id="attempt-1",
                                   course_id="示例课程", section_id="1289",
                                   section_name="别的名字") is True
    row = conn.execute("SELECT course_id,section_id,section_name FROM gx_q_papers").fetchone()
    # 节名已有值 → 不被补写；课程与节 ID 是空的 → 被填上
    assert tuple(row) == ("示例课程", "1289", "示例节")
    assert bank_counts(conn) == dict(papers=1, question_versions=1,
                                     attempt_questions=1, snapshots=1)


def test_bank_items_merges_same_question_across_sections(conn, bundle):
    import_bundle(conn, bundle)
    same = deepcopy(bundle)
    same.update(exam_start_id="attempt-2", exercise_id="exercise-2")
    same["questions"][0]["qid"] = "q-2"          # 同一道题在另一节里的题号不同
    import_bundle(conn, same)
    assert bank_counts(conn)["question_versions"] == 2      # 库里留两条痕迹
    merged = bank_items(conn)
    assert len(merged) == 1                                 # 导出时归并成一道题
    assert merged[0]["occurrences"] == 2
    assert sorted(merged[0]["platform_qids"].split(",")) == ["q-1", "q-2"]


def test_future_schema_rejected(conn):
    with conn:
        conn.execute("UPDATE meta SET value='99' WHERE key='gxks_schema_version'")
    with pytest.raises(ValueError, match="降级"):
        migrate(conn)
    assert conn.execute("SELECT value FROM meta WHERE key='gxks_schema_version'").fetchone()[0] == "99"
