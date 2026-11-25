# -*- coding: utf-8 -*-
"""
Weave 체커 dev set 회귀 게이트.

BASELINE 은 event_sourcing 8d89349 의 체커를 같은 커밋의 30장면 코퍼스에 돌린 결과를
장면 ID **집합**으로 고정한 것이다. 정확도가 아니라 회귀 게이트 — 숫자가 바뀌면 의도한
변화인지 확인하고 BASELINE 과 docs/EVAL.md 를 함께 갱신한다.
LLM·네트워크·DB 호출 없음.
"""
from __future__ import annotations

from tests.eval.checker_eval import CORPUS_DIR, run

BASELINE = {
    "hard": {"scene_04", "scene_12", "scene_14"},
    "soft": {"scene_20"},
    "false_positives": set(),
    "checker_missed": {"scene_10"},
}

HINT = ("숫자가 바뀌면 의도한 변화인지 확인하고 BASELINE과 docs/EVAL.md를 함께 갱신"
        " (python -m tests.eval.checker_eval 로 현재 표를 본다)")


def _sets(result: dict) -> dict[str, set[str]]:
    return {
        "hard": set(result["hard_scenes"]),
        "soft": set(result["soft_scenes"]),
        "false_positives": set(result["false_positive_scenes"]),
        "checker_missed": set(result["checker_missed"]),
    }


def test_corpus_shape() -> None:
    result = run(CORPUS_DIR)
    assert result["counts"]["scenes"] == 30
    assert result["counts"]["normal"] == 10
    assert result["counts"]["contradiction"] == 20
    assert set(result["checker_labeled"]) == {"scene_04", "scene_10", "scene_12", "scene_20"}


def test_checker_gate_matches_baseline() -> None:
    got = _sets(run(CORPUS_DIR))
    for key, expected in BASELINE.items():
        assert got[key] == expected, (
            f"{key}: 기준선 {sorted(expected)} ≠ 현재 {sorted(got[key])} — {HINT}"
        )


def test_ledger_replay_is_deterministic() -> None:
    """원장 재생은 순수 함수 — 두 번 돌려도 장면별 판정이 같아야 한다."""
    a = run(CORPUS_DIR)
    b = run(CORPUS_DIR)
    assert _sets(a) == _sets(b)
    assert {s: (v["hard"], v["soft"]) for s, v in a["scenes"].items()} == \
           {s: (v["hard"], v["soft"]) for s, v in b["scenes"].items()}
