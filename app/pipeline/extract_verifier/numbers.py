"""수치 보존 검사 — number_lost. LLM 0회·결정적. **표시(mark)** 신호, 등급을 내리지 않는다.

정의(2026-11-07 판단 세션 보정 포함):
    항목(state_changes·assumed_states·knowledge_gained·worldview_rules)의 인용(source_text)이 덮는 원문 문장에
    수치가 있는데,
      (1) 그 항목의 구조값(to/from/decays_to · assumes · info · 규칙 content)에 그 수치가 없고
      (2) **회차 전체에서 한 번이라도** 패킷 운반 칸(규칙 content · knowledge info · state_changes to/from)에
          그 수치가 보존돼 있지 않으면
    number_lost. (2)가 보정이다 — Q1 1화 '매달 첫째 월요일과 셋째 월요일이 휴무였다'는 knowledge info 가
    '월 2회 휴무'로 서수를 잃었지만 같은 회차 규칙 content '매달 첫째·셋째 월요일은 휴무다'가 운반하므로 끈다.
    Q3 4화 '배터리 뱅크 하나 교체'는 info '점검/교체' 어디에도 '하나'가 없고 회차 어디에도 없으므로 켠다.
    '같은 수치' 판정은 kor.Mention.same(값·단위·주변 내용어 겹침).
    사건 단위 source_text(문단 통째)는 쓰지 않는다 — 1,443자 문단이 모든 문장을 덮는다(related-work §4-1).

원문이 있으면 인용 조각을 원문에서 찾아 그 조각이 걸친 문장 전체를 본다(probe number_preservation 규약:
공백·따옴표·줄표 제거 후 부분 문자열 검색, 4자 미만 조각 버림). 원문이 없거나 못 찾으면 인용 자체를 본다.

거르는 유형: 요약 손실 — 원문 수치가 구조값으로 옮겨지며 빠짐(개수·서수·시각·금액).
못 거르는 유형: 인용 밖 문장의 수치(항목이 아예 없는 누락), 수치를 바꿔 적은 것(구조값에 다른 수치가 있으면
값 비교를 하지 않는다 — 다른 값은 그대로 '없음'으로 셈), 대사 속 부수 수치와 핵심 수치의 구분(과다 — T3 정밀도).
"""
from __future__ import annotations

import re

from app.pipeline.extract_verifier import items as I
from app.pipeline.extract_verifier.kor import WS, carried, label_text, mentions, norm, quote_pieces, sentences


def carried_own(m, own) -> bool:  # noqa: ANN001
    """항목 자신의 구조값 — 맥락 겹침 없이 값·단위만 본다(같은 항목이므로)."""
    return any(m.value == o.value and (not m.unit or not o.unit or m.unit == o.unit) for o in own)


class TextIndex:
    """원문 정규화 색인 — 정규화 문자열 위치 → 원문 위치."""

    def __init__(self, text: str):
        self.text = text or ""
        self.ntext = norm(self.text)
        self.mp = [i for i, ch in enumerate(self.text) if not WS.fullmatch(ch)]
        self.sents = sentences(self.text)

    def _find(self, piece: str) -> tuple[int, int] | None:
        q = norm(piece)
        i = self.ntext.find(q) if q else -1
        if i < 0:
            return None
        return self.mp[i], self.mp[min(i + len(q), len(self.mp)) - 1] + 1

    def covered(self, quote: str) -> tuple[list[str], list[str]]:
        """(인용이 걸친 원문 문장들, 원문에서 못 찾은 인용 조각들). 조각을 못 찾으면 문장 단위로 다시 쪼개 찾는다(probe 규약)."""
        spans, missing = [], []
        for p in quote_pieces(quote):
            sp = self._find(p) if self.ntext else None
            if sp:
                spans.append(sp)
                continue
            subs = [x for x in re.split(r"(?<=[.!?])\s*", p) if len(norm(x)) >= 6]
            got = [self._find(x) for x in subs] if self.ntext else []
            spans += [g for g in got if g]
            missing += [x for x, g in zip(subs, got) if not g] if got else [p]
        sents = [s for a, b, s in self.sents if any(x < b and a < y for x, y in spans)]
        return sents, missing


def number_marks(doc: dict, text: str | None = None, basis: str = "quote") -> list[dict]:
    """basis="quote"(기본): 항목 인용 안의 수치만 본다. basis="sentence": 인용이 걸친 원문 문장 전체의 수치를 본다
    (지시문 원 정의. 인용이 문장 일부만 덮으면 항목이 주장하지 않은 수치까지 세어 과다 — T3에서 두 기준을 함께 잰다).
    원문에서 못 찾은 인용 조각은 두 기준 모두 조각 글 자체를 본다."""
    pool = I.packet_pool(doc)
    idx = TextIndex(text or "")
    out = []

    def check(base: dict, quote: str, value: str) -> None:
        if not quote:
            return
        if basis == "sentence" and idx.ntext:
            sents, missing = idx.covered(quote)
            src = " ".join(sents + missing)
            used = "sentence"
        else:
            src, used = quote, "quote"
        own = mentions(value) + mentions(label_text(value))     # v4 (f): 자기 구조값의 NEW: 라벨 속 수치도 운반
        lost, seen = [], set()
        for m in mentions(src):
            key = (m.value, m.unit, m.context)
            if key in seen or carried_own(m, own) or carried(m, pool):
                continue
            seen.add(key)
            lost.append(m.surface)
        if lost:
            out.append(base | {"signal": "number_lost", "numbers": lost, "basis": used,
                               "value": value, "source_text": quote})

    for r_i, r in enumerate(I.rules(doc)):
        check({"event_id": None, "collection": "worldview_rules", "index": r_i, "entity": r.get("rule_id"),
               "field": "content"}, r.get("source_text") or "", I.value_text("rule", r))
    for eid, coll, i, it in I.items(doc):
        ent = it.get("entity") or it.get("character_id")
        check({"event_id": eid, "collection": coll, "index": i, "entity": ent, "field": it.get("field")},
              it.get("source_text") or "", I.value_text(coll, it))
    return out
