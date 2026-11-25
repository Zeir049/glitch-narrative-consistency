# 지시문 — LLM 추출 검증기(독립 개발) 구현 세션

2026-11-07, 판단 세션이 썼다. 이 문서를 새 Opus 세션의 첫 메시지에 그대로 붙인다.
배경 문서: [`related-work-extraction-verification.md`](related-work-extraction-verification.md)(선행 조사·측정·13건 재귀속 정정판·학습 데이터 후보),
[`learned-judge-plan.md`](learned-judge-plan.md)·[`learned-judge-decisions.md`](learned-judge-decisions.md)(R1~R3 요구·사전 확정값·주장 수위).
이 저장소(`C:\glitch_integrated_noai`, main `fa9d981` 이후)는 **읽기 전용 참고 자료**다.

---

## 0. 역할과 운영 규칙

- 너는 **구현 세션**이다. 설계·판단은 판단 세션이 한다. 설계를 바꿔야 할 이유가 생기면 바꾸지 말고 **근거를 적어 보고**한다.
- 작업 장소는 **별도 저장소** `C:\glitch_extract_verifier`(새로 만든다. 자체 venv, Postgres·Redis 불필요). 이 저장소(`glitch_integrated_noai`)의 파일은 읽기만 하고, 복사는 아래 §3에 허용한 것만.
- 숫자에는 꼬리표를 단다: **[측정]**(네가 직접 잰 값) · **[기록]**(문서·논문 값) · **[추정]**(식으로 계산). 결과를 본 뒤 기준을 바꾸지 않는다.
- 보고는 "한 것 / 못 한 것 / 측정값 표 / 판단 세션에 묻는 것" 네 칸. 완벽한·확실한·LLM보다 낫다 같은 표현은 쓰지 않는다 — "알려진 범위에서, 측정된 한계 안에서 동작한다".
- 사용자 PC: Windows 11, 콘솔 cp949(`sys.stdout.reconfigure(encoding="utf-8")` 또는 `PYTHONIOENCODING=utf-8`), GPU RTX 3060 12GB. 서버를 띄울 일이 있으면 `127.0.0.1`에만 바인딩.
- **유료 LLM 키는 검증 단계 전용**이다(2026-11-07 결정, 커밋 c52ae3d). 데이터 생성·번역·라벨링에 LLM이 필요하면 로컬(3060) 또는 무료 모델만.
- 언어는 **한국어**(2026-11-06 결정). 다국어 모델 전환 없음. 학습 단계가 생기면 기반 모델은 klue/roberta 계열.

## 1. 목표와 주장 수위

| 하는 말 | 안 하는 말 |
|---|---|
| LLM 추출 산출물에서 **기계가 확인할 수 있는 것은 전부 기계가 확인**하는 결정적 검증기를 만들고, 4작품 실제 코퍼스에서 적중·과다 반응·결정성·시간을 잤다 | "환각을 거른다"(이 추출기의 인용은 환각이 아님이 측정됨), "누락을 막는다" |
| 검증기 출력은 기존 관문(`extract_gate.GateResult`)과 같은 신호 모양이라 병합은 **신호 종류 추가**로 끝난다 | 독자적 등급 체계·판정 원장 직접 수정 |
| 추출 층을 **사람 라벨로 직접 검증한 사례**(선행 연구에 없음) | "최초"(Granthika·Book Check Genie가 상용 선례) |

## 2. 확정된 사실 (출발점 — 다시 재지 말고 인용한다. 재현은 `scripts/eval/probe/`)

