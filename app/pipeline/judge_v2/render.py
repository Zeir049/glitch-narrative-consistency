"""입력 렌더러 — 맥락(전제) + 주장(가설) 문장 쌍 (학습 judge v2). 규칙 고정(바꾸면 RENDER_VERSION 을 올린다).

판단 세션 결정(main dd50160 설계 문서 §10): **맥락이 앞(전제), 주장이 뒤(가설)**. E1·E1L 이 (전제, 가설)로 NLI 학습됐고
MiniCheck·AlignScore 도 (문서, 주장) 순서다. `[주장]`·`[맥락]` 은 구성 요소 표기이지 순서가 아니다.

- 첫째 칸 = `[맥락] 조각1 … 조각k`(표시 순서 = 앞선 원문 안 위치 순), 둘째 칸 = `[주장] 문장`.
- 토크나이저 쌍 입력, `truncation="only_first"`, `max_length` 512 — 넘치면 맥락 칸의 끝부분만 잘린다.
- 주장은 CLAIM_MAX 토큰을 넘으면 그 길이로 줄인 뒤 넣는다(only_first 가 주장을 자르지 못하므로).
- 주장·조각 글은 공백·줄바꿈을 공백 하나로 접는다(clean).
"""
from __future__ import annotations

from typing import Any, Sequence

RENDER_VERSION = "2"   # 1: 주장 첫째 칸·점수 순 조각 배정 → 2: 맥락 첫째 칸·only_first
CONTEXT_FIRST = True
MAX_LENGTH = 512
CLAIM_MAX = 160
CLAIM_TAG = "[주장] "
CONTEXT_TAG = "[맥락] "
CONTEXT_SEP = " … "
NO_CONTEXT = "(앞선 원문에서 찾은 구간 없음)"


def clean(text: str) -> str:
    """공백·줄바꿈을 공백 하나로(출처마다 다른 줄바꿈 보존 차이를 없앤다 — E1·E1L 토크나이저는 원래 같은 ID 를 낸다)."""
    return " ".join(str(text).split())


def context_text(contexts: Sequence[str]) -> str:
    return CONTEXT_TAG + (CONTEXT_SEP.join(clean(c) for c in contexts) if contexts else NO_CONTEXT)


def claim_text(tok: Any, claim: str) -> str:
    c = CLAIM_TAG + clean(claim)
    ids = tok(c, add_special_tokens=False)["input_ids"]
    if len(ids) <= CLAIM_MAX:
        return c
    return tok.decode(ids[:CLAIM_MAX])


def render_ids(tok: Any, claim: str, contexts: Sequence[str], max_length: int = MAX_LENGTH) -> dict:
    """토크나이저 쌍 입력 (맥락, 주장), truncation="only_first". 반환: input_ids·attention_mask·token_type_ids·truncated."""
    a, b = context_text(contexts), claim_text(tok, claim)
    enc = tok(a, b, truncation="only_first", max_length=max_length)
    full = len(tok(a, add_special_tokens=False)["input_ids"]) + len(tok(b, add_special_tokens=False)["input_ids"]) + 3
    out = {"input_ids": list(enc["input_ids"]), "attention_mask": list(enc["attention_mask"]),
           "token_type_ids": list(enc.get("token_type_ids") or [0] * len(enc["input_ids"]))}
    out["truncated"] = full > len(out["input_ids"])
    return out


def render_text(claim: str, contexts: Sequence[str]) -> str:
    """사람이 읽는 글(로그·감사용, 맥락 줄이 먼저). 절단은 하지 않는다."""
    return f"{context_text(contexts)}\n{CLAIM_TAG}{clean(claim)}"
