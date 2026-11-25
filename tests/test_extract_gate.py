"""① 추출 관문 — 등급 규칙, 판정 원장 제외(exclude/annotate/off), 결정성, 캐논 attrs.trust, analyze auto_save.

LLM 은 가짜(FakeLLM)로 바꿔 끼운다. 등급 규칙은 extract_gate.grade_episode 를 직접 부른다.
"""
from __future__ import annotations

import copy
import json

import pytest

from app.core.config import settings
from app.pipeline import extract_gate as G
from app.pipeline import weave as W
from tests.test_weave_pipeline import DOC2, FakeLLM, _snapshot
from tests.util import poll_result

# ── 등급 규칙 (grade_episode 직접) ─────────────────────────────────────

CHAR, LOC = "CHAR_001", "LOC_001"


def _ev(eid: str, order: int, scs: list[dict], **kw) -> dict:
    return {"event_id": eid, "order": order, "timeline": "main", "reality_status": "actual",
            "participant_character_ids": [CHAR], "main_actor_character_id": CHAR,
            "location_id": LOC, "state_changes": scs, "knowledge_gained": [],
            "assumed_states": [], "certainty": "explicit"} | kw


# 확정 1화: 인물이 죽는다 (alive 원장 = false)
EP1 = {"episode": 1, "events": [_ev("EVENT_1_1", 1, [
    {"entity": CHAR, "field": "alive", "from": "true", "to": "false"}])]}


def _grade(events: list[dict], **kw) -> G.GateResult:
    doc = {"episode": 2, "events": events}
    return G.grade_episode(doc, [copy.deepcopy(EP1)], {}, knows_mode="accumulate", **kw)


def _signals(entry: dict) -> list[str]:
    return [r["signal"] for r in entry["own_reasons"]] + [
        r["signal"] for s in entry["state_changes"] for r in s["reasons"]]


def test_clean_event_is_confirmed():
    g = _grade([_ev("EVENT_2_1", 1, [{"entity": CHAR, "field": "body_status",
                                      "from": "unknown", "to": "부상"}])])
    e = g.events["EVENT_2_1"]
    assert e["grade"] == G.CONFIRMED and _signals(e) == []
    assert g.tentative_event_ids == [] and g.warnings == []


def test_validator_hard_is_tentative():
    # 원장 alive=false 인데 from="true" → hard
    g = _grade([_ev("EVENT_2_1", 1, [{"entity": CHAR, "field": "alive", "from": "true", "to": "false"}]),
                _ev("EVENT_2_2", 2, [])])
    e = g.events["EVENT_2_1"]
    assert e["grade"] == G.TENTATIVE and e["own_reasons"] == []
    sc = e["state_changes"][0]
    assert sc["grade"] == G.TENTATIVE
    assert sc["reasons"][0]["signal"] == "validator_hard"
    assert sc["reasons"][0]["ledger_since"] == "EVENT_1_1"
    assert g.events["EVENT_2_2"]["grade"] == G.CONFIRMED
    assert g.tentative_event_ids == ["EVENT_2_1"]
    # validator 경고는 버리지 않고 결과에 그대로 남는다(새 회차 사건 것만)
    assert [w["level"] for w in g.warnings] == ["hard"]


def test_validator_soft_is_tentative():
    ep1 = copy.deepcopy(EP1)
    ep1["events"][0]["state_changes"].append(
        {"entity": CHAR, "field": "body_status", "from": "unknown", "to": "왼팔 골절"})
    doc = {"episode": 2, "events": [_ev("EVENT_2_1", 1, [
        {"entity": CHAR, "field": "body_status", "from": "건강함", "to": "회복"}])]}
    g = G.grade_episode(doc, [ep1], {}, knows_mode="accumulate")
    assert _signals(g.events["EVENT_2_1"]) == ["validator_soft"]


def test_schema_error_is_tentative():
    g = _grade([_ev("EVENT_2_1", 1, [{"entity": LOC, "field": "body_status",
                                      "from": "unknown", "to": "무너짐"}])])
    assert g.events["EVENT_2_1"]["grade"] == G.TENTATIVE
    assert _signals(g.events["EVENT_2_1"]) == ["schema_error"]