| 사실 | 값 | 함의 |
|---|---|---|
| nemotron 원장 `source_text`는 환각이 아니라 이어 붙인 문장 | 문장 단위·문장부호 정규화 후 못 찾는 인용 Q1 5/530(0.9%)·Q3 1/574(0.2%) [측정] | **인용 대조는 등급 신호가 아니다.** 품질 통계로만 |
| 사건 단위 `source_text`는 문단 통째(최대 1,443자) | — [측정] | 커버리지는 **항목 단위 인용**(state_changes·assumed_states·knowledge_gained)으로만 계산 |
| 수치 문장 미커버 | Q1 회차당 36문장, Q3 54문장 [측정] | 재추출 요청의 주 수단으로 쓰면 과다. 장르 의존 |
| 커버됐으나 구조값에 수치 없음(요약 손실) | 회차당 18~20건 [측정] | **수치 보존 검사**는 유효 |
| 엔티티 미참조율 | 29~31%, 대부분 장소 [측정] | 보조 신호 |
| Weave "원장 부재 13건" 정정 재귀속 | 판정자 미탐 10 · **진짜 보존 손실 3**(Q1 s21 EVENT_1_4 요금 600 — `assumed_states`에만, Q3 s04 EVENT_3_5, Q3 s42 EVENT_5_4 종이 일지) · 매핑 없음 1 [측정, 진단 파일 `gold_in_sections` 기준, 사람 확인 전] | 보존 검사의 표적 = **`assumed_states`에만 있는 지속성 사실**. 규모 3건 안팎 |
| 제품 어댑터 경로의 규칙 흐름 | `episode_to_delta` → 캐논 델타 → `judge_registry` → 패킷 (b)절. `NEW:` 라벨도 `RULE_U###`로 실림 [코드] | 규칙 누락은 표적이 아니다 |
| 반복 추출 일치도(WM 3벌) | 슬롯 J 0.78(assumed)·0.57(state_changes), **값 J 0.01** [측정, `docs/EVAL.md`] | 반복 일치 필터는 슬롯 단위로만. 값은 글자 비교 불가 |

## 3. 경계와 계약

**입력** (전부 코퍼스에 이미 있는 형태):
- Weave 회차 문서(스키마 v2 JSON: `events[].state_changes/assumed_states/knowledge_gained`, `worldview_rules`)
- 그 회차 원문 텍스트 (조각 `chunks/episode_NN_c*.txt`를 이어 붙인 것)
- 확정 회차 문서 목록(앞 회차들) + 엔티티 레지스트리

**출력**: `extract_gate.GateResult.to_dict()`와 같은 모양 — 사건 ID → `{grade, own_reasons, state_changes[{index, entity, field, grade, reasons[{signal, ...}]}]}` — 에
새 `signal` 값만 추가한다. 1단계 신호 이름(확정, 2026-11-07 T1 뒤 보완):
- `assumed_only_persistent` — 보존 검사. 수치·시각·개수·명시 사물 상태(위치·매체)가 든 `assumed_states` 항목인데 같은 회차의 `knowledge_gained`·`state_changes`·`worldview_rules` 어디에도 그 수치·상태가 없음. **T3 적중 지표의 분모는 이것만**(T1 판독: Q1 s21, Q3 s28 = 2건).
- `declaration_only_persistent` — 같은 검사를 **엔티티 선언**(`characters/objects/locations[].description`·`traits`)에 적용. 선언은 추출기 산출물이지만 패킷 어느 절에도 안 실린다(T1: Q3 s42 종이 일지, Q3 s28의 45분도 OBJ_015 description에). 입력 계약의 "회차 문서"에 선언이 포함된다.
- `assumed_only_exclusive` — **실험 신호, 등급·적중 지표에 넣지 않음.** 비수치 배타 사실(`뿐`·`만`·`유일`·`단독`·`밖에 없` 어휘 단서)이 `assumed_states`에만 있을 때(T1: Q3 s05 "콘솔은 도현뿐"). T3에서 발화 수와 표본 정밀도만 보고.
- `number_lost` — 수치 보존. 수치 문장이 항목 인용에 덮였는데 구조값(`to`·`assumes`·`info`·규칙 content)에 그 수치가 없음(T1: Q1 s35 (d) "월 2회", Q3 s28 (d) "점검/교체"에 "하나" 빠짐). 단 같은 사실을 다른 절이 운반하면(s35는 (b) 규칙에 서수 있음) 손실이 아니다 — 회차 전체에서 한 번이라도 수치가 보존되면 끈다.
- `entity_uncovered` — 엔티티 커버리지, 보조.
승격 후보는 별도 목록 `promotion_candidates[{event_id, index|declaration, entity, field, value, numbers, source_text, signal}]`. 등급을 내리는 신호와 "후보 표시만" 하는 신호를 구분해 적는다.
**검사기 표적이 아닌 것**(T1에서 확정): 사건 `source_text`·요약에만 있는 사실(Q1 s03·s09 — 비수치라 결정적 검사 불가, s09는 ERS 검색 누락), 매핑 오류·진짜 누락(Q1 s13). 트레이드오프 표에 "거르지 못하는 유형"으로 적는다.

