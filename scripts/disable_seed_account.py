"""시드 데모 계정(cho@example.com) 정리 — SEED_ON_STARTUP 을 꺼도 이미 만든 계정은 DB 에 남는다.

  .venv\\Scripts\\python.exe -m scripts.disable_seed_account            비밀번호 무효화(계정·프로젝트 유지)
  .venv\\Scripts\\python.exe -m scripts.disable_seed_account --delete   계정 삭제(프로젝트·회차·캐논까지 CASCADE)

대상 DB 는 .env 의 DATABASE_URL. 무효화 후 그 계정은 비밀번호로 로그인되지 않는다
(로컬 데모가 다시 필요하면 계정을 지운 뒤 SEED_ON_STARTUP=true 로 한 번 띄우면 다시 만들어진다).
"""
from __future__ import annotations

import argparse
import asyncio
import sys

# bcrypt 해시가 아닌 값 — verify_password 가 항상 False 를 돌려준다(app/core/security.py).
DISABLED_PASSWORD_HASH = "!disabled-seed-account"


async def run(delete: bool) -> str:
    from app.core.db import SessionLocal, engine
    from app.repository.user_repo import UserRepository
    from app.seed import DEMO_EMAIL

    try:
        async with SessionLocal() as s:
            user = await UserRepository(s).get_by_email(DEMO_EMAIL)
            if user is None:
                return f"{DEMO_EMAIL} 계정이 없습니다 — 할 일 없음."
            if delete:
                await s.delete(user)
                result = f"{DEMO_EMAIL} 계정을 삭제했습니다(프로젝트 포함)."
            else:
                user.password_hash = DISABLED_PASSWORD_HASH
                result = f"{DEMO_EMAIL} 계정의 비밀번호를 무효화했습니다."
            await s.commit()
            return result
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--delete", action="store_true", help="무효화 대신 계정을 삭제한다")
    args = parser.parse_args()
    print(asyncio.run(run(args.delete)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
