"""캐논 속성 누적 병합(V3) — 인물·공간 attrs 를 통째로 교체하지 않고 쌓는다.

- 별칭·특성은 합집합, 설명은 새 값, 처음 등장은 기존 값 유지
- 새 정보가 없으면 새 버전을 만들지 않음(멱등), is_current 는 항상 1개
- 같은 회차 재저장은 그 전 버전 위에 다시 얹음(재분석에서 빠진 별칭이 남지 않게)
- 앞 회차를 늦게 저장하면 뒤 회차의 설명을 되돌리지 않음(D11)
- 관계는 누적하지 않고 교체
"""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.security import hash_password
from app.models import CanonCharacter, CanonRelation, Project
from app.repository.user_repo import UserRepository
from app.service.canon_service import CanonService, accumulate_attrs


async def _setup(s):
    user = await UserRepository(s).create(
        f"ca_{uuid.uuid4().hex[:8]}@x.com", hash_password("x"), "t"
    )
    project = Project(owner_id=user.id, name="canon-accumulate-test")
    s.add(project)
    await s.flush()
    return project.id


def _char(attrs: dict, key: str = "CHAR_001", name: str = "민수") -> dict:
    return {"entity_key": key, "name": name, "attrs": attrs}


async def _rows(s, model, pid, key="CHAR_001"):
    return (
        await s.execute(
            select(model)
            .where(model.project_id == pid, model.entity_key == key)
            .order_by(model.version)
        )
    ).scalars().all()


def _current(rows):
    cur = [r for r in rows if r.is_current]
    assert len(cur) == 1
    return cur[0]


# ── 순수 함수 ────────────────────────────────────────────────
def test_accumulate_rules():
    base = {
        "aliases": ["민"],
        "traits": [{"trait": "겁이 많다", "trait_type": "성격", "certainty": "low"}],
        "description": "옛 설명",
        "first_appearance_event_id": "EVENT_001",
    }
    new = {
        "aliases": ["민", "수수"],
        "traits": [
            {"trait": "겁이 많다", "trait_type": "성격", "certainty": "high"},
            {"trait": "검을 쓴다", "trait_type": "능력", "certainty": "high"},
        ],
        "description": "새 설명",
        "first_appearance_event_id": "EVENT_009",
    }
    out = accumulate_attrs(base, new, keep_base_text=False)
    assert out["aliases"] == ["민", "수수"]
    assert [t["trait"] for t in out["traits"]] == ["겁이 많다", "검을 쓴다"]
    assert out["traits"][0]["certainty"] == "high"  # 같은 특성은 최신 값으로
    assert out["description"] == "새 설명"
    assert out["first_appearance_event_id"] == "EVENT_001"


def test_accumulate_keeps_missing_keys_and_legacy_traits():
    out = accumulate_attrs(
        {"description": "설명", "traits": ["옛 문자열 특성"]},
        {"traits": [{"trait": "새 특성"}]},
        keep_base_text=False,
    )
    assert out["description"] == "설명"
    assert out["traits"] == ["옛 문자열 특성", {"trait": "새 특성"}]


def test_accumulate_keep_base_text():
    out = accumulate_attrs(
        {"description": "3화 설명", "aliases": ["A"]},
        {"description": "2화 설명", "aliases": ["B"], "source_text": "2화 인용"},
        keep_base_text=True,
    )
    assert out["description"] == "3화 설명"
    assert out["aliases"] == ["A", "B"]
    assert out["source_text"] == "2화 인용"  # 기존에 없던 키는 채운다


def test_accumulate_empty_sides():
    assert accumulate_attrs(None, {"a": 1}, keep_base_text=False) == {"a": 1}
    assert accumulate_attrs({"a": 1}, None, keep_base_text=False) == {"a": 1}


# ── 병합(DB) ─────────────────────────────────────────────────
async def test_character_attrs_accumulate_across_chapters():
    async with SessionLocal() as s:
        pid = await _setup(s)
        cs = CanonService(s)
        await cs.merge_delta(pid, {"characters": [_char(
            {"aliases": ["민"], "traits": [{"trait": "겁이 많다"}], "description": "1화",
             "first_appearance_event_id": "EVENT_001"}
        )]}, 1)
        stats = await cs.merge_delta(pid, {"characters": [_char(
            {"aliases": ["수수"], "traits": [{"trait": "검을 쓴다"}], "description": "2화",
             "first_appearance_event_id": "EVENT_020"}
        )]}, 2)
        await s.commit()

        assert stats["versioned"] == 1
        rows = await _rows(s, CanonCharacter, pid)
        assert [r.version for r in rows] == [1, 2]
        cur = _current(rows)
        assert cur.source_chapter == 2
        assert cur.attrs["aliases"] == ["민", "수수"]
        assert [t["trait"] for t in cur.attrs["traits"]] == ["겁이 많다", "검을 쓴다"]
        assert cur.attrs["description"] == "2화"
        assert cur.attrs["first_appearance_event_id"] == "EVENT_001"
        # 옛 버전은 그대로 보존(append-only)
        assert rows[0].attrs["aliases"] == ["민"]


