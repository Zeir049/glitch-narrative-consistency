# 검증 기록 — 추출 검증기 병합 브랜치 (`claude/extract-verifier-merge`)

2026-11-07, 설계·판단 세션(Opus 5.5)이 구현 세션(extract-verifier) 보고를 저장소를 열어 직접 확인했다. 사용자 승인 뒤 main **a459fee**(`--no-ff`)로 병합. push 안 함.
범위·결정 근거: [`extract-verifier-brief.md`](extract-verifier-brief.md) §8, 검증기 저장소 `C:\glitch_extract_verifier\docs\merge_proposal.md` §2~§7.

## 0. 선행 — 패킷 재덤프·T1 재확인 (검증기 저장소, 커밋 c274390 → d0da385 → d555981)

| 항목 | 결과 | 꼬리표 |
|---|---|---|
| 사전 기록 | c274390이 코드보다 먼저, 문서 1개만 바꿈 | [측정] |
| `scripts/packets.py` | main `collect_episode_rules`(04e7bf8, `judge_eval.py:302`)와 로직 줄 단위 동일, `app/` import 없음 | [측정] |
| 테스트 | 76 통과(62 + 규칙 조립 14) — 판단 세션 재실행 | [측정] |
| 패킷 diff | 13개 파일, 지운 줄 0, 추가 42줄 전부 (b)절, 내용은 사전 기록의 규칙 4개(Q1 `NEW:열쇠_관리_규칙@ep3`, Q3 `RULE_007@ep4`·`RULE_007@ep5`·`RULE_008@ep5`)뿐 | [측정] |
| T1 13행 | 분류 변동 0/13. CSV는 열 하나 추가, 기존 열 동일. 판독은 구현 세션 — 판단 세션은 Q3 s04(부분 운반, 이미 판정자 미탐)·Q1 s22(다른 사실) 2행만 원문 대조 | [측정, 구현 세션 판독] |
| 못 한 것 | 재덤프 두 번 바이트 비교(그 저장소 파일을 덮어쓰므로 안 함) | — |

## 1. 병합 브랜치 (기점 9628372, 커밋 578ccef · e638813 · 4df972a)

| 항목 | 결과 | 꼬리표 |
|---|---|---|
| 바뀐 파일 | `app/pipeline/extract_verifier/`(새로 9개), `config.py` +3, `.env.example` +2, `weave.py` +26/−5, 시험 2개(새로) | [측정] |
| 보호 파일 | `weave_core/`·`extract_gate.py` 무변경. `filter_event`·`grade_of`·`attrs.trust`·`episode_to_delta` 손대지 않음 | [측정] |
| 패키지 충실도 | `entities·items·kor·numbers·preserve·rank`는 검증기 저장소 `verifier/`와 import 줄 외 동일. `report.py`는 import 경로와 코어 `sys.path` 추가(extract_gate.py 방식)만 다름. `merge.py`는 marks만(안 A 코드 없음) | [측정] |
| `weave.py` | `_grade(…, text=, reg=)` + `weave_verifier=="annotate" and weave_gate_mode!="off"`일 때만 `_verify_into`(try/except, 실패 시 관문 dict 그대로 + `logger.exception`). `text`는 `chapter_text`. `_gate_section`에 `verifier{version, promotion_candidates_high[], promotion_candidates_mid_count}`, low 없음, 검증기 안 돈 등급 파일이면 키 없음 | [측정, 코드 읽음] |
| 설정 | `weave_verifier: Literal["off","annotate"] = "off"` — `weave_gate_mode` 바로 아래. J가 고치는 judge 설정 줄과 떨어짐 | [측정] |
| 새 시험 | 42 통과(서비스 경로 16 + 코퍼스 26, 코퍼스 시험 skip 없음). (4) 실패 격리·(5) LLM 0회는 본문까지 읽음 — (5)는 검증기가 실제로 돌았음(`ran==1`, `verifier` 키 있음)도 확인한다 | [측정] |
| 전체 suite | **518 통과 · 1 skip · 3 xfail**(326초, `TEST_DB_NAME=glitch_noai_test_vjudge`) — 구현 세션 보고와 같음 | [측정] |
| 충돌 | main(9a09999·e3c27ba, 문서만 앞섬)과 J(`claude/pensive-wing-5159d2` a4614e8) 모두 `merge-tree` 깨끗 | [측정] |
| 병합 후 | a459fee 트리는 검증한 브랜치와 docs 3개만 다름. 새 시험 + 규칙 조립 시험 56 통과 | [측정] |

판정: **확인됨.** 틀린 항목 없음.

## 2. 조건 차이·후속 (병합을 막지 않음)

1. **화면에 닿지 않는다.** 구현 세션 조사(코드 읽기, 화면 실행 안 함): 검수 화면·API·service 어디도 `review_NN.json`을 읽지 않는다. gate 절의 high 후보 묶음은 지금 파일에만 있다. 화면에 올리려면 `ReviewOutput`(파이프라인 계약) 확장 또는 `WEAVE_DATA_DIR`를 읽는 새 API — **사용자 결정 대기.**
2. **`entity_uncovered` 서비스 레지스트리 재측정은 근사만.** 코퍼스 원장이 씨앗 레지스트리로 ID가 풀린 채 추출돼 서비스 레지스트리를 정확히 재현할 수 없다. 20회차 합: registry_live 전체 135 · 시점 근사 130 · 추출 선언만 92 [측정, 근사]. 서비스 경로 시험은 추출 병합 `reg`로 정확히 1건을 확인. 정확한 서비스 조건 값은 **미측정.** 신호가 보조·표시 전용이라 병합은 막지 않는다.
3. 시험 (6) 신호 표적은 원문 없이(`text=None`) 돈다. 검증기 저장소에서 원문 유무와 상관없이 `number_lost` 수가 같음(107/167/86/4)을 보고했다 [기록, 구현 세션].
4. 관찰(범위 밖, 미확인): `canon-view.js`가 `attrs.trust`를 숨김 목록 없이 '속성' 칩으로 날것 표시하는 것으로 보인다 — 기존 동작.
5. 검증기 저장소 `packets.py`가 WM에도 `timelines=["main"]`을 쓴다(현재 영향 없음) [기록, 구현 세션].
