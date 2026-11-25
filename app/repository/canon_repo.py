"""캐논 테이블 공통 DB 접근. append-only 버전 관리의 SQL은 여기에만.

7개 canon_* 테이블이 동일한 버전 컬럼을 공유하므로 모델 클래스를 인자로 받아
제네릭하게 처리한다.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import and_, exists, func, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import Base


class CanonRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def get_current(
        self, model: type[Base], project_id: uuid.UUID, entity_key: str
    ) -> Any | None:
        """같은 항목(entity_key)의 현재 유효 버전 1건."""
        res = await self.s.execute(
            select(model).where(
                model.project_id == project_id,  # type: ignore[attr-defined]
                model.entity_key == entity_key,  # type: ignore[attr-defined]
                model.is_current.is_(True),  # type: ignore[attr-defined]
            )
        )
        return res.scalar_one_or_none()

    async def list_current(
        self, model: type[Base], project_id: uuid.UUID
    ) -> list[Any]:
        """현재 캐논(is_current=true) 전체."""
        res = await self.s.execute(
            select(model).where(
                model.project_id == project_id,  # type: ignore[attr-defined]
                model.is_current.is_(True),  # type: ignore[attr-defined]
            )
        )
        return list(res.scalars().all())

    async def list_at_chapter(
        self, model: type[Base], project_id: uuid.UUID, chapter_no: int
    ) -> list[Any]:
        """N화 시점에 유효했던 버전들.

        source_chapter <= N AND (superseded_at_chapter IS NULL OR superseded_at_chapter > N).
        철회된 행은 superseded_at_chapter = source_chapter 라 자기 회차부터 빠지고,
        복원된 행은 superseded_at_chapter 가 NULL 로 돌아가 다시 잡힌다.
        """
        res = await self.s.execute(
            select(model).where(
                model.project_id == project_id,  # type: ignore[attr-defined]
                model.source_chapter <= chapter_no,  # type: ignore[attr-defined]
                or_(
                    model.superseded_at_chapter.is_(None),  # type: ignore[attr-defined]
                    model.superseded_at_chapter > chapter_no,  # type: ignore[attr-defined]
                ),
            )
        )
        return list(res.scalars().all())

    async def list_by_source(
        self, model: type[Base], project_id: uuid.UUID, chapter_no: int
    ) -> list[Any]:
        """N화가 캐논에 넣은 행 전부 — 이후 회차에서 대체·철회된 버전도 포함(is_current 무관).

        회차별 시각화용. 같은 entity_key 가 여러 버전(같은 회차 재저장)일 수 있어 version 순.
        """
        res = await self.s.execute(
            select(model)
            .where(
                model.project_id == project_id,  # type: ignore[attr-defined]
                model.source_chapter == chapter_no,  # type: ignore[attr-defined]
            )
            .order_by(model.version)  # type: ignore[attr-defined]
        )
        return list(res.scalars().all())

    async def has_contribution(
        self, models: list[type[Base]], project_id: uuid.UUID, source_chapter: int
    ) -> bool:
        """여러 캐논 테이블 중 하나라도 source_chapter 출처 행이 있으면 True — EXISTS 를 OR 로 한 번에."""
        conds = [
            exists().where(
                m.project_id == project_id,  # type: ignore[attr-defined]
                m.source_chapter == source_chapter,  # type: ignore[attr-defined]
            )
            for m in models
        ]
        return bool(await self.s.scalar(select(or_(*conds))))

    async def list_current_by_source(
        self, model: type[Base], project_id: uuid.UUID, source_chapter: int
    ) -> list[Any]:
        """이 회차가 출처인 현재 버전들 — 재저장 시 철회 후보 탐색용."""
        res = await self.s.execute(
            select(model).where(
                model.project_id == project_id,  # type: ignore[attr-defined]
                model.is_current.is_(True),  # type: ignore[attr-defined]
                model.source_chapter == source_chapter,  # type: ignore[attr-defined]
            )
        )
        return list(res.scalars().all())

    async def get_latest_excluding_source(
        self,
        model: type[Base],
        project_id: uuid.UUID,
        entity_key: str,
        source_chapter: int,
    ) -> Any | None:
        """다른 회차가 만든 가장 최신 버전 — 철회 시 복원 대상.

        같은 회차가 만든 버전들은 전부 '철회되는 기여'이므로 건너뛴다.
        """
        res = await self.s.execute(
            select(model)
            .where(
                model.project_id == project_id,  # type: ignore[attr-defined]
                model.entity_key == entity_key,  # type: ignore[attr-defined]
                model.source_chapter != source_chapter,  # type: ignore[attr-defined]
            )
            .order_by(model.version.desc())  # type: ignore[attr-defined]
            .limit(1)
        )
        return res.scalar_one_or_none()

    async def get_restore_candidate(
        self,
        model: type[Base],
        project_id: uuid.UUID,
        entity_key: str,
        source_chapter: int,
    ) -> Any | None:
        """철회 시 복원 대상 — 회차 단위로 고른다.

        N화가 k 의 기여를 거둘 때, N 이외의 회차 c 각각이 k 에 남긴 **최대 version 행**을 보고,
        그 행이 철회 상태(is_current=false AND superseded_by IS NULL)가 아니면 c 는 아직 k 를
        주장하는 것이다. 그런 회차들 중 **source_chapter 가 가장 큰** 회차의 그 행을 돌려준다.

        정렬이 version desc 가 아니라 source_chapter desc 인 이유: 참조 모델("N화 시점 캐논 =
        1..N화 각각의 최종 저장 델타를 회차 순으로 겹친 것")에서 N 을 뺀 결과가 정확히 이것이다.
        순서대로 저장할 때는 둘이 같지만 명세의 기준은 회차 순이다.

        단순히 superseded_by IS NOT NULL 로 거르면 부족하다: v1 을 밀어낸 v2 가 같은 회차 것이고
        그 v2 가 뒤에 철회됐으면 그 회차의 기여는 통째로 거둬진 것인데 v1 은 superseded_by 를
        갖고 있어 후보로 남는다(속성 테스트 반례 A'). 그래서 회차의 최신 행으로 판단한다.

        get_latest_excluding_source 는 attrs 누적 기준 선택에 쓰이므로 동작을 바꾸지 않는다.
        """
        latest_per_chapter = (
            select(
                model.source_chapter.label("sc"),  # type: ignore[attr-defined]
                func.max(model.version).label("mv"),  # type: ignore[attr-defined]
            )
            .where(
                model.project_id == project_id,  # type: ignore[attr-defined]
                model.entity_key == entity_key,  # type: ignore[attr-defined]
                model.source_chapter != source_chapter,  # type: ignore[attr-defined]
            )
            .group_by(model.source_chapter)  # type: ignore[attr-defined]
            .subquery()
        )
        retired = and_(
            model.is_current.is_(False),  # type: ignore[attr-defined]
            model.superseded_by.is_(None),  # type: ignore[attr-defined]
        )
        res = await self.s.execute(
            select(model)
            .join(
                latest_per_chapter,
                and_(
                    model.source_chapter == latest_per_chapter.c.sc,  # type: ignore[attr-defined]
                    model.version == latest_per_chapter.c.mv,  # type: ignore[attr-defined]
                ),
            )
            .where(
                model.project_id == project_id,  # type: ignore[attr-defined]
                model.entity_key == entity_key,  # type: ignore[attr-defined]
                not_(retired),
            )
            .order_by(model.source_chapter.desc())  # type: ignore[attr-defined]
            .limit(1)
        )
        return res.scalar_one_or_none()

    async def get_max_version(
        self, model: type[Base], project_id: uuid.UUID, entity_key: str
    ) -> int:
        """이 항목의 최대 버전(철회된 버전 포함). 없으면 0 — 재추가 시 이어 매김용."""
        res = await self.s.scalar(
            select(func.coalesce(func.max(model.version), 0)).where(  # type: ignore[attr-defined]
                model.project_id == project_id,  # type: ignore[attr-defined]
                model.entity_key == entity_key,  # type: ignore[attr-defined]
            )
        )
        return int(res or 0)

    async def insert_version(
        self,
        model: type[Base],
        project_id: uuid.UUID,
        entity_key: str,
        content: dict,
        version: int,
        source_chapter: int,
    ) -> Any:
        """새 버전 행 INSERT (is_current=true)."""
        obj = model(
            project_id=project_id,
            entity_key=entity_key,
            source_chapter=source_chapter,
            version=version,
            is_current=True,
            **content,
        )
        self.s.add(obj)
        await self.s.flush()
        return obj
