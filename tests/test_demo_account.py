"""시연 계정(읽기 전용): POST /auth/demo 로그인 + 쓰기 요청 403."""
from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from app.core.config import settings


@pytest.fixture
async def demo_email(client: AsyncClient, monkeypatch) -> str:
    """시연 계정을 미리 만들어 두고(일반 가입) 기능을 켠다."""
    email = f"demo_{uuid.uuid4().hex[:8]}@glitch.local"
    r = await client.post(
        "/auth/register", json={"email": email, "password": "secret123", "name": "시연"}
    )
    assert r.status_code == 201, r.text
    monkeypatch.setattr(settings, "enable_demo_account", True)
    monkeypatch.setattr(settings, "demo_account_email", email)
    return email


async def _demo_headers(client: AsyncClient) -> dict[str, str]:
    r = await client.post("/auth/demo")
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_demo_disabled_404(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "enable_demo_account", False)
    r = await client.get("/auth/demo")
    assert r.status_code == 200 and r.json() == {"enabled": False}
    r = await client.post("/auth/demo")
    assert r.status_code == 404


async def test_demo_account_missing_404(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "enable_demo_account", True)
    monkeypatch.setattr(settings, "demo_account_email", f"none_{uuid.uuid4().hex[:8]}@x.com")
    r = await client.post("/auth/demo")
    assert r.status_code == 404


async def test_demo_login_issues_token_for_that_account(client: AsyncClient, demo_email: str):
    r = await client.get("/auth/demo")
    assert r.json() == {"enabled": True}

    headers = await _demo_headers(client)
    r = await client.get("/me", headers=headers)
    assert r.status_code == 200
    assert r.json()["email"] == demo_email


async def test_demo_account_can_read_but_not_write(
    client: AsyncClient, demo_email: str, monkeypatch
):
    # 기능을 끈 상태(=녹화 스크립트처럼 주인으로 쓸 때)에서 시연 프로젝트를 만들어 둔다
    monkeypatch.setattr(settings, "enable_demo_account", False)
    r = await client.post("/auth/login", json={"email": demo_email, "password": "secret123"})
    owner = {"Authorization": f"Bearer {r.json()['access_token']}"}
    r = await client.post("/projects", json={"name": "인셉션"}, headers=owner)
    assert r.status_code == 201, r.text
    pid = r.json()["id"]

    monkeypatch.setattr(settings, "enable_demo_account", True)
    headers = await _demo_headers(client)

    # 읽기: 허용
    r = await client.get("/projects", headers=headers)
    assert r.status_code == 200
    assert any(p["id"] == pid for p in r.json()["items"])
    r = await client.get(f"/projects/{pid}", headers=headers)
    assert r.status_code == 200

    # 쓰기: 전부 403
    r = await client.post("/projects", json={"name": "새 작품"}, headers=headers)
    assert r.status_code == 403
    assert "읽기 전용" in r.json()["detail"]
    r = await client.delete(f"/projects/{pid}", headers=headers)
    assert r.status_code == 403
    r = await client.post(
        f"/projects/{pid}/chapters",
        json={"chapter_no": 1, "title": "1화", "text": "본문"},
        headers=headers,
    )
    assert r.status_code == 403

    # 프로젝트는 그대로 남아 있다
    r = await client.get(f"/projects/{pid}", headers=headers)
    assert r.status_code == 200

    # 로그아웃은 허용
    r = await client.post("/auth/logout", headers=headers)
    assert r.status_code == 204


async def test_other_users_unaffected(
    client: AsyncClient, demo_email: str, auth_headers: dict[str, str]
):
    r = await client.post("/projects", json={"name": "일반 작품"}, headers=auth_headers)
    assert r.status_code == 201

    # 시연 계정은 남의 프로젝트를 볼 수 없다(소유권 검사 그대로)
    pid = r.json()["id"]
    headers = await _demo_headers(client)
    r = await client.get(f"/projects/{pid}", headers=headers)
    assert r.status_code in (403, 404)
