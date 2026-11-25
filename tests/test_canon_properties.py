"""캐논 append-only 버전 모델의 속성 기반 테스트 (hypothesis).

`CanonService.merge_delta` 를 임의의 저장 순서로 돌린 뒤, 결과 테이블이 아래 **참조 모델**과
일치하는지 본다. 손으로 고른 시나리오(test_canon_retire.py, test_canon_at_chapter.py)가
아닌 무작위 저장 순서에서도 불변식이 지켜지는지가 목적이다.

참조 모델 (명세)
----------------
저장을 순서대로 접으며 ``latest[chapter_no] = delta`` (같은 회차 재저장은 덮어쓴다).
``reference_at(N)`` 은 빈 dict 에서 시작해 ``for c in sorted(latest): if c <= N: ref.update(latest[c])``.
즉 **"N화 시점 캐논 = 1..N화 각각의 최종 저장 델타를 회차 순으로 겹친 것"** 이다.

검사하는 불변식 (시퀀스를 모두 적용한 뒤)
------------------------------------------
1. entity_key 당 ``is_current=true`` 행 최대 1개, 그리고 현재 ``{k: item}`` == ``reference_at(max_chapter)``.
2. ``N = 0..max_chapter`` 각각 ``list_at_chapter(N)`` 이 entity_key 당 최대 1행이고
   ``{k: item}`` == ``reference_at(N)``.
3. 행 단위로 ``superseded_at_chapter IS NULL`` ⟺ ``is_current``.
4. 멱등: 마지막 저장을 한 번 더 ``merge_delta`` 하면 ``inserted=versioned=retired=restored=0`` 이고
   전체 행 수가 변하지 않는다.
5. append-only: 시퀀스 중 어떤 시점에도 전체 행 수가 줄지 않는다.

세계관 테이블(CanonWorldview)만 쓴다. 키 {a,b,c}, 값 {v1,v2,v3}, 회차 1~4, 시퀀스 길이 1~8.
hypothesis 는 async 테스트를 직접 지원하지 않아 예제마다 ``asyncio.run`` 으로 감싸고,
끝에서 ``engine.dispose()`` 를 해 다음 예제의 새 이벤트 루프가 asyncpg 커넥션을 재사용하지 않게 한다.

2026-11-05 발견된 위반
---------------------
(A) [수정됨] 비감소 순서에서도 깨졌다 — 철회된 버전이 '복원'으로 되살아났다.
    최소 반례(시드 0~4 모두 같은 것으로 수축): ``[(1, {'a': 'v1'}), (1, {}), (2, {'a': 'v1'}), (2, {})]``
    DB 행 (entity_key, version, source_chapter, is_current, superseded_at_chapter, item):
        a  1  1  True   None  v1      ← 1화 재저장에서 철회됐던 행이 2화 철회의 복원 대상으로 되살아남
        a  2  2  False  2     v1
    위반: current {'a': 'v1'} != reference_at(2) {} / at_chapter(1), at_chapter(2) 도 {'a': 'v1'}.
    원인: ``_retire_stale`` 의 복원이 ``get_latest_excluding_source`` 로 "다른 회차의 최신 버전"을
    골랐는데, 그 버전이 자기 회차에서 이미 철회된 것인지(superseded_by 없음) 보지 않았다.
    수정: 복원 후보를 ``CanonRepository.get_restore_candidate`` (superseded_by IS NOT NULL — 새 버전에
    밀려난 행만) 로 한정. 반례는 ``@example`` 로 남겨 회귀를 막는다. 고정 재현은 tests/test_canon_retire.py 에도 있다.

(A') [수정됨] (A) 의 첫 수정(superseded_by IS NOT NULL 로 거르기) 뒤 in_order 를 시드 0~4 × 200예제로
    돌리면 시드 1 을 뺀 넷이 같은 반례로 수축했다: ``[(1, {'a': 'v1'}), (1, {'a': 'v2'}), (1, {}), (2, {'a': 'v1'}), (2, {})]``
        a  1  1  True   None  v1      ← 같은 회차 v2 에 밀렸던 v1 (superseded_by 있음) 이 복원됨
        a  2  1  False  1     v2      ← 1화 재저장에서 철회된 v2
        a  3  2  False  2     v1
    위반: current {'a': 'v1'} != reference_at(2) {} / at_chapter(1), at_chapter(2) 도 {'a': 'v1'}.
    원인: superseded_by IS NOT NULL 만으로는 부족하다 — v1 을 밀어낸 v2 가 같은 회차 것이고 그 v2 가 뒤에
    철회됐으면 1화의 기여는 통째로 거둬진 것이다.
    수정: ``get_restore_candidate`` 를 회차 단위로 — 다른 회차 각각의 최대 version 행이 철회 상태가 아닌 회차 중
    source_chapter 가 가장 큰 회차의 그 행(참조 모델에서 N 을 뺀 것과 같다). 수정 뒤 같은 시드 0~4 × 200예제에서
    반례 없음. 반례는 ``@example`` 로 남긴다. 고정 재현은 tests/test_canon_retire.py 에도 있다.

(B) [미수정 — xfail(strict)] 임의 순서, 앞 회차 늦은 재저장. hypothesis 최소 반례:
    ``[(1, {'a': 'v1'}), (2, {'a': 'v1'}), (1, {})]``
        a  1  1  False  1     v1
    위반: current {} != reference_at(2) {'a': 'v1'} / at_chapter(2) {} != {'a': 'v1'}.
    원인: 2화 저장은 값이 같아 'unchanged' 로 끝나 2화의 기여가 행으로 남지 않고, 1화 재저장이
    유일한 행을 철회하면 2화가 말한 설정까지 함께 사라진다.
    손으로 고른 의심 사례 ``[(1, {'a': 'v1'}), (2, {'a': 'v2'}), (1, {'a': 'v3'})]`` 도 깨진다:
        a  1  1  False  2     v1
        a  2  2  False  1     v2      ← 2화 행이 1화 재저장에 superseded_at_chapter=1 로 내려감
        a  3  1  True   None  v3
    위반: at_chapter(1) 에 a 가 2행 / at_chapter(2) {'a': 'v3'} != {'a': 'v2'} / current 도 v3.
    원인: ``_merge_item`` 이 현재 행의 source_chapter 와 무관하게 ``superseded_at_chapter=source_chapter``
    를 찍고 새 행을 현재로 올린다 — 뒤 회차가 만든 현재 버전을 앞 회차가 덮는다.
"""
from __future__ import annotations

