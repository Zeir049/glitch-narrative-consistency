"""판정 패킷 (b)절 세계관 규칙 조립 회귀 — judge_eval.load_context · datagen_common._rules_before.

2026-11-07 이전 하네스는 확정 원장 5화의 worldview_rules 를 rule_id 가 처음 나온 것만 남겼다. nemotron 원장은
회차마다 RULE_007·RULE_008 같은 ID 를 다른 규칙에 재사용해서 뒤 회차 규칙 4개가 패킷에서 빠졌다
(Q1 3화 1개, Q3 4·5화 3개). 제품 경로 app/pipeline/weave.py judge_registry 는 본문 정규화(norm_text) 기준이라
해당 없었다. 지금은 하네스도 같은 기준이고, ID 가 겹치면 `<rule_id>@ep<화>` 로 구분한다.
LLM·네트워크 호출 없음.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EVAL = ROOT / "scripts" / "eval"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    if str(EVAL) not in sys.path:
        sys.path.insert(0, str(EVAL))
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


JE = _load("judge_eval", EVAL / "judge_eval.py")
C = _load("datagen_common", EVAL / "datagen_common.py")

# 작품별 (b)절 규칙 수 — (수정 전 rule_id 기준, 수정 후 본문 기준). wm 은 레지스트리 규칙(12)을 그대로 쓴다.
COUNTS = {"wm": (12, 12), "q1": (3, 4), "q3": (38, 41), "sherlock": (9, 9)}

# 수정 전 유실된 규칙: (작품, 새 ID, 화, 본문 앞부분)
RECOVERED = [
    ("q1", "NEW:열쇠_관리_규칙@ep3", 3, "이 집 열쇠는 안에서 자는 사람 것만 있다"),
    ("q3", "RULE_007@ep4", 4, "기지장은 설비 담당의 격벽 관리 권한을 임시 변경할 수 있다"),
    ("q3", "RULE_007@ep5", 5, "규정 4조에 따른 안전 보고는 기지장 승인 없이 보낼 수 있다"),
    ("q3", "RULE_008@ep5", 5, "본부 청문 결과 전까지 현 운영 체제 유지"),
]


@pytest.fixture(scope="module")
def contexts():
    return {w: JE.load_context(w) for w in COUNTS}


@pytest.mark.parametrize("work", list(COUNTS))
def test_rule_counts(contexts, work):
    old, new = COUNTS[work]
    rules = contexts[work].registry["worldview_rules"]
    assert len(rules) == new, f"{work}: {len(rules)}"
    assert sum(COUNTS[w][1] - COUNTS[w][0] for w in COUNTS) == 4


@pytest.mark.parametrize("work,rid,ep,head", RECOVERED)
def test_recovered_rules_present(contexts, work, rid, ep, head):
    by_id = {r["id"]: r for r in contexts[work].registry["worldview_rules"]}
    assert rid in by_id
    assert by_id[rid]["content"].startswith(head)
    assert by_id[rid]["first_episode"] == ep


@pytest.mark.parametrize("work", list(COUNTS))
def test_ids_unique_and_first_occurrence_keeps_plain_id(contexts, work):
    rules = contexts[work].registry["worldview_rules"]
    ids = [r["id"] for r in rules]
    assert len(ids) == len(set(ids))
    if work == "q3":
        by_id = {r["id"]: r for r in rules}
        assert by_id["RULE_007"]["first_episode"] == 3
        assert by_id["RULE_008"]["first_episode"] == 4


def test_dedup_by_normalized_text_not_id():
    eps = [
        (1, {"worldview_rules": [{"rule_id": "RULE_001", "content": "규정 7조: 통신은 22시"},
                                 {"rule_id": "RULE_002", "content": "규정_7조:  통신은 22시 (원문)"}]}),
        (2, {"worldview_rules": [{"rule_id": "RULE_001", "content": "다른 규칙"},
                                 {"rule_id": "RULE_001", "content": "또 다른 규칙"}]}),
    ]
    rules = JE.collect_episode_rules(eps)
    assert [(r["id"], r["first_episode"]) for r in rules] == [
        ("RULE_001", 1), ("RULE_001@ep2", 2), ("RULE_001@ep2#2", 2)]


def test_rules_before_matches_load_context_prefix(contexts):
    """학습 데이터(datagen_common._rules_before)와 평가(load_context)가 같은 규칙에 같은 ID 를 준다."""
    eps, reg, _mapping = C.load_episodes("q3")
    full = contexts["q3"].registry["worldview_rules"]
    for k in range(2, 7):
        before = C._rules_before(reg, eps, k)
        assert before == [r for r in full if r["first_episode"] < k]
    assert {r["id"] for r in C._rules_before(reg, eps, 5)} >= {"RULE_007@ep4"}
    assert "RULE_008@ep5" not in {r["id"] for r in C._rules_before(reg, eps, 5)}
