# -*- coding: utf-8 -*-
"""
judge.py — LLM 2차 검수

checker.py의 결정론적 구조 검사가 다루지 않는 소프트 모순
(설정 충돌, 뉘앙스 위반, 지식 범위의 애매한 경계)을 LLM 판정으로 잡는다.

구성
----
- build_judge_prompt(event, ledger, related_events)
  판정 요청 프롬프트를 생성한다. 포함 내용:
  (a) 신규 사건의 요약·원문 발췌·assumed_states
  (b) (v3) entity_registry의 세계관 규칙 전체 — 검색 없이 상시 포함.
      세계관 규칙은 어느 사건과 충돌할지 예측할 수 없으므로 항상 컨텍스트에 실린다.
  (b') (v5) facts 원장 — 사건 로그에서 파생한 설정 사실. (b)와 마찬가지로
      검색 없이 결정론적 규칙(scope)으로 싣는다. related_rules를 가진 FACT은
      (b)절 해당 RULE 아래에 붙고 (b')에 중복 게재하지 않는다.

      ★ **기본값 include_facts=False (비활성화)** — Phase 3A의 G8 회귀 검증에서
        Recall이 0.85 → 0.70으로 떨어졌기 때문이다. 명세 §6 "G8 미달 시 (b')절을
        되돌리고 코드는 남기되 기본 비활성화한다"에 따른 조치다.
        원인은 판정 모델 교체가 아니라 facts 자체임이 표적 대조군으로 확인되었다
        (judge_results_v4c_ctrl.json, 5/5가 Phase 2 조건 C와 동일 판정).
        상세: handoff/phase3a_report.md §6.
        켜려면 include_facts=True 또는 run_pipeline.py --facts.

      ★ (v5d) include_facts=True일 때 **reality 게이트가 기본 적용**된다 —
        declared_in 사건의 reality_status가 actual/memory가 아닌 FACT
        (현재 FACT_023~026, 전부 EVENT_3_6 = document 유래)을 제외한다.
        해제하려면 include_undisclosed_facts=True 또는
        run_pipeline.py --include-undisclosed-facts.
  (c) 관련 엔티티들의 원장 스냅샷 (모든 필드 + since + certainty,
      (v3) 반복 전제 승격("_promoted") 항목과 그 관측 이력 포함)
  (d) 해당 인물들의 knowledge 목록 (info + via + since)
  (e) 관련 과거 사건 — retrieval_mode에 따라 달라지는 **유일한 절**
      "ers"(기본, 회귀 방지): ERS reranker top-k
      "full"(Phase 2 조건 A): 사건 코퍼스 전량, 요약+제목
      "full_source"(조건 B): 전량 + source_text
  (f) 판정 지시: 모순/설정누수/지식범위위반/정상 중 하나,
      근거 사건 ID 인용 필수, confidence 0.0~1.0, JSON 출력

- judge(event_id, prompt, mode)
  (1) mode="api"    : anthropic 라이브러리로 Claude 호출.
                      ANTHROPIC_API_KEY가 없으면 안내만 반환.
  (2) mode="manual" : 프롬프트를 judge_requests/<event_id>.txt로 저장.
                      사람(또는 별도 세션의 LLM)이 읽고 판정한 결과를
                      judge_results_v2.json에 넣으면 파이프라인이 병합한다.

판정 결과 형식 (judge_results_v2.json):
  { "<event_id>": {"verdict": "모순|설정누수|지식범위위반|정상",
                   "reason": "...", "evidence_event_ids": [...],
                   "confidence": 0.0~1.0, "judged_by": "..."} }
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from ledger import BASE, Ledger, load_registry, normalize

# 기본값은 v2 시절 경로다. 현재는 run_pipeline.py가 --tag에 따라
# 경로를 명시적으로 넘기므로 이 값들은 단독 실행 시의 폴백일 뿐이다.
# v1~v3 산출물은 archive/ 아래로 옮겼다 (archive/README.md 참조).
REQUEST_DIR = BASE / "archive" / "v2" / "judge_requests"
RESULTS_FILE = BASE / "archive" / "v2" / "judge_results_v2.json"

VERDICTS = ("모순", "설정누수", "지식범위위반", "정상")


# ---------------------------------------------------------------- 프롬프트

def _entities_of(event: dict) -> list[str]:
    ids = list(event.get("participant_character_ids", []))
    ids += event.get("witness_character_ids", [])
    ids += event.get("objects", [])
    ids += [a.get("entity") for a in event.get("assumed_states", [])]
    if event.get("location_id") and event["location_id"] != "unknown":
        ids.append(event["location_id"])
    seen, out = set(), []
    for i in ids:
        if i and i != "unknown" and i not in seen:
            seen.add(i)
            out.append(i)
    return out


def _snapshot_lines(ledger: Ledger, timeline: str, entity: str) -> list[str]:
    fields = ledger.state.get(timeline, {}).get(entity, {})
    if not fields:
        return [f"- {entity}: (원장에 상태 없음)"]
    lines = [f"- {entity}:"]
    for field, prov in fields.items():
        if field == "_promoted":
            continue
        if field == "possesses":
            for oid, p in prov.items():
                if p.get("status") == "removed":
                    # (v4) 잃은 소유는 지우지 않고 표시한다 — 판정자가 이력을 봐야 한다
                    lines.append(f"    possesses = {oid} [상실됨: {p.get('removed_by')}에서 소유를 잃음]"
                                 f" (취득 {p['since']}, certainty {p['certainty']})")
                else:
                    lines.append(f"    possesses = {oid} (since {p['since']}, certainty {p['certainty']})")
        elif field == "knows" and "accumulated" in prov:
            # (B-026) 누적 모드 — 획득 순 전 이력. legacy 원장에는 이 키가 없다
            for h in prov["accumulated"]:
                lines.append(f"    knows = {h['value']} (since {h['since']},"
                             f" certainty {h['certainty']})")
        else:
            # (v4) transient 상태는 라벨을 달아 그대로 싣는다 [수정 3]
            mark = ""
            if prov.get("volatility") == "transient":
                mark = (f" [일시적, 해소 사건 미기록 — 해소 시 {prov.get('decays_to')}]")
            lines.append(f"    {field} = {prov['value']} (since {prov['since']},"
                         f" certainty {prov['certainty']}){mark}")
    # (v3) 반복 전제 승격 항목 — 명시 값과 별도로, 관측 이력까지 판정 컨텍스트에 싣는다
    for field, p in fields.get("_promoted", {}).items():
        lines.append(f"    {field} [승격 전제] = {p['value']}"
                     f" (since {p['since']}, certainty inferred, source promoted_assumption)")
        for ob in p.get("observations", []):
            lines.append(f"        · 관측 {ob['event_id']}: {ob['assumes']}")
    return lines


def _judge_marks(findings: list[dict] | None) -> dict:
    """(v4) checker findings에서 (entity, field)별 [검수 요망] 마커를 만든다. [수정 7]

    needs_judge는 LLM 호출을 줄이는 게이트가 아니다 — 파이프라인은 모든 사건을
    judge에 보낸다. 이 플래그의 의미는 "판정자가 이 항목을 특히 주의해서 보라"는
    힌트이며, 그 힌트를 실제로 전달하는 것이 이 함수다.
    """
    marks: dict[tuple, list[str]] = {}
    for f in findings or []:
        if not f.get("needs_judge"):
            continue
        key = (f.get("entity"), f.get("field"))
        note = f.get("detail") or f.get("type")
        marks.setdefault(key, [])
        if note not in marks[key]:
            marks[key].append(note)
    return marks


RETRIEVAL_MODES = ("ers", "full", "full_source")

# ---------------------------------------------------------------- (b') facts

FACTS_FILE = BASE / "facts.json"

# (v5d) reality 게이트 — 원장의 불변식을 facts 층에도 적용한다.
#
# 원장(ledger.py)은 dream / vision / hearsay / document 사건의 state_change를
# 상태에 반영하지 않는다. "문서에 적혀 있다"와 "세계가 그러하다"는 다르고,
# 특히 "인물이 그것을 안다"와는 더 다르기 때문이다.
#
# Phase 3A에서 facts 층은 이 불변식을 우회했다. reality_status=document인
# EVENT_3_6에서 FACT_023~026이 승격되어 (b')절에 실렸고, 판정자가 그것을
# "세계의 사실 → 인물이 알 수 있는 정보"로 읽어 지식 범위 위반 3건을 놓쳤다
# (handoff/phase3a_report.md §6-4).
#
# 이 필터는 facts.json을 수정하지 않는다. **프롬프트에 싣는 시점에만** 거른다.
# facts.json은 Phase 3A에서 동결한 해시 그대로 보존된다.
REALITY_OK = ("actual", "memory")


def _event_reality(base: Path | None = None) -> dict[str, str]:
    """event_id → reality_status 맵 (episode_*.json에서 직접 읽는다)."""
    out: dict[str, str] = {}
    for path in sorted((base or BASE).glob("episode_0*.json")):
        for ev in json.loads(path.read_text(encoding="utf-8")).get("events", []):
            out[ev["event_id"]] = ev.get("reality_status")
    return out


def reality_gate(facts: list[dict], reality: dict[str, str] | None = None
                 ) -> tuple[list[dict], list[dict]]:
    """(통과한 FACT, 걸러진 FACT)로 나눈다.

    declared_in 사건이 **하나라도** actual/memory가 아니면 걸러낸다.
    보수적으로 잡는 이유: 미공개 정보가 한 번이라도 섞이면 그 FACT은
    "인물이 알 수 있는 정보"의 근거로 쓰일 수 없기 때문이다.
    """
    reality = reality if reality is not None else _event_reality()
    keep, drop = [], []
    for f in facts:
        st = [reality.get(d) for d in f.get("declared_in", [])]
        (keep if st and all(s in REALITY_OK for s in st) else drop).append(f)
    return keep, drop


def load_facts(path: Path | None = None) -> list[dict]:
    """facts.json 로드 (없으면 빈 리스트 — (b')절이 통째로 빠진다).

    reality 게이트는 여기서 적용하지 않는다 — build_judge_prompt가
    include_undisclosed_facts에 따라 적용한다.
    """
    p = path or FACTS_FILE
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8")).get("facts", [])


def facts_for_event(event: dict, facts: list[dict]) -> list[dict]:
    """(b')절 포함 규칙 — **검색하지 않는다** (Phase 3A 명세 §4).

        scope=world  → 전량 상시 포함 ((b)규칙과 동일 취급)
        scope=entity → subject_ids가 사건의 엔티티와 겹치면 포함
        scope=local  → subject_ids 또는 사건 location_id가 겹치면 포함

    `topic_keys` 매칭은 조회 함수(measure_retrieval_v5.facts_by_topic)로만
    구현하고 **포함 조건으로 쓰지 않는다.** 토큰 예산 영향을 먼저 재고
    도입 여부는 Phase 3B에서 판단한다.
    """
    ents = set(_entities_of(event))
    loc = event.get("location_id")
    out = []
    for f in facts:
        scope, subj = f.get("scope"), set(f.get("subject_ids") or [])
        if scope == "world":
            out.append(f)
        elif scope == "entity" and (subj & ents):
            out.append(f)
        elif scope == "local" and ((subj & ents) or (loc and loc in subj)):
            out.append(f)
    return out


def _fact_lines(f: dict, indent: str = "") -> list[str]:
    rules = f.get("related_rules") or []
    tail = f"{f['declared_in'][0]}에서 확립" if f.get("declared_in") else "출처 미상"
    if len(f.get("declared_in", [])) > 1:
        tail = f"{', '.join(f['declared_in'])}에서 확립"
    if rules:
        tail += f", {', '.join(rules)} 관련"
    sup = f.get("superseded_by")
    if sup:
        tail += f", **{sup}에서 갱신됨 — 현재는 유효하지 않다**"
    quote = f["source_text"]
    if not (quote.startswith('"') and quote.endswith('"')):
        quote = f'"{quote}"'
    return [
        f"{indent}- {f['fact_id']} [{f['certainty']}] ({tail}):",
        f"{indent}    {f['content']}",
        f"{indent}    근거 원문: {quote}",
    ]


def _section_e(related_events: list[dict], retrieval_mode: str,
               corpus: list[dict] | None) -> list[str]:
    """(e)절 생성 — **A/B/C 조건이 갈리는 유일한 지점이다.**

    (a)(b)(c)(d)(f)절은 3조건 모두 동일해야 비교가 성립하므로 건드리지 않는다.

    - "ers"         : 현행 동작. ERS reranker top-k + 점수 + stale_label
    - "full"        : 사건 코퍼스 전량을 시간순으로. ERS 점수·stale_label 없음
    - "full_source" : full + 각 사건의 source_text
    """
    lines: list[str] = []
    if retrieval_mode == "ers":
        lines.append("## (e) 관련 과거 사건 (ERS reranker top 선별)")
        if related_events:
            for r in related_events:
                stale = f"  [{r['stale_label']}]" if r.get("stale_label") else ""
                lines.append(f"- {r['event_id']} ({r.get('episode', '?')}화, ERS {r.get('ers')}){stale}")
                lines.append(f"    {r.get('title')}: {r.get('summary')}")
        else:
            lines.append("- (선별된 관련 사건 없음)")
        return lines

    # 전량 투입 — 검색을 끄고 판정 성능의 상한을 잰다.
    # 코퍼스가 34건(요약+제목 ≈1,900토큰)이라 가능한 실험이며,
    # 수백~수천 건 규모에서는 성립하지 않는다.
    with_source = retrieval_mode == "full_source"
    label = "원문 포함" if with_source else "요약"
    corpus = corpus or []
    lines.append(f"## (e) 과거 사건 **전량** ({len(corpus)}건, 시간순, {label})")
    lines.append("검색으로 선별하지 않았다. 지금까지의 모든 사건이 아래에 있다.")
    for p in corpus:
        lines.append(f"- {p['event_id']} ({p.get('episode', '?')}화, "
                     f"reality_status {p.get('reality_status')})")
        lines.append(f"    {p.get('title')}: {p.get('summary')}")
        if with_source and p.get("source_text"):
            lines.append(f"    [원문] {p['source_text']}")
    return lines


def build_judge_prompt(event: dict, ledger: Ledger, related_events: list[dict],
                       mapping: dict | None = None,
                       findings: list[dict] | None = None,
                       retrieval_mode: str = "ers",
                       corpus: list[dict] | None = None,
                       include_facts: bool = False,
                       facts: list[dict] | None = None,
                       include_undisclosed_facts: bool = False,
                       reality: dict[str, str] | None = None,
                       registry: dict | None = None) -> str:
    """판정 요청 프롬프트 문자열 생성.

    findings를 주면 checker가 needs_judge를 붙인 전제에 [검수 요망] 마커가 달린다.
    retrieval_mode가 "full"/"full_source"면 related_events를 무시하고
    corpus 전량을 (e)절에 싣는다 (Phase 2 상한 측정용).

    registry 미주입 시 루트 WM 레지스트리로 폴백한다 (WM 측정 스크립트 호환).
    제품 경로(weave_pipeline)는 반드시 프로젝트 레지스트리를 주입해야 한다 —
    미주입 시 타 작품 판정에 WM 세계관 규칙이 혼입된다 (2026-09-06 결함 수정).
    """
    if retrieval_mode not in RETRIEVAL_MODES:
        raise ValueError(f"retrieval_mode는 {RETRIEVAL_MODES} 중 하나여야 합니다: {retrieval_mode}")
    if registry is None or mapping is None:
        registry_loaded, mapping_loaded = load_registry()
        if registry is None:
            registry = registry_loaded
        if mapping is None:
            mapping = mapping_loaded
    event = normalize(event, mapping)
    timeline = event.get("timeline") or "unknown"

    lines = []
    lines.append("당신은 연재 소설의 서사 일관성 검수자다. 아래 신규 사건이 지금까지의")
    lines.append("세계관 규칙·상태 원장·과거 사건들과 모순되는지 판정하라.")
    lines.append("")

    # (a) 신규 사건
    lines.append("## (a) 신규 사건")
    lines.append(f"- event_id: {event.get('event_id')}")
    lines.append(f"- 제목: {event.get('title')}")
    lines.append(f"- 요약: {event.get('summary')}")
    lines.append(f"- 원문 발췌: {event.get('source_text')}")
    lines.append(f"- timeline: {timeline} / reality_status: {event.get('reality_status')}"
                 f" / 장소: {event.get('location_id')}")
    lines.append(f"- 참여자: {event.get('participant_character_ids')}")
    if event.get("assumed_states"):
        marks = _judge_marks(findings)
        lines.append("- assumed_states (이 사건이 성립하려면 참이어야 하는 전제):")
        lines.append("    ※ [검수 요망]는 구조 검사가 결론을 내리지 못하고 판정자에게")
        lines.append("       넘긴 항목이다. 특히 주의해서 보라.")
        for a in event["assumed_states"]:
            key = (a.get("entity"), a.get("field"))
            mark = "  [검수 요망]" if key in marks else ""
            lines.append(f"    · {a.get('entity')}.{a.get('field')} = {a.get('assumes')}{mark}"
                         f"  (근거 발췌: {a.get('source_text')})")
            for note in marks.get(key, []):
                lines.append(f"        └ 구조 검사 소견: {note}")
        # 구조적 전제(참여자 alive/location 등)에서 넘어온 검수 요망 항목
        premise_keys = {(a.get("entity"), a.get("field")) for a in event["assumed_states"]}
        extra = {k: v for k, v in marks.items() if k not in premise_keys}
        if extra:
            lines.append("- 구조 검사가 자동 생성한 전제 중 [검수 요망]:")
            for (ent, fld), notes in extra.items():
                lines.append(f"    · {ent}.{fld}  [검수 요망]")
                for note in notes:
                    lines.append(f"        └ {note}")
    else:
        lines.append("- assumed_states: (없음)")
    lines.append("")

    # (b) (v3) 세계관 규칙 전체 — 검색 없이 상시 포함
    # (v5) related_rules를 가진 FACT은 해당 RULE 바로 아래에 붙여 싣는다.
    #      (b')절에 별도 항목으로 두 번 싣지 않는다 (Phase 3A 명세 §4 중복 제거).
    pool = facts if facts is not None else load_facts()
    if include_facts and not include_undisclosed_facts:
        # (v5d) reality 게이트 — declared_in이 actual/memory가 아닌 FACT 제외
        pool, _dropped = reality_gate(pool, reality)
    selected = facts_for_event(event, pool) if include_facts else []
    under_rule: dict[str, list[dict]] = {}
    standalone: list[dict] = []
    for f in selected:
        rules = f.get("related_rules") or []
        if rules:
            under_rule.setdefault(rules[0], []).append(f)
        else:
            standalone.append(f)

    lines.append("## (b) 세계관 규칙 (registry 전체 — 상시 포함)")
    for rule in registry.get("worldview_rules", []):
        cert = rule.get("certainty", "explicit")
        lines.append(f"- {rule['id']} [{cert}] ({rule.get('first_episode', '?')}화): {rule['content']}")
        for f in under_rule.get(rule["id"], []):
            lines.extend(_fact_lines(f, indent="  "))
    lines.append("")

    # (b') (v5) 확립된 설정 사실 — facts 원장. (b)와 같이 검색 없이 싣는다.
    if include_facts:
        lines.append("## (b') 확립된 설정 사실 (facts 원장)")
        lines.append("사건 로그에서 파생한 설정 사실이다. 각 항목은 확립 사건과 원문 인용을")
        lines.append("가지므로, 근거로 인용할 때 그 사건 ID를 쓰면 된다.")
        lines.append("(위 (b)절 규칙 아래에 붙은 FACT은 여기 다시 싣지 않는다.)")
        if standalone:
            for f in standalone:
                lines.extend(_fact_lines(f))
        else:
            lines.append("- (이 사건에 해당하는 항목 없음)")
        lines.append("")

    # (c) 원장 스냅샷
    lines.append(f"## (c) 관련 엔티티 원장 스냅샷 (timeline={timeline})")
    for eid in _entities_of(event):
        lines.extend(_snapshot_lines(ledger, timeline, eid))
    lines.append("")

    # (d) knowledge 목록
    lines.append("## (d) 관련 인물의 knowledge (지식 획득 이력)")
    chars = [e for e in _entities_of(event) if e.startswith("CHAR_")]
    any_knowledge = False
    for cid in chars:
        knowledge = ledger.knowledge.get(cid, [])
        if not knowledge:
            continue
        any_knowledge = True
        lines.append(f"- {cid}: ({len(knowledge)}건)")
        for k in knowledge:
            # (v4) retained=False는 인물이 보유하지 않은 지식이다. 숨기지 않고
            # [미보유] 표시를 달아 싣는다 — 판정자가 "사실은 꿈에서 봤다"는
            # 맥락을 알아야 하기 때문이다. [수정 2]
            mark = "" if k.get("retained", True) else " [미보유 — 획득했으나 보유하지 않음]"
            lines.append(f"    · [{k['via']}, since {k['since']}] {k['info']}{mark}")
    if not any_knowledge:
        lines.append("- (기록 없음)")
    lines.append("")

    # (e) 관련 과거 사건 — 조건 A/B/C가 갈리는 유일한 절
    lines.extend(_section_e(related_events, retrieval_mode, corpus))
    lines.append("")

    # (f) 판정 지시
    lines.append("## (f) 판정 지시")
    lines.append("1. 다음 중 하나로 판정하라: 모순 / 설정누수 / 지식범위위반 / 정상")
    lines.append("   - 모순: 확립된 상태·세계관 규칙(RULE_xxx)·설정과 직접 충돌")
    lines.append("   - 설정누수: 아직 공개되지 않은 설정·정보가 새어 나옴")
    lines.append("   - 지식범위위반: 인물이 알 수 없는 것을 알거나, 아는 것을 모르는 척함")
    lines.append("   - 정상: 충돌 없음")
    lines.append("2. 근거가 되는 사건 ID를 반드시 인용하라 (원장 since 값 포함,")
    lines.append("   세계관 규칙 위반이면 RULE ID도 reason에 명시).")
    lines.append("3. confidence를 0.0~1.0으로 제시하라.")
    lines.append("4. 의도된 전개일 가능성(복선, 점진적 변화)이 보이면 reason에 언급하고")
    lines.append("   confidence를 낮춰라.")
    lines.append("5. 출력은 아래 JSON 형식만:")
    lines.append('   {"verdict": "...", "reason": "...", "evidence_event_ids": ["..."], "confidence": 0.0}')
    return "\n".join(lines)


# ---------------------------------------------------------------- 판정 실행

def judge(event_id: str, prompt: str, mode: str = "manual",
          request_dir: Path | None = None) -> dict:
    """
    mode="api"    : anthropic 라이브러리로 Claude 호출 (키 없으면 안내 반환)
    mode="manual" : <request_dir>/<event_id>.txt로 프롬프트 저장 (기본 judge_requests/)
    """
    if mode == "api":
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            return {
                "status": "unavailable",
                "message": "ANTHROPIC_API_KEY 환경변수가 없습니다. "
                           "키를 설정하거나 mode='manual'로 프롬프트를 저장한 뒤 "
                           "판정 결과를 judge_results_v2.json에 넣어주세요.",
            }
        try:
            import anthropic  # type: ignore
        except ImportError:
            return {
                "status": "unavailable",
                "message": "anthropic 라이브러리가 설치되어 있지 않습니다. "
                           "`pip install anthropic` 후 다시 시도하세요.",
            }
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        try:
            start, end = text.find("{"), text.rfind("}") + 1
            result = json.loads(text[start:end])
            result["status"] = "ok"
            result["judged_by"] = "api"
            return result
        except Exception:
            return {"status": "parse_error", "raw": text}

    # manual 모드
    rdir = request_dir or REQUEST_DIR
    rdir.mkdir(exist_ok=True)
    path = rdir / f"{event_id}.txt"
    path.write_text(prompt, encoding="utf-8")
    return {"status": "saved", "path": str(path),
            "message": "프롬프트 저장됨 — 판정 결과를 judge_results 파일에 병합하세요."}


def load_results(path: Path | None = None) -> dict:
    """수동/외부 판정 결과 로드 (없으면 빈 dict). 기본 파일: judge_results_v2.json"""
    p = path or RESULTS_FILE
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {}
