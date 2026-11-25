"""judge 앞 단계 개선 P(app/pipeline/prejudge.py) — 규칙별 단위 테스트 + 플래그를 끄면 출력이 그대로인지(바이트 단위)."""
from __future__ import annotations

import copy
import json
from collections import Counter
from contextlib import contextmanager

import pytest

from app.core.config import settings
from app.pipeline import prejudge as PJ
from app.pipeline import weave as W
from app.pipeline.pairs import build_pairs
from tests.test_weave_pipeline import CH1, CH2, DOC1, DOC2, FakeLLM, _snapshot

import ledger as w_ledger  # noqa: E402 - weave_core 경로는 app.pipeline.weave 가 연다

REG = {"locations": [{"id": "LOC_001", "name": "보트 갑판"}, {"id": "LOC_002", "name": "함교"}]}


def _ev(**kw) -> dict:
    base = {"event_id": "EVENT_2_1", "timeline": "main", "reality_status": "actual", "certainty": "explicit",
            "location_id": "LOC_001", "participant_character_ids": ["CHAR_001"], "witness_character_ids": [],
            "main_actor_character_id": "CHAR_001", "state_changes": []}
    return base | kw


# ---------------------------------------------------------------- F2

def test_f2_restraint_to_transient_only_for_keywords():
    ev = _ev(state_changes=[
        {"entity": "CHAR_001", "field": "body_status", "to": "수갑 채워져 체포됨", "volatility": "persistent"},
        {"entity": "CHAR_002", "field": "body_status", "to": "오른 손목 골절", "volatility": "persistent"},
        {"entity": "CHAR_003", "field": "body_status", "to": "기절함", "volatility": "transient"}])
    c = Counter()
    out = PJ.f2_event(ev, {}, c)
    vols = [sc["volatility"] for sc in out["state_changes"]]
    assert vols == ["transient", "persistent", "transient"]
    assert c["restraint_transient"] == 1
    assert ev["state_changes"][0]["volatility"] == "persistent"   # 원본 불변


def test_f2_drops_location_unknown_and_resolves_new_location():
    names = PJ._location_names(REG)
    ev = _ev(location_id="NEW:보트_갑판", state_changes=[
        {"entity": "CHAR_001", "field": "location", "to": "unknown"},
        {"entity": "CHAR_002", "field": "location", "to": "NEW:함교"},
        {"entity": "CHAR_003", "field": "location", "to": "NEW:없는_곳"}])
    c = Counter()
    out = PJ.f2_event(ev, names, c)
    assert out["location_id"] == "LOC_001"
    assert [sc["to"] for sc in out["state_changes"]] == ["LOC_002", "NEW:없는_곳"]
    assert c["drop_location_unknown"] == 1 and c["new_location_resolved"] == 2


def test_f2_auto_location_extra_and_ledger_apply():
    ev = _ev(participant_character_ids=["CHAR_001"], witness_character_ids=["CHAR_002", "CHAR_001", "unknown"],
             main_actor_character_id="CHAR_003",
             state_changes=[{"entity": "CHAR_003", "field": "location", "to": "LOC_002"}])
    c = Counter()
    out = PJ.f2_event(ev, {}, c)
    assert out["auto_location_extra"] == ["CHAR_002"]   # 참여자·명시적 위치 변경 인물·unknown 제외
    led = w_ledger.Ledger(timelines=["main"], knows_mode="accumulate")
    PJ.apply_event_f2(led, out)
    st = led.state["main"]
    assert st["CHAR_002"]["location"]["value"] == "LOC_001"
    assert st["CHAR_002"]["location"]["source"] == "auto_witness"
    assert st["CHAR_001"]["location"]["source"] == "auto_participant"
    assert st["CHAR_003"]["location"]["value"] == "LOC_002"


def test_apply_event_f2_same_as_original_without_key():
    evs = [_ev(event_id=f"EVENT_2_{i}", witness_character_ids=["CHAR_009"],
               state_changes=[{"entity": "CHAR_001", "field": "body_status", "to": f"상태{i}"}]) for i in range(1, 4)]
    a = w_ledger.Ledger(timelines=["main"], knows_mode="accumulate")
    b = w_ledger.Ledger(timelines=["main"], knows_mode="accumulate")
    for e in evs:
        PJ._ORIG_APPLY_EVENT(a, e)
        PJ.apply_event_f2(b, e)
    assert json.dumps(a.to_dict(), sort_keys=True) == json.dumps(b.to_dict(), sort_keys=True)
    assert w_ledger.apply_event is PJ.apply_event_f2   # 어댑터 import 때 설치됨


