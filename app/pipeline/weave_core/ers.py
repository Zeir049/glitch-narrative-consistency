# -*- coding: utf-8 -*-
"""
ers.py — ERS reranker (보조 검색)

신규 사건 N과 과거 사건 P들의 연관 점수를 계산해, LLM 판정(judge.py)에
넘길 관련 사건 top-k(기본 5)를 고른다.

ERS(N,P) = 0.40 × SemanticSim   (summary TF-IDF 코사인 — ★임베딩 교체 지점)
         + 0.35 × EntityOverlap (참여자+객체 집합의 자카드 유사도)
         + 0.25 × StateDep      (N의 assumed_states (엔티티,필드)가 P의 state_changes와 겹치는 정도)
         + 0.00 × CausalLink    (구현 보존, 가중치 0 — 개정 3차 정정 참조)
         + 0.00 × LocationRel   (제거 — 같은 장소 사건이 상위를 독식해 결정적
                                 근거를 밀어내는 역작동 실측)
         + 0.00 × TimeRel       (제거 — 저장 순서 거리는 잡음이었다. null 대조군
                                 실측: time 제거만으로 엄격 합 30→39)
(ThreadLink는 미구현 상수 0이라 공식에서 제외 — 점수 상한을 1.0으로 유지)

2026-08-17 개정 3차 + 정정 (팀원 제안 CausalLink의 채택과 철회):
  S_causal(P→N) = tfidf_cos(P.result, N.cause). 최초 ablation에서
  "time→causal 대체"가 엄격 평균 +2.0으로 채택됐으나, 독립 리뷰가
  **null 대조군(time을 그냥 제거)** 누락을 지적. 재측정 결과 null이
  현행과 동급 이상 (엄격 합: causal 38 vs time→semantic 39, 최악 벌 r3
  +1 우위) — **이득의 출처는 causal 추가가 아니라 time 제거였다.**
  결정 규칙(느슨 무손해 → 엄격 최악값 → 단순성)에 따라 causal 가중치 0,
  time의 0.10은 semantic으로. causal_link 구현은 보존 — 임베딩 도입 시
  null 대조군과 함께 재시도할 가치 있음 (cause/result가 30~50자 단문이라
  TF-IDF 프록시에 특히 불리했을 가능성).
  근거: handoff/causal_ab_results.json + ers_ab_report.md §6.
  교훈은 B-015로 등록: 교체 실험(A→B)은 (기준)/(A 제거만)/(A→B)
  세 조건을 반드시 함께 잰다.

2026-08-15 개정 2차 (v1 개정의 독립 재현 리뷰 반영):
  · semantic: 토큰 자카드 → TF-IDF 코사인 (자카드는 문서 길이로 나누며
    희귀 결정 토큰을 묻는다). 단, 실효 기여도가 낮아(표준편차 0.021)
    가중치는 늘리지 않는다.
  · location(0.10)·thread(0.10)에서 풀린 0.20은 **entity(+0.10)와
    statedep(+0.10)으로 이관** — 추출 벌 4종(원/r1/r2/r3) A/B에서 유일하게
    전 벌 엄격 Δ ≥ 0 (+1/+1/+2/0, 느슨 무손해)인 배분.
    v1 개정(semantic 0.40 이관)은 벌에 따라 부호가 갈려(+2/−1/+2/−1) 철회.
  근거: measure_ers_ab.py → handoff/ers_ab_results_v2.json,
        handoff/ers_ab_report.md §4.
  단일 작품 34사건 코퍼스 기준 — 코퍼스 증설 후 재검증할 것.

REAS(저장 순서 거리 감쇠 exp(-거리/50))는 점수에 곱하지 않는다.
결과 표시에 "오래된 사건 — 독자가 잊었을 가능성" 라벨로만 사용한다.
(독자 기억 모델은 '관련성'과 별개 축이므로 순위를 왜곡하면 안 됨)

후보 생성: 전수 계산이 아니라
  (엔티티 역인덱스: N의 참여자·객체가 등장한 과거 사건) ∪ (같은 장소 사건)
을 후보로 모은 뒤 ERS로 순위화한다.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

from ledger import BASE, load_episodes, load_registry, normalize

WEIGHTS = {
    "semantic": 0.40,   # 0.30 + time 이관분 (개정 3차 정정 — docstring 참조)
    "entity": 0.35,     # +0.10 (location·thread 이관분 — 개정 2차)
    "statedep": 0.25,   # +0.10 (동일)
    "causal": 0.0,      # 순기여 미검출 (null 대조군) — 구현 보존, 임베딩 시 재시도
    "location": 0.0,    # 역작동 실측으로 제거
    "time": 0.0,        # null 대조군 실측 — 제거 자체가 이득의 출처
    # thread: 미구현 상수 0 — 공식에서 제외 (합계 1.00 유지)
}

_TOKEN = re.compile(r"[가-힣A-Za-z0-9]{2,}")


# ---------------------------------------------------------------- 코퍼스

def load_corpus(base: Path = BASE) -> list[dict]:
    """전체 화의 사건을 서술 순서대로 펼친 코퍼스.
    각 사건에 episode 번호와 global_idx(저장 순서 인덱스)를 부여한다."""
    corpus = []
    for ep in load_episodes(base):
        for ev in sorted(ep["events"], key=lambda e: e.get("order", 0)):
            item = dict(ev)
            item["episode"] = ep["episode"]
            item["global_idx"] = len(corpus)
            corpus.append(item)
    return corpus


def entity_set(ev: dict) -> set:
    """참여자 + 객체 집합 (unknown 제외)."""
    s = set(ev.get("participant_character_ids", [])) | set(ev.get("objects", []))
    s.discard("unknown")
    return s


def _tokens(text: str) -> set:
    return set(_TOKEN.findall(str(text)))


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# ---------------------------------------------------------------- 점수 성분

_TFIDF_CACHE: dict = {}


def _idf_of(corpus: list[dict]) -> dict:
    """코퍼스 summary 토큰의 IDF. 코퍼스 지문(길이+양끝 ID)으로 캐시한다."""
    key = (len(corpus),
           corpus[0].get("event_id") if corpus else "",
           corpus[-1].get("event_id") if corpus else "")
    if key not in _TFIDF_CACHE:
        from collections import Counter
        df = Counter()
        for ev in corpus:
            df.update(_tokens(ev.get("summary", "")))
        n = len(corpus)
        _TFIDF_CACHE[key] = {
            "idf": {t: math.log((1 + n) / (1 + c)) + 1.0 for t, c in df.items()},
            "max_idf": math.log(1 + n) + 1.0,
        }
    return _TFIDF_CACHE[key]


def _tfidf_vec(text: str, idf_state: dict) -> dict:
    from collections import Counter
    idf, mx = idf_state["idf"], idf_state["max_idf"]
    return {t: c * idf.get(t, mx)
            for t, c in Counter(_tokens(text)).items()}


def semantic_sim(n: dict, p: dict, corpus: list[dict] | None = None) -> float:
    """★ 임베딩 교체 지점 ★
    summary의 TF-IDF 코사인 (2026-08-15, 자카드에서 교체 — docstring 참조).
    자카드는 문서 길이로 나누느라 결정적 희귀어("충전 플랫폼")가 묻혔다.
    corpus가 없으면(IDF를 만들 수 없으면) 자카드로 폴백한다."""
    a, b = n.get("summary", ""), p.get("summary", "")
    if not corpus:
        return _jaccard(_tokens(a), _tokens(b))
    st = _idf_of(corpus)
    va, vb = _tfidf_vec(a, st), _tfidf_vec(b, st)
    if not va or not vb:
        return 0.0
    dot = sum(w * vb[t] for t, w in va.items() if t in vb)
    na = math.sqrt(sum(w * w for w in va.values()))
    nb = math.sqrt(sum(w * w for w in vb.values()))
    return dot / (na * nb) if na and nb else 0.0


def entity_overlap(n: dict, p: dict) -> float:
    return _jaccard(entity_set(n), entity_set(p))


def state_dep(n: dict, p: dict) -> float:
    """N의 assumed_states (엔티티, 필드) 쌍이 P의 state_changes에 등장하는 비율."""
    n_pairs = {(a.get("entity"), a.get("field")) for a in n.get("assumed_states", [])}
    if not n_pairs:
        return 0.0
    p_pairs = {(s.get("entity"), s.get("field")) for s in p.get("state_changes", [])}
    return len(n_pairs & p_pairs) / len(n_pairs)


def location_rel(n: dict, p: dict) -> float:
    ln, lp = n.get("location_id"), p.get("location_id")
    return 1.0 if ln and ln != "unknown" and ln == lp else 0.0


def time_rel(n_idx: int, p: dict) -> float:
    """order(저장 순서) 거리 감쇠. memory/document 사건은 시간축 위치가
    서술 위치와 다르므로 중립값 0.5."""
    if p.get("reality_status") in ("memory", "document"):
        return 0.5
    dist = abs(n_idx - p["global_idx"])
    return 1.0 / (1.0 + dist / 10.0)


_CR_IDF_CACHE: dict = {}


def _cr_idf_of(corpus: list[dict]) -> dict:
    """cause·result 텍스트 전용 IDF (summary IDF와 분리 — 어휘 분포가 다르다)."""
    key = ("cr", len(corpus),
           corpus[0].get("event_id") if corpus else "",
           corpus[-1].get("event_id") if corpus else "")
    if key not in _CR_IDF_CACHE:
        from collections import Counter
        df = Counter()
        n_docs = 0
        for ev in corpus:
            for field in ("cause", "result"):
                df.update(_tokens(str(ev.get(field) or "")))
                n_docs += 1
        _CR_IDF_CACHE[key] = {
            "idf": {t: math.log((1 + n_docs) / (1 + c)) + 1.0
                    for t, c in df.items()},
            "max_idf": math.log(1 + n_docs) + 1.0,
        }
    return _CR_IDF_CACHE[key]


def causal_link(n: dict, p: dict, corpus: list[dict] | None = None) -> float:
    """S_causal(P→N) = tfidf_cos(P.result, N.cause) — 개정 3차 (팀원 제안).
    과거 사건 P의 결과가 신규 사건 N의 원인을 만드는 방향성 인과 신호."""
    a, b = str(p.get("result") or ""), str(n.get("cause") or "")
    if not a.strip() or not b.strip():
        return 0.0
    if not corpus:
        return _jaccard(_tokens(a), _tokens(b))
    st = _cr_idf_of(corpus)
    va, vb = _tfidf_vec(a, st), _tfidf_vec(b, st)
    if not va or not vb:
        return 0.0
    dot = sum(w * vb[t] for t, w in va.items() if t in vb)
    na = math.sqrt(sum(w * w for w in va.values()))
    nb = math.sqrt(sum(w * w for w in vb.values()))
    return dot / (na * nb) if na and nb else 0.0


def thread_link(n: dict, p: dict) -> float:
    return 0.0  # 프로토타입: 복선(thread) 리스트 미구현 → 0 고정


def reas(n_idx: int, p: dict) -> float:
    """독자 기억 감쇠 (표시용 라벨 전용 — 점수에 곱하지 말 것)."""
    return math.exp(-abs(n_idx - p["global_idx"]) / 50.0)


# ---------------------------------------------------------------- 후보/순위

def build_inverted_index(corpus: list[dict]) -> dict:
    """엔티티 ID → 그 엔티티가 등장한 사건 global_idx 목록."""
    index: dict[str, list[int]] = {}
    for ev in corpus:
        for eid in entity_set(ev):
            index.setdefault(eid, []).append(ev["global_idx"])
    return index


def candidates(n: dict, corpus: list[dict], index: dict | None = None) -> list[dict]:
    """(N의 엔티티가 등장한 사건) ∪ (같은 장소 사건) — 전수 계산 회피."""
    if index is None:
        index = build_inverted_index(corpus)
    idxs: set[int] = set()
    for eid in entity_set(n):
        idxs.update(index.get(eid, []))
    loc = n.get("location_id")
    if loc and loc != "unknown":
        idxs.update(ev["global_idx"] for ev in corpus if ev.get("location_id") == loc)
    idxs.discard(n.get("global_idx", -1))
    return [corpus[i] for i in sorted(idxs)]


def rank(n: dict, corpus: list[dict] | None = None, top_k: int = 5,
         mapping: dict | None = None) -> list[dict]:
    """신규 사건 N에 대해 관련 과거 사건 top-k를 ERS 점수로 순위화."""
    if corpus is None:
        corpus = load_corpus()
    if mapping is None:
        _, mapping = load_registry()
    n = normalize(n, mapping)
    n_idx = n.get("global_idx", len(corpus))  # 신규 사건 = 코퍼스 맨 뒤

    results = []
    for p in candidates(n, corpus):
        comp = {
            "semantic": semantic_sim(n, p, corpus),
            "entity": entity_overlap(n, p),
            "statedep": state_dep(n, p),
            "causal": causal_link(n, p, corpus),
            "location": location_rel(n, p),
            "time": time_rel(n_idx, p),
            # thread: 미구현 상수 0 — 구현되면 WEIGHTS와 함께 복원 (docstring)
        }
        score = sum(WEIGHTS[k] * v for k, v in comp.items())
        r = reas(n_idx, p)
        results.append({
            "event_id": p["event_id"],
            "episode": p["episode"],
            "global_idx": p["global_idx"],
            "title": p.get("title", ""),
            "summary": p.get("summary", ""),
            "reality_status": p.get("reality_status"),
            "ers": round(score, 4),
            "components": {k: round(v, 4) for k, v in comp.items()},
            "reas": round(r, 4),
            "stale_label": "오래된 사건 — 독자가 잊었을 가능성" if r < 0.7 else None,
        })
    # 〔동결 규격 — ers_ab_report §7〕 동점 처리: 점수 내림차순, 동점이면
    # global_idx 오름차순(오래된 사건 우선). 종전의 안정 정렬이 암묵적으로
    # 하던 것을 명시한다 — 재현 구현이 여기서 갈리면 top-5 경계가 흔들린다.
    results.sort(key=lambda x: (-x["ers"], x["global_idx"]))
    return results[:top_k]


if __name__ == "__main__":
    corpus = load_corpus()
    tests = json.loads((BASE / "test_episode_06_v2.json").read_text(encoding="utf-8"))
    for ev in tests["events"]:
        print(f"\n=== {ev['event_id']} 관련 사건 top-5 ===")
        for r in rank(ev, corpus):
            label = f"  ← {r['stale_label']}" if r["stale_label"] else ""
            print(f"  {r['ers']:.3f}  {r['event_id']} ({r['episode']}화) {r['title']}{label}")
            print(f"         {r['components']}")
