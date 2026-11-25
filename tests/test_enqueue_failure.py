"""큐 투입 실패 — 진행 중 상태로 고착되지 않고 failed + 503, 바로 재시도 가능.

상태를 extracting/reviewing 으로 커밋한 뒤 Redis 투입이 터지면, 도는 작업 없이
진행 중 상태가 남아 고착 기준(기본 25분)까지 재시도가 409 로 막혔다.
"""
from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from tests.util import poll_result


@pytest.fixture
def broken_queue(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    async def broken_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        raise ConnectionError("Redis 연결 실패(테스트)")

    monkeypatch.setattr("app.api.chapters.enqueue_analysis", broken_enqueue)
    return monkeypatch


async def _upload(client: AsyncClient, project_id: str, headers: dict[str, str]) -> str:
    base = f"/projects/{project_id}/chapters"
    r = await client.post(
        base, json={"chapter_no": 1, "title": "1화", "text": f"본문 {uuid.uuid4().hex}"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return f"{base}/1"


async def _assert_failed(client: AsyncClient, url: str, headers: dict[str, str], stage: str) -> None:
    r = await client.get(url, headers=headers)
    body = r.json()
    assert body["status"] == "failed"
    assert body["failed_stage"] == stage
    assert "대기열" in body["failed_detail"]


@pytest.mark.parametrize("action", ["extract", "analyze"])
async def test_enqueue_failure_on_extract_marks_failed(
    client: AsyncClient, auth_headers: dict[str, str], project_id: str,
    broken_queue: pytest.MonkeyPatch, action: str,
):
    url = await _upload(client, project_id, auth_headers)

    r = await client.post(f"{url}/{action}", headers=auth_headers)
    assert r.status_code == 503
    assert "대기열" in r.json()["detail"]
    await _assert_failed(client, url, auth_headers, "extract")

    # 큐가 돌아오면 409 없이 바로 다시 시작된다
    broken_queue.undo()
    r = await client.post(f"{url}/{action}", headers=auth_headers)
    assert r.status_code == 202, r.text
    # inline 작업이 테스트 종료(엔진 정리) 전에 끝나게 기다린다
    done = ("extracted", "failed") if action == "extract" else ("reviewed", "saved", "failed")
    assert (await poll_result(client, project_id, 1, auth_headers, stop=done))["status"] in done[:-1]


async def test_enqueue_failure_on_review_keeps_delta_and_allows_retry(
    client: AsyncClient, auth_headers: dict[str, str], project_id: str,
    monkeypatch: pytest.MonkeyPatch,
):
    url = await _upload(client, project_id, auth_headers)
    assert (await client.post(f"{url}/extract", headers=auth_headers)).status_code == 202
    assert (await poll_result(client, project_id, 1, auth_headers, stop=("extracted", "failed")))[
        "status"
    ] == "extracted"

    async def broken_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        raise ConnectionError("Redis 연결 실패(테스트)")

    monkeypatch.setattr("app.api.chapters.enqueue_analysis", broken_enqueue)
    r = await client.post(f"{url}/review", headers=auth_headers)
    assert r.status_code == 503
    await _assert_failed(client, url, auth_headers, "review")

    # 추출 결과는 남아 있어 재추출 없이 재검수로 이어진다
    result = (await client.get(f"{url}/result", headers=auth_headers)).json()
    assert result["canon_delta"] is not None

    monkeypatch.undo()
    r = await client.post(f"{url}/review", headers=auth_headers)
    assert r.status_code == 202, r.text
    assert (await poll_result(client, project_id, 1, auth_headers))["status"] == "saved"
