"""학습 judge(WEAVE_JUDGE_MODE=learned) 테스트 — 모델 없이 FakeBackend 로.

(a) LLM 0회: 추출·1화 저장까지는 FakeLLM, 그 뒤 w_llm.complete 를 터지는 함수로 바꿔도 2화 검수가 돈다
(b) 결정성: 같은 입력 두 번 → 모순 목록 완전 일치
(c) 쌍 상한·순서
(d) confidence 통과와 라벨
(e) tentative_event_ids 꼬리표
"""
from __future__ import annotations

import pytest

from app.core.config import settings
from app.pipeline import learned_judge as LJ
from app.pipeline import weave as W
from app.pipeline.pairs import KIND_RANK, build_pairs
from tests.test_weave_pipeline import CH1, CH2, _snapshot, weave  # noqa: F401 - fixture

from ledger import Ledger  # noqa: E402 - 경로는 app.pipeline.weave 가 연다


def _boom(*a, **kw):  # noqa: ANN002, ANN003
    raise AssertionError("learned 모드에서 LLM 이 호출됐다")


@pytest.fixture
def learned(monkeypatch):
    monkeypatch.setattr(settings, "weave_judge_mode", "learned")
    monkeypatch.setattr(settings, "weave_judge_backend", "fake")
    monkeypatch.setattr(settings, "weave_judge_threshold", None)
    LJ._CACHE.clear()
    yield
    LJ._CACHE.clear()


async def _extract_two(pipe):  # noqa: ANN001
    out1 = await pipe.extract_chapter(CH1, _snapshot("p1", 0, None), chapter_no=1)
    out2 = await pipe.extract_chapter(CH2, _snapshot("p1", 1, out1.canon_delta), chapter_no=2)
    return out1, out2


# ---------------------------------------------------------------- (a) LLM 0회

async def test_learned_review_makes_no_llm_call(weave, monkeypatch):  # noqa: F811
    pipe, fake, root = weave
    out1, out2 = await _extract_two(pipe)
    monkeypatch.setattr(settings, "weave_judge_mode", "learned")
    monkeypatch.setattr(settings, "weave_judge_backend", "fake")
    monkeypatch.setattr(settings, "weave_judge_threshold", None)
    LJ._CACHE.clear()
    monkeypatch.setattr(W.w_llm, "complete", _boom)
    monkeypatch.setattr(W.w_llm, "complete_json", _boom)

    res = await pipe.review_chapter(CH2, _snapshot("p1", 1, out1.canon_delta), out2.canon_delta, chapter_no=2)
    learned_items = [c for c in res.contradictions if c.explanation.startswith("[학습 판정")]
    assert learned_items, res.contradictions
    assert not any(c.explanation.startswith("[LLM 판정") for c in res.contradictions)

    import json
    saved = json.loads((root / "p1" / "reviews" / "review_02.json").read_text(encoding="utf-8"))
    assert saved["config"]["judge_mode"] == "learned" and saved["config"]["k"] == 1
    assert saved["config"]["majority"] == 1
    runs = [r for it in saved["judge"] for r in it["runs"]]
    assert len(runs) == 1 and runs[0]["judged_by"].startswith("learned")
    assert runs[0]["model_hash"] == saved["config"]["judge_model_hash"]
    assert saved["config"]["judge_env"]["backend"] == "fake"  # 환경 기록은 회차에 한 번
    assert runs[0]["pairs_version"] == "2" and "env" not in runs[0]
    LJ._CACHE.clear()


# ---------------------------------------------------------------- (b) 결정성

async def test_learned_review_is_deterministic(weave, learned, monkeypatch):  # noqa: F811
    pipe, _, _ = weave
    out1, out2 = await _extract_two(pipe)
    monkeypatch.setattr(W.w_llm, "complete", _boom)
    snap = _snapshot("p1", 1, out1.canon_delta)
    a = await pipe.review_chapter(CH2, snap, out2.canon_delta, chapter_no=2)
    b = await pipe.review_chapter(CH2, snap, out2.canon_delta, chapter_no=2)
    assert [c.model_dump() for c in a.contradictions] == [c.model_dump() for c in b.contradictions]
    assert a.contradictions


# ---------------------------------------------------------------- (c) 쌍 상한·순서

