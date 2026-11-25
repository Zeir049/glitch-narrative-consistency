"""추출 검증기(app/pipeline/extract_verifier) 병합 시험 — 서비스 경로(_extract_sync → _grade → 등급 파일) 와 검수 gate 절.

병합 제안서 §6 중 코퍼스 없이 도는 항목: (2) 등급 불변 · (3) annotate/off 키 유무 · (4) 실패 격리 · (5) LLM 0회 ·
(7) 화면 묶음 · (8) 서비스 레지스트리(추출 병합 reg)로 entity_uncovered. 코퍼스 항목은 tests/eval/test_extract_verifier_corpus.py.
LLM 은 FakeLLM(추출 응답 고정)만 쓴다.
"""
from __future__ import annotations

import copy
import json
import sys

import pytest

from app.core.config import settings
from app.pipeline import extract_gate as G
from app.pipeline import weave as W
from app.pipeline.extract_verifier import merge as VM
from app.pipeline.extract_verifier import report as VR
from tests.test_extract_gate import GCH1, GCH2, GDOC1, GDOC2
from tests.test_weave_pipeline import FakeLLM, _snapshot

# 1화: 요금(high)·책장 수(mid)·남은 시간(low) 전제 + 사건에 안 묶인 인물 '박노인'(원문에 이름이 나온다)
VCH1 = ("지하 서고 입장 요금은 600원이었다. 서고에는 책장이 일곱 개 있었다. 남은 시간은 10분이었다.\n\n"
        "박노인이 문간에서 졸고 있었다. 한서진은 책을 펼쳤다.")
VDOC1 = {
    "schema_version": 2, "worldview_rules": [],
    "characters": [{"character_id": "NEW:한서진", "name": "한서진", "entity_type": "person"},
                   {"character_id": "NEW:박노인", "name": "박노인", "entity_type": "person"}],
    "locations": [{"location_id": "NEW:지하_서고", "name": "지하 서고"}],
    "objects": [], "times": [],
    "events": [{
        "event_id": "EVENT_1_1", "order": 1, "scene_id": "S1", "title": "서고",
        "summary": "한서진이 서고에서 책을 펼친다.", "location_id": "NEW:지하_서고", "time_id": None,
        "timeline": "main", "reality_status": "actual", "participant_character_ids": ["NEW:한서진"],
        "witness_character_ids": [], "main_actor_character_id": "NEW:한서진", "objects": [],
        "state_changes": [],
        "assumed_states": [
            {"entity": "NEW:지하_서고", "field": "condition", "assumes": "입장 요금 600원",
             "source_text": "지하 서고 입장 요금은 600원이었다."},
            {"entity": "NEW:지하_서고", "field": "condition", "assumes": "책장 일곱 개",
             "source_text": "서고에는 책장이 일곱 개 있었다."},
            {"entity": "NEW:한서진", "field": "condition", "assumes": "남은 시간 10분",
             "source_text": "남은 시간은 10분이었다."},
        ],
        "knowledge_gained": [], "cause": "unknown", "result": "unknown",
        "source_text": "한서진은 책을 펼쳤다.", "certainty": "explicit"}],
}
VERIFIER_KEYS = ("verifier", "episode_marks", "promotion_candidates", "experimental_candidates")


