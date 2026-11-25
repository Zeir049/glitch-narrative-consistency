"""학습 데이터 생성 도구(갈래 A label_pairs · 갈래 B counterfactual) — LLM 없이.

LLM 은 tests/test_weave_pipeline.py 의 FakeLLM 방식(weave_llm.complete 자리를 가짜로)으로 바꾼다. 그래서
코어 complete_json(JSON 추출·재요청) 경로는 실제로 지난다. 네트워크 호출 0회.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EVAL = ROOT / "scripts" / "eval"


def _load(name: str, path: Path):  # noqa: ANN202
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
LP = _load("label_pairs", EVAL / "label_pairs.py")
CF = _load("counterfactual", EVAL / "counterfactual.py")
TJ = _load("train_judge", ROOT / "scripts" / "train_judge.py")

from app.core.config import settings  # noqa: E402
from app.pipeline import weave as W  # noqa: E402


class FakeLLM:
    """weave_llm.complete 대체. 역할별로 응답을 고른다. usage 도 돌려준다(비용 집계 확인)."""

    def __init__(self, label_fn=None, gen=None, det_fn=None):  # noqa: ANN001
        self.label_fn, self.gen, self.det_fn = label_fn, gen, det_fn
        self.calls: list[str] = []

    def __call__(self, system, user, llm, *, role="judge", base=None):  # noqa: ANN001
        self.calls.append(role)
        if role == LP.ROLE:
            data = self.label_fn(user)
        elif role == CF.GEN_ROLE:
            data = self.gen
        elif role == CF.DET_ROLE:
            data = self.det_fn(user)
        else:  # pragma: no cover
            raise AssertionError(role)
        return {"status": "ok", "text": json.dumps(data, ensure_ascii=False),
                "usage": {"input": len(user) // 2, "output": 40}}


@pytest.fixture
def fake_env(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-real")
    monkeypatch.setattr(settings, "weave_llm_provider", "openrouter")
    monkeypatch.setattr(settings, "weave_llm_record", "off")

    def install(fake: FakeLLM) -> FakeLLM:
        monkeypatch.setattr(W.w_llm, "complete", fake)
        return fake
    return install


@pytest.fixture(scope="module")
def wm_pool():
    return LP.pool_rows("wm")


# ================================================================ 갈래 A — 표본

def test_pool_is_deterministic_and_respects_caps(wm_pool):
    again = LP.pool_rows("wm")
    assert [r["pair_id"] for r in again] == [r["pair_id"] for r in wm_pool]
    assert len({r["pair_id"] for r in wm_pool}) == len(wm_pool)
    assert {r["episode"] for r in wm_pool} <= {2, 3, 4, 5}
    small = LP.pool_rows("wm", top_n=3, scene_cap=10)
    per_hyp: dict = {}
    per_scene: dict = {}
    for r in small:
        hk = (r["group_id"], r["event_id"], r["entity"], r["field"], r["hypothesis"])
        per_hyp[hk] = per_hyp.get(hk, 0) + 1
        per_scene[r["group_id"]] = per_scene.get(r["group_id"], 0) + 1
    assert max(per_hyp.values()) <= 3 and max(per_scene.values()) <= 10


def test_pool_cuts_future_episodes(wm_pool):
    # k화 쌍의 past 전제는 k화 이전 사건만, rule 전제는 first_episode < k 만
    for r in wm_pool:
        if r["premise_kind"] == "past":
            m = re.match(r"EVENT_(\d+)_", r["premise_id"])
            if m:
                assert int(m.group(1)) < r["episode"]


def test_stratified_sample_deterministic_and_proportional(wm_pool):
    a = LP.stratified_sample(wm_pool, 200, seed=7)
    b = LP.stratified_sample(list(reversed(wm_pool)), 200, seed=7)
    c = LP.stratified_sample(wm_pool, 200, seed=8)
    assert [r["pair_id"] for r in a] == [r["pair_id"] for r in b]  # 입력 순서와 무관
    assert [r["pair_id"] for r in a] != [r["pair_id"] for r in c]
    assert len(a) == 200
    from collections import Counter
    pool_k, samp_k = Counter(r["premise_kind"] for r in wm_pool), Counter(r["premise_kind"] for r in a)
    for k, n in pool_k.items():
        assert abs(samp_k[k] - 200 * n / len(wm_pool)) <= 1
    assert len(LP.stratified_sample(wm_pool, 10 ** 6, seed=1)) == len(wm_pool)


def test_allocate_largest_remainder():
    assert LP.allocate({"a": 10, "b": 10}, 5) in ({"a": 3, "b": 2}, {"a": 2, "b": 3})
    assert sum(LP.allocate({"a": 7, "b": 2, "c": 1}, 6).values()) == 6
    assert LP.allocate({"a": 1, "b": 100}, 50)["a"] <= 1
    assert LP.allocate({"a": 3}, 10) == {"a": 3}


# ================================================================ 갈래 A — 라벨 병합

def _lab(label, conf=0.9, status="ok"):  # noqa: ANN001, ANN202
    return {"status": status, "label": label, "confidence": conf, "reason": "", "evidence_quote": ""}


@pytest.mark.parametrize("a,b,expect", [
    (_lab("모순"), _lab("모순", 0.7), ("모순", None, 0.8)),
    (_lab("정상"), _lab("무관"), ("제외", "불일치", None)),
    (_lab("판단불가"), _lab("판단불가"), ("제외", "판단불가", None)),
    (_lab("모순"), _lab("판단불가"), ("제외", "판단불가", None)),
    (_lab(None, None, "failed"), _lab("무관"), ("제외", "실패", None)),
    (_lab("x", None, "bad_label"), _lab("무관"), ("제외", "실패", None)),
])
def test_merge_labels(a, b, expect):  # noqa: ANN001
    assert LP.merge_labels(a, b) == expect


def test_merge_labels_min_confidence():
    assert LP.merge_labels(_lab("무관", 0.4), _lab("무관", 0.9), min_confidence=0.5) == ("제외", "저신뢰", None)
    assert LP.merge_labels(_lab("무관", None), _lab("무관", 0.9), min_confidence=0.5)[1] == "저신뢰"
    assert LP.merge_labels(_lab("무관", 0.6), _lab("무관", 0.9), min_confidence=0.5)[0] == "무관"


def test_parse_label_and_agreement():
    ok = LP.parse_label({"status": "ok", "data": {"label": "모순", "confidence": "1.4", "reason": "r"}})
    assert ok["status"] == "ok" and ok["confidence"] == 1.0
    assert LP.parse_label({"status": "ok", "data": {"label": "contradiction"}})["status"] == "bad_label"
    assert LP.parse_label({"status": "error", "data": None, "message": "x"})["status"] == "failed"
    ag = LP.agreement([(_lab("모순"), _lab("모순")), (_lab("무관"), _lab("정상")),
                       (_lab(None, None, "failed"), _lab("무관"))])
    assert ag["n_both_ok"] == 2 and ag["agreement"] == 0.5


def test_prompt_variants_differ_but_carry_same_text():
    u0, u1 = LP.user_prompt("P", "H", 0), LP.user_prompt("P", "H", 1)
    assert u0 != u1 and u0.index("[전제]") < u0.index("[가설]") and u1.index("[가설]") < u1.index("[전제]")
    for lab in ("모순", "정상", "무관", "판단불가"):
        assert lab in LP.SYSTEM


# ================================================================ 갈래 A — 종단(가짜 LLM) + 스키마 호환

def test_label_pairs_main_writes_train_compatible_jsonl(tmp_path, fake_env):
    def label_fn(user: str) -> dict:
        # 검수 요망(checker) 전제 → 두 변형 모두 모순. 규칙 전제 → 변형 0 은 무관, 1 은 정상(불일치 → 제외)
        variant = 0 if user.index("[전제]") < user.index("[가설]") else 1
        if "[검수 요망]" in user:
            return {"label": "모순", "confidence": 0.9, "reason": "r", "evidence_quote": "q"}
        if "세계관 규칙" in user:
            return {"label": "무관" if variant == 0 else "정상", "confidence": 0.8, "reason": "r"}
        return {"label": "무관", "confidence": 0.95, "reason": "r", "evidence_quote": ""}

    fake = fake_env(FakeLLM(label_fn=label_fn))
    out = tmp_path / "wm.jsonl"
    LP.main(["--work", "wm", "--target", "60", "--out", str(out), "--concurrency", "2", "--run"])
    rows = TJ.read_jsonl(out)
    excluded = TJ.read_jsonl(out.with_suffix(".excluded.jsonl"))
    meta = json.loads(out.with_suffix(".meta.json").read_text(encoding="utf-8"))
    assert len(rows) + len(excluded) == 60 and len(fake.calls) == 120
    # train_judge.py 가 읽는 필드
    for r in rows:
        assert r["label"] in TJ.LABELS and TJ.L2I[r["label"]] in (0, 1, 2)
        assert isinstance(r["premise"], str) and isinstance(r["hypothesis"], str)
        assert r["source"] == "llm_label" and r["split"] == "train" and r["case_id"] == r["group_id"]
    # export_pairs.py 행 필드를 모두 가진다
    for k in ("premise", "hypothesis", "label", "premise_kind", "premise_id", "event_id", "entity", "field",
              "case_id", "contrast_group", "source", "split", "work", "scene_id", "episode", "group_id"):
        assert all(k in r for r in rows), k
    tr, va = TJ.split_val(rows, 0.2, 1)  # 분할 단위(case_id)로 갈린다
    assert {r["case_id"] for r in tr}.isdisjoint({r["case_id"] for r in va})
    assert all(r["label"] == "제외" and r["excluded_reason"] == "불일치" for r in excluded)
    assert all(r["premise_kind"] == "rule" for r in excluded)
    assert meta["usage"]["calls"] == 120 and meta["usage"]["input_tokens"] > 0
    assert meta["usage"]["cost"]["usd"] == 0.0  # 설정 모델(:free)
    assert meta["agreement"]["n_both_ok"] == 60


def test_cost_estimate_uses_price_table(wm_pool):
    rows = wm_pool[:10]
    est = LP.estimate(rows, ["claude-haiku-4-5"], chars_per_token=2.0, out_tokens=100, price=(None, None))
    assert est["calls"] == 20 and est["output_tokens"] == 2000
    assert est["usd"] == pytest.approx(est["input_tokens"] / 1e6 * 1.0 + 2000 / 1e6 * 5.0, abs=1e-4)
    unk = LP.estimate(rows, ["some/unknown-model"], chars_per_token=2.0, out_tokens=100, price=(None, None))
    assert unk["unpriced_models"] == ["some/unknown-model", "some/unknown-model"]
    assert C.price_of("x/y:free") == (0.0, 0.0) and C.price_of("x", (1.0, 2.0)) == (1.0, 2.0)


# ================================================================ 갈래 B — 사실 후보·자기일관성

@pytest.fixture(scope="module")
def q1_facts():
    eps, reg, _ = C.load_episodes("q1")
    return CF.fact_candidates(eps, reg, 4), eps, reg


def test_fact_candidates_deterministic_and_bounded(q1_facts):
    facts, eps, reg = q1_facts
    again = CF.fact_candidates(eps, reg, 4)
    assert [f.fact_id for f in again] == [f.fact_id for f in facts]
    assert [f.score for f in facts] == sorted((f.score for f in facts), reverse=True)
    assert all(f.since_episode is None or f.since_episode <= 4 for f in facts)
    assert all(f.field != "location" for f in facts)
    a = CF.pick_fact(facts, seed=3)
    assert a == CF.pick_fact(facts, seed=3)
    band = CF.middle_band(facts, (0.3, 0.7), 2)
    assert a in CF.middle_band([f for f in facts if f.kind == a.kind], (0.3, 0.7), 2)
    assert CF.pick_fact(facts, pick=0) == band[0]
    assert CF.pick_fact(facts, pick=0, exclude={band[0].fact_id}) != band[0]


def _det(has, no):  # noqa: ANN001, ANN202
    return {"status": "ok", "data": {"has_contradiction": has, "fact_no": no}}


def test_self_consistency_rule():
    hits4 = [_det(True, 3)] * 4 + [_det(False, None)]
    wrong_no = [_det(True, 2)] * 5
    clean = [_det(False, None)] * 4 + [_det(True, 3)]
    dirty = [_det(False, None)] * 3 + [_det(True, 3)] * 2
    failed = [{"status": "error", "data": None}] * 2 + [_det(False, None)] * 3
    assert CF.judge_runs(hits4, 3) == {"runs": 5, "ok": 5, "flagged": 4, "target_hits": 4}
    assert CF.judge_runs(wrong_no, 3)["target_hits"] == 0
    assert CF.self_consistency(CF.judge_runs(hits4, 3), CF.judge_runs(clean, 3))["passed"] is True
    assert CF.self_consistency(CF.judge_runs(hits4[1:] + [_det(False, None)], 3),
                               CF.judge_runs(clean, 3))["passed"] is False      # 3/5
    assert CF.self_consistency(CF.judge_runs(hits4, 3), CF.judge_runs(dirty, 3))["passed"] is False   # 대조 2회 오탐
    assert CF.self_consistency(CF.judge_runs(hits4, 3), CF.judge_runs(failed, 3))["passed"] is False  # 대조 유효 3회
    assert CF.judge_runs([{"status": "ok", "data": {"has_contradiction": "true", "fact_no": "3"}}], 3)["target_hits"] == 1


def test_validate_generation():
    good = {"contradiction": {"text": "가 나. 위반 문장. 다.", "seeded_text": "위반 문장."},
            "control": {"text": "가 나. 지킨 문장. 다.", "kept_text": "지킨 문장."}}
    assert CF.validate_generation(good) == (True, "")
    assert not CF.validate_generation(None)[0]
    bad = json.loads(json.dumps(good))
    bad["contradiction"]["seeded_text"] = "없는 문장"
    assert not CF.validate_generation(bad)[0]
    same = json.loads(json.dumps(good))
    same["control"]["text"] = good["contradiction"]["text"]
    assert not CF.validate_generation(same)[0]


def test_counterfactual_case_end_to_end(tmp_path, fake_env):
    common = dict(sources=None, model="nvidia/nemotron-3-ultra-550b-a55b:free", seed=11, pick=None,
                  band=(0.3, 0.7), min_refs=2, kinds=("state", "knowledge", "rule"), sc_runs=5, sc_min=4,
                  control_max=1, sc_temperature=0.7)
    src = tmp_path / "src"
    src.mkdir()
    (src / "episode_04.txt").write_text("4화 원문 끝부분. " * 50, encoding="utf-8")
    common["sources"] = src
    plan = CF.run_case("q1", 4, out_dir=None, dry_run=True, **common)
    target_no = plan["detector_target_no"]
    gen = {"contradiction": {"text": "장면 하나. 모순 문장이다. 끝.", "seeded_text": "모순 문장이다.",
                             "violation": "사실을 뒤집음"},
           "control": {"text": "장면 하나. 지키는 문장이다. 끝.", "kept_text": "지키는 문장이다."}}

    def det_fn(user: str) -> dict:
        return ({"has_contradiction": True, "fact_no": target_no, "quote": "모순 문장이다."}
                if "모순 문장이다" in user else {"has_contradiction": False, "fact_no": None})

    fake = fake_env(FakeLLM(gen=gen, det_fn=det_fn))
    usage = C.Usage()
    out = tmp_path / "cf" / "q1"
    entry = CF.run_case("q1", 4, out_dir=out, dry_run=False, usage=usage, **common)
    assert fake.calls == [CF.GEN_ROLE] + [CF.DET_ROLE] * 10
    assert entry["passed"] is True and entry["human_verified"] is None
    assert usage.calls == 11 and usage.output_tokens == 440
    labels = json.loads((out / "labels.json").read_text(encoding="utf-8"))["labels"]
    assert [lab["scene_id"] for lab in labels] == ["scene_01", "scene_02"]
    c, n = labels
    assert c["has_contradiction"] is True and c["seeded_text"] == "모순 문장이다."
    assert c["type"] in ("상태", "지식", "설정") and c["contrast_group"] == n["contrast_group"] == entry["case_id"]
    assert set(c) >= {"seeded_text", "violated_fact", "evidence_event_ids", "type"}
    assert (c["evidence_event_ids"] == [entry["fact"]["since"]]) if entry["fact"]["since"] else c["evidence_rule_ids"]
    assert n["has_contradiction"] is False and n["evidence_event_ids"] == []
    assert (out / "scenes" / "scene_01.txt").read_text(encoding="utf-8") == gen["contradiction"]["text"]
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["cases"][0]["scenes"] == {"contradiction": "scene_01", "control": "scene_02"}
    # 두 번째 사례: 장면 번호를 잇고, 이미 쓴 사실은 다시 고르지 않는다
    entry2 = CF.run_case("q1", 4, out_dir=out, dry_run=False, **(common | {"seed": 11}))
    assert entry2["scenes"]["contradiction"] == "scene_03"
    assert entry2["fact"]["fact_id"] != entry["fact"]["fact_id"]


def test_label_rates_report_exclusions():
    def row(label, why, a="무관", b="무관", st="ok"):  # noqa: ANN001, ANN202
        return {"label": label, "excluded_reason": why,
                "llm_labels": [{"status": st, "label": a}, {"status": "ok", "label": b}]}
    rates = LP.label_rates([row("모순", None, "모순", "모순"), row("무관", None),
                            row("제외", "불일치", "모순", "정상"), row("제외", "실패", None, "무관", "failed")])
    assert rates["contradiction_rate_kept"] == 0.5 and rates["excluded_rate"] == 0.5
    assert rates["contradiction_any_labeler"] == 2
    assert rates["disagreement_rate_among_both_ok"] == round(1 / 3, 4) and rates["failed_rate"] == 0.25


def test_record_mode_reuses_recorded_calls(tmp_path, monkeypatch, fake_env):
    from app.pipeline.llm_record import LLMRecorder
    inner = FakeLLM(label_fn=lambda u: {"label": "무관", "confidence": 0.9, "reason": "r"})
    rec = LLMRecorder(inner, mode="record", root=tmp_path / "rec")
    monkeypatch.setattr(W, "llm_recorder", rec)
    fake_env(rec)
    monkeypatch.setattr(settings, "weave_llm_record", "record")
    llm = C.llm_for("m:free")
    u = C.Usage()
    a = C.call_json("S", "U", llm, role=LP.ROLE, usage=u)
    b = C.call_json("S", "U", llm, role=LP.ROLE, usage=u)  # 녹화본 재사용 — 안쪽 호출 없음
    assert a["data"] == b["data"] and b["reused_recording"] is True
    assert len(inner.calls) == 1 and u.reused == 1 and u.calls == 2
    C.call_json("S", "U", llm, role=LP.ROLE, usage=u, occurrence=1)  # 두 번째 녹화 없음 → 호출
    assert len(inner.calls) == 2


def test_counterfactual_marks_train_only(tmp_path):
    case = {"case_id": "cf_x", "work": "q1", "target_episode": 5, "model": "m",
            "fact": {"fact_id": "state:C.alive", "kind": "state", "text": "t", "since": "EVENT_1_1",
                     "since_episode": 1}}
    gen = {"contradiction": {"text": "a", "seeded_text": "a"}, "control": {"text": "b", "kept_text": "b"}}
    entry = CF.save_case(tmp_path, case, gen, {})
    labels = json.loads((tmp_path / "labels.json").read_text(encoding="utf-8"))["labels"]
    assert entry["intended_use"] == "train_only" and all(lab["intended_use"] == "train_only" for lab in labels)
    assert labels[1]["rewritten"] is True and all(lab["work"] == "q1" for lab in labels)


# ================================================================ label_pairs 가드·녹화 모델 (검증 기록 §E-12)

def test_label_pairs_bulk_guard(tmp_path, fake_env):
    fake = fake_env(FakeLLM(label_fn=lambda u: {"label": "무관", "confidence": 0.9, "reason": "r"}))
    with pytest.raises(SystemExit, match="--run"):
        LP.main(["--work", "wm", "--target", str(LP.TRIAL_MAX + 1), "--out", str(tmp_path / "x.jsonl")])
    assert fake.calls == [] and not (tmp_path / "x.jsonl").exists()
    est = {"calls": 0, "usd": 0, "input_tokens": 0, "output_tokens": 0}
    LP.guard_run_size(LP.TRIAL_MAX, False, est)      # 시험 규모까지는 통과
    LP.guard_run_size(LP.TRIAL_MAX + 1, True, est)   # --run 이면 통과


def test_label_pairs_estimate_defaults_match_readme():
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        LP.main(["--work", "wm", "--target", "5", "--dry-run"])
    est = json.loads(buf.getvalue())["estimate"]
    assert est["chars_per_token"] == 2.4 and est["out_tokens_per_call"] == 95
    readme = (EVAL / "README.md").read_text(encoding="utf-8")
    assert "출력 95 토큰, cpt 2.4" in readme


def test_label_pairs_meta_tokens_per_call_uses_calls_with_usage():
    assert LP.per_call(950, 10) == 95.0 and LP.per_call(0, 0) is None


def test_record_reuse_misses_on_model_mismatch(tmp_path, monkeypatch, fake_env):
    from app.pipeline.llm_record import LLMRecorder
    inner = FakeLLM(label_fn=lambda u: {"label": "무관", "confidence": 0.9, "reason": "r"})
    rec = LLMRecorder(inner, mode="record", root=tmp_path / "rec")
    monkeypatch.setattr(W, "llm_recorder", rec)
    fake_env(rec)
    monkeypatch.setattr(settings, "weave_llm_record", "record")
    a, b = C.llm_for("model-a:free"), C.llm_for("model-b:free")
    C.call_json("S", "U", a, role=LP.ROLE)
    assert len(inner.calls) == 1
    r = C.call_json("S", "U", b, role=LP.ROLE)                 # 다른 모델 → miss, 새로 부른다
    assert len(inner.calls) == 2 and not r.get("reused_recording")
    assert C.call_json("S", "U", b, role=LP.ROLE)["reused_recording"] is True   # 이제 b 녹화가 있다
    assert C.call_json("S", "U", a, role=LP.ROLE)["reused_recording"] is True   # a 녹화도 그대로
    assert len(inner.calls) == 2
    assert C.recorded_response(LP.ROLE, "S", "U", 0, model="model-c:free") is None
    assert C.recorded_response(LP.ROLE, "S", "U", 1) is not None                 # model 없이는 전부 센다


# ================================================================ export_pairs — 평가와 같은 재생 (설계안 §3-1)

XP = _load("export_pairs", EVAL / "export_pairs.py")
INJ = _load("inject", EVAL / "inject.py")


@pytest.mark.parametrize("work", ["wm", "q1", "q3", "sherlock"])
def test_export_replay_matches_load_context(work):
    base = XP.base_replay(work)
    ctx = JE.load_context(work)
    assert XP.ledger_digest(base.ledger) == XP.ledger_digest(ctx.ledger)
    assert base.corpus == ctx.corpus and base.registry == ctx.registry and base.mapping == ctx.mapping


def test_export_real_uses_harness_packets_and_labels():
    from app.pipeline.pairs import build_pairs
    ctx = JE.load_context("q1")
    rows = XP.export_real("q1", keyed_only=False, ctx=ctx)
    # 하네스(LearnedJudge.votes)와 같은 패킷: 장면 하나 = group 하나 → 쌍 수가 같다
    n = sum(len(build_pairs({"key": sid, "unit": "scene", "events": evs}, ctx.ledger, ctx.corpus, ctx.registry,
                            list(ctx.checker_items.get(sid, [])))) for sid, evs in ctx.scenes.items())
    assert len(rows) == n
    for k in ("work", "scene_id", "group_id", "scene_positive"):
        assert all(k in r for r in rows), k
    assert {r["work"] for r in rows} == {"q1"} and all(r["group_id"] == f"q1:{r['scene_id']}" for r in rows)
    labels = XP.load_scene_labels("q1")
    ev = XP.evidence_ids("q1", labels)
    for r in rows:
        lab = labels[r["scene_id"]]
        assert r["scene_positive"] == bool(lab["has_contradiction"])
        if r["label"] == "모순":
            assert r["premise_id"] in ev[r["scene_id"]]
        if lab["has_contradiction"] and lab.get("type") == "설정" and r["premise_kind"] == "rule":
            assert r["label"] == XP.EXCLUDED
        if not lab["has_contradiction"]:
            assert r["label"] in ("정상", "무관")
    c = Counter(r["label"] for r in rows)
    assert c["모순"] > 0 and c[XP.EXCLUDED] > 0
    assert all(r["label_version"] == XP.LABEL_VERSION == 2 for r in rows)
    # v2 는 양성 장면만 바꾼다: 정상 장면은 v1 그대로, v2 '모순' ⊂ v1 '모순', v1 '제외'는 그대로 '제외'
    for r in rows:
        if not r["scene_positive"]:
            assert r["label"] == r["label_v1"]
        if r["label"] == "모순":
            assert r["label_v1"] == "모순" and r["keyed"]
        if r["label_v1"] == XP.EXCLUDED:
            assert r["label"] == XP.EXCLUDED
    # 제외 = v1 제외 조건(근거와 같은 (엔티티, 필드) · 근거 사건을 가리킴 · 설정 장면 규칙) 또는 근거 전제 또는 가설 키
    for sid in {r["scene_id"] for r in rows if r["scene_positive"]}:
        srows = [r for r in rows if r["scene_id"] == sid]
        keys = {(r["entity"], r["field"]) for r in srows if r["premise_id"] in ev[sid]}
        seeded = labels[sid].get("seeded_text")
        for r in srows:
            if r["label"] == XP.EXCLUDED and r["premise_kind"] != "rule":
                assert ((r["entity"], r["field"]) in keys or any(e in r["premise"] for e in ev[sid])
                        or XP.hypothesis_key(r["hypothesis"], r["field"], seeded))
    v1 = XP.export_real("q1", keyed_only=False, ctx=ctx, label_version=1)
    assert [r["label"] for r in v1] == [r["label_v1"] for r in rows]
    assert [(r["premise"], r["hypothesis"]) for r in v1] == [(r["premise"], r["hypothesis"]) for r in rows]


# ---------------------------------------------------------------- 라벨 v2 (가설 키 AND 전제 키) — 작은 고정 사례

def _pair(premise, hypothesis, *, pid, kind="knowledge", ent="A", field="knows", eid="E9"):  # noqa: ANN001, ANN202
    from types import SimpleNamespace
    return SimpleNamespace(premise_text=premise, hypothesis_text=hypothesis, premise_kind=kind, premise_id=pid,
                           event_id=eid, entity=ent, field=field)


SEED = "블루멘이 의사였어요? 전혀 몰랐네요."
H_KEY = "[6화] 알파의 knows: not_knows: 블루멘 직업 (근거: 블루멘이 의사였어요?  전혀 몰랐네요!) | 사건: 요약 | 원문: 아무 말"
H_OTHER = f"[6화] 알파의 location: 광장 (근거: 광장에 서 있었다.) | 사건: 요약 | 원문: 광장에 서 있었다. {SEED}"
H_PLAIN = "[6화] 알파의 possesses: 공 (근거: 공을 쥐었다.) | 사건: 요약 | 원문: 공을 쥐었다."
H_EVENT = f"[6화] 사건 알파의 대화 | 사건: 요약 | 원문: 그는 말했다. {SEED} 그리고 떠났다."


def test_hypothesis_key_normalization_and_target():
    assert XP.hypothesis_key(H_KEY, "knows", SEED)                 # 공백·구두점 정규화 뒤 포함
    assert not XP.hypothesis_key(H_OTHER, "location", SEED)        # 상태 가설은 근거 인용만 본다(원문에 있어도)
    assert XP.hypothesis_key(H_EVENT, None, SEED)                  # 사건 가설은 원문
    assert XP.hypothesis_target(H_EVENT, None).startswith("그는 말했다")
    # 인용이 seeded_text 의 한 문장(8자 이상)만 가져온 경우
    assert XP.hypothesis_key("[6화] 알파의 knows: x (근거: 블루멘이 의사였어요?) | 원문: -", "knows", SEED)
    assert not XP.hypothesis_key("[6화] 알파의 knows: x (근거: 의사였어요) | 원문: -", "knows", SEED)
    # 2-gram 포함률 τ_h
    part = "[6화] 알파의 knows: x (근거: 블루멘이 의사였다고? 전혀 몰랐네) | 원문: -"  # 포함 아님, 포함률 10/14
    assert XP.hypothesis_key(part, "knows", SEED, tau_h=0.5) and not XP.hypothesis_key(part, "knows", SEED, tau_h=0.95)
    assert not XP.hypothesis_key(H_KEY, "knows", None) and not XP.hypothesis_key(H_KEY, "knows", "")


def test_premise_head_strips_provenance():
    p = "알파이(가) 앎: 블루멘의 직업이 의사임 (via told, since EVENT_5_9 · 5화) | 발췌: 긴 발췌"
    assert XP.premise_head(p) == "알파이(가) 앎: 블루멘의 직업이 의사임"
    assert XP.dice("가나다", "가나다") == 1.0 and XP.dice("가나", "다라") == 0.0


def test_label_scene_v2_three_tiers():
    lab = {"has_contradiction": True, "type": "지식", "seeded_text": SEED,
           "violated_fact": "알파는 5화에서 블루멘 본인에게 직접 의사임을 들었다.",
           "evidence_quotes": [{"quote": "저는 의사예요."}]}
    ev = {"E5", "E6"}
    p_good = "알파이(가) 앎: 블루멘 본인에게 직접 의사임을 들었다 (via told, since E5 · 5화) | 발췌: …"
    p_weak = "알파이(가) 앎: 광장 위치는 북쪽 (via told, since E5 · 5화) | 발췌: …"
    p_other = "알파이(가) 앎: 날씨가 맑음 (via told, since E1 · 1화) | 발췌: …"
    pairs = [_pair(p_good, H_KEY, pid="E5"),                              # 가설 키 + 전제 키 → 모순
             _pair(p_weak, H_KEY, pid="E5"),                              # 근거 사건이지만 Dice 미달 → 제외
             _pair(p_other, H_KEY, pid="E1"),                             # 가설 키만 → 제외
             _pair(p_good, H_OTHER, pid="E5", ent="B", field="location"),  # 전제 키만 → 제외
             _pair(p_other, H_PLAIN, pid="E1", ent="C", field="possesses"),  # 둘 다 아님 → 무관
             _pair("[검수 요망] 알파.knows — 전제 x / 원장 y", H_EVENT, pid="E6", kind="checker", ent=None, field=None),
             _pair(p_weak, H_EVENT, pid="E6", ent=None, field=None)]      # 그 가설의 근거 전제(checker 빼고) 하나뿐 → 키
    got = [lb for lb, _k in XP.label_scene_v2(pairs, lab, ev)]
    assert got == ["모순", XP.EXCLUDED, XP.EXCLUDED, XP.EXCLUDED, "무관", "모순", "모순"]
    assert [k for _l, k in XP.label_scene_v2(pairs, lab, ev)] == [True, False, False, False, False, True, True]
    # τ_p 를 올리면 Dice 로 키가 된 쌍만 제외로 내려간다(유일·checker 는 그대로)
    hi = [lb for lb, _k in XP.label_scene_v2(pairs, lab, ev, tau_p=0.99)]
    assert hi == [XP.EXCLUDED, XP.EXCLUDED, XP.EXCLUDED, XP.EXCLUDED, "무관", "모순", "모순"]


def test_label_scene_v2_keeps_negative_scenes_and_v1_exclusions():
    neg = {"has_contradiction": False, "seeded_text": SEED}
    pairs = [_pair("알파.knows = x (since E5)", H_KEY, pid="E5", kind="ledger"), _pair("규칙", H_KEY, pid="R1", kind="rule")]
    assert XP.label_scene_v2(pairs, neg, {"E5"}) == [("정상", False), ("무관", False)]
    setting = {"has_contradiction": True, "type": "설정", "seeded_text": "전혀 다른 문장입니다", "violated_fact": ""}
    out = XP.label_scene_v2([_pair("세계관 규칙 R2: 돈이 없다", H_OTHER, pid="R2", kind="rule")], setting, {"E5"})
    assert out == [(XP.EXCLUDED, False)]                                   # v1 제외(설정 장면 규칙)는 그대로


def test_export_real_rejects_unlabeled_work():
    with pytest.raises(SystemExit):
        XP.export_real("dracula", keyed_only=False)


def test_export_inject_on_work_replay():
    base = XP.base_replay("wm")
    manifest = INJ.generate(base.registry, per_operator=2, seed=7, operators=["state_contrast", "knowledge_contrast"])
    rows = XP.export_inject("wm", manifest, keyed_only=False, holdout=set(), base=base)
    assert rows and {r["work"] for r in rows} == {"wm"}
    assert all(r["group_id"] == f"wm:{r['contrast_group']}" and r["scene_id"] == r["case_id"] for r in rows)
    keyed = [r for r in rows if r["keyed"]]
    assert {r["label"] for r in keyed} >= {"모순", "정상"}
    # 주입 사건이 기반 사건과 같은 원장 칸에 들어가 checker needs_judge 전제가 나온다
    assert XP.inject_timeline(base) == "future_world" and XP.inject_timeline(XP.base_replay("q1")) == "main"
    assert any(r["premise_kind"] == "checker" and r["label"] == "모순" for r in keyed)
    assert all(r["scene_positive"] == any(x["label"] == "모순" for x in rows if x["case_id"] == r["case_id"]
                                          and x["keyed"]) for r in rows)
    # 같은 입력 → 같은 행
    assert XP.export_inject("wm", manifest, keyed_only=False, holdout=set(), base=base) == rows


def test_inject_registry_resolution_and_cli(tmp_path):
    """inject CLI 가 registry_live.json 도 연다 — 학습 작품 레지스트리 기반 주입(누수 규칙, 설계안 §0-6)."""
    import hashlib
    corpus = ROOT / "tests" / "eval" / "weave_judge_corpus"
    live = corpus / "sherlock" / "ledger_v2_3" / "registry_live.json"
    assert INJ.find_registry(base=corpus / "sherlock" / "ledger_v2_3") == live          # base 자동 탐지
    assert INJ.find_registry(work="sherlock") == live                                  # judge_eval 레지스트리
    assert INJ.find_registry(base=ROOT / "tests" / "eval" / "weave_checker_corpus").name == "entity_registry.json"
    assert INJ.find_registry(registry=live, work="wm") == live                         # --registry 가 앞선다
    for kw in ({}, {"base": tmp_path}, {"work": "nope"}, {"registry": tmp_path / "x.json"}):
        with pytest.raises(SystemExit):
            INJ.find_registry(**kw)
    out = tmp_path / "m.json"
    INJ.main(["--work", "sherlock", "--out", str(out), "--per-operator", "1", "--seed", "7",
              "--operators", "state_contrast", "knowledge_contrast"])
    m = json.loads(out.read_text(encoding="utf-8"))
    assert m["work"] == "sherlock" and m["registry_sha256"] == hashlib.sha256(live.read_bytes()).hexdigest()
    names = {str(c["id"]) for c in json.loads(live.read_text(encoding="utf-8"))["characters"]}
    used = {cid for case in m["cases"] for ev in case["test_events"] for cid in ev["participant_character_ids"]}
    assert used and used <= names                                                       # 셜록 인물만
