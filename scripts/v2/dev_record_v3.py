"""고정 레시피 dev 기록 (main 451bab2 §10) — **기록만, 선택에 쓰지 않음**. 온도 1.0 고정.

- 행 단위: dev 의 A·B·D 정확도(p ≥ 0.5)와 AUROC(A·B 는 유형 안, D 는 무모순뿐이라 'A·B 모순 행 대 D' 로)
- 쌍 성질: 모순 행 p > 짝 무모순 행 p 인 비율(A·B)
- S dev 장면 AUROC(장면 → 문장 → 검색 → max, 층화 bootstrap 2,000·시드 0)

    python scripts/v2/dev_record_v3.py --model DIR --pairs pd_train_v3.jsonl --d gen\\D.jsonl --s pd_train_v3_S.jsonl --out DIR\\dev_record.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import v2_common as C

NOTE = "기록만 — 선택에 쓰지 않음 (고정 레시피, 온도 1.0)"


def row_metrics(rows: list[dict], probs: list[float]) -> dict:
    """유형별 정확도·AUROC, 쌍 성질. rows 와 probs 는 같은 순서."""
    p_of = {r["id"]: p for r, p in zip(rows, probs)}
    out: dict = {}
    types = sorted({r["type"] for r in rows})
    for t in types:
        sub = [(p, C.y_of(r)) for r, p in zip(rows, probs) if r["type"] == t]
        pos = [p for p, y in sub if y]
        neg = [p for p, y in sub if not y]
        acc = sum((p >= 0.5) == bool(y) for p, y in sub) / len(sub)
        out[t] = {"n": len(sub), "pos": len(pos), "acc": round(acc, 4),
                  "auroc": round(C.auc(pos, neg), 4) if pos and neg else None,
                  "mean_p_pos": round(sum(pos) / len(pos), 4) if pos else None,
                  "mean_p_neg": round(sum(neg) / len(neg), 4) if neg else None}
    pos_ab = [p for r, p in zip(rows, probs) if r["type"] != "D" and C.y_of(r)]
    neg_d = [p for r, p in zip(rows, probs) if r["type"] == "D"]
    if pos_ab and neg_d:
        out["AB_pos_vs_D"] = {"auroc": round(C.auc(pos_ab, neg_d), 4), "n_pos": len(pos_ab), "n_neg": len(neg_d)}
    allp = [p for r, p in zip(rows, probs) if C.y_of(r)]
    alln = [p for r, p in zip(rows, probs) if not C.y_of(r)]
    out["all"] = {"n": len(rows), "acc": round(sum((p >= 0.5) == bool(C.y_of(r)) for r, p in zip(rows, probs)) / len(rows), 4),
                  "auroc": round(C.auc(allp, alln), 4) if allp and alln else None}
    pp: dict = {}
    for t in types:
        wins = [p_of[r["id"]] > p_of[r["pair_of"]] for r in rows
                if r["type"] == t and C.y_of(r) and r.get("pair_of") in p_of]
        if wins:
            pp[t] = {"n_pairs": len(wins), "contra_gt_consistent": round(sum(wins) / len(wins), 4)}
    out["pair_property"] = pp
    return out


def scene_metrics(scenes: list[dict], per: list[list[float]]) -> dict:
    mx = [C.sigmoid(max(v)) if v else 0.0 for v in per]
    sc = {s["scene_id"]: (p, s["y"]) for s, p in zip(scenes, mx)}
    pairs = {}
    for s, p in zip(scenes, mx):
        pairs.setdefault(s["pair_id"], {})[s["kind"]] = p
    d = [v["P"] - v["N"] for v in pairs.values() if "P" in v and "N" in v]
    return {"n_scenes": len(scenes), "scene_auroc": C.bootstrap_auroc(sc, 2000, 0),
            "twin_P_gt_N": round(sum(x > 0 for x in d) / len(d), 4) if d else None,
            "twin_d_mean": round(sum(d) / len(d), 4) if d else None,
            "scores": {k: round(v[0], 4) for k, v in sc.items()}}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--model", type=Path, required=True)
    ap.add_argument("--pairs", type=Path, required=True)
    ap.add_argument("--d", type=Path, required=True)
    ap.add_argument("--s", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--device", default="cuda")
    a = ap.parse_args(argv)
    if a.out.exists():
        raise SystemExit(f"덮어쓰지 않는다: {a.out}")
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(str(a.model))
    model = AutoModelForSequenceClassification.from_pretrained(str(a.model)).to(a.device)
    rows = [r for f in (a.pairs, a.d) for r in C.read_jsonl(f) if r.get("split") == "dev"]
    logits = C.predict_logits(model, tok, [C.encode_item(tok, r["claim"], C.contexts_of(r)) for r in rows], a.device)
    probs = [C.sigmoid(x) for x in logits]
    scenes = C.scenes_of([r for r in C.read_jsonl(a.s) if r.get("split") == "dev"])
    encs = [[C.encode_item(tok, it.claim, it.contexts) for it in s["items"]] for s in scenes]
    per = [C.predict_logits(model, tok, e, a.device) if e else [] for e in encs]
    rec = {"note": NOTE, "model": str(a.model), "temperature": 1.0, "versions": C.VERSIONS,
           "inputs": {str(p): C.sha256_file(p) for p in (a.pairs, a.d, a.s)},
           "rows": row_metrics(rows, probs), "S_dev": scene_metrics(scenes, per)}
    a.out.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"rows": {k: v for k, v in rec["rows"].items()},
                      "S_dev": {k: v for k, v in rec["S_dev"].items() if k != "scores"}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
