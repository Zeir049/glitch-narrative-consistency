"""LLM 녹화·재생 래퍼(app/pipeline/llm_record.py) 테스트 — 네트워크 없음.

1. record → replay 왕복: 같은 응답, 안쪽 함수는 replay 에서 0회.
2. 어댑터 경로 전체(추출 1·2화 + 검수 2화)를 record 로 한 번 돌리고, replay 에서는
   `urllib.request.urlopen` 을 터지는 함수로 바꾼 채 **실제 provider 경로**(_complete_resilient)를 안쪽에 두고
   다시 돌려도 결과가 같다 → complete_json 이 모듈 전역 complete 를 부르므로 교체만으로 추출·판정 양쪽에 적용된다.
3. miss 는 즉시 ReplayMiss(키·role·프롬프트 앞 200자).
"""
from __future__ import annotations

import json
import urllib.request

import pytest

from app.core.config import settings
from app.pipeline import llm_record as R
from app.pipeline import weave as W
from tests.test_weave_pipeline import CH1, CH2, DOC1, DOC2, FakeLLM, _snapshot


def _bomb(*a, **k):  # noqa: ANN002, ANN003
    raise AssertionError("replay 모드에서 네트워크 호출이 일어났다")


class Counting:
    def __init__(self, texts: list[str]):
        self.texts, self.n = texts, 0

    def __call__(self, system, user, llm, *, role="judge", base=None):  # noqa: ANN001
        t = self.texts[self.n % len(self.texts)]
        self.n += 1
        return {"status": "ok", "text": t, "usage": {"input": 1, "output": 1}}


LLM = {"provider": "openrouter", "model": "m-default", "judge_model": "m-judge"}

# 이 파일의 가정(LLM 판정 경로, 관문 꺼짐, 녹화 꺼짐)을 기본값으로 고정한다 — .env·다른 테스트가 무엇을 두든.
_PINNED = ("weave_llm_record", "weave_judge_mode", "weave_gate_mode")


@pytest.fixture(autouse=True)
def _isolate_recorder(tmp_path, monkeypatch):
    """모듈 전역 llm_recorder 의 커서·캐시와 녹화 관련 설정을 테스트마다 비운다.

    2026-11-06 간헐 실패의 원인은 벽시계 초 경계였다(_freeze_clock 참고). 이 fixture 는 그와 별개로
    전역 recorder 상태·settings 누수가 순서 의존을 만들지 않게 막는 격리 장치다.
    settings 는 monkeypatch 가 끝나면 원래 값으로 돌린다.
    """
    fields = type(settings).model_fields
    for name in _PINNED:
        monkeypatch.setattr(settings, name, fields[name].default)
    monkeypatch.setattr(settings, "weave_llm_record_dir", str(tmp_path / "_default_rec"))
    W.llm_recorder.reset()
    yield
    W.llm_recorder.reset()


def test_record_then_replay_roundtrip(tmp_path, monkeypatch):
    inner = Counting(['{"verdict": "모순"}'])
    rec = R.LLMRecorder(inner, mode="record", root=tmp_path)
    out = rec("SYS", "USER 프롬프트", LLM, role="judge")
    assert out["text"] == '{"verdict": "모순"}' and inner.n == 1

    sha = R.prompt_sha256("SYS", "USER 프롬프트")
    doc = json.loads((tmp_path / "judge" / sha[:2] / f"{sha}.json").read_text(encoding="utf-8"))
    assert doc["key"] == f"judge:{sha}" and doc["role"] == "judge"
    assert doc["system"] == "SYS" and doc["user"] == "USER 프롬프트"
    e = doc["entries"][0]
    assert e["response"] == out and e["model"] == "m-judge" and e["provider"] == "openrouter"
    assert e["recorded_at"].endswith("Z")

    monkeypatch.setattr(urllib.request, "urlopen", _bomb)
    never = Counting(["x"])
    rep = R.LLMRecorder(never, mode="replay", root=tmp_path)
    assert rep("SYS", "USER 프롬프트", LLM, role="judge") == out
    assert never.n == 0


def test_role_is_part_of_key(tmp_path):
    rec = R.LLMRecorder(Counting(["a"]), mode="record", root=tmp_path)
    rec(None, "같은 프롬프트", LLM, role="extract")
    rep = R.LLMRecorder(_bomb, mode="replay", root=tmp_path)
    assert rep(None, "같은 프롬프트", LLM, role="extract")["text"] == "a"
    with pytest.raises(R.ReplayMiss):
        rep(None, "같은 프롬프트", LLM, role="judge")


def test_repeated_prompt_replays_in_order_and_cycles(tmp_path):
    rec = R.LLMRecorder(Counting(["r1", "r2", "r3"]), mode="record", root=tmp_path)
    assert [rec(None, "k3", LLM)["text"] for _ in range(3)] == ["r1", "r2", "r3"]
    rep = R.LLMRecorder(_bomb, mode="replay", root=tmp_path)
    assert [rep(None, "k3", LLM)["text"] for _ in range(6)] == ["r1", "r2", "r3"] * 2
    rep.reset()
    assert rep(None, "k3", LLM)["text"] == "r1"


def test_record_skips_non_ok(tmp_path):
    def err(system, user, llm, *, role="judge", base=None):  # noqa: ANN001
        return {"status": "error", "text": "", "message": "HTTP 429"}
    rec = R.LLMRecorder(err, mode="record", root=tmp_path)
    assert rec(None, "p", LLM)["status"] == "error"
    assert not any(tmp_path.rglob("*.json"))