def _toy(n_past: int = 40):
    led = Ledger(timelines=["main"], knows_mode="accumulate")
    led.set("main", "CHAR_001", "body_status", "오른 손목 골절", "EVENT_1_1", "explicit")
    led.add_knowledge("CHAR_001", "금고 비밀번호", "direct", "EVENT_1_2")
    corpus = [{"event_id": f"EVENT_1_{i}", "episode": 1, "global_idx": i, "title": f"과거{i}",
               "summary": "s", "source_text": f"원문{i}", "participant_character_ids": ["CHAR_001"]}
              for i in range(1, n_past + 1)]
    ev = {"event_id": "EVENT_2_1", "scene_id": "S1", "timeline": "main", "title": "필기",
          "summary": "오른손으로 적는다", "source_text": "오른손으로 또박또박 적었다",
          "participant_character_ids": ["CHAR_001"],
          "assumed_states": [{"entity": "CHAR_001", "field": "body_status", "assumes": "오른손 멀쩡",
                              "source_text": "오른손으로"}]}
    ev2 = {"event_id": "EVENT_2_2", "scene_id": "S1", "timeline": "main", "title": "퇴장",
           "summary": "나간다", "source_text": "나갔다", "participant_character_ids": ["CHAR_001"]}
    reg = {"characters": [{"id": "CHAR_001", "name": "슐라우"}],
           "worldview_rules": [{"id": "RULE_002", "content": "돈이 없다"},
                               {"id": "RULE_001", "content": "점원이 없다"}]}
    checker = [{"event_id": "EVENT_2_1", "entity": "CHAR_001", "field": "body_status",
                "premise": "오른손 멀쩡", "ledger_value": "오른 손목 골절", "since": "EVENT_1_1",
                "needs_judge": True},
               {"event_id": "EVENT_2_1", "entity": "CHAR_001", "field": "alive", "needs_judge": False}]
    return {"key": "S1", "events": [ev, ev2]}, led, corpus, reg, checker


def test_pairs_order_and_cap_v1():
    """quotas=None — 개정 전(v1) 순서·상한 32."""
    group, led, corpus, reg, checker = _toy()
    pairs = build_pairs(group, led, corpus, reg, checker, quotas=None)
    assert pairs == build_pairs(group, led, corpus, reg, checker, quotas=None)
    by_hyp: dict = {}
    for p in pairs:
        by_hyp.setdefault((p.event_id, p.entity, p.field), []).append(p)
    assert all(len(v) <= 32 for v in by_hyp.values())
    assert len(by_hyp[("EVENT_2_1", "CHAR_001", "body_status")]) == 32  # 과거 사건 40건 → 잘림
    first = by_hyp[("EVENT_2_1", "CHAR_001", "body_status")]
    ranks = [KIND_RANK[p.premise_kind] for p in first]
    assert ranks == sorted(ranks)
    assert [p.premise_kind for p in first[:5]] == ["checker", "ledger", "rule", "rule", "knowledge"]
    assert first[0].premise_id == "EVENT_1_1" and "골절" in first[0].premise_text
    assert [p.premise_id for p in first if p.premise_kind == "rule"] == ["RULE_001", "RULE_002"]
    past = [p.premise_id for p in first if p.premise_kind == "past"]
    assert past[:3] == ["EVENT_1_40", "EVENT_1_39", "EVENT_1_38"]  # 최근 순
    scene = [p for p in first if p.premise_kind == "scene"]
    assert [p.premise_id for p in scene] == ["EVENT_2_2"]
    # 가설에 원문 발췌가 들어간다
    assert "오른손으로 또박또박 적었다" in first[0].hypothesis_text
    assert build_pairs(group, led, corpus, reg, checker, max_premises=3, quotas=None)[0:3] == first[:3]
    assert len([p for p in build_pairs(group, led, corpus, reg, checker, max_premises=3, quotas=None)
                if p.event_id == "EVENT_2_1" and p.field == "body_status"]) == 3


def test_judge_group_picks_best_premise_and_threshold():
    group, led, corpus, reg, checker = _toy(n_past=3)
    backend = LJ.FakeBackend(fixed={("EVENT_2_1", "EVENT_1_1"): 0.8, ("EVENT_2_2", "RULE_001"): 0.8})
    pos = LJ.LearnedJudge(backend, threshold=0.7).judge_group(group, led, corpus, reg, checker)
    assert pos["verdict"] == "모순" and pos["confidence"] == 0.8
    assert pos["evidence_event_ids"] == ["EVENT_1_1"]  # 동률이면 앞선 쌍
    assert pos["run_index"] == 1 and pos["status"] == "ok"
    assert pos["judged_by"] == f"learned / {backend.model_hash[:16]}"
    neg = LJ.LearnedJudge(backend, threshold=0.85).judge_group(group, led, corpus, reg, checker)
    assert neg["verdict"] == "정상" and neg["confidence"] == 0.8
    kn = LJ.LearnedJudge(LJ.FakeBackend(fixed={("EVENT_2_1", "EVENT_1_2"): 0.99}), 0.5)
    assert kn.judge_group(group, led, corpus, reg, checker)["verdict"] == "지식범위위반"


