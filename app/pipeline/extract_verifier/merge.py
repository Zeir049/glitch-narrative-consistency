"""등급 파일(extract_gate.GateResult.to_dict())에 검증기 신호를 합친다 — 병합 제안서 안 B(marks)만.

입력을 바꾸지 않고 새 dict 를 돌려준다. grade·own_reasons·state_changes[].reasons·tentative_event_ids·summary 는
**그대로** 둔다 — 검증기 신호는 전부 표시(effect=mark)라서 옆 키 own_marks / state_changes[].marks 에 들어간다.
그래서 고치지 않은 extract_gate.filter_event(own_reasons 가 있으면 사건을 통째로 뺌)·grade_of 와 함께 써도 결과가 같다.
(신호를 own_reasons 에 넣는 안 A 는 채택되지 않았다 — 비교 근거는 glitch_extract_verifier 저장소에 남아 있다.)

등급 파일에 더하는 키: verifier(version·signals·summary) · episode_marks · promotion_candidates · experimental_candidates.
"""
from __future__ import annotations

import copy

from app.pipeline.extract_verifier.report import SIGNALS, VerifyResult

SCREEN_PRIORITY = "high"   # 검수 화면에 펼치는 승격 후보 순위(판단 세션 결정: high 목록 · mid 건수 · low 숨김)


def _tag(m: dict) -> dict:
    return {"effect": SIGNALS[m["signal"]]["effect"]} | m


def merge_verifier(gate: dict, vres: VerifyResult) -> dict:
    out = copy.deepcopy(gate)
    v = vres.to_dict()
    for eid, ve in v["events"].items():
        ge = out.get("events", {}).get(eid)
        if ge is None:
            continue
        ge.setdefault("own_marks", [])
        ge["own_marks"] += [_tag(m) for m in ve["own_marks"]]
        by_idx = {s["index"]: s for s in ge.get("state_changes") or []}
        for s in ve["state_changes"]:
            gs = by_idx.get(s["index"])
            if gs is not None and s["marks"]:
                gs.setdefault("marks", [])
                gs["marks"] += [_tag(m) for m in s["marks"]]
    out["verifier"] = {"version": v["verifier_version"], "style": "marks", "signals": v["signals"],
                       "summary": {"marks": v["summary"]["marks"],
                                   "promotion_candidates": v["summary"]["promotion_candidates"],
                                   "experimental_candidates": v["summary"]["experimental_candidates"]}}
    out["episode_marks"] = [_tag(m) for m in v["episode_marks"]]
    out["promotion_candidates"] = v["promotion_candidates"]
    out["experimental_candidates"] = v["experimental_candidates"]
    return out


def screen_bundle(gate: dict | None) -> dict | None:
    """review_NN.json gate 절에 싣는 검증기 묶음. 검증기를 안 돌린 등급 파일이면 None.

    high 승격 후보는 목록으로, mid 는 건수만, low 는 넣지 않는다(건수도 없음)."""
    if not gate or "verifier" not in gate:
        return None
    cands = gate.get("promotion_candidates") or []
    return {"version": gate["verifier"].get("version"),
            "promotion_candidates_high": [c for c in cands if c.get("priority") == SCREEN_PRIORITY],
            "promotion_candidates_mid_count": sum(1 for c in cands if c.get("priority") == "mid")}
