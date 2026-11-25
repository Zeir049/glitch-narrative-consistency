# -*- coding: utf-8 -*-
"""
validator.py — replay 검증기

재생 중 각 state_change의 "from" 값이 원장의 현재값과 일치하는지 검사한다.
불일치는 경고 리포트에 기록된다: (사건 ID, 엔티티, 필드, 기대값, 실제 원장값).

판정 방식
---------
- 원장에 아직 값이 없으면(첫 기록) 검사하지 않는다.
- "from"이 "unknown"이면 검사하지 않는다 (추출기가 이전 값을 모른다고 선언한 것).
- location 특례: expected_from이 그 사건의 location_id와 일치하면
  "장면 도착에 의한 암묵 이동"으로 판정해 level="soft"로 강등한다.
  (인물이 장면 시작 시점에 이미 사건 장소로 이동해 있었던 것 —
   이동 사건 미기록은 서술 관례이지 모순이 아님)
- 그 외 alive / location : 정규화 후 정확히 일치해야 함 → 불일치 시 level="hard"
- body_status / ability (자유 서술) : 표현이 다를 수 있으므로 토큰(단어) 교집합이
  하나도 없을 때만 level="soft" 경고 (예: "로봇 신체 정상" vs
  "로봇 신체 α-0760"은 '로봇','신체'가 겹치므로 일관된 것으로 본다)
- possesses : 집합형 필드이므로 from 검사는 생략한다.

예상 검출 사례: EVENT_5_8에서 CHAR_006의 alive from="true"이지만,
원장상 EVENT_4_4 이후 "unconscious"다 (4화 실신 후 '각성' 사건이 로그에
누락되어 있으므로). validator가 이걸 잡아내면 정상 작동이다.

사용법:  python validator.py  → 전체 재생 + validation_report.json 생성
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ledger import ENTITY_FIELD_SCHEMA, BASE, Ledger, compile_ledger, norm_alive

_TOKEN = re.compile(r"[A-Za-z가-힣0-9]+")


class Validator:
    """replay 도중 apply_event()가 state_change마다 check_schema() / check()를 호출한다."""

    def __init__(self):
        self.warnings: list[dict] = []

    # ------------------------------------------------------------------
    def check_schema(self, ev: dict, sc: dict):
        """(v4) 엔티티 타입별 허용 필드 검사. [수정 6]

        장소에 신체 상태(body_status)가 붙는 것 같은 타입 오류를 잡는다.
        ALLOWED_FIELDS는 필드명만 보므로 'LOC_011.body_status'를 통과시킨다.

        위반은 level="schema_error"로 기록하되 재생은 계속한다 —
        로그가 원천이므로 재생을 죽이면 안 된다.
        정식 ID 접두(CHAR_/LOC_/OBJ_)가 아닌 엔티티(NEW: 미매핑 라벨 등)는
        타입을 알 수 없으므로 검사하지 않는다.
        """
        entity = str(sc.get("entity", ""))
        field = sc.get("field")
        for prefix, allowed in ENTITY_FIELD_SCHEMA.items():
            if not entity.startswith(prefix):
                continue
            if field not in allowed:
                self.warnings.append({
                    "level": "schema_error",
                    "event_id": ev.get("event_id"),
                    "timeline": ev.get("timeline"),
                    "entity": entity,
                    "field": field,
                    "expected_from": sc.get("from"),
                    "ledger_value": None,
                    "ledger_since": None,
                    "note": f"엔티티 타입 {prefix}* 에 허용되지 않는 필드 "
                            f"'{field}' (허용: {sorted(allowed)})",
                })
            return

    # ------------------------------------------------------------------
    def check(self, ledger: Ledger, timeline: str, ev: dict, sc: dict):
        field = sc.get("field")
        expected = sc.get("from")

        if field == "possesses":            # 집합형 필드: from 검사 생략
            return
        if expected in (None, "", "unknown"):
            return

        current = ledger.get(timeline, sc.get("entity"), field)
        if current is None:                 # 원장에 값 없음 → 첫 기록, 검사 불가
            return
        cur_value = current.get("value")

        if field == "alive":
            if norm_alive(expected) != norm_alive(cur_value):
                self._warn("hard", timeline, ev, sc, expected, current)
        elif field == "location":
            if str(expected) != str(cur_value):
                if str(expected) == str(ev.get("location_id")):
                    # 사건이 주장하는 이전 위치 == 사건 자신의 장소:
                    # 인물이 장면 시작 전에 그 장소로 이동해 있었던 것.
                    self._warn("soft", timeline, ev, sc, expected, current,
                               note="장면 도착에 의한 암묵 이동 — 이동 사건 미기록은 서술 관례로 해석")
                else:
                    self._warn("hard", timeline, ev, sc, expected, current)
        else:  # body_status / ability / knows — 자유 서술
            a = set(_TOKEN.findall(str(expected)))
            b = set(_TOKEN.findall(str(cur_value)))
            if not (a & b):
                self._warn("soft", timeline, ev, sc, expected, current)

    # ------------------------------------------------------------------
    def warn_knows_op(self, timeline: str, ev: dict, sc: dict, missing_value: bool = False):
        """(B-026) knows 누적 모드에서 add가 아닌 op(set/remove 등)를 add로 강제했음을 기록.
        missing_value=True면 to 값이 없어 누적에서 제외했음을 기록.
        legacy 모드에서는 호출되지 않는다."""
        if missing_value:
            self.warnings.append({
                "level": "knows_value_missing", "event_id": ev.get("event_id"),
                "timeline": timeline, "entity": sc.get("entity"), "field": "knows",
                "expected_from": sc.get("from"), "ledger_value": None, "ledger_since": None,
                "note": "knows state_change에 to 값 없음 — 누적에서 제외 (legacy는 None으로 덮어씀)",
            })
            return
        self.warnings.append({
            "level": "knows_op_coerced",
            "event_id": ev.get("event_id"),
            "timeline": timeline,
            "entity": sc.get("entity"),
            "field": "knows",
            "expected_from": sc.get("from"),
            "ledger_value": None,
            "ledger_since": None,
            "note": f"knows는 add 전용 — op '{sc.get('op')}'를 add로 처리 (이전 지식 보존)",
        })

    # ------------------------------------------------------------------
    def _warn(self, level, timeline, ev, sc, expected, current,
              note="사건의 from 값과 원장 현재값 불일치 — 중간 사건 누락 또는 추출 오류 가능"):
        self.warnings.append({
            "level": level,                       # hard=명백한 불일치 / soft=설명 가능한 불일치
            "event_id": ev.get("event_id"),
            "timeline": timeline,
            "entity": sc.get("entity"),
            "field": sc.get("field"),
            "expected_from": expected,            # 사건이 주장하는 이전 값
            "ledger_value": current.get("value"), # 원장의 실제 현재값
            "ledger_since": current.get("since"), # 그 값의 근거 사건
            "note": note,
        })

    # ------------------------------------------------------------------
    def report(self) -> dict:
        by_level: dict[str, int] = {}
        for w in self.warnings:
            by_level[w["level"]] = by_level.get(w["level"], 0) + 1
        return {
            "warning_count": len(self.warnings),
            "by_level": by_level,
            "warnings": self.warnings,
        }

    def print_report(self):
        if not self.warnings:
            print("replay 검증: 경고 0건")
            return
        print(f"replay 검증: 경고 {len(self.warnings)}건")
        for w in self.warnings:
            print(f"  [{w['level'].upper()}] {w['event_id']} | {w['entity']}.{w['field']}")
            print(f"        사건의 from : {w['expected_from']!r}")
            print(f"        원장 현재값 : {w['ledger_value']!r} (since {w['ledger_since']})")


if __name__ == "__main__":
    validator = Validator()
    compile_ledger(validator=validator)
    validator.print_report()
    # 기존 validation_report{,_v2,_v3}.json은 발표 자료로 보존 — 새 결과는 _v4로 저장
    (BASE / "validation_report_v4.json").write_text(
        json.dumps(validator.report(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("→ validation_report_v4.json 저장")
