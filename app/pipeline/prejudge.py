"""judge 앞 단계 개선 P(설계 문서 §0-9 "judge 앞 단계 개선 P", main 18f5b20) — 어댑터 쪽 후처리. 코어(weave_core) 무수정.

세 갈래, 각각 설정 플래그로 켜고 끈다(기본 전부 꺼짐 → 기존 출력과 바이트 단위로 같다).
- F1 화 안 증분 원장: 사건마다 원장 사본에 같은 화의 앞 사건을 `ledger.apply_event` 로 순서대로 적용한 스냅샷.
  LLM judge 프롬프트(build_judge_prompt 가 받는 원장)와 학습 judge 쌍(build_pairs 에 넘기는 원장)에 같은 시점을 쓴다.
  단위는 사건이다. 패킷 앞 사건 + 같은 패킷 안 앞 사건 모두 반영한다(원인 (2) "같은 화·같은 패킷 안").
- F2 추출 후처리(일반 규칙, 문서 단위): ① 구속 계열 body_status → transient ② 위치 자동 갱신 대상에 목격자·주체 추가
  ③ 명시적 location→unknown 버림 ④ `NEW:` 장소를 레지스트리 이름과 정확히 같을 때 그 ID 로 해소. 규칙별 적용 수를 센다.
  ②는 사건에 `auto_location_extra` 를 달고, 원장 재생 때 `apply_event_f2` 가 source "auto_witness" 로 반영한다
  (그 키가 없는 사건에는 원래 apply_event 와 같다).
- F3 judge 프롬프트 후처리: ① (e)절 "모든 사건" 문구를 사실대로 ② 스냅샷 줄에 "since 이후 경과 사건 수·출처"
  ③ (f) 판정 지시에 지침 한 줄.
"""
from __future__ import annotations

import copy
import re
import sys
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

CORE_DIR = Path(__file__).resolve().parent / "weave_core"
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

import ledger as w_ledger  # noqa: E402

# ---------------------------------------------------------------- 상수(설계 문서 고정)
RESTRAINT_KEYWORDS: tuple[str, ...] = ("구속", "수갑", "체포", "기절", "실신", "마취", "취함", "잠듦")
F3_GUIDE = "변화 기록이 없다는 것만으로 모순이라 판정하지 않는다. 일시 상태·오래된 위치는 참고로만 쓴다."
E_CLAUSE_OLD = "검색으로 선별하지 않았다. 지금까지의 모든 사건이 아래에 있다."
E_CLAUSE_NEW = ("검색으로 선별하지 않았다. 아래는 확정된 이전 회차의 사건이다. 같은 회차에서 이 사건보다 앞선 사건은 "
                "이 목록에 없다. 원장과 이 목록은 추출된 변화만 담으므로, 기록되지 않은 이동·해제 같은 변화가 있을 수 있다.")
F_HEADER = "## (f) 판정 지시"
AUTO_SOURCES = ("auto_participant", "auto_witness")
UNKNOWN_VALUES = (None, "", "unknown")
F2_RULES = ("restraint_transient", "auto_location_extra", "drop_location_unknown", "new_location_resolved")
F2_BASE = frozenset(F2_RULES)                      # 개선 P 의 F2 ①–④
F2_ASSUMED_LOCATION = "drop_assumed_location"      # 개선 P2 의 F2⑤ (H1)
NON_ACTUAL = ("memory", "dream", "vision")         # F6·F8 대상 reality_status
F5_MAX = 6
E_CLAUSE_NEW_F9 = ("검색으로 선별하지 않았다. 아래는 확정된 이전 회차의 사건이고, 같은 회차에서 이 사건(묶음)보다 앞선 사건은 "
                   "목록 끝에 따로 붙였다. 원장과 이 목록은 추출된 변화만 담으므로, 기록되지 않은 이동·해제 같은 변화가 있을 수 있다.")
