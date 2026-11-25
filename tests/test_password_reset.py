"""비밀번호 재설정 흐름 테스트."""
from __future__ import annotations

import datetime as dt
import logging
import uuid

import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.core.db import SessionLocal
from app.email.sender import get_email_sender
from app.core.security import hash_token
from app.repository.reset_repo import PasswordResetRepository


async def test_forgot_password_generic_200(client: AsyncClient):
    # 계정 존재 여부와 무관하게 항상 동일 응답
    r = await client.post("/auth/forgot-password", json={"email": "nobody@x.com"})
    assert r.status_code == 200


async def test_reset_password_flow(client: AsyncClient):
    email = f"r_{uuid.uuid4().hex[:8]}@x.com"
    r = await client.post(
        "/auth/register", json={"email": email, "password": "oldpass123"}
    )
    user_id = uuid.UUID(r.json()["user"]["id"])

    # 원본 토큰을 알기 위해 직접 주입(해시만 저장됨)
    raw = "known-reset-token-123"
    async with SessionLocal() as s:
        await PasswordResetRepository(s).create(
            user_id,
            hash_token(raw),
            dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=30),
        )
        await s.commit()

    # 재설정
    r = await client.post(
        "/auth/reset-password", json={"token": raw, "new_password": "newpass123"}
    )
    assert r.status_code == 200

    # 옛 비번 실패, 새 비번 성공
    assert (await client.post("/auth/login", json={"email": email, "password": "oldpass123"})).status_code == 401
    assert (await client.post("/auth/login", json={"email": email, "password": "newpass123"})).status_code == 200

    # 토큰 재사용 → 401
    r = await client.post(
        "/auth/reset-password", json={"token": raw, "new_password": "another123"}
    )
    assert r.status_code == 401


async def test_reset_invalid_token_401(client: AsyncClient):
    r = await client.post(
        "/auth/reset-password", json={"token": "nope", "new_password": "x123456"}
    )
    assert r.status_code == 401


@pytest.mark.parametrize("app_env, link_logged", [("dev", True), ("prod", False)])
async def test_reset_mail_log_hides_token_in_prod(
    client: AsyncClient,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    app_env: str,
    link_logged: bool,
):
    """콘솔 메일은 재설정 링크(원문 토큰)를 logs/api.log 에 남긴다 — prod 에서는 찍지 않는다."""
    monkeypatch.setattr(settings, "app_env", app_env)
    get_email_sender.cache_clear()
    try:
        email = f"m_{uuid.uuid4().hex[:8]}@x.com"
        await client.post("/auth/register", json={"email": email, "password": "oldpass123"})
        with caplog.at_level(logging.INFO, logger="app.email.sender"):
            r = await client.post("/auth/forgot-password", json={"email": email})
        assert r.status_code == 200
        mail_logs = [rec.getMessage() for rec in caplog.records if rec.name == "app.email.sender"]
        assert len(mail_logs) == 1 and email in mail_logs[0]
        assert ("reset-password?token=" in mail_logs[0]) is link_logged
    finally:
        get_email_sender.cache_clear()
