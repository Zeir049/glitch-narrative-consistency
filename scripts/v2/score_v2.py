"""학습 judge v2 채점(설계 문서 §10) — S 형식 장면 쌍둥이 {id,work,split,prefix,target_P,target_N,planted,evidence,type}.

장면 점수 = 문장 p(모순) max(온도 T 적용). 보고:
- 장면 AUROC + 층화 bootstrap(2,000, 시드 0)
- recall·오탐 @기준값(모델 meta 의 dev 값, 또는 --threshold)
- 쌍둥이 d = max_P − max_N(평균·중앙값·부호 수·쌍 bootstrap 95% 구간, 시드 0)
- 근거 일치: P 장면에서 점수 1위 문장이 **P/N 사이에 다른 문장**(P 의 문장 중 정규화해 N 에 없는 것)인가.
  S·PD-eval 의 `planted` 는 편집 설명이라 쓰지 않는다(main 451bab2 §10 고정 레시피). 비교 열(ii)의 window_sentence 는
  window_grounded(문자 2-gram Jaccard ≥ 0.5)로 같은 다른 문장 집합에 대어 본다.
입력 행은 {id, prefix, target_P, target_N, ...} 가 있어야 한다(없으면 멈춘다).
- 유형별 recall(type 이 있으면)
영 학습 기준선(E1·E1L 등 3분류 모델)도 같은 읽기(softmax[모순])로 채점한다(meta 가 없으면 T=1, 기준값 없음).

    python scripts/v2/score_v2.py --model-dir DIR --s S.jsonl [--split dev] --out OUT.json [--scores-out X.jsonl]
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
from pathlib import Path

import v2_common as C
from app.pipeline.judge_v2.retrieve import bigrams, normalize
from app.pipeline.judge_v2.text import split_sentences

GROUND_DICE = 0.5
WINDOW_JACCARD = 0.5
GROUNDING_RULE = "P 장면 1위 문장 ∈ (P 문장 중 정규화해 N 에 없는 문장) — planted 안 씀"
REQUIRED = ("id", "prefix", "target_P", "target_N")


def diff_sentences(target_p: str, target_n: str) -> list[str]:
    """P 의 문장 중 N 에 (정규화해) 없는 문장 — 장면 항목과 같은 문장 분리(split_sentences)."""
    n_set = {normalize(x) for x in split_sentences(target_n)}
    return [x for x in split_sentences(target_p) if normalize(x) not in n_set]


def grounded_diff(top: str | None, diff: list[str]) -> bool:
    """점수 1위 문장이 P/N 사이에 다른 문장인가(정규화 같음)."""
    return bool(top) and normalize(top) in {normalize(x) for x in diff}


def jaccard(a: str, b: str) -> float:
    x, y = set(bigrams(a)), set(bigrams(b))
    return len(x & y) / len(x | y) if x | y else 0.0


def window_grounded(window_sentence: str | None, diff: list[str]) -> bool:
    """비교 열(ii) LLM 의 window_sentence 가 다른 문장 하나와 문자 2-gram Jaccard ≥ 0.5 인가."""
    return bool(window_sentence) and any(jaccard(window_sentence, d) >= WINDOW_JACCARD for d in diff)


def check_rows(s_rows: list[dict]) -> None:
    bad = [r.get("id") for r in s_rows if not all(r.get(k) for k in REQUIRED)]
    if bad:
        raise SystemExit(f"장면 쌍둥이 행에 {REQUIRED} 가 없다: {bad[:5]}")


def dice(a: str, b: str) -> float:
    x, y = set(bigrams(a)), set(bigrams(b))
    return 2 * len(x & y) / (len(x) + len(y)) if x and y else 0.0


def grounded(top: str, planted: str | list | None) -> bool:
    cands = planted if isinstance(planted, list) else [planted]
    for p in cands:
        if not p:
            continue
        a, b = normalize(top), normalize(str(p))
        if a and b and (a in b or b in a or dice(top, str(p)) >= GROUND_DICE):
            return True
    return False


def score(model_dir: Path, s_rows: list[dict], device: str, threshold: float | None = None) -> tuple[dict, list[dict]]:
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    meta_p = model_dir / "v2_meta.json"
    meta = json.loads(meta_p.read_text(encoding="utf-8")) if meta_p.exists() else {}
    t = float(meta.get("temperature") or 1.0)
    thr = threshold if threshold is not None else meta.get("threshold")
    check_rows(s_rows)
    tok = AutoTokenizer.from_pretrained(str(model_dir))
    model = AutoModelForSequenceClassification.from_pretrained(str(model_dir)).to(device)
    diff_of = {r["id"]: diff_sentences(r["target_P"], r["target_N"]) for r in s_rows}
    scenes = C.scenes_of(s_rows)
    encs, owner = [], []
    for si, sc in enumerate(scenes):
        for it in sc["items"]:
            encs.append(C.encode_item(tok, it.claim, it.contexts))
            owner.append(si)
    flat = C.predict_logits(model, tok, encs, device)
    per: list[list[float]] = [[] for _ in scenes]
    for si, v in zip(owner, flat):
        per[si].append(C.sigmoid(v, t))
    rows, smax = [], {}
    for sc, ps in zip(scenes, per):
        j = max(range(len(ps)), key=lambda k: (ps[k], -k)) if ps else None
        smax[sc["scene_id"]] = (max(ps, default=0.0), sc["y"])
        rows.append({"scene_id": sc["scene_id"], "pair_id": sc["pair_id"], "kind": sc["kind"], "y": sc["y"],
                     "type": sc.get("type"), "work": sc.get("work"), "max": max(ps, default=0.0),
                     "top_claim": sc["items"][j].claim if j is not None else None,
                     "grounded": grounded_diff(sc["items"][j].claim, diff_of[sc["pair_id"]]) if (j is not None and sc["y"]) else None,
                     "n_diff_sentences": len(diff_of[sc["pair_id"]]) if sc["y"] else None,
                     "claims": [{"claim": it.claim, "p": round(p, 6), "chunks": it.chunk_ids} for it, p in zip(sc["items"], ps)]})
    out = {"model_dir": str(model_dir), "model_sha256": C.sha256_file(model_dir / "model.safetensors"),
           "temperature": t, "threshold": thr, "versions": C.VERSIONS, "n_pairs": len(s_rows),
           "n_scenes": len(scenes), "n_claims": len(encs), "scene_auroc": C.bootstrap_auroc(smax, 2000, 0)}
    if thr is not None:
        pos = [r for r in rows if r["y"]]
        neg = [r for r in rows if not r["y"]]
        out["at_threshold"] = {"tp": sum(r["max"] >= thr for r in pos), "n_pos": len(pos),
                               "fp": sum(r["max"] >= thr for r in neg), "n_neg": len(neg)}
        types = sorted({r["type"] for r in pos if r.get("type")})
        out["by_type"] = {ty: [sum(r["max"] >= thr for r in pos if r["type"] == ty), sum(r["type"] == ty for r in pos)]
                          for ty in types}
    by_pair: dict[str, dict] = {}
    for r in rows:
        by_pair.setdefault(r["pair_id"], {})[r["kind"]] = r["max"]
    d = [v["P"] - v["N"] for v in by_pair.values() if "P" in v and "N" in v]
    rng = random.Random(0)
    bm = sorted(statistics.mean(rng.choice(d) for _ in d) for _ in range(2000)) if d else []
    out["twin_d"] = {"n": len(d), "mean": round(statistics.mean(d), 4) if d else None,
                     "median": round(statistics.median(d), 4) if d else None,
                     "P_gt_N": sum(x > 0 for x in d), "eq": sum(x == 0 for x in d), "P_lt_N": sum(x < 0 for x in d),
                     "mean_ci": [round(bm[50], 4), round(bm[1949], 4)] if bm else None}
    g = [r["grounded"] for r in rows if r["y"] and r["grounded"] is not None]
    out["grounding"] = {"rule": GROUNDING_RULE, "top_is_diff": sum(g), "n_pos": len(g),
                        "pairs_without_diff": sum(1 for v in diff_of.values() if not v)}
    return out, rows


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--model-dir", type=Path, required=True)
    ap.add_argument("--s", type=Path, required=True)
    ap.add_argument("--split", default=None, help="이 split 행만(예: dev)")
    ap.add_argument("--threshold", type=float, default=None)
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--scores-out", type=Path)
    ap.add_argument("--tag", default=None, help="산출물에 붙일 꼬리표(예: 탐색 실행)")
    a = ap.parse_args(argv)
    s_rows = [r for r in C.read_jsonl(a.s) if a.split is None or r.get("split") == a.split]
    out, rows = score(a.model_dir, s_rows, a.device, a.threshold)
    out |= {"s_file": str(a.s), "s_sha256": C.sha256_file(a.s), "split": a.split}
    if a.tag:
        out = {"tag": a.tag} | out
        rows = [{"tag": a.tag} | r for r in rows]
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    if a.scores_out:
        C.write_jsonl(a.scores_out, rows)
    print(json.dumps({k: out[k] for k in ("n_scenes", "n_claims", "scene_auroc", "twin_d", "grounding")
                      if k in out} | {"at_threshold": out.get("at_threshold")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
