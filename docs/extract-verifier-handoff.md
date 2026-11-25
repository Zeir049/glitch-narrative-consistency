# 설계·판단 세션 인수인계 — LLM 추출 검증기 트랙

2026-11-07. 판단 세션(Fable, "LLM 없는 추출기 또는 검증기")이 사용량 한도로 **Opus 5.5 high 세션으로 역할을 넘기며** 쓴다.
학습형 judge 트랙의 인수인계는 [`learned-judge-handoff.md`](learned-judge-handoff.md)(다른 판단 세션 작성)에 있고, 이 문서는 **검증기 트랙만** 다룬다.
둘은 같은 저장소에서 돌며 하네스(`scripts/eval/judge_eval.py`)를 공유한다.

## 1. 역할 (사용자가 정한 것 — 지켜라)

- 이 세션은 **설계·판단 전용**. 코드·문서 수정은 사용자가 "해라"라고 한 뒤에만. 구현은 사용자가 여는 별도 Opus 세션(또는 이 세션이 `start_session`으로 띄우는 세션)에서.
- 다른 세션의 완료 보고는 믿지 말고 **직접 검증**(git log·diff, pytest 재실행, 데이터 표본 대조)한 뒤 답한다. 지금까지의 관행: 구현 세션 보고 → 판단 세션이 저장소를 직접 열어 확인 → 결정을 `SendMessage`로 전달.
- 커밋은 사용자가 요청할 때만. 문서 커밋은 main에 직접(또는 짧은 브랜치 → ff). **push는 아무도 안 한다.** 같은 폴더에서 다른 세션이 작업 중이면 체크아웃을 바꾸지 않는다.
- 숫자 꼬리표 [측정]·[기록]·[추정]. 규칙·기준은 **측정 전에 문서에 적는다**. 표적을 보고 규칙을 고치지 않는다. 주장 수위: "최초" ✗, "추출 층을 사람 라벨로 직접 검증한 사례" ○.

## 2. 지금 상태 (2026-11-07 밤, main a459fee 기준)

| 항목 | 상태 | 어디 |
|---|---|---|
| 검증기 독립 개발(T0~T5) | **종료.** `C:\glitch_extract_verifier` af0eac9, 테스트 62 통과(판단 세션 재실행 확인), `vendor/weave_core` = 8d89349 줄끝 외 무변경, 검사기는 라벨을 읽지 않음, 275단위 재실행 바이트 동일 | 그 저장소 `docs/`: t1_premise_check · t3_measurement(v2·v3·v4) · t4_learned_verifier_design · merge_proposal · review/t1_t3_human_review |
| 병합 | **main a459fee에 병합됨**(11-07, 사용자 승인, 브랜치 `claude/extract-verifier-merge` 578ccef·e638813·4df972a). B안, `WEAVE_VERIFIER` 기본 off. 판단 세션 재실행: 전체 518 통과·1 skip·3 xfail, 새 시험 42. **화면 미연결**(검수 화면이 review_NN.json을 안 읽음), `entity_uncovered` 서비스 조건 값은 근사만 | [`extract-verifier-merge-review.md`](extract-verifier-merge-review.md) |
| 사람 확인 | 검토 세션(Opus)이 대행: T1 13행 동의 12·부분 동의 1(Q1 s21)·부동의 0, 표본 115건 재판정. **사용자 본인 확인은 아님** — 문서에 그렇게 적혀 있다 | 검증기 저장소 `docs/review/` |
| 하네스 규칙 중복 제거 결함 | **수정돼 main 병합됨**(77bf021, 커밋 59c3b48·1dad041·04e7bf8). `load_context`가 본문 정규화 기준, ID 충돌은 `RULE_007@ep4`식. 유실 규칙 4개 복구, 동결 열 기준선 불변, learned 가짜 게이트 기준 갱신 | `scripts/eval/judge_eval.py` `collect_episode_rules`, `tests/eval/test_judge_rule_assembly.py`, EVAL.md |
| 하네스 수정 뒤 후속 | **완료**(검증기 저장소 c274390 사전 기록 → d0da385 → d555981, 테스트 76): `packets.py`를 본문 정규화 조립으로 옮기고 패킷 13개 재덤프 — (b)절 규칙 4개 추가뿐, T1 분류 변동 0/13(판독은 구현 세션) | 그 저장소 `docs/t1_premise_check.md` "재확인" 절, 검증 기록 §0 |
| T6 high 정밀도 | 검증기 저장소 8f4611e → a6c26f3: v4 high 전수 80건 의미 정밀도 0.31 [0.22, 0.42], mid 표본 0.40 — 순위가 품질을 못 가름(판독은 구현 세션, 판단 세션 13행 대조 동의) | 그 저장소 `docs/t6_high_precision.md` |
| **표시 트랙 종료** | T7(6bcbc4c → 7264a57 → 4184520 → 41f9538): v5 미판독 시험셋 high 43건 0.535 [0.389, 0.675], 사전 기준 하한 0.40 미달 → **멈춤**(사용자가 미리 정한 규칙). `WEAVE_VERIFIER` off 유지, v5는 검증기 저장소에만. 약점: where 열로 맹검 깨짐·판독자=설계자·'배경 수치' 판정 비일관 | 그 저장소 `docs/t7_v5_validation.md`, [`extract-verifier-t4-plan.md`](extract-verifier-t4-plan.md) §0 |
| T4 | **사전 기록 작성**(측정 전): 과제·라벨 지침·표본 300 층화·혼합 라벨(사용자 100 맹검 + 세션 300, 채택 기준 κ ≥ 0.70·M 재현율 ≥ 0.80)·B0/B1/B2·관문 G0~G6. 구현 미착수 | [`extract-verifier-t4-plan.md`](extract-verifier-t4-plan.md) |
| 선행 조사·측정 | [`related-work-extraction-verification.md`](related-work-extraction-verification.md) §0~§6(§4-2-1이 최신 재귀속) | 탐침 `scripts/eval/probe/` |

