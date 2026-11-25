# -*- coding: utf-8 -*-
"""반복 추출 일치도 — WM 30장면 × 4벌(원본 + 격리 재추출 r1~r3)에서 추출 단위가 얼마나 같게 나오는가.

데이터
  원본  tests/eval/weave_checker_corpus/scene_NN_event.json  (Weave eval_corpus, 체커 게이트와 같은 파일)
  r1~r3 tests/eval/weave_judge_corpus/wm/reextract/scene_NN_event_v2_r{1,2,3}.json
        Weave Track 2(handoff/track2_scoring.md)의 격리 재추출: claude-opus 서브에이전트, 추출 프롬프트 v2_1,
        장면마다 1회 단독 호출, r1 사전 지정. 원본은 같은 조건으로 만든 것이 아니다 — Phase 4 진단대로
        원본 추출은 심어 둔 모순을 조준한다. 그래서 두 묶음을 따로 적는다:
          재추출끼리  (r1,r2) (r1,r3) (r2,r3)   — 같은 조건 반복 = 순수 반복 변동
          원본 대 재추출 (o,r1) (o,r2) (o,r3)   — 조건 차이 + 반복 변동

단위 정의 (장면 단위 집합 — 사건 분할이 벌마다 달라도 비교되게, 장면의 모든 사건을 합친 집합으로 본다)
  assumed_states  항목 {entity, field, assumes}
     슬롯   (entity, field)
     값     (entity, field, 값)
  state_changes   항목 {entity, field, from, to}
     슬롯   (entity, field)
     도착값 (entity, field, to)
     전이   (entity, field, from, to)
  정규화: 문자열의 NEW: 라벨을 WM 레지스트리(entity_registry.json)로 정식 ID 치환(코어 ledger.normalize),
          값은 앞뒤 공백 제거·연속 공백 1칸·소문자, alive 는 코어 norm_alive(true/false/unconscious).
          그 밖의 자유 서술 값(body_status·possesses 설명 등)은 **글자 그대로** 비교한다 — 뜻이 같아도 표현이
          다르면 불일치로 센다(값 일치도는 하한). 레지스트리에 없는 새 NEW: 라벨은 이름이 다르면 다른 엔티티.

지표
  쌍 일치도 J(A,B) = Σ_장면 |A_s ∩ B_s| / Σ_장면 |A_s ∪ B_s|  (장면 합산 Jaccard, 두 벌 모두 빈 장면은 기여 0)
  묶음 일치도 = 그 묶음 쌍들의 J 평균 (최소·최대 함께)
  재추출 만장일치율 = r1~r3 중 어느 하나에라도 나온 단위 가운데 셋 모두에 나온 비율
  값 유사도(문턱 없음, 서술용) = 두 벌에 모두 있는 assumed_states 슬롯에서, 한쪽의 값마다 다른 쪽 같은 슬롯 값들과의
          문자 2-gram Dice 최댓값을 구해 평균(양방향 평균). 자유 서술 값이 '표현만 다른지'를 가늠하는 값이지
          일치 판정이 아니다 — 문턱을 두지 않는다(결과를 보고 문턱을 고르지 않기 위해).
  필드별: assumed_states 슬롯·값 J 를 field 별로(재추출끼리)

실행:
  PYTHONIOENCODING=utf-8 python scripts/eval/extract_agreement.py [--json out.json]
LLM·네트워크 호출 없음.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

WM_DIR = ROOT / "tests" / "eval" / "weave_checker_corpus"
REEXTRACT_DIR = ROOT / "tests" / "eval" / "weave_judge_corpus" / "wm" / "reextract"
VERSIONS = ("o", "r1", "r2", "r3")
RR_PAIRS = (("r1", "r2"), ("r1", "r3"), ("r2", "r3"))
OR_PAIRS = (("o", "r1"), ("o", "r2"), ("o", "r3"))
UNITS = ("as_slot", "as_value", "sc_slot", "sc_to", "sc_transition")
UNIT_NAMES = {"as_slot": "assumed_states 슬롯 (entity, field)",
              "as_value": "assumed_states 값 (entity, field, 값)",
              "sc_slot": "state_changes 슬롯 (entity, field)",
              "sc_to": "state_changes 도착값 (entity, field, to)",
              "sc_transition": "state_changes 전이 (entity, field, from, to)"}


def _core():
    import app.pipeline.weave  # noqa: F401 - weave_core 경로를 연다(코어 무수정)
    from ledger import load_registry, norm_alive, normalize
    return load_registry, norm_alive, normalize


def _val(field: str, v, norm_alive) -> str:  # noqa: ANN001
    if field == "alive":
        return norm_alive(v)
    return re.sub(r"\s+", " ", str(v if v is not None else "")).strip().lower()


def scene_units(doc: dict, mapping: dict) -> dict[str, set[tuple]]:
    _, norm_alive, normalize = _core()
    doc = normalize(doc, mapping)
    out: dict[str, set[tuple]] = {u: set() for u in UNITS}
    for ev in doc.get("events", []) or []:
        for a in ev.get("assumed_states", []) or []:
            ent, fld = str(a.get("entity")), str(a.get("field"))
            out["as_slot"].add((ent, fld))
            out["as_value"].add((ent, fld, _val(fld, a.get("assumes"), norm_alive)))
        for c in ev.get("state_changes", []) or []:
            ent, fld = str(c.get("entity")), str(c.get("field"))
            out["sc_slot"].add((ent, fld))
            out["sc_to"].add((ent, fld, _val(fld, c.get("to"), norm_alive)))
            out["sc_transition"].add((ent, fld, _val(fld, c.get("from"), norm_alive),
                                      _val(fld, c.get("to"), norm_alive)))
    return out


def load_versions(wm_dir: Path = WM_DIR, re_dir: Path = REEXTRACT_DIR) -> dict[str, dict[str, dict[str, set]]]:
    """{scene_id: {version: {unit: set}}}"""
    load_registry, _, _ = _core()
    _, mapping = load_registry(wm_dir)
    data: dict[str, dict[str, dict[str, set]]] = {}
    for p in sorted(wm_dir.glob("scene_*_event.json")):
        sid = p.name.replace("_event.json", "")
        files = {"o": p} | {f"r{i}": re_dir / f"{sid}_event_v2_r{i}.json" for i in (1, 2, 3)}
        missing = [str(f) for f in files.values() if not f.exists()]
        if missing:
            raise FileNotFoundError(f"{sid}: 벌이 빠졌다 {missing}")
        data[sid] = {v: scene_units(json.loads(f.read_text(encoding="utf-8")), mapping) for v, f in files.items()}
    return data


def pair_j(data: dict, a: str, b: str, unit: str, field: str | None = None) -> tuple[float | None, int, int]:
    inter = union = 0
    for vs in data.values():
        A, B = vs[a][unit], vs[b][unit]
        if field is not None:
            A = {x for x in A if x[1] == field}
            B = {x for x in B if x[1] == field}
        inter += len(A & B)
        union += len(A | B)
    return (inter / union if union else None), inter, union


def group_summary(data: dict, pairs, unit: str, field: str | None = None) -> dict:
    js = {f"{a}-{b}": pair_j(data, a, b, unit, field) for a, b in pairs}
    vals = [j for j, _, _ in js.values() if j is not None]
    return {"mean": round(sum(vals) / len(vals), 3) if vals else None,
            "min": round(min(vals), 3) if vals else None, "max": round(max(vals), 3) if vals else None,
            "pairs": {k: {"J": round(j, 3) if j is not None else None, "inter": i, "union": u}
                      for k, (j, i, u) in js.items()}}


def _bigrams(s: str) -> set[str]:
    s = re.sub(r"\s+", "", s)
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 1 else ({s} if s else set())


def dice(a: str, b: str) -> float:
    A, B = _bigrams(a), _bigrams(b)
    return 2 * len(A & B) / (len(A) + len(B)) if A or B else 1.0


def value_similarity(data: dict, pairs, field: str | None = None) -> float | None:
    """같은 슬롯 안 값 문자 2-gram Dice 의 최대 쌍 평균(양방향). 슬롯이 겹치지 않으면 기여 없음."""
    scores: list[float] = []
    for a, b in pairs:
        for vs in data.values():
            for x, y in ((a, b), (b, a)):
                by_slot: dict[tuple, list[str]] = defaultdict(list)
                for ent, fld, val in vs[y]["as_value"]:
                    by_slot[(ent, fld)].append(val)
                for ent, fld, val in vs[x]["as_value"]:
                    if (field is None or fld == field) and (ent, fld) in by_slot:
                        scores.append(max(dice(val, o) for o in by_slot[(ent, fld)]))
    return round(sum(scores) / len(scores), 3) if scores else None


def unanimity(data: dict, unit: str) -> dict:
    any_ = all3 = two = 0
    for vs in data.values():
        cnt: dict[tuple, int] = defaultdict(int)
        for v in ("r1", "r2", "r3"):
            for x in vs[v][unit]:
                cnt[x] += 1
        any_ += len(cnt)
        all3 += sum(1 for c in cnt.values() if c == 3)
        two += sum(1 for c in cnt.values() if c >= 2)
    return {"units_any": any_, "in_all_3": all3, "in_2_or_more": two,
            "unanimity": round(all3 / any_, 3) if any_ else None,
            "majority_share": round(two / any_, 3) if any_ else None}


def run(wm_dir: Path = WM_DIR, re_dir: Path = REEXTRACT_DIR) -> dict:
    data = load_versions(wm_dir, re_dir)
    res: dict = {"scenes": len(data), "versions": list(VERSIONS),
                 "counts": {v: {u: sum(len(vs[v][u]) for vs in data.values()) for u in UNITS} for v in VERSIONS},
                 "units": {},
                 "value_similarity": {"reextract_vs_reextract": value_similarity(data, RR_PAIRS),
                                      "original_vs_reextract": value_similarity(data, OR_PAIRS)}}
    for u in UNITS:
        res["units"][u] = {"reextract_vs_reextract": group_summary(data, RR_PAIRS, u),
                           "original_vs_reextract": group_summary(data, OR_PAIRS, u),
                           "reextract_unanimity": unanimity(data, u)}
    fields = sorted({x[1] for vs in data.values() for v in VERSIONS for x in vs[v]["as_slot"]})
    res["assumed_by_field"] = {}
    for f in fields:
        n = {v: sum(1 for vs in data.values() for x in vs[v]["as_value"] if x[1] == f) for v in VERSIONS}
        res["assumed_by_field"][f] = {
            "counts": n,
            "slot_rr": group_summary(data, RR_PAIRS, "as_slot", f)["mean"],
            "value_rr": group_summary(data, RR_PAIRS, "as_value", f)["mean"],
            "value_or": group_summary(data, OR_PAIRS, "as_value", f)["mean"],
            "value_sim_rr": value_similarity(data, RR_PAIRS, f)}
    return res


def _f(x: float | None) -> str:
    return "-" if x is None else f"{x:.3f}"


def format_markdown(res: dict) -> str:
    L = [f"WM {res['scenes']}장면 × 4벌 (o=원본, r1~r3=격리 재추출)", "",
         "| 단위 | 재추출끼리 J 평균 [최소–최대] | 원본 대 재추출 J 평균 [최소–최대] | 재추출 만장일치율 (3벌 모두 / 1벌 이상) |",
         "|---|---|---|---|"]
    for u in UNITS:
        rr, orr, un = (res["units"][u][k] for k in ("reextract_vs_reextract", "original_vs_reextract",
                                                      "reextract_unanimity"))
        L.append(f"| {UNIT_NAMES[u]} | {_f(rr['mean'])} [{_f(rr['min'])}–{_f(rr['max'])}] | "
                 f"{_f(orr['mean'])} [{_f(orr['min'])}–{_f(orr['max'])}] | "
                 f"{_f(un['unanimity'])} ({un['in_all_3']}/{un['units_any']}) |")
    vs = res["value_similarity"]
    L += ["", f"assumed_states 값 유사도(같은 슬롯 안 문자 2-gram Dice 최대 쌍 평균, 문턱 없음): "
              f"재추출끼리 {_f(vs['reextract_vs_reextract'])} · 원본 대 재추출 {_f(vs['original_vs_reextract'])}"]
    L += ["", "벌별 단위 수 (30장면 합)", "", "| 벌 | " + " | ".join(UNITS) + " |",
          "|---|" + "---|" * len(UNITS)]
    for v in VERSIONS:
        L.append(f"| {v} | " + " | ".join(str(res["counts"][v][u]) for u in UNITS) + " |")
    L += ["", "assumed_states 필드별 (재추출끼리 슬롯·값 J, 원본 대 재추출 값 J)", "",
          "| field | o / r1 / r2 / r3 항목 수 | 슬롯 J (rr) | 값 J (rr) | 값 J (o-r) | 값 유사도 (rr) |",
          "|---|---|---|---|---|---|"]
    for f, d in res["assumed_by_field"].items():
        c = d["counts"]
        L.append(f"| {f} | {c['o']} / {c['r1']} / {c['r2']} / {c['r3']} | {_f(d['slot_rr'])} | "
                 f"{_f(d['value_rr'])} | {_f(d['value_or'])} | {_f(d['value_sim_rr'])} |")
    return "\n".join(L)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="WM 반복 추출 일치도")
    ap.add_argument("--json", type=Path, default=None)
    a = ap.parse_args(argv)
    res = run()
    print(format_markdown(res))
    if a.json:
        a.json.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
