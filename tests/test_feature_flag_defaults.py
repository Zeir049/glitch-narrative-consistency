"""공개 배포 안전 기본값 — 미완성 선택 기능은 설정하지 않으면 꺼져 있다."""
from __future__ import annotations

import pytest

from app.core.config import Settings

FLAGS = ("enable_subscription", "enable_sharing", "enable_chat", "enable_notifications")


@pytest.mark.parametrize("flag", FLAGS)
def test_optional_feature_flags_default_off(monkeypatch: pytest.MonkeyPatch, flag: str) -> None:
    monkeypatch.delenv(flag.upper(), raising=False)  # conftest 가 테스트용으로 켠 값을 걷어낸다
    assert getattr(Settings(_env_file=None), flag) is False  # type: ignore[call-arg]
