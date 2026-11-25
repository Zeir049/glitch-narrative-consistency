"""titanic_structure.py — 합성 데이터만(타이타닉 원문 없음)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "eval"))

import titanic_structure as T  # noqa: E402


def test_levenshtein():
    assert T.levenshtein("abc", "abc") == 0
    assert T.levenshtein("kitten", "sitting") == 3
    assert T.levenshtein("", "ab") == 2


def test_match_quote_status_and_spacing():
    frags = [("EVENT_1_1", "source_text", T.norm("로즈는 열일곱 살이다")),
             ("EVENT_1_2", "state_changes", T.norm("칼은 로즈에게 반지를 준다")),
             ("EVENT_1_3", "source_text", T.norm("칼은 로즈에게 반지를 준다 그리고 떠난다"))]
    assert T.match_quote("로즈는  열일곱 살이다", frags)["status"] == "matched"      # 공백 차이 무시
    amb = T.match_quote("칼은 로즈에게 반지를 준다", frags)
    assert amb["status"] == "ambiguous" and len(amb["candidates"]) == 2
    assert T.match_quote("전혀 다른 문장이다", frags)["status"] == "unmatched"


def test_build_map_and_review_do_not_choose():
    labels = {"scene_01": {"has_contradiction": True, "type": "상태", "evidence_quotes": [
        {"episode": 1, "quote": "칼은 로즈에게 반지를 준다", "role": "확립 근거"}]},
        "scene_02": {"has_contradiction": False, "evidence_quotes": []}}
    eps = [{"events": [{"event_id": "EVENT_1_1", "source_text": "칼은 로즈에게 반지를 준다"},
                       {"event_id": "EVENT_1_2", "source_text": "칼은 로즈에게 반지를 준다 그리고"}]}]
    emap = T.build_evidence_map(labels, eps)
    assert list(emap["scenes"]) == ["scene_01"] and emap["stats"] == {"ambiguous": 1}
    md = T.review_markdown(emap, labels, eps)
    assert "scene_01" in md and "ambiguous" in md and "후보 EVENT_1_1" in md and "후보 EVENT_1_2" in md


def test_trace_report_detects_shortcut_and_none():
    # P 는 항상 더 길게, N 은 항상 같은 길이 → 길이 차 단일 특징이 완벽(1.0)
    val = ([{"scene_id": f"p{i}", "kind": "P", "original": "가나다라마", "replacement": "가나다라마바사아"} for i in range(6)]
           + [{"scene_id": f"n{i}", "kind": "N", "original": "가나다라마", "replacement": "가나다라바"} for i in range(6)])
    rep = T.trace_report(val)
    assert rep["len_diff_abs"]["auroc_high_is_P"] == 1.0 and rep["any_over_limit"]
    val2 = ([{"scene_id": f"p{i}", "kind": "P", "original": "가나다라마", "replacement": "가나다라바"} for i in range(6)]
            + [{"scene_id": f"n{i}", "kind": "N", "original": "가나다라마", "replacement": "가나다라사"} for i in range(6)])
    rep2 = T.trace_report(val2)
    assert rep2["edit_distance"]["auroc_high_is_P"] == 0.5 and not rep2["any_over_limit"]


def rows_for(sid, positive, labels):
    return [{"scene_id": sid, "scene_positive": positive, "label": lab} for lab in labels]


def test_structure_R_and_oracle():
    rows = []
    rows += rows_for("s1", True, ["모순", "무관", "제외"])      # 짚을 수 있음
    rows += rows_for("s2", True, ["무관", "제외"])             # 제외만 → 낙관에서만
    rows += rows_for("s3", True, ["무관"])                     # 가설도 없음
    for i in range(3):
        rows += rows_for(f"n{i}", False, ["정상", "무관"])
    rep = T.structure_report(rows)
    assert rep["R"] == 1 and rep["able_scenes"] == ["s1"] and rep["R_total_pos"] == 3
    # 오라클: (1*3 + 2*3*0.5)/9 = 6/9
    assert abs(rep["oracle"]["auroc"] - 6 / 9) < 1e-5
    assert rep["optimistic_R_with_excluded"] == 2
    assert rep["pairs_total"] == len(rows)
    assert rep["label_v2"]["모순"] == 1


def test_register_corpus_and_episode_count(tmp_path):
    import judge_eval as JE
    led = tmp_path / "ledger_nemotron_ultra"
    led.mkdir()
    for i in (1, 2, 3, 4):
        (led / f"episode_{i:02d}.json").write_text("{}", encoding="utf-8")
    try:
        spec = JE.register_corpus("titanic_t", tmp_path)
        assert JE.episode_count(spec) == 4                       # 화 수는 폴더에서 센다(Q1·Q3·셜록은 5)
        assert spec.labels == tmp_path / "labels.json" and spec.scene_events == led / "scene_events"
        assert spec.timelines == ("main",) and JE.timelines_of("titanic_t") == ["main"]
    finally:
        JE.WORKS.pop("titanic_t", None)
