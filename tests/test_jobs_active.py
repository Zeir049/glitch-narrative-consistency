"""GET /jobs/active — chapters.status 에서 파생한 진행 중 작업 목록 (D7)."""
from __future__ import annotations

import uuid

from httpx import AsyncClient
from sqlalchemy import text

from app.core.config import settings
from app.core.db import SessionLocal
from tests.util import poll_result


async def _register(client: AsyncClient) -> dict[str, str]:
    sfx = uuid.uuid4().hex[:8]
    r = await client.post(
        "/auth/register",
        json={"email": f"ja_{sfx}@x.com", "password": "secret123", "name": "Other"},
    )
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _active(client: AsyncClient, h: dict) -> list[dict]:
    r = await client.get("/jobs/active", headers=h)
    assert r.status_code == 200, r.text
    return r.json()["items"]


async def test_active_job_lifecycle(client: AsyncClient, auth_headers: dict, project_id: str):
    h, pid = auth_headers, project_id
    assert await _active(client, h) == []

    await client.post(f"/projects/{pid}/chapters", json={"chapter_no": 1, "title": "첫 화", "text": "본문"}, headers=h)
    await client.post(f"/projects/{pid}/chapters/1/extract", headers=h)  # FakePipeline 1초 지연

    items = await _active(client, h)
    assert len(items) == 1
    job = items[0]
    assert job["project_id"] == pid and job["project_name"] == "테스트작품"
    assert job["chapter_no"] == 1 and job["title"] == "첫 화"
    assert job["status"] == "extracting"
    assert job["progress"] == 0
    assert job["started_at"] and job["stale"] is False

    await poll_result(client, pid, 1, h, stop=("extracted", "failed"))
    assert await _active(client, h) == []  # 완료 후 0건

    # 검수 중에도 보인다
    await client.post(f"/projects/{pid}/chapters/1/review", headers=h)
    items = await _active(client, h)
    assert [j["status"] for j in items] == ["reviewing"]
    await poll_result(client, pid, 1, h, stop=("saved", "failed"))
    assert await _active(client, h) == []


async def test_active_jobs_owner_scoped(client: AsyncClient, auth_headers: dict, project_id: str):
    h, pid = auth_headers, project_id
    await client.post(f"/projects/{pid}/chapters", json={"chapter_no": 1, "text": "본문"}, headers=h)
    await client.post(f"/projects/{pid}/chapters/1/extract", headers=h)

    other = await _register(client)
    assert await _active(client, other) == []  # 남의 작업은 안 보인다
    assert len(await _active(client, h)) == 1
    await poll_result(client, pid, 1, h, stop=("extracted", "failed"))


async def _make_stuck(pid: str, minutes: int, status: str = "reviewing") -> None:
    """워커 없이 진행 중 상태로 두고 minutes 분 전부터 그대로인 것처럼."""
    async with SessionLocal() as s:
        await s.execute(
            text(
                "UPDATE chapters SET status = :st,"
                " updated_at = now() - make_interval(mins => :m)"
                " WHERE project_id = :p AND chapter_no = 1"
            ),
            {"st": status, "m": minutes, "p": uuid.UUID(pid)},
        )
        await s.commit()


async def test_active_job_stale_is_failed_and_dropped(client: AsyncClient, auth_headers: dict, project_id: str):
    """워커가 죽은 작업이 목록에 영원히 남아 폴링이 끝나지 않던 버그 — 조회가 failed 로 정리한다."""
    h, pid = auth_headers, project_id
    await client.post(f"/projects/{pid}/chapters", json={"chapter_no": 1, "text": "본문"}, headers=h)
    await _make_stuck(pid, settings.analysis_stale_threshold_minutes + 1)

    assert await _active(client, h) == []  # 목록에서 빠진다 → 작업 창 폴링이 멈춘다
    res = (await client.get(f"/projects/{pid}/chapters/1/result", headers=h)).json()
    assert res["status"] == "failed"
    assert res["failed_stage"] == "review"
    assert res["detail"] == "시간 초과로 중단된 작업"
    assert await _active(client, h) == []  # 다시 조회해도 그대로


async def test_active_job_not_yet_stale_stays(client: AsyncClient, auth_headers: dict, project_id: str):
    h, pid = auth_headers, project_id
    await client.post(f"/projects/{pid}/chapters", json={"chapter_no": 1, "text": "본문"}, headers=h)
    await _make_stuck(pid, settings.analysis_stale_threshold_minutes - 2, status="extracting")

    items = await _active(client, h)
    assert len(items) == 1 and items[0]["stale"] is False and items[0]["status"] == "extracting"
    d = (await client.get(f"/projects/{pid}/chapters/1", headers=h)).json()
    assert d["status"] == "extracting"  # 아직 돌 수 있는 작업은 건드리지 않는다


async def test_result_poll_fails_stale_job(client: AsyncClient, auth_headers: dict, project_id: str):
    """작업 창 없이 결과 화면만 폴링해도 고착 작업이 failed 로 끝난다."""
    h, pid = auth_headers, project_id
    await client.post(f"/projects/{pid}/chapters", json={"chapter_no": 1, "text": "본문"}, headers=h)
    await _make_stuck(pid, settings.analysis_stale_threshold_minutes + 1, status="extracting")

    res = (await client.get(f"/projects/{pid}/chapters/1/result", headers=h)).json()
    assert res["status"] == "failed" and res["failed_stage"] == "extract"
    assert res.get("canon_delta") is None


async def test_active_jobs_requires_auth(client: AsyncClient):
    assert (await client.get("/jobs/active")).status_code == 401
