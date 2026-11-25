# 학습형 judge · 추출 관문 계획 (실행판 v2)

2026-11-06. 초안(Opus 세션)과 3일 실행판을 이 저장소에서 두 차례 검토한 뒤 고친 판이다.
왜 이렇게 정했는지는 [`learned-judge-decisions.md`](learned-judge-decisions.md).
`weave_core/`는 고치지 않는다 — 전부 어댑터(`app/pipeline/weave.py`)와 몸통, `scripts/`, `tests/`에서 한다.

## 목표와 주장 수위

| 하는 말 | 안 하는 말 |
|---|---|
| 판정 단계의 LLM 호출을 0으로 만드는 경로를 만들고, 제품이 실제로 돌리는 LLM judge와 **같은 코퍼스에서** 비교했다 | "LLM보다 낫다", "완벽하게 잡는다" |
| 잘못 추출된 사실을 판정 원장에서 **제외**하는 관문을 만들고, 결함 주입으로 막는 범위와 recall 손실을 쟀다 | "잘못된 사실이 쌓이지 않는다" (일부 유형만 막는다) |
| 작가 검수를 **선택**으로 만들 수 있는 근거(관문 + 신뢰 등급 표시 + 기존 재검수·철회 경로) | "작가 검수 없이" (사후 정정 자동화가 없다) |
| 같은 입력이면 같은 결과, 녹화된 LLM 응답으로 회귀 게이트 | — |
| 발표 주장은 R2(동결 열 0.30 대비 비열등). recall 0.5·정상 장면 오탐 ≤ 0.05는 내부 목표 | "문헌 수준" — 같은 설정의 선행 수치는 없다 |

## 요구

- **R1** 관문이 거르는 추출 오류 유형에서는 그 사실이 판정 원장에 들어가지 않는다. 거르지 못하는 유형과 recall 손실을 함께 보고한다.
- **R2** learned 모드는 판정에 LLM을 쓰지 않는다. 제품 동결 열(아래)과 같은 코퍼스에서 recall 차이의 대응 bootstrap 95% 신뢰구간 하한이 −δ보다 크다.
- **R3** 같은 입력 두 번 → 결과 완전 일치. 녹화 재생 회귀 게이트가 CI에서 돈다. 결과 JSON에 모델 해시·기준값·프롬프트 버전.

### 사전 확정 (결과를 본 뒤 바꾸지 않는다)

| 항목 | 값 |
|---|---|
| R2 **주 기준선** | 제품 동결 열: `nemotron-3-ultra:free` · `full` · scene · k=3 · majority 2 · C=2500 · knows_mode accumulate. Weave `final_report.md` 기준 합산 Recall 0.30 (18/60), 오탐 0.05. 이 저장소의 하네스로 **재현한 값**을 쓴다 |
| R2 참조 상한 | rnc 패킷 + Claude Sonnet 서브에이전트 0.767 (제품 경로 미연결, 나란히 적기만) |
| δ | 10%p (주 기준선 대비) |
| 평가 코퍼스 | Weave 4작품 실제 코퍼스 — WM 30 / Q1 금호탕 45 / Q3 돔 C-4 45 / 셜록 45. 원문에 심어진 모순 + 실제 추출 원장. **구조 주입 평가셋은 쓰지 않는다** |
| 분할 | 작품 단위. **주 결과 = 작품 하나 빼기 교차 적합 4접기의 합산 예측**(WM·Q1·Q3 양성 60, 설계안 §2-6). 검증 작품은 접기마다 하나 |
| 학습 데이터 | **생성 LLM은 로컬(RTX 3060, OpenAI 호환 서버)·무료 한도만 — 유료 키는 검증 단계 전용.** 1단계: 쌍 라벨링 1~2만 쌍(FactTrack식, 로컬 모델 2개 이중 라벨) + 한국어 반사실 회차 200~300건(FlawedFictions식, 사람 검증). 첫 실험은 외부 데이터 없이. 구조 주입 생성기는 대조 묶음·어려운 음성 보조. 학습 작품의 실제 장면 포함. 주입 연산자 1개 홀드아웃. 근거: [`learned-judge-prior-work.md`](learned-judge-prior-work.md) **누수 규칙**: 쌍 라벨링·반사실 회차는 각 접기의 학습 작품 확정 회차에서만 만들고, 평가 작품의 회차·장면은 어느 접기에서도 라벨링·생성에 쓰지 않는다. 반사실 산출물은 학습 전용 |
| R1 주입 분류표 | Weave 종단 측정의 미탐 귀속(원장 부재 13건)의 실제 유형에서 뽑는다. 관문 설계와 독립적으로 먼저 고정 |
| R1 recall 손실 허용치 | 5%p (관문 exclude 대비 off) |
| 회차당 판정 시간 상한 | 60초 (사건 30개·과거 사건 300건 기준). 워커 GPU(RTX 3060 12GB)가 기본, CPU는 대체 경로로 따로 측정 |
| 코어 기준 커밋 | `weave_core` = event_sourcing `8d89349`. 실험 끝날 때까지 재동기화하지 않는다 |