def test_replay_miss_raises_with_key_role_and_prompt_head(tmp_path):
    rep = R.LLMRecorder(_bomb, mode="replay", root=tmp_path)
    user = "가" * 300
    with pytest.raises(R.ReplayMiss) as ei:
        rep("시스템", user, LLM, role="extract")
    msg = str(ei.value)
    assert ei.value.key == R.record_key("extract", "시스템", user) and ei.value.key in msg
    assert "role=extract" in msg
    assert ei.value.prompt_head == ("시스템\n" + user)[:200] and len(ei.value.prompt_head) == 200


def test_off_mode_passes_through(tmp_path):
    inner = Counting(["x"])
    rec = R.LLMRecorder(inner, mode="off", root=tmp_path)
    rec(None, "p", LLM)
    assert inner.n == 1 and not any(tmp_path.rglob("*.json"))


def test_settings_drive_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "weave_llm_record", "record")
    monkeypatch.setattr(settings, "weave_llm_record_dir", str(tmp_path))
    rec = R.LLMRecorder(Counting(["y"]))
    rec(None, "p", LLM)
    monkeypatch.setattr(settings, "weave_llm_record", "replay")
    assert R.LLMRecorder(_bomb)(None, "p", LLM)["text"] == "y"


def test_adapter_installs_recorder_over_resilient():
    assert isinstance(W.w_llm.complete, R.LLMRecorder)
    assert W.w_llm.complete.inner is W._complete_resilient


def test_complete_json_goes_through_module_global(tmp_path, monkeypatch):
    """complete_json 이 모듈 전역 complete 를 부르는지 — 교체 지점이 맞는지."""
    monkeypatch.setattr(W.w_llm, "complete",
                        R.LLMRecorder(Counting(['{"verdict": "정상"}']), mode="record", root=tmp_path))
    r1 = W.w_llm.complete_json(None, "판정 프롬프트", LLM, role="judge")
    assert r1["data"] == {"verdict": "정상"}

    monkeypatch.setattr(urllib.request, "urlopen", _bomb)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-real")
    monkeypatch.setattr(W.w_llm, "complete",
                        R.LLMRecorder(W._complete_resilient, mode="replay", root=tmp_path))
    r2 = W.w_llm.complete_json(None, "판정 프롬프트", LLM, role="judge")
    assert r2["data"] == {"verdict": "정상"}


async def _run_adapter(pipe: W.WeavePipeline):
    out1 = await pipe.extract_chapter(CH1, _snapshot("p1", 0, None), title="1화", chapter_no=1)
    out2 = await pipe.extract_chapter(CH2, _snapshot("p1", 1, out1.canon_delta), chapter_no=2)
    res = await pipe.review_chapter(CH2, _snapshot("p1", 1, out1.canon_delta),
                                    out2.canon_delta, chapter_no=2)
    return out1, out2, res


def _freeze_clock(monkeypatch) -> None:  # noqa: ANN001
    """어댑터·코어의 벽시계 도장을 고정한다.

    추출 판본 `_rev` = 현지 시각(초) + sha1(doc) 이고 doc 에 `_extraction.produced_at`(UTC 초)이 들어간다.
    그래서 record 와 replay 두 번의 실행이 **초 경계를 넘으면** canon_delta 의 attrs.weave_rev 가 달라
    비교가 깨졌다(2026-11-06 간헐 실패의 원인, tests/eval 반복 실행 14회 중 2회 재현). 응답 내용과 무관한 차이다.
    weave_core 파일은 고치지 않고 모듈 속성만 바꾼다.
    """
    import time as _time

    fixed_struct = _time.localtime(1_800_000_000)
    real_strftime = _time.strftime
    monkeypatch.setattr(_time, "strftime", lambda fmt, *t: real_strftime(fmt, *(t or (fixed_struct,))))
    for mod in (W.w_extract, W.w_pipe, W.w_llm):
        monkeypatch.setattr(mod, "now", lambda: "2027-01-15T08:00:00Z")


async def test_adapter_record_then_replay_without_network(tmp_path, monkeypatch):
    _freeze_clock(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-real")
    monkeypatch.setattr(settings, "weave_llm_provider", "openrouter")
    monkeypatch.setattr(settings, "weave_judge_k", 3)
    rec_dir = tmp_path / "rec"
    monkeypatch.setattr(settings, "weave_llm_record_dir", str(rec_dir))

    # record: 안쪽 = 가짜 LLM (설정으로 모드를 읽는 제품 래퍼와 같은 객체 모양)
    fake = FakeLLM({1: DOC1, 2: DOC2})
    monkeypatch.setattr(settings, "weave_llm_record", "record")
    monkeypatch.setattr(W.w_llm, "complete", R.LLMRecorder(fake))
    rec_out = await _run_adapter(W.WeavePipeline(data_dir=str(tmp_path / "a")))
    assert fake.calls.count("extract") == 2 and fake.calls.count("judge") == 3
    roles = {p.parent.parent.name for p in rec_dir.rglob("*.json")}
    assert roles == {"extract", "judge"}

    # replay: 안쪽 = 제품 경로(_complete_resilient → 코어 complete → urllib). urlopen 이 불리면 터진다.
    monkeypatch.setattr(urllib.request, "urlopen", _bomb)
    monkeypatch.setattr(settings, "weave_llm_record", "replay")
    monkeypatch.setattr(W.w_llm, "complete", W.llm_recorder)
    W.llm_recorder.reset()
    rep_out = await _run_adapter(W.WeavePipeline(data_dir=str(tmp_path / "b")))

    for a, b in zip(rec_out, rep_out):
        assert a.model_dump() == b.model_dump()
    assert any(c.explanation.startswith("[LLM 판정") for c in rep_out[2].contradictions)