F9_HEADER = "- (같은 회차의 앞선 사건 — 원장 스냅샷에 반영된 범위)"
TRANSIENT_OLD_NONE = " [일시적, 해소 사건 미기록 — 해소 시 None]"
TRANSIENT_NEW = " [일시 상태(지속 여부 불명)]"
_LABEL_TAIL = re.compile(r"[가-힣A-Za-z0-9_]")
_ID_TOKEN = re.compile(r"\b((?:CHAR|LOC|OBJ|TIME)_\d+)\b(?![(_])")


# ---------------------------------------------------------------- F2 추출 후처리

def _location_names(registry: dict | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for loc in (registry or {}).get("locations") or []:
        if not isinstance(loc, dict) or not loc.get("id"):
            continue
        for n in [loc.get("name")] + list(loc.get("aliases") or []):
            if isinstance(n, str) and n:
                out.setdefault(n, loc["id"])
    return out


def _resolve_new_location(value: Any, names: dict[str, str]) -> str | None:
    """`NEW:라벨` → 레지스트리 장소 ID. 라벨(또는 밑줄을 공백으로 되돌린 라벨)이 이름·별칭과 정확히 같을 때만."""
    if not isinstance(value, str) or not value.startswith("NEW:"):
        return None
    label = value[4:]
    return names.get(label) or names.get(label.replace("_", " "))


def f2_event(ev: dict, names: dict[str, str], counts: Counter, rules: frozenset = F2_BASE) -> dict:
    """사건 하나의 사본에 F2 규칙을 적용한다(원본은 바꾸지 않는다). rules 기본 = ①–④(개선 P), ⑤는 F2_ASSUMED_LOCATION."""
    ev = copy.deepcopy(ev)
    if F2_ASSUMED_LOCATION in rules and ev.get("assumed_states"):
        # ⑤ location 자유 서술 전제는 버린다(추출 프롬프트 지시와 같게, H1)
        keep = [a for a in ev["assumed_states"] if not (isinstance(a, dict) and a.get("field") == "location")]
        counts[F2_ASSUMED_LOCATION] += len(ev["assumed_states"]) - len(keep)
        ev["assumed_states"] = keep
    if not (rules & F2_BASE):
        return ev
    # ④ NEW: 장소 해소 (location_id, location state_change 값)
    rid = _resolve_new_location(ev.get("location_id"), names)
    if rid:
        ev["location_id"] = rid
        counts["new_location_resolved"] += 1
    kept = []
    for sc in ev.get("state_changes") or []:
        if not isinstance(sc, dict):
            kept.append(sc)
            continue
        field = sc.get("field")
        if field == "location":
            # ③ 명시적 location → unknown 은 버린다
            if sc.get("to") in UNKNOWN_VALUES:
                counts["drop_location_unknown"] += 1
                continue
            rid = _resolve_new_location(sc.get("to"), names)
            if rid:
                sc = dict(sc) | {"to": rid}
                counts["new_location_resolved"] += 1
        if field == "body_status" and sc.get("volatility", w_ledger.VOL_PERSISTENT) != "transient" \
                and any(k in str(sc.get("to") or "") for k in RESTRAINT_KEYWORDS):
            # ① 구속 계열 → transient
            sc = dict(sc) | {"volatility": "transient"}
            counts["restraint_transient"] += 1
        kept.append(sc)
    if "state_changes" in ev:
        ev["state_changes"] = kept
    # ② 위치 자동 갱신 대상에 목격자·주체(main_actor) 추가 — 참여자·명시적 위치 변경 인물은 제외
    loc = ev.get("location_id")
    if loc and loc != "unknown":
        explicit = {sc.get("entity") for sc in ev.get("state_changes") or []
                    if isinstance(sc, dict) and sc.get("field") == "location"}
        parts = set(ev.get("participant_character_ids") or [])
        cands = list(ev.get("witness_character_ids") or []) + [ev.get("main_actor_character_id")]
        extra: list[str] = []
        for ch in cands:
            if ch in UNKNOWN_VALUES or ch in parts or ch in explicit or ch in extra:
                continue
            extra.append(ch)
        if extra:
            ev["auto_location_extra"] = extra
            counts["auto_location_extra"] += len(extra)
    return ev


def f2_doc(doc: dict, registry: dict | None, counts: Counter | None = None,
           rules: frozenset = F2_BASE) -> tuple[dict, Counter]:
    """추출 문서(화 또는 장면, {"events": [...]})의 사본에 F2 를 적용한다. 반환: (사본, 규칙별 적용 수)."""
    counts = counts if counts is not None else Counter()
    names = _location_names(registry)
    out = dict(doc)
    out["events"] = [f2_event(e, names, counts, rules) for e in doc.get("events") or []]
    return out, counts


def f2_rules(*, base: bool, assumed_location: bool) -> frozenset:
    return (F2_BASE if base else frozenset()) | (frozenset({F2_ASSUMED_LOCATION}) if assumed_location else frozenset())


# ---------------------------------------------------------------- F4 경계 인식 정규화 (H2)

def normalize_bounded(value: Any, mapping: dict) -> Any:
    """코어 normalize 와 같되(긴 라벨 우선), 라벨 바로 뒤 글자가 [가-힣A-Za-z0-9_] 이면 치환하지 않는다.
    예: 매핑 `NEW:칼`→CHAR_054 가 보류 라벨 `NEW:칼_하클리` 를 `CHAR_054_하클리` 로 깨뜨리지 않는다."""
    if isinstance(value, str):
        if "NEW:" not in value:
            return value
        for label in sorted(mapping, key=len, reverse=True):
            if label not in value:
                continue
            out, i = [], 0
            while True:
                j = value.find(label, i)
                if j < 0:
                    out.append(value[i:])
                    break
                end = j + len(label)
                if end < len(value) and _LABEL_TAIL.match(value[end]):
                    out.append(value[i:end])
                else:
                    out.append(value[i:j] + mapping[label])
                i = end
            value = "".join(out)
        return value
    if isinstance(value, list):
        return [normalize_bounded(v, mapping) for v in value]
    if isinstance(value, dict):
        return {k: normalize_bounded(v, mapping) for k, v in value.items()}
    return value


# ---------------------------------------------------------------- F6 비현실 사건 재생 제외 (M1)

def f6_view(ev: dict, counts: Counter | None = None) -> dict:
    """재생용 사본: memory·dream·vision 사건은 state_changes·위치 자동 갱신(목격자 포함)을 뺀다. knowledge 는 둔다."""
    if ev.get("reality_status") not in NON_ACTUAL:
        return ev
    if counts is not None:
        counts["f6_events"] += 1
        counts["f6_state_changes"] += len(ev.get("state_changes") or [])
    out = dict(ev) | {"state_changes": [], "location_id": "unknown"}
    out.pop("auto_location_extra", None)
    return out


def f6_doc(doc: dict, counts: Counter | None = None) -> dict:
    return dict(doc) | {"events": [f6_view(e, counts) for e in doc.get("events") or []]}


# ---------------------------------------------------------------- F5 패킷 재묶음 (H3)

def f5_regroup(events: list[dict], max_n: int = F5_MAX) -> tuple[list[dict], dict[str, int]]:
    """같은 화 사건(서술 순서)을 연속·같은 location_id 로 묶는다(최대 max_n). 사본의 scene_id 를 `F5_<k>` 로 다시 쓴다
    (원래 `c{n}:` 접두 포함 scene_id 는 버린다). location_id 가 없거나 unknown 이면 혼자 묶음. 반환: (사본, 사건→묶음 첫 위치)."""
    out: list[dict] = []
    starts: dict[str, int] = {}
    k, start, size, prev = 0, 0, 0, None
    for i, e in enumerate(events):
        loc = e.get("location_id")
        ok = loc not in UNKNOWN_VALUES
        if not (ok and loc == prev and size < max_n):
            k, start, size = k + 1, i, 0
        size += 1
        prev = loc if ok else None
        out.append(dict(e) | {"scene_id": f"F5_{k}"})
        if e.get("event_id"):
            starts[e["event_id"]] = start
    return out, starts


# ---------------------------------------------------------------- F8 checker 항목 강도 내림 (M2·M6)

def _sev_info() -> str:
    import checker as w_checker  # noqa: PLC0415 - weave_core 경로(위에서 연다)
    return w_checker.SEV_INFO


def f8_downgrade(items: list[dict], events_by_id: dict, counts: Counter | None = None) -> list[dict]:
    """비actual 사건의 structural_* 와 possesses_removed 를 '참고'(검수 요망)로 내린다. 서비스 화면(checks_to_contradictions)용."""
    info = _sev_info()
    out = []
    for it in items:
        t = str(it.get("type") or "")
        ev = events_by_id.get(it.get("event_id")) or {}
        hit = (t.startswith("structural_") and ev.get("reality_status", "actual") != "actual") or t == "possesses_removed"
        if hit and it.get("severity") != info:
            if counts is not None:
                counts["f8_" + ("structural" if t.startswith("structural_") else "possesses_removed")] += 1
            it = dict(it) | {"severity": info, "needs_judge": True,
                             "detail": ((it.get("detail") or "") + " [참고로 내림·검수 요망]").strip()}
        out.append(it)
    return out


_ORIG_APPLY_EVENT: Callable = w_ledger.apply_event


def apply_event_f2(ledger: Any, ev: dict, validator: Any = None) -> None:
    """원래 apply_event + F2 ② 추가 인물 위치 자동 갱신(source auto_witness). `auto_location_extra` 가 없으면 원래와 같다."""
    _ORIG_APPLY_EVENT(ledger, ev, validator)
    extra = ev.get("auto_location_extra")
    if not extra or ev.get("reality_status", "actual") not in w_ledger.STATE_APPLY:
        return
    loc = ev.get("location_id")
    if not loc or loc == "unknown":
        return
    timeline = ev.get("timeline") or "unknown"
    for ch in extra:
        if ch and ch != "unknown":
            ledger.set(timeline, ch, "location", loc, ev.get("event_id", "?"), ev.get("certainty", "explicit"),
                       source="auto_witness")


def install_apply_event_f2() -> None:
    """코어 모듈 전역 이름을 apply_event_f2 로 바꾼다(코어 파일 무수정). 키가 없는 사건에는 동작이 같다."""
    w_ledger.apply_event = apply_event_f2
    pipe = sys.modules.get("weave_pipeline")
    if pipe is not None and getattr(pipe, "apply_event", None) is _ORIG_APPLY_EVENT:
        pipe.apply_event = apply_event_f2


# ---------------------------------------------------------------- F1 화 안 증분 원장

class Snapshots:
    """같은 화 사건 목록(서술 순서, 정규화된 것)에 대해 '사건 i 직전' 원장 스냅샷을 만든다(사본, 앞에서부터 누적)."""

    def __init__(self, base_ledger: Any, episode_events: Iterable[dict], *, starts: dict[str, int] | None = None,
                 view: Callable[[dict], dict] | None = None) -> None:
        """starts: 사건→묶음 첫 위치(F5). 주면 같은 묶음 사건은 묶음 앞 원장을 같이 쓴다. view: 재생용 사본(F6)."""
        self.base = base_ledger
        self.raw = list(episode_events)
        self.events = [view(e) for e in self.raw] if view else self.raw
        self.index = {e.get("event_id"): i for i, e in enumerate(self.raw) if e.get("event_id")}
        if starts:
            self.index = {eid: starts.get(eid, i) for eid, i in self.index.items()}
        self._cache: dict[int, Any] = {0: base_ledger}

    def before_index(self, i: int) -> Any:
        if i in self._cache:
            return self._cache[i]
        j = max(k for k in self._cache if k <= i)
        led = copy.deepcopy(self._cache[j])
        for e in self.events[j:i]:
            w_ledger.apply_event(led, e)
        self._cache[i] = led
        return led

    def before(self, event_id: str | None) -> Any:
        i = self.index.get(event_id)
        return self.base if i is None else self.before_index(i)

    def prior_events(self, event_id: str | None) -> list[dict]:
        """그 사건(묶음) 앞의 같은 화 사건 — 원래 사건(재생용 사본 아님). F9 용."""
        i = self.index.get(event_id)
        return [] if i is None else self.raw[:i]


def build_pairs_f1(group: dict, snaps: Snapshots, corpus: list[dict], registry: dict | None,
                   checker_items: list[dict] | None, **kw: Any) -> list:
    """사건마다 그 시점 스냅샷으로 build_pairs 를 돌려 그 사건의 쌍만 모은다(pairs.py 규칙·순서 그대로, 원장만 다름)."""
    from app.pipeline.pairs import build_pairs

    out = []
    for ev in group.get("events") or []:
        eid = ev.get("event_id")
        pairs = build_pairs(group, snaps.before(eid), corpus, registry, checker_items, **kw)
        out.extend(p for p in pairs if p.event_id == ("" if eid is None else str(eid)))
    return out


# ---------------------------------------------------------------- F3 프롬프트 후처리

_ENTITY_HEAD = re.compile(r"^- (\S+):$")
_FIELD_LINE = re.compile(r"^    (\S+?)( \[승격 전제\])? = (.*?) \((?:since|취득) (\S+?),")


def _source_of(ledger: Any, timeline: str, entity: str, field: str, value: str) -> str | None:
    prov = (getattr(ledger, "state", {}).get(timeline) or {}).get(entity, {}).get(field)
    if not isinstance(prov, dict):
        return None
    if field == "possesses":
        oid = value.split(" ", 1)[0]
        p = prov.get(oid)
        return p.get("source") if isinstance(p, dict) else None
    if field == "knows":
        return None
    return prov.get("source")


def f3_prompt(prompt: str, *, ledger: Any, event: dict, order: list[str], counts: Counter | None = None) -> str:
    """판정 프롬프트 글(사건 하나 분량)에 F3 ①–③ 을 적용한다. order = 판정 시점까지의 사건 id 서술 순서(확정 회차 + 같은 화)."""
    return postprocess_prompt(prompt, ledger=ledger, event=event, order=order, counts=counts, f3=True)


def _event_lines(evs: list[dict], episode: Any) -> list[str]:
    out = [F9_HEADER]
    for p in evs:
        out.append(f"- {p.get('event_id')} ({episode if episode is not None else '?'}화, "
                   f"reality_status {p.get('reality_status')})")
        out.append(f"    {p.get('title')}: {p.get('summary')}")
    return out


def postprocess_prompt(prompt: str, *, ledger: Any, event: dict, order: list[str], counts: Counter | None = None,
                       f3: bool = False, f7: bool = False, f9: bool = False, names: dict[str, str] | None = None,
                       prior: list[dict] | None = None, episode: Any = None) -> str:
    """판정 프롬프트 글(사건 하나 분량) 후처리. F3 ①–③, F7(스냅샷 ID 뒤 (이름)·transient 문구), F9((e)절 끝에 같은 화 앞 사건).
    order = 판정 시점까지의 사건 id 서술 순서(확정 회차 + 같은 화)."""
    counts = counts if counts is not None else Counter()
    names = names or {}

    def named(text: str) -> str:
        def sub(m: re.Match) -> str:
            n = names.get(m.group(1))
            return f"{m.group(1)}({n})" if n else m.group(1)
        return _ID_TOKEN.sub(sub, text)
    if not f3:
        lines = prompt.split("\n")
        out: list[str] = []
        entity = None
        for line in lines:
            if _ENTITY_HEAD.match(line):
                entity = line
                if f7:
                    line = named(line)
                    counts["f7_snapshot_lines"] += 1
            elif entity and line.startswith("    "):
                if f7:
                    line = named(line.replace(TRANSIENT_OLD_NONE, TRANSIENT_NEW))
                    counts["f7_snapshot_lines"] += 1
            else:
                entity = None
            if f9 and line == F_HEADER and prior:
                while out and out[-1] == "":
                    out.pop()
                out.extend(_event_lines(prior, episode) + [""])
                counts["f9_events"] += len(prior)
            out.append(line)
        return "\n".join(out)
    pos = {eid: i for i, eid in enumerate(order)}
    cur = pos.get(event.get("event_id"), len(order))
    timeline = event.get("timeline") or "unknown"
    out: list[str] = []
    entity: str | None = None
    for line in prompt.split("\n"):
        m = _ENTITY_HEAD.match(line)
        if m:
            entity = m.group(1)
            if f7:
                line = named(line)
                counts["f7_snapshot_lines"] += 1
            out.append(line)
            continue
        if not line.startswith("    "):
            entity = None
        f = _FIELD_LINE.match(line) if entity else None
        if f and f.group(4) in pos:
            field, promoted, value, since = f.group(1), f.group(2), f.group(3), f.group(4)
            n = max(0, cur - pos[since] - 1)
            tag = f"since 이후 {n}사건"
            if not promoted:
                src = _source_of(ledger, timeline, entity, field, value)
                tag += " · 출처 " + ("자동" if src in AUTO_SOURCES else "명시")
            line = f"{line} [{tag}]"
            counts["f3_snapshot_lines"] += 1
        if entity and f7 and line.startswith("    "):
            line = named(line.replace(TRANSIENT_OLD_NONE, TRANSIENT_NEW))
            counts["f7_snapshot_lines"] += 1
        if line == E_CLAUSE_OLD:
            line = E_CLAUSE_NEW_F9 if (f9 and prior) else E_CLAUSE_NEW
            counts["f3_e_clause"] += 1
        if f9 and line == F_HEADER and prior:
            while out and out[-1] == "":
                out.pop()
            out.extend(_event_lines(prior, episode) + [""])
            counts["f9_events"] += len(prior)
        out.append(line)
        if line == F_HEADER:
            out.append(F3_GUIDE)
            counts["f3_guide"] += 1
    return "\n".join(out)


@contextmanager
def judge_prompt_patch(pipe: Any, *, f1: bool, f3: bool, episode_events: list[dict], corpus: list[dict],
                       counts: Counter | None = None, f7: bool = False, f9: bool = False,
                       starts: dict[str, int] | None = None, view: Callable[[dict], dict] | None = None,
                       names: dict[str, str] | None = None) -> Iterator[Counter]:
    """weave_pipeline.build_judge_prompt 를 잠깐 감싼다(F1 원장 스냅샷, F3 글 후처리). 둘 다 꺼지면 아무것도 바꾸지 않는다.

    episode_events: 이번 화 사건(정규화, 서술 순서). corpus: 확정 회차 사건(시간순). 호출자가 잠금을 잡는다.
    """
    counts = counts if counts is not None else Counter()
    if not (f1 or f3 or f7 or f9):
        yield counts
        return
    orig = pipe.build_judge_prompt
    snaps: Snapshots | None = None
    order = [str(p.get("event_id")) for p in corpus or []] + [str(e.get("event_id")) for e in episode_events]

    eps = [p.get("episode") for p in corpus or [] if isinstance(p.get("episode"), int)]
    cur_episode = (max(eps) + 1) if eps else None

    def patched(e: dict, ledger: Any, *a: Any, **k: Any) -> str:
        nonlocal snaps
        led = ledger
        if snaps is None or snaps.base is not ledger:
            snaps = Snapshots(ledger, episode_events, starts=starts, view=view)
        if f1:
            led = snaps.before(e.get("event_id"))
            counts["f1_snapshots"] += 1
        p = orig(e, led, *a, **k)
        if f3 or f7 or f9:
            p = postprocess_prompt(p, ledger=led, event=e, order=order, counts=counts, f3=f3, f7=f7, f9=f9,
                                   names=names, prior=snaps.prior_events(e.get("event_id")) if f9 else None,
                                   episode=cur_episode)
        return p

    pipe.build_judge_prompt = patched
    try:
        yield counts
    finally:
        pipe.build_judge_prompt = orig
