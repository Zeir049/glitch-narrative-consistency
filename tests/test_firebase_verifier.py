"""GoogleFirebaseVerifier — 네트워크 없이 실제 RS256 서명 토큰으로 검증 규칙을 확인한다.

JWKS 조회만 테스트용 키로 바꿔 끼우고, 서명·aud·iss 검증은 실제 jwt.decode 를 탄다.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.exceptions import AuthError
from app.firebase.verifier import FirebaseIdentity, GoogleFirebaseVerifier

PROJECT = "glitch-test-project"
_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


@dataclass
class _SigningKey:
    key: Any


class _StubJwks:
    def get_signing_key_from_jwt(self, token: str) -> _SigningKey:
        return _SigningKey(_KEY.public_key())


def _verifier() -> GoogleFirebaseVerifier:
    v = GoogleFirebaseVerifier(PROJECT)
    v.jwks = _StubJwks()  # type: ignore[assignment]
    return v


def _token(**claims: Any) -> str:
    now = int(time.time())
    payload: dict[str, Any] = {
        "iss": f"https://securetoken.google.com/{PROJECT}",
        "aud": PROJECT,
        "sub": "uid-123",
        "iat": now,
        "exp": now + 600,
        "email": "writer@example.com",
        "name": "Writer",
        "email_verified": True,
    }
    payload.update(claims)
    payload = {k: v for k, v in payload.items() if v is not _MISSING}
    return jwt.encode(payload, _KEY, algorithm="RS256", headers={"kid": "test"})


_MISSING = object()


async def test_verified_email_passes() -> None:
    identity = await _verifier().verify(_token())
    assert identity == FirebaseIdentity(uid="uid-123", email="writer@example.com", name="Writer")


@pytest.mark.parametrize("value", [False, _MISSING, "true", 1, None])
async def test_unverified_email_rejected(value: object) -> None:
    with pytest.raises(AuthError, match="이메일 인증"):
        await _verifier().verify(_token(email_verified=value))


async def test_wrong_audience_still_rejected() -> None:
    with pytest.raises(AuthError):
        await _verifier().verify(_token(aud="someone-else"))
