"""보존 검사 — 패킷에 실리지 않는 칸(assumed_states·엔티티 선언)에만 있는 지속성 사실을 찾는다. LLM 0회·결정적.

신호 세 가지. 셋 다 **표시(mark)** 이고 등급을 내리지 않는다(승격 후보·실험 후보 목록을 만든다).

assumed_only_persistent  (1단계 확정 신호, T3 적중 분모 = T1 판독 Q1 s21·Q3 s28)
    assumed_states 항목의 `assumes` 에 수치·시각·개수가 있는데, 그 수치가 같은 회차의 패킷 운반 칸
    (worldview_rules.content · knowledge_gained.info · state_changes.to/from)에 없다.
    '같은 수치' = 값이 같고, 단위가 둘 다 있으면 같고, 주변 내용어(앞뒤 2토큰)가 하나 이상 겹친다(kor.Mention.same).
    · 거르는 유형: '요금 체계(어른 600원)'처럼 수치 사실이 전제로만 적힌 것. 숫자가 같아도 맥락이 다른 운반
      ('최영감 600원 채무')은 운반으로 치지 않는다.
    · 명시 사물 상태: 사물(OBJ_*)의 location 전제가 같은 회차 state_changes·확정 원장 어디에도 그 값으로 없을 때,
      또는 assumes 에 매체 어휘(MEDIUM)가 있는데 같은 엔티티의 운반 칸에 그 어휘가 없을 때.
    · 못 거르는 유형: 수치·매체 어휘가 없는 사실(습관·배타 — 실험 신호로 일부), 운반 칸에 같은 수치가 맥락 단어까지
      겹쳐 우연히 있는 경우(과소), 장면 한정 일시 수치('잔여 9분')와 지속성 수치의 구분(과다 — T3에서 잰다),
      assumes 글이 수치를 빼먹고 인용에만 있는 경우(그건 number_lost 가 본다).

declaration_only_persistent  (1단계, 2026-11-07 판단 세션 결정 2)
    같은 검사를 엔티티 선언(characters/objects/locations[].description·traits[].trait)에 적용한다.
    선언은 추출기 산출물이지만 원장 재생·패킷 어느 절에도 실리지 않는다. 승격 후보의 자리 표시는
    `declaration: {collection, id, part, index}`.
    · 거르는 유형: 'UPS … 45분 지속'(수치), '카림이 종이 수첩에 기록하는 설비 일지'(매체 어휘).
    · 못 거르는 유형: 매체 어휘 목록 밖의 사물 상태, 운반 칸이 엔티티 이름 대신 대명사·다른 표기로 적은 경우(과다).

assumed_only_exclusive  (실험 신호 — 등급·적중 지표에 넣지 않는다. 판단 세션 결정 1)
    `assumes` 또는 그 `source_text` 에 배타 어휘(뿐·유일·단독·밖에 없·명사+만. '-지만·-기만·-라고만·-다만'의 '만'은 제외 — v3 (c))가 있고, 같은 회차 운반 칸에
    배타 어휘 + 내용어 하나 이상이 겹치는 값이 없다.
    · 거르는 유형: '콘솔을 다룰 수 있는 사람은 도현뿐'. · 못 거르는 유형: 어휘 단서 없는 배타, 대사 속 강조('나만 믿어')(과다).
"""
from __future__ import annotations

import re

from app.pipeline.extract_verifier import items as I
from app.pipeline.extract_verifier.kor import carried, content_tokens, mentions

MEDIUM = ("종이", "수첩", "태블릿", "노트북", "단말", "컴퓨터", "손글씨", "손으로", "전자", "디지털", "아날로그",
          "사진", "테이프", "녹음", "편지", "메모")
EXCLUSIVE = re.compile(r"뿐|유일|단독|밖에\s*없|(?<![0-9일이삼사오육칠팔구십백천지기고다])(?<=[가-힣]{2})만(?=\s|이|을|를|으로|이다|$)")


def _medium(s: str) -> list[str]:
    return [w for w in MEDIUM if w in (s or "")]


def _entity_carries(values, ent: str, names: list[str], word: str) -> bool:
    for _kind, ents, val in values:
        if word in val and (ent and ent in ents or any(n in val for n in names)):
            return True
    return False


def assumed_marks(doc: dict, ledger=None, timeline: str = "main") -> list[dict]:  # noqa: ANN001
    """assumed_only_persistent · assumed_only_exclusive 표시 목록(문서 순서)."""
    pool = I.packet_pool(doc)
    values = I.packet_values(doc)
    sc_keys = {(it.get("entity"), it.get("field"), str(it.get("to")))
               for _e, c, _i, it in I.items(doc) if c == "state_changes"}
    out = []
    for eid, coll, i, it in I.items(doc):
        if coll != "assumed_states":
            continue
        assumes = str(it.get("assumes") or "")
        ent, fld = it.get("entity"), it.get("field")
        base = {"event_id": eid, "collection": coll, "index": i, "entity": ent, "field": fld,
                "value": assumes, "source_text": it.get("source_text")}
        lost = [m for m in mentions(assumes) if not carried(m, pool)]
        why = []
        if lost:
            why.append("number")
        med = [w for w in _medium(assumes) if not _entity_carries(values, ent or "", [], w)]
        if med:
            why.append("medium")
        if (isinstance(ent, str) and ent.startswith("OBJ_") and fld == "location"):
            to = assumes.split()[0] if assumes.split() else ""
            prior = ledger.get(timeline, ent, "location") if ledger is not None else None
            if not any(k[0] == ent and k[1] == "location" for k in sc_keys) and (prior is None or str(prior) != to):
                why.append("object_location")
        if why:
            out.append(base | {"signal": "assumed_only_persistent", "why": why,
                               "numbers": [m.surface for m in lost], "words": med})
        cue_src = assumes + " " + str(it.get("source_text") or "")
        if EXCLUSIVE.search(cue_src):
            toks = set(content_tokens(assumes))
            hit = any(EXCLUSIVE.search(v) and toks & set(content_tokens(v)) for _k, _e, v in values)
            if not hit:
                out.append(base | {"signal": "assumed_only_exclusive", "experimental": True,
                                   "cue": EXCLUSIVE.search(cue_src).group()})
    return out


def declaration_marks(doc: dict) -> list[dict]:
    """declaration_only_persistent 표시 목록(문서 순서)."""
    pool = I.packet_pool(doc)
    values = I.packet_values(doc)
    out = []
    for coll, did, names, part, j, text, src in I.declarations(doc):
        lost = [m for m in mentions(text) if not carried(m, pool)]
        med = [w for w in _medium(text) if not _entity_carries(values, did or "", names, w)]
        if lost or med:
            out.append({"signal": "declaration_only_persistent",
                        "declaration": {"collection": coll, "id": did, "part": part, "index": j},
                        "entity": did, "field": part, "value": text, "source_text": src,
                        "why": (["number"] if lost else []) + (["medium"] if med else []),
                        "numbers": [m.surface for m in lost], "words": med})
    return out