def test_model_dir_hash_changes_with_files(tmp_path):
    (tmp_path / "a.bin").write_bytes(b"x")
    h1 = LJ.model_dir_hash(tmp_path)
    assert h1 == LJ.model_dir_hash(tmp_path)
    (tmp_path / "judge_meta.json").write_text('{"threshold": 0.6}', encoding="utf-8")
    assert LJ.model_dir_hash(tmp_path) != h1
    with pytest.raises(FileNotFoundError):
        LJ.model_dir_hash(tmp_path / "none")


# ---------------------------------------------------------------- (d)(e) 변환 함수

def _checks(confidence: float = 0.734, verdict: str = "모순", evidence=("EVENT_1_1",)):  # noqa: ANN001
    run = {"run_index": 1, "status": "ok", "verdict": verdict, "confidence": confidence,
           "evidence_event_ids": list(evidence), "judged_by": "learned / abc",
           "reason": "r", "evidence_premise": {"kind": "ledger", "id": evidence[0] if evidence else None,
                                               "text": "한서진.alive = false", "hypothesis": "한서진의 alive: alive",
                                               "event_id": "EVENT_2_1"}}
    return {
        "checker": [{"event_id": "EVENT_2_1", "severity": "강한 모순", "entity": "CHAR_001", "field": "alive",
                     "premise": "alive", "ledger_value": "false", "since": "EVENT_1_1"}],
        "judge": [{"packet_key": "S1", "event_ids": ["EVENT_2_1"], "runs": [run],
                   "aggregate": {"evidence_union": list(evidence)}}],
        "config": {"judge_mode": "learned", "k": 1},
    }


EVENTS = [{"event_id": "EVENT_2_1", "source_text": "한서진이 서고 문을 열고 들어와"}]
CORPUS = [{"event_id": "EVENT_1_1", "episode": 1, "source_text": "그날 밤 한서진은 숨을 거두었다."}]


def test_learned_confidence_and_label():
    out = W.checks_to_contradictions(_checks(), EVENTS, CORPUS, CH2, 2, 3)
    j = [c for c in out if c.explanation.startswith("[학습 판정")]
    assert len(j) == 1
    c = j[0]
    assert c.confidence == 0.734  # k 와 무관하게 모델 확률
    assert c.explanation.startswith("[학습 판정·모순]")
    assert "판정" in c.explanation and "회 중" not in c.explanation
    assert "한서진.alive = false" in c.explanation  # 근거 전제
    assert c.conflict_ref.chapter_no == 1 and c.type == "event"
    assert CH2[c.span.start:c.span.end] == c.span.quote
    # 정상 판정은 올리지 않는다
    assert not [c for c in W.checks_to_contradictions(_checks(verdict="정상"), EVENTS, CORPUS, CH2, 2, 3)
                if c.explanation.startswith("[학습 판정")]


def test_llm_mode_label_unchanged():
    checks = _checks()
    checks["config"] = {"judge_mode": "api", "k": 2}
    out = W.checks_to_contradictions(checks, EVENTS, CORPUS, CH2, 2, 2)
    j = [c for c in out if c.explanation.startswith("[LLM 판정")]
    assert j and "판정 2회 중 1회 검출" in j[0].explanation and j[0].confidence == 0.5


def test_tentative_tail():
    tail = W.TENTATIVE_NOTE
    assert tail == " (낮은 확신 — 근거가 잠정 사실)"
    plain = W.checks_to_contradictions(_checks(), EVENTS, CORPUS, CH2, 2, 3)
    assert not any(c.explanation.endswith(tail) for c in plain)
    tagged = W.checks_to_contradictions(_checks(), EVENTS, CORPUS, CH2, 2, 3,
                                        tentative_event_ids=frozenset({"EVENT_1_1"}))
    assert len(tagged) == 2 and all(c.explanation.endswith(tail) for c in tagged)
    other = W.checks_to_contradictions(_checks(), EVENTS, CORPUS, CH2, 2, 3,
                                       tentative_event_ids=frozenset({"EVENT_9_9"}))
    assert not any(c.explanation.endswith(tail) for c in other)
    # 근거 사건이 하나라도 확정이면 꼬리표 없음
    mixed = W.checks_to_contradictions(_checks(evidence=("EVENT_1_1", "EVENT_1_2")), EVENTS,
                                       CORPUS + [{"event_id": "EVENT_1_2", "episode": 1}], CH2, 2, 3,
                                       tentative_event_ids=frozenset({"EVENT_1_1"}))
    assert not any(c.explanation.endswith(tail) for c in mixed if c.explanation.startswith("[학습"))