**허용 복사**: `app/pipeline/weave_core/` 전체를 **같은 커밋(event_sourcing 8d89349, `VENDORED.md` 참고) 읽기 전용**으로 복사해 `vendor/weave_core/`에 둔다.
`app/pipeline/extract_gate.py`는 출력 모양 참고용으로 복사해도 되나 고치지 않는다. `scripts/eval/probe/*.py`는 가져가 재사용한다.

**금지**: `weave_core` 수정, 이 저장소의 `app/` import, 판정 원장·캐논에 쓰기, 이 저장소 main에 커밋, 유료 LLM으로 데이터 생성.

## 4. 작업 순서

### T0. 저장소 골격 (반나절)
`C:\glitch_extract_verifier`: `pyproject.toml`(Python 3.11+, 의존성 최소 — pytest·hypothesis 정도), `vendor/weave_core/`, `verifier/`(패키지), `scripts/`, `tests/`, `data/`(코퍼스 복사, 아래 §5), `docs/`. 첫 커밋.

### T1. 전제 확인 — 13건이 제품 경로 패킷에서 어떻게 보이나 (LLM 0회, 1일)
목적: "`assumed_states`에만 있음" 집합을 **사람이 읽어** 고정한다. 검사기는 그 뒤에 그 집합에 맞춘다.
- 패킷 만들기: 이 저장소 `scripts/eval/judge_eval.py`의 `load_context(work)`(5화 확정 원장 재생, knows accumulate)와
  코어 `judge.build_judge_prompt(event, ledger, related_events, ...)`로 Q1·Q3 양성 장면의 패킷 텍스트를 덤프한다(LLM 호출 없음, `run_checks`가 쓰는 함수와 같다).
  대조군으로 Weave 원본 동결 열 패킷 `judge_requests_long_q1_full/`·`_q3_full/`도 읽는다.
- 13건마다 표: 장면 · 라벨 근거 인용 · 정답 사실의 수치/사물 · 패킷 어느 절(b/c/d/e)에 **어떤 문장으로** 실렸나 · 실린 문장에 그 수치가 있나 · 분류(판정자 미탐 / assumed_states에만 / 어디에도 없음 / 매핑 오류).
  `evidence_event_map.json`은 문자열 대조 후보라 믿지 말고 원문을 읽는다(Q1 s13 사진 위치는 매핑 오류 의심).
- 산출물: `docs/t1_premise_check.md` + 표 CSV. 이 표가 R1 주입 분류표의 출처가 된다(계획서 수정은 판단 세션 몫).

### T2. 1단계 검사기 (결정적, 2~3일)
- `verifier/preserve.py`: 보존 검사. 지속성 후보 = 수치·시각·개수·사물 상태가 든 `assumed_states` 항목(한국어 수사→숫자 변환은 probe `number_preservation.kor2int` 재사용).
  같은 수치/상태가 같은 회차의 `knowledge_gained`·`state_changes`·`worldview_rules` 어디에도 없으면 `assumed_only_persistent` + 승격 후보.
