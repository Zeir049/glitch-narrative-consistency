"""공개 URL(start_tunnel.bat)로 띄우기 전 점검. 서버를 띄우기 전에 bat 이 부른다.

  .venv\\Scripts\\python.exe -m scripts.public_preflight

종료 코드:
  0 = 통과
  1 = 막아야 함 — APP_ENV=prod 기동 가드 실패(JWT_SECRET·FIREBASE_MODE·SEED_ON_STARTUP),
      또는 공개 비밀번호(README)로 로그인되는 시드 계정이 DB 에 남아 있음
  2 = 경고 — 미완성 선택 기능(구독·공유·채팅·알림)이 켜져 있거나 DB 를 확인하지 못함
"""
from __future__ import annotations

import asyncio
import sys

from pydantic import ValidationError

OPTIONAL_FLAGS = ("ENABLE_SUBSCRIPTION", "ENABLE_SHARING", "ENABLE_CHAT", "ENABLE_NOTIFICATIONS")


async def _seed_account_has_public_password() -> bool:
    from app.core.db import SessionLocal, engine
    from app.core.security import verify_password
    from app.repository.user_repo import UserRepository
    from app.seed import DEMO_EMAIL, DEMO_PASSWORD

    try:
        async with SessionLocal() as s:
            user = await UserRepository(s).get_by_email(DEMO_EMAIL)
            return user is not None and verify_password(DEMO_PASSWORD, user.password_hash)
    finally:
        await engine.dispose()


def main() -> int:
    try:
        from app.core.config import settings
    except ValidationError as e:
        print("[BLOCK] .env 가 공개 실행 조건을 만족하지 않습니다 (TUNNEL_GUIDE.md '공개 전 체크리스트').")
        for err in e.errors():
            print(err["msg"])
        return 1

    try:
        exposed = asyncio.run(_seed_account_has_public_password())
    except Exception as e:  # noqa: BLE001 - DB 미기동 등. 막지는 않고 경고만
        print(f"[WARN] DB 에서 시드 계정을 확인하지 못했습니다: {type(e).__name__}: {e}")
        return 2
    if exposed:
        print(
            "[BLOCK] 시드 계정 cho@example.com 이 README 에 공개된 비밀번호로 로그인됩니다.\n"
            "        .venv\\Scripts\\python.exe -m scripts.disable_seed_account  (비밀번호 무효화)\n"
            "        .venv\\Scripts\\python.exe -m scripts.disable_seed_account --delete  (계정·프로젝트 삭제)"
        )
        return 1

    enabled = [f for f in OPTIONAL_FLAGS if getattr(settings, f.lower())]
    if enabled:
        print(
            "[WARN] 미완성 선택 기능이 켜져 있습니다: " + ", ".join(enabled)
            + " — 공개 시연은 .env 에서 false 권장(구독은 누구나 pro 로 바뀜)."
        )
        return 2
    print("[OK] 공개 실행 점검 통과 (APP_ENV=prod)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
