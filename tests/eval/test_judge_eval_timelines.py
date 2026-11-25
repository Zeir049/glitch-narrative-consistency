"""judge_eval.load_context 의 작품별 원장 timelines 회귀 (docs/learned-judge-branch-review-G.md §4a).

WM 코퍼스는 Weave 원본의 다중 timeline(real_present·future_world·unknown = ledger.STATE_TIMELINES)으로 추출돼
장면 사건이 'future_world' 다. checker.check_event 는 사건 timeline 이름 그대로 원장 칸을 찾으므로
WM 을 timelines=["main"] 으로 재생하면 checker 항목이 9개로 준다. 코어 기본(None)으로 재생하면 31개.
Q1·Q3·셜록은 서비스 추출본(timeline 'main')이라 ["main"] 그대로 — 수치가 바뀌면 안 된다.
LLM·네트워크 호출 없음.
"""
from __future__ import annotations

import importlib.util
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

# (checker 항목 수, 그중 needs_judge 수) — 2026-11-07 실측. wm 이전(timelines=["main"]) 값은 (9, 7).
EXPECTED = {"wm": (31, 28), "q1": (103, 92), "q3": (108, 100), "sherlock": (130, 119)}


def test_timelines_spec():
    assert JE.timelines_of("wm") is None
    for w in ("q1", "q3", "sherlock"):
        assert JE.timelines_of(w) == ["main"]
    assert JE.timelines_of("dracula") == ["main"]   # WORKS 밖(서비스 추출본)


@pytest.mark.parametrize("work", list(EXPECTED))
def test_checker_item_counts(work):
    ctx = JE.load_context(work)
    items = [i for v in ctx.checker_items.values() for i in v]
    assert (len(items), sum(i["needs_judge"] for i in items)) == EXPECTED[work]


def test_wm_scene_events_land_in_ledger_buckets():
    from ledger import STATE_TIMELINES  # load_context 가 코어 경로를 연 뒤
    ctx = JE.load_context("wm")
    assert tuple(ctx.ledger.state) == STATE_TIMELINES
    tls = {e.get("timeline") for evs in ctx.scenes.values() for e in evs}
    assert tls <= set(ctx.ledger.state)