> 개정 이력(모두 실험 시작 전): 2026-11-06 — 시간 상한 행 "워커 CPU 60초" → "워커 GPU 기본, CPU 대체 경로"; 학습 데이터 행 "구조 주입 + 실제 장면" → "LLM 쌍 라벨링·반사실 1단계 + 누수 규칙"; 분할 행 "작품 하나 빼기로도 보고" → "교차 적합 합산이 주 결과"; 기준은 [`learned-judge-design-review.md`](learned-judge-design-review.md). 2026-11-07 — 학습 데이터 행에 "생성 LLM은 로컬·무료, 유료 키는 검증 전용" 추가(사용자 결정).

## 설계

```
원문 → [LLM 추출] → ① 관문(확정/잠정) ─exclude→ 판정 원장(확정만) → [규칙 checker] ─┐
                                   └─annotate→ 등급 파일 → 결과 사후 처리            ├→ 결과
                                 ② 학습 judge (LLM 0회, k=1) ────────────────────────┘
③ 녹화·재생 + 코퍼스 하네스 → R1·R2·R3
```

| 구성 | 위치 | 내용 |
|---|---|---|
| ① 관문 | `app/pipeline/extract_gate.py` | 확정 회차 + 새 회차를 재생할 때의 validator 경고(hard/soft/schema_error), 미해결 `NEW:` 라벨, `from="unknown"` 상태 변화 → 잠정. 등급 파일 `WEAVE_DATA_DIR/<project>/gates/`. `WEAVE_GATE_MODE=off\|annotate\|exclude`. exclude는 잠정 사실을 판정 원장 재생에서 뺀다(확정 회차 재생 시 필터). validator 경고는 모순 후보로도 넘긴다 |
| ① 표시 | 캐논 `attrs.trust` | **INSERT 시점에만** 기록(`episode_to_delta`). 승격 없음. 몸통 service가 Weave 폴더를 읽는 경로는 만들지 않는다 |
| ① 사후 처리 | `checks_to_contradictions` | 근거가 잠정 사실뿐인 모순 → 설명에 "낮은 확신 — 근거가 잠정 사실" |
| ② 학습 judge | `app/pipeline/pairs.py`, `learned_judge.py`, `WEAVE_JUDGE_MODE=llm\|learned` | `run_checks_parallel`에서 `_judge_run` 대신 호출. k=1·majority=1 강제. 쌍 생성은 결정적(정렬·kind별 할당량 합 24, 설계안 §2-3), 추론 배치 고정. `_judge_run`과 같은 dict 반환, `judged_by`에 모델 해시 |
| ② 변환 함수 | `checks_to_contradictions` | learned면 confidence = 모델 확률, 라벨 `[학습 판정·…]`, "k회 중" 문구 제거 |
| 자동 흐름 | `app/workers/tasks.py` | `run_full_analysis(..., auto_save=False)` 인자 추가, `/analyze` 요청에서 받는다 |
| ③ 녹화·재생 | `app/pipeline/llm_record.py` | `w_llm.complete` 감싸기 `WEAVE_LLM_RECORD=off\|record\|replay`. 키 = 역할·회차·조각(또는 패킷 키)·회차 번호, 프롬프트 해시는 검증용. replay는 네트워크 0 |
| ③ 하네스 | `scripts/eval/judge_eval.py`, `tests/eval/weave_judge_corpus/` | Weave `score_longitudinal.py` 지표 이식(Wilson, 작품별·유형별·집필주체별, 작품 하나 빼기, 재실행 재현율) + 대응 bootstrap. 판정기 교체 가능. 동결 열 원시 결과를 재생해 18/60·3/60을 재현하는 것이 인수 조건 |
| ③ 반복 추출 일치도 | `scripts/eval/extract_agreement.py` | WM 재추출 3벌(`_v2_r1..r3`)로 필드별 일치도. 추출 가정을 수치로 |

### 학습 judge

