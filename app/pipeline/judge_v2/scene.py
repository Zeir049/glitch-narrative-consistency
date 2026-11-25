"""장면(대상 구간) 항목 구성과 장면 점수(학습 judge v2).

장면 = 앞선 원문(prefix) + 대상 구간(target). 주장 = 대상 구간의 문장 전부(따옴표 대사 포함).
맥락 = prefix 조각 중 주장과 BM25+인명 상위 k(표시 순서는 prefix 안 위치).
장면 점수 = 문장별 p(모순)의 max.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from app.pipeline.judge_v2.retrieve import K, Retriever, guess_names
from app.pipeline.judge_v2.text import chunk_prefix, split_sentences


@dataclass
class SceneItem:
    claim: str
    contexts: list[str]                 # 표시 순서(앞선 원문 안 위치 순)
    priority: list[int]                 # 검색 점수 순위(contexts 위치, 점수 내림차순) — 기록용, 렌더러는 쓰지 않는다
    chunk_ids: list[int] = field(default_factory=list)
    scores: list[float] = field(default_factory=list)


def build_scene(prefix: str, target: str, *, names: list[str] | None = None, k: int = K,
                retriever: Retriever | None = None) -> list[SceneItem]:
    """대상 구간 문장마다 검색 맥락을 붙인다. names 를 주지 않으면 prefix 에서 근사 추출(guess_names)."""
    if retriever is None:
        retriever = Retriever(chunk_prefix(prefix), guess_names(prefix) if names is None else names)
    out = []
    for c in split_sentences(target):
        hits = retriever.search(c, k)
        by_pos = sorted(hits, key=lambda h: h.index)
        pos = {h.index: n for n, h in enumerate(by_pos)}
        out.append(SceneItem(claim=c, contexts=[retriever.chunks[h.index] for h in by_pos],
                             priority=[pos[h.index] for h in hits], chunk_ids=[h.index for h in by_pos],
                             scores=[round(h.score, 4) for h in by_pos]))
    return out


def scene_score(probs: Iterable[float]) -> float:
    """장면 점수 = 문장 p(모순) max (문장이 없으면 0)."""
    return max(probs, default=0.0)
