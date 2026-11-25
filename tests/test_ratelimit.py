"""레이트리밋 테스트 — 기본 테스트 환경은 비활성(conftest), 여기서만 켜서 검증."""
from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.core import ratelimit
from app.core.config import settings


@pytest.fixture(autouse=True)
def _clean_state():
    ratelimit.reset_state()
    yield
    ratelimit.reset_state()


async def test_general_limit(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_per_minute", 5)
    for _ in range(5):
        assert (await client.get("/health")).status_code == 200
    r = await client.get("/health")
    assert r.status_code == 429 and "요청이 너무 많습니다" in r.text


async def test_auth_stricter_limit(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_per_minute", 100)
    monkeypatch.setattr(settings, "rate_limit_auth_per_minute", 3)
    for _ in range(3):
        r = await client.post("/auth/login", json={"email": "x@x.com", "password": "wrong1234"})
        assert r.status_code != 429
    r = await client.post("/auth/login", json={"email": "x@x.com", "password": "wrong1234"})
    assert r.status_code == 429  # 무차별 대입 차단

    # 일반 경로는 별도 카운터라 여전히 통과
    assert (await client.get("/health")).status_code == 200


async def test_disabled_when_zero(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_per_minute", 0)
    for _ in range(20):
        assert (await client.get("/health")).status_code == 200


# ── 클라이언트 식별 헤더 (TRUSTED_CLIENT_IP_HEADER) ─────────────────
async def test_client_ip_header_ignored_by_default(client: AsyncClient, monkeypatch):
    """설정이 없으면 CF-Connecting-IP 를 지어내 바꿔 보내도 같은 클라이언트로 센다(우회 불가)."""
    monkeypatch.setattr(settings, "rate_limit_per_minute", 3)
    monkeypatch.setattr(settings, "trusted_client_ip_header", "")
    for i in range(3):
        r = await client.get("/health", headers={"cf-connecting-ip": f"10.0.0.{i}"})
        assert r.status_code == 200
    r = await client.get("/health", headers={"cf-connecting-ip": "10.0.0.99"})
    assert r.status_code == 429


async def test_trusted_header_separates_clients(client: AsyncClient, monkeypatch):
    """프록시 뒤: 지정한 헤더로 사용자를 구분한다(전원이 127.0.0.1 한 칸을 나눠 쓰지 않게)."""
    monkeypatch.setattr(settings, "rate_limit_per_minute", 2)
    monkeypatch.setattr(settings, "trusted_client_ip_header", "x-real-ip")
    a, b = {"x-real-ip": "203.0.113.1"}, {"x-real-ip": "203.0.113.2"}
    for _ in range(2):
        assert (await client.get("/health", headers=a)).status_code == 200
    assert (await client.get("/health", headers=a)).status_code == 429
    assert (await client.get("/health", headers=b)).status_code == 200


async def test_trusted_header_list_uses_last_value(client: AsyncClient, monkeypatch):
    """X-Forwarded-For 목록은 마지막(프록시가 붙인) 값만 본다 — 앞쪽을 바꿔도 같은 클라이언트."""
    monkeypatch.setattr(settings, "rate_limit_per_minute", 1)
    monkeypatch.setattr(settings, "trusted_client_ip_header", "x-forwarded-for")
    r = await client.get("/health", headers={"x-forwarded-for": "1.1.1.1, 198.51.100.7"})
    assert r.status_code == 200
    r = await client.get("/health", headers={"x-forwarded-for": "2.2.2.2, 198.51.100.7"})
    assert r.status_code == 429
