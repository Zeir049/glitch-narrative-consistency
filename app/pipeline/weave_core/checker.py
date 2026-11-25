# -*- coding: utf-8 -*-
"""
checker.py — 모순 대조기 (v4)

신규 사건 1개와 현재 원장을 받아 판정 결과(findings) 리스트를 반환한다.

심각도 3단계
------------
- "강한 모순"  : 원장의 강한 근거와 정면 충돌
                 (alive=false 참여, 강한 등급 via의 지식범위위반, 상실한 물건 요구)
- "약한 의심"  : 저절로 해소될 수 있거나 근거 등급이 중간인 충돌.
                 예) 근거 via가 inference인 지식범위위반 — 추론은 잊거나 번복될 수 있음
- "참고"       : 충돌이 아닌 관찰 (통과 근거, 정보 없음, 이동 간주, transient 상태 등).
                 needs_judge=True가 붙은 참고 항목은 LLM 2차 검수(judge) 대상

v4 변경 (Phase 1)
-----------------
[수정 1] 순방향 knows 미매칭 → 약한 의심에서 참고(no_info)로 강등.
        원장 knowledge는 명시 기록만 담는 부분집합이므로 미매칭이 모순의 근거가
        될 수 없다. 역방향(not_knows)은 논리가 반대이므로 그대로 둔다.
[수정 2] via별 근거 등급(VIA_STRENGTH)을 도입해 not_knows 위반 심각도를 결정한다.
        retained=False 지식은 매칭 근거에서 제외한다(judge에는 [미보유]로 실림).
[수정 3] volatility="transient" 상태는 구조적 전제 위반으로 쓰지 않고
        참고 + needs_judge로 내려보낸다. 사망은 persistent다.
[수정 4] 상실한 소유(status="removed")를 전제가 요구하면 강한 모순.
[수정 7] needs_judge는 LLM 호출을 줄이는 게이트가 아니라, judge에게 "이 항목을
        특히 주의해서 보라"고 표시하는 힌트다. 현재 파이프라인은 전량 전송한다.

검사 항목
---------
1. assumed_states 대조 — 원장에 정보가 없으면 통과(참고).
   - possesses : 전제된 OBJ ID가 소유 목록에 있으면 근거와 함께 통과
   - body_status / ability : (B-002) 결정론적 판정을 하지 않는다.
     원장값·since를 근거로 붙여 참고 + needs_judge=True로 표시하고
     LLM 2차 검수(judge.py)에 넘긴다. 자유 서술 모순은 judge의 몫이다.
   - knows : 인물의 knowledge 목록과 키워드 매칭(조사 등 한국어 어미를 고려한
     부분 문자열 토큰 매칭). 근거를 찾으면 통과(참고), 못 찾아도 참고(no_info)
     + needs_judge — 원장 knowledge는 부분집합이므로 미매칭이 모순은 아니다.
   - "not_knows:" 접두 규약(역방향 검사): 인물이 모른다고 전제된 정보가
     knowledge 목록에 이미 있으면 → "지식범위위반".
     심각도는 근거의 via 등급으로 결정 (strong 강한 모순 / medium 약한 의심 /
     weak 참고+judge)
2. 구조적 전제 자동 생성 (참여자별)
   - alive: 원장이 "false"면 강한 모순, "unconscious"이고 persistent면 약한 의심,
     transient면 참고 + needs_judge
   - location: 사건 장소와 다르면 — 이동 가능 상태면 참고(이동 간주),
     unconscious+persistent면 약한 의심, transient면 참고, false(사망)면 강한 모순

사용법:  python checker.py  → 원장 재생 후 test_episode_06_v2.json 검사,
        conflict_report_v4.json 저장 (기존 conflict_report*.json은 보존)
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ledger import (BASE, VOL_TRANSIENT, Ledger, compile_ledger, extract_ids,
                    load_registry, norm_alive, normalize)

SEV_HARD = "강한 모순"
SEV_SOFT = "약한 의심"
SEV_INFO = "참고"

NOT_KNOWS_PREFIX = "not_knows:"

# (v4) via별 근거 등급 [수정 2]
# 같은 "안다"라도 근거력이 다르다. 꿈에서 본 것과 직접 들은 것을 같은 무게로
# 다루면 안 된다. 역방향(not_knows) 위반 시 심각도를 이 등급이 결정한다.
VIA_STRENGTH = {
    "direct": "strong",     # 직접 관찰
    "told": "strong",       # 들음
    "document": "strong",   # 문서로 읽음
    "inference": "medium",  # 스스로 추론 — 잊거나 틀릴 수 있음
    "dream": "weak",        # 꿈
    "vision": "weak",       # 환영
}
VIA_DEFAULT_STRENGTH = "medium"

# 역방향(not_knows) 위반 시 근거 등급 → 심각도
NOT_KNOWS_SEVERITY = {
    "strong": SEV_HARD,
    "medium": SEV_SOFT,
    "weak": SEV_INFO,
}


def via_strength(via) -> str:
    return VIA_STRENGTH.get(str(via), VIA_DEFAULT_STRENGTH)

# (B-002, 제품화 시점 제거) 자유 서술 모순용 정규식 휴리스틱
# `CONTRADICTION_RULES` 5개와 `text_contradicts()`를 삭제했다.
#
# 왜 제거하는가
#   - 원작(WM)에 맞춰 사후적으로 짠 규칙이라 다른 작품에 일반화되지 않는다.
#     제품은 "아무 작품이나 넣으면 도는" 것이 목표이므로 작품 특화 상수는
#     오히려 오탐·미탐의 원인이 된다.
#   - 30장면 자유 서술 전제 15회 검사에서 발화 0회였다 (handoff/backlog.md B-002).
#   - Phase 2에서 judge가 정규식 도움 없이 자유 서술 신체 모순을 잡는 것이
#     세 조건(A/B/C) 모두에서 확인됐다 — scene_02 "오른손 정밀 필기",
#     confidence 0.55 / 0.80 / 0.75, 근거 사건(EVENT_4_4·EVENT_5_7)도 정확히 인용.
#
# 무엇이 달라지는가
#   자유 서술 필드(body_status/ability)의 전제는 이제 **전부** 참고 + needs_judge로
#   내려간다. checker가 자유 서술로 강한 모순을 내는 경로가 사라진다.
#   구조 검사(alive/location/possesses/knows)와 not_knows 역방향 검사는 그대로다.
#   회귀 기대값 변화는 test_phase1_gates.py의 test_free_text_goes_to_judge 참조.

_TOKEN = re.compile(r"[가-힣A-Za-z0-9]{2,}")


# ---------------------------------------------------------------- 유틸

def _tokens(text: str) -> set:
    return set(_TOKEN.findall(str(text)))


def _token_match(a: str, b: str) -> int:
    """한국어 조사/어미를 고려한 느슨한 토큰 매칭: 겹치는 토큰 수.
    토큰끼리 부분 문자열 관계('이름' ⊂ '이름이')면 일치로 본다."""
    ta, tb = _tokens(a), _tokens(b)
    count = 0
    for x in ta:
        if any(x in y or y in x for y in tb):
            count += 1
    return count


def knowledge_match(premise: str, knowledge: list) -> dict | None:
    """전제된 정보가 knowledge 목록에 있는지 키워드 매칭.
    전제 토큰의 절반 이상(최소 2개)이 매칭되는 항목 중 최고를 반환.

    (v4) retained=False 항목은 근거로 쓰지 않는다 — 순방향("안다")의 근거도,
    역방향("모른다")의 반증도 될 수 없다. 원문이 "기억나지 않는다"고 못 박은
    지식을 근거로 모순을 선언하면 그건 시스템의 오류다. [수정 2]
    판정자에게는 숨기지 않는다 — judge 프롬프트에 [미보유]로 실린다.
    """
    p_tokens = _tokens(premise)
    if not p_tokens:
        return None
    threshold = max(2, (len(p_tokens) + 1) // 2)
    best, best_n = None, 0
    for k in knowledge:
        if not k.get("retained", True):
            continue
        n = _token_match(premise, k.get("info", ""))
        if n >= threshold and n > best_n:
            best, best_n = k, n
    return best


def _severity_by_certainty(provenance: dict) -> str:
    return SEV_HARD if provenance.get("certainty") == "explicit" else SEV_SOFT


def _finding(severity, kind, entity, field, premise, ledger_value=None,
             since=None, detail="", needs_judge=False):
    return {
        "severity": severity,          # 강한 모순 / 약한 의심 / 참고
        "type": kind,                  # assumed_state / structural_alive / structural_location
                                       # / knowledge / knowledge_violation / pass / no_info
        "entity": entity,
        "field": field,
        "premise": premise,            # 사건이 전제하는 값
        "ledger_value": ledger_value,  # 원장의 실제 값 (없으면 None)
        "since": since,                # 원장값/지식의 근거 사건 ID
        "detail": detail,
        # (v4) [수정 7] needs_judge는 게이트가 아니라 힌트다.
        # run_pipeline.py는 이 플래그와 무관하게 *모든* 사건을 judge에 보낸다.
        # 의미: "판정자가 이 항목을 특히 주의해서 보라" — judge 프롬프트에
        # [검수 요망] 마커로 실린다. (Phase 2에서 판정 상한을 재야 하므로
        # 전량 전송이 현재로선 옳다)
        "needs_judge": needs_judge,
    }


# ---------------------------------------------------------------- 본체

def check_event(event: dict, ledger: Ledger, mapping: dict | None = None) -> list[dict]:
    """신규 사건 1개를 원장과 대조해 findings 리스트를 반환한다."""
    if mapping is None:
        _, mapping = load_registry()
    event = normalize(event, mapping)

    timeline = event.get("timeline") or "unknown"
    loc = event.get("location_id")
    findings: list[dict] = []

    # ---------- 1) 명시적 assumed_states 대조 ----------
    for a in event.get("assumed_states", []):
        entity, field = a.get("entity"), a.get("field")
        premise = str(a.get("assumes", "")).strip()

        # --- possesses ---
        if field == "possesses":
            wanted = extract_ids(premise, prefix="OBJ_")
            owned = ledger.possesses(timeline, entity)
            for oid in wanted:
                p = owned.get(oid)
                if p is None:
                    findings.append(_finding(
                        SEV_INFO, "no_info", entity, field, oid,
                        detail="원장에 소유 기록 없음 → 통과"))
                elif p.get("status") == "removed":
                    # (v4) 잃은 물건을 전제가 요구함 → 강한 모순 [수정 4]
                    findings.append(_finding(
                        SEV_HARD, "possesses_removed", entity, field, oid,
                        ledger_value=f"{oid} [상실]", since=p.get("removed_by", p.get("since")),
                        detail=f"원장상 {p.get('removed_by')}에서 소유를 잃은 물건을 "
                               f"전제가 요구함 (취득 {p.get('since')})"))
                else:
                    findings.append(_finding(
                        SEV_INFO, "pass", entity, field, oid,
                        ledger_value=oid, since=p["since"],
                        detail="원장 소유 기록과 일치"))
            continue

        # --- alive (명시적 전제) ---
        if field == "alive":
            cur = ledger.get(timeline, entity, "alive")
            if cur is None:
                findings.append(_finding(SEV_INFO, "no_info", entity, field, premise))
            elif norm_alive(premise) != norm_alive(cur["value"]):
                cur_v = norm_alive(cur["value"])
                if cur["value"] is not None and cur.get("volatility") == VOL_TRANSIENT:
                    # (v4) transient 상태는 구조적 전제 위반으로 쓰지 않는다 [수정 3]
                    findings.append(_finding(
                        SEV_INFO, "transient_state", entity, "alive", premise,
                        ledger_value=cur["value"], since=cur["since"],
                        detail="transient 상태 — 해소 사건이 로그에 없음",
                        needs_judge=True))
                else:
                    sev = SEV_SOFT if cur_v == "unconscious" else SEV_HARD
                    findings.append(_finding(
                        sev, "assumed_state", entity, "alive", premise,
                        ledger_value=cur["value"], since=cur["since"],
                        detail="각성 장면 누락 가능성" if cur_v == "unconscious" else ""))
            continue

        # --- knows (작업 3: 지식 범위 검수) ---
        if field == "knows":
            knowledge = ledger.knowledge.get(entity, [])
            if premise.startswith(NOT_KNOWS_PREFIX):
                # 역방향 검사: "모른다"가 전제인데 이미 아는 정보면 지식범위위반
                info = premise[len(NOT_KNOWS_PREFIX):].strip()
                hit = knowledge_match(info, knowledge)
                if hit:
                    # (v4) 심각도는 근거의 via 등급이 결정한다 [수정 2]
                    strength = via_strength(hit.get("via"))
                    sev = NOT_KNOWS_SEVERITY[strength]
                    note = {
                        "strong": "",
                        "medium": " — 추론으로 얻은 지식이므로 잊거나 번복될 수 있어 약한 의심으로 강등",
                        "weak": " — 근거가 꿈/환영이므로 참고로 강등, judge 확인 필요",
                    }[strength]
                    findings.append(_finding(
                        sev, "knowledge_violation", entity, "knows",
                        premise, ledger_value=hit["info"], since=hit["since"],
                        detail=f"이미 아는 정보를 모르는 것처럼 행동 "
                               f"(via {hit['via']}, 근거 등급 {strength}) — 지식 범위 위반{note}",
                        needs_judge=(strength != "strong")))
                else:
                    findings.append(_finding(
                        SEV_INFO, "pass", entity, "knows", premise,
                        detail="knowledge 목록에 해당 정보 없음 → '모른다'는 전제와 일관"))
            else:
                # 순방향 검사: 아는 것이 전제 — 근거가 있으면 통과
                hit = knowledge_match(premise, knowledge)
                if hit:
                    findings.append(_finding(
                        SEV_INFO, "pass", entity, "knows", premise,
                        ledger_value=hit["info"], since=hit["since"],
                        detail=f"지식 획득 근거 확인 (via {hit['via']})"))
                else:
                    # (v4) 순방향 미매칭은 의심이 아니라 참고다. [수정 1]
                    # 원장의 knowledge는 로그에 knowledge_gained가 *명시된* 것만
                    # 담는다. 인물이 스스로 관찰해서 아는 것, 상식, 직전 문장에서
                    # 알게 된 것은 들어오지 않는다. 즉 원장 knowledge는 인물이
                    # 아는 것의 부분집합이다. 부분집합에 없다는 이유로 의심을
                    # 거는 것은 구조적 오탐이다.
                    findings.append(_finding(
                        SEV_INFO, "no_info", entity, "knows", premise,
                        detail="knowledge 목록에 획득 기록 없음 — 원장 knowledge는 "
                               "명시 기록만 담는 부분집합이므로 미매칭이 곧 모순은 아님. "
                               "judge 확인 대상.",
                        needs_judge=True))
            continue

        # --- location (명시적 전제) ---
        if field == "location":
            cur = ledger.get(timeline, entity, "location")
            if cur is None:
                findings.append(_finding(SEV_INFO, "no_info", entity, field, premise))
            elif str(cur["value"]) != premise and premise.find(str(cur["value"])) < 0:
                findings.append(_finding(
                    _severity_by_certainty(cur), "assumed_state", entity, "location",
                    premise, ledger_value=cur["value"], since=cur["since"]))
            continue

        # --- body_status / ability — 자유 서술 ---
        cur = ledger.get(timeline, entity, field)
        if cur is None:
            findings.append(_finding(SEV_INFO, "no_info", entity, field, premise))
            continue
        # (B-002) 자유 서술 전제는 결정론적으로 판정하지 않는다.
        # 원장값을 근거로 붙여 judge(LLM)에게 넘긴다.
        findings.append(_finding(
            SEV_INFO, "pass", entity, field, premise,
            ledger_value=cur["value"], since=cur["since"],
            detail="자유 서술 전제 — 판정은 judge(LLM) 대상",
            needs_judge=True))

    # ---------- 2) 구조적 전제 자동 생성 (참여자별) ----------
    for ch in event.get("participant_character_ids", []):
        if not ch or ch == "unknown":
            continue

        # (a) alive == true 전제
        cur_alive = ledger.get(timeline, ch, "alive")
        alive_v = norm_alive(cur_alive["value"]) if cur_alive else None
        # (v4) transient 상태는 구조적 전제 위반의 근거로 쓰지 않는다 [수정 3]
        alive_transient = bool(cur_alive) and cur_alive.get("volatility") == VOL_TRANSIENT
        if alive_v == "false":
            # 사망은 persistent다 — transient 표기가 있어도 강한 모순으로 다룬다.
            findings.append(_finding(
                SEV_HARD, "structural_alive", ch, "alive", "true",
                ledger_value=cur_alive["value"], since=cur_alive["since"],
                detail="사망한 인물이 사건에 참여"))
        elif alive_v != "true" and alive_v is not None:
            if alive_transient:
                findings.append(_finding(
                    SEV_INFO, "transient_state", ch, "alive", "true",
                    ledger_value=cur_alive["value"], since=cur_alive["since"],
                    detail=f"transient 상태 — 해소 사건이 로그에 없음 "
                           f"(해소 시 {cur_alive.get('decays_to')}). 구조적 전제 위반으로 보지 않음",
                    needs_judge=True))
            elif alive_v == "unconscious":
                findings.append(_finding(
                    SEV_SOFT, "structural_alive", ch, "alive", "true",
                    ledger_value=cur_alive["value"], since=cur_alive["since"],
                    detail="약한 의심 — 각성 장면 누락 가능성 (의식 없음은 수면·진정처럼 저절로 풀릴 수 있음)"))

        # (b) location == 사건 location_id 전제
        if loc and loc != "unknown":
            cur_loc = ledger.get(timeline, ch, "location")
            if cur_loc is None or str(cur_loc["value"]) == str(loc):
                continue
            if alive_v == "false":
                findings.append(_finding(
                    SEV_HARD, "structural_location", ch, "location", loc,
                    ledger_value=cur_loc["value"], since=cur_loc["since"],
                    detail="사망 상태인데 다른 장소에 등장"))
            elif alive_v == "unconscious" and alive_transient:
                # (v4) 실신이 transient면 이송 여부를 구조로 따질 근거가 없다 [수정 3]
                findings.append(_finding(
                    SEV_INFO, "transient_state", ch, "location", loc,
                    ledger_value=cur_loc["value"], since=cur_loc["since"],
                    detail="transient 상태(의식 없음) — 해소 사건이 로그에 없어 "
                           "이송/자력 이동을 구분할 수 없음. 구조적 전제 위반으로 보지 않음",
                    needs_judge=True))
            elif alive_v == "unconscious":
                findings.append(_finding(
                    SEV_SOFT, "structural_location", ch, "location", loc,
                    ledger_value=cur_loc["value"], since=cur_loc["since"],
                    detail="의식 없는 상태의 위치 이동 — 이송 장면 누락 가능성"))
            else:
                findings.append(_finding(
                    SEV_INFO, "movement", ch, "location", loc,
                    ledger_value=cur_loc["value"], since=cur_loc["since"],
                    detail="이동 가능 상태이므로 충돌 아님 (이동 간주)"))

    return findings


# ---------------------------------------------------------------- 출력

def conflicts_of(findings: list[dict]) -> list[dict]:
    """참고 항목을 제외한 실제 충돌(강한 모순/약한 의심)만."""
    return [f for f in findings if f["severity"] != SEV_INFO]


def print_findings(title: str, findings: list[dict]):
    conflicts = conflicts_of(findings)
    print(f"\n=== {title} ===")
    print(f"충돌 {len(conflicts)}건 (강한 모순 "
          f"{sum(1 for c in conflicts if c['severity'] == SEV_HARD)}, "
          f"약한 의심 {sum(1 for c in conflicts if c['severity'] == SEV_SOFT)})")
    for c in conflicts:
        print(f"  [{c['severity']}] ({c['type']}) {c['entity']}.{c['field']}")
        print(f"      전제값 : {c['premise']}")
        if c["ledger_value"] is not None:
            print(f"      원장값 : {c['ledger_value']}  (근거: since {c['since']})")
        if c["detail"]:
            print(f"      판정   : {c['detail']}")
    for f in findings:
        if f["severity"] == SEV_INFO:
            tag = " [judge 대상]" if f["needs_judge"] else ""
            base = f"  · 참고({f['type']}){tag} {f['entity']}.{f['field']}: {f['premise']}"
            if f["since"]:
                base += f" — 근거 since {f['since']}"
            print(base)


if __name__ == "__main__":
    ledger = compile_ledger()
    _, mapping = load_registry()
    tests = json.loads((BASE / "test_episode_06_v2.json").read_text(encoding="utf-8"))

    report = {}
    for ev in tests["events"]:
        findings = check_event(ev, ledger, mapping)
        print_findings(f"{ev['event_id']} — {ev['title']}", findings)
        report[ev["event_id"]] = {"title": ev["title"], "findings": findings}

    # 기존 conflict_report{,_v2}.json은 발표 자료로 보존 — 새 결과는 _v4로 저장
    (BASE / "conflict_report_test06_v4.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n→ conflict_report_test06_v4.json 저장")
