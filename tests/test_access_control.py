"""개발진 전용 접속 — ALLOWED_EMAILS 허용 목록 + ENABLE_PASSWORD_AUTH 끄기."""
from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.core.db import SessionLocal
from app.firebase.verifier import get_firebase_verifier
from app.repository.user_repo import UserRepository
from app.service.auth_service import is_email_allowed


@pytest.fixture(autouse=True)
def _fake_firebase(monkeypatch):
    monkeypatch.setattr(settings, "firebase_mode", "fake")
    get_firebase_verifier.cache_clear()
    yield
    get_firebase_verifier.cache_clear()


def _email(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}@gmail.com"


# ── 허용 목록 ────────────────────────────────────────────────
async def test_firebase_login_refused_and_no_account_created(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "allowed_emails", "dev@team.com")
    outsider = _email("out")
    r = await client.post("/auth/firebase", json={"id_token": f"fake:{outsider}:외부인"})
    assert r.status_code == 403
    assert "허용되지 않은" in r.json()["detail"]
    async with SessionLocal() as s:
        assert await UserRepository(s).get_by_email(outsider) is None


async def test_firebase_login_allowed_case_insensitive(client: AsyncClient, monkeypatch):
    member = _email("dev")
    monkeypatch.setattr(settings, "allowed_emails", f" other@team.com , {member.upper()} ")
    r = await client.post("/auth/firebase", json={"id_token": f"fake:{member}:개발자"})
    assert r.status_code == 200, r.text


async def test_existing_token_blocked_after_removal(client: AsyncClient, monkeypatch):
    """목록에서 빠지면 이미 받은 토큰으로도 못 들어온다."""
    member = _email("dev")
    r = await client.post("/auth/firebase", json={"id_token": f"fake:{member}:개발자"})
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert (await client.get("/me", headers=headers)).status_code == 200

    monkeypatch.setattr(settings, "allowed_emails", "someone-else@team.com")
    r = await client.get("/me", headers=headers)
    assert r.status_code == 403
    assert (await client.get("/projects", headers=headers)).status_code == 403


async def test_password_register_and_login_respect_allowlist(client: AsyncClient, monkeypatch):
    outsider = _email("out")
    monkeypatch.setattr(settings, "allowed_emails", "dev@team.com")
    r = await client.post("/auth/register", json={"email": outsider, "password": "secret123"})
    assert r.status_code == 403
    r = await client.post("/auth/login", json={"email": outsider, "password": "secret123"})
    assert r.status_code == 403


async def test_empty_allowlist_keeps_everyone(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "allowed_emails", "")
    r = await client.post("/auth/firebase", json={"id_token": f"fake:{_email('any')}:누구나"})
    assert r.status_code == 200


def test_enabled_demo_account_is_exempt(monkeypatch):
    monkeypatch.setattr(settings, "allowed_emails", "dev@team.com")
    monkeypatch.setattr(settings, "demo_account_email", "Demo@Glitch.local")
    monkeypatch.setattr(settings, "enable_demo_account", True)
    assert is_email_allowed("demo@glitch.local")
    monkeypatch.setattr(settings, "enable_demo_account", False)
    assert not is_email_allowed("demo@glitch.local")


# ── 비밀번호 방식 끄기 ─────────────────────────────────────────
async def test_password_routes_404_when_disabled(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "enable_password_auth", False)
    email = _email("pw")
    calls = [
        ("/auth/register", {"email": email, "password": "secret123"}),
        ("/auth/login", {"email": email, "password": "secret123"}),
        ("/auth/forgot-password", {"email": email}),
        ("/auth/reset-password", {"token": "x" * 20, "new_password": "secret123"}),
    ]
    for path, body in calls:
        r = await client.post(path, json=body)
        assert r.status_code == 404, path

    # 구글 로그인은 그대로
    r = await client.post("/auth/firebase", json={"id_token": f"fake:{email}:구글"})
    assert r.status_code == 200
