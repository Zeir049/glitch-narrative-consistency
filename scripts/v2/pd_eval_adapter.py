"""PD-eval pairs.json → score_v2 입력 jsonl — **이름만 바꾸는 어댑터**(지시문 V2-F 측정 1). 내용은 바꾸지 않는다.

    id       ← pair_id
    target_P ← p_window
    target_N ← n_window
    prefix, work, type 은 그대로. 나머지 필드(orig·p_new·n_new·evidence·note·verdicts 등)는 채점에 쓰지 않아 옮기지 않는다.

입력·출력·어댑터 자신의 sha256 을 <out>.meta.json 에 남긴다. 덮어쓰지 않는다.

    python scripts/v2/pd_eval_adapter.py --pairs C:\\judge_models\\V2\\eval\\pd_eval\\pairs.json --out C:\\judge_models\\V2\\eval\\final\\pd_eval_adapted.jsonl
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import v2_common as C

RENAME = {"pair_id": "id", "p_window": "target_P", "n_window": "target_N"}
KEEP = ("prefix", "work", "type")


def adapt(pairs: list[dict]) -> list[dict]:
    need = (*RENAME, *KEEP)
    bad = [p.get("pair_id") for p in pairs if not all(isinstance(p.get(k), str) and p.get(k) for k in need)]
    if bad:
        raise SystemExit(f"필드가 없다({need}): {bad[:5]}")
    ids = [p["pair_id"] for p in pairs]
    if len(set(ids)) != len(ids):
        raise SystemExit("pair_id 가 겹친다")
    return [{RENAME[k]: p[k] for k in RENAME} | {k: p[k] for k in KEEP} for p in pairs]


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--pairs", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    meta_p = a.out.with_suffix(".meta.json")
    for p in (a.out, meta_p):
        if p.exists():
            raise SystemExit(f"덮어쓰지 않는다: {p}")
    pairs = json.loads(a.pairs.read_text(encoding="utf-8"))
    rows = adapt(pairs)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    C.write_jsonl(a.out, rows)
    meta = {"kind": "pd_eval_adapter", "rule": "이름만 바꿈 — 내용 그대로", "rename": RENAME, "keep": list(KEEP),
            "n": len(rows), "input": {str(a.pairs): C.sha256_file(a.pairs)},
            "adapter": {str(Path(__file__).resolve()): C.sha256_file(Path(__file__).resolve())},
            "output": {str(a.out): C.sha256_file(a.out)}}
    meta_p.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False))


if __name__ == "__main__":
    main()
