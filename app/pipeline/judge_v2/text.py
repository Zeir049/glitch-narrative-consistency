"""문장 분리·문단 조각(학습 judge v2). 규칙은 고정이다 — 바꾸면 v2 데이터·모델 meta 의 TEXT_VERSION 을 올린다."""
from __future__ import annotations

import re

TEXT_VERSION = "2"  # 2: chunk_prefix 를 PD-train 생성 조각화(220–450자)와 같게
TERMINALS = ".?!。"               # 문장 끝(말줄임표 …은 문장 안 쉼으로 본다)
QUOTE_END = TERMINALS + "…"      # 대사 끝 판정에는 말줄임표도 문장 끝으로 본다
# 여는 따옴표 → 닫는 따옴표. 곧은 따옴표는 같은 글자로 열고 닫는다.
QUOTES = {"“": "”", "‘": "’", "「": "」", "『": "』", '"': '"', "'": "'"}
CLOSERS = set(QUOTES.values())
MIN_SENTENCE_CHARS = 2          # 글자(한글·영숫자) 수가 이보다 적은 조각은 문장으로 치지 않는다
_CONTENT = re.compile(r"[가-힣A-Za-z0-9]")


def split_paragraphs(text: str) -> list[str]:
    """줄 단위 문단(빈 줄 무시, 앞뒤 공백 제거)."""
    return [p.strip() for p in re.split(r"\r?\n", text or "") if p.strip()]


def _content_len(s: str) -> int:
    return len(_CONTENT.findall(s))


def _split_paragraph(p: str) -> list[str]:
    out: list[str] = []
    buf: list[str] = []
    stack: list[str] = []
    n = len(p)
    i = 0

    def cut() -> None:
        s = "".join(buf).strip()
        buf.clear()
        if s:
            out.append(s)

    while i < n:
        ch = p[i]
        buf.append(ch)
        if stack and ch == stack[-1]:                       # 닫는 따옴표
            stack.pop()
            if not stack:
                prev = "".join(buf[:-1]).rstrip()
                nxt = p[i + 1:].lstrip()
                # 대사가 종결 부호로 끝나고, 뒤가 문단 끝이거나 새 대사면 문장 경계
                if prev and prev[-1] in QUOTE_END and (not nxt or nxt[0] in QUOTES):
                    cut()
        elif ch in QUOTES and ch not in CLOSERS - set(QUOTES):  # 여는 따옴표
            if not (ch in ('"', "'") and stack and stack[-1] == ch):
                stack.append(QUOTES[ch])
        elif not stack and ch in TERMINALS:
            while i + 1 < n and (p[i + 1] in TERMINALS or p[i + 1] in CLOSERS - {'"', "'"}):
                i += 1
                buf.append(p[i])
            if i + 1 >= n or p[i + 1].isspace():
                cut()
        i += 1
    cut()
    # 너무 짧은 조각(문장부호만 등)은 앞 문장에 붙인다
    merged: list[str] = []
    for s in out:
        if merged and _content_len(s) < MIN_SENTENCE_CHARS:
            merged[-1] = f"{merged[-1]} {s}"
        else:
            merged.append(s)
    return [s for s in merged if _content_len(s) >= MIN_SENTENCE_CHARS]


def split_sentences(text: str) -> list[str]:
    """문장 분리. 문단(줄) 경계는 항상 문장 경계다. 따옴표 안의 종결 부호에서는 끊지 않는다.
    대사(“…”) 뒤에 '하고 물었다' 같은 서술이 이어지면 한 문장, 대사 다음이 문단 끝이거나 새 대사면 대사 하나가 한 문장이다."""
    out: list[str] = []
    for p in split_paragraphs(text):
        out.extend(_split_paragraph(p))
    return out


_LGEN_SENT_END = re.compile(r"(?<=[.!?…])[\"'”’」』)]*\s+")
CHUNK_LO, CHUNK_HI = 220, 450      # PD-train 생성(L-gen pd_train.make_chunks)과 같은 값


def _lgen_sentences(p: str) -> list[str]:
    return [s for s in _LGEN_SENT_END.split(p.strip()) if s]


def chunk_prefix(text: str, lo: int = CHUNK_LO, hi: int = CHUNK_HI) -> list[str]:
    """앞선 원문 조각 — PD-train 생성 코드(L-gen `scripts/eval/pd_train.py` make_chunks)와 글자 하나까지 같은 규칙.
    문단을 공백 하나로 접고, hi 를 넘는 문단은 문장 경계로 끊고, 조각을 이어 붙이되 hi 를 넘고 lo 이상이면 끊는다.
    마지막 조각이 lo/2 보다 짧으면 앞 조각에 붙인다. 학습 행(A·B·C)과 D·장면 검색이 같은 조각 모양을 쓰게 한다(TEXT_VERSION 2)."""
    paras = [re.sub(r"\s+", " ", ln).strip() for ln in (text or "").split("\n") if ln.strip()]
    pieces: list[str] = []
    for p in paras:
        if len(p) <= hi:
            pieces.append(p)
            continue
        cur = ""
        for s in _lgen_sentences(p):
            if cur and len(cur) + 1 + len(s) > hi:
                pieces.append(cur)
                cur = s
            else:
                cur = f"{cur} {s}".strip()
        if cur:
            pieces.append(cur)
    out: list[str] = []
    cur = ""
    for p in pieces:
        if cur and len(cur) + 1 + len(p) > hi and len(cur) >= lo:
            out.append(cur)
            cur = p
        else:
            cur = f"{cur} {p}".strip()
    if cur:
        if out and len(cur) < lo // 2:
            out[-1] = f"{out[-1]} {cur}"
        else:
            out.append(cur)
    return out