# ---------------------------------------------------------------- 학습 데이터 (모델 없이)

def _load_script(name: str):  # noqa: ANN202
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_script_{path.stem}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_inject_and_export_are_deterministic_and_keyed(tmp_path):
    from pathlib import Path

    inject = _load_script("eval/inject")
    export = _load_script("eval/export_pairs")
    base = Path(__file__).resolve().parent / "eval" / "weave_checker_corpus"
    m1, m2 = tmp_path / "m1.json", tmp_path / "m2.json"
    inject.main(["--base", str(base), "--out", str(m1), "--per-operator", "2", "--seed", "3"])
    inject.main(["--base", str(base), "--out", str(m2), "--per-operator", "2", "--seed", "3"])
    assert m1.read_bytes() == m2.read_bytes()
    # 연산자 하나를 빼도 나머지 사례는 그대로(연산자별 독립 난수열)
    m3 = tmp_path / "m3.json"
    inject.main(["--base", str(base), "--out", str(m3), "--per-operator", "2", "--seed", "3",
                 "--exclude", "knowledge_heard"])
    import json
    full = [c for c in json.loads(m1.read_text(encoding="utf-8"))["cases"] if c["operator"] != "knowledge_heard"]
    assert json.loads(m3.read_text(encoding="utf-8"))["cases"] == full

    out = tmp_path / "pairs.jsonl"
    export.main(["--work", "wm", "--inject", str(m1), "--real", "--out", str(out),
                 "--holdout-operators", "knowledge_heard"])
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    manifest = json.loads(m1.read_text(encoding="utf-8"))
    need = {(c["case_id"], lab["premise_id"], lab["label"]) for c in manifest["cases"] for lab in c["labels"]}
    got = {(r["case_id"], r["premise_id"], r["label"]) for r in rows if r["keyed"] and r["source"] == "inject"}
    assert need <= got  # 정답이 명시된 전제는 전부 쌍으로 나온다(상한에 잘리지 않음)
    assert {r["split"] for r in rows if r["operator"] == "knowledge_heard"} == {"holdout"}
    assert {r["label"] for r in rows} <= set(LJ.LABELS) | {"제외"}  # 제외: 학습·보정·기준값에서 빠진다
    assert all(r["work"] == "wm" and r.get("group_id") and r.get("scene_id") for r in rows)
    assert any(r["source"] == "real" and r["label"] == "모순" for r in rows)
    out2 = tmp_path / "pairs2.jsonl"
    export.main(["--work", "wm", "--inject", str(m1), "--real", "--out", str(out2),
                 "--holdout-operators", "knowledge_heard"])
    assert out.read_bytes() == out2.read_bytes()


def test_pick_scene_threshold():
    """쌍 단위 F1 기준값(pick_threshold)은 버렸다 — 장면 max p(모순), 정상 장면 오탐 ≤ 목표에서 recall 최대(설계안 §2-4).
    세부 경우는 tests/eval/test_train_lib.py."""
    train = _load_script("train_judge")
    assert not hasattr(train, "pick_threshold")
    rows = [{"label": "모순", "scene_id": "a", "work": "w"}, {"label": "무관", "scene_id": "a", "work": "w"},
            {"label": "무관", "scene_id": "b", "work": "w"}, {"label": "정상", "scene_id": "c", "work": "w"}]
    t, m = train.pick_scene_threshold(rows, [0.9, 0.95, 0.4, 0.2], 0.0)
    assert t == 0.95 and m["recall"] == 1.0 and m["fp"] == 0


# ---------------------------------------------------------------- 쌍 생성기 v2 (설계안 §2-3·§2-5)

V1_TOY_SHA256 = "28985a83d4a3eed4f68272725fd7c62e71c4dc8627c6fccc596c9d95fe1767c2"  # 개정 전 pairs.py 출력


def _digest(pairs) -> str:  # noqa: ANN001
    import hashlib
    import json
    return hashlib.sha256(json.dumps([p.to_dict() for p in pairs], ensure_ascii=False,
                                     sort_keys=True).encode()).hexdigest()


