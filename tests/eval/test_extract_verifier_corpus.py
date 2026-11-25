"""추출 검증기 — 4작품 20회차 실코퍼스 시험 (병합 제안서 §6-1·§6-6·§7). LLM·네트워크 호출 없음.

코퍼스는 judge_eval.WORKS(tests/eval/weave_judge_corpus · weave_checker_corpus)의 확정 원장 5화와 레지스트리.
회차 원문은 코퍼스에 없다 → 원문 없이(text=None) 돌린다. number_lost 는 원문이 없으면 인용 자체를 보므로
표적(s35 꺼짐·s28 켜짐)이 그대로 서고, entity_uncovered 는 원문이 없으면 내지 않는다(원문 기준 측정은 별도 보고).
코퍼스 폴더가 없으면 skip.

  §6-1 exclude 불변   검증기 병합 전후 extract_gate.filter_episode 결과·grade·tentative_event_ids 가 같다(20회차)
  §6-6 신호 표적      T1 표적 5건(Q1 s21 ×2, Q3 s28, 선언 OBJ_012·OBJ_015) + number_lost Q1 s35 꺼짐 / Q3 s28 '하나' 켜짐
  §7   결정성         같은 입력 두 번 → to_json 바이트 동일, 병합 등급 파일 sort_keys JSON 동일, 입력 불변
"""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from app.pipeline import extract_gate as G
from app.pipeline.extract_verifier.merge import merge_verifier
from app.pipeline.extract_verifier.report import verify_episode

from ledger import normalize  # noqa: E402  (extract_gate 가 weave_core 경로를 연다)

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
WORKS = ("q1", "q3", "sherlock", "wm")

if not all((JE.WORKS[w].ledger_dir / "episode_05.json").exists() for w in WORKS):
    pytest.skip("판정 코퍼스(tests/eval/weave_judge_corpus·weave_checker_corpus)가 없다", allow_module_level=True)

_CTX: dict = {}


def ctx(work: str):
    if work not in _CTX:
        spec = JE.WORKS[work]
        reg = json.loads(spec.registry.read_text(encoding="utf-8"))
        mp = {e["new_label"]: e["id"] for c in ("characters", "locations", "objects", "times", "worldview_rules")
              for e in reg.get(c, []) or [] if e.get("new_label")}
        raw = [json.loads((spec.ledger_dir / f"episode_{i:02d}.json").read_text(encoding="utf-8")) for i in range(1, 6)]
        _CTX[work] = (reg, mp, raw, [normalize(copy.deepcopy(d), mp) for d in raw])
    return _CTX[work]


def run(work: str, ep: int):
    reg, mp, raw, eps = ctx(work)
    return verify_episode(raw[ep - 1], eps[:ep - 1], mp, registry=reg, text=None)


CASES = [(w, ep) for w in WORKS for ep in range(1, 6)]


# ── §6-1 exclude 불변 · §6-2 등급 불변 · §7 결정성·입력 불변 ─────────────
@pytest.mark.parametrize("work,ep", CASES, ids=[f"{w}-{e}" for w, e in CASES])
def test_exclude_and_grades_unchanged_and_deterministic(work, ep):
    reg, mp, raw, eps = ctx(work)
    doc = raw[ep - 1]
    doc0, conf0 = copy.deepcopy(doc), copy.deepcopy(eps[:ep - 1])
    gate = G.grade_episode(doc, eps[:ep - 1], mp, knows_mode="accumulate").to_dict()
    gate0 = copy.deepcopy(gate)
    v1 = verify_episode(doc, eps[:ep - 1], mp, registry=reg, text=None)
    v2 = verify_episode(doc, eps[:ep - 1], mp, registry=reg, text=None)
    assert v1.to_json().encode("utf-8") == v2.to_json().encode("utf-8")       # 바이트 동일
    merged = merge_verifier(gate, v1)
    assert gate == gate0 and doc == doc0 and eps[:ep - 1] == conf0            # 입력 불변
    assert merged["tentative_event_ids"] == gate["tentative_event_ids"]
    assert merged["summary"] == gate["summary"]
    for eid, e in gate["events"].items():
        m = merged["events"][eid]
        assert m["grade"] == e["grade"] and m["own_reasons"] == e["own_reasons"]
        assert [(s["grade"], s["reasons"]) for s in m["state_changes"]] == \
            [(s["grade"], s["reasons"]) for s in e["state_changes"]]
    assert G.filter_episode(doc, merged) == G.filter_episode(doc, gate)       # exclude 결과 동일
    dump = lambda g: json.dumps(g, ensure_ascii=False, sort_keys=True)  # noqa: E731
    assert dump(merged) == dump(merge_verifier(gate, v2))


def test_corpus_totals_recorded():
    """20회차 합계 — 숫자가 바뀌면 검증기 동작이 바뀐 것이다(원문 없이 잰 값)."""
    tot = {"events": 0, "promotion": 0, "high": 0}
    for w, ep in CASES:
        d = run(w, ep).to_dict()
        tot["events"] += d["summary"]["events"]
        tot["promotion"] += len(d["promotion_candidates"])
        tot["high"] += sum(1 for c in d["promotion_candidates"] if c["priority"] == "high")
    assert tot["events"] > 0 and 0 < tot["high"] < tot["promotion"]


# ── §6-6 신호 표적 ───────────────────────────────────────────────────
def _marks(res) -> list[dict]:
    out = [m for e in res.events.values() for m in e["own_marks"]]
    out += [m for e in res.events.values() for s in e["state_changes"] for m in s["marks"]]
    return out + res.episode_marks


def _has(res, signal, event_id=None, collection=None, index=None, decl=None) -> bool:
    for m in _marks(res):
        if m["signal"] != signal:
            continue
        if decl is not None:
            if (m.get("declaration") or {}).get("id") == decl:
                return True
            continue
        if m.get("event_id") == event_id and m.get("collection") == collection and m.get("index") == index:
            return True
    return False


def test_target_q1_s21_fare_600():
    assert _has(run("q1", 1), "assumed_only_persistent", "EVENT_1_4", "assumed_states", 1)
    assert _has(run("q1", 2), "assumed_only_persistent", "EVENT_2_2", "assumed_states", 1)


def test_target_q3_s28_ups_45():
    assert _has(run("q3", 1), "assumed_only_persistent", "EVENT_1_9", "assumed_states", 3)


def test_target_declarations_q3():
    r = run("q3", 1)
    assert _has(r, "declaration_only_persistent", decl="OBJ_012")   # s42 종이 일지
    assert _has(r, "declaration_only_persistent", decl="OBJ_015")   # s28 45분 지속


def test_target_number_lost_s35_off_s28_on():
    assert not _has(run("q1", 1), "number_lost", "EVENT_1_3", "knowledge_gained", 1)
    r4 = run("q3", 4)
    hit = [m for m in _marks(r4) if m["signal"] == "number_lost" and m.get("event_id") == "EVENT_4_7"
           and m.get("collection") == "knowledge_gained" and m.get("index") == 1]
    assert hit and "하나" in hit[0]["numbers"]


def test_no_entity_uncovered_without_text():
    """원문이 없으면 entity_uncovered 는 내지 않는다(과다 표시 없음)."""
    assert all(not any(m["signal"] == "entity_uncovered" for m in run(w, ep).episode_marks) for w, ep in CASES)