# ---------------------------------------------------------------- F1

def test_snapshots_apply_prior_same_episode_events_only():
    base = w_ledger.Ledger(timelines=["main"], knows_mode="accumulate")
    base.set("main", "CHAR_001", "body_status", "수갑", "EVENT_1_1", "explicit")
    e1 = _ev(event_id="EVENT_2_1", state_changes=[{"entity": "CHAR_001", "field": "body_status", "to": "자유"}])
    e2 = _ev(event_id="EVENT_2_2")
    snaps = PJ.Snapshots(base, [e1, e2])
    assert snaps.before("EVENT_2_1") is base
    after = snaps.before("EVENT_2_2")
    assert after.state["main"]["CHAR_001"]["body_status"]["value"] == "자유"
    assert base.state["main"]["CHAR_001"]["body_status"]["value"] == "수갑"   # 기준 원장 불변
    assert snaps.before("EVENT_X") is base


def test_build_pairs_f1_same_order_and_rules():
    led = w_ledger.Ledger(timelines=["main"], knows_mode="accumulate")
    led.set("main", "CHAR_001", "body_status", "수갑 채워져 체포됨", "EVENT_1_1", "explicit")
    e1 = {"event_id": "EVENT_2_1", "scene_id": "S1", "timeline": "main", "title": "해제", "summary": "풀려난다",
          "source_text": "수갑이 풀렸다", "participant_character_ids": ["CHAR_001"],
          "state_changes": [{"entity": "CHAR_001", "field": "body_status", "to": "자유"}],
          "assumed_states": [{"entity": "CHAR_001", "field": "body_status", "assumes": "묶여 있음"}]}
    e2 = {"event_id": "EVENT_2_2", "scene_id": "S1", "timeline": "main", "title": "질주", "summary": "달린다",
          "source_text": "두 손을 흔들며 달렸다", "participant_character_ids": ["CHAR_001"],
          "assumed_states": [{"entity": "CHAR_001", "field": "body_status", "assumes": "두 손 자유"}]}
    group = {"key": "S1", "events": [e1, e2]}
    corpus = [{"event_id": "EVENT_1_1", "episode": 1, "global_idx": 0, "title": "체포", "summary": "체포",
               "participant_character_ids": ["CHAR_001"]}]
    reg = {"characters": [{"id": "CHAR_001", "name": "잭"}]}
    plain = build_pairs(group, led, corpus, reg, [])
    # 앞 사건이 상태를 안 바꾸면 F1 결과는 그대로
    still = PJ.build_pairs_f1(group, PJ.Snapshots(led, [dict(e1, state_changes=[]), e2]), corpus, reg, [])
    assert [(p.event_id, p.premise_kind, p.premise_id, p.premise_text) for p in still] == \
        [(p.event_id, p.premise_kind, p.premise_id, p.premise_text) for p in plain]
    f1 = PJ.build_pairs_f1(group, PJ.Snapshots(led, [e1, e2]), corpus, reg, [])
    assert [p.event_id for p in f1] == [p.event_id for p in plain]
    led2 = [p.premise_text for p in f1 if p.event_id == "EVENT_2_2" and p.premise_kind == "ledger"]
    assert led2 and all("자유" in t for t in led2)                         # 2번째 사건은 해제 뒤 원장을 본다
    led1 = [p.premise_text for p in f1 if p.event_id == "EVENT_2_1" and p.premise_kind == "ledger"]
    assert led1 and all("수갑" in t for t in led1)


# ---------------------------------------------------------------- F3