def test_pairs_quotas_none_matches_v1():
    """quotas=None 이면 개정 전 출력과 바이트 단위로 같다 — current_episode·excerpt_chars 를 줘도."""
    group, led, corpus, reg, checker = _toy()
    group["events"][0]["source_text"] = "오른손으로 " * 60  # 160자보다 길다 — v1 은 자르지 않는다
    pairs = build_pairs(group, led, corpus, reg, checker, quotas=None)
    assert len(pairs) == 96 and _digest(pairs) == V1_TOY_SHA256
    again = build_pairs(group, led, corpus, reg, checker, quotas=None, current_episode=7, excerpt_chars=10)
    assert _digest(again) == V1_TOY_SHA256


def test_pairs_quota_deterministic_and_counts():
    import random
    from collections import Counter

    from app.pipeline.pairs import MAX_PREMISES, PAIRS_VERSION, QUOTAS

    assert PAIRS_VERSION == "2" and MAX_PREMISES == 24
    group, led, corpus, reg, checker = _toy()
    a = build_pairs(group, led, corpus, reg, checker, current_episode=2)
    assert a == build_pairs(group, led, corpus, reg, checker, current_episode=2)
    # corpus·checker 입력 순서가 달라도 같은 출력(정렬 키에 동률 처리)
    shuffled = list(corpus)
    random.Random(7).shuffle(shuffled)
    assert a == build_pairs(group, led, shuffled, reg, list(reversed(checker)), current_episode=2)

    by_hyp: dict = {}
    for p in a:
        by_hyp.setdefault((p.event_id, p.entity, p.field), []).append(p)
    assert all(len(v) <= 24 for v in by_hyp.values())
    first = by_hyp[("EVENT_2_1", "CHAR_001", "body_status")]
    kinds = [p.premise_kind for p in first]
    order = list(QUOTAS)
    assert [order.index(k) for k in kinds] == sorted(order.index(k) for k in kinds)  # kind 순서 유지
    # checker 1 · ledger 1 · knowledge 1 · rule 2 · scene 1 → 남는 칸이 past 로 넘어간다(6 + 12)
    assert Counter(kinds) == {"checker": 1, "ledger": 1, "knowledge": 1, "rule": 2, "past": 18, "scene": 1}
    assert len(first) == 24


def test_pairs_quota_overflow_and_relevance():
    """할당량을 넘는 kind 는 관련도 순으로 잘린다. 동률은 기존 키(ID)."""
    from app.pipeline.pairs import _relevance

    group, led, corpus, reg, checker = _toy(n_past=3)
    reg = dict(reg)
    reg["worldview_rules"] = [{"id": f"RULE_{i:03d}", "content": f"무관한 규칙 {i}"} for i in range(1, 30)] + \
        [{"id": "RULE_099", "content": "슐라우의 body_status: 오른손 멀쩡해야 글을 쓴다"}]
    pairs = build_pairs(group, led, corpus, reg, checker, current_episode=2)
    hyp = [p for p in pairs if p.event_id == "EVENT_2_1" and p.field == "body_status"]
    rules = [p.premise_id for p in hyp if p.premise_kind == "rule"]
    assert rules[0] == "RULE_099"  # 관련도 최고가 ID 순서를 앞지른다
    assert rules[1:] == sorted(rules[1:])  # 나머지는 관련도 동률 → ID 순
    # 할당 4 + 넘어온 칸(ledger 5, knowledge 5) = 14, past·scene 뒤에 남은 3칸은 다시 rule 로
    assert len(rules) == 17 and len(hyp) == 24

    assert _relevance("오른손 멀쩡", "오른손 멀쩡") == 1.0
    assert _relevance("오른손", "점원이 없다") == 0.0
    assert _relevance("", "x") == 0.0


def test_pairs_event_hypothesis_ledger_capped():
    """사건 단위 가설(field=None)의 ledger 전제는 관련도 상위 quotas['ledger']개가 끝."""
    group, led, corpus, reg, checker = _toy(n_past=2)
    for i in range(10):
        led.set("main", "CHAR_001", f"f{i:02d}", f"값{i}", "EVENT_1_1", "explicit")
    pairs = build_pairs(group, led, corpus, reg, checker, current_episode=2)
    ev_hyp = [p for p in pairs if p.event_id == "EVENT_2_1" and p.field is None]
    assert len([p for p in ev_hyp if p.premise_kind == "ledger"]) == 6
    state_hyp = [p for p in pairs if p.event_id == "EVENT_2_1" and p.field == "body_status"]
    assert len([p for p in state_hyp if p.premise_kind == "ledger"]) == 1


