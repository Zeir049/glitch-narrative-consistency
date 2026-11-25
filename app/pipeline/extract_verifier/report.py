"""검증기 진입점과 출력 — extract_gate.GateResult.to_dict() 와 같은 모양 + 새 신호.

출력 모양(GateResult 호환):
    events[사건 ID] = {grade, own_reasons, own_marks, state_changes[{index, entity, field, grade, reasons, marks}]}
  · `reasons`·`own_reasons` — 등급을 내리는 신호 자리. 1단계 신호는 **하나도 등급을 내리지 않으므로 항상 빈 목록**이고
    grade 는 항상 confirmed 다. extract_gate.filter_event 는 own_reasons 가 있으면 사건을 통째로 빼므로, 표시 신호를
    거기 넣으면 WEAVE_GATE_MODE=exclude 에서 사실이 사라진다. 그래서 표시 신호는 같은 자리 옆 `marks`·`own_marks` 에 둔다.
  · `episode_marks` — 사건에 묶이지 않는 표시(규칙의 number_lost, entity_uncovered, declaration_only_persistent).
  · `promotion_candidates` — assumed_only_persistent·declaration_only_persistent 에서 나온 승격 후보.
    자리 표시는 사건 항목이면 `index`(+collection), 선언이면 `declaration: {collection, id, part, index}`.
  · 승격 후보마다 `priority`(high|mid|low, verifier/rank.py — 사전 확정 순위 규칙).
  · `experimental_candidates` — assumed_only_exclusive(실험, 등급·적중 지표 밖).

신호 효과표 SIGNALS: effect="mark"(표시만) | "grade"(등급을 내림). 1단계는 전부 mark.

결정성: 입력을 바꾸지 않는다(deepcopy 후 정규화), 시각·난수 없음, 순서는 문서 순서·정렬 키 고정,
to_json 은 sort_keys + ensure_ascii=False + indent=1 → 같은 입력이면 같은 바이트.
"""
from __future__ import annotations

import copy
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from app.pipeline.extract_verifier import VERIFIER_VERSION
from app.pipeline.extract_verifier import items as I
from app.pipeline.extract_verifier.entities import entity_marks
from app.pipeline.extract_verifier.numbers import number_marks
from app.pipeline.extract_verifier.preserve import assumed_marks, declaration_marks
from app.pipeline.extract_verifier.rank import priority

CORE_DIR = Path(__file__).resolve().parents[1] / "weave_core"  # extract_gate.py 와 같은 방식(코어는 호출만)
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from ledger import compile_ledger  # noqa: E402
from ledger import normalize as w_normalize  # noqa: E402

CONFIRMED = "confirmed"
SIGNALS = {
    "assumed_only_persistent": {"effect": "mark", "experimental": False, "candidate": "promotion"},
    "declaration_only_persistent": {"effect": "mark", "experimental": False, "candidate": "promotion"},
    "number_lost": {"effect": "mark", "experimental": False, "candidate": None},
    "entity_uncovered": {"effect": "mark", "experimental": False, "candidate": None},
    "assumed_only_exclusive": {"effect": "mark", "experimental": True, "candidate": "experimental"},
}
_CAND_KEYS = ("event_id", "collection", "index", "declaration", "entity", "field", "value", "numbers", "words",
              "source_text", "signal", "why", "cue")


@dataclass
class VerifyResult:
    episode: int | None
    config: dict
    events: dict[str, dict] = field(default_factory=dict)
    episode_marks: list[dict] = field(default_factory=list)
    promotion_candidates: list[dict] = field(default_factory=list)
    experimental_candidates: list[dict] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        c = {k: 0 for k in SIGNALS}
        for e in self.events.values():
            for m in e["own_marks"]:
                c[m["signal"]] += 1
            for s in e["state_changes"]:
                for m in s["marks"]:
                    c[m["signal"]] += 1
        for m in self.episode_marks:
            c[m["signal"]] += 1
        return c

    def to_dict(self) -> dict:
        return {
            "verifier_version": VERIFIER_VERSION,
            "episode": self.episode,
            "config": self.config,
            "signals": SIGNALS,
            "summary": {"events": len(self.events), "tentative_events": 0,
                        "state_changes": sum(len(v["state_changes"]) for v in self.events.values()),
                        "tentative_state_changes": 0, "marks": self.counts(),
                        "promotion_candidates": len(self.promotion_candidates),
                        "experimental_candidates": len(self.experimental_candidates)},
            "tentative_event_ids": [],
            "events": self.events,
            "episode_marks": self.episode_marks,
            "promotion_candidates": self.promotion_candidates,
            "experimental_candidates": self.experimental_candidates,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, indent=1)


