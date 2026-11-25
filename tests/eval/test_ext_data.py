"""외부 데이터 변환(scripts/eval/ext_data.py) — 받은 파일 없이 작은 고정 사례로."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EVAL = ROOT / "scripts" / "eval"


def _load(name: str, path: Path):  # noqa: ANN202
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


X = _load("ext_data", EVAL / "ext_data.py")
TL = _load("train_lib", EVAL / "train_lib.py")


def _kor(labels):  # noqa: ANN001, ANN202
    return [{"premise": f"전제{i}", "hypothesis": f"가설{i}", "label": lab} for i, lab in enumerate(labels)]


def _mnli(labels, genres):  # noqa: ANN001, ANN202
    return [{"label": lab, "genre": g, "promptID": i // 3} for i, (lab, g) in enumerate(zip(labels, genres))]


def test_kornli_label_column_check_stops_on_any_mismatch():
    labels = [0, 1, 2, 0, 1, 2]
    with pytest.raises(SystemExit, match="라벨 열 불일치 1행"):
        X.kornli_rows(_kor(labels), _mnli([0, 1, 2, 0, 1, 1], ["fiction"] * 6))
    with pytest.raises(SystemExit, match="행 수"):
        X.kornli_rows(_kor(labels), _mnli(labels[:5], ["fiction"] * 5))


def test_kornli_fiction_only_and_label_map():
    labels = [0, 1, 2, 0, 1, 2]
    rows, rep = X.kornli_rows(_kor(labels), _mnli(labels, ["fiction"] * 3 + ["travel"] * 3))
    assert [r["label"] for r in rows] == ["정상", "무관", "모순"]
    assert {r["work"] for r in rows} == {"ext_kornli"} and all(r["source"] == "ext" for r in rows)
    assert {r["group_id"] for r in rows} == {"ext_kornli:0"} and rep["rows_genre"] == 3 and rep["label_mismatch"] == 0
    TL.check_labels(rows)   # train_lib 가 받아들이는 라벨


def test_kornli_max_rows_by_group_deterministic():
    labels = [0, 1, 2] * 10
    a, _ = X.kornli_rows(_kor(labels), _mnli(labels, ["fiction"] * 30), max_rows=9, seed=3)
    b, _ = X.kornli_rows(_kor(labels), _mnli(labels, ["fiction"] * 30), max_rows=9, seed=3)
    assert a == b and len(a) == 9 and len({r["group_id"] for r in a}) == 3


def test_li_items_and_strip_html():
    assert X.li_items('<li>"First line."</li><li>Second <i>one</i></li>') == ["First line.", "Second one"]
    assert X.split_sentences("He came.</p><p>She left. Then rain fell!") == ["He came.", "She left.", "Then rain fell!"]


STORY = ("The old king lived alone in a tall tower by the sea. He had no children at all. "
         "Every morning he walked along the shore with his dog. The village baker brought him bread on Sundays. "
         "One winter a storm broke the tower windows.")


def test_story_pairs_contra_and_two_unrelated_per_contra():
    contra = [("He had no children at all.", "His three sons came to visit him every spring.")]
    rows = X.story_pairs("s1", STORY, contra, src="ff", seed=7)
    assert [r["label"] for r in rows] == ["모순", "무관", "무관"]
    c, u1, u2 = rows
    assert (c["premise_en"], c["hypothesis_en"]) == contra[0]
    assert u1["hypothesis_en"] == c["hypothesis_en"] and u1["premise_en"] != c["premise_en"]   # 가설 고정
    assert u2["premise_en"] == c["premise_en"] and u2["hypothesis_en"] != c["hypothesis_en"]   # 전제 고정
    assert "no children" not in u1["premise_en"] and "no children" not in u2["hypothesis_en"]   # 충돌 문장은 무관 후보에서 뺀다
    assert X.story_pairs("s1", STORY, contra, src="ff", seed=7) == rows


def test_story_pairs_skips_identical_pair():
    assert X.story_pairs("s1", STORY, [("He had no children.", "He had no children!")], src="cd", seed=7) == []


def test_ffcd_en_flawed_fictions_and_contradoc():
    ff = [{"cont_error": 1.0, "example_id": "ff_1", "story": STORY,
           "cont_error_lines": '<li>"His three sons came to visit him every spring."</li>',
           "contradicted_lines": '<li>"He had no children at all."</li>'},
          {"cont_error": 0.0, "example_id": "ff_2", "story": STORY, "cont_error_lines": "", "contradicted_lines": ""}]
    cd = {"pos": {"u1": {"doc_type": "story", "text": STORY, "evidence": "The tower stood far inland.",
                         "ref sentences": ["The old king lived alone in a tall tower by the sea."]},
                  "u2": {"doc_type": "news", "text": STORY, "evidence": "x", "ref sentences": "y"},
                  "u3": {"doc_type": "story", "text": STORY, "evidence": "No ref."}}}
    rows, rep = X.ffcd_en([ff], cd, seed=7)
    assert rep["ff_stories"] == 1 and rep["cd_stories"] == 1 and rep["labels"] == {"모순": 2, "무관": 4}
    assert {r["src"] for r in rows} == {"flawed_fictions", "contradoc"}


def test_ffcd_build_drops_untranslated_and_marks_rows():
    en = [{"premise_en": "A.", "hypothesis_en": "B.", "label": "모순", "story": "s", "src": "ff", "pair": 0},
          {"premise_en": "A.", "hypothesis_en": "C.", "label": "무관", "story": "s", "src": "ff", "pair": 0}]
    rows, rep = X.ffcd_build(en, {"A.": "가.", "B.": "나.", "C.": ""})
    assert rep["rows"] == 1 and rep["dropped_untranslated"] == 1
    assert rows[0]["premise"] == "가." and rows[0]["work"] == "ext_ffcd" and rows[0]["group_id"] == "ext_ffcd:s"


def test_clean_translation_and_leak_check():
    assert X.clean_translation('<think>x</think> "번역문."') == "번역문."
    rows = [{"premise": "가나다라마바사아자차카타파하가나다라마바사아", "hypothesis": "다른 문장"}]
    assert X.leak_check(rows, ["가나다라마바사아자차카타파하가나다라마바사아"])["rows_with_hit"] == 1
    assert X.leak_check(rows, ["전혀 다른 원문입니다 아주 길게 써서 스무 자를 넘깁니다"])["rows_with_hit"] == 0