def test_unresolved_label_is_tentative():
    # 사건 본문(참여 인물)에 남은 NEW: → 사건 자체가 잠정(own_reasons)
    g = _grade([_ev("EVENT_2_1", 1, [], participant_character_ids=[CHAR, "NEW:낯선_남자"])])
    e = g.events["EVENT_2_1"]
    assert e["grade"] == G.TENTATIVE
    assert e["own_reasons"] == [{"signal": "unresolved_label", "label": "NEW:낯선_남자"}]
    # state_change 의 엔티티에 남은 NEW: → 그 state_change 만 잠정
    g = _grade([_ev("EVENT_2_1", 1, [{"entity": "NEW:낯선_남자", "field": "alive",
                                      "from": "unknown", "to": "false"}])])
    e = g.events["EVENT_2_1"]
    assert e["own_reasons"] == [] and e["state_changes"][0]["grade"] == G.TENTATIVE
    assert _signals(e) == ["unresolved_label"]


def test_mapped_label_is_not_unresolved():
    doc = {"episode": 2, "events": [_ev("EVENT_2_1", 1, [], participant_character_ids=["NEW:한서진"])]}
    g = G.grade_episode(doc, [copy.deepcopy(EP1)], {"NEW:한서진": CHAR}, knows_mode="accumulate")
    assert g.events["EVENT_2_1"]["grade"] == G.CONFIRMED


def test_unknown_from_only_when_ledger_has_value_and_switchable():
    known = _ev("EVENT_2_1", 1, [{"entity": CHAR, "field": "alive", "from": "unknown", "to": "true"}])
    g = _grade([copy.deepcopy(known)])
    assert _signals(g.events["EVENT_2_1"]) == ["unknown_from"]
    assert _grade([copy.deepcopy(known)], unknown_from=False).events["EVENT_2_1"]["grade"] == G.CONFIRMED
    # 원장에 값이 없으면(첫 기록) from="unknown" 은 정상
    first = _ev("EVENT_2_1", 1, [{"entity": "CHAR_002", "field": "alive", "from": "unknown", "to": "false"}])
    assert _grade([first]).events["EVENT_2_1"]["grade"] == G.CONFIRMED
    # possesses 는 from 이 의미 없는 필드
    pos = _ev("EVENT_2_1", 1, [{"entity": CHAR, "field": "possesses", "from": "unknown", "to": "OBJ_001"}])
    assert _grade([pos]).events["EVENT_2_1"]["grade"] == G.CONFIRMED


def test_grade_is_deterministic_and_does_not_mutate_input():
    events = [_ev("EVENT_2_1", 1, [{"entity": CHAR, "field": "alive", "from": "true", "to": "false"}],
                  participant_character_ids=[CHAR, "NEW:누군가"])]
    before = copy.deepcopy(events)
    a, b = _grade(copy.deepcopy(events)).to_dict(), _grade(events).to_dict()
    assert json.dumps(a, sort_keys=True, ensure_ascii=False) == json.dumps(b, sort_keys=True, ensure_ascii=False)
    assert events == before


def test_filter_event_drops_tentative_facts():
    gate = _grade([
        _ev("EVENT_2_1", 1, [{"entity": CHAR, "field": "alive", "from": "true", "to": "false"},
                             {"entity": CHAR, "field": "body_status", "from": "unknown", "to": "창백"}]),
        _ev("EVENT_2_2", 2, [], participant_character_ids=["NEW:x"]),
        _ev("EVENT_2_3", 3, []),
    ]).to_dict()
    doc = {"episode": 2, "events": [
        _ev("EVENT_2_1", 1, [{"entity": CHAR, "field": "alive", "from": "true", "to": "false"},
                             {"entity": CHAR, "field": "body_status", "from": "unknown", "to": "창백"}]),
        _ev("EVENT_2_2", 2, [], participant_character_ids=["NEW:x"]),
        _ev("EVENT_2_3", 3, []),
    ]}
    out, dropped, n_sc = G.filter_episode(doc, gate)
    assert dropped == ["EVENT_2_2"] and n_sc == 1
    assert [e["event_id"] for e in out["events"]] == ["EVENT_2_1", "EVENT_2_3"]
    assert [s["field"] for s in out["events"][0]["state_changes"]] == ["body_status"]
    assert len(doc["events"][0]["state_changes"]) == 2  # 원본 무변경
    assert G.filter_episode(doc, None) == (doc, [], 0)


# ── 어댑터 흐름 (FakeLLM) ─────────────────────────────────────────────