- 모델 `klue/roberta-base`를 먼저, `-large`로 상한(KLUE-NLI ACC base 84.83 / large 89.17). 쌍 분류 3클래스(모순 / 정상·관련 / 무관). KLUE-NLI 기초 → LLM 라벨·반사실 데이터 + 주입 + 학습 작품 장면으로 본 학습 → 보정 → 장면 단위 기준값(정상 오탐 ≤ 0.05). small은 CPU 대체 경로.
- 가설 = 새 사건의 `assumed_states` 항목 + 요약 + **원문 발췌(`source_text`)**. 전제 = 같은 (엔티티, 필드) 원장 이력(값·since·발췌), 세계관 규칙 전부, 참여 인물 knowledge, 같은 장면의 다른 사건, 같은 엔티티가 나온 과거 사건. checker의 `needs_judge` 항목(원장값·since)을 그대로 입력으로 쓴다.
- 대상 유형: 자유 서술(신체 상태·능력), 세계관 규칙, 지식(knowledge 전제 포함). 범위 밖 유형이 learned 모드에서 사라지는 것은 트레이드오프 표에 적는다.
- 학습·추론 모두 로컬 RTX 3060 12GB(워커와 같은 PC). CPU 추론은 GPU 없는 배포용 대체 경로. 모델 파일은 저장소 밖(`WEAVE_JUDGE_MODEL_DIR`), 해시만 결과에 기록. 목표 recall 0.5 안팎·오탐 ≤ 0.05. 지표에 쌍 정확도·근거 적중 추가.

## 만들 것과 세션 배정

| 세션 | 항목 | 파일 |
|---|---|---|
| **A 녹화·하네스** | 녹화·재생 래퍼, 4코퍼스 이식, 하네스, 동결 열 재현, 반복 추출 일치도 | `app/pipeline/llm_record.py`, `weave.py`(LLM 호출 보강 구역만), `scripts/eval/judge_eval.py`·`extract_agreement.py`, `tests/eval/weave_judge_corpus/`, `tests/eval/test_llm_record.py`, `docs/EVAL.md` |
| **B 관문** | 관문 + exclude/annotate + `attrs.trust` + `auto_save` + 테스트 | `app/pipeline/extract_gate.py`, `weave.py`(`episode_to_delta`·`confirmed_episodes`·`_review_sync`), `app/workers/tasks.py`, `app/api`·`schemas`(analyze 인자), `tests/test_extract_gate.py` |
| **C 학습 judge** | 쌍 생성기, LearnedJudge(가짜·HF 백엔드), 모드 연결, 변환 함수, 주입 생성기, 학습 데이터 내보내기, 학습 스크립트, LLM 0회·결정성 테스트 | `app/pipeline/pairs.py`·`learned_judge.py`, `weave.py`(`_judge_run`·`run_checks_parallel`·`checks_to_contradictions`), `app/core/config.py`, `scripts/eval/inject.py`·`export_pairs.py`, `scripts/train_judge.py`, `tests/test_learned_judge.py` |
| 합친 뒤 | R1 결함 주입 실험, R2 비교, R3 회귀 게이트·결과표 | `scripts/eval/experiments/`, `docs/EVAL.md` |

세 세션은 각자 워크트리·브랜치. `weave.py`는 구역이 겹치지 않게 위 표대로만 건드린다. 합치는 순서 A → B → C, 검증은 이 저장소의 판단 세션에서.

## 검증

| 요구 | 방법 | 기준 |
|---|---|---|
| R1 | 녹화된 추출 원장(Q1·Q3·셜록)에 분류표대로 오류 주입, `off/annotate/exclude` 세 조건 재생 | exclude에서 주입 사실의 원장 유입 0건(거르는 유형), 거르지 못하는 유형은 표로. recall 손실 ≤ 5%p |
| R1 | 단위 테스트: 등급 규칙, exclude가 원장을 바꾸고 annotate는 안 바꿈, 사후 처리 | pytest |
| R2 | WM·Q1·Q3 양성 60장면 전부에서(judge 담당 추정 54는 참고 칸) 동결 열(재생) vs learned, 작품별·유형별, 대응 bootstrap | 하한 > −10%p. 못 넘으면 구간을 그대로 보고 |
| R2 | LLM 0회 테스트(추출 뒤 `w_llm.complete`를 터지는 함수로 교체) | pytest |
| R2 | 회차당 판정 시간(워커 GPU 기본; CPU 대체 경로 따로) | ≤ 60초 |
| R3 | 같은 입력 두 번 → 결과 JSON 동일 | pytest |
| R3 | 녹화 재생 회귀 게이트: 동결 열 재현값 + checker 장면 ID 집합 | CI |

## 위험

| 위험 | 대비 |
|---|---|
| 실제 코퍼스라 숫자가 낮다 (동결 열이 0.30) | 측정된 한계로 보고. 구조 주입으로 올리지 않는다 |
| 학습이 안 됨 | 가짜 백엔드로 연결을 끝까지 유지, 녹화·하네스·관문·테스트는 모델 없이 성과 |
| 세 브랜치 충돌 | `weave.py` 구역 분리, config.py 충돌은 합칠 때 수작업 |
| Weave 원장 개선(round2-graph)이 main에 들어옴 | 코어 기준 커밋 고정, 실험 뒤 재동기화 |