def test_pairs_episode_elapsed_and_excerpt():
    group, led, corpus, reg, checker = _toy(n_past=5)
    group["events"][0]["source_text"] = "가" * 400
    corpus[0]["source_text"] = "나" * 400  # EVENT_1_1 = 골절 since 사건
    pairs = build_pairs(group, led, corpus, reg, checker, current_episode=4, excerpt_chars=160)
    hyp = [p for p in pairs if p.event_id == "EVENT_2_1" and p.field == "body_status"]
    h = hyp[0].hypothesis_text
    assert h.startswith("[4화] 슐라우의 body_status")
    assert "원문: " + "가" * 160 + "…" in h and "가" * 161 not in h
    led_p = [p for p in hyp if p.premise_kind == "ledger"][0].premise_text
    assert "since EVENT_1_1 · 1화, 경과 3화" in led_p
    assert "발췌: " + "나" * 160 + "…" in led_p
    # since 이후 같은 엔티티의 가장 이른 사건 1줄(EVENT_1_2), 제목·요약 80자
    tail = led_p.split(" | 이후: ")[1]
    assert tail == "[1화] 과거2: s"
    corpus[1]["summary"] = "깁스를 푼다 " * 20
    led_long = [p for p in build_pairs(group, led, corpus, reg, checker, current_episode=4)
                if p.premise_kind == "ledger" and p.field == "body_status"][0].premise_text
    assert len(led_long.split(" | 이후: ")[1]) == 81
    kn = [p for p in hyp if p.premise_kind == "knowledge"][0].premise_text
    assert "since EVENT_1_2 · 1화, 경과 3화" in kn
    # 사건에 episode 가 있으면 그것이 current_episode 보다 앞선다
    group["events"][0]["episode"] = 5
    h5 = [p for p in build_pairs(group, led, corpus, reg, checker, current_episode=4)
          if p.event_id == "EVENT_2_1"][0].hypothesis_text
    assert h5.startswith("[5화] ")
    # 현재 회차를 모르면 [M화]·경과 없음(since 회차만)
    group["events"][0].pop("episode")
    p0 = build_pairs(group, led, corpus, reg, checker)
    assert not p0[0].hypothesis_text.startswith("[")
    assert "경과" not in " ".join(p.premise_text for p in p0)


