"""WeavePipeline(PIPELINE_MODE=weave) 어댑터 테스트 — LLM 은 가짜로 바꿔 끼운다(API 호출 없음).

시나리오: 1화에서 인물(한서진)이 죽고, 2화에서 그 인물이 살아 있다고 전제하는 사건이 나온다.
  → 규칙 검사기(checker)가 원장 대조로 잡아야 하고, LLM 판정(가짜 '모순')도 함께 올라와야 한다.
"""
from __future__ import annotations

import json
import re

import pytest

from app.core.config import settings
from app.pipeline import weave as W
from app.pipeline.interface import CanonDelta, CanonSnapshot

CH1 = "한서진은 지하 서고에서 쓰러졌다.\n\n그날 밤 한서진은 숨을 거두었다."
CH2 = "다음 날 아침, 한서진이 서고 문을 열고 들어와 웃으며 인사했다."

DOC1 = {
    "schema_version": 2,
    "worldview_rules": [{"rule_id": "NEW:죽은_자는_돌아오지_않는다", "content": "죽은 자는 되살아나지 않는다",
                         "certainty": "explicit", "source_text": ""}],
    "characters": [{"character_id": "NEW:한서진", "name": "한서진", "entity_type": "person"}],
    "locations": [{"location_id": "NEW:지하_서고", "name": "지하 서고"}],
    "objects": [],
    "times": [{"time_id": "NEW:그날_밤", "expression": "그날 밤", "timeline": "main"}],
    "events": [{
        "event_id": "EVENT_1_1", "order": 1, "scene_id": "S1", "title": "한서진의 죽음",
        "summary": "한서진이 지하 서고에서 쓰러져 그날 밤 숨을 거둔다.",
        "location_id": "NEW:지하_서고", "time_id": "NEW:그날_밤", "timeline": "main",
        "reality_status": "actual", "participant_character_ids": ["NEW:한서진"],
        "witness_character_ids": [], "main_actor_character_id": "NEW:한서진", "objects": [],
        "state_changes": [{"entity": "NEW:한서진", "field": "alive", "from": "alive", "to": "dead",
                           "source_text": "그날 밤 한서진은 숨을 거두었다."}],
        "assumed_states": [], "knowledge_gained": [], "cause": "unknown", "result": "사망",
        "source_text": "그날 밤 한서진은 숨을 거두었다.", "certainty": "explicit",
    }],
}

DOC2 = {
    "schema_version": 2, "worldview_rules": [],
    "characters": [{"character_id": "NEW:한서진", "name": "한서진", "entity_type": "person"}],
    "locations": [{"location_id": "NEW:지하_서고", "name": "지하 서고"}],
    "objects": [], "times": [],
    "events": [{
        "event_id": "EVENT_2_1", "order": 1, "scene_id": "S1", "title": "한서진의 인사",
        "summary": "한서진이 서고에 들어와 인사한다.", "location_id": "NEW:지하_서고",
        "time_id": None, "timeline": "main", "reality_status": "actual",
        "participant_character_ids": ["NEW:한서진"], "witness_character_ids": [],
        "main_actor_character_id": "NEW:한서진", "objects": [], "state_changes": [],
        "assumed_states": [{"entity": "NEW:한서진", "field": "alive", "assumes": "alive",
                            "source_text": "한서진이 서고 문을 열고 들어와"}],
        "knowledge_gained": [], "cause": "unknown", "result": "unknown",
        "source_text": "한서진이 서고 문을 열고 들어와 웃으며 인사했다.", "certainty": "explicit",
    }],
}


