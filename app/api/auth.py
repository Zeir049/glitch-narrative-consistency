"""인증 라우터 (계약서 1장). HTTP 입출력만, 로직은 service로."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.db import get_session
from app.core.exceptions import NotFoundError
from app.models import User
from app.core.config import settings
from app.schemas.auth import (
    DemoAccountStatus,
    ForgotPasswordRequest,
    LoginRequest,
    RegisterRequest,
    ResetPasswordRequest,
    TokenResponse,
    UserOut,
)
from app.schemas.common import MessageResponse
from app.service.auth_service import AuthService

router = APIRouter(tags=["auth"])


def require_password_auth() -> None:
    """ENABLE_PASSWORD_AUTH=false 면 이메일·비밀번호 경로를 없는 것처럼 404 (구글 로그인만 남김)."""
    if not settings.enable_password_auth:
        raise NotFoundError("비밀번호 로그인이 꺼져 있습니다.")


_password_only = [Depends(require_password_auth)]


@router.post(
    "/auth/register", response_model=TokenResponse, status_code=201, dependencies=_password_only
)
async def register(
    body: RegisterRequest, session: AsyncSession = Depends(get_session)
) -> TokenResponse:
    return await AuthService(session).register(body)


@router.post("/auth/login", response_model=TokenResponse, dependencies=_password_only)
async def login(
    body: LoginRequest, session: AsyncSession = Depends(get_session)
) -> TokenResponse:
    return await AuthService(session).login(body)


@router.get("/auth/demo", response_model=DemoAccountStatus)
async def demo_status() -> DemoAccountStatus:
    return DemoAccountStatus(enabled=settings.enable_demo_account)


@router.post("/auth/demo", response_model=TokenResponse)
async def demo_login(session: AsyncSession = Depends(get_session)) -> TokenResponse:
    # 시연 계정 하나에만 토큰 발급(꺼져 있거나 계정이 없으면 404). 쓰기 차단은 deps.get_current_user.
    return await AuthService(session).login_demo()


@router.post("/auth/logout", status_code=204)
async def logout(_: User = Depends(get_current_user)) -> Response:
    # stateless JWT: 서버는 상태를 안 가지므로 클라이언트가 토큰을 폐기한다.
    return Response(status_code=204)


@router.post("/auth/forgot-password", response_model=MessageResponse, dependencies=_password_only)
async def forgot_password(
    body: ForgotPasswordRequest, session: AsyncSession = Depends(get_session)
) -> MessageResponse:
    # 토큰 발급 + 메일 발송. 계정 존재 여부를 노출하지 않도록 항상 동일 응답.
    await AuthService(session).request_reset(body.email)
    return MessageResponse(message="재설정 메일을 보냈습니다.")


@router.post("/auth/reset-password", response_model=MessageResponse, dependencies=_password_only)
async def reset_password(
    body: ResetPasswordRequest, session: AsyncSession = Depends(get_session)
) -> MessageResponse:
    await AuthService(session).reset_password(body)
    return MessageResponse(message="비밀번호가 변경되었습니다.")


@router.get("/me", response_model=UserOut, tags=["auth"])
async def me(user: User = Depends(get_current_user)) -> UserOut:
    return UserOut.model_validate(user)