def test_f3_prompt_e_clause_guide_and_snapshot_tags():
    led = w_ledger.Ledger(timelines=["main"], knows_mode="accumulate")
    led.set("main", "CHAR_001", "location", "LOC_001", "EVENT_1_2", "explicit", source="auto_participant")
    led.set("main", "CHAR_001", "body_status", "수갑", "EVENT_1_1", "explicit")
    prompt = "\n".join([
        "## (b) 원장", "- CHAR_001:",
        "    location = LOC_001 (since EVENT_1_2, certainty explicit)",
        "    body_status = 수갑 (since EVENT_1_1, certainty explicit)", "",
        "## (e) 과거 사건 **전량** (3건, 시간순, 요약)", PJ.E_CLAUSE_OLD,
        "- EVENT_1_1 (1화, reality_status actual)", "    체포: 잡힘", "",
        PJ.F_HEADER, "1. 다음 중 하나로 판정하라"])
    c = Counter()
    out = PJ.f3_prompt(prompt, ledger=led, event={"event_id": "EVENT_2_1", "timeline": "main"},
                       order=["EVENT_1_1", "EVENT_1_2", "EVENT_1_3", "EVENT_2_1"], counts=c)
    lines = out.split("\n")
    assert "    location = LOC_001 (since EVENT_1_2, certainty explicit) [since 이후 1사건 · 출처 자동]" in lines
    assert "    body_status = 수갑 (since EVENT_1_1, certainty explicit) [since 이후 2사건 · 출처 명시]" in lines
    assert PJ.E_CLAUSE_NEW in lines and PJ.E_CLAUSE_OLD not in lines
    assert lines[lines.index(PJ.F_HEADER) + 1] == PJ.F3_GUIDE
    assert "    체포: 잡힘" in lines                                  # 과거 사건 줄은 건드리지 않는다
    assert c == Counter({"f3_snapshot_lines": 2, "f3_e_clause": 1, "f3_guide": 1})


def test_judge_prompt_patch_off_is_noop():
    class P:
        @staticmethod
        def build_judge_prompt(e, ledger, *a, **k):  # noqa: ANN001, ANN205
            return "x"
    orig = P.build_judge_prompt
    with PJ.judge_prompt_patch(P, f1=False, f3=False, episode_events=[], corpus=[]) as c:
        assert P.build_judge_prompt is orig
    assert not c


# ---------------------------------------------------------------- 어댑터: 플래그 끄면 그대로, 켜면 적용

class RecLLM(FakeLLM):
    def __init__(self, *a, **k) -> None:  # noqa: ANN002, ANN003
        super().__init__(*a, **k)
        self.users: list[tuple[str, str]] = []

    def __call__(self, system, user, llm, *, role="judge", base=None):  # noqa: ANN001
        self.users.append((role, user))
        return super().__call__(system, user, llm, role=role, base=base)


@contextmanager
def _flags(monkeypatch, **kw):  # noqa: ANN001, ANN003
    for k in ("f1", "f2", "f2_5", "f3", "f4", "f5", "f6", "f7", "f8", "f9"):
        monkeypatch.setattr(settings, f"weave_prejudge_{k}", bool(kw.get(k)))
    yield


async def _run(tmp_path, monkeypatch, tag: str):  # noqa: ANN001
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-real")
    monkeypatch.setattr(settings, "weave_llm_provider", "openrouter")
    monkeypatch.setattr(settings, "weave_judge_k", 3)
    monkeypatch.setattr(settings, "weave_judge_concurrency", 1)
    fake = RecLLM({1: copy.deepcopy(DOC1), 2: copy.deepcopy(DOC2)})
    monkeypatch.setattr(W.w_llm, "complete", fake)
    pipe = W.WeavePipeline(data_dir=str(tmp_path / tag))
    out1 = await pipe.extract_chapter(CH1, _snapshot("p1", 0, None), title="1화", chapter_no=1)
    out2 = await pipe.extract_chapter(CH2, _snapshot("p1", 1, out1.canon_delta), title="2화", chapter_no=2)
    res = await pipe.review_chapter(CH2, _snapshot("p1", 1, out1.canon_delta), out2.canon_delta, chapter_no=2)
    judge = sorted(u for r, u in fake.users if r == "judge")
    return out1, out2, res, judge


