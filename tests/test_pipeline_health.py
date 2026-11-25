"""분석 파이프라인 연결 사전 점검 (GET /pipeline/health + RemotePipeline.health)."""
from __future__ import annotations

import httpx
import pytest
from httpx import AsyncClient

from app.pipeline.remote import RemotePipeline

URL = "/pipeline/health"


# ── API ─────────────────────────────────────────────────────
async def test_health_fake_ok(client: AsyncClient, auth_headers: dict) -> None:
    r = await client.get(URL, headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["mode"] == "fake"
    assert set(body) == {"ok", "mode", "message"}


async def test_health_hides_pipeline_info(client: AsyncClient, auth_headers: dict, monkeypatch) -> None:
    """구현체의 부가 정보(경로 등)는 응답에 없고 서버 로그에만 남는다."""
    import logging

    from app.pipeline.interface import PipelineHealth

    # caplog 대신 로거에 직접 붙인다 — 같은 세션의 alembic fileConfig 가 루트 레벨을 WARN 으로 올려 둔다.
    # 레벨은 setLevel 로 바꿔야 로거의 isEnabledFor 캐시가 비워진다(속성 대입은 캐시에 안 닿음).
    records: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    svc_logger = logging.getLogger("app.service.pipeline_service")
    handler = _Capture(level=logging.INFO)
    old_level = svc_logger.level
    svc_logger.setLevel(logging.INFO)
    svc_logger.addHandler(handler)

    class _Remote:
        async def health(self):
            return PipelineHealth(
                ok=True, mode="remote", message="AI 서버 연결됨",
                info={"scope": {"data_root": "C:/secret/Data/stories"}},
            )

    monkeypatch.setattr("app.service.pipeline_service.get_pipeline", lambda: _Remote())
    try:
        r = await client.get(URL, headers=auth_headers)
    finally:
        svc_logger.removeHandler(handler)
        svc_logger.setLevel(old_level)
    assert r.status_code == 200
    assert "info" not in r.json()
    assert "data_root" not in r.text
    assert any("C:/secret/Data/stories" in m for m in records)


async def test_health_requires_auth(client: AsyncClient) -> None:
    r = await client.get(URL)
    assert r.status_code == 401


async def test_health_reports_down_pipeline_as_200(
    client: AsyncClient, auth_headers: dict, monkeypatch
) -> None:
    """AI 서버가 꺼져 있어도 500 이 아니라 ok=false + 원인으로 내려온다."""

    class _Down:
        async def health(self):
            return RemotePipeline._down("AI 서버에 연결할 수 없습니다.")

    monkeypatch.setattr("app.service.pipeline_service.get_pipeline", lambda: _Down())
    r = await client.get(URL, headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["mode"] == "remote"
    assert "연결할 수 없습니다" in body["message"]


# ── RemotePipeline.health 의 실패 분류 ─────────────────────────
def _patch_transport(monkeypatch, handler) -> None:
    """RemotePipeline 이 만드는 AsyncClient 에 가짜 전송 계층을 끼운다."""
    real = httpx.AsyncClient
    monkeypatch.setattr(
        "app.pipeline.remote.httpx.AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )


async def test_remote_health_ok(monkeypatch) -> None:
    _patch_transport(
        monkeypatch,
        lambda req: httpx.Response(200, json={"ok": True, "extract_mode": "llm", "review_mode": "llm"}),
    )
    h = await RemotePipeline("http://ai").health()
    assert h.ok is True
    assert h.message == "AI 서버 연결됨"
    assert h.info["extract_mode"] == "llm"


# ── echo 모드 거부 ──────────────────────────────────────────
ECHO_HEALTH = {"ok": True, "extract_mode": "echo", "review_mode": "llm"}


async def test_remote_health_echo_is_down(monkeypatch) -> None:
    _patch_transport(monkeypatch, lambda req: httpx.Response(200, json=ECHO_HEALTH))
    h = await RemotePipeline("http://ai").health()
    assert h.ok is False
    assert "echo" in h.message


async def test_remote_health_echo_allowed(monkeypatch) -> None:
    _patch_transport(monkeypatch, lambda req: httpx.Response(200, json=ECHO_HEALTH))
    h = await RemotePipeline("http://ai", allow_echo=True).health()
    assert h.ok is True


async def test_remote_extract_refuses_echo(monkeypatch) -> None:
    """extract 가 echo 면 /extract 를 부르지 않고 거부한다(샘플이 캐논에 섞이지 않게)."""
    from app.pipeline.interface import CanonSnapshot
    from app.pipeline.remote import PipelineEchoError

    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.path)
        return httpx.Response(200, json=ECHO_HEALTH)

    _patch_transport(monkeypatch, handler)
    with pytest.raises(PipelineEchoError, match="echo"):
        await RemotePipeline("http://ai").extract_chapter("본문", CanonSnapshot(project_id="p"))
    assert calls == ["/health"]


async def test_remote_review_checks_its_own_stage(monkeypatch) -> None:
    """review_mode 가 llm 이면 extract 가 echo 여도 검수는 진행한다."""
    from app.pipeline.interface import CanonDelta, CanonSnapshot

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/health":
            return httpx.Response(200, json=ECHO_HEALTH)
        return httpx.Response(200, json={"contradictions": []})

    _patch_transport(monkeypatch, handler)
    out = await RemotePipeline("http://ai").review_chapter(
        "본문", CanonSnapshot(project_id="p"), CanonDelta()
    )
    assert out.contradictions == []


async def test_remote_health_warns_when_embedder_loading(monkeypatch) -> None:
    _patch_transport(
        monkeypatch,
        lambda req: httpx.Response(
            200, json={"ok": True, "review_mode": "llm", "scope": {"embedder_loaded": False}}
        ),
    )
    h = await RemotePipeline("http://ai").health()
    assert h.ok is True
    assert "임베딩 모델 준비 중" in h.message


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (httpx.ConnectError("refused"), "연결할 수 없습니다"),
        (httpx.ReadTimeout("slow"), "응답하지 않습니다"),
        (httpx.RemoteProtocolError("broken"), "RemoteProtocolError"),
    ],
)
async def test_remote_health_transport_errors(monkeypatch, error, expected) -> None:
    def handler(req):
        raise error

    _patch_transport(monkeypatch, handler)
    h = await RemotePipeline("http://ai").health()
    assert h.ok is False
    assert expected in h.message