@pytest.fixture
def vweave(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-real")
    monkeypatch.setattr(settings, "weave_llm_provider", "openrouter")
    monkeypatch.setattr(settings, "weave_judge_k", 1)
    monkeypatch.setattr(settings, "weave_judge_majority", 1)
    monkeypatch.setattr(settings, "weave_gate_mode", "annotate")
    monkeypatch.setattr(settings, "weave_verifier", "annotate")
    fake = FakeLLM({1: VDOC1, 2: GDOC2})
    monkeypatch.setattr(W.w_llm, "complete", fake)
    return W.WeavePipeline(data_dir=str(tmp_path)), fake, tmp_path


def _gate(root, pid: str, ep: int = 1) -> dict:
    files = sorted((root / pid / "gates").glob(f"gate_{ep:02d}_*.json"))
    assert len(files) == 1
    return json.loads(files[0].read_text(encoding="utf-8"))


def _strip(g: dict) -> dict:
    """검증기 키·표시·시각·판본을 뺀 관문 등급 파일."""
    g = copy.deepcopy(g)
    for k in VERIFIER_KEYS + ("graded_at", "rev"):
        g.pop(k, None)
    for e in g.get("events", {}).values():
        e.pop("own_marks", None)
        for s in e.get("state_changes") or []:
            s.pop("marks", None)
    return g


# ── (3) annotate / off 키 유무 ─────────────────────────────────────────
@pytest.mark.parametrize("verifier,gate_mode,expect", [
    ("annotate", "annotate", True), ("annotate", "exclude", True),
    ("off", "annotate", False), ("annotate", "off", False)])
async def test_verifier_keys_follow_settings(vweave, monkeypatch, verifier, gate_mode, expect):
    pipe, _, root = vweave
    monkeypatch.setattr(settings, "weave_verifier", verifier)
    monkeypatch.setattr(settings, "weave_gate_mode", gate_mode)
    await pipe.extract_chapter(VCH1, _snapshot("p1", 0, None), chapter_no=1)
    g = _gate(root, "p1")
    assert all((k in g) is expect for k in VERIFIER_KEYS), sorted(g)
    if expect:
        assert g["verifier"]["style"] == "marks" and g["verifier"]["version"] == 1


def test_default_setting_is_off():
    from app.core.config import Settings
    assert Settings.model_fields["weave_verifier"].default == "off"


# ── (2) 등급·잠정 집합·attrs.trust 불변 ───────────────────────────────
@pytest.mark.parametrize("doc,text", [(VDOC1, VCH1), (GDOC1, GCH1)], ids=["vdoc1", "gdoc1"])
async def test_grades_and_trust_unchanged(vweave, monkeypatch, doc, text):
    pipe, fake, root = vweave
    fake.docs[1] = doc
    monkeypatch.setattr(settings, "weave_verifier", "off")
    off = await pipe.extract_chapter(text, _snapshot("p1", 0, None), chapter_no=1)
    monkeypatch.setattr(settings, "weave_verifier", "annotate")
    on = await pipe.extract_chapter(text, _snapshot("p2", 0, None), chapter_no=1)
    g_off, g_on = _gate(root, "p1"), _gate(root, "p2")
    assert "verifier" in g_on and "verifier" not in g_off
    assert _strip(g_on) == _strip(g_off)                      # grade·own_reasons·reasons·summary·tentative 전부
    assert g_on["tentative_event_ids"] == g_off["tentative_event_ids"]
    trust = lambda o: {e.entity_key: e.attrs.get("trust") for e in o.canon_delta.events}  # noqa: E731
    assert trust(on) == trust(off)
    # 캐논 델타 전체가 같다(weave_rev 는 문서 해시라 같은 입력이면 시각 접두만 다를 수 있다)
    dump = lambda o: [e.model_dump(exclude={"attrs"}) for e in o.canon_delta.events]  # noqa: E731
    assert dump(on) == dump(off)
    # exclude 원장 거르기(filter_event)도 같다
    for ev in doc["events"]:
        evn = {"event_id": ev["event_id"], **{k: v for k, v in ev.items() if k != "event_id"}}
        assert G.filter_event(copy.deepcopy(evn), g_on) == G.filter_event(copy.deepcopy(evn), g_off)


# ── (4) 실패 격리 ─────────────────────────────────────────────────────
async def test_verifier_failure_keeps_gate(vweave, monkeypatch, caplog):
    pipe, _, root = vweave
    monkeypatch.setattr(settings, "weave_verifier", "off")
    base = await pipe.extract_chapter(VCH1, _snapshot("p1", 0, None), chapter_no=1)

    def boom(*a, **kw):
        raise RuntimeError("verifier exploded")

    monkeypatch.setattr(settings, "weave_verifier", "annotate")
    monkeypatch.setattr(W.w_verifier, "verify_episode", boom)
    with caplog.at_level("ERROR"):
        out = await pipe.extract_chapter(VCH1, _snapshot("p2", 0, None), chapter_no=1)
    assert "weave verifier failed" in caplog.text
    g, g0 = _gate(root, "p2"), _gate(root, "p1")
    assert "verifier" not in g
    for x in (g, g0):
        x.pop("graded_at"), x.pop("rev")
    assert g == g0
    assert [e.attrs.get("trust") for e in out.canon_delta.events] == \
        [e.attrs.get("trust") for e in base.canon_delta.events]


# ── (5) LLM 0회 ───────────────────────────────────────────────────────
async def test_verifier_makes_no_llm_call(vweave, monkeypatch):
    pipe, _, root = vweave
    real = W.w_verifier.verify_episode
    core_llm = sys.modules["weave_llm"]
    seen = {"ran": 0}

    def no_llm(*a, **kw):
        raise AssertionError("검증기 구간에서 LLM 호출")

    def guarded(*a, **kw):
        saved = (W.w_llm.complete, core_llm.complete)
        W.w_llm.complete = core_llm.complete = no_llm
        try:
            seen["ran"] += 1
            return real(*a, **kw)
        finally:
            W.w_llm.complete, core_llm.complete = saved

    monkeypatch.setattr(W.w_verifier, "verify_episode", guarded)
    await pipe.extract_chapter(VCH1, _snapshot("p1", 0, None), chapter_no=1)
    assert seen["ran"] == 1
    assert "verifier" in _gate(root, "p1")       # 실패해서 빠진 게 아니라 실제로 돌았다


# ── (8) 서비스 레지스트리(추출 병합 reg)로 entity_uncovered ───────────
async def test_entity_uncovered_uses_service_registry(vweave):
    pipe, _, root = vweave
    await pipe.extract_chapter(VCH1, _snapshot("p1", 0, None), chapter_no=1)
    reg = json.loads((root / "p1" / "registry.json").read_text(encoding="utf-8"))
    park = next(c["id"] for c in reg["characters"] if c["name"] == "박노인")
    g = _gate(root, "p1")
    unc = [m for m in g["episode_marks"] if m["signal"] == "entity_uncovered"]
    assert [m["entity"] for m in unc] == [park] and unc[0]["names"] == ["박노인"]
    assert unc[0]["effect"] == "mark"


# ── 신호·순위가 서비스 경로에서 그대로 나온다 ─────────────────────────
async def test_promotion_candidates_and_priority(vweave):
    pipe, _, root = vweave
    await pipe.extract_chapter(VCH1, _snapshot("p1", 0, None), chapter_no=1)
    g = _gate(root, "p1")
    pr = {c["value"]: c["priority"] for c in g["promotion_candidates"]}
    assert pr == {"입장 요금 600원": "high", "책장 일곱 개": "mid", "남은 시간 10분": "low"}
    marks = g["events"]["EVENT_1_1"]["own_marks"]
    assert {m["signal"] for m in marks} >= {"assumed_only_persistent"}
    assert all(m["effect"] == "mark" for m in marks)
    assert g["events"]["EVENT_1_1"]["own_reasons"] == [] and g["events"]["EVENT_1_1"]["grade"] == G.CONFIRMED


# ── (7) 화면 묶음: gate 절에 high 목록 · mid 건수 · low 없음 ─────────
def test_screen_bundle_buckets():
    cands = [{"value": "v_high", "priority": "high"}, {"value": "v_mid1", "priority": "mid"},
             {"value": "v_mid2", "priority": "mid"}, {"value": "v_low", "priority": "low"}]
    b = VM.screen_bundle({"verifier": {"version": 1}, "promotion_candidates": cands})
    assert b == {"version": 1, "promotion_candidates_high": [cands[0]], "promotion_candidates_mid_count": 2}
    assert "v_low" not in json.dumps(b) and "v_mid1" not in json.dumps(b)
    assert VM.screen_bundle(None) is None
    assert VM.screen_bundle({"tentative_event_ids": []}) is None     # 검증기를 안 돌린 등급 파일


@pytest.mark.parametrize("verifier", ["annotate", "off"])
async def test_review_gate_section_bundle(vweave, monkeypatch, verifier):
    pipe, fake, root = vweave
    monkeypatch.setattr(settings, "weave_verifier", verifier)
    fake.docs[2] = copy.deepcopy(VDOC1) | {"events": [
        copy.deepcopy(VDOC1["events"][0]) | {"event_id": "EVENT_2_1"}]}
    out1 = await pipe.extract_chapter(VCH1, _snapshot("p1", 0, None), chapter_no=1)
    out2 = await pipe.extract_chapter(VCH1, _snapshot("p1", 1, out1.canon_delta), chapter_no=2)
    await pipe.review_chapter(VCH1, _snapshot("p1", 1, out1.canon_delta), out2.canon_delta, chapter_no=2)
    review = json.loads((root / "p1" / "reviews" / "review_02.json").read_text(encoding="utf-8"))
    sec = review["gate"]
    if verifier == "off":
        assert "verifier" not in sec
        return
    v = sec["verifier"]
    assert [c["value"] for c in v["promotion_candidates_high"]] == ["입장 요금 600원"]
    assert v["promotion_candidates_mid_count"] == 1
    assert "남은 시간 10분" not in json.dumps(v, ensure_ascii=False)


# ── §7 결정성: 같은 입력 두 번 → 등급 파일 바이트 동일(graded_at·rev 제외), 입력 불변 ──
async def test_gate_file_deterministic(vweave):
    pipe, _, root = vweave
    await pipe.extract_chapter(VCH1, _snapshot("p1", 0, None), chapter_no=1)
    await pipe.extract_chapter(VCH1, _snapshot("p2", 0, None), chapter_no=1)
    a, b = _gate(root, "p1"), _gate(root, "p2")
    for g in (a, b):
        g.pop("graded_at"), g.pop("rev")
    dump = lambda g: json.dumps(g, ensure_ascii=False, sort_keys=True).encode("utf-8")  # noqa: E731
    assert dump(a) == dump(b)


def test_merge_does_not_mutate_inputs():
    doc = copy.deepcopy(VDOC1)
    gate = G.grade_episode(doc, [], {}, knows_mode="accumulate").to_dict()
    gate0, doc0 = copy.deepcopy(gate), copy.deepcopy(doc)
    v = VR.verify_episode(doc, [], {}, registry={}, text=VCH1)
    merged = VM.merge_verifier(gate, v)
    assert gate == gate0 and doc == doc0
    assert merged is not gate and merged["events"] is not gate["events"]
