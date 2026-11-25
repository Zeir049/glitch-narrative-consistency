"""앞선 원문 조각 검색 — 문자 2-gram BM25 + 인명 겹침 가산(학습 judge v2). 상수는 고정이다(바꾸면 RETRIEVE_VERSION 을 올린다)."""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

RETRIEVE_VERSION = "1"
K = 4                    # 맥락 조각 수 상한
BM25_K1 = 1.2
BM25_B = 0.75
NAME_BONUS = 1.5         # 주장과 조각이 함께 가진 인명 하나당 가산
NAME_BONUS_MAX = 2       # 가산하는 인명 수 상한
NAME_MIN_COUNT = 3       # 앞선 원문에서 이 횟수 이상 나온 후보만 인명으로 본다
_NORM = re.compile(r"[^가-힣A-Za-z0-9]")
_NAME = re.compile(r"([가-힣]{2,4})(?:이가|이는|이를|이의|이와|에게|한테|더러|씨|이|가|은|는|을|를|의|와|과)"
                   r"(?=[\s,.!?…\"”’」』)]|$)")
# 사람 이름처럼 조사가 붙지만 인명이 아닌 흔한 말(대명사·지시어·일반 명사). 역할어(아내·어머니 등)는 인물을 가리키므로 남긴다.
NAME_STOP = frozenset("""
그것 이것 저것 무엇 누구 자기 자신 우리 너희 저희 그들 이들 그녀 그대 당신 여기 거기 저기 이제 그때 지금 오늘 어제 내일
사람 마음 생각 소리 얼굴 눈물 이야기 일이 것이 때문 하나 모두 서로 무슨 어디 아무 그날 그런 이런 저런 한번 처음 다시
""".split())


def normalize(text: str) -> str:
    return _NORM.sub("", text or "")


def bigrams(text: str) -> list[str]:
    t = normalize(text)
    return [t[i:i + 2] for i in range(len(t) - 1)]


def guess_names(text: str, min_count: int = NAME_MIN_COUNT) -> list[str]:
    """앞선 원문에서 '2–4글자 + 조사' 꼴로 min_count 번 이상 나온 말(불용어 제외)을 인명 후보로 본다(근사, 고정 규칙)."""
    c = Counter(m.group(1) for m in _NAME.finditer(text or ""))
    return sorted(n for n, k in c.items() if k >= min_count and n not in NAME_STOP)


@dataclass(frozen=True)
class Hit:
    index: int           # 조각 순서(앞선 원문 안 위치)
    score: float
    bm25: float
    names: tuple[str, ...]


class Retriever:
    """조각 목록에 대한 BM25 색인. 같은 입력 → 같은 결과(동점은 앞 조각 우선)."""

    def __init__(self, chunks: list[str], names: list[str] | None = None) -> None:
        self.chunks = list(chunks)
        self.names = sorted(set(names or []), key=lambda s: (-len(s), s))
        self.tf = [Counter(bigrams(c)) for c in self.chunks]
        self.len = [sum(t.values()) for t in self.tf]
        self.avgdl = (sum(self.len) / len(self.len)) if self.len else 0.0
        df: Counter = Counter()
        for t in self.tf:
            df.update(t.keys())
        n = len(self.chunks)
        self.idf = {g: math.log(1 + (n - d + 0.5) / (d + 0.5)) for g, d in df.items()}
        self.chunk_names = [frozenset(x for x in self.names if x in c) for c in self.chunks]

    def bm25(self, query: str, i: int) -> float:
        tf, dl = self.tf[i], self.len[i]
        s = 0.0
        for g, q in Counter(bigrams(query)).items():
            f = tf.get(g)
            if not f:
                continue
            s += self.idf[g] * f * (BM25_K1 + 1) / (f + BM25_K1 * (1 - BM25_B + BM25_B * dl / (self.avgdl or 1)))
        return s

    def search(self, claim: str, k: int = K) -> list[Hit]:
        """점수 상위 k개(점수 > 0). 반환 순서 = 점수 내림차순(동점은 앞 조각)."""
        cn = frozenset(x for x in self.names if x in claim)
        hits = []
        for i in range(len(self.chunks)):
            b = self.bm25(claim, i)
            shared = tuple(sorted(cn & self.chunk_names[i]))
            s = b + NAME_BONUS * min(NAME_BONUS_MAX, len(shared))
            if s > 0:
                hits.append(Hit(i, s, b, shared))
        hits.sort(key=lambda h: (-h.score, h.index))
        return hits[:k]
