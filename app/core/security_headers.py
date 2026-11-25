"""보안 응답 헤더(CSP·nosniff·Referrer-Policy·X-Frame-Options) — 모든 응답(API·정적 프론트·/uploads)에 붙인다.

CSP 허용 출처는 frontend/ 가 실제로 쓰는 것만:
  - script  : www.gstatic.com (Firebase SDK ES 모듈), apis.google.com (Firebase Auth 팝업이 gapi 를 불러온다)
  - connect : Firebase Auth REST(identitytoolkit·securetoken·www.googleapis.com)
  - frame   : <프로젝트>.firebaseapp.com/__/auth/iframe (팝업 로그인 중계), accounts.google.com
  - style   : 'unsafe-inline' — 화면 코드가 innerHTML 로 style="..." 속성을 만든다(관계도 색, 아바타 등).
              스크립트는 인라인을 허용하지 않으므로 XSS 로 주입된 <script>·on* 핸들러는 막힌다.
Cross-Origin-Opener-Policy 는 일부러 붙이지 않는다 — signInWithPopup 이 opener 관계를 쓴다.
Firebase 도메인을 바꾸면 CONTENT_SECURITY_POLICY 로 통째로 덮어쓸 수 있다.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response

from app.core.config import settings

DEFAULT_CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self' https://www.gstatic.com https://apis.google.com",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "font-src 'self' data:",
        "connect-src 'self' https://identitytoolkit.googleapis.com"
        " https://securetoken.googleapis.com https://www.googleapis.com",
        "frame-src https://*.firebaseapp.com https://accounts.google.com",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)

# Swagger UI·ReDoc 은 CDN(jsdelivr) 스크립트와 인라인 스크립트를 쓴다 — 켜져 있을 때(개발)만 CSP 를 빼 준다.
# 배포는 ENABLE_API_DOCS=false 라 이 경로 자체가 없다.
_API_DOC_PATHS = frozenset({"/docs", "/docs/oauth2-redirect", "/redoc"})


def security_headers_for(path: str) -> dict[str, str]:
    headers = {
        "X-Content-Type-Options": "nosniff",  # /uploads 이미지가 HTML·스크립트로 해석되는 것 방지
        "Referrer-Policy": "strict-origin-when-cross-origin",  # 재설정 링크의 ?token= 이 외부로 새지 않게
        "X-Frame-Options": "DENY",  # 클릭재킹 — 구형 브라우저용, 신형은 frame-ancestors
    }
    if not (settings.enable_api_docs and path in _API_DOC_PATHS):
        headers["Content-Security-Policy"] = settings.content_security_policy or DEFAULT_CSP
    return headers


def register_security_headers(app: FastAPI) -> None:
    @app.middleware("http")
    async def security_headers(  # noqa: ANN202 - starlette 미들웨어 시그니처
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ):
        response = await call_next(request)
        for name, value in security_headers_for(request.url.path).items():
            response.headers.setdefault(name, value)
        return response