import asyncio
import uuid
from functools import lru_cache

import pytest
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st
from sqlalchemy import func, select

from app.core.db import SessionLocal, engine
from app.core.security import hash_password
from app.models import CanonWorldview, Project
from app.repository.user_repo import UserRepository
from app.service.canon_service import CanonService

KEYS = ["a", "b", "c"]
VALUES = ["v1", "v2", "v3"]
MAX_CHAPTER = 4

Delta = dict[str, str]
Save = tuple[int, Delta]
Sequence = list[Save]

# ── 알려진 반례 (위 docstring) ─────────────────────────────────────
COUNTEREXAMPLE_IN_ORDER: Sequence = [(1, {"a": "v1"}), (1, {}), (2, {"a": "v1"}), (2, {})]
COUNTEREXAMPLE_IN_ORDER_A2: Sequence = [(1, {"a": "v1"}), (1, {"a": "v2"}), (1, {}), (2, {"a": "v1"}), (2, {})]
COUNTEREXAMPLE_ANY_ORDER_MIN: Sequence = [(1, {"a": "v1"}), (2, {"a": "v1"}), (1, {})]
COUNTEREXAMPLE_LATE_RESAVE: Sequence = [(1, {"a": "v1"}), (2, {"a": "v2"}), (1, {"a": "v3"})]

XFAIL_ANY_ORDER = pytest.mark.xfail(
    strict=True,
    reason=(
        "앞 회차 늦은 재저장 시 시점 조회 불변식 위반 — 재현: "
        f"{COUNTEREXAMPLE_ANY_ORDER_MIN} 또는 {COUNTEREXAMPLE_LATE_RESAVE}"
    ),
)