def test_hf_backend_rejects_pairs_version_mismatch(tmp_path):
    import json
    (tmp_path / "judge_meta.json").write_text(json.dumps({"pairs_version": "1"}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="pairs_version"):
        LJ.HFBackend(str(tmp_path))
    (tmp_path / "judge_meta.json").write_text("{}", encoding="utf-8")  # 기록 없음도 거부
    with pytest.raises(RuntimeError, match="pairs_version"):
        LJ.HFBackend(str(tmp_path))
    with pytest.raises(ValueError):
        LJ.HFBackend(str(tmp_path), padding="dynamic")


def test_bucket_plan_restores_order():
    lengths = [300, 50, 200, 50, 129, 10, 256, 128]
    plan = LJ.bucket_plan(lengths, batch_size=2, max_length=256)
    assert plan == [(128, [1, 3]), (128, [5, 7]), (256, [0, 2]), (256, [4, 6])]
    assert LJ.bucket_plan(lengths, 2, 256) == plan  # 결정적
    assert LJ.buckets_for(256) == (128, 256) and LJ.buckets_for(512) == (128, 256, 384, 512)
    assert LJ.buckets_for(300) == (128, 256, 300)
    seen: list = []

    def infer(idx, pad):  # noqa: ANN001, ANN202 - 가짜 추론: 쌍 번호와 패딩 길이를 그대로 돌려준다
        seen.append((pad, list(idx)))
        return [[float(i), float(pad), 0.0] for i in idx]

    out = LJ.run_plan(len(lengths), plan, infer)
    assert [r[0] for r in out] == [float(i) for i in range(len(lengths))]
    assert [r[1] for r in out] == [256, 128, 256, 128, 256, 128, 256, 128]
    assert seen == plan
    with pytest.raises(RuntimeError):
        LJ.run_plan(3, [(128, [0, 1])], infer)


def test_judge_group_reports_top2_and_version():
    from app.pipeline.pairs import PAIRS_VERSION
    group, led, corpus, reg, checker = _toy(n_past=3)
    backend = LJ.FakeBackend(fixed={("EVENT_2_1", "EVENT_1_1"): 0.8, ("EVENT_2_2", "RULE_001"): 0.7})
    r = LJ.LearnedJudge(backend, threshold=0.5).judge_group(group, led, corpus, reg, checker, current_episode=2)
    assert r["pairs_version"] == PAIRS_VERSION
    assert r["top2"]["p"] == 0.8 and r["top2"]["id"] == "EVENT_1_1"  # 같은 0.8 의 다음 쌍(ledger)
    assert r["top2"]["kind"] == "ledger" and r["evidence_premise"]["kind"] == "checker"
    assert r["evidence_premise"]["hypothesis"].startswith("[2화] ")
    assert backend.env()["backend"] == "fake"


# ---------------------------------------------------------------- 로드 조건(meta) — 설계안 §0-6

def test_resolve_load_options_defaults_and_meta():
    from app.pipeline.pairs import PAIRS_VERSION
    base = {"pairs_version": PAIRS_VERSION}
    o = LJ.resolve_load_options(base, "cuda")  # meta 에 아무것도 없으면 GPU fp16·eager·bucket·256
    assert (o["precision"], o["attn_implementation"], o["padding"], o["max_length"]) == ("fp16", "eager", "bucket", 384)
    assert o["notes"] == []
    o = LJ.resolve_load_options(base, "cpu")  # CPU 대체 경로는 항상 fp32
    assert o["precision"] == "fp32" and o["notes"]
    m = base | {"precision": "fp32", "attn_implementation": "sdpa", "padding": "max_length", "max_length": 128}
    o = LJ.resolve_load_options(m, "cuda")
    assert (o["precision"], o["attn_implementation"], o["padding"], o["max_length"]) == ("fp32", "sdpa", "max_length", 128)
    assert LJ.resolve_load_options(m, "cpu")["notes"] == []  # fp32 학습 모델을 CPU 로 — 차이 없음
    o = LJ.resolve_load_options(m, "cuda", max_length=256, padding="bucket")  # 인자가 meta 보다 앞선다(경고)
    assert (o["padding"], o["max_length"]) == ("bucket", 256) and any("max_length" in n for n in o["notes"])
    with pytest.raises(ValueError):
        LJ.resolve_load_options(base | {"precision": "bf16"}, "cuda")
    with pytest.raises(ValueError):
        LJ.resolve_load_options(base | {"padding": "dynamic"}, "cuda")


def _fake_torch_modules(monkeypatch, cuda: bool) -> dict:
    """torch·transformers 대역 — HFBackend 가 meta 대로 로드하는지(dtype·attn·half·cudnn) 기록만 한다."""
    import sys
    import types

    rec: dict = {"half": 0, "to": None, "from_pretrained": None}

    class _Model:
        config = types.SimpleNamespace(id2label={0: "모순", 1: "정상", 2: "무관"})

        def half(self):
            rec["half"] += 1
            return self

        def to(self, dev):
            rec["to"] = dev
            return self

        def eval(self):
            return self

    class _AMSC:
        @staticmethod
        def from_pretrained(path, **kw):  # noqa: ANN001, ANN003
            rec["from_pretrained"] = kw
            return _Model()

    class _Tok:
        @staticmethod
        def from_pretrained(path):  # noqa: ANN001
            return object()

    cudnn = types.SimpleNamespace(benchmark=True, version=lambda: 0)
    torch = types.SimpleNamespace(
        cuda=types.SimpleNamespace(is_available=lambda: cuda), float32="float32",
        backends=types.SimpleNamespace(cudnn=cudnn),
        set_num_threads=lambda n: None, set_num_interop_threads=lambda n: None,
        use_deterministic_algorithms=lambda flag: rec.__setitem__("deterministic", flag))
    tf = types.SimpleNamespace(AutoModelForSequenceClassification=_AMSC, AutoTokenizer=_Tok)
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "transformers", tf)
    monkeypatch.setenv("CUBLAS_WORKSPACE_CONFIG", LJ.CUBLAS_WORKSPACE)
    rec["cudnn"] = cudnn
    return rec


@pytest.mark.parametrize("cuda,device,want_half", [(True, "auto", 1), (False, "auto", 0), (True, "cpu", 0)])
def test_hf_backend_loads_with_meta_conditions(tmp_path, monkeypatch, cuda, device, want_half):
    import json
    from app.pipeline.pairs import PAIRS_VERSION
    (tmp_path / "judge_meta.json").write_text(json.dumps(
        {"pairs_version": PAIRS_VERSION, "precision": "fp16", "max_length": 192, "padding": "max_length"}),
        encoding="utf-8")
    rec = _fake_torch_modules(monkeypatch, cuda)
    b = LJ.HFBackend(str(tmp_path), device=device)
    assert rec["from_pretrained"]["attn_implementation"] == "eager"  # meta 에 없으면 eager
    assert rec["half"] == want_half                                   # GPU fp16 → model.half(), CPU → fp32
    assert b.precision == ("fp16" if want_half else "fp32")
    assert (b.max_length, b.padding) == (192, "max_length")           # meta 값
    assert rec["cudnn"].benchmark is False and rec["deterministic"] is True
    assert rec["to"] == ("cuda" if want_half else "cpu")
    b2 = LJ.HFBackend(str(tmp_path), device=device, max_length=256, padding="bucket")  # 설정이 meta 보다 앞선다
    assert (b2.max_length, b2.padding) == (256, "bucket")


