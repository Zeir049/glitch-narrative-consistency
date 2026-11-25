"""start_tunnel.bat 사전 점검(scripts.public_preflight)과 시드 계정 정리(scripts.disable_seed_account)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.core.config import JWT_SECRET_PLACEHOLDER, settings
from app.core.db import SessionLocal
from app.core.security import verify_password
from app.repository.user_repo import UserRepository
from app.seed import DEMO_EMAIL, DEMO_PASSWORD, seed_demo
from scripts import disable_seed_account, public_preflight

ROOT = Path(__file__).resolve().parent.parent


def _run_preflight(**env: str) -> subprocess.CompletedProcess[str]:
    full_env = {**os.environ, "PYTHONUTF8": "1", **env}
    return subprocess.run(
        [sys.executable, "-m", "scripts.public_preflight"],
        cwd=ROOT, env=full_env, capture_output=True, text=True, timeout=120,
        encoding="utf-8",  # 자식은 PYTHONUTF8=1 로 UTF-8 출력 — 부모 기본(cp949)으로 풀면 리더 스레드가 죽어 stdout=None
    )


def test_preflight_blocks_unsafe_prod_env() -> None:
    """bat 이 APP_ENV=prod 로 부를 때 .env 가 위험하면 종료 코드 1 + 이유."""
    r = _run_preflight(
        APP_ENV="prod", JWT_SECRET=JWT_SECRET_PLACEHOLDER, FIREBASE_MODE="fake", SEED_ON_STARTUP="true"
    )
    assert r.returncode == 1, r.stdout + r.stderr
    assert "[BLOCK]" in r.stdout
    assert "JWT_SECRET" in r.stdout and "FIREBASE_MODE=fake" in r.stdout and "SEED_ON_STARTUP" in r.stdout


async def _seed_password_ok() -> bool:
    async with SessionLocal() as s:
        user = await UserRepository(s).get_by_email(DEMO_EMAIL)
        return user is not None and verify_password(DEMO_PASSWORD, user.password_hash)


async def test_disable_then_delete_seed_account() -> None:
    async with SessionLocal() as s:
        await seed_demo(s)  # 없으면 만든다(이미 있으면 멱등)
    try:
        # 비밀번호가 살아 있는 상태로 되돌린 뒤 시작
        async with SessionLocal() as s:
            user = await UserRepository(s).get_by_email(DEMO_EMAIL)
            assert user is not None
            from app.core.security import hash_password

            user.password_hash = hash_password(DEMO_PASSWORD)
            await s.commit()
        assert await public_preflight._seed_account_has_public_password() is True

        await disable_seed_account.run(delete=False)
        assert await _seed_password_ok() is False
        assert await public_preflight._seed_account_has_public_password() is False

        await disable_seed_account.run(delete=True)
        async with SessionLocal() as s:
            assert await UserRepository(s).get_by_email(DEMO_EMAIL) is None
        assert "할 일 없음" in await disable_seed_account.run(delete=True)
    finally:
        async with SessionLocal() as s:
            await seed_demo(s)  # 다른 테스트(test_seed)가 기대하는 시드 상태 복구


@pytest.mark.parametrize(
    "flags, expected",
    [({}, 0), ({"enable_subscription": True}, 2)],
)
def test_preflight_flags(monkeypatch: pytest.MonkeyPatch, flags: dict[str, bool], expected: int) -> None:
    async def no_seed() -> bool:
        return False

    monkeypatch.setattr(public_preflight, "_seed_account_has_public_password", no_seed)
    for name in public_preflight.OPTIONAL_FLAGS:
        monkeypatch.setattr(settings, name.lower(), flags.get(name.lower(), False))
    assert public_preflight.main() == expected


def test_preflight_blocks_exposed_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def exposed() -> bool:
        return True

    monkeypatch.setattr(public_preflight, "_seed_account_has_public_password", exposed)
    assert public_preflight.main() == 1