- `verifier/numbers.py`: 수치 보존 검사. 수치 문장이 항목 인용에 덮였는데 구조값(`to`·`assumes`·`info`·규칙 content)에 그 수치가 없으면 `number_lost`.
- `verifier/entities.py`: 엔티티 커버리지(보조).
- `verifier/report.py`: GateResult 호환 JSON + 사람이 읽는 표.
- 규칙: 입력을 바꾸지 않는다, 시각·난수 없음, 같은 입력 → 같은 출력(바이트 동일). 각 신호마다 "거르는 유형 / 못 거르는 유형"을 docstring에 쓴다.
- 테스트: 신호별 최소 사례(양성·음성·경계), 결정성(두 번 실행 비교), 4작품 전 회차 실행이 예외 없이 끝남.

### T3. 측정 (1~2일, 사전 확정)
| 지표 | 방법 | 기준(사전 확정) |
|---|---|---|
| 적중 | T1 표의 "assumed_states에만" 집합에서 `assumed_only_persistent`가 켜진 비율 | 전건. 못 잡은 건은 유형을 적는다 |
| 과다 반응 | 정상 장면(Q1 25·Q3 25·셜록 25·WM 10)과 전 회차에서 승격 후보 수 / 회차 | 보고만(허용치 없음 — 승격은 표시이므로). 회차당 수를 작품별로 |
| 수치 손실 | `number_lost` 건수와 표본 30건 사람 검수 정밀도 | 정밀도 보고 |
| 결정성 | 같은 입력 두 번 → JSON 바이트 동일 | 필수 |
| 시간 | 회차당 CPU 초 | 보고 |
| 추출기 교차 | WM Claude 추출본 + 재추출 3벌에도 실행 | 신호 분포가 추출기에 따라 어떻게 다른지 |

### T4. (조건부) 학습 검증기 — T3 결과를 보고 판단 세션이 결정한 뒤에만
후보: "인용 ⊨ 구조값" 함의 점수(klue/roberta, 한국어). 라벨 출처가 없으므로 WM 재추출 3벌의 슬롯 일치를 약한 라벨로 쓰는 안과 사람 라벨 소량 안을 **비교 설계만** 써서 보고한다. 구현하지 않는다.

### T5. 병합 제안서 (반일)
`docs/merge_proposal.md`: 이 저장소 어디에 어떻게 끼우나 — `extract_gate.grade_episode` 뒤에 신호를 합치는 함수 하나, `WEAVE_GATE_MODE`와의 관계(승격 후보는 exclude 대상이 아님),
필요한 테스트, `weave.py` 변경 범위(추출 구역 `_extract_sync`/`_grade` 직후 한 줄). 병합은 판단 세션이 검증한 뒤 사용자가 결정한다.

## 5. 데이터 위치

| 자료 | 위치 | 비고 |
|---|---|---|
| Weave 원본 저장소 | `%USERPROFILE%\Downloads\event_sourcing` (main 9180c3a) | **원문 조각 `corpus_*/chunks/`는 여기만 있다.** `judge_requests_long_q1_full/`·`_q3_full/`(동결 열 패킷), `handoff/longitudinal_miss_diagnosis_nemotron_ultra.json`(13건 진단) |
| Q1·Q3·셜록 라벨 코퍼스(이 저장소 복사본) | `tests/eval/weave_judge_corpus/{q1,q3,sherlock}/` | 각 45장면(양성 20·정상 25). `ledger_nemotron_ultra/episode_01..05.json`·`labels.json`·`evidence_event_map.json`·`scenes/`·레지스트리. 원문 chunks는 없음 → Weave 원본에서 복사 |
| WM 코퍼스 | `tests/eval/weave_checker_corpus/` (30장면, 양성 20·정상 10, 원본 추출본) | 체커 게이트와 공유. WM 원문 `WM_1-5.txt`는 Weave 원본에 |
| WM 재추출 3벌 | `tests/eval/weave_judge_corpus/wm/reextract/` (90파일) | 추출기 교차 측정용 |
| 탐침 스크립트 | `scripts/eval/probe/` (+README) | 정규화 규약·수사 변환·측정 절차의 원본 |
| 코어 | `app/pipeline/weave_core/` (8d89349) | 읽기 전용 복사 |

