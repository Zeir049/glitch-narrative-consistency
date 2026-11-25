"""임시 스모크 테스트 테이블(smoke_checks) 제거

app/smoke/(전체 스택 배선 확인용 일회성 모듈)를 지웠다. 그 모듈은 Alembic 체인 밖에서
기동 때 smoke_checks 를 직접 만들었으므로(ENABLE_SMOKE=true 였던 DB 에만 있다) 여기서 IF EXISTS 로 지운다.
안에 든 것은 에코 판정 기록뿐이라 보존할 데이터가 없다.

downgrade 는 아무것도 하지 않는다 — 체인 밖 임시 테이블이었고 되살릴 코드도 없다.

Revision ID: 0006_drop_smoke_checks
Revises: 0005_canon_attrs
Create Date: 2026-11-01
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0006_drop_smoke_checks"
down_revision: Union[str, None] = "0005_canon_attrs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS smoke_checks")


def downgrade() -> None:
    pass
