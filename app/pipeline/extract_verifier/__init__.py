"""① 추출 관문 옆 결정적 추출 검증기 (LLM 0회) — 등급을 바꾸지 않는 표시(mark) 신호만 낸다.

출처: glitch_extract_verifier(별도 저장소) `verifier/` 에서 `_core.py`(vendor 경로)·`corpus.py`(코퍼스 로더)를 뺀 것.
병합 제안서 docs/merge_proposal.md 안 B — 신호는 등급 파일의 옆 키(`own_marks`·`state_changes[].marks`·
`episode_marks`·`promotion_candidates`)에 들어가고 `grade`·`own_reasons`·`tentative_event_ids` 는 그대로다.
그래서 extract_gate.filter_event(WEAVE_GATE_MODE=exclude)·grade_of·attrs.trust 는 영향을 받지 않는다.

설정 WEAVE_VERIFIER=off|annotate(기본 off). WEAVE_GATE_MODE=off 면 무시한다. 연결은 app/pipeline/weave.py `_verify_into`.
라벨·평가 코퍼스를 읽지 않는다. 코어(weave_core)는 호출만 한다.

  report.py    verify_episode(doc, confirmed_eps, mapping, registry=, text=) → VerifyResult, SIGNALS
  merge.py     merge_verifier(gate_dict, vres) → 새 dict(입력 불변), screen_bundle(gate_dict) → 검수 화면 묶음
  preserve.py  assumed_only_persistent · declaration_only_persistent · assumed_only_exclusive(실험)
  numbers.py   number_lost      entities.py  entity_uncovered      rank.py  승격 후보 priority(high|mid|low)
  kor.py       한국어 수치·단위·어간 규칙      items.py  항목·패킷 운반 칸 모으기
"""
VERIFIER_VERSION = 1
