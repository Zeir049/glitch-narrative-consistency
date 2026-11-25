"""IP별 슬라이딩 윈도 레이트리밋 (인메모리) — 공개 터널 데모 보호용.

- 일반 요청: RATE_LIMIT_PER_MINUTE (기본 240/분), 0이면 비활성.
- /auth/* : RATE_LIMIT_AUTH_PER_MINUTE (기본 30/분) — 무차별 대입 방지.
- 프록시·터널 뒤에서는 모든 요청의 소스 IP가 127.0.0.1이므로 TRUSTED_CLIENT_IP_HEADER
  (프록시가 덮어쓰는 헤더: x-real-ip, cf-connecting-ip 등)로 클라이언트를 구분한다.
  비우면 접속 IP만 본다 — 프록시 없이 받은 헤더는 지어낼 수 있어 우회 수단이 된다.
- 인메모리라 프로세스별로 따로 센다 — 단일 서버 데모엔 충분, 분산 시 Redis로 교체.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.config import settings

_hits: dict[str, deque[float]] = defaultdict(deque)
_last_sweep = 0.0


def _client_key(request: Request) -> str:
    header = settings.trusted_client_ip_header
    if header:
        # X-Forwarded-For 처럼 목록이면 마지막 값 — 믿는 프록시가 직접 붙인 것(앞쪽은 클라이언트가 보낸 것)
        value = request.headers.get(header, "").split(",")[-1].strip()
        if value:
            return value
    return request.client.host if request.client else "unknown"


def reset_state() -> None:
    """테스트용: 카운터 초기화."""
    _hits.clear()


def register_rate_limit(app: FastAPI) -> None:
    @app.middleware("http")
    async def rate_limit(request: Request, call_next):  # noqa: ANN001, ANN202
        limit = settings.rate_limit_per_minute
        if limit <= 0:
            return await call_next(request)

        now = time.monotonic()
        global _last_sweep
        if now - _last_sweep > 60:  # 낡은 클라이언트 키 청소
            _last_sweep = now
            stale = [k for k, dq in _hits.items() if not dq or now - dq[-1] > 120]
            for k in stale:
                _hits.pop(k, None)

        is_auth = request.url.path.startswith("/auth")
        effective = settings.rate_limit_auth_per_minute if is_auth else limit
        dq = _hits[f"{_client_key(request)}:{'auth' if is_auth else 'all'}"]
        cutoff = now - 60
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= effective:
            return JSONResponse(
                status_code=429,
                content={"detail": "요청이 너무 많습니다. 잠시 후 다시 시도해주세요."},
            )
        dq.append(now)
        return await call_next(request)
