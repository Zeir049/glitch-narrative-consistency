"""인증 요청/응답 스키마 (계약서 1장)."""
from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field


class RegisterRequest(BaseModel):
    # email 형식 엄격검증은 email-validator 의존성을 피하려 str로 둠(프론트/422에서 1차 검증).
    email: str
    password: str = Field(min_length=6)
    name: str | None = None


class LoginRequest(BaseModel):
    email: str
    password: str


class FirebaseLoginRequest(BaseModel):
    id_token: str = Field(min_length=1)


class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=6)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    name: str | None
    plan: str


class TokenResponse(BaseModel):
    access_token: str
    user: UserOut


class DemoAccountStatus(BaseModel):
    """GET /auth/demo — 시연 계정 로그인을 쓸 수 있는지(프론트가 버튼 표시 여부에 씀)."""

    enabled: bool
