"""[탐색: PD-eval 재사용, 확증 아님] 지시문 V2-X 의 X1 — 걸러내지 않은 전체 데이터로 1단계 학습 행을 만든다.

main 42697ac §10 "탐색 실행"(결과를 보기 전 확정):
 - A 818 · B 679 쌍: pd_train.jsonl 의 A·B 전부. 판정 걸러내기·A− 기계 걸러내기를 하지 않는다.
 - C_v2 96 쌍(gen/C_v2.jsonl) 전부(판정 걸러내기 없음). 옛 C(v1) 234쌍은 쓰지 않는다.
 - 맥락 채우기 K=4 는 build_v2.build 와 같은 코드(같은 시드·같은 ban 규칙).
 - D 는 층화본(gen/D.jsonl)을 학습 때 따로 넣는다. 혼합 점검은 재서 기록만 하고 0.70 을 넘어도 멈추지 않는다.
 - S 179 쌍(pd_train_S.jsonl)은 2단계에 그대로 쓴다(여기서는 수만 확인).
 - split 은 기존 작품 단위 그대로.

    python scripts/v2/build_x1.py --out C:\\judge_models\\V2\\train\\explore\\pd_train_x1.jsonl
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import v2_common as C
from build_v2 import SOURCE, build, leakage, pair_property
from build_v3 import mix_v3, pair_counts

TAG = "[탐색: PD-eval 재사용, 확증 아님]"
TRAIN = Path(r"C:\judge_models\V2\train")
EXPECT = {"A": 818, "B": 679, "C": 96}
EXPECT_S = 179


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--pd", type=Path, default=TRAIN / "pd_train.jsonl")
    ap.add_argument("--c-v2", type=Path, default=TRAIN / "gen" / "C_v2.jsonl")
    ap.add_argument("--d", type=Path, default=TRAIN / "gen" / "D.jsonl")
    ap.add_argument("--s", type=Path, default=TRAIN / "pd_train_S.jsonl")
    ap.add_argument("--split", type=Path, default=TRAIN / "split.json")
    ap.add_argument("--source", type=Path, default=SOURCE)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    meta_p = a.out.with_suffix(".meta.json")
    for p in (a.out, meta_p):
        if p.exists():
            raise SystemExit(f"덮어쓰지 않는다: {p}")
    pd_rows = [r for r in C.read_jsonl(a.pd) if r["type"] in ("A", "B")]    # 옛 C(v1)는 넣지 않는다
    rows, bmeta = build(pd_rows, a.source, None, C.read_jsonl(a.c_v2), None, mech_filter=False)
    pc = pair_counts(rows)
    got = {t: sum(v.values()) for t, v in pc.items()}
    if got != EXPECT:
        raise SystemExit(f"쌍 수가 기대와 다르다: {got} ≠ {EXPECT}")
    s_rows = C.read_jsonl(a.s)
    if len(s_rows) != EXPECT_S:
        raise SystemExit(f"S 수가 기대와 다르다: {len(s_rows)} ≠ {EXPECT_S}")
    d_all = C.read_jsonl(a.d)
    mix = mix_v3(rows, [r for r in d_all if r.get("split") != "dev"])
    meta = {"tag": TAG, "kind": "pd_train_x1", "rule": "main 42697ac §10 탐색 실행 X1 — 걸러내지 않은 전체",
            "inputs": {str(p): C.sha256_file(p) for p in (a.pd, a.c_v2, a.d, a.s, a.split)},
            "build": bmeta, "pairs": pc, "S": {"pairs": dict(Counter(r.get("split") for r in s_rows)), "file": str(a.s)},
            "D": {"path": str(a.d), "rows": dict(Counter(r.get("split") for r in d_all))},
            "mix_check": mix | {"note": "기록만 — 0.70 을 넘어도 멈추지 않는다(V2-X)"},
            "pair_property": pair_property(rows), "leakage": leakage(rows + s_rows, a.split)}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    C.write_jsonl(a.out, rows)
    meta["outputs"] = {str(a.out): C.sha256_file(a.out)}
    meta_p.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"tag": TAG, "pairs": pc, "S": meta["S"]["pairs"], "mix_worst": mix["worst"],
                      "mix_worst_incl_within": mix["worst_incl_within_type"],
                      "pair_property": {k: v for k, v in meta["pair_property"].items() if k != "examples"},
                      "leakage": meta["leakage"], "drop_reasons": bmeta["drop_reasons"],
                      "contexts_after_fill": bmeta["contexts_after_fill"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