def _cand(m: dict) -> dict:
    return {k: m[k] for k in _CAND_KEYS if k in m}


def verify_episode(doc: dict, confirmed_eps: list[dict], mapping: dict, *, registry: dict | None = None,
                   text: str | None = None, knows_mode: str = "accumulate", number_basis: str = "quote",
                   timelines: list[str] | None = None) -> VerifyResult:
    """확정 회차(정규화된 화 목록) + 새 회차 doc(+ 그 회차 원문·레지스트리)를 검사한다. extract_gate.grade_episode 와 같은 인자 순서."""
    tls = timelines or ["main"]
    ledger = compile_ledger(episodes=copy.deepcopy(confirmed_eps), timelines=tls, knows_mode=knows_mode)
    new = w_normalize(copy.deepcopy(doc), mapping)
    tl = tls[0]
    am = assumed_marks(new, ledger, tl)
    nm = number_marks(new, text, basis=number_basis)
    dm = declaration_marks(new)
    em = entity_marks(new, text, registry or {})

    out: dict[str, dict] = {}
    for ev in I.events(new):
        eid = ev.get("event_id")
        if not eid or eid in out:
            continue
        out[eid] = {"grade": CONFIRMED, "own_reasons": [], "own_marks": [],
                    "state_changes": [{"index": i, "entity": sc.get("entity"), "field": sc.get("field"),
                                       "grade": CONFIRMED, "reasons": [], "marks": []}
                                      for i, sc in enumerate(ev.get("state_changes") or []) if isinstance(sc, dict)]}
    ep_marks = []
    for m in am + nm:
        e = out.get(m.get("event_id") or "")
        if e is None:
            ep_marks.append(m)
        elif m["collection"] == "state_changes":
            sc = next((s for s in e["state_changes"] if s["index"] == m["index"]), None)
            (sc["marks"] if sc else e["own_marks"]).append(m)
        else:
            e["own_marks"].append(m)
    ep_marks += dm + em
    promo = [_cand(m) | {"priority": priority(m)} for m in am + dm if SIGNALS[m["signal"]]["candidate"] == "promotion"]
    exper = [_cand(m) for m in am if SIGNALS[m["signal"]]["candidate"] == "experimental"]
    return VerifyResult(episode=new.get("episode"),
                        config={"knows_mode": knows_mode, "number_basis": number_basis, "timelines": tls, "has_text": bool(text),
                                "has_registry": registry is not None},
                        events=out, episode_marks=ep_marks, promotion_candidates=promo,
                        experimental_candidates=exper)


def format_table(res: VerifyResult) -> str:
    """사람이 읽는 표(마크다운)."""
    rows = ["| 신호 | 사건 | 칸 | 엔티티.필드 | 수치·어휘 | 값 |", "|---|---|---|---|---|---|"]

    def row(m: dict) -> str:
        where = m.get("collection") or (m.get("declaration") or {}).get("collection") or ""
        idx = m.get("index") if m.get("index") is not None else (m.get("declaration") or {}).get("index")
        nums = ", ".join((m.get("numbers") or []) + (m.get("words") or []) + ([m["cue"]] if m.get("cue") else [])
                         + (m.get("names") or []))
        val = str(m.get("value") or "").replace("|", "/")[:60]
        return (f"| {m['signal']} | {m.get('event_id') or '—'} | {where}{'' if idx is None else '#' + str(idx)} "
                f"| {m.get('entity')}.{m.get('field') or ''} | {nums} | {val} |")

    for eid, e in res.events.items():
        for m in e["own_marks"]:
            rows.append(row(m))
        for s in e["state_changes"]:
            for m in s["marks"]:
                rows.append(row(m))
    for m in res.episode_marks:
        rows.append(row(m))
    return "\n".join(rows)