async def test_flags_off_byte_identical_to_unpatched(tmp_path, monkeypatch):
    with _flags(monkeypatch):
        a = await _run(tmp_path, monkeypatch, "a")

    @contextmanager
    def noop(*a, **k):  # noqa: ANN002, ANN003
        yield Counter()

    def boom(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("플래그가 꺼졌는데 F2 가 불렸다")
    monkeypatch.setattr(W.w_prejudge, "judge_prompt_patch", noop)
    for name in ("f2_doc", "normalize_bounded", "f5_regroup", "f6_doc", "f6_view", "f8_downgrade"):
        monkeypatch.setattr(W.w_prejudge, name, boom)
    with _flags(monkeypatch):
        b = await _run(tmp_path, monkeypatch, "b")
    assert a[3] and a[3] == b[3]                                      # 판정 프롬프트 바이트 단위로 같다
    assert a[0].model_dump_json() == b[0].model_dump_json()
    assert a[1].model_dump_json() == b[1].model_dump_json()
    assert a[2].model_dump_json() == b[2].model_dump_json()
    assert all(PJ.F3_GUIDE not in u for u in a[3])


async def test_flags_on_reach_judge_prompt(tmp_path, monkeypatch):
    with _flags(monkeypatch, f1=True, f2=True, f3=True, f2_5=True, f4=True, f5=True, f6=True, f7=True, f8=True, f9=True):
        _o1, _o2, res, judge = await _run(tmp_path, monkeypatch, "on")
    assert judge and all(PJ.F3_GUIDE in u for u in judge)
    assert any("since 이후" in u for u in judge)
    assert all(PJ.E_CLAUSE_OLD not in u for u in judge)
    assert res.contradictions                                          # 판정 경로는 그대로 돈다


# ---------------------------------------------------------------- 개선 P2

def test_f2_5_drops_location_assumptions_only_when_enabled():
    ev = _ev(assumed_states=[{"entity": "CHAR_001", "field": "location", "assumes": "보트 갑판에 머무름"},
                             {"entity": "CHAR_001", "field": "body_status", "assumes": "멀쩡"}])
    c = Counter()
    assert len(PJ.f2_event(ev, {}, c)["assumed_states"]) == 2                     # P 규칙만이면 그대로
    out = PJ.f2_event(ev, {}, c, PJ.f2_rules(base=False, assumed_location=True))
    assert [a["field"] for a in out["assumed_states"]] == ["body_status"]
    assert c[PJ.F2_ASSUMED_LOCATION] == 1 and c["restraint_transient"] == 0


def test_f4_normalize_bounded_keeps_pending_labels():
    m = {"NEW:칼": "CHAR_054", "NEW:보트_갑판": "LOC_030"}
    v = {"a": "NEW:칼_하클리 옆의 NEW:칼, 그리고 NEW:칼의_권총", "b": ["NEW:보트_갑판_로비", "NEW:보트_갑판"]}
    out = PJ.normalize_bounded(v, m)
    assert out == {"a": "NEW:칼_하클리 옆의 CHAR_054, 그리고 NEW:칼의_권총", "b": ["NEW:보트_갑판_로비", "LOC_030"]}
    assert w_ledger.normalize(v, m)["a"].startswith("CHAR_054_하클리")             # 코어는 깨뜨린다(H2)


def test_f5_regroup_consecutive_same_location_max6():
    evs = [_ev(event_id=f"E{i}", scene_id=f"c1:s{i}", location_id=loc) for i, loc in
           enumerate(["L1", "L1", "L2", "unknown", "unknown"] + ["L3"] * 8)]
    out, starts = PJ.f5_regroup(evs)
    keys = [e["scene_id"] for e in out]
    assert keys == ["F5_1", "F5_1", "F5_2", "F5_3", "F5_4"] + ["F5_5"] * 6 + ["F5_6"] * 2
    assert starts["E1"] == 0 and starts["E4"] == 4 and starts["E10"] == 5 and starts["E11"] == 11
    assert evs[0]["scene_id"] == "c1:s0"                                           # 원본 불변


def test_snapshots_bundle_boundary_and_f6_view():
    base = w_ledger.Ledger(timelines=["main"], knows_mode="accumulate")
    e1 = _ev(event_id="E1", location_id="L1", state_changes=[{"entity": "CHAR_001", "field": "body_status", "to": "자유"}])
    e2 = _ev(event_id="E2", location_id="L1")
    mem = _ev(event_id="E3", location_id="L9", reality_status="memory",
              state_changes=[{"entity": "CHAR_001", "field": "body_status", "to": "어린 시절"}])
    e4 = _ev(event_id="E4", location_id="L2")
    _, starts = PJ.f5_regroup([e1, e2, mem, e4])
    snaps = PJ.Snapshots(base, [e1, e2, mem, e4], starts=starts, view=PJ.f6_view)
    assert snaps.before("E2") is base                                              # 같은 묶음은 묶음 앞 원장
    led4 = snaps.before("E4")
    assert led4.state["main"]["CHAR_001"]["body_status"]["value"] == "자유"       # memory 상태는 재생 안 함
    assert led4.state["main"]["CHAR_001"]["location"]["value"] == "L1"            # memory 위치 갱신도 안 함
    assert [e["event_id"] for e in snaps.prior_events("E4")] == ["E1", "E2", "E3"]
    assert snaps.prior_events("E2") == []


def test_f7_names_and_transient_and_f9_prior_events():
    prompt = "\n".join([
        "- CHAR_001:", "    location = LOC_001 (since EVENT_1_2, certainty explicit)",
        "    body_status = 수갑 (since EVENT_1_1, certainty explicit) [일시적, 해소 사건 미기록 — 해소 시 None]",
        "    possesses = OBJ_043 (since EVENT_1_1, certainty explicit)", "",
        "## (e) 과거 사건 **전량** (1건, 시간순, 요약)", PJ.E_CLAUSE_OLD,
        "- EVENT_1_1 (1화, reality_status actual)", "    체포: CHAR_001 이 잡힘", "", PJ.F_HEADER])
    names = {"CHAR_001": "잭", "LOC_001": "보트 갑판", "OBJ_043": "승선권"}
    prior = [{"event_id": "EVENT_2_1", "reality_status": "actual", "title": "해제", "summary": "풀려난다"}]
    c = Counter()
    out = PJ.postprocess_prompt(prompt, ledger=w_ledger.Ledger(timelines=["main"]), event={"event_id": "EVENT_2_2"},
                                order=["EVENT_1_1", "EVENT_1_2", "EVENT_2_1", "EVENT_2_2"], counts=c,
                                f7=True, f9=True, names=names, prior=prior, episode=2)
    lines = out.split("\n")
    assert lines[0] == "- CHAR_001(잭):"
    assert lines[1] == "    location = LOC_001(보트 갑판) (since EVENT_1_2, certainty explicit)"
    assert lines[2].endswith(PJ.TRANSIENT_NEW.strip()) and "None" not in lines[2]
    assert "OBJ_043(승선권)" in lines[3]
    assert "    체포: CHAR_001 이 잡힘" in lines                                     # 과거 사건 줄은 이름 안 붙임
    i = lines.index(PJ.F_HEADER)
    assert lines[i - 4:i] == [PJ.F9_HEADER, "- EVENT_2_1 (2화, reality_status actual)", "    해제: 풀려난다", ""]
    assert PJ.E_CLAUSE_OLD in lines                                                 # F3 꺼짐 → (e)절 문구 그대로
    out3 = PJ.postprocess_prompt(prompt, ledger=w_ledger.Ledger(timelines=["main"]), event={"event_id": "EVENT_2_2"},
                                 order=["EVENT_1_1", "EVENT_1_2", "EVENT_2_1", "EVENT_2_2"], f3=True, f9=True,
                                 prior=prior, episode=2)
    assert PJ.E_CLAUSE_NEW_F9 in out3.split("\n")


def test_f8_downgrades_nonactual_structural_and_possesses_removed():
    import checker as w_checker
    items = [{"event_id": "E1", "type": "structural_alive", "severity": w_checker.SEV_HARD},
             {"event_id": "E2", "type": "structural_alive", "severity": w_checker.SEV_HARD},
             {"event_id": "E2", "type": "possesses_removed", "severity": w_checker.SEV_HARD},
             {"event_id": "E2", "type": "assumed_state", "severity": w_checker.SEV_HARD}]
    evs = {"E1": {"reality_status": "vision"}, "E2": {"reality_status": "actual"}}
    c = Counter()
    out = PJ.f8_downgrade(items, evs, c)
    assert [o["severity"] for o in out] == [w_checker.SEV_INFO, w_checker.SEV_HARD, w_checker.SEV_INFO, w_checker.SEV_HARD]
    assert c == Counter({"f8_structural": 1, "f8_possesses_removed": 1})
    assert items[0]["severity"] == w_checker.SEV_HARD