async def test_remote_health_bad_status(monkeypatch) -> None:
    _patch_transport(monkeypatch, lambda req: httpx.Response(503, text="down"))
    h = await RemotePipeline("http://ai").health()
    assert h.ok is False
    assert "HTTP 503" in h.message


async def test_remote_health_not_json(monkeypatch) -> None:
    _patch_transport(monkeypatch, lambda req: httpx.Response(200, text="<html>"))
    h = await RemotePipeline("http://ai").health()
    assert h.ok is False
    assert "JSON" in h.message


async def test_remote_health_real_refused_port() -> None:
    """실제 소켓: 아무도 안 듣는 포트면 ConnectError 경로로 떨어진다."""
    h = await RemotePipeline("http://127.0.0.1:9").health()
    assert h.ok is False
    assert "연결할 수 없습니다" in h.message


# ── AI 서버 공유 토큰 ──────────────────────────────────────
LLM_HEALTH = {"ok": True, "extract_mode": "llm", "review_mode": "llm"}


async def test_remote_sends_token_on_every_call(monkeypatch) -> None:
    """토큰을 주면 /health·/review 모두 Authorization: Bearer 를 싣는다."""
    from app.pipeline.interface import CanonDelta, CanonSnapshot

    seen: list[tuple[str, str | None]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.url.path, req.headers.get("authorization")))
        if req.url.path == "/health":
            return httpx.Response(200, json=LLM_HEALTH)
        return httpx.Response(200, json={"contradictions": []})

    _patch_transport(monkeypatch, handler)
    await RemotePipeline("http://ai", token="s3cret").review_chapter(
        "본문", CanonSnapshot(project_id="p"), CanonDelta()
    )
    assert seen == [("/health", "Bearer s3cret"), ("/review", "Bearer s3cret")]


async def test_remote_without_token_sends_no_header(monkeypatch) -> None:
    seen: list[str | None] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.headers.get("authorization"))
        return httpx.Response(200, json=LLM_HEALTH)

    _patch_transport(monkeypatch, handler)
    await RemotePipeline("http://ai").health()
    assert seen == [None]


async def test_remote_health_unauthorized(monkeypatch) -> None:
    _patch_transport(monkeypatch, lambda req: httpx.Response(401, json={"detail": "인증 실패"}))
    h = await RemotePipeline("http://ai", token="wrong").health()
    assert h.ok is False
    assert "AI_SERVER_TOKEN" in h.message
