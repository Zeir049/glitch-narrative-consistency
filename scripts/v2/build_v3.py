"""PD-train 조립 v3 (main 451bab2 설계 문서 §10 "고정 레시피") — pd_train_v2.jsonl + consensus_keep.json → pd_train_v3.

규칙(고정, 결과를 보고 바꾸지 않는다):
 - B: pd_train_v2 의 B 전부(1·2차 걸러내기 충돌 222쌍) 그대로.
 - A: consensus_keep.json 의 A 43쌍 — pd_train_v2 의 해당 두 행(맥락 채우기 뒤) 그대로.
 - C: 뺀다.
 - D: 주어진 D(층화본)를 그대로 쓴다. 혼합 점검(All/A/B + D, 특징 5개)을 다시 재 0.60 을 넘으면 멈춘다
   (그때는 make_d.py --match pd_train_v3.jsonl 로 같은 방식(주장 길이 × 겹침 분위) 재표본 뒤 --d 를 바꿔 다시 돌린다).
 - S: consensus_keep.json 의 S 50쌍(train 43 / dev 7) → 2단계 전용 파일.
 - 매니페스트(meta)에 입력 SHA256 과 유형·split 수. 기대 수와 다르면 멈춘다(파일을 쓰지 않는다).
 - 세 번째 감사 분기(§10, 미리 정함): `--exclude A` 면 A 를 빼고(B+D) 같은 규칙으로 조립·혼합 점검한다.

    python scripts/v2/build_v3.py --out C:\\judge_models\\V2\\train\\pd_train_v3.jsonl
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import v2_common as C
from build_v2 import GATE_STOP, GATE_TARGET, leakage, mix_check, pair_property

BUILD_V3_VERSION = "1"
TRAIN = Path(r"C:\judge_models\V2\train")
# 기대 수(쌍, train/dev) — 지시문 V2-K5
EXPECT_PAIRS = {"A": {"train": 41, "dev": 2}, "B": {"train": 183, "dev": 39}}
EXPECT_S = {"train": 43, "dev": 7}
MIX_SETS = ("all+D", "A+D", "B+D")   # D 재표본 판단에 쓰는 집합(All/A/B + D)


def select(v2_rows: list[dict], keep: dict, exclude: tuple[str, ...] = ()) -> tuple[list[dict], dict]:
    """B 전부 + consensus A 쌍(두 행), C 제외. 쌍 단위로 고른다. exclude 에 든 유형은 뺀다(감사 분기)."""
    a_keep = {p["id"]: p for p in keep["pairs"]["A"]}
    a_ids = set(a_keep) | {p["pair_of"] for p in a_keep.values()}
    by = {r["id"]: r for r in v2_rows}
    missing = sorted(x for x in a_ids if x not in by)
    if missing:
        raise SystemExit(f"consensus A 의 행이 pd_train_v2 에 없다: {missing[:5]}")
    for i, p in a_keep.items():
        r = by[i]
        if C.y_of(r) != 1 or r.get("pair_of") != p["pair_of"] or r.get("split") != p["split"]:
            raise SystemExit(f"consensus A 행이 v2 와 다르다(라벨·짝·split): {i}")
    out = [r for r in v2_rows if r["type"] not in exclude
           and (r["type"] == "B" or (r["type"] == "A" and r["id"] in a_ids))]
    stat = {"v2_rows": len(v2_rows), "dropped": dict(Counter(r["type"] for r in v2_rows if r not in out))}
    return out, stat


def pair_counts(rows: list[dict]) -> dict[str, dict[str, int]]:
    out: dict[str, Counter] = {}
    for r in rows:
        if C.y_of(r) == 1:
            out.setdefault(r["type"], Counter())[r["split"]] += 1
    return {t: dict(v) for t, v in sorted(out.items())}


def mix_v3(rows: list[dict], d_train: list[dict]) -> dict:
    sets = {"all+D": rows + d_train}
    for t in sorted({r["type"] for r in rows}):
        sub = [r for r in rows if r["type"] == t]
        sets[t] = sub
        sets[f"{t}+D"] = sub + d_train
    rep = {k: mix_check(v) for k, v in sets.items()}

    def worst_of(keys: tuple[str, ...] | list[str]) -> dict:
        cand = [(rep[k]["worst"], k, max(rep[k]["features"], key=lambda f: rep[k]["features"][f]["dir_free"] or 0))
                for k in keys if k in rep and rep[k].get("features")]
        v, s, f = max(cand) if cand else (0.0, None, None)
        return {"value": v, "set": s, "feature": f}
    w = worst_of(MIX_SETS)
    return {"sets": rep, "decision_sets": list(MIX_SETS), "worst": w, "worst_incl_within_type": worst_of(list(rep)),
            "target_met": w["value"] <= GATE_TARGET, "gate": "fail" if w["value"] > GATE_STOP else "pass",
            "resample_D": w["value"] > GATE_TARGET}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--v2", type=Path, default=TRAIN / "pd_train_v2.jsonl")
    ap.add_argument("--keep", type=Path, default=TRAIN / "consensus_keep.json")
    ap.add_argument("--d", type=Path, default=TRAIN / "gen" / "D.jsonl")
    ap.add_argument("--s", type=Path, default=TRAIN / "pd_train_S.jsonl")
    ap.add_argument("--split", type=Path, default=TRAIN / "split.json")
    ap.add_argument("--out", type=Path, required=True, help="A+B 쌍 행 jsonl (S 는 <out stem>_S.jsonl)")
    ap.add_argument("--exclude", nargs="*", default=[], choices=("A",), help="세 번째 감사 분기: 뺄 유형")
    a = ap.parse_args(argv)
    exclude = tuple(a.exclude)
    expect = {t: v for t, v in EXPECT_PAIRS.items() if t not in exclude}
    s_out = a.out.with_name(a.out.stem + "_S.jsonl")
    meta_p = a.out.with_suffix(".meta.json")
    for p in (a.out, s_out, meta_p):
        if p.exists():
            raise SystemExit(f"덮어쓰지 않는다: {p}")
    keep = json.loads(a.keep.read_text(encoding="utf-8"))
    rows, stat = select(C.read_jsonl(a.v2), keep, exclude)
    pc = pair_counts(rows)
    if pc != expect:
        raise SystemExit(f"쌍 수가 기대와 다르다: {pc} ≠ {expect}")
    s_keep = {p["id"]: p["split"] for p in keep["pairs"]["S"]}
    s_rows = [r for r in C.read_jsonl(a.s) if r["id"] in s_keep]
    if len(s_rows) != len(s_keep) or any(s_keep[r["id"]] != r["split"] for r in s_rows):
        raise SystemExit("consensus S 가 pd_train_S 와 맞지 않다(누락·split)")
    s_split = dict(Counter(r["split"] for r in s_rows))
    if s_split != EXPECT_S:
        raise SystemExit(f"S 수가 기대와 다르다: {s_split} ≠ {EXPECT_S}")
    d_all = C.read_jsonl(a.d)
    d_train = [r for r in d_all if r.get("split") != "dev"]
    mix = mix_v3(rows, d_train)
    pp = pair_property(rows)
    if pp["n_violations"]:
        raise SystemExit(f"쌍 성질 위반: {pp}")
    meta = {"kind": "pd_train_v3", "version": BUILD_V3_VERSION, "recipe": "main 451bab2 §10 고정 레시피",
            "exclude": list(exclude), "branch": "세 번째 감사 A 미달 → A 제외" if "A" in exclude else None,
            "inputs": {str(p): C.sha256_file(p) for p in (a.v2, a.keep, a.d, a.s, a.split)},
            "select": stat, "pairs": pc, "rows": dict(Counter(f"{r['type']}:{r['split']}:{C.y_of(r)}" for r in rows)),
            "D": {"path": str(a.d), "rows": dict(Counter(r.get("split") for r in d_all))},
            "S": {"pairs": s_split, "file": str(s_out)},
            "mix_check": mix, "pair_property": pp, "leakage": leakage(rows + s_rows, a.split)}
    if mix["resample_D"]:
        meta_p.with_name(meta_p.stem + ".STOPPED.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        raise SystemExit(f"혼합 점검 {mix['worst']} > {GATE_TARGET}: D 를 같은 방식으로 다시 뽑는다(파일 안 씀)")
    C.write_jsonl(a.out, rows)
    C.write_jsonl(s_out, s_rows)
    meta["outputs"] = {str(a.out): C.sha256_file(a.out), str(s_out): C.sha256_file(s_out)}
    meta_p.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: meta[k] for k in ("pairs", "S", "D", "leakage")} | {"mix_worst": mix["worst"],
                      "mix_worst_incl_within": mix["worst_incl_within_type"], "outputs": meta["outputs"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