class FakeLLM:
    """weave_llm.complete 대체. 호출을 기록하고 역할별 고정 응답을 준다."""

    def __init__(self, docs: dict[int, dict], verdict: str = "모순"):
        self.docs, self.verdict = docs, verdict
        self.calls: list[str] = []

    def __call__(self, system, user, llm, *, role="judge", base=None):
        self.calls.append(role)
        if role == "extract":
            ep = int(re.search(r"episode = (\d+)", user).group(1))
            return {"status": "ok", "text": json.dumps(self.docs[ep], ensure_ascii=False)}
        # 2화 사건(EVENT_2_*)을 판정할 때만 양성 — 1화 자체는 정상
        verdict = self.verdict if "EVENT_2_" in user else "정상"
        return {"status": "ok", "text": json.dumps({
            "verdict": verdict, "reason": "1화에서 사망한 인물이 2화에서 살아 있다.",
            "evidence_event_ids": ["EVENT_1_1"], "confidence": 0.9}, ensure_ascii=False)}


@pytest.fixture
def weave(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-real")
    monkeypatch.setattr(settings, "weave_llm_provider", "openrouter")
    monkeypatch.setattr(settings, "weave_judge_k", 3)
    fake = FakeLLM({1: DOC1, 2: DOC2})
    monkeypatch.setattr(W.w_llm, "complete", fake)
    return W.WeavePipeline(data_dir=str(tmp_path)), fake, tmp_path


def _snapshot(pid: str, up_to: int, delta1: CanonDelta | None) -> CanonSnapshot:
    snap = CanonSnapshot(project_id=pid, up_to_chapter=up_to)
    if delta1 is not None:
        snap.events = [e.model_dump() | {"source_chapter": 1} for e in delta1.events]
        snap.characters = [c.model_dump() | {"source_chapter": 1} for c in delta1.characters]
        snap.worldview = [w.model_dump() | {"source_chapter": 1} for w in delta1.worldview]
    return snap


async def test_extract_builds_canon_delta_and_state(weave):
    pipe, fake, root = weave
    out = await pipe.extract_chapter(CH1, _snapshot("p1", 0, None), title="1화", chapter_no=1)
    d = out.canon_delta
    assert [c.name for c in d.characters] == ["한서진"]
    assert d.characters[0].entity_key.startswith("CHAR_")
    assert d.spaces[0].place == "지하 서고"
    assert d.events[0].entity_key == "EVENT_1_1" and d.events[0].quote
    assert d.worldview[0].item == "죽은 자는 되살아나지 않는다"
    assert d.timeline[0].time_value == "그날 밤"
    assert fake.calls == ["extract"]
    assert (root / "p1" / "episodes" / "episode_01.json").exists()
    reg = json.loads((root / "p1" / "registry.json").read_text(encoding="utf-8"))
    assert [c["name"] for c in reg["characters"]] == ["한서진"]


async def test_review_detects_dead_character_reappearing(weave):
    pipe, fake, root = weave
    out1 = await pipe.extract_chapter(CH1, _snapshot("p1", 0, None), chapter_no=1)
    out2 = await pipe.extract_chapter(CH2, _snapshot("p1", 1, out1.canon_delta), chapter_no=2)
    # 2화에서도 같은 인물로 묶여야 한다(레지스트리 병합)
    assert out2.canon_delta.characters[0].entity_key == out1.canon_delta.characters[0].entity_key

    fake.calls.clear()
    res = await pipe.review_chapter(CH2, _snapshot("p1", 1, out1.canon_delta),
                                    out2.canon_delta, chapter_no=2)
    assert fake.calls == ["judge"] * 3  # 장면 1개 × k=3
    kinds = [c.explanation.split("]")[0] for c in res.contradictions]
    assert any(k.startswith("[규칙 검사") for k in kinds), res.contradictions
    assert any(k.startswith("[LLM 판정") for k in kinds)
    for c in res.contradictions:
        assert CH2[c.span.start:c.span.end] == c.span.quote and c.span.end > c.span.start
    judge = next(c for c in res.contradictions if c.explanation.startswith("[LLM 판정"))
    assert judge.confidence == 1.0 and judge.type == "event"
    assert judge.conflict_ref.chapter_no == 1
    checker = next(c for c in res.contradictions if c.explanation.startswith("[규칙 검사"))
    assert checker.type == "character" and checker.conflict_ref.chapter_no == 1
    saved = json.loads((root / "p1" / "reviews" / "review_02.json").read_text(encoding="utf-8"))
    assert saved["confirmed_episodes"] == [1] and saved["judge_total_runs"] == 3


async def test_review_ignores_unsaved_chapters(weave):
    """캐논에 기여하지 않은(저장 안 된) 1화는 원장에 넣지 않는다 → 검사기 모순 없음."""
    pipe, fake, _ = weave
    await pipe.extract_chapter(CH1, _snapshot("p1", 0, None), chapter_no=1)
    out2 = await pipe.extract_chapter(CH2, _snapshot("p1", 0, None), chapter_no=2)
    res = await pipe.review_chapter(CH2, _snapshot("p1", 0, None), out2.canon_delta, chapter_no=2)
    assert not any(c.explanation.startswith("[규칙 검사") for c in res.contradictions)


async def test_review_negative_verdict_is_not_reported(weave, monkeypatch):
    pipe, fake, _ = weave
    fake.verdict = "정상"
    out1 = await pipe.extract_chapter(CH1, _snapshot("p1", 0, None), chapter_no=1)
    out2 = await pipe.extract_chapter(CH2, _snapshot("p1", 1, out1.canon_delta), chapter_no=2)
    res = await pipe.review_chapter(CH2, _snapshot("p1", 1, out1.canon_delta),
                                    out2.canon_delta, chapter_no=2)
    assert not any(c.explanation.startswith("[LLM 판정") for c in res.contradictions)


async def test_review_honors_deleted_events(weave):
    pipe, fake, _ = weave
    out1 = await pipe.extract_chapter(CH1, _snapshot("p1", 0, None), chapter_no=1)
    out2 = await pipe.extract_chapter(CH2, _snapshot("p1", 1, out1.canon_delta), chapter_no=2)
    edited = out2.canon_delta.model_copy(deep=True)
    edited.events = [e for e in edited.events if e.entity_key != "EVENT_2_1"]
    fake.calls.clear()
    res = await pipe.review_chapter(CH2, _snapshot("p1", 1, out1.canon_delta), edited, chapter_no=2)
    # 사건을 전부 지웠다 → 판정·검사할 사건이 없다
    assert fake.calls == [] and res.contradictions == []
    # 항목을 전부 지워 델타가 통째로 비어도 '편집 없음'으로 되돌려 전량 판정하지 않는다
    res2 = await pipe.review_chapter(CH2, _snapshot("p1", 1, out1.canon_delta), CanonDelta(), chapter_no=2)
    assert fake.calls == [] and res2.contradictions == []
    # 지우지 않은 원래 델타로는 판정한다 (위 두 결과가 '판정 자체가 고장'이 아님을 확인)
    res3 = await pipe.review_chapter(CH2, _snapshot("p1", 1, out1.canon_delta), out2.canon_delta, chapter_no=2)
    assert fake.calls.count("judge") == 3 and res3.contradictions


async def test_rules_document_uses_no_llm(weave):
    pipe, fake, _ = weave
    out = await pipe.extract_chapter("- 마법은 밤에만 쓸 수 있다\n- 용은 말을 하지 않는다",
                                     _snapshot("p1", 0, None), title="[규칙] 세계관", chapter_no=1)
    assert [w.item for w in out.canon_delta.worldview] == ["마법은 밤에만 쓸 수 있다", "용은 말을 하지 않는다"]
    res = await pipe.review_chapter("x", _snapshot("p1", 0, None), out.canon_delta,
                                    title="[규칙] 세계관", chapter_no=1)
    assert res.contradictions == [] and fake.calls == []


async def test_long_chapter_is_chunked_and_events_renumbered(weave, monkeypatch):
    pipe, fake, root = weave
    monkeypatch.setattr(settings, "weave_chunk_chars", 1200)
    long_text = "\n\n".join(["가" * 700] * 4)
    doc = json.loads(json.dumps(DOC1))
    fake.docs[3] = doc
    out = await pipe.extract_chapter(long_text, _snapshot("p1", 0, None), chapter_no=3)
    n = fake.calls.count("extract")
    assert n >= 2
    assert [e.entity_key for e in out.canon_delta.events] == [f"EVENT_3_{i}" for i in range(1, n + 1)]
    assert len(out.canon_delta.characters) == 1  # 조각마다 같은 인물 → 하나로


async def test_extract_without_key_fails_clearly(tmp_path, monkeypatch):
    for env in W.PROVIDER_KEY_ENV.values():
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setattr(settings, "weave_api_key", "")
    monkeypatch.setattr(settings, "weave_llm_provider", "openrouter")
    pipe = W.WeavePipeline(data_dir=str(tmp_path))
    health = await pipe.health()
    assert health.ok is False and health.mode == "weave"
    with pytest.raises(W.WeaveError, match="키"):
        await pipe.extract_chapter(CH1, _snapshot("p1", 0, None), chapter_no=1)


async def test_review_without_extraction_fails_clearly(weave):
    pipe, _, _ = weave
    with pytest.raises(W.WeaveError, match="추출"):
        await pipe.review_chapter(CH2, _snapshot("p9", 0, None), CanonDelta(), chapter_no=5)


async def test_health_ok_with_key(weave):
    pipe, _, _ = weave
    h = await pipe.health()
    assert h.ok is True and h.info["judge_k"] == 3


def test_factory_returns_weave(monkeypatch):
    from app.pipeline.factory import get_pipeline

    get_pipeline.cache_clear()
    monkeypatch.setattr(settings, "pipeline_mode", "weave")
    try:
        assert isinstance(get_pipeline(), W.WeavePipeline)
    finally:
        get_pipeline.cache_clear()


def test_locate_whitespace_and_missing():
    text = "그는  천천히\n걸어갔다. 그리고 멈췄다."
    s, found = W.locate(text, "그는 천천히 걸어갔다.")
    assert found and s.start == 0 and text[s.start:s.end] == s.quote
    s2, found2 = W.locate(text, "전혀 없는 문장")
    assert not found2 and s2.start == 0 and s2.end > 0


# ── 종단: 실제 API·DB 흐름에 WeavePipeline 을 꽂는다 (LLM 만 가짜) ──────────
async def test_api_flow_with_weave(client, auth_headers, project_id, weave, monkeypatch):
    from tests.util import MANUAL_SAVE, poll_result

    pipe, fake, root = weave
    monkeypatch.setattr("app.workers.tasks.get_pipeline", lambda: pipe)
    h, pid = auth_headers, project_id

    for no, text in ((1, CH1), (2, CH2)):
        r = await client.post(f"/projects/{pid}/chapters", json={"chapter_no": no, "text": text}, headers=h)
        assert r.status_code == 201, r.text
        await client.post(f"/projects/{pid}/chapters/{no}/extract", headers=h)
        ext = await poll_result(client, pid, no, h, stop=("extracted", "failed"))
        assert ext["status"] == "extracted", ext
        await client.post(f"/projects/{pid}/chapters/{no}/review", json=MANUAL_SAVE, headers=h)
        res = await poll_result(client, pid, no, h)
        assert res["status"] == "reviewed", res
        if no == 1:
            assert not res.get("contradictions")
            r = await client.post(f"/projects/{pid}/chapters/1/save", headers=h)
            assert r.status_code == 200, r.text

    cs = res["contradictions"]
    assert any(c["type"] == "character" for c in cs), cs
    assert all(CH2[c["span"]["start"]:c["span"]["end"]] == c["span"]["quote"] for c in cs)
    assert any((c.get("conflict_ref") or {}).get("chapter_no") == 1 for c in cs)

    # 1화 저장으로 Weave 추출 결과가 캐논에 들어갔다
    wv = (await client.get(f"/projects/{pid}/canon?view=worldview", headers=h)).json()
    assert any("죽은 자는 되살아나지 않는다" in json.dumps(it, ensure_ascii=False) for it in wv["items"]), wv
    chars = (await client.get(f"/projects/{pid}/canon?view=characters", headers=h)).json()
    assert "한서진" in json.dumps(chars, ensure_ascii=False), chars
    assert (root / str(pid) / "reviews" / "review_02.json").exists()


# ── 판정 병렬화 ─────────────────────────────────────────────
async def test_parallel_judging_matches_sequential(weave, monkeypatch):
    pipe, fake, _ = weave
    out1 = await pipe.extract_chapter(CH1, _snapshot("p1", 0, None), chapter_no=1)
    out2 = await pipe.extract_chapter(CH2, _snapshot("p1", 1, out1.canon_delta), chapter_no=2)
    snap = _snapshot("p1", 1, out1.canon_delta)

    monkeypatch.setattr(settings, "weave_judge_concurrency", 1)
    seq = await pipe.review_chapter(CH2, snap, out2.canon_delta, chapter_no=2)
    monkeypatch.setattr(settings, "weave_judge_concurrency", 3)
    par = await pipe.review_chapter(CH2, snap, out2.canon_delta, chapter_no=2)
    assert [c.model_dump() for c in par.contradictions] == [c.model_dump() for c in seq.contradictions]
    # 원래 judge_packet 이 제자리로 돌아왔다
    assert W.w_pipe.judge_packet.__name__ == "judge_packet"


async def test_parallel_judging_overlaps_calls(weave, monkeypatch):
    import threading
    import time as _t

    pipe, fake, root = weave
    out1 = await pipe.extract_chapter(CH1, _snapshot("p1", 0, None), chapter_no=1)
    out2 = await pipe.extract_chapter(CH2, _snapshot("p1", 1, out1.canon_delta), chapter_no=2)

    active, peak, lock = [0], [0], threading.Lock()
    inner = W.w_llm.complete

    def slow(system, user, llm, *, role="judge", base=None):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        _t.sleep(0.3)
        try:
            return inner(system, user, llm, role=role, base=base)
        finally:
            with lock:
                active[0] -= 1

    monkeypatch.setattr(W.w_llm, "complete", slow)
    monkeypatch.setattr(settings, "weave_judge_concurrency", 3)
    t0 = _t.monotonic()
    res = await pipe.review_chapter(CH2, _snapshot("p1", 1, out1.canon_delta), out2.canon_delta, chapter_no=2)
    took = _t.monotonic() - t0
    assert peak[0] == 3 and took < 0.8, (peak[0], took)  # 순차면 0.9초 이상
    assert any(c.explanation.startswith("[LLM 판정") for c in res.contradictions)
    saved = json.loads((root / "p1" / "reviews" / "review_02.json").read_text(encoding="utf-8"))
    assert saved["config"]["concurrency"] == 3
    assert [r["run_index"] for r in saved["judge"][0]["runs"]] == [1, 2, 3]


async def test_parallel_judging_survives_one_failed_run(weave, monkeypatch):
    pipe, fake, _ = weave
    out1 = await pipe.extract_chapter(CH1, _snapshot("p1", 0, None), chapter_no=1)
    out2 = await pipe.extract_chapter(CH2, _snapshot("p1", 1, out1.canon_delta), chapter_no=2)
    inner, n = W.w_llm.complete, [0]

    def flaky(system, user, llm, *, role="judge", base=None):
        n[0] += 1
        if n[0] == 1:
            raise RuntimeError("boom")
        return inner(system, user, llm, role=role, base=base)

    monkeypatch.setattr(W.w_llm, "complete", flaky)
    monkeypatch.setattr(settings, "weave_judge_concurrency", 3)
    res = await pipe.review_chapter(CH2, _snapshot("p1", 1, out1.canon_delta), out2.canon_delta, chapter_no=2)
    judge = next(c for c in res.contradictions if c.explanation.startswith("[LLM 판정"))
    # 검출률은 요청한 k=3 기준, 실패 회차는 따로 밝힌다
    assert "3회 중 2회 검출, 1회는 호출 실패" in judge.explanation and judge.confidence == 0.667


def _pair_doc(ep: int, title: str) -> dict:
    """한서진·윤도하가 한 사건에 함께 나오는 회차 (관계는 추출 스키마에 없다)."""
    return {
        "schema_version": 2, "worldview_rules": [],
        "characters": [{"character_id": "NEW:한서진", "name": "한서진", "entity_type": "person"},
                       {"character_id": "NEW:윤도하", "name": "윤도하", "entity_type": "person"}],
        "locations": [], "objects": [], "times": [],
        "events": [{
            "event_id": f"EVENT_{ep}_1", "order": 1, "scene_id": "S1", "title": title,
            "summary": "두 사람이 서고에서 만난다.", "location_id": None, "time_id": None,
            "timeline": "main", "reality_status": "actual",
            "participant_character_ids": ["NEW:윤도하"], "witness_character_ids": [],
            "main_actor_character_id": "NEW:한서진", "objects": [], "state_changes": [],
            "assumed_states": [], "knowledge_gained": [], "cause": "unknown", "result": "unknown",
            "source_text": "한서진은 서고에서 윤도하를 만났다.", "certainty": "explicit",
        }],
    }


async def test_co_appearance_relations(weave):
    """같은 사건의 주체·참여 인물끼리 '함께 등장' 관계 하나 — 방향 없는 키, 이름은 attrs 에."""
    pipe, fake, _ = weave
    fake.docs[1] = _pair_doc(1, "서고의 만남")
    out = await pipe.extract_chapter("한서진은 서고에서 윤도하를 만났다.", _snapshot("p1", 0, None), chapter_no=1)
    rels = out.canon_delta.relations
    assert len(rels) == 1
    r = rels[0]
    chars = {c.name: c.entity_key for c in out.canon_delta.characters}
    assert {r.from_char, r.to_char} == set(chars.values())
    assert r.entity_key == W.co_appearance_key(chars["윤도하"], chars["한서진"])
    assert r.relation == "함께 등장"
    assert r.attrs["basis"] == "co_appearance"
    assert {r.attrs["from_name"], r.attrs["to_name"]} == {"한서진", "윤도하"}
    assert r.attrs["description"] == "1화 「서고의 만남」에 함께 등장"
    assert r.attrs["event_ids"] == ["EVENT_1_1"]
    assert fake.calls == ["extract"]  # 관계를 위해 LLM 을 더 부르지 않는다

    # 2화에 같은 쌍이 또 나와도, 1화가 캐논에 넣은 관계는 다시 내지 않는다(새 버전이 쌓이지 않게)
    fake.docs[2] = _pair_doc(2, "다시 만남")
    snap = _snapshot("p1", 1, out.canon_delta)
    snap.relations = [x.model_dump() | {"source_chapter": 1} for x in rels]
    out2 = await pipe.extract_chapter("한서진은 서고에서 윤도하를 만났다.", snap, chapter_no=2)
    assert out2.canon_delta.relations == []
    # 캐논에 없으면(1화 미저장) 2화가 처음 잇는다
    out3 = await pipe.extract_chapter("한서진은 서고에서 윤도하를 만났다.", _snapshot("p1", 0, None), chapter_no=2)
    assert [x.attrs["description"] for x in out3.canon_delta.relations] == ["2화 「다시 만남」에 함께 등장"]


async def test_single_character_event_has_no_relation(weave):
    pipe, _, _ = weave
    out = await pipe.extract_chapter(CH1, _snapshot("p1", 0, None), chapter_no=1)
    assert out.canon_delta.relations == []


# 2화부터는 추출기가 '처음 나온' 인물만 characters 에 선언하고, 기존 인물은 사건 안에서 ID 로만 쓴다.
# 또 추출기가 선언을 빠뜨린 채 사건의 인물 칸에 NEW: 라벨만 쓰는 경우가 있다(실측: 'NEW:수리공').
DOC2_EXISTING_CAST = {
    "schema_version": 2, "worldview_rules": [], "characters": [], "locations": [],
    "objects": [], "times": [],
    "events": [{
        "event_id": "EVENT_2_1", "order": 1, "scene_id": "S1", "title": "수리",
        "summary": "수리공이 한서진 앞에서 보일러를 고친다.", "location_id": None,
        "time_id": None, "timeline": "main", "reality_status": "actual",
        "participant_character_ids": ["CHAR_001", "NEW:수리공"], "witness_character_ids": [],
        "main_actor_character_id": "NEW:수리공", "objects": [], "state_changes": [],
        "assumed_states": [], "knowledge_gained": [], "cause": "unknown", "result": "unknown",
        "source_text": "수리공이 보일러를 고쳤다.", "certainty": "explicit",
    }],
}


async def test_later_chapter_lists_existing_and_undeclared_cast(weave):
    pipe, fake, root = weave
    fake.docs[2] = DOC2_EXISTING_CAST
    out1 = await pipe.extract_chapter(CH1, _snapshot("p1", 0, None), chapter_no=1)
    out2 = await pipe.extract_chapter("수리공이 보일러를 고쳤다.",
                                      _snapshot("p1", 1, out1.canon_delta), chapter_no=2)
    chars = {c.entity_key: c for c in out2.canon_delta.characters}
    # 기존 인물(선언 없이 사건에만 등장)도 이 화 인물로 나온다
    assert chars["CHAR_001"].name == "한서진"
    # 선언이 빠진 인물 칸 라벨은 새 인물로 등재되고 사건 안의 라벨도 정식 ID 로 바뀐다
    new = [c for k, c in chars.items() if k != "CHAR_001"]
    assert [c.name for c in new] == ["수리공"] and new[0].entity_key.startswith("CHAR_")
    reg = json.loads((root / "p1" / "registry.json").read_text(encoding="utf-8"))
    assert "수리공" in [c["name"] for c in reg["characters"]]
    ev = json.loads((root / "p1" / "episodes" / "episode_02.json").read_text(encoding="utf-8"))
    assert ev["events"][0]["main_actor_character_id"] == new[0].entity_key
    # 등장 인물 사이 관계도 생긴다
    assert out2.canon_delta.relations


def test_reemitted_character_does_not_version_or_erase_description():
    """기존 인물을 다시 낼 때 캐논에 새 버전이 생기거나 앞 회차 설명이 지워지면 안 된다."""
    from app.service.canon_service import accumulate_attrs, normalize_for_compare

    reg = {"characters": [{"id": "CHAR_001", "name": "한서진", "entity_type": "person"}]}
    ev = {"event_id": "EVENT_2_1", "participant_character_ids": ["CHAR_001"]}
    d2 = W.episode_to_delta({"events": [ev]}, reg, {}, "r2")
    ch = d2.characters[0].model_dump()
    base = {"weave_id": "CHAR_001", "aliases": [], "description": "1화의 설명"}
    merged = accumulate_attrs(base, ch["attrs"], keep_base_text=False)
    assert merged["description"] == "1화의 설명"
    old = {"name": "한서진", "group_name": "person", "attrs": base}
    new = {"name": ch["name"], "group_name": ch["group_name"], "attrs": merged}
    assert normalize_for_compare(old) == normalize_for_compare(new)
