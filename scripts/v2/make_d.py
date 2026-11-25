"""학습 judge v2 — D 실제 검색 음성(설계 문서 §10 PD-train D). 수정하지 않은 원문 문장 + 실제 검색 맥락, 라벨 무모순.

원문은 학습 작가 작품만(`C:\\judge_models\\V2\\train\\source\\<작가>__<제목>.txt`), 작품 단위 split 은 V2-T 의
`split.json`(작품 → train/dev). 평가 폴더(`V2\\eval\\`)는 읽지 않는다.
규칙(고정):
- 작품마다 앞부분이 MIN_PREFIX 자 이상인 지점부터 WINDOW 자 간격으로 대상 구간(문단 경계로 맞춤)을 잡는다.
- 대상 구간 문장마다 그 앞 원문 전체에서 검색(scene.build_scene 과 같은 검색·인명 규칙)한다. 맞는 조각이 없거나
  문장 글자(한글·영숫자)가 MIN_CLAIM 보다 적으면 뺀다.
- 후보를 시드 SEED 로 섞어 작품당 상한(ceil(TARGET × WORK_CAP))을 지키며 TARGET 행까지 고른다.
행: {id, type:"D", work, split, claim, context:[표시 순서], context_priority:[검색 점수 순위 — 기록용], label:"무모순",
     pair_of:null, authored_by:"code:make_d", window_start, chunk_ids, overlap_jaccard}
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path

import v2_common as C
from app.pipeline.judge_v2 import retrieve as RT
from app.pipeline.judge_v2.render import clean
from app.pipeline.judge_v2.retrieve import Retriever, bigrams, guess_names
from app.pipeline.judge_v2.scene import build_scene
from app.pipeline.judge_v2.text import chunk_prefix

MAKE_D_VERSION = "3"   # 3: 조각화를 PD-train 생성과 같게(text.chunk_prefix TEXT_VERSION 2). 2: 공백 접기
SEED = "V2:D:20261108"
TARGET = 2000
MIN_PREFIX = 3000
WINDOW = 1500
MIN_CLAIM = 10
WORK_CAP = 0.15
_CONTENT = re.compile(r"[가-힣A-Za-z0-9]")


def jaccard(a: str, b: str) -> float:
    x, y = set(bigrams(a)), set(bigrams(b))
    return len(x & y) / len(x | y) if x | y else 0.0


STRATA_BINS = 5
STRATA_SEED = "V2:D:strata:20261108"


def quantile_edges(values: list[float], bins: int = STRATA_BINS) -> list[float]:
    """분위 경계(bins−1 개). 값 v 의 칸 = bisect_right(edges, v)."""
    v = sorted(values)
    return [v[min(len(v) - 1, int(len(v) * k / bins))] for k in range(1, bins)]


def cell_of(claim: str, contexts: list[str], edges: dict) -> tuple[int, int]:
    return (bisect.bisect_right(edges["claim_len"], len(claim)),
            bisect.bisect_right(edges["overlap"], C.claim_overlap(claim, contexts)))


def quotas(match_rows: list[dict], edges: dict, target: int) -> dict[tuple[int, int], int]:
    """맞출 행(A·B)의 (주장 길이 칸 × 겹침 칸) 비율로 target 을 나눈다(최대 나머지법, 합 = target)."""
    cnt = Counter(cell_of(r["claim"], C.contexts_of(r), edges) for r in match_rows)
    n = sum(cnt.values())
    raw = {c: target * k / n for c, k in cnt.items()}
    q = {c: int(v) for c, v in raw.items()}
    for c in sorted(raw, key=lambda c: (-(raw[c] - q[c]), c))[:target - sum(q.values())]:
        q[c] += 1
    return q


def load_split(path: Path) -> dict[str, tuple[str, str]]:
    """split.json → {원문 파일 이름(작가__제목): (작품 id, train|dev)}.
    V2-T 형식 {"seed", "works": [{id, author, title, split}, ...]} 를 받는다(행의 work 는 이 id).
    옛 형식 {"train": [...], "dev": [...]} · {작품: split} 은 이름을 그대로 id 로 쓴다."""
    d = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(d, dict) and isinstance(d.get("works"), list):
        return {f"{w['author']}__{w['title']}": (str(w["id"]), str(w["split"])) for w in d["works"]}
    if isinstance(d, dict) and all(isinstance(v, list) for v in d.values()):
        return {w: (w, s) for s, ws in d.items() for w in ws}
    if isinstance(d, dict):
        return {k: (k, str(v)) for k, v in d.items()}
    raise SystemExit(f"split.json 형식을 모른다: {type(d)}")


def windows(text: str) -> list[int]:
    """대상 구간 시작 위치(문단 시작으로 맞춤)."""
    starts = [0] + [m.end() for m in re.finditer(r"\n", text)]
    out, p = [], MIN_PREFIX
    while p < len(text) - 200:
        s = min((x for x in starts if x >= p), default=None)
        if s is None:
            break
        out.append(s)
        p = s + WINDOW
    return out


def candidates(work: str, text: str) -> list[dict]:
    out = []
    for w in windows(text):
        prefix = text[:w]
        end = text.find("\n", w + WINDOW)
        target = text[w:end if end > 0 else len(text)]
        ret = Retriever(chunk_prefix(prefix), guess_names(prefix))
        for it in build_scene(prefix, target, retriever=ret):
            if len(_CONTENT.findall(it.claim)) < MIN_CLAIM or not it.contexts:
                continue
            out.append({"work": work, "window_start": w, "claim": clean(it.claim), "context": [clean(c) for c in it.contexts],
                        "context_priority": it.priority, "chunk_ids": it.chunk_ids,
                        "overlap_jaccard": round(jaccard(it.claim, " ".join(it.contexts)), 4)})
    return out


def make(source: Path, split: dict[str, tuple[str, str]], target: int = TARGET,
         match_rows: list[dict] | None = None) -> tuple[list[dict], dict]:
    """match_rows 를 주면 후보 전체에서 그 행들의 (주장 길이 × 겹침) 분위 칸 비율에 맞춰 층화 표본을 뽑는다."""
    cands, skipped = [], []
    # V2-T source_meta.json 과 같은 규칙: 줄바꿈을 LF 로 맞춘 글(utf-8)의 sha256 (파일은 CRLF 로 저장돼 있다)
    source_sha = {f.name: hashlib.sha256(f.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
                  for f in sorted(source.glob("*.txt"))}
    for f in sorted(source.glob("*.txt")):
        hit = split.get(f.stem)
        if hit is None:
            skipped.append(f.stem)
            continue
        work, sp = hit
        text = f.read_text(encoding="utf-8")
        for c in candidates(work, text):
            cands.append(c | {"split": sp, "source": f.name})
    rng = C.seeded(SEED if match_rows is None else STRATA_SEED)
    rng.shuffle(cands)
    cap = math.ceil(target * WORK_CAP)
    per: Counter = Counter()
    rows = []
    strata = None
    if match_rows is not None:
        edges = {"claim_len": quantile_edges([float(len(r["claim"])) for r in match_rows]),
                 "overlap": quantile_edges([C.claim_overlap(r["claim"], C.contexts_of(r)) for r in match_rows])}
        q = quotas(match_rows, edges, target)
        got: Counter = Counter()
        avail = Counter(cell_of(c["claim"], c["context"], edges) for c in cands)
        strata = {"bins": STRATA_BINS, "seed": STRATA_SEED, "edges": edges, "match_rows": len(match_rows)}
    for c in cands:
        if len(rows) >= target:
            break
        if per[c["work"]] >= cap:
            continue
        if strata is not None:
            cell = cell_of(c["claim"], c["context"], edges)
            if got[cell] >= q.get(cell, 0):
                continue
            got[cell] += 1
        per[c["work"]] += 1
        rows.append({"id": f"D:{c['work']}:{per[c['work']]}", "type": "D", "work": c["work"], "split": c["split"],
                     "claim": c["claim"], "context": c["context"], "context_priority": c["context_priority"],
                     "label": "무모순", "pair_of": None, "authored_by": "code:make_d", "window_start": c["window_start"],
                     "chunk_ids": c["chunk_ids"], "overlap_jaccard": c["overlap_jaccard"], "source": c["source"]})
    ov = sorted(r["overlap_jaccard"] for r in rows)
    meta = {"version": MAKE_D_VERSION, "versions": C.VERSIONS, "seed": SEED, "target": target, "rows": len(rows),
            "candidates": len(cands), "per_work": dict(per), "split": dict(Counter(r["split"] for r in rows)),
            "works_without_split": skipped, "overlap_jaccard_median": ov[len(ov) // 2] if ov else None,
            "constants": {"MIN_PREFIX": MIN_PREFIX, "WINDOW": WINDOW, "MIN_CLAIM": MIN_CLAIM, "WORK_CAP": WORK_CAP},
            "retrieval": {"K": RT.K, "BM25_K1": RT.BM25_K1, "BM25_B": RT.BM25_B, "NAME_BONUS": RT.NAME_BONUS,
                          "NAME_BONUS_MAX": RT.NAME_BONUS_MAX, "NAME_MIN_COUNT": RT.NAME_MIN_COUNT,
                          "ngram": "문자 2-gram(한글·영숫자만)", "chunk": "문단 경계 약 300자(text.chunk_prefix)"},
            "source_sha256": source_sha}
    if strata is not None:
        short = {f"{a},{b}": q[(a, b)] - got[(a, b)] for a, b in sorted(q) if got[(a, b)] < q[(a, b)]}
        strata |= {"quota": {f"{a},{b}": v for (a, b), v in sorted(q.items())},
                   "got": {f"{a},{b}": v for (a, b), v in sorted(got.items())},
                   "available": {f"{a},{b}": v for (a, b), v in sorted(avail.items())},
                   "shortfall": short, "shortfall_total": sum(short.values())}
        meta["sampling"] = "stratified(claim_len × overlap)"
        meta["strata"] = strata
    else:
        meta["sampling"] = "random"
    return rows, meta


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source", type=Path, default=Path(r"C:\judge_models\V2\train\source"))
    ap.add_argument("--split", type=Path, default=Path(r"C:\judge_models\V2\train\split.json"))
    ap.add_argument("--target", type=int, default=TARGET)
    ap.add_argument("--out", type=Path, default=Path(r"C:\judge_models\V2\train\gen\D.jsonl"))
    ap.add_argument("--match", type=Path, help="이 행들(A·B)의 주장 길이 × 겹침 분포에 맞춰 층화 표본(예: pd_train_v2.jsonl)")
    a = ap.parse_args(argv)
    if "eval" in {p.name.lower() for p in a.source.parents} or a.source.name.lower() == "eval":
        raise SystemExit("평가 폴더는 읽지 않는다")
    if not a.split.exists():
        raise SystemExit(f"split.json 이 없다(V2-T 가 만든다): {a.split}")
    match = [r for r in C.read_jsonl(a.match) if r.get("type") in ("A", "B")] if a.match else None
    rows, meta = make(a.source, load_split(a.split), a.target, match)
    if a.match:
        meta["match_file"] = {"path": str(a.match), "sha256": C.sha256_file(a.match)}
    if a.out.exists():
        raise SystemExit(f"이미 있다(덮어쓰지 않는다): {a.out}")
    C.write_jsonl(a.out, rows)
    a.out.with_suffix(".meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in meta.items() if k != "per_work"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
