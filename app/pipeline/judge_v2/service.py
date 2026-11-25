"""학습 judge v2 서비스 연결 뼈대(설계 문서 §10 "기다리는 동안의 작업", 기본 꺼짐 JUDGE_V2_ENABLED=false).

`AnalysisPipeline` 인터페이스는 그대로 두고, 검수 단계에서 켜져 있을 때만 이 판정기가 회차 원문 문장마다
앞 회차 원문(저장된 회차)에서 검색한 구간과 짝지어 p(모순)을 내고, 기준값 이상을 `ContradictionOut` 으로 바꾼다.
모델 호출은 주입된 `SentenceScorer` 뒤에 있다(실제는 hf.HFScorer, 테스트는 가짜). 서비스 코드는 SDK 를 모른다.

출력 규칙(고정):
- span = 회차 원문에서 그 문장의 위치(못 찾으면 0,0 + 설명에 표시).
- conflict_ref = 그 문장의 검색 1위 구간이 나온 회차·구간 앞부분.
- type = "event"(v2 는 유형을 가르지 않는다), confidence = p(모순)(온도 적용).
- explanation 은 `[judge_v2]` 로 시작한다(ContradictionOut 에 source 칸이 없어 출처를 글머리로 남긴다).
- 기준값 이상 문장을 p 내림차순으로 최대 max_items 개.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, Sequence

from app.pipeline.interface import ConflictRef, ContradictionOut, Span
from app.pipeline.judge_v2.retrieve import K, Retriever, guess_names
from app.pipeline.judge_v2.text import chunk_prefix, split_sentences

SOURCE_TAG = "[judge_v2]"
QUOTE_CHARS = 160
NOT_FOUND = " (원문에서 문장 위치를 찾지 못함)"


class SentenceScorer(Protocol):
    def score(self, items: Sequence[tuple[Sequence[str], str]]) -> list[float]:
        """(맥락 조각 목록, 주장 문장) 마다 p(모순)."""
        ...


@dataclass(frozen=True)
class PriorChapter:
    chapter_no: int
    text: str


def locate(text: str, quote: str) -> tuple[Span, bool]:
    q = quote.strip()
    if not text or not q:
        return Span(start=0, end=0, quote=q), False
    i = text.find(q)
    if i >= 0:
        return Span(start=i, end=i + len(q), quote=q), True
    m = re.search(r"\s+".join(re.escape(t) for t in q.split()), text)
    if m:
        return Span(start=m.start(), end=m.end(), quote=text[m.start():m.end()]), True
    return Span(start=0, end=0, quote=q), False


class JudgeV2:
    """회차 단위 v2 판정. 같은 입력 → 같은 출력(점수기가 결정적이면)."""

    def __init__(self, scorer: SentenceScorer, threshold: float, *, max_items: int = 5, k: int = K) -> None:
        self.scorer, self.threshold, self.max_items, self.k = scorer, float(threshold), int(max_items), k

    def review(self, chapter_text: str, prior: Sequence[PriorChapter]) -> list[ContradictionOut]:
        prior = sorted((p for p in prior if p.text), key=lambda p: p.chapter_no)
        claims = split_sentences(chapter_text)
        if not prior or not claims:
            return []
        chunks, owner = [], []
        for p in prior:
            for c in chunk_prefix(p.text):
                chunks.append(c)
                owner.append(p.chapter_no)
        ret = Retriever(chunks, guess_names("\n".join(p.text for p in prior)))
        hits = [ret.search(c, self.k) for c in claims]
        items = [([ret.chunks[h.index] for h in sorted(hs, key=lambda h: h.index)], c) for c, hs in zip(claims, hits)]
        probs = self.scorer.score(items)
        picked = sorted((i for i, p in enumerate(probs) if p >= self.threshold), key=lambda i: (-probs[i], i))
        out = []
        for i in picked[:self.max_items]:
            claim, p = claims[i], float(probs[i])
            top = hits[i][0] if hits[i] else None
            ref_no = owner[top.index] if top else None
            ref_q = ret.chunks[top.index][:QUOTE_CHARS] if top else None
            span, found = locate(chapter_text, claim)
            expl = (f"{SOURCE_TAG} 학습 판정 p(모순) {p:.3f} — 문장: {claim}"
                    + (f" / 근거 구간({ref_no}화): {ref_q}" if top else " / 근거 구간 없음")
                    + ("" if found else NOT_FOUND))
            out.append(ContradictionOut(span=span, conflict_ref=ConflictRef(chapter_no=ref_no, quote=ref_q),
                                        type="event", explanation=expl, confidence=round(p, 4)))
        return out
