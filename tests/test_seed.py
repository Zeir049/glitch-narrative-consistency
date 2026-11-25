"""시드 데이터 테스트: 멱등성 + '1~3화 저장, 4화 신규' 구조 + 캐논 버전 보존."""
from __future__ import annotations

import pytest
from sqlalchemy import select

import app.seed as app_seed
from app.core.config import Settings, settings
from app.core.db import SessionLocal
from app.main import app, lifespan
from app.models import CanonWorldview, Chapter, Project
from app.repository.chapter_repo import ChapterRepository
from app.repository.user_repo import UserRepository
from app.seed import DEMO_EMAIL, seed_demo


async def test_seed_demo_idempotent_and_shape():
    async with SessionLocal() as s:
        # 첫 호출(또는 이미 시드됨) → 둘째 호출은 반드시 멱등(None)
        await seed_demo(s)
        assert await seed_demo(s) is None

        user = await UserRepository(s).get_by_email(DEMO_EMAIL)
        assert user is not None and user.plan == "pro"

        project = (
            await s.execute(select(Project).where(Project.owner_id == user.id))
        ).scalars().first()
        assert project is not None and project.name == "금도끼 은도끼"

        chapters = await ChapterRepository(s).list_by_project(project.id)
        status_by_no = {c.chapter_no: c.status for c in chapters}
        assert len(chapters) == 4
        assert status_by_no[1] == "saved"
        assert status_by_no[2] == "saved"
        assert status_by_no[3] == "saved"
        assert status_by_no[4] == "uploaded"  # 4화 신규

        # 3화에서 currency가 변경됨 → append-only로 2버전 보존, 현재 1개
        currency_rows = (
            await s.execute(
                select(CanonWorldview).where(
                    CanonWorldview.project_id == project.id,
                    CanonWorldview.entity_key == "currency",
                )
            )
        ).scalars().all()
        assert len(currency_rows) == 2
        assert sum(1 for r in currency_rows if r.is_current) == 1


def test_seed_on_startup_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """공개 비밀번호의 시드 계정은 기본으로 만들지 않는다 — 로컬은 .env 로 켠다."""
    monkeypatch.delenv("SEED_ON_STARTUP", raising=False)
    assert Settings(_env_file=None).seed_on_startup is False  # type: ignore[call-arg]


@pytest.mark.parametrize("enabled", [False, True])
async def test_lifespan_seeds_only_when_enabled(
    monkeypatch: pytest.MonkeyPatch, enabled: bool
) -> None:
    calls: list[int] = []

    async def fake_run_seed() -> None:
        calls.append(1)

    monkeypatch.setattr(app_seed, "run_seed", fake_run_seed)
    monkeypatch.setattr(settings, "seed_on_startup", enabled)
    async with lifespan(app):
        pass
    assert calls == ([1] if enabled else [])
