"""학습 judge v2 서비스 연결 뼈대 — 기본 꺼짐(출력 그대로), 켜면 가짜 점수기로 변환 경로."""
from __future__ import annotations

import json
import sys
import uuid

import pytest
from httpx import AsyncClient

from app.core.config import Settings, settings
from app.core.db import SessionLocal
from app.pipeline import factory
from app.pipeline.fake import FakePipeline
from app.pipeline.interface import ConflictRef, ContradictionOut, ReviewOutput, Span
from app.pipeline.judge_v2.service import SOURCE_TAG, JudgeV2, PriorChapter
from app.repository.analysis_repo import AnalysisRepository
from app.repository.chapter_repo import ChapterRepository
from app.workers import tasks
from tests.util import poll_result

PRIOR1 = "김첨지는 인력거꾼이다. 아내는 집에서 앓고 있었다.\n아내는 설렁탕을 먹고 싶어 했다."
PRIOR2 = "비 오는 날 김첨지는 학생을 태우고 정거장까지 달렸다.\n그는 팔십 전을 손에 쥐었다."
CH = "김첨지는 설렁탕을 사 들고 집으로 갔다. 아내는 이미 건강했다. 비가 그쳤다."


class FakeScorer:
    """주장 글에 '건강' 이 있으면 0.95, '설렁탕' 이면 0.6, 나머지 0.1. 받은 입력을 기록한다."""

    def __init__(self) -> None:
        self.seen: list = []

    def score(self, items):  # noqa: ANN001, ANN201
        self.seen = list(items)
        return [0.95 if "건강" in c else 0.6 if "설렁탕" in c else 0.1 for _ctx, c in items]


def test_judge_v2_review_rules():
    sc = FakeScorer()
    out = JudgeV2(sc, 0.5, max_items=5).review(CH, [PriorChapter(2, PRIOR2), PriorChapter(1, PRIOR1)])
    assert [o.confidence for o in out] == [0.95, 0.6]                 # 기준값 이상, p 내림차순
    top = out[0]
    assert top.explanation.startswith(SOURCE_TAG) and top.type == "event"
    assert CH[top.span.start:top.span.end] == "아내는 이미 건강했다."
    assert top.conflict_ref.chapter_no == 1 and "아내" in (top.conflict_ref.quote or "")
    assert len(sc.seen) == 3 and all(len(ctx) <= 4 for ctx, _ in sc.seen)
    assert JudgeV2(sc, 0.5, max_items=1).review(CH, [PriorChapter(1, PRIOR1)])[0].confidence == 0.95
    assert JudgeV2(sc, 0.99).review(CH, [PriorChapter(1, PRIOR1)]) == []
    assert JudgeV2(sc, 0.5).review(CH, []) == []                      # 앞 회차가 없으면 판정하지 않는다
    assert JudgeV2(sc, 0.5).review("", [PriorChapter(1, PRIOR1)]) == []


def test_defaults_off_and_factory_none_without_loading_model(monkeypatch):
    assert Settings.model_fields["judge_v2_enabled"].default is False
    assert Settings.model_fields["judge_v2_model_dir"].default == ""
    monkeypatch.setitem(sys.modules, "app.pipeline.judge_v2.hf", None)   # 모델 모듈을 읽으면 ImportError
    factory.get_judge_v2.cache_clear()
    try:
        monkeypatch.setattr(settings, "judge_v2_enabled", False)
        assert factory.get_judge_v2() is None
        factory.get_judge_v2.cache_clear()
        monkeypatch.setattr(settings, "judge_v2_enabled", True)
        monkeypatch.setattr(settings, "judge_v2_model_dir", "")
        assert factory.get_judge_v2() is None                         # 켜도 모델 폴더가 없으면 None
    finally:
        factory.get_judge_v2.cache_clear()


# ---------------------------------------------------------------- 워커(run_review) 통합

FIXED = ContradictionOut(span=Span(start=0, end=3, quote="김첨지"), conflict_ref=ConflictRef(chapter_no=1, quote="q"),
                         type="character", explanation="파이프라인 모순", confidence=0.9)


class _FixedReview(FakePipeline):
    async def review_chapter(self, chapter_text, canon, delta, title=None, *, chapter_no=None):  # noqa: ANN001
        return ReviewOutput(contradictions=[FIXED])


async def _setup(client: AsyncClient, pid: str, h: dict) -> None:
    for no, text in ((1, PRIOR1), (2, CH)):
        await client.post(f"/projects/{pid}/chapters", json={"chapter_no": no, "text": text}, headers=h)
        await client.post(f"/projects/{pid}/chapters/{no}/extract", headers=h)
        assert (await poll_result(client, pid, no, h, stop=("extracted", "failed")))["status"] == "extracted"
    async with SessionLocal() as s:
        repo = ChapterRepository(s)
        (await repo.get(uuid.UUID(pid), 1)).status = "saved"
        (await repo.get(uuid.UUID(pid), 2)).status = "reviewing"
        await s.commit()


async def _stored(pid: str, no: int = 2) -> list[tuple]:
    async with SessionLocal() as s:
        ch = await ChapterRepository(s).get(uuid.UUID(pid), no)
        rows = await AnalysisRepository(s).list_contradictions(ch.id)
        return sorted((r.type, r.explanation, r.confidence, json.dumps(r.span, sort_keys=True, ensure_ascii=False),
                       json.dumps(r.conflict_ref, sort_keys=True, ensure_ascii=False)) for r in rows)


def _expect(c: ContradictionOut) -> tuple:
    return (c.type, c.explanation, c.confidence, json.dumps(c.span.model_dump(), sort_keys=True, ensure_ascii=False),
            json.dumps(c.conflict_ref.model_dump(), sort_keys=True, ensure_ascii=False))


async def test_run_review_off_is_unchanged(client: AsyncClient, auth_headers: dict, project_id: str, monkeypatch):
    await _setup(client, project_id, auth_headers)
    monkeypatch.setattr(tasks, "get_pipeline", lambda: _FixedReview())

    async def boom(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("꺼져 있는데 v2 가 불렸다")
    monkeypatch.setattr(tasks, "_with_judge_v2", boom)
    factory.get_judge_v2.cache_clear()
    monkeypatch.setattr(settings, "judge_v2_enabled", False)
    assert await tasks.run_review(uuid.UUID(project_id), 2)
    assert await _stored(project_id) == [_expect(FIXED)]               # 파이프라인 출력 그대로(바이트 단위 같은 필드)
    factory.get_judge_v2.cache_clear()


async def test_run_review_on_appends_v2(client: AsyncClient, auth_headers: dict, project_id: str, monkeypatch):
    await _setup(client, project_id, auth_headers)
    monkeypatch.setattr(tasks, "get_pipeline", lambda: _FixedReview())
    judge = JudgeV2(FakeScorer(), 0.5, max_items=1)
    monkeypatch.setattr(tasks, "get_judge_v2", lambda: judge)
    assert await tasks.run_review(uuid.UUID(project_id), 2)
    rows = await _stored(project_id)
    assert len(rows) == 2 and _expect(FIXED) in rows
    v2 = [r for r in rows if r[1].startswith(SOURCE_TAG)]
    assert len(v2) == 1 and v2[0][2] == 0.95 and json.loads(v2[0][4])["chapter_no"] == 1