# ── 전략 ───────────────────────────────────────────────────────────
_delta_st = st.dictionaries(st.sampled_from(KEYS), st.sampled_from(VALUES), max_size=len(KEYS))
_save_st = st.tuples(st.integers(min_value=1, max_value=MAX_CHAPTER), _delta_st)
seq_any_order = st.lists(_save_st, min_size=1, max_size=8)
# 비감소 순서: 같은 시퀀스를 회차로 안정 정렬 (같은 회차 재저장의 상대 순서는 유지)
seq_in_order = seq_any_order.map(lambda s: sorted(s, key=lambda x: x[0]))

PROPERTY_SETTINGS = dict(
    max_examples=30,
    deadline=None,
    database=None,  # .hypothesis/ 디렉터리를 저장소에 만들지 않는다 (반례는 @example 로 고정)
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)


# ── 참조 모델 ───────────────────────────────────────────────────────
def fold_latest(seq: Sequence) -> dict[int, Delta]:
    latest: dict[int, Delta] = {}
    for chapter_no, delta in seq:
        latest[chapter_no] = dict(delta)
    return latest


def reference_at(latest: dict[int, Delta], n: int) -> Delta:
    ref: Delta = {}
    for c in sorted(latest):
        if c <= n:
            ref.update(latest[c])
    return ref


# ── DB 헬퍼 ────────────────────────────────────────────────────────
@lru_cache(maxsize=1)
def _pw_hash() -> str:
    return hash_password("x")  # bcrypt 는 느리다 — 예제마다 다시 만들지 않는다


async def _setup(s) -> uuid.UUID:
    user = await UserRepository(s).create(f"cp_{uuid.uuid4().hex[:8]}@x.com", _pw_hash(), "t")
    project = Project(owner_id=user.id, name="canon-properties-test")
    s.add(project)
    await s.flush()
    return project.id


def _wv_items(delta: Delta) -> list[dict]:
    return [{"entity_key": k, "item": v} for k, v in delta.items()]


async def _all_rows(s, pid: uuid.UUID) -> list[CanonWorldview]:
    res = await s.execute(
        select(CanonWorldview)
        .where(CanonWorldview.project_id == pid)
        .order_by(CanonWorldview.entity_key, CanonWorldview.version, CanonWorldview.source_chapter)
    )
    return list(res.scalars().all())


async def _count(s, pid: uuid.UUID) -> int:
    return int(
        await s.scalar(select(func.count()).select_from(CanonWorldview).where(CanonWorldview.project_id == pid))
    )


def _dump(rows: list[CanonWorldview]) -> str:
    head = "  entity_key version source_chapter is_current superseded_at_chapter item"
    body = [
        f"  {r.entity_key:<10} {r.version:<7} {r.source_chapter:<14} {str(r.is_current):<10} "
        f"{str(r.superseded_at_chapter):<21} {r.item}"
        for r in rows
    ]
    return "\n".join([head, *body])


def _key_map(rows: list[CanonWorldview], violations: list[str], label: str) -> Delta:
    """행 목록을 {entity_key: item} 으로 접되, 키가 겹치면 위반으로 기록."""
    out: Delta = {}
    for r in rows:
        if r.entity_key in out:
            violations.append(f"{label}: entity_key={r.entity_key!r} 행이 2개 이상")
        out[r.entity_key] = r.item
    return out


