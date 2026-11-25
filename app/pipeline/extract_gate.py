"""① 추출 관문 — 새 회차 추출 결과의 사건·상태 변화를 '확정(confirmed)'과 '잠정(tentative)'으로 가른다.

결정적 규칙만 쓴다(LLM 없음). 확정 회차를 원장으로 재생한 뒤 새 회차 사건을 한 건씩 이어 재생하면서
다음 신호가 붙은 항목을 잠정으로 본다.

  validator_hard / validator_soft  state_change 의 from 이 원장 현재값과 다르다 (코어 Validator)
  schema_error                     엔티티 종류에 허용되지 않는 필드 (코어 Validator)
  unresolved_label                 레지스트리에 매핑되지 않은 NEW: 라벨이 남아 있다
  unknown_from                     원장에 이미 값이 있는데 from="unknown" — Validator 가 대조를 건너뛴다
                                   (alive·location·body_status·ability·condition 만. possesses·knows 는
                                   from 이 의미 없는 필드라 제외. WEAVE_GATE_UNKNOWN_FROM 으로 끈다)

거르지 못하는 것(결정 기록 2장): 사건 누락, from 을 맞게 적은 환각, 다른 엔티티로의 오매핑.

등급은 두 층이다.
  · state_change 단위  — 그 state_change 에 직접 붙은 신호
  · 사건 단위          — own_reasons(사건 본문 — 인물·장소·시간·지식 칸 — 의 신호) 와
                         grade(own_reasons 또는 잠정 state_change 가 하나라도 있으면 tentative)
exclude 는 own_reasons 가 있는 사건을 통째로, 그 밖의 사건에서는 잠정 state_change 만 뺀다
(filter_episode). 표시(attrs.trust)와 annotate 의 잠정 사건 집합은 grade 를 쓴다.

이 모듈은 코어(weave_core)를 호출만 한다 — 코어는 고치지 않는다.
"""
from __future__ import annotations

import copy
import sys
from dataclasses import dataclass, field
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent / "weave_core"
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from ledger import apply_event, compile_ledger, iter_events  # noqa: E402
from ledger import normalize as w_normalize  # noqa: E402
from validator import Validator  # noqa: E402

GATE_VERSION = 1
CONFIRMED = "confirmed"
TENTATIVE = "tentative"
GATE_MODES = ("off", "annotate", "exclude")

# Validator 경고 중 등급을 내리는 수준. knows_op_coerced·knows_value_missing 은 기록만 한다.
GRADING_LEVELS = {"hard": "validator_hard", "soft": "validator_soft", "schema_error": "schema_error"}
# from="unknown" 을 신호로 보는 필드 (possesses 는 집합, knows 는 누적이라 from 이 의미 없다)
UNKNOWN_FROM_FIELDS = {"alive", "location", "body_status", "ability", "condition"}


def _labels(obj) -> list[str]:  # noqa: ANN001
    """값 안에 남은 미해결 NEW: 라벨(문자열이 NEW: 로 시작). 정렬·중복 없음."""
    out: set[str] = set()

    def walk(v) -> None:  # noqa: ANN001
        if isinstance(v, str):
            if v.startswith("NEW:"):
                out.add(v)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)

    walk(obj)
    return sorted(out)


@dataclass
class GateResult:
    """관문 결과. events 는 사건 ID → 등급 정보, warnings 는 새 회차 사건에 붙은 Validator 경고 전부."""

    episode: int | None
    config: dict
    events: dict[str, dict] = field(default_factory=dict)
    warnings: list[dict] = field(default_factory=list)

    def grade_of(self, event_id: str | None) -> str | None:
        e = self.events.get(event_id or "")
        return e["grade"] if e else None

    @property
    def tentative_event_ids(self) -> list[str]:
        return sorted(k for k, v in self.events.items() if v["grade"] == TENTATIVE)

    def to_dict(self) -> dict:
        n_sc = sum(len(v["state_changes"]) for v in self.events.values())
        n_sc_t = sum(1 for v in self.events.values() for s in v["state_changes"]
                     if s["grade"] == TENTATIVE)
        return {
            "gate_version": GATE_VERSION,
            "episode": self.episode,
            "config": self.config,
            "summary": {
                "events": len(self.events), "tentative_events": len(self.tentative_event_ids),
                "state_changes": n_sc, "tentative_state_changes": n_sc_t,
            },
            "tentative_event_ids": self.tentative_event_ids,
            "events": self.events,
            "warnings": self.warnings,
        }


