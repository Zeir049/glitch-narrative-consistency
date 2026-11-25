"""코드 리뷰에서 나온 의심 지점 재현 (현재 동작을 그대로 단언 — 통과 = 의심이 사실).

A. 앞 회차를 늦게 저장하면 교체 방식(관계) 항목이 앞 회차 값으로 되돌아간다.
(B. 큐 투입 실패 고착은 고쳐서 tests/test_enqueue_failure.py 회귀 테스트로 옮겼다.)
"""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.security import hash_password
from app.models import CanonRelation, Project
from app.repository.user_repo import UserRepository
from app.service.canon_service import CanonService


def _rel(relation: str) -> dict:
    return {"relations": [{"entity_key": "CHAR_001->CHAR_002", "from_char": "CHAR_001",
                           "to_char": "CHAR_002", "relation": relation, "attrs": None}]}


async def test_a_late_save_reverts_relation_to_earlier_chapter():
    async with SessionLocal() as s:
        user = await UserRepository(s).create(
            f"repro_{uuid.uuid4().hex[:8]}@x.com", hash_password("x"), "t"
        )
        project = Project(owner_id=user.id, name="repro-late-save")
        s.add(project)
        await s.flush()
        pid = project.id

        cs = CanonService(s)
        await cs.merge_delta(pid, _rel("적대"), source_chapter=3)  # 3화를 먼저 저장
        await cs.merge_delta(pid, _rel("동료"), source_chapter=2)  # 2화를 나중에 저장
        await s.commit()

        rows = (await s.execute(
            select(CanonRelation).where(CanonRelation.project_id == pid).order_by(CanonRelation.version)
        )).scalars().all()
        print("\n[A] rows:", [(r.version, r.source_chapter, r.relation, r.is_current,
                                r.superseded_at_chapter) for r in rows])

        current = [r for r in rows if r.is_current]
        assert len(current) == 1
        # 현재 캐논이 3화의 최신 관계가 아니라 2화의 옛 관계를 가리킨다
        assert current[0].relation == "동료"
        assert current[0].source_chapter == 2

        # 3화 시점 조회에도 3화가 넣은 관계가 보이지 않는다
        at3 = await cs.build_snapshot(pid, at_chapter=3)
        print("[A] at_chapter=3 relations:", [r["relation"] for r in at3.relations])
        assert [r["relation"] for r in at3.relations] == ["동료"]

        # 4화 검수가 받는 N-1(=3화) 스냅샷도 같다 → 4화는 '동료' 기준으로 대조된다
        at_for_ch4 = await cs.build_snapshot(pid, at_chapter=4 - 1)
        assert [r["relation"] for r in at_for_ch4.relations] == ["동료"]