# ── 본체 ───────────────────────────────────────────────────────────
async def _run(seq: Sequence) -> None:
    """시퀀스를 한 세션에서 순서대로 저장(commit)하고 불변식 1~5 를 모두 모아 한 번에 단언한다."""
    try:
        async with SessionLocal() as s:
            pid = await _setup(s)
            cs = CanonService(s)
            counts: list[int] = []
            for chapter_no, delta in seq:
                await cs.merge_delta(pid, {"worldview": _wv_items(delta)}, chapter_no)
                await s.commit()
                counts.append(await _count(s, pid))

            latest = fold_latest(seq)
            max_chapter = max(latest)
            violations: list[str] = []

            # 1. 현재 캐논
            cur_rows = await cs.repo.list_current(CanonWorldview, pid)
            cur = _key_map(cur_rows, violations, "current")
            if cur != reference_at(latest, max_chapter):
                violations.append(
                    f"current {cur} != reference_at({max_chapter}) {reference_at(latest, max_chapter)}"
                )

            # 2. 시점 조회
            for n in range(0, max_chapter + 1):
                at_rows = await cs.repo.list_at_chapter(CanonWorldview, pid, n)
                at = _key_map(at_rows, violations, f"at_chapter({n})")
                if at != reference_at(latest, n):
                    violations.append(f"at_chapter({n}) {at} != reference {reference_at(latest, n)}")

            # 3. superseded_at_chapter IS NULL ⟺ is_current
            rows = await _all_rows(s, pid)
            for r in rows:
                if (r.superseded_at_chapter is None) != bool(r.is_current):
                    violations.append(
                        f"row {r.entity_key} v{r.version}: is_current={r.is_current} "
                        f"superseded_at_chapter={r.superseded_at_chapter}"
                    )

            # 5. append-only (행 수 비감소)
            if any(b < a for a, b in zip(counts, counts[1:])):
                violations.append(f"row count decreased: {counts}")

            # 4. 멱등 — 마지막 저장 반복
            last_no, last_delta = seq[-1]
            before = await _count(s, pid)
            stats = await cs.merge_delta(pid, {"worldview": _wv_items(last_delta)}, last_no)
            await s.commit()
            after = await _count(s, pid)
            if any(stats[k] for k in ("inserted", "versioned", "retired", "restored")):
                violations.append(f"idempotency: re-merge of {seq[-1]} changed something: {stats}")
            if before != after:
                violations.append(f"idempotency: row count {before} -> {after}")

            assert not violations, (
                f"sequence={seq}\nviolations:\n  " + "\n  ".join(violations) + "\nrows:\n" + _dump(rows)
            )
    finally:
        await engine.dispose()


@settings(**PROPERTY_SETTINGS)
@given(seq_in_order)
@example(COUNTEREXAMPLE_IN_ORDER)
@example(COUNTEREXAMPLE_IN_ORDER_A2)
def test_canon_invariants_in_order(seq: Sequence) -> None:
    """chapter_no 비감소(같은 회차 재저장 허용) — 제품의 통상 저장 순서. (A)·(A') 반례는 회귀 방지용 @example."""
    asyncio.run(_run(seq))


@XFAIL_ANY_ORDER
@settings(**PROPERTY_SETTINGS)
@given(seq_any_order)
@example(COUNTEREXAMPLE_ANY_ORDER_MIN)
@example(COUNTEREXAMPLE_LATE_RESAVE)
def test_canon_invariants_any_order(seq: Sequence) -> None:
    """chapter_no 임의 순서 — 앞 회차를 늦게 다시 저장하는 경로 포함. (B) 로 깨진다."""
    asyncio.run(_run(seq))


def test_retire_restores_already_retired_version() -> None:
    """고정 재현 (A, 수정됨): 1화 a=v1 → 1화 {} (철회) → 2화 a=v1 → 2화 {} (철회) ⇒ 철회된 v1 이 복원되면 안 된다."""
    asyncio.run(_run(COUNTEREXAMPLE_IN_ORDER))


@XFAIL_ANY_ORDER
def test_late_resave_of_earlier_chapter() -> None:
    """고정 재현 (B): 1화 a=v1 → 2화 a=v2 → 1화 a=v3 (앞 회차 늦은 재저장)."""
    asyncio.run(_run(COUNTEREXAMPLE_LATE_RESAVE))


@XFAIL_ANY_ORDER
def test_late_resave_drops_unchanged_later_statement() -> None:
    """고정 재현 (B, hypothesis 최소 반례): 1화 a=v1 → 2화 a=v1 (unchanged) → 1화 {} ⇒ 2화의 a 도 사라진다."""
    asyncio.run(_run(COUNTEREXAMPLE_ANY_ORDER_MIN))