async def test_no_new_info_makes_no_new_version():
    async with SessionLocal() as s:
        pid = await _setup(s)
        cs = CanonService(s)
        await cs.merge_delta(pid, {"characters": [_char(
            {"aliases": ["민", "수수"], "description": "설명"}
        )]}, 1)
        # 2화가 별칭 일부만 다시 언급 — 누적 결과가 현재와 같으므로 버전업 없음
        stats = await cs.merge_delta(pid, {"characters": [_char({"aliases": ["민"]})]}, 2)
        await s.commit()

        assert stats["unchanged"] == 1
        assert len(await _rows(s, CanonCharacter, pid)) == 1


async def test_resave_same_chapter_rebuilds_on_previous_version():
    async with SessionLocal() as s:
        pid = await _setup(s)
        cs = CanonService(s)
        await cs.merge_delta(pid, {"characters": [_char({"aliases": ["민"]})]}, 1)
        await cs.merge_delta(pid, {"characters": [_char({"aliases": ["잘못뽑힌별칭"]})]}, 2)
        # 2화 재분석에서 그 별칭이 빠짐 → 1화 버전 위에 다시 얹으므로 남지 않는다
        await cs.merge_delta(pid, {"characters": [_char({"aliases": ["수수"]})]}, 2)
        await s.commit()

        cur = _current(await _rows(s, CanonCharacter, pid))
        assert cur.attrs["aliases"] == ["민", "수수"]


async def test_resave_unchanged_is_idempotent():
    async with SessionLocal() as s:
        pid = await _setup(s)
        cs = CanonService(s)
        await cs.merge_delta(pid, {"characters": [_char({"aliases": ["민"]})]}, 1)
        delta2 = {"characters": [_char({"aliases": ["수수"], "description": "2화"})]}
        await cs.merge_delta(pid, delta2, 2)
        stats = await cs.merge_delta(pid, delta2, 2)
        await s.commit()

        assert stats["unchanged"] == 1
        assert len(await _rows(s, CanonCharacter, pid)) == 2


async def test_late_earlier_chapter_does_not_revert_description():
    async with SessionLocal() as s:
        pid = await _setup(s)
        cs = CanonService(s)
        await cs.merge_delta(pid, {"characters": [_char({"description": "3화 설명", "aliases": ["A"]})]}, 3)
        await cs.merge_delta(pid, {"characters": [_char({"description": "2화 설명", "aliases": ["B"]})]}, 2)
        await s.commit()

        cur = _current(await _rows(s, CanonCharacter, pid))
        assert cur.attrs["description"] == "3화 설명"
        assert cur.attrs["aliases"] == ["A", "B"]


async def test_retire_restores_accumulated_previous_version():
    async with SessionLocal() as s:
        pid = await _setup(s)
        cs = CanonService(s)
        await cs.merge_delta(pid, {"characters": [_char({"aliases": ["민"]})]}, 1)
        await cs.merge_delta(pid, {"characters": [_char({"aliases": ["수수"]})]}, 2)
        stats = await cs.merge_delta(pid, {"characters": []}, 2)  # 2화에서 인물이 빠짐
        await s.commit()

        assert stats["retired"] == 1 and stats["restored"] == 1
        cur = _current(await _rows(s, CanonCharacter, pid))
        assert cur.source_chapter == 1
        assert cur.attrs["aliases"] == ["민"]


async def test_relation_attrs_are_replaced_not_accumulated():
    async with SessionLocal() as s:
        pid = await _setup(s)
        cs = CanonService(s)

        def rel(attrs):
            return {"relations": [{"entity_key": "R1", "from_char": "민수", "to_char": "지은",
                                   "relation": "동료", "attrs": attrs}]}

        await cs.merge_delta(pid, rel({"sentiment": "positive", "description": "친함"}), 1)
        await cs.merge_delta(pid, rel({"sentiment": "negative"}), 2)
        await s.commit()

        cur = _current(await _rows(s, CanonRelation, pid, key="R1"))
        assert cur.attrs == {"sentiment": "negative"}
