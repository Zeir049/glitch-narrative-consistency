"""judge 층 회귀 게이트 — learned 모드 + 가짜 백엔드(WEAVE_JUDGE_MODE=learned, WEAVE_JUDGE_BACKEND=fake)로
4작품(wm·q1·q3·sherlock) 하네스를 돌린 장면 판정 결과를 기준선으로 고정한다.

목적은 성능이 아니다. FakeBackend 는 결정적 휴리스틱이라 거의 모든 장면을 양성으로 낸다(정상 장면 80/85).
이 테스트는 "쌍 생성기(app/pipeline/pairs.py)·판정 경로(app/pipeline/learned_judge.py)·하네스 문맥 구성
(scripts/eval/judge_eval.py load_context)을 바꿨을 때 결과가 바뀌었는지"를 알아채는 데만 쓴다.
LLM·네트워크 호출 없음, 약 2초. 호출은 scripts/eval/judge_eval.py 의 learned 모드(LearnedJudge + score)를 그대로 쓴다.

고정하는 것
  - 작품별 검출(양성) 장면 ID 집합, 작품별·합산 TP/n_pos/FP/n_neg
  - 작품별 판정 쌍 수 합계와 장면별 (쌍 수, 판정, 확신도, 근거 전제 ID) 지문(sha256 앞 16자리)
    — q1·q3 는 장면 전부가 양성이라 집합만으로는 쌍 생성 변화를 못 본다. 지문이 그 몫을 한다.

기준선이 바뀌는 것이 정상인 경우 (그때 갱신한다)
  - 쌍 생성기·FakeBackend·판정 집계를 의도적으로 바꾼 경우. 예: 세션 D 의 pairs.py 변경.
  - tests/eval/weave_judge_corpus/ 의 코퍼스·라벨이 바뀐 경우.
  의도하지 않은 변화(예: 어댑터나 weave_core 교체로 원장·checker 항목이 달라짐)라면 고치지 말고 원인을 찾는다.

갱신 절차 (docs/EVAL.md "learned 가짜 백엔드 회귀 게이트" 절과 같다)
  1. PYTHONIOENCODING=utf-8 python -m pytest tests/eval/test_judge_learned_fake_baseline.py -q
     실패 메시지에 새 값이 나온다. 또는 아래로 한꺼번에 출력:
     PYTHONIOENCODING=utf-8 python -c "import tests.eval.test_judge_learned_fake_baseline as t; t.print_current()"
  2. 바뀐 이유를 커밋 메시지에 적고(어떤 변경 때문에, 검출 집합이 어떻게 달라졌는지) 아래 상수를 새 값으로 바꾼다.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load():
    name = "judge_eval"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "eval" / "judge_eval.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


JE = _load()


def _ids(*nums: int) -> list[str]:
    return [f"scene_{n:02d}" for n in nums]


# ── 기준선 (2026-11-07, 쌍 생성기 v2(할당량 24·관련도 정렬) 병합 뒤 main 에서 측정) ────────
# 이전(2026-11-06, b1a4fb3, 쌍 생성기 v1 상한 32): TP 71/80 · FP 80/85 · 쌍 1920/9503/6816/8301.
# v2 로 wm 양성 scene_12·14·17 이 검출에 더해지고(TP 12→15), sherlock 정상 scene_09·32 가 양성으로 바뀌었다(FP 22→24).
# 재측정(2026-11-07, WM 재생 timeline 수정 — wm 을 코어 기본 STATE_TIMELINES 로): 아래 값 전부 그대로.
# wm checker 전제 쌍이 14→49 로 바뀌어도 지문은 (쌍 수, 판정, 확신도, 근거 ID)만 봐서 같다 — 쌍 구성은
# tests/eval/test_judge_eval_timelines.py 가 본다.
# 갱신(2026-11-07, 하네스 규칙 조립 수정 — rule_id 기준 → 본문 정규화 기준 중복 제거, 유실 규칙 4개 복구):
# q1 쌍 7142→7145·지문 65f3a1c60bcf05ef→0fd1d4381d44d8dc. 검출 집합·TP/FP·wm·q3·셜록 값은 그대로
# (q3 는 규칙 할당량 4가 이미 차 있어 쌍 수가 같고 구성만 바뀐다 — tests/eval/test_judge_rule_assembly.py).
BASELINE_WORKS = ["wm", "q1", "q3", "sherlock"]
BASELINE_TOTAL = (74, 80, 82, 85)  # TP, n_pos, FP, n_neg
BASELINE_PER_WORK = {
    "wm": (15, 20, 8, 10),
    "q1": (20, 20, 25, 25),
    "q3": (20, 20, 25, 25),
    "sherlock": (19, 20, 24, 25),
}
BASELINE_DETECTED = {
    "wm": _ids(1, 2, 4, 5, 6, 7, 11, 12, 13, 14, 15, 16, 17, 19, 20, 21, 24, 25, 26, 27, 28, 29, 30),
    "q1": _ids(*range(1, 46)),
    "q3": _ids(*range(1, 46)),
    "sherlock": _ids(*[n for n in range(1, 46) if n not in (28, 29)]),
}
BASELINE_PAIRS = {"wm": 1440, "q1": 7145, "q3": 5112, "sherlock": 6507}
BASELINE_FINGERPRINT = {
    "wm": "a00db0ca604c806f",
    "q1": "0fd1d4381d44d8dc",
    "q3": "e398085567d40c8e",
    "sherlock": "c5a2a5d189f35cb2",
}


def _counts(m: dict) -> tuple[int, int, int, int]:
    return m["TP"], m["n_pos"], m["FP"], m["n_neg"]


def _fingerprint(runs: dict[str, dict]) -> str:
    rows = [[s, r.get("pairs"), r.get("verdict"), r.get("confidence"), r.get("evidence_event_ids")]
            for s, r in sorted(runs.items())]
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def _run() -> tuple[dict, dict]:
    """설정을 learned/fake 로 바꿔 하네스를 돌리고 원래대로 돌려 놓는다."""
    from app.core.config import settings

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(settings, "weave_judge_mode", "learned")
        mp.setattr(settings, "weave_judge_backend", "fake")
        mp.setattr(settings, "weave_judge_model_dir", "")
        mp.setattr(settings, "weave_judge_threshold", None)
        mp.setattr(settings, "weave_judge_batch_size", 16)
        judge = JE.LearnedJudge(JE.learned_backend())
        rep = JE.score(judge)
    return rep, judge.runs


def current() -> dict:
    rep, runs = _run()
    return {
        "works": rep["works"],
        "total": _counts(rep["total"]),
        "per_work": {w: _counts(r["metrics"]) for w, r in rep["per_work"].items()},
        "detected": {w: [s for s, v in r["predictions"].items() if v] for w, r in rep["per_work"].items()},
        "pairs": {w: sum(r.get("pairs", 0) for r in rs.values()) for w, rs in runs.items()},
        "fingerprint": {w: _fingerprint(rs) for w, rs in runs.items()},
        "judged_by": rep["judge"],
    }


def print_current() -> None:
    """갱신용: 지금 트리의 값을 출력한다."""
    print(json.dumps(current(), ensure_ascii=False, indent=1))


@pytest.fixture(scope="module")
def cur(request) -> dict:  # noqa: ANN001
    from app.pipeline import weave as W

    mp = pytest.MonkeyPatch()

    def bomb(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("learned/fake 게이트에서 LLM 호출")
    mp.setattr(W.w_llm, "complete", bomb)
    try:
        yield current()
    finally:
        mp.undo()


def test_backend_is_fake(cur):
    assert cur["judged_by"].startswith("learned:learned / "), cur["judged_by"]
    assert cur["works"] == BASELINE_WORKS


def test_detected_scene_sets(cur):
    assert cur["detected"] == BASELINE_DETECTED


def test_counts(cur):
    assert cur["per_work"] == BASELINE_PER_WORK
    assert cur["total"] == BASELINE_TOTAL


def test_pair_fingerprint(cur):
    assert cur["pairs"] == BASELINE_PAIRS
    assert cur["fingerprint"] == BASELINE_FINGERPRINT


def test_settings_restored():
    """_run 이 바꾼 judge 설정을 원래 값으로 돌려 놓는다(다른 테스트로 새지 않게)."""
    from app.core.config import settings

    names = ("weave_judge_mode", "weave_judge_backend", "weave_judge_model_dir",
             "weave_judge_threshold", "weave_judge_batch_size")
    before = {n: getattr(settings, n) for n in names}
    rep, _ = _run()
    assert rep["works"] == BASELINE_WORKS
    assert {n: getattr(settings, n) for n in names} == before
