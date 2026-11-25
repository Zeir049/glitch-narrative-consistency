"""라우터 공통 의존성: 현재 사용자 인증."""
from __future__ import annotations

import uuid

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.exceptions import AuthError, PermissionDeniedError
from app.core.security import decode_access_token
from app.models import User
from app.repository.user_repo import UserRepository
from app.service.auth_service import NOT_ALLOWED_MESSAGE, is_email_allowed, is_read_only_demo

bearer_scheme = HTTPBearer(auto_error=False)

# 시연 계정이 쓸 수 있는 요청: 조회 + 로그아웃
_DEMO_ALLOWED_METHODS = {"GET", "HEAD", "OPTIONS"}
_DEMO_ALLOWED_PATHS = {"/auth/logout"}


async def get_current_user(
    request: Request,
    cred: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_session),
) -> User:
    """Authorization: Bearer <token> 검증 → User. 실패 시 401. 시연 계정의 쓰기 요청은 403."""
    if cred is None:
        raise AuthError("인증이 필요합니다.")
    user_id_str = decode_access_token(cred.credentials)
    try:
        user_id = uuid.UUID(user_id_str)
    except ValueError as e:
        raise AuthError("유효하지 않은 토큰입니다.") from e
    user = await UserRepository(session).get_by_id(user_id)
    if user is None:
        raise AuthError("유효하지 않은 토큰입니다.")
    # 허용 목록에서 빠진 계정은 이미 받은 토큰(최대 ACCESS_TOKEN_EXPIRE_MINUTES)으로도 못 들어온다
    if not is_email_allowed(user.email):
        raise PermissionDeniedError(NOT_ALLOWED_MESSAGE)
    if (
        request.method not in _DEMO_ALLOWED_METHODS
        and request.url.path not in _DEMO_ALLOWED_PATHS
        and is_read_only_demo(user)
    ):
        raise PermissionDeniedError("시연 계정은 읽기 전용입니다. 둘러보기만 할 수 있어요.")
    return user
