"""보안 응답 헤더 — API·프론트 정적 파일·오류 응답 모두에 붙는지."""
from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.core.security_headers import DEFAULT_CSP


def _assert_security_headers(headers: object) -> None:
    h = headers  # httpx.Headers (대소문자 무관)
    assert h["x-content-type-options"] == "nosniff"  # type: ignore[index]
    assert h["referrer-policy"] == "strict-origin-when-cross-origin"  # type: ignore[index]
    assert h["x-frame-options"] == "DENY"  # type: ignore[index]
    assert h["content-security-policy"] == DEFAULT_CSP  # type: ignore[index]


@pytest.mark.parametrize("path", ["/health", "/", "/index.html", "/script.js"])
async def test_security_headers_on_api_and_frontend(client: AsyncClient, path: str) -> None:
    r = await client.get(path)
    assert r.status_code == 200
    _assert_security_headers(r.headers)


async def test_security_headers_on_error_responses(client: AsyncClient) -> None:
    r = await client.get("/projects")  # 인증 없음 → 401
    assert r.status_code == 401
    _assert_security_headers(r.headers)


def test_default_csp_allows_only_what_frontend_needs() -> None:
    directives = dict(d.split(" ", 1) for d in DEFAULT_CSP.split("; "))
    assert directives["frame-ancestors"] == "'none'"
    assert directives["object-src"] == "'none'"
    # 인라인 스크립트·eval 은 허용하지 않는다(스타일 속성만 인라인 허용)
    assert "'unsafe-inline'" not in directives["script-src"]
    assert "'unsafe-eval'" not in directives["script-src"]
    assert "https://www.gstatic.com" in directives["script-src"]  # Firebase SDK


async def test_csp_override_from_settings(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "content_security_policy", "default-src 'none'")
    r = await client.get("/health")
    assert r.headers["content-security-policy"] == "default-src 'none'"


@pytest.mark.parametrize("docs_enabled", [True, False])
async def test_api_docs_csp_relaxed_only_when_docs_enabled(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, docs_enabled: bool
) -> None:
    """Swagger UI 는 CDN·인라인 스크립트를 쓰므로 문서가 켜졌을 때만 /docs 에서 CSP 를 뺀다."""
    monkeypatch.setattr(settings, "enable_api_docs", docs_enabled)
    r = await client.get("/docs")
    assert ("content-security-policy" in r.headers) is (not docs_enabled)
    assert r.headers["x-frame-options"] == "DENY"