# 1화: 한서진이 죽고(EVENT_1_1), 이어서 '살아 있던' 한서진이 다시 살아난다고 잘못 추출된 사건(EVENT_1_2:
# from="true" 인데 원장은 false → validator hard → 잠정). off 면 원장 alive=true, exclude 면 false.
GDOC1 = {
    "schema_version": 2, "worldview_rules": [],
    "characters": [{"character_id": "NEW:한서진", "name": "한서진", "entity_type": "person"}],
    "locations": [{"location_id": "NEW:지하_서고", "name": "지하 서고"}],
    "objects": [], "times": [],
    "events": [
        {"event_id": "EVENT_1_1", "order": 1, "scene_id": "S1", "title": "한서진의 죽음",
         "summary": "한서진이 숨을 거둔다.", "location_id": "NEW:지하_서고", "time_id": None,
         "timeline": "main", "reality_status": "actual", "participant_character_ids": ["NEW:한서진"],
         "witness_character_ids": [], "main_actor_character_id": "NEW:한서진", "objects": [],
         "state_changes": [{"entity": "NEW:한서진", "field": "alive", "from": "true", "to": "false"}],
         "assumed_states": [], "knowledge_gained": [], "cause": "unknown", "result": "사망",
         "source_text": "그날 밤 한서진은 숨을 거두었다.", "certainty": "explicit"},
        {"event_id": "EVENT_1_2", "order": 2, "scene_id": "S2", "title": "한서진의 회복",
         "summary": "한서진이 기운을 차린다.", "location_id": "NEW:지하_서고", "time_id": None,
         "timeline": "main", "reality_status": "actual", "participant_character_ids": ["NEW:한서진"],
         "witness_character_ids": [], "main_actor_character_id": "NEW:한서진", "objects": [],
         "state_changes": [{"entity": "NEW:한서진", "field": "alive", "from": "true", "to": "true"}],
         "assumed_states": [], "knowledge_gained": [], "cause": "unknown", "result": "unknown",
         "source_text": "한서진은 기운을 차렸다.", "certainty": "explicit"},
    ],
}
# 2화: 살아 있는 한서진이 등장(전제 alive=true) — 원장이 false 일 때만 규칙 검사에 걸린다
GDOC2 = copy.deepcopy(DOC2)
GDOC2["events"][0]["assumed_states"][0]["assumes"] = "true"
GCH1 = "그날 밤 한서진은 숨을 거두었다.\n\n한서진은 기운을 차렸다."
GCH2 = "다음 날 아침, 한서진이 서고 문을 열고 들어와 웃으며 인사했다."


