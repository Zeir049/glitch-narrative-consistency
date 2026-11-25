"""시연용 ReplayPipeline (PIPELINE_MODE=replay).

- 단위: 인셉션 1~3화·인터스텔라 1·2화 자료가 복선을 뺀 6종을 모두 채우고, 모순 인용이 업로드 원문 안에 있는지.
- API: 실제 업로드 → 추출 → 검수 → 저장을 1→2→3화로 돌려, 회차별 시각화(canon chapter=N)가
  모든 탭에서 비지 않는지와 인물 버전이 쌓이는지 본다.
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.pipeline.interface import CanonSnapshot
from app.pipeline.replay import REPLAY_ROOT, ReplayPipeline
from tests.util import MANUAL_SAVE, poll_result

# The demo replay data (film-derived) is not published in this repository.
pytestmark = pytest.mark.skipif(
    not (REPLAY_ROOT / "inception" / "chapter_1.txt").exists(),
    reason="replay demo data not included (third-party text)",
)

CHAPTERS = (1, 2, 3)
DELTA_FIELDS = ("worldview", "characters", "relations", "timeline", "spaces", "events")


def _text(n: int) -> str:
    return (REPLAY_ROOT / "inception" / f"chapter_{n}.txt").read_text(encoding="utf-8")


def _canon() -> CanonSnapshot:
    return CanonSnapshot(project_id="p")


@pytest.mark.parametrize("n", CHAPTERS)
async def test_extract_fills_every_category_but_foreshadowing(n: int) -> None:
    out = await ReplayPipeline().extract_chapter(_text(n), _canon(), chapter_no=n)
    for field in DELTA_FIELDS:
        assert getattr(out.canon_delta, field), f"{n}화 {field} 비어 있음"
    assert out.canon_delta.foreshadowing == []  # AI 결과에 복선이 없다
    assert out.visual_json and out.highlighted_text == _text(n)
    assert all(t.time_value for t in out.canon_delta.timeline)


async def test_review_quotes_come_from_uploaded_text() -> None:
    pipe = ReplayPipeline()
    assert (await pipe.review_chapter(_text(1), _canon(), None, chapter_no=1)).contradictions == []
    for n in (2, 3):
        text = _text(n)
        out = await pipe.review_chapter(text, _canon(), None, chapter_no=n)
        assert out.contradictions
        for c in out.contradictions:
            assert text[c.span.start : c.span.end] == c.span.quote
            assert c.conflict_ref.chapter_no is None or 1 <= c.conflict_ref.chapter_no <= n
            assert "PLANT_" not in c.explanation
            assert "유형 목록" not in c.explanation and "(order " not in c.explanation  # 평가용 메모


async def test_unknown_chapter_fails_loudly() -> None:
    with pytest.raises(ValueError, match="리플레이 자료가 없는 회차"):
        await ReplayPipeline().extract_chapter("본문", _canon(), chapter_no=9)


async def test_health_lists_chapters() -> None:
    h = await ReplayPipeline().health()
    assert h.ok and h.mode == "replay" and "[1, 2, 3]" in h.message


async def _save(client: AsyncClient, pid: str, h: dict, n: int) -> None:
    r = await client.post(
        f"/projects/{pid}/chapters", json={"chapter_no": n, "text": _text(n)}, headers=h
    )
    assert r.status_code == 201, r.text
    await client.post(f"/projects/{pid}/chapters/{n}/extract", headers=h)
    assert (await poll_result(client, pid, n, h, stop=("extracted", "failed")))["status"] == "extracted"
    await client.post(f"/projects/{pid}/chapters/{n}/review", json=MANUAL_SAVE, headers=h)
    res = await poll_result(client, pid, n, h)
    assert res["status"] == "reviewed"
    assert bool(res["contradictions"]) == (n > 1)
    assert (await client.post(f"/projects/{pid}/chapters/{n}/save", headers=h)).status_code == 200


async def test_every_tab_has_chapter_contribution(
    client: AsyncClient, auth_headers: dict, project_id: str, monkeypatch
) -> None:
    from app.workers import tasks

    pipe = ReplayPipeline()
    monkeypatch.setattr(tasks, "get_pipeline", lambda: pipe)
    h, pid = auth_headers, project_id
    for n in CHAPTERS:
        await _save(client, pid, h, n)

    for n in CHAPTERS:
        for view in ("worldview", "timeline", "spaces", "events"):
            r = await client.get(f"/projects/{pid}/canon", params={"view": view, "chapter": n}, headers=h)
            assert r.json()["items"], f"{n}화 {view} 기여 없음"
        chars = (
            await client.get(f"/projects/{pid}/canon", params={"view": "characters", "chapter": n}, headers=h)
        ).json()
        assert chars["nodes"] and chars["edges"], f"{n}화 인물 기여 없음"

    # 특징이 회차마다 누적돼 코브는 3화에서 버전 3
    current = (await client.get(f"/projects/{pid}/canon", params={"view": "characters"}, headers=h)).json()
    cobb = next(node for node in current["nodes"] if node["entity_key"] == "CHAR_001")
    assert cobb["version"] == 3 and cobb["source_chapter"] == 3


# ── 인터스텔라 (REPLAY_STORY=interstellar, 1·2화) ──
INTERSTELLAR = ReplayPipeline(story="interstellar")


def _istext(n: int) -> str:
    return (REPLAY_ROOT / "interstellar" / f"chapter_{n}.txt").read_text(encoding="utf-8")


@pytest.mark.parametrize("n", (1, 2))
async def test_interstellar_extract_fills_every_category_but_foreshadowing(n: int) -> None:
    out = await INTERSTELLAR.extract_chapter(_istext(n), _canon(), chapter_no=n)
    for field in DELTA_FIELDS:
        assert getattr(out.canon_delta, field), f"{n}화 {field} 비어 있음"
    assert out.canon_delta.foreshadowing == []
    assert all(t.time_value for t in out.canon_delta.timeline)


async def test_interstellar_source_texts_come_from_uploaded_text() -> None:
    """심은 모순 없이 원문만 쓴다 — 사건·시간·장소·규칙의 source_text 가 그 회차 원문에 그대로 있다."""
    for n in (1, 2):
        text = _istext(n)
        visual = (await INTERSTELLAR.extract_chapter(text, _canon(), chapter_no=n)).visual_json
        for key in ("events", "times", "locations", "worldview_rules"):
            for item in visual[key]:
                assert item["source_text"] in text, (n, key, item["source_text"])


async def test_interstellar_review_quotes_come_from_uploaded_text() -> None:
    assert (await INTERSTELLAR.review_chapter(_istext(1), _canon(), None, chapter_no=1)).contradictions == []
    text = _istext(2)
    out = await INTERSTELLAR.review_chapter(text, _canon(), None, chapter_no=2)
    assert [c.confidence for c in out.contradictions] == [0.9, 0.6, 0.6]  # 확정 모순이 먼저
    for c in out.contradictions:
        assert text[c.span.start : c.span.end] == c.span.quote
        assert c.conflict_ref.chapter_no == 1 and c.conflict_ref.quote in _istext(1)
        assert "\n근거: " in c.explanation


async def test_interstellar_health_lists_chapters() -> None:
    h = await INTERSTELLAR.health()
    assert h.ok and "interstellar" in h.message and "[1, 2]" in h.message


async def test_interstellar_chapter2_adds_versions(
    client: AsyncClient, auth_headers: dict, project_id: str, monkeypatch
) -> None:
    from app.core.config import settings
    from app.workers import tasks

    monkeypatch.setattr(tasks, "get_pipeline", lambda: INTERSTELLAR)
    # 각본 원문이라 회차가 3만 자 안팎 — .env 의 MAX_CHAPTER_CHARS 를 낮춰 둔 환경에서도 돌게
    monkeypatch.setattr(settings, "max_chapter_chars", 100_000)
    h, pid = auth_headers, project_id
    for n in (1, 2):
        r = await client.post(
            f"/projects/{pid}/chapters", json={"chapter_no": n, "text": _istext(n)}, headers=h
        )
        assert r.status_code == 201, r.text
        await client.post(f"/projects/{pid}/chapters/{n}/extract", headers=h)
        assert (await poll_result(client, pid, n, h, stop=("extracted", "failed")))["status"] == "extracted"
        await client.post(f"/projects/{pid}/chapters/{n}/review", json=MANUAL_SAVE, headers=h)
        res = await poll_result(client, pid, n, h)
        assert res["status"] == "reviewed" and len(res["contradictions"]) == (3 if n == 2 else 0)
        assert (await client.post(f"/projects/{pid}/chapters/{n}/save", headers=h)).status_code == 200

    for n in (1, 2):
        for view in ("worldview", "timeline", "spaces", "events"):
            r = await client.get(f"/projects/{pid}/canon", params={"view": view, "chapter": n}, headers=h)
            assert r.json()["items"], f"{n}화 {view} 기여 없음"

    current = (await client.get(f"/projects/{pid}/canon", params={"view": "characters"}, headers=h)).json()
    nodes = {node["entity_key"]: node for node in current["nodes"]}
    # 2화에서 특징이 늘어난 인물은 버전 2, 2화에 처음 나온 로스는 버전 1
    assert nodes["CHAR_001"]["version"] == 2 and nodes["CHAR_001"]["source_chapter"] == 2
    assert nodes["CHAR_008"]["version"] == 2
    assert nodes["CHAR_011"]["version"] == 1 and nodes["CHAR_011"]["source_chapter"] == 2
    assert nodes["CHAR_005"]["version"] == 1  # 앤슨은 1화에만