def grade_episode(doc: dict, confirmed_eps: list[dict], mapping: dict, *,
                  knows_mode: str, unknown_from: bool = True,
                  timelines: list[str] | None = None) -> GateResult:
    """확정 회차(confirmed_eps, 정규화된 화 목록) + 새 회차 doc 를 재생해 새 회차 사건에 등급을 매긴다.

    재생은 weave_pipeline.replay_confirmed(confirmed_eps + [doc]) 와 같다 — 그 함수 본문 그대로
    (Validator + compile_ledger)로 확정분을 재생한 뒤 새 회차 사건을 같은 Validator 로 이어 붙인다
    (compile_ledger 와 같은 순서). 사건마다 from="unknown" 신호를 보려면 그 사건 직전 원장이 필요해서
    마지막 회차만 직접 돈다. replay_confirmed 를 부르지 않는 것은 검수 경로의 재생 호출과 섞이지 않게
    하려는 것뿐이다.
    입력은 바꾸지 않는다. 같은 입력이면 같은 결과(시각·난수 없음).
    """
    tls = timelines or ["main"]
    validator = Validator()
    ledger = compile_ledger(episodes=confirmed_eps, validator=validator, timelines=tls,
                            knows_mode=knows_mode)
    new = w_normalize(copy.deepcopy(doc), mapping)
    start = len(validator.warnings)
    unknown_hits: dict[str, set[int]] = {}
    events = [e for e in iter_events(new) if isinstance(e, dict)]
    for ev in events:
        timeline = ev.get("timeline") or "unknown"
        if unknown_from:
            for i, sc in enumerate(ev.get("state_changes") or []):
                if (sc.get("field") in UNKNOWN_FROM_FIELDS
                        and str(sc.get("from", "")).strip().lower() == "unknown"
                        and ledger.get(timeline, sc.get("entity"), sc.get("field")) is not None):
                    unknown_hits.setdefault(ev.get("event_id"), set()).add(i)
        apply_event(ledger, ev, validator=validator)
    warnings = [w for w in validator.warnings[start:]]

    out: dict[str, dict] = {}
    for ev in events:
        eid = ev.get("event_id")
        if not eid or eid in out:
            continue
        mine = [w for w in warnings if w.get("event_id") == eid]
        scs = []
        for i, sc in enumerate(ev.get("state_changes") or []):
            reasons = []
            for w in mine:
                sig = GRADING_LEVELS.get(w.get("level"))
                if sig and w.get("entity") == sc.get("entity") and w.get("field") == sc.get("field"):
                    reasons.append({"signal": sig, "ledger_value": w.get("ledger_value"),
                                    "ledger_since": w.get("ledger_since"),
                                    "expected_from": w.get("expected_from")})
            for label in _labels(sc):
                reasons.append({"signal": "unresolved_label", "label": label})
            if i in unknown_hits.get(eid, set()):
                reasons.append({"signal": "unknown_from"})
            scs.append({"index": i, "entity": sc.get("entity"), "field": sc.get("field"),
                        "grade": TENTATIVE if reasons else CONFIRMED, "reasons": reasons})
        body = {k: v for k, v in ev.items() if k != "state_changes"}
        own = [{"signal": "unresolved_label", "label": lb} for lb in _labels(body)]
        tentative = bool(own) or any(s["grade"] == TENTATIVE for s in scs)
        out[eid] = {"grade": TENTATIVE if tentative else CONFIRMED,
                    "own_reasons": own, "state_changes": scs}
    return GateResult(
        episode=new.get("episode"),
        config={"knows_mode": knows_mode, "unknown_from": bool(unknown_from), "timelines": tls},
        events=out, warnings=warnings,
    )


def filter_event(ev: dict, gate: dict | None) -> tuple[dict | None, int]:
    """exclude: 등급 파일(gate, GateResult.to_dict 형태)에 따라 사건 하나에서 잠정 사실을 뺀다.

    own_reasons 가 있는 사건은 None(통째로 제외), 나머지는 잠정 state_change 만 뺀 사본.
    gate 가 없거나(관문 도입 전 판본) 그 사건 등급이 없으면 그대로. ev 는 바꾸지 않는다.
    반환: (사건 또는 None, 뺀 state_change 수)
    """
    g = ((gate or {}).get("events") or {}).get(ev.get("event_id")) if isinstance(ev, dict) else None
    if g is None:
        return ev, 0
    if g.get("own_reasons"):
        return None, 0
    bad = {s["index"] for s in g.get("state_changes") or [] if s.get("grade") == TENTATIVE}
    if not bad:
        return ev, 0
    scs = ev.get("state_changes") or []
    return dict(ev) | {"state_changes": [sc for i, sc in enumerate(scs) if i not in bad]}, len(bad)


def filter_episode(doc: dict, gate: dict | None) -> tuple[dict, list[str], int]:
    """filter_event 를 화 문서 전체에 적용한 사본. 반환: (문서 사본, 뺀 사건 ID, 뺀 state_change 수)"""
    kept, dropped_ev, dropped_sc = [], [], 0
    for ev in doc.get("events") or []:
        out, n = filter_event(ev, gate)
        if out is None:
            dropped_ev.append(ev.get("event_id"))
        else:
            kept.append(out)
            dropped_sc += n
    return dict(doc) | {"events": kept}, dropped_ev, dropped_sc