## 3. 확정된 결정 (바꾸려면 근거를 적고 사용자에게)

- 검증기는 독립 개발 → 검증 → 병합(사용자, 11-07). 한국어로 간다(klue/roberta 계열, 다국어 전환 없음, 11-06). 유료 LLM 키는 검증 전용, **API 호출로** 하는 데이터 생성·번역·라벨은 로컬 3060·무료만(c52ae3d). Claude Code 세션이 직접 읽고 판정하는 작업은 해당 없음(사용자 설명, 11-07) — 단 학습·평가 라벨을 같은 판정자에게서 내지 않는다(T4 계획 §4).
- 1단계 신호 5개 전부 **표시 전용**: `assumed_only_persistent`(T3 분모 2: Q1 s21·Q3 s28) · `declaration_only_persistent` · `assumed_only_exclusive`(실험) · `number_lost`(회차 어디서든 한 번 보존되면 끔) · `entity_uncovered`. 등급·exclude·재추출 근거 금지.
- 인용 대조(`source_text` 실재)는 등급 신호 아님(환각 0.2~0.9%). 재추출은 진짜 누락 2~3건 규모라 우선순위 낮음.
- 병합 B안, `WEAVE_VERIFIER=off|annotate`(기본 off). 화면 묶음(high 펼침·mid 건수·low 숨김)은 gate 절에만 있고 화면에는 연결 안 함 — T7 멈춤으로 연결하지 않기로(11-07).
- T4(학습 검증기 "인용 ⊨ 구조값")는 평가셋이 선행: **혼합 라벨**(사용자 100 맹검 + 세션 300, 채택 기준 사전 고정) — [`extract-verifier-t4-plan.md`](extract-verifier-t4-plan.md). WM 재추출 3벌 슬롯 일치는 증량 수단으로만.
- 외부 데이터(FlawedFictions 817쌍 MIT·ContraDoc story 125쌍 Apache-2.0)는 문장 단위 오프라인 번역으로 중간 학습 단계에만, **첫 실험은 외부 데이터 없이**. FactTrack 보류(라이선스 없음). FABLES는 검증기 평가용.

## 4. 미뤄 둔 결정 (사용자가 정한다 — 물을 때 선택지와 근거를 같이)

