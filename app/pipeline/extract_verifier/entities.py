"""엔티티 커버리지 — entity_uncovered (보조). LLM 0회·결정적. **표시(mark)** 신호.

레지스트리 엔티티(characters·locations·objects)의 이름·별칭(2자 이상)이 회차 원문에 나오는데, 그 회차 어느 사건도
그 엔티티를 참조하지 않는다(참여·목격·주체·장소·시간·objects·state_changes/assumed_states 의 entity·to,
knowledge_gained 의 character_id). probe entity_coverage.py 규칙과 같다.

거르는 유형: 등장했는데 사건에 안 묶인 인물·장소·사물(재추출 요청의 보조 근거).
못 거르는 유형: 레지스트리에 없는 사물(Q1 '사진' — 원리상 불가), 별칭이 다른 뜻으로 쓰인 부분 문자열 일치(과다 —
'정문'이 '정문식'에 걸리는 류), 참조는 됐으나 잘못된 엔티티에 붙은 오매핑. 장소가 미참조의 대부분이다(related-work §4-1, 29~31%).
"""
from __future__ import annotations

from app.pipeline.extract_verifier import items as I


def referenced(doc: dict) -> set[str]:
    out: set[str] = set()
    for ev in I.events(doc):
        for k in ("participant_character_ids", "witness_character_ids", "objects"):
            out.update(x for x in ev.get(k) or [] if isinstance(x, str))
        for k in ("main_actor_character_id", "location_id", "time_id"):
            if isinstance(ev.get(k), str):
                out.add(ev[k])
        for coll in ("state_changes", "assumed_states"):
            for it in ev.get(coll) or []:
                if isinstance(it, dict):
                    for k in ("entity", "to"):
                        if isinstance(it.get(k), str):
                            out.add(it[k])
        for it in ev.get("knowledge_gained") or []:
            if isinstance(it, dict) and isinstance(it.get("character_id"), str):
                out.add(it["character_id"])
    return out


def registry_entities(reg: dict) -> list[tuple[str, str, list[str]]]:
    ents = []
    for coll in ("characters", "locations", "objects"):
        for e in reg.get(coll) or []:
            if not isinstance(e, dict):
                continue
            eid = e.get("id") or e.get(I.DECL_ID[coll])
            names = sorted({n for n in [e.get("name")] + list(e.get("aliases") or [])
                            if isinstance(n, str) and len(n) >= 2})
            if eid and names:
                ents.append((coll, eid, names))
    return sorted(ents, key=lambda x: (x[0], x[1]))


def entity_marks(doc: dict, text: str | None, reg: dict) -> list[dict]:
    if not text:
        return []
    refs = referenced(doc)
    out = []
    for coll, eid, names in registry_entities(reg):
        hit = [n for n in names if n in text]
        if hit and eid not in refs:
            out.append({"signal": "entity_uncovered", "collection": coll, "entity": eid, "names": hit})
    return out
