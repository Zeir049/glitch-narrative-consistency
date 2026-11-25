"""진행 중 분석 작업 조회. 상태는 chapters.status 하나에서 파생한다(불변원칙 4, D7)."""
from __future__ import annotations

import datetime as dt

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ChapterStatus, User
from app.repository.chapter_repo import ChapterRepository
from app.schemas.job import ActiveJob, ActiveJobsOut
from app.service.chapter_service import ChapterService

ACTIVE_STATUSES = [ChapterStatus.extracting.value, ChapterStatus.reviewing.value]


class JobService:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session
        self.chapters = ChapterRepository(session)

    async def list_active(self, user: User) -> ActiveJobsOut:
        """로그인 사용자 소유 프로젝트의 extracting/reviewing 회차 (소유자 필터는 쿼리에서).

        고착(워커 사망 추정) 회차는 여기서 failed 로 정리하고 목록에서 뺀다 — 남겨 두면 아무도
        상태를 바꾸지 않아 작업 창 폴링이 끝나지 않는다. 작업 창은 결과 조회로 실패를 받는다.
        """
        rows = await self.chapters.list_active_for_owner(user.id, ACTIVE_STATUSES)
        chapter_service = ChapterService(self.s)
        for chapter, _, _ in rows:
            await chapter_service.fail_if_stale(chapter)
        rows = [r for r in rows if r[0].status in ACTIVE_STATUSES]
        now = dt.datetime.now(dt.timezone.utc)
        return ActiveJobsOut(
            items=[
                ActiveJob(
                    project_id=chapter.project_id,
                    project_name=project_name,
                    chapter_no=chapter.chapter_no,
                    title=chapter.title,
                    status=chapter.status,
                    progress=progress,
                    started_at=chapter.updated_at,
                    stale=ChapterService.is_stale(chapter, now),
                )
                for chapter, project_name, progress in rows
            ]
        )
