"""API 문서(/docs·/redoc·/openapi.json) 노출 — ENABLE_API_DOCS."""
from __future__ import annotations

from httpx import AsyncClient

from app.core.config import settings
from app.main import api_docs_kwargs


async def test_docs_on_by_default(client: AsyncClient):
    assert settings.enable_api_docs is True
    assert (await client.get("/openapi.json")).status_code == 200


def test_docs_off_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "enable_api_docs", False)
    assert api_docs_kwargs() == {"docs_url": None, "redoc_url": None, "openapi_url": None}


async def test_smoke_routes_are_gone(client: AsyncClient):
    """임시 스모크 모듈은 지웠다 — /smoke 는 API 가 아니라 프론트 정적 서빙의 404."""
    paths = (await client.get("/openapi.json")).json()["paths"]
    assert not [p for p in paths if "smoke" in p]