@pytest.fixture
def gweave(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-real")
    monkeypatch.setattr(settings, "weave_llm_provider", "openrouter")
    monkeypatch.setattr(settings, "weave_judge_k", 1)
    monkeypatch.setattr(settings, "weave_judge_majority", 1)
    fake = FakeLLM({1: GDOC1, 2: GDOC2})
    monkeypatch.setattr(W.w_llm, "complete", fake)
    return W.WeavePipeline(data_dir=str(tmp_path)), fake, tmp_path


def _gate_files(root, pid: str = "p1") -> list:
    return sorted((root / pid / "gates").glob("gate_*.json"))


async def test_extract_writes_gate_and_trust(gweave):
    pipe, _, root = gweave
    out = await pipe.extract_chapter(GCH1, _snapshot("p1", 0, None), chapter_no=1)
    trust = {e.entity_key: e.attrs.get("trust") for e in out.canon_delta.events}
    assert trust == {"EVENT_1_1": "confirmed", "EVENT_1_2": "tentative"}
    files = _gate_files(root)
    assert len(files) == 1
    rev = out.canon_delta.events[0].attrs["weave_rev"]
    assert files[0].name == f"gate_01_{rev}.json"
    gate = json.loads(files[0].read_text(encoding="utf-8"))
    assert gate["tentative_event_ids"] == ["EVENT_1_2"] and gate["rev"] == rev
    assert gate["warnings"][0]["level"] == "hard" and gate["warnings"][0]["event_id"] == "EVENT_1_2"


async def test_same_input_twice_gives_same_gate(gweave):
    pipe, _, root = gweave
    await pipe.extract_chapter(GCH1, _snapshot("p1", 0, None), chapter_no=1)
    await pipe.extract_chapter(GCH1, _snapshot("p2", 0, None), chapter_no=1)
    a, b = (json.loads(f.read_text(encoding="utf-8")) for f in (_gate_files(root, "p1")[0],
                                                                 _gate_files(root, "p2")[0]))
    for g in (a, b):
        g.pop("graded_at"), g.pop("rev")
    assert a == b


async def test_relation_trust_follows_source_events(gweave):
    pipe, _, _ = gweave
    reg = {"characters": [{"id": "CHAR_001", "name": "가"}, {"id": "CHAR_002", "name": "나"}]}
    ev = {"event_id": "EVENT_1_1", "title": "만남", "participant_character_ids": ["CHAR_001", "CHAR_002"]}
    gate = G.GateResult(episode=1, config={}, events={
        "EVENT_1_1": {"grade": G.TENTATIVE, "own_reasons": [], "state_changes": []}})
    d = W.episode_to_delta({"events": [ev], "episode": 1}, reg, {}, "r1", gate=gate)
    assert d.events[0].attrs["trust"] == "tentative"
    assert d.relations[0].attrs["trust"] == "tentative"
    # 관문 실패(gate=None)면 trust 키를 넣지 않는다
    d = W.episode_to_delta({"events": [ev], "episode": 1}, reg, {}, "r1")
    assert "trust" not in d.events[0].attrs and "trust" not in d.relations[0].attrs


async def _review_with_spy(pipe, monkeypatch, out1, out2):
    seen = {}
    real = W.w_pipe.replay_confirmed

    def spy(eps, **kw):
        led, v = real(eps, **kw)
        seen["eps"], seen["ledger"] = eps, led
        return led, v

    monkeypatch.setattr(W.w_pipe, "replay_confirmed", spy)
    res = await pipe.review_chapter(GCH2, _snapshot("p1", 1, out1.canon_delta), out2.canon_delta,
                                    chapter_no=2)
    return res, seen


@pytest.mark.parametrize("mode", ["off", "annotate", "exclude"])
async def test_gate_mode_controls_judge_ledger(gweave, monkeypatch, mode):
    pipe, _, root = gweave
    out1 = await pipe.extract_chapter(GCH1, _snapshot("p1", 0, None), chapter_no=1)
    out2 = await pipe.extract_chapter(GCH2, _snapshot("p1", 1, out1.canon_delta), chapter_no=2)
    char = out1.canon_delta.characters[0].entity_key
    monkeypatch.setattr(settings, "weave_gate_mode", mode)
    res, seen = await _review_with_spy(pipe, monkeypatch, out1, out2)

    alive = seen["ledger"].get("main", char, "alive")
    ev12 = next(e for e in seen["eps"][0]["events"] if e["event_id"] == "EVENT_1_2")
    review = json.loads((root / "p1" / "reviews" / "review_02.json").read_text(encoding="utf-8"))
    checker_hit = any(c.explanation.startswith("[규칙 검사") for c in res.contradictions)
    if mode == "exclude":
        # 잠정 state_change 가 빠져 원장이 실제로 달라진다 → 1화 사망이 살아 있고 2화 등장이 걸린다
        assert ev12["state_changes"] == []
        assert alive["value"] == "false" and alive["since"] == "EVENT_1_1"
        assert checker_hit
        assert review["gate"]["excluded_state_changes"] == 1
        assert review["gate"]["excluded_event_ids"] == []
    else:
        assert len(ev12["state_changes"]) == 1
        assert alive["value"] == "true" and alive["since"] == "EVENT_1_2"
        assert not checker_hit, [c.explanation for c in res.contradictions]
    if mode == "off":
        assert "gate" not in review
    else:
        g = review["gate"]
        assert g["mode"] == mode and g["tentative_event_ids"] == ["EVENT_1_2"]
        assert g["missing_gates"] == [] and g["current_gate_found"] is True
        assert g["current_tentative_event_ids"] == []


async def test_gate_off_review_matches_previous_behavior(gweave, monkeypatch):
    """off 는 등급 파일이 있어도 읽지 않는다 — 등급 파일을 지워도 결과가 같다."""
    pipe, _, root = gweave
    out1 = await pipe.extract_chapter(GCH1, _snapshot("p1", 0, None), chapter_no=1)
    out2 = await pipe.extract_chapter(GCH2, _snapshot("p1", 1, out1.canon_delta), chapter_no=2)
    a, _ = await _review_with_spy(pipe, monkeypatch, out1, out2)
    for f in _gate_files(root):
        f.unlink()
    b, _ = await _review_with_spy(pipe, monkeypatch, out1, out2)
    strip = lambda r: [c.model_dump(exclude={"id"}) for c in r.contradictions]  # noqa: E731
    assert strip(a) == strip(b)


async def test_exclude_without_gate_file_keeps_everything(gweave, monkeypatch):
    """관문 도입 전 판본(등급 파일 없음)은 전부 확정으로 본다."""
    pipe, _, root = gweave
    out1 = await pipe.extract_chapter(GCH1, _snapshot("p1", 0, None), chapter_no=1)
    out2 = await pipe.extract_chapter(GCH2, _snapshot("p1", 1, out1.canon_delta), chapter_no=2)
    for f in _gate_files(root):
        f.unlink()
    monkeypatch.setattr(settings, "weave_gate_mode", "exclude")
    _, seen = await _review_with_spy(pipe, monkeypatch, out1, out2)
    char = out1.canon_delta.characters[0].entity_key
    assert seen["ledger"].get("main", char, "alive")["value"] == "true"
    review = json.loads((root / "p1" / "reviews" / "review_02.json").read_text(encoding="utf-8"))
    assert len(review["gate"]["missing_gates"]) == 1 and review["gate"]["current_gate_found"] is False


# ── analyze auto_save (FakePipeline, 실제 API·DB) ──────────────────────

async def _upload(client, pid, h, no: int) -> None:
    r = await client.post(f"/projects/{pid}/chapters", json={"chapter_no": no, "text": f"{no}화 본문"},
                          headers=h)
    assert r.status_code == 201, r.text


async def test_analyze_auto_save_reaches_saved(client, auth_headers, project_id):
    h, pid = auth_headers, project_id
    await _upload(client, pid, h, 1)
    r = await client.post(f"/projects/{pid}/chapters/1/analyze", json={"auto_save": True}, headers=h)
    assert r.status_code == 202, r.text
    res = await poll_result(client, pid, 1, h, stop=("saved", "failed"))
    assert res["status"] == "saved", res


async def test_analyze_default_stops_at_reviewed(client, auth_headers, project_id):
    h, pid = auth_headers, project_id
    await _upload(client, pid, h, 1)
    r = await client.post(f"/projects/{pid}/chapters/1/analyze", headers=h)
    assert r.status_code == 202, r.text
    res = await poll_result(client, pid, 1, h)
    assert res["status"] == "reviewed", res
    res = await poll_result(client, pid, 1, h, tries=3, stop=("saved",))
    assert res["status"] == "timeout"  # 저장까지 가지 않는다


async def test_run_full_analysis_passes_auto_save(monkeypatch):
    import uuid

    from app.workers import tasks

    calls = []

    async def fake_extract(pid, no):
        return True

    async def fake_review(pid, no, auto_save=False):
        calls.append(auto_save)
        return True

    class _Ch:
        status = "extracted"

    class _Repo:
        def __init__(self, s):
            pass

        async def get(self, pid, no):
            return _Ch()

    class _Proj:
        def __init__(self, s):
            pass

        async def touch(self, pid):
            pass

    class _Sess:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def commit(self):
            pass

    monkeypatch.setattr(tasks, "run_extraction", fake_extract)
    monkeypatch.setattr(tasks, "run_review", fake_review)
    monkeypatch.setattr(tasks, "ChapterRepository", _Repo)
    monkeypatch.setattr(tasks, "ProjectRepository", _Proj)
    monkeypatch.setattr(tasks, "SessionLocal", lambda: _Sess())
    await tasks.run_full_analysis(uuid.uuid4(), 1)
    await tasks.run_full_analysis(uuid.uuid4(), 1, auto_save=True)
    assert calls == [False, True]


async def test_weave_analyze_auto_save_stores_trust(client, auth_headers, project_id, gweave, monkeypatch):
    """Weave 로 analyze(auto_save) → 캐논 사건 attrs.trust 가 INSERT 된 등급 그대로 남는다."""
    pipe, _, _ = gweave
    monkeypatch.setattr("app.workers.tasks.get_pipeline", lambda: pipe)
    h, pid = auth_headers, project_id
    r = await client.post(f"/projects/{pid}/chapters", json={"chapter_no": 1, "text": GCH1}, headers=h)
    assert r.status_code == 201, r.text
    r = await client.post(f"/projects/{pid}/chapters/1/analyze", json={"auto_save": True}, headers=h)
    assert r.status_code == 202, r.text
    res = await poll_result(client, pid, 1, h, stop=("saved", "failed"))
    assert res["status"] == "saved", res
    body = (await client.get(f"/projects/{pid}/canon?view=events", headers=h)).json()
    text = json.dumps(body, ensure_ascii=False)
    assert '"trust": "tentative"' in text and '"trust": "confirmed"' in text, text[:2000]
