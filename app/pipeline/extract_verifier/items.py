"""회차 문서에서 항목·구조값을 꺼내는 공통 함수. 입력을 바꾸지 않는다."""
from __future__ import annotations

from app.pipeline.extract_verifier.kor import Mention, mentions

COLLS = ("state_changes", "assumed_states", "knowledge_gained")
DECL_COLLS = ("characters", "objects", "locations")
DECL_ID = {"characters": "character_id", "objects": "object_id", "locations": "location_id"}


def events(doc: dict) -> list[dict]:
    return [e for e in doc.get("events") or [] if isinstance(e, dict)]


def rules(doc: dict) -> list[dict]:
    return [r for r in doc.get("worldview_rules") or [] if isinstance(r, dict)]


def value_text(coll: str, it: dict) -> str:
    """항목의 구조값(패킷에 실리는 쪽). state_changes: to·from·decays_to, assumed: assumes, knowledge: info, 규칙: content."""
    if coll == "state_changes":
        keys = ("to", "from", "decays_to")
    elif coll == "assumed_states":
        keys = ("assumes",)
    elif coll == "knowledge_gained":
        keys = ("info",)
    else:
        keys = ("content",)
    return " ".join(str(it.get(k)) for k in keys if isinstance(it.get(k), (str, int, float)))


def items(doc: dict):
    """(event_id, coll, index, item) — 사건 항목 전부, 문서 순서."""
    for ev in events(doc):
        for coll in COLLS:
            for i, it in enumerate(ev.get(coll) or []):
                if isinstance(it, dict):
                    yield ev.get("event_id"), coll, i, it


def packet_pool(doc: dict) -> list[Mention]:
    """패킷에 실리는 구조값(규칙 content·knowledge info·state_changes to/from)의 수치 언급 전부.

    assumed_states 는 넣지 않는다 — 원장 재생에서 상태가 되지 않아 패킷 (c)(d)에 안 실린다(T1 판독).
    """
    out: list[Mention] = []
    for r in rules(doc):
        out += mentions(value_text("rule", r))
    for _eid, coll, _i, it in items(doc):
        if coll != "assumed_states":
            out += mentions(value_text(coll, it))
    return out


def packet_values(doc: dict) -> list[tuple[str, str, str]]:
    """(종류, 관련 엔티티 ID 묶음 문자열, 구조값) — 어휘 단서 대조용. assumed_states 제외(위와 같은 이유)."""
    out = []
    for r in rules(doc):
        out.append(("rule", "", value_text("rule", r)))
    for _eid, coll, _i, it in items(doc):
        if coll == "assumed_states":
            continue
        ent = it.get("entity") or it.get("character_id") or ""
        out.append((coll, f"{ent} {it.get('to') or ''}", value_text(coll, it)))
    return out


def declarations(doc: dict):
    """(coll, id, name 목록, part, index, text) — 선언의 description 과 traits[].trait."""
    for coll in DECL_COLLS:
        for d in doc.get(coll) or []:
            if not isinstance(d, dict):
                continue
            did = d.get(DECL_ID[coll]) or d.get("id")
            names = [n for n in [d.get("name")] + list(d.get("aliases") or []) if isinstance(n, str) and len(n) >= 2]
            if isinstance(d.get("description"), str):
                yield coll, did, names, "description", None, d["description"], d.get("source_text")
            for j, t in enumerate(d.get("traits") or []):
                if isinstance(t, dict) and isinstance(t.get("trait"), str):
                    yield coll, did, names, "traits", j, t["trait"], t.get("source_text")
