"""WEAVE_KNOWS_MODE(B-039 knows 누적) 연결 테스트 — LLM 호출 없음."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings, settings
from app.pipeline import weave as W
from app.pipeline.weave_core import weave_pipeline as wpipe
from tests.test_weave_pipeline import CH1, CH2, _snapshot, weave  # noqa: F401  (fixture 재사용)

KNOWS_EPS = [{"episode": 1, "events": [
    {"event_id": "EV_K1", "order": 1, "reality_status": "actual", "timeline": "main",
     "state_changes": [{"entity": "CHAR_001", "field": "knows", "op": "add", "to": "지식A"}]},
    {"event_id": "EV_K2", "order": 2, "reality_status": "actual", "timeline": "main",
     "state_changes": [{"entity": "CHAR_001", "field": "knows", "op": "set", "to": "지식B"}]},
]}]


def test_legacy_has_no_accumulated_and_accumulate_keeps_both():
    led_leg, _ = wpipe.replay_confirmed(KNOWS_EPS, timelines=["main"], knows_mode="legacy")
    led_acc, _ = wpipe.replay_confirmed(KNOWS_EPS, timelines=["main"], knows_mode="accumulate")
    assert "accumulated" not in led_leg.state["main"]["CHAR_001"]["knows"]
    assert len(led_acc.state["main"]["CHAR_001"]["knows"]["accumulated"]) == 2


def test_default_is_accumulate():
    assert Settings().weave_knows_mode == "accumulate"


def test_invalid_value_is_rejected_at_startup(monkeypatch):
    monkeypatch.setenv("WEAVE_KNOWS_MODE", "bogus")
    with pytest.raises(ValidationError):
        Settings()
    monkeypatch.setenv("WEAVE_KNOWS_MODE", "legacy")
    assert Settings().weave_knows_mode == "legacy"


@pytest.mark.parametrize("mode", ["legacy", "accumulate"])
async def test_adapter_passes_setting_to_replay(weave, monkeypatch, mode):  # noqa: F811
    pipe, _, _ = weave
    monkeypatch.setattr(settings, "weave_knows_mode", mode)
    seen: list[str] = []
    real = W.w_pipe.replay_confirmed

    def spy(eps, **kw):
        seen.append(kw.get("knows_mode"))
        return real(eps, **kw)

    monkeypatch.setattr(W.w_pipe, "replay_confirmed", spy)
    out1 = await pipe.extract_chapter(CH1, _snapshot("p1", 0, None), chapter_no=1)
    out2 = await pipe.extract_chapter(CH2, _snapshot("p1", 1, out1.canon_delta), chapter_no=2)
    await pipe.review_chapter(CH2, _snapshot("p1", 1, out1.canon_delta), out2.canon_delta, chapter_no=2)
    assert seen == [mode]
