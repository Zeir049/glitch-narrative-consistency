# -*- coding: utf-8 -*-
"""
ledger.py — Weave 상태 원장 컴파일러 (이벤트 소싱 replay)

사건 로그(episode_XX.json)가 원천(source of truth)이고,
상태 원장(ledger)은 로그를 order 순서로 재생(replay)해 만드는 파생 캐시다.

재생 규칙
---------
1. reality_status가 "actual" 또는 "memory"인 사건만 상태(state)를 반영한다.
   dream / vision / hearsay / document 사건은 상태 반영 금지.
2. knowledge_gained는 reality_status와 무관하게 인물의 knows(지식 목록)에
   반영한다. via 정보(direct/told/dream/document/inference)를 함께 유지한다.
3. 모든 상태 값에는 provenance가 붙는다:
   {"value": ..., "since": <event_id>, "certainty": ..., "source": ...}
4. 원장은 timeline별로 분리된다. 사건의 timeline 필드에 따라 해당 timeline
   원장에만 적용되고, 목록에 없는 값은 "unknown" 버킷으로 들어간다.
   (지식은 인물에게 귀속되는 것이므로 timeline과 무관하게 인물 단위로 관리)
   (제품화) timeline 목록은 작품마다 다르므로 Ledger(timelines=...) /
   compile_ledger(timelines=...)로 주입한다. 인자를 생략하면 WM 값
   (real_present / future_world / unknown)이 쓰여 기존 재생 결과가 유지된다.
5. 참여자 위치 자동 갱신: 사건이 적용될 때 participant_character_ids의 각
   인물 location을 사건의 location_id로 자동 갱신한다. location_id가
   "unknown"이면 갱신하지 않으며, 같은 사건에 그 인물에 대한 명시적
   location state_change가 있으면 명시적 값이 우선한다.
6. NEW: 라벨은 entity_registry.json의 new_label 매핑으로 정식 ID로
   정규화한 뒤 처리한다 (문자열 내부에 섞인 라벨 포함).
7. 멱등성: 재생은 순수 함수다. 같은 로그를 몇 번 재생해도 같은 원장.
7-1. (v4) state_change 확장 필드 — 전부 선택 사항이며 기본값은 기존 동작과 같다.
   - "certainty" : 사건 레벨 certainty를 덮어쓴다 (없으면 사건 레벨로 폴백).
     한 사건 안의 "A가 넘어졌다(explicit)"와 "그래서 다쳤을 것이다(inferred)"를
     구분하기 위한 것. [수정 5]
   - "volatility" : "persistent"(기본) / "transient".
     transient는 장면 하나로 풀릴 수 있는 상태(실신·취기·구속 등)를 뜻하며,
     checker가 이를 구조적 전제 위반으로 쓰지 않게 한다. 사망은 persistent다.
     "decays_to"는 해소되면 돌아갈 값(예: alive transient → "true"). [수정 3]
   - "op" : "add"(기본) / "remove". possesses 전용.
     remove는 소유 항목을 삭제하지 않고 status="removed"로 표시해
     provenance(언제/어느 사건으로 잃었는지)를 보존한다. 원장은 감사 가능해야
     하므로 삭제하지 않는다. [수정 4]
7-2. (v4) knowledge_gained 확장 필드
   - "retained" : bool, 기본 true. false면 인물이 그 정보를 보유하고 있지
     않다는 뜻이다(예: 꿈에서 봤으나 깨자마자 잊음). 원장에는 남되 checker의
     순방향·역방향 매칭 어느 쪽에도 근거로 쓰이지 않으며, judge 프롬프트에는
     [미보유] 표시를 달아 그대로 싣는다 — 판정자가 맥락을 알아야 하므로
     숨기지 않는다. [수정 2]
8. (v3) 반복 전제 승격: 서로 다른 사건 3개 이상에서 같은 (entity, field)의
   assumed_states가 일관되게(공통 핵심 토큰 존재) 확인되면 그 전제를
   원장에 등재한다 — source: "promoted_assumption", certainty: "inferred",
   since: 마지막 확인 사건 ID, 값: 가장 상세한 서술(최장 문자열) 채택,
   전체 관측 이력(observations) 동봉.
   명시적 state_change가 있는 필드는 승격이 본 값을 덮어쓰지 않는다 —
   승격 전제는 엔티티의 "_promoted" 슬롯에 별도 등재되어 판정 컨텍스트에
   보조 정보로 실린다 (명시 우선).
   대상 필드는 자유 서술 필드(body_status, ability)로 한정한다.
   (possesses/location/alive는 구조 검사·명시 변경이, knows는 knowledge
   원장이 담당하므로 승격 대상에서 제외)
9. (B-026) knows 누적 모드 — Ledger(knows_mode=...) / compile_ledger(knows_mode=...).
   - "legacy"(기본): 기존 동작. knows state_change는 op와 무관하게 이전 값을 덮어쓴다
     (add도 덮어쓴다 — 단일 값 필드와 같은 경로). 동결 패킷 재현용.
   - "accumulate": knows는 add 전용. op가 "set"/"remove" 등이어도 add로 처리하고
     validator가 있으면 knows_op_coerced 경고를 남긴다. 슬롯의 최상위
     value/since는 최근 값(기존 조회 호환), "accumulated"에 획득 순 전 이력
     (같은 value 문자열은 최초 since만 유지).

사용법:  python ledger.py   → ledger_final.json, snapshots/ep_01..05.json 생성
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent

# (제품화) timeline 목록은 작품마다 다르다. 아래는 WM(원 프로토타입)의 값이며
# 인자를 주지 않으면 이 값이 그대로 쓰인다 — 기존 재생 결과 무변경 보장.
# 신규 프로젝트는 projects/<이름>/project.json의 "timelines"에서 주입한다
# (weave_config.load_project_config / normalize_timelines 참조). 기본값은 ["main"].
# "unknown"은 timeline 미상 사건의 폴백 버킷이므로 항상 마지막에 포함된다.
FALLBACK_TIMELINE = "unknown"
STATE_TIMELINES = ("real_present", "future_world", FALLBACK_TIMELINE)


def normalize_timelines(timelines=None) -> tuple:
    """설정에서 받은 timeline 목록을 재생에 쓸 튜플로 정규화한다.

    - None이면 STATE_TIMELINES(기존 동작)를 그대로 돌려준다.
    - 중복을 제거하고 순서를 보존하며, FALLBACK_TIMELINE을 반드시 포함시킨다.
    """
    if timelines is None:
        return STATE_TIMELINES
    ordered = list(dict.fromkeys(str(t) for t in timelines if str(t).strip()))
    if FALLBACK_TIMELINE not in ordered:
        ordered.append(FALLBACK_TIMELINE)
    return tuple(ordered)


# (B-026) knows 처리 방식. legacy = 기존(덮어쓰기), accumulate = add 전용 누적
KNOWS_MODES = ("legacy", "accumulate")

STATE_APPLY = {"actual", "memory"}          # 상태 반영이 허용되는 reality_status
ALLOWED_FIELDS = {"alive", "location", "body_status", "possesses", "knows", "ability",
                  "condition"}              # (v4) condition: LOC_/OBJ_ 상태 필드 [수정 6]

# (v4) 엔티티 타입별 허용 필드 [수정 6]
# 장소에 신체 상태(body_status)가 붙는 것 같은 타입 오류를 잡기 위한 스키마.
# 위반은 validator가 schema_error로 리포트하되 재생은 계속한다 (로그가 원천).
ENTITY_FIELD_SCHEMA = {
    "CHAR_": {"alive", "location", "body_status", "possesses", "knows", "ability"},
    "LOC_":  {"condition"},
    "OBJ_":  {"condition", "location"},
}

# (v4) 상태 수명 [수정 3]
VOL_PERSISTENT = "persistent"
VOL_TRANSIENT = "transient"

_ID_PATTERN = re.compile(r"(?:CHAR|LOC|OBJ|TIME|RULE)_\d+")
_WORD = re.compile(r"[가-힣A-Za-z0-9]{2,}")

# 반복 전제 승격 대상 필드와 최소 관측 사건 수
PROMOTE_FIELDS = ("body_status", "ability")
PROMOTE_MIN_EVENTS = 3


def _common_core_tokens(texts: list[str]) -> list[str]:
    """관측된 전제 서술들이 '일관'되는지 판정하는 공통 핵심 토큰.
    첫 서술의 토큰 중, 나머지 모든 서술에 (부분 문자열 허용으로) 등장하는 것."""
    if not texts:
        return []
    first = _WORD.findall(texts[0])
    common = []
    for tok in first:
        if all(any(tok in u or u in tok for u in _WORD.findall(t)) for t in texts[1:]):
            common.append(tok)
    return common


# ---------------------------------------------------------------- 로딩/정규화

def load_registry(base: Path = BASE):
    """entity_registry.json을 읽고 (registry, NEW:라벨 → 정식ID 매핑)을 반환."""
    reg = json.loads((base / "entity_registry.json").read_text(encoding="utf-8"))
    mapping = {}
    for coll in ("characters", "locations", "objects", "times", "worldview_rules"):
        for item in reg.get(coll, []):
            label = item.get("new_label")
            if label:
                mapping[label] = item["id"]
    return reg, mapping


def normalize(value, mapping: dict):
    """모든 문자열에서 NEW: 라벨을 정식 ID로 치환 (긴 라벨 우선 → 접두 충돌 방지)."""
    if isinstance(value, str):
        for label in sorted(mapping, key=len, reverse=True):
            if label in value:
                value = value.replace(label, mapping[label])
        return value
    if isinstance(value, list):
        return [normalize(v, mapping) for v in value]
    if isinstance(value, dict):
        return {k: normalize(v, mapping) for k, v in value.items()}
    return value


def load_episodes(base: Path = BASE):
    """episode_XX.json 전부를 정규화하여 화 번호 순으로 반환."""
    _, mapping = load_registry(base)
    episodes = []
    for path in sorted(base.glob("episode_*.json")):
        ep = json.loads(path.read_text(encoding="utf-8"))
        episodes.append(normalize(ep, mapping))
    episodes.sort(key=lambda e: e["episode"])
    return episodes


def norm_alive(v) -> str:
    """alive 값을 'true' / 'false' / 'unconscious' 문자열로 정규화."""
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v).strip().lower()


def extract_ids(text: str, prefix: str = ""):
    """문자열에서 정식 ID들을 추출. prefix가 있으면 해당 종류만."""
    ids = _ID_PATTERN.findall(str(text))
    return [i for i in ids if i.startswith(prefix)] if prefix else ids


# ---------------------------------------------------------------------- 원장

class Ledger:
    """timeline별 엔티티 상태 + 인물별 지식 목록."""

    def __init__(self, timelines=None, knows_mode: str = "legacy"):
        if knows_mode not in KNOWS_MODES:
            raise ValueError(f"knows_mode must be one of {KNOWS_MODES}: {knows_mode}")
        self.knows_mode = knows_mode
        # (제품화) timelines를 주면 그 목록으로 원장을 만든다. 주지 않으면
        # STATE_TIMELINES — 즉 기존 동작과 완전히 동일하다.
        self.timelines = normalize_timelines(timelines)
        # state[timeline][entity_id][field] = provenance dict
        # (entity dict의 "_promoted" 키는 반복 전제 승격 슬롯 — 필드명과 충돌 없음)
        self.state = {tl: {} for tl in self.timelines}
        # knowledge[char_id] = [{"info","via","since"}]  (획득 순서 보존)
        self.knowledge = {}
        # 반복 전제 관측: (timeline, entity, field) -> {event_id: assumes}
        self._assumption_obs = {}

    # -- 조회 --------------------------------------------------------------
    def get(self, timeline: str, entity: str, field: str):
        """해당 timeline 원장의 현재 상태(provenance dict) 또는 None."""
        return self.state.get(timeline, {}).get(entity, {}).get(field)

    def possesses(self, timeline: str, entity: str) -> dict:
        """소유 목록 {OBJ_id: provenance}. 없으면 빈 dict."""
        slot = self.get(timeline, entity, "possesses")
        return slot if isinstance(slot, dict) and "value" not in slot else (slot or {})

    # -- 갱신 --------------------------------------------------------------
    def set(self, timeline, entity, field, value, since, certainty, source="state_change",
            op="add", volatility=VOL_PERSISTENT, decays_to=None):
        """상태 1건을 반영한다.

        (v4) op        : possesses 전용. "add"(기본) / "remove". [수정 4]
        (v4) volatility: "persistent"(기본) / "transient". [수정 3]
        (v4) decays_to : transient 상태가 해소되면 돌아갈 값.
        """
        if timeline not in self.state:
            timeline = FALLBACK_TIMELINE
        ent = self.state[timeline].setdefault(entity, {})
        prov = {"value": value, "since": since, "certainty": certainty, "source": source}
        if volatility != VOL_PERSISTENT:
            prov["volatility"] = volatility
            if decays_to is not None:
                prov["decays_to"] = decays_to
        if field == "alive":
            prov["value"] = norm_alive(value)
            ent["alive"] = prov
        elif field == "possesses":
            # 소유는 단일 값이 아니라 집합: OBJ ID별 provenance 유지.
            # (v4) 제거는 삭제가 아니라 status="removed" 표시 — provenance 보존.
            slot = ent.setdefault("possesses", {})
            for oid in extract_ids(value, prefix="OBJ_"):
                if op == "remove":
                    cur = slot.get(oid, {})
                    slot[oid] = {
                        "value": oid,
                        "status": "removed",
                        "since": cur.get("since", since),   # 취득 시점은 보존
                        "certainty": certainty,
                        "source": source,
                        "removed_since": since,
                        "removed_by": since,
                    }
                else:
                    slot[oid] = {"value": oid, "status": "held", "since": since,
                                 "certainty": certainty, "source": source}
        elif field == "knows" and self.knows_mode == "accumulate":
            # (B-026) add 전용 누적 — 이전 지식을 덮어쓰지 않는다
            prev = ent.get("knows")
            hist = list(prev.get("accumulated", [])) if isinstance(prev, dict) else []
            if not any(str(h["value"]) == str(value) for h in hist):
                hist.append({k: v for k, v in prov.items()})
            ent["knows"] = dict(prov, accumulated=hist)
        else:
            ent[field] = prov

    def observe_assumption(self, timeline, entity, field, assumes, event_id):
        """(v3) 반복 전제 승격 — assumed_states 관측을 누적하고,
        서로 다른 사건 PROMOTE_MIN_EVENTS개 이상에서 일관되게 확인되면
        "_promoted" 슬롯에 등재한다. 명시적 state_change 값은 덮어쓰지 않는다."""
        if timeline not in self.state:
            timeline = FALLBACK_TIMELINE
        key = (timeline, entity, field)
        obs = self._assumption_obs.setdefault(key, {})
        obs[event_id] = str(assumes)
        if len(obs) < PROMOTE_MIN_EVENTS:
            return
        texts = list(obs.values())
        core = _common_core_tokens(texts)
        if not core:
            return  # 서술이 일관되지 않음 → 승격 보류
        ent = self.state[timeline].setdefault(entity, {})
        ent.setdefault("_promoted", {})[field] = {
            "value": max(texts, key=len),          # 가장 상세한 서술 채택
            "since": event_id,                     # 마지막 확인 사건
            "certainty": "inferred",
            "source": "promoted_assumption",
            "core_tokens": core,
            "observations": [{"event_id": e, "assumes": t} for e, t in obs.items()],
        }

    def add_knowledge(self, char_id, info, via, since, retained=True):
        """(v4) retained=False면 인물이 그 정보를 보유하고 있지 않다는 뜻이다.
        원장에는 남기되(감사 가능성), checker 매칭 근거로는 쓰지 않는다. [수정 2]"""
        self.knowledge.setdefault(char_id, []).append(
            {"info": info, "via": via, "since": since, "retained": bool(retained)}
        )

    def to_dict(self):
        return {"state": self.state, "knowledge": self.knowledge}


# ---------------------------------------------------------------------- 재생

def iter_events(episode: dict):
    return sorted(episode.get("events", []), key=lambda e: e.get("order", 0))


def apply_event(ledger: Ledger, ev: dict, validator=None):
    """사건 1개를 원장에 반영한다. validator가 있으면 from 값 대조를 먼저 수행."""
    timeline = ev.get("timeline") or "unknown"
    reality = ev.get("reality_status", "actual")
    event_id = ev.get("event_id", "?")
    certainty = ev.get("certainty", "explicit")

    # (규칙 2) 지식은 reality_status와 무관하게 반영 — via / retained 정보 유지
    for kg in ev.get("knowledge_gained", []):
        cid = kg.get("character_id")
        if cid and cid != "unknown":
            ledger.add_knowledge(cid, kg.get("info", ""), kg.get("via"), event_id,
                                 retained=kg.get("retained", True))

    # (규칙 1) dream/vision/hearsay/document는 상태 반영 금지
    if reality not in STATE_APPLY:
        return

    # (규칙 5 준비) 이 사건에서 명시적 location 변경이 있는 인물
    explicit_location = {
        sc["entity"] for sc in ev.get("state_changes", []) if sc.get("field") == "location"
    }

    # state_changes 적용 (validator가 있으면 from 대조 + 스키마 검사 먼저)
    for sc in ev.get("state_changes", []):
        field = sc.get("field")
        if validator is not None:
            # (v4) 스키마 검사는 ALLOWED_FIELDS 필터보다 먼저 — 필터에 걸려
            # 조용히 버려지는 필드도 리포트에 남아야 한다. [수정 6]
            validator.check_schema(ev, sc)
        if field not in ALLOWED_FIELDS:
            continue
        if validator is not None:
            validator.check(ledger, timeline, ev, sc)
        # (v4) certainty는 state_change 단위 값이 있으면 그것을, 없으면 사건 레벨로 폴백 [수정 5]
        sc_certainty = sc.get("certainty", certainty)
        op = sc.get("op", "add")
        if field == "knows" and ledger.knows_mode == "accumulate":
            # (B-026) knows는 add 전용 — set/remove는 add로 강제하고 경고.
            # to 값이 없는 항목(추출기가 assumes 등 다른 키에 적은 경우)은 싣지 않고 경고만
            # (원장 수정 금지 — 다른 키로 보정하지 않는다)
            if op != "add" and validator is not None and hasattr(validator, "warn_knows_op"):
                validator.warn_knows_op(timeline, ev, sc)
            op = "add"
            if sc.get("to") in (None, ""):
                if validator is not None and hasattr(validator, "warn_knows_op"):
                    validator.warn_knows_op(timeline, ev, sc, missing_value=True)
                continue
        # remove는 대상 OBJ ID가 to에 없을 수 있으므로 from으로 폴백
        value = sc.get("to")
        if op == "remove" and not extract_ids(value, prefix="OBJ_"):
            value = sc.get("from")
        ledger.set(timeline, sc["entity"], field, value, event_id, sc_certainty,
                   op=op,
                   volatility=sc.get("volatility", VOL_PERSISTENT),
                   decays_to=sc.get("decays_to"))

    # (규칙 5) 참여자 위치 자동 갱신 — 명시적 변경이 있는 인물은 건너뜀
    loc = ev.get("location_id")
    if loc and loc != "unknown":
        for ch in ev.get("participant_character_ids", []):
            if ch and ch != "unknown" and ch not in explicit_location:
                ledger.set(timeline, ch, "location", loc, event_id, certainty,
                           source="auto_participant")

    # (규칙 8) 반복 전제 승격 — actual/memory 사건의 자유 서술 전제만 관측
    for a in ev.get("assumed_states", []):
        if a.get("field") in PROMOTE_FIELDS and a.get("entity") not in (None, "unknown"):
            ledger.observe_assumption(timeline, a["entity"], a["field"],
                                      a.get("assumes", ""), event_id)


def compile_ledger(episodes=None, validator=None, base: Path = BASE,
                   snapshot_dir: Path | None = None, timelines=None,
                   knows_mode: str = "legacy"):
    """
    전체 화를 서술 순서대로 재생해 원장을 만든다.
    snapshot_dir가 주어지면 화 단위 스냅샷(ep_XX.json)을 저장한다.
    순수 함수적 재생이므로 같은 로그 → 항상 같은 원장 (멱등성).

    (제품화) timelines를 주면 그 목록으로 원장을 만든다. 주지 않으면
    STATE_TIMELINES가 쓰이므로 기존 호출부의 결과는 바뀌지 않는다.
    (B-026) knows_mode 기본 "legacy" — 생략 시 기존 재생 결과 그대로.
    """
    if episodes is None:
        episodes = load_episodes(base)
    ledger = Ledger(timelines, knows_mode=knows_mode)
    for ep in episodes:
        for ev in iter_events(ep):
            apply_event(ledger, ev, validator=validator)
        if snapshot_dir is not None:
            snapshot_dir.mkdir(parents=True, exist_ok=True)
            out = snapshot_dir / f"ep_{ep['episode']:02d}.json"
            out.write_text(
                json.dumps(copy.deepcopy(ledger.to_dict()), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
    return ledger


# ------------------------------------------------------------------ 요약 출력

def summarize_entity(ledger: Ledger, timeline: str, entity: str) -> list[str]:
    """엔티티 한 개의 timeline 상태를 사람이 읽을 요약 라인으로."""
    lines = [f"[{timeline}] {entity}"]
    fields = ledger.state.get(timeline, {}).get(entity, {})
    if not fields:
        lines.append("  (상태 없음)")
    for field, prov in fields.items():
        if field == "_promoted":
            continue
        if field == "possesses":
            for oid, p in prov.items():
                if p.get("status") == "removed":
                    lines.append(f"  possesses   : {oid}  [상실] (취득 {p['since']}, "
                                 f"상실 {p.get('removed_by')})")
                else:
                    lines.append(f"  possesses   : {oid}  (since {p['since']}, {p['certainty']})")
        else:
            vol = ""
            if prov.get("volatility") == VOL_TRANSIENT:
                vol = f"  [일시적, 해소 사건 미기록 → {prov.get('decays_to')}]"
            lines.append(f"  {field:<12}: {prov['value']}  (since {prov['since']}, "
                         f"{prov['certainty']}, {prov['source']}){vol}")
    for field, p in fields.get("_promoted", {}).items():
        lines.append(f"  {field:<12}: [승격 전제] {p['value']}  "
                     f"(since {p['since']}, inferred, {len(p['observations'])}개 사건에서 관측)")
    knows = ledger.knowledge.get(entity, [])
    if knows:
        lines.append(f"  knows       : {len(knows)}건 (최근 3건)")
        for k in knows[-3:]:
            lines.append(f"    - [{k['via']}/{k['since']}] {k['info']}")
    return lines


if __name__ == "__main__":
    # 기존 ledger_final.json / ledger_final_v3.json은 보존 — 새 결과는 v4 태그로 저장
    ledger = compile_ledger(snapshot_dir=BASE / "snapshots")
    (BASE / "ledger_final_v4.json").write_text(
        json.dumps(ledger.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("원장 컴파일 완료 → ledger_final_v4.json, snapshots/ep_01..05.json")
    for eid in ("CHAR_002", "CHAR_006"):
        print("\n".join(summarize_entity(ledger, "future_world", eid)))