# ---------------------------------------------------------------- 쌍 렌더링 v3 (설계안 §0-9 실험 N)

def test_pairs_v3_same_selection_and_v2_unchanged():
    from app.pipeline.pairs import FIELD_KO
    group, led, corpus, reg, checker = _toy(n_past=12)
    corpus[0]["source_text"] = "나" * 400
    v2 = build_pairs(group, led, corpus, reg, checker, current_episode=4)
    assert _digest(v2) == _digest(build_pairs(group, led, corpus, reg, checker, current_episode=4, render="v2"))
    v3 = build_pairs(group, led, corpus, reg, checker, current_episode=4, render="v3")
    key = lambda ps: [(p.premise_kind, p.premise_id, p.entity, p.field, p.event_id) for p in ps]  # noqa: E731
    assert key(v2) == key(v3) and len(v3) == len(v2)
    bad = ("EVENT_", "since", "certainty", "이후:", "발췌:", "body_status", "RULE_0", "CHAR_")
    for p in v3:
        for t in (p.premise_text, p.hypothesis_text):
            assert not any(b in t for b in bad), t
    st = [p for p in v3 if p.field == "body_status"]
    assert st[0].hypothesis_text == f"[4화] “오른손으로” → 슐라우의 {FIELD_KO['body_status']}: 오른손 멀쩡"
    by = {p.premise_kind: p.premise_text for p in st}
    assert by["ledger"].startswith("1화 기준 슐라우의 몸 상태: 오른 손목 골절. 원문: “" + "나" * 160 + "…”")
    assert by["checker"] == "슐라우의 몸 상태: 전제 오른손 멀쩡 / 원장 오른 손목 골절"
    assert by["knowledge"] == "슐라우은(는) 금고 비밀번호을(를) 안다 (1화)"
    assert by["rule"] in ("세계관 규칙: 돈이 없다", "세계관 규칙: 점원이 없다")
    assert by["past"].startswith("[1화] 과거") and ". 원문: “" in by["past"]
    ev = [p for p in v3 if p.event_id == "EVENT_2_1" and p.field is None][0].hypothesis_text
    assert ev == "[4화] 필기: 오른손으로 적는다. 원문: “오른손으로 또박또박 적었다”"
    with pytest.raises(ValueError, match="render"):
        build_pairs(group, led, corpus, reg, checker, render="v4")
    with pytest.raises(ValueError, match="v3"):
        build_pairs(group, led, corpus, reg, checker, quotas=None, render="v3")


def test_pairs_v3_ids_mapped_and_unknown_field_kept():
    group, led, corpus, reg, checker = _toy(n_past=2)
    reg["objects"] = [{"id": "OBJ_002", "name": "장부"}]
    group["events"][0]["assumed_states"].append({"entity": "CHAR_001", "field": "mood", "assumes": "OBJ_002 를 쥠",
                                                 "source_text": "쥐었다"})
    v3 = build_pairs(group, led, corpus, reg, checker, render="v3")
    h = [p.hypothesis_text for p in v3 if p.field == "mood"][0]
    assert h == "“쥐었다” → 슐라우의 mood: 장부 를 쥠"   # 모르는 필드는 원문 그대로, ID → 이름


def test_learned_judge_renders_by_meta_pairs_version(tmp_path):
    import json
    (tmp_path / "judge_meta.json").write_text(json.dumps({"pairs_version": "3"}), encoding="utf-8")
    assert LJ.check_pairs_version({"pairs_version": "3"}, tmp_path) == "3"
    assert LJ.check_pairs_version({"pairs_version": "2"}, tmp_path) == "2"
    group, led, corpus, reg, checker = _toy(n_past=3)
    backend = LJ.FakeBackend(fixed={("EVENT_2_1", "EVENT_1_1"): 0.8})
    backend.pairs_version = "3"
    r = LJ.LearnedJudge(backend, threshold=0.5).judge_group(group, led, corpus, reg, checker, current_episode=2)
    assert r["pairs_version"] == "3" and "since" not in r["evidence_premise"]["text"]