1. ~~병합 실행 여부·시점~~ → 11-07 병합됨(a459fee). ~~검증기 표시 화면 연결~~ → T7 멈춤으로 **트랙 종료**(11-07). 1단계 신호는 off로 남는다.
2. ~~하네스 수정 뒤 패킷 재덤프·T1 재확인~~ → 11-07 완료(분류 변동 0/13).
3. **T4** — 라벨 방식은 **혼합(사용자 100 맹검 + 세션 300)으로 결정**(사용자, 11-07). 유료 LLM 규칙은 API 호출 기준이라 세션 라벨 허용(사용자 설명, 11-07). 남은 결정: G1 착수 지시, 세션 라벨러를 어느 세션으로 할지. judge 트랙(lj-design2)에도 같은 규칙 설명이 닿아야 한다 — 사용자가 직접 전하기로.
4. **R1 주입 분류표 출처 수정**(`learned-judge-plan.md` 사전 확정 표의 "미탐 귀속 13건" 문구) — 재귀속표(§4-2-1)로 바꿔야 한다. judge 트랙 판단 세션 몫으로 넘어갔는지 `learned-judge-handoff.md`에서 확인.
5. 검토 유형 E(오추출이 심은 문장을 지지 — Q1 s09·Q3 s40) → **T4의 M(어긋남) 표적으로 귀속**(판단 세션 제안, T4 계획 §2). 1단계 결정적 검사는 원리상 못 잡는다.

## 5. 판단 기준 (이 트랙에서 실제로 쓴 것)

- 구현 세션 보고의 수치는 **저장소를 열어 재실행**해 확인(pytest, 결정성은 두 번 돌려 바이트 비교, 코어는 `diff -rq --strip-trailing-cr`).
- 판독 표는 **사건 ID가 절에 있음 ≠ 정답 사실이 절에 있음** — 반드시 문장 단위로. 진단 파일 `gold_in_sections`는 ID 기준이라 그대로 믿으면 안 된다(§4-2 1차 정정판의 오류).
- 같은 사실의 다른 표기('두 시 반'↔'2시 30분')는 운반으로 친다. 다른 사실의 같은 숫자('최영감 600원 채무')는 운반이 아니다.
- 사람 확인을 세션이 대행했으면 문서에 반드시 그렇게 적는다.
- 세션 간 파일 충돌: 쓰는 파일이 겹치면 워크트리. 테스트 DB는 공유라 전체 suite 동시 실행은 흔들린다(`GLITCH_TEST_DSN`은 호스트만, DB 이름은 상수).

## 6. 연결 세션 (2026-11-07, 전부 idle)

| 이름 | ID | 한 일 |
|---|---|---|
| extract-verifier (구현, Opus medium) | `local_3a687dd4-a9be-49d1-ab6e-293ac8186b27` | T0~T5·T3-bis·v4, 재덤프·T1 재확인, 병합 브랜치. **11-07 최상위로 분리됨** — 턴 끝 알림이 어디에도 안 가므로 지시문에 "완료 보고는 SendMessage로 판단 세션에"를 넣는다 |
| verifier-review (검토, Opus) | `local_991d21f2-ecf7-4a23-90fe-0ea05c50f171` | 사람 확인 대행 |
| judge-rule-id (수정, Opus) | `local_8f652550-0914-4b5f-a386-6b4d393016a1` | 하네스 규칙 중복 제거 수정(main 병합됨) |

후속 지시는 새 세션을 열지 말고 해당 세션에 `SendMessage`(to = ID)로.

## 7. 읽는 순서

1. 이 문서 → [`learned-judge-handoff.md`](learned-judge-handoff.md)(judge 트랙 상태·역할 체계)
2. [`extract-verifier-brief.md`](extract-verifier-brief.md) §3·§8
3. [`related-work-extraction-verification.md`](related-work-extraction-verification.md) §0·§4-2-1·§5
4. `C:\glitch_extract_verifier\docs\merge_proposal.md` §1~§3·§9, `docs/review/t1_t3_human_review.md` §3~§4
5. 메모리 `MEMORY.md`(세션 역할·GPU·한국어 결정·진행 상태)
