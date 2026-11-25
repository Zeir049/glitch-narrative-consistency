"""학습 judge v2 — 쌍 행(A·B·C·D 스키마) 채점: 행별 p(모순)(v2 렌더러, softmax[모순], 온도 T).

영 학습 기준선(E1·E1L, v2_meta 없음 → T=1) 용. 보고:
- 라벨별 p(모순) 분포(평균·분위수), 고정 기준값(0.5·0.7·0.9)에서 모순 판정 비율(무모순 행이면 오탐률), 3분류 argmax=모순 비율
- 작품별 오탐률(@0.5)
- 실효 k: 512토큰 안에 들어간 맥락 조각 수(통째로 / 일부라도)

    python scripts/v2/score_rows_v2.py --model-dir C:\\judge_models\\E1 --rows D.jsonl --split dev --out X.json
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

import v2_common as C
from app.pipeline.judge_v2 import render as R

THRESHOLDS = (0.5, 0.7, 0.9)


def effective_k(tok, row: dict, kept_len: int) -> tuple[int, int]:  # noqa: ANN001
    """(통째로 들어간 조각 수, 일부라도 들어간 조각 수)."""
    ctxs = C.contexts_of(row)
    if not ctxs:
        return 0, 0
    claim = len(tok(R.claim_text(tok, row["claim"]), add_special_tokens=False)["input_ids"])
    room = kept_len - 3 - claim
    pos = len(tok(R.CONTEXT_TAG, add_special_tokens=False)["input_ids"])
    sep = len(tok(R.CONTEXT_SEP, add_special_tokens=False)["input_ids"])
    full = part = 0
    for i, c in enumerate(ctxs):
        start = pos + (sep if i else 0)
        end = start + len(tok(R.clean(c), add_special_tokens=False)["input_ids"])
        if start < room:
            part += 1
            if end <= room:
                full += 1
        pos = end
    return full, part


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--model-dir", type=Path, required=True)
    ap.add_argument("--rows", type=Path, required=True)
    ap.add_argument("--split", default=None)
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    rows = [r for r in C.read_jsonl(a.rows) if a.split is None or r.get("split") == a.split]
    meta_p = a.model_dir / "v2_meta.json"
    t = float(json.loads(meta_p.read_text(encoding="utf-8")).get("temperature") or 1.0) if meta_p.exists() else 1.0
    tok = AutoTokenizer.from_pretrained(str(a.model_dir))
    model = AutoModelForSequenceClassification.from_pretrained(str(a.model_dir)).to(a.device)
    encs = [C.encode_item(tok, r["claim"], C.contexts_of(r)) for r in rows]
    logits = C.predict_logits(model, tok, encs, a.device)
    # 3분류 argmax 도 따로(같은 입력)
    ci = C.contra_index(model)
    argmax_contra = []
    model.eval()
    with torch.no_grad():
        for s in range(0, len(encs), 16):
            b = {k: v.to(a.device) for k, v in C.collate(encs[s:s + 16], tok.pad_token_id).items()}
            with torch.autocast(device_type="cuda", dtype=torch.float16, enabled=(a.device == "cuda")):
                argmax_contra += (model(**b).logits.float().argmax(-1) == ci).tolist()
    ps = [C.sigmoid(x, t) for x in logits]
    by_lab: dict[int, list[float]] = defaultdict(list)
    for r, p in zip(rows, ps):
        by_lab[C.y_of(r)].append(p)

    def q(v: list[float]) -> dict:
        v = sorted(v)
        return {"n": len(v), "mean": round(statistics.mean(v), 4), "p50": round(v[len(v) // 2], 4),
                "p90": round(v[int(len(v) * 0.9)], 4), "p99": round(v[min(len(v) - 1, int(len(v) * 0.99))], 4)}
    out = {"model_dir": str(a.model_dir), "model_sha256": C.sha256_file(a.model_dir / "model.safetensors"),
           "rows_file": str(a.rows), "rows_sha256": C.sha256_file(a.rows), "split": a.split, "temperature": t,
           "versions": C.VERSIONS, "n": len(rows), "labels": dict(Counter(C.y_of(r) for r in rows))}
    out["by_label"] = {}
    for y, v in sorted(by_lab.items()):
        out["by_label"][str(y)] = q(v) | {f"rate@{th}": round(sum(p >= th for p in v) / len(v), 4) for th in THRESHOLDS}
    out["argmax_contra_rate"] = {str(y): round(sum(am for r, am in zip(rows, argmax_contra) if C.y_of(r) == y) /
                                               max(1, sum(C.y_of(r) == y for r in rows)), 4) for y in by_lab}
    # 유형별 정확도(@0.5)와 쌍 성질(같은 쌍에서 모순 행 p > 무모순 행 p 인 비율)
    by_type: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r, p in zip(rows, ps):
        c = by_type[str(r.get("type"))]
        c[0] += int((p >= 0.5) == bool(C.y_of(r)))
        c[1] += 1
    out["acc@0.5_by_type"] = {t: [round(c / n, 4), n] for t, (c, n) in sorted(by_type.items())}
    pid = {r.get("id"): (r, p) for r, p in zip(rows, ps)}
    order: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r, p in zip(rows, ps):
        q2 = pid.get(r.get("pair_of"))
        if q2 is None or C.y_of(r) != 1 or C.y_of(q2[0]) != 0:
            continue
        o = order[str(r.get("type"))]
        o[0] += int(p > q2[1])
        o[1] += 1
    out["pair_contra_gt_ok"] = {t: [round(c / n, 4), n] for t, (c, n) in sorted(order.items())}
    pw: dict[str, list[float]] = defaultdict(list)
    for r, p in zip(rows, ps):
        pw[r.get("work")].append(p)
    out["by_work_rate@0.5"] = {w: [round(sum(p >= 0.5 for p in v) / len(v), 4), len(v)] for w, v in sorted(pw.items())}
    ks = [effective_k(tok, r, len(e["input_ids"])) for r, e in zip(rows, encs)]
    out["effective_k"] = {"contexts_mean": round(statistics.mean(len(C.contexts_of(r)) for r in rows), 3),
                          "full_mean": round(statistics.mean(k[0] for k in ks), 3),
                          "partial_mean": round(statistics.mean(k[1] for k in ks), 3),
                          "full_dist": dict(sorted(Counter(k[0] for k in ks).items())),
                          "truncated_rows": sum(1 for r, e in zip(rows, encs) if len(e["input_ids"]) >= R.MAX_LENGTH)}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("n", "by_label", "argmax_contra_rate", "acc@0.5_by_type", "pair_contra_gt_ok")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
