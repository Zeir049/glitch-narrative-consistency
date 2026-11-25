"""APP_ENV=prod 기동 가드 — 위험한 설정이면 Settings 생성(=api·worker 기동)이 실패한다.

Settings 는 .env 를 읽으므로 _env_file=None 으로 끊고, 검사 대상 값은 인자로 준다.
(conftest 가 넣은 DATABASE_URL 등 환경변수는 그대로 들어오지만 가드와 무관하다.)
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import JWT_SECRET_PLACEHOLDER, Settings

SAFE_SECRET = "x" * 48


def _prod(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "prod",
        "jwt_secret": SAFE_SECRET,
        "firebase_mode": "google",
        "seed_on_startup": False,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def test_prod_with_safe_settings_starts() -> None:
    s = _prod()
    assert s.app_env == "prod"


def test_default_env_is_dev_and_allows_dev_conveniences() -> None:
    s = Settings(
        _env_file=None,  # type: ignore[call-arg]
        jwt_secret=JWT_SECRET_PLACEHOLDER,
        firebase_mode="fake",
        seed_on_startup=True,
    )
    assert s.app_env == "dev"


def test_prod_refuses_placeholder_jwt_secret() -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET 이 저장소에 공개된 자리표시자"):
        _prod(jwt_secret=JWT_SECRET_PLACEHOLDER)


def test_prod_refuses_short_jwt_secret() -> None:
    with pytest.raises(ValidationError, match="32자 미만"):
        _prod(jwt_secret="s" * 31)


def test_prod_accepts_exactly_min_length_secret() -> None:
    assert _prod(jwt_secret="s" * 32).jwt_secret == "s" * 32


@pytest.mark.parametrize("mode", ["fake", "FAKE"])
def test_prod_refuses_fake_firebase(mode: str) -> None:
    with pytest.raises(ValidationError, match="FIREBASE_MODE=fake"):
        _prod(firebase_mode=mode)


def test_prod_refuses_seed_on_startup() -> None:
    with pytest.raises(ValidationError, match="SEED_ON_STARTUP=true"):
        _prod(seed_on_startup=True)


def test_prod_lists_every_problem_at_once() -> None:
    with pytest.raises(ValidationError) as exc:
        _prod(jwt_secret=JWT_SECRET_PLACEHOLDER, firebase_mode="fake", seed_on_startup=True)
    msg = str(exc.value)
    assert "JWT_SECRET" in msg and "FIREBASE_MODE=fake" in msg and "SEED_ON_STARTUP=true" in msg


def test_prod_guard_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """compose 가 쓰는 경로(환경변수)로도 같은 가드가 걸린다."""
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("JWT_SECRET", JWT_SECRET_PLACEHOLDER)
    monkeypatch.setenv("FIREBASE_MODE", "google")
    monkeypatch.setenv("SEED_ON_STARTUP", "false")
    with pytest.raises(ValidationError, match="APP_ENV=prod"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_unknown_app_env_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="production")  # type: ignore[call-arg, arg-type]