복사한 데이터는 `data/` 아래 작품별 폴더에 두고 출처 커밋을 `data/README.md`에 적는다. 라벨(`labels.json`)은 T1·T3 채점에만 읽고 검사기 코드가 읽지 않게 분리한다.

## 6. 참고 파일 (읽는 순서)
1. `docs/related-work-extraction-verification.md` §0·§4(정정판)·§5
2. `app/pipeline/extract_gate.py` (출력 모양·기존 신호 4종·"거르지 못하는 것" docstring)
3. `scripts/eval/probe/README.md` + `number_preservation.py`
4. `scripts/eval/judge_eval.py` 상단 docstring과 `load_context`
5. `app/pipeline/weave_core/judge.py` `build_judge_prompt`(패킷 절 구성), `weave_pipeline.py` `run_checks`
6. `docs/EVAL.md` 반복 추출 일치도 절
7. `docs/learned-judge-plan.md` R1~R3와 사전 확정 표

## 8. 병합 결정 (2026-11-07, 판단 세션 — T5 제안서 `C:\glitch_extract_verifier\docs\merge_proposal.md` 검토 뒤)

- **안 B(marks)** 채택: 신호는 `own_marks`·`state_changes[].marks`와 별도 키(`verifier`·`promotion_candidates`·`experimental_candidates`)로. 등급·`tentative_event_ids`·`attrs.trust`·exclude 결과 불변(구현 저장소 시험으로 고정). 안 A는 `filter_event` 미수정 시 4작품 20회차에서 사건 135건이 추가 제외돼 기각.
- 설정 `WEAVE_VERIFIER=off|annotate`(기본 off), `WEAVE_GATE_MODE=off`면 무시. 다섯 신호 전부 표시 전용 — 등급·exclude·재추출 근거 금지.
- 화면: 승격 후보 `priority=high`만 기본 표시(서비스 조건 회차당 Q1 4.6·Q3 7.0·셜록 3.6), mid는 건수만, low 미표시. 표적 4건 중 high 3건(Q3 s28 assumed "잔여 45분"은 low — 거르지 못하는 유형).
- 사람 확인은 검토 세션(Opus)이 대행: T1 13행 동의 12·부분 동의 1(Q1 s21 "에만" 단서)·부동의 0. 표본 재판정으로 `number_lost` 기계 정밀도는 v2 0.80·v3 0.90(같은 사실의 다른 표기를 운반으로 칠 때), 의미 정밀도 0.07~0.10, exclusive v3 0.44.
- 검토가 찾은 **하네스 결함**: `scripts/eval/judge_eval.py` `load_context`가 `rule_id` 기준으로 뒤 회차 규칙을 버린다(nemotron이 RULE_007·008 ID를 회차마다 재사용 — Q1 1·Q3 3개 유실, Q3 5화 RULE_008이 s04 정답 후반부). 제품 경로 `judge_registry`는 본문 정규화 기준이라 영향 없음. 이 저장소 수정 과제(별도 세션).
- T4 학습 검증기: 어느 안이든 사람 라벨 평가셋 300이 선행. 사용자가 라벨 시간을 배정할 때 시작. 유료 LLM 라벨 금지 유지.
- 병합 실행은 사용자 결정. 병합 쪽 테스트 목록은 제안서 §6(7항목) + 서비스 레지스트리 기준 `entity_uncovered` 재측정.

## 7. 끝내는 조건
T1 표가 사람 확인까지 끝났고, T2가 4작품 전 회차에서 결정적으로 돌며, T3 표가 채워졌고, T5 제안서가 있다. T4는 설계 비교만. 못 한 항목은 "못 했다"고 적는다.
