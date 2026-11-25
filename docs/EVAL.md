# EVAL — Weave 체커 dev set 회귀 게이트

> 이 저장소가 자기 모델의 동작을 스스로 재는 유일한 장치다. 2026-11-05 작성.
> 코드: `tests/eval/checker_eval.py`(러너) · `tests/eval/test_weave_checker_gate.py`(게이트) · `tests/eval/weave_checker_corpus/`(데이터).

## 무엇을 재는가

Weave 엔진의 **체커 계층**만 잰다. 체커는 추출된 사건의 전제(인물 생사·위치·소지품·지식 등)를
1~5화를 재생해 만든 원장과 대조하는 규칙 검사기다(`app/pipeline/weave_core/checker.py`).
**LLM 호출이 없고** 결정적이므로 몇 초면 끝나고 CI에서 그대로 돈다.
판정(judge) 계층 — LLM이 2차로 검수하는 부분 — 은 여기서 재지 않는다.

## 데이터

- Weave 저장소 `event_sourcing` 커밋 `8d89349`의 `eval_corpus/`에서 **커밋 기준으로** 꺼내 고정했다
  (`scene_01_event.json`~`scene_30_event.json`, `labels.json`, `episode_01~05.json`, `entity_registry.json`).
  `_v2_r*` 변형(재추출 반복분)은 포함하지 않는다.
- 30장면 = 정상 10 + 모순 20. 모순 20 중 라벨이 `expected_layer="checker"`인 것은 **4건**(scene_04·10·12·20),
  나머지 16건은 judge 담당이다.
- 각 장면은 5화 종료 시점 원장을 기준으로 독립 판정한다(장면 간 상태 전파 없음) — Weave `run_eval_checker.py`와 같다.
- **이 코퍼스는 Weave가 체커를 고치면서 기준선으로 쓴 개발용 dev set이다.** 그래서 이 숫자는
  일반화 성능(정확도)이 아니라 "코어를 다시 동기화하거나 어댑터를 바꿔도 같은 장면을 같은 등급으로
  잡는가"를 보는 **회귀 게이트**다.

## 현재 기준선 (event_sourcing 8d89349)

`python -m tests.eval.checker_eval` 출력 그대로:

```
Weave 체커 dev set 회귀 게이트 (event_sourcing 8d89349, 30장면, LLM 호출 없음)
정상 10건 중 플래그           : 0
checker 담당 라벨 4건 중 잡음  : 3 (강 2, 약 1)  놓침: scene_10
라벨 모순 20건 중 checker 플래그: 4
강: scene_04, scene_12, scene_14 / 약: scene_20
```

- 강한 모순: scene_04, scene_12, scene_14 (scene_14는 라벨상 judge 담당이지만 체커가 잡는다)
- 약한 의심: scene_20
- 정상 10건 중 오탐: 0
- checker 담당 4건 중 놓침: scene_10

n이 작다(checker 담당 라벨 4건, 정상 10건). 정밀도·재현율·F1로 적으면 한 건 차이가 25%p로 보이므로
**원시 카운트와 장면 ID 집합**으로만 적고, 게이트도 집합 일치로 비교한다
(`test_weave_checker_gate.py`의 `BASELINE`).

## 재현

```bash
PYTHONIOENCODING=utf-8 python -m tests.eval.checker_eval
```

```bash
pytest tests/eval -q
```

PowerShell에는 `변수=값 명령` 접두 문법이 없다. 같은 창에서 한 번만 환경변수를 설정한 뒤 실행한다:

```powershell
$env:PYTHONIOENCODING = "utf-8"
.venv\Scripts\python -m tests.eval.checker_eval
```

(저장소 루트에서. `PYTHONIOENCODING`은 cp949 콘솔에서 한국어 출력이 죽는 것을 막는다.
러너(`python -m tests.eval.checker_eval`)는 DB·Redis·.env 없이 돈다. `pytest tests/eval`은 저장소 공용
`tests/conftest.py`가 Postgres 테스트 DB를 먼저 준비하므로 다른 테스트와 같은 DB 환경이 필요하다(CI에서는 서비스 컨테이너).
테스트 DB 이름은 `TEST_DB_NAME`(기본 `glitch_noai_test`) — 여러 세션·워크트리가 동시에 돌면
`TEST_DB_NAME=glitch_noai_test_<branch>`처럼 세션마다 다른 이름을 준다(없으면 만들고 alembic head까지 올린다).)

## 측정하지 않은 것

- **judge 계층**(LLM 2차 검수) — Weave 쪽 측정만 있고 이 몸통 안에서는 미측정.
- 다른 작품·장르. 코퍼스는 Weave의 단일 작품(1~5화) 하나다.
- 이 몸통이 쓰는 LLM 모델·설정 조합(`WEAVE_*`)의 성능.
- 어댑터 `app/pipeline/weave.py`를 거친 경로(회차 업로드 → 추출 → 검수). 이 게이트는 코어의
  `checker.check_event`를 직접 부른다. 어댑터가 사건을 다르게 만들거나 원장을 다르게 쌓으면 여기서 안 보인다.
- 추출 품질(원문 → 사건 JSON). 코퍼스의 사건 JSON은 이미 추출된 결과다.

## 기준선 갱신 절차

1. `app/pipeline/weave_core/`를 Weave 새 커밋과 동기화하고 `VENDORED.md`의 커밋을 바꾼다(코어 파일은 수정 금지).
2. 같은 커밋에서 코퍼스를 다시 꺼낸다(`git -C <event_sourcing> show <commit>:eval_corpus/...`) →
   `tests/eval/weave_checker_corpus/`에 덮어쓴다. 작업 트리가 아니라 커밋에서 꺼낸다.
3. `PYTHONIOENCODING=utf-8 python -m tests.eval.checker_eval`로 새 표를 본다.
4. 바뀐 장면이 **의도한 변화**(Weave 쪽 체커 수정)인지 확인한다. 어댑터·몸통 변경만으로 바뀌었다면 회귀다.
5. 의도한 변화면 `test_weave_checker_gate.py`의 `BASELINE`, `checker_eval.py`의 `WEAVE_COMMIT`,
   이 문서의 "현재 기준선" 절을 **한 커밋에서** 갱신한다.

---

# judge 층 기준선 (동결 열 재현)

> 2026-11-06 추가. 코드: `scripts/eval/judge_eval.py`(하네스) · `tests/eval/test_judge_baseline.py`(고정) ·
> `tests/eval/weave_judge_corpus/`(데이터, 작품별 README). 위 체커 게이트와 별개다.

## 무엇을 재는가

체커 다음 단계인 **판정(judge) 층**의 장면 검출률·오탐율. 계획서(`learned-judge-plan.md`) R2의 주 기준선인
Weave **동결 열**을 이 저장소에서 다시 계산할 수 있게 했다. 동결 열 = 제품 경로와 같은 조건의 LLM judge:
`openrouter/nvidia/nemotron-3-ultra-550b-a55b:free` · reasoning off · full 조건 · 장면 단위 · k=3 · 다수결 2/3
(원장: WM=Claude 추출본, Q1·Q3=nemotron-3-ultra C=2500). Weave 측정 완료 2026-10-07.

채점은 Weave `score_longitudinal.py`(+ `score_b_track.py`·`score_phase2.py`·`score_track1.py`)를 **규칙 무변경으로** 옮겼다:
양성 verdict = {모순, 설정누수, 지식범위위반}, status≠ok 회차는 버림, WM은 회차마다 장면 사건을 OR로 접음,
양성 회차 수 ≥ 2면 검출, Wilson 95% 구간. 판정기만 바꿔 끼울 수 있다:

| 모드 | 판정기 | LLM | 작품 |
|---|---|---|---|
| `recorded` | 동결 열 원시 판정 결과(`baseline/nemotron_ultra_long_*/main.jsonl`) 재생 | 0회 | WM·Q1·Q3 |
| `learned` | `app.pipeline.learned_judge.judge_from_settings()` (없으면 항상 '정상' stub). 확정 원장 5화 재생 + checker 항목을 함께 넘김, k=1 | 0회 | 4작품 |
| `llm` | 어댑터 `run_checks_parallel` 그대로(k=`WEAVE_JUDGE_K`). `WEAVE_LLM_RECORD`로 녹화·재생 가능 | 장면 × k | 4작품 |

두 판정기 비교는 **작품 층화 대응 bootstrap**(같은 양성 장면을 복원 추출해 recall 차이 B−A를 다시 계산, 10,000회,
시드 고정, 백분위 95%)으로 한다: `--compare-recorded`.

## 현재 기준선 (event_sourcing 9180c3a, 동결 열)

`python scripts/eval/judge_eval.py --mode recorded` 출력 그대로:

```
judge 층 평가 — recorded:nemotron_ultra (k=3, 다수결 2/3) · 작품 wm, q1, q3 · 코퍼스 event_sourcing 9180c3a
조건: 판정 openrouter/nvidia/nemotron-3-ultra-550b-a55b:free · reasoning off · k=3 다수결 2/3 · full 조건 · 원장: WM=Claude 추출본(v4c 패킷), Q1·Q3=nemotron-3-ultra C=2500
합산: Recall 18/60=0.300 [0.199–0.425] | 오탐 3/60=0.050 [0.017–0.137] | d=0.250
  wm: Recall 15/20 오탐 1/10 | 미탐 귀속 판정자 5 (매핑 없음) | 재실행 6/6
  q1: Recall 0/20 오탐 1/25 | 미탐 귀속 원장부재 6 / 판정자 14 | 재실행 9/9
  q3: Recall 3/20 오탐 1/25 | 미탐 귀속 원장부재 7 / 판정자 10 | 재실행 9/9
  LOWO without_wm: Recall 3/40 [0.026–0.199] 오탐 2/50
  LOWO without_q1: Recall 18/40 [0.307–0.602] 오탐 2/35
  LOWO without_q3: Recall 15/40 [0.242–0.530] 오탐 2/35
  유형별: 상태 8/24 · 설정 4/18 · 지식 6/18
  담당 층별(라벨 expected_layer, 제작 측 추정): checker 4/17 · judge 14/43
  사람 집필: 모순 6 중 TP 2 · 정상 4 중 FP 0
  재실행 재현율: 24/24
```

Weave `handoff/longitudinal_report.md` §2(합산·작품별·LOWO)·§3(유형별)·§4(미탐 귀속 장면 ID)·§5(집필주체)·§6(재실행)과
`final_report.md` §1의 수치가 전부 같다. `test_judge_baseline.py`가 이 값과 귀속 장면 ID 목록까지 고정한다.

읽는 법:

- 합산 0.300은 거의 WM(15/20)이 만든다. WM을 빼면 0.075(3/40). WM은 개발 코퍼스이고 원장 추출 열도 다르다(Claude).
- Q1·Q3 미탐 37건 = 원장 부재 13 + 판정자 24. 원장 부재는 판정기를 바꿔도 못 잡는 몫(추출 상한)이다.
- 담당 층별 칸은 라벨의 `expected_layer` 기준인데, Q1·Q3 라벨 자체가 이 필드를 "제작 측 추정으로 격하 — 분석에 쓰지 않는다"고 적는다.
  주 지표로 쓰지 말고 참고로만.
- 셜록은 동결 열에 없다(종단 측정은 WM·Q1·Q3). learned·llm 모드에서만 4번째 작품으로 나온다.
  bootstrap 비교는 두 판정기가 모두 있는 작품(WM·Q1·Q3, 양성 60)에서만 한다.

## 재현

```bash
PYTHONIOENCODING=utf-8 python scripts/eval/judge_eval.py --mode recorded
```

```bash
PYTHONIOENCODING=utf-8 python scripts/eval/judge_eval.py --mode learned --compare-recorded
```

```powershell
$env:PYTHONIOENCODING = "utf-8"
.venv\Scripts\python scripts\eval\judge_eval.py --mode recorded
```

`learned` 모드는 학습 judge 모듈이 없으면 stub(전부 '정상')이라 Recall 0/80 · 오탐 0/85,
동결 열 대비 −0.300 [−0.383, −0.217]이 나온다 — 연결 확인용 숫자이지 학습 judge의 성능이 아니다.

## learned 가짜 백엔드 회귀 게이트

`tests/eval/test_judge_learned_fake_baseline.py`. `WEAVE_JUDGE_MODE=learned`, `WEAVE_JUDGE_BACKEND=fake`
(결정적 휴리스틱, 모델 폴더 없음, 기준값 기본 0.5)로 위 `--mode learned`와 같은 호출(`LearnedJudge` + `score`)을 4작품에 돌려 고정한다.
LLM 0회, 약 2초. **성능 측정이 아니다** — 가짜 백엔드는 거의 모든 장면을 양성으로 낸다. 쌍 생성기(`app/pipeline/pairs.py`)·
판정 경로(`app/pipeline/learned_judge.py`)·하네스 문맥 구성을 바꿨을 때 결과가 달라졌는지 알아채는 용도다.

기준선 (2026-11-07, 쌍 생성기 v2 — kind 할당량 24·관련도 정렬 — 병합 뒤 main):

| 작품 | TP / 양성 | FP / 정상 | 검출 장면 | 판정 쌍 합계 |
|---|---|---|---|---|
| WM | 15 / 20 | 8 / 10 | 23 / 30 | 1,440 |
| Q1 | 20 / 20 | 25 / 25 | 45 / 45 | 7,145 ¹ |
| Q3 | 20 / 20 | 25 / 25 | 45 / 45 | 5,112 |
| 셜록 | 19 / 20 | 24 / 25 | 43 / 45 (28·29 빼고) | 6,507 |
| 합 | 74 / 80 | 82 / 85 | | 20,204 ¹ |

¹ 규칙 조립 수정으로 갱신(2026-11-07, 브랜치 `claude/judge-rules-dedup`, 아래 "판정 패킷 세계관 규칙 조립"): Q1 7,142 → 7,145,
합 20,201 → 20,204, Q1 지문 `65f3a1c60bcf05ef` → `0fd1d4381d44d8dc`. 나머지 칸·다른 작품 값은 그대로.

이전 기준선(2026-11-06, b1a4fb3, 쌍 생성기 v1 상한 32)은 TP 71/80 · FP 80/85 · 쌍 1,920/9,503/6,816/8,301 (합 26,540)이었다.
v2로 WM 양성 scene_12·14·17이 검출에 더해지고, 셜록 정상 scene_09·32가 양성으로 바뀌었다(가짜 백엔드 기준 — 성능 변화가 아니라 쌍 구성 변화).

재측정 (2026-11-07, WM 재생 timeline 수정 — 아래 "작품별 재생 timelines"): **위 표·`BASELINE_*` 상수 전부 그대로**
(검출 집합·TP/FP·쌍 수·지문 4작품 모두 같음). WM checker 전제 쌍이 14→49로 바뀌었는데도 지문이 같았다 — 지문은 장면별
(쌍 수, 판정, 확신도, 근거 전제 ID)만 보므로 할당량(24)이 같고 가짜 백엔드의 판정·확신도가 같으면 쌍 **구성** 변화는 못 본다.
그 몫은 `tests/eval/test_judge_eval_timelines.py`(작품별 checker 항목 수)가 맡는다.

## 판정 패킷 세계관 규칙 조립

`judge_eval.collect_episode_rules` — `load_context`(평가·`export_pairs --real`·`pair_coverage`)와
`datagen_common._rules_before`(`export_pairs` 기반 재생·`replay_upto`·`label_pairs`·`counterfactual`)가 같이 쓴다.
레지스트리에 `worldview_rules`가 없을 때(Q1·Q3·셜록 — WM은 레지스트리 규칙 12개를 그대로 씀) 확정 원장 화 문서에서 모은다.

2026-11-07 수정(브랜치 `claude/judge-rules-dedup`): 중복 제거를 `rule_id` 기준 → 제품 경로
`app/pipeline/weave.py judge_registry`와 같은 **본문 정규화(`weave_registry.norm_text`)** 기준으로 바꿨다.
nemotron 원장은 회차마다 `RULE_007`·`RULE_008` 같은 ID를 다른 규칙에 재사용해서(Q3 3·4·5화 `RULE_007` 본문이 전부 다름)
뒤 회차 규칙이 판정 패킷 (b)절에서 빠졌다. ID가 앞 규칙과 겹치면 `<rule_id>@ep<화>`로 구분한다. 추출 검증기 검토 세션이
찾았다. 본문이 같은데 ID만 다른 규칙은 네 작품 모두 없어서 수정은 규칙을 **더하기만** 한다.

| 작품 | 규칙 수 전 → 뒤 | 복구된 규칙 |
|---|---|---|
| WM | 12 → 12 | (레지스트리 규칙 사용 — 해당 없음) |
| Q1 | 3 → 4 | `NEW:열쇠_관리_규칙@ep3` "이 집 열쇠는 안에서 자는 사람 것만 있다" |
| Q3 | 38 → 41 | `RULE_007@ep4` 격벽 관리 권한 임시 변경 · `RULE_007@ep5` 규정 4조 안전 보고 · `RULE_008@ep5` "본부 청문 결과 전까지 현 운영 체제 유지…"(라벨 Q3 scene_04 정답 사실 후반부) |
| 셜록 | 9 → 9 | — |

영향 (`tests/eval/test_judge_rule_assembly.py`가 규칙 수·복구 본문·ID 고유성·`_rules_before`와의 ID 일치를 고정):

- `--mode recorded`: 패킷을 쓰지 않으므로 그대로(`test_judge_baseline.py` 통과).
- learned 가짜 백엔드 게이트: 위 표 ¹. Q3는 규칙 할당량(4)이 이미 차 있어 쌍 수·지문이 같고 **구성**만 바뀐다 —
  복구 규칙 3개가 Q3 33장면의 쌍에 들어간다(scene_04에 `RULE_008@ep5`).
- `pair_coverage` v2@24 근거 포함: Q1 matched 18 → 17 · all 19 → 18, 합 matched 63/69 → **62/69** · all 69/75 → **68/75**
  (G0 목표 ≥ 66/75 유지). 빠진 장면은 Q1 scene_32(근거 EVENT_1_3, past 전제). 전에는 Q1 규칙이 3개라 규칙 칸 1개가
  다음 kind로 넘어가 past 전제가 하나 더 들어갔는데, 이제 복구된 규칙이 그 칸을 채운다. 조립 결함이 아니라 할당량 넘김의
  부수 효과다(v1@∞는 19 그대로). 규칙 ID는 근거 지도에 없어 규칙 전제로 생긴 포함은 세지 못한다(`pair_coverage` 근사 그대로).
- 학습 데이터(`export_pairs`·`label_pairs`·`counterfactual`)는 다음 재생성부터 복구 규칙이 들어간다. 이미 만든 산출물은 그대로다.

## 작품별 재생 timelines

`judge_eval.WORKS[*].timelines` → `judge_eval.timelines_of(work)`. `load_context`·`export_pairs`(base_replay·export_inject)·
`datagen_common.replay_upto`가 모두 이 값을 `compile_ledger(timelines=...)`에 넘긴다.

| 작품 | timelines | 이유 |
|---|---|---|
| WM | `None` → 코어 기본 `ledger.STATE_TIMELINES`(real_present·future_world·unknown) | 코퍼스가 Weave 원본 다중 timeline으로 추출됨. 체커 게이트 `checker_eval.py`(`compile_ledger(base=...)`, timelines 미지정)와 같은 timeline 조건 |
| Q1·Q3·셜록 | `["main"]` | 서비스 추출본(사건 timeline 'main') |
| WORKS 밖(dracula) | `["main"]` | 서비스 추출본 |

체커 게이트와 다른 점은 timeline이 아니라 매핑·knows: 하네스는 레지스트리 `new_label` 매핑으로 normalize하고 knows `accumulate`,
체커 게이트는 원본 그대로 knows `legacy`(코어 기본)다.

2026-11-07 수정 전(전 작품 `["main"]`) → 수정 뒤. WM 장면 사건 timeline이 `future_world`인데 `["main"]` 원장에는 그 칸이 없어
`checker.check_event`가 원장을 못 찾았다(docs/learned-judge-branch-review-G.md §4a). Q1·Q3·셜록은 원장 digest까지 그대로.

| 측정 (WM) | 전 | 뒤 |
|---|---|---|
| `load_context` checker 항목 (needs_judge) | 9 (7) | 31 (28) |
| `pair_coverage` 쌍 kind v2@24: checker / ledger / knowledge / past | 14 / 217 / 620 / 343 | 49 / 199 / 605 / 341 |
| `pair_coverage` 근거 포함 v1@32 / v2@24 / v1@∞ (matched·all 같음) | 18 / 19 / 19 (of 20) | 17 / 18 / 19 (of 20) |
| 합 근거 포함 v2@24 matched / all | 64/69 · 70/75 | 63/69 · 69/75 (G0 목표 ≥ 66/75 유지) |
| `export_pairs --work wm --real` checker 전제 행 (모순/정상/제외/무관) | 14 (2/2/2/8), 7장면 | 49 (12/9/5/23), 20장면 |
| 같은 내보내기 전체 라벨 (모순/정상/제외/무관, 합 1,440) | 106/17/335/982 | 111/20/334/975 |

WM 근거 포함은 예상(18→19)과 반대로 **19→18**이다. 빠진 장면은 scene_16(지식, 근거 EVENT_1_3·1_4). 근거 사건이 둘 다
`real_present`라 수정 뒤에는 `future_world` 장면 사건의 원장 전제 칸에 없다 — 수정 전에는 전 사건이 `unknown` 한 칸에 섞여
EVENT_1_4(게임 프로그램 소유)가 원장 전제로 우연히 24개 안에 들었다. 상한이 없으면(v1@∞) 여전히 19로 닿는다.
timeline을 넘는 지식 근거(현실 세계 사실을 미래 세계 인물이 앎)를 할당량 안으로 끌어올지는 쌍 생성기 쪽 문제로 남긴다.

고정하는 것: 작품별 검출 장면 ID 집합, 작품별·합산 TP/n_pos/FP/n_neg, 작품별 판정 쌍 수, 장면별
(쌍 수, 판정, 확신도, 근거 전제 ID)의 sha256 지문 앞 16자리. Q1·Q3는 전 장면이 양성이라 집합만으로는 변화를 못 보므로
쌍 수와 지문이 그 몫을 한다. `PYTHONHASHSEED`를 바꿔도 같은 값이다(확인함).

**바뀌는 것이 정상인 경우:** 쌍 생성기·FakeBackend·판정 집계를 의도적으로 바꾼 경우(예: 쌍 생성기 개정 작업),
코퍼스·라벨이 바뀐 경우. 그때 갱신한다. 의도하지 않은 변화(어댑터·`weave_core` 교체로 원장·checker 항목이 달라짐 등)라면
고치지 말고 원인을 찾는다.

갱신 절차:

```bash
PYTHONIOENCODING=utf-8 python -c "import tests.eval.test_judge_learned_fake_baseline as t; t.print_current()"
```

1. 위 출력으로 테스트 파일 위쪽 `BASELINE_*` 상수와 이 절의 표를 함께 바꾼다.
2. 커밋 메시지에 무엇을 바꿔서 기준선이 움직였는지와 검출 집합 차이(늘어난·빠진 장면)를 적는다.

## 녹화·재생 (WEAVE_LLM_RECORD)

`app/pipeline/llm_record.py`가 `weave_core/weave_llm.complete`를 감싼다(어댑터 `weave.py`의 LLM 호출 보강 구역).
코어 `complete_json`이 모듈 전역 `complete`를 부르므로 추출·판정 호출이 모두 지난다.

- `off`(기본) 그대로 호출. `record` 호출하고 status=ok 응답을 `WEAVE_LLM_RECORD_DIR/<role>/<sha 앞 2자>/<sha>.json`에 남김.
  `replay` 녹화본만 돌려준다 — 안쪽 함수를 부르지 않으므로 네트워크 0회. 없는 호출이면 즉시 `ReplayMiss`(키·role·프롬프트 앞 200자).
- 키 = role + sha256(system + `\x00` + user). 항목에 프롬프트 원문, 응답 dict 전체, 기록 시각, provider·모델.
- 같은 프롬프트(판정 k회, JSON 재요청)는 녹화 순서대로 돌려주고 끝까지 쓰면 처음부터 다시 쓴다.
- 프롬프트가 한 글자라도 바뀌면(레지스트리·원장·프롬프트 템플릿 변경) miss다. 계획서 v2는 구조 키(역할·회차·조각/패킷)를
  주키로 적었지만 세션 A 지시에 따라 프롬프트 해시를 주키로 했다 — miss가 나면 녹화를 다시 떠야 한다는 뜻이고,
  "입력이 바뀌었는데 옛 응답을 재생"하는 일은 없다.
- 검증: `tests/eval/test_llm_record.py` — 어댑터 추출(1·2화)·검수(2화, k=3)를 record로 돌린 뒤 `urllib.request.urlopen`을
  터지는 함수로 바꾸고 실제 provider 경로를 안쪽에 둔 채 replay해도 결과가 같다.

## 측정하지 않은 것

- 동결 열을 **이 저장소의 어댑터로 다시 돌린 값**. 여기 기준선은 Weave가 자기 패킷(`judge_requests_*`)으로 낸 원시 결과의 재채점이다.
  어댑터 패킷은 코어 `build_packet_prompt`로 만들며 Weave 측정 패킷과 바이트가 같다는 확인은 하지 않았다(`--mode llm`이 그 측정).
- 개선 열(rnc 패킷 + Claude Sonnet 서브에이전트, 0.767)은 제품 경로 밖이라 넣지 않았다(참조 상한).

## 첫 접기(평가 Q3) 결과

> 2026-11-07 세션 J. 설계안 §0-6 순서, 접기 = 평가 Q3 · 학습 WM+셜록 · 검증 Q1. 처음엔 G0 길이(≤256)에서 멈췄고,
> 판단 세션 결정(GPU max_length 384, 발췌 160자 유지)으로 재개해 G3·본 학습(시드 3)까지 했다. **사용자 지시로 hyp-only(G4) 학습 2에폭째에 중단.**
> 아래 "재개: max_length 384" 절이 최신이다.
> **후속: 실제 장면 쌍 라벨 결함(v1)을 고친 재채점·재학습은 아래 "라벨 v2 재채점·재학습" 절.** 이 절의 쌍 지표(PR-AUC·NLL)는 v1 라벨 기준이다.
> 평가 작품 Q3 는 모델로 돌리지 않았다(Q3 수치 없음이 정상). G1·G2 는 G0 수정 방향을 고르는 진단으로만 쟀다.

환경: `.venv-train`(Python 3.11, 앱 `.venv` 와 분리) — torch 2.9.1+cu128 · transformers 4.57.6 · tokenizers 0.22.2 · datasets 4.8.5 ·
cuDNN 91002 · 드라이버 610.88 · RTX 3060 12GB · Intel64 Family 6 Model 151. llama-server 없음(VRAM 1 GB 는 데스크톱 앱).
KLUE-NLI: `load_dataset("klue", "nli")`·`("klue/klue", "nli")` 둘 다 된다(datasets 4.8.5, train 24,998, 라벨 entailment/neutral/contradiction).
`train_judge.py --nli-dataset` 기본값은 `klue/klue`.

데이터(저장소 밖 `C:\judge_models\data_q3fold\`, main 6cdd8cd 이후 = 규칙 중복 제거 반영): `inject.py --work wm|sherlock --per-operator 40 --seed 7`
(각 600 사례, 그 작품 레지스트리만) → `export_pairs.py --work wm|sherlock --real --inject ...`, `export_pairs.py --work q1 --real`.
Q3 는 내보내지 않았다. `train_judge.py --prepare-only` 분할: 학습 모순 1,288 · 정상 4,165 · 무관 39,898(제외 1,084) / 검증 Q1 실제 장면
모순 165 · 정상 386 · 무관 5,279(제외 1,315).

| 파일 | 행 | sha256(앞 16) |
|---|---|---|
| pairs_wm.jsonl | 21,322 | a966498c659f5907 |
| pairs_sherlock.jsonl | 25,113 | a0827a36f6050c95 |
| pairs_q1.jsonl | 7,145 | 268b2e5a267c064e |

### 관문

| 관문 | 측정 | 값 | 기준 | 판정 |
|---|---|---|---|---|
| G0 쌍 | `pair_coverage.py` v2@24 근거 포함 | all 68/75 (90.7%) · matched 62/69 (89.9%) | ≥ 66/75 | **통과** |
| G0 길이 | `pair_coverage.py --tokenizer klue/roberta-base` v2@24 토큰 p95 | wm 222 · q1 **348** · q3 **394** · 셜록 **320** | ≤ 256 | **실패 → 멈춤** |
| G1 지연 (진단) | `judge_latency.py` 합성 회차(사건 30·과거 300, 2,160쌍) GPU fp16·eager·256 | base 6.3 s · large 18.9 s | ≤ 60 s | (통과) |
| G1 CPU (진단) | 같은 회차, fp32·스레드 8 | small 116 s · base 233 s | 대체 경로 | 60 s 넘음 |
| G2 결정성 (진단) | 확률 배열 float64 `tobytes()` sha256 — 같은 프로세스 반복·프로세스 재기동 | GPU base·large(합성·Q1 실제 쌍), CPU base 모두 동일 | 동일 | (통과) |
| G3~G6 | — | 돌리지 않음 | — | — |

G1·G2 는 분류 머리가 무작위인 측정용 사본(`C:\judge_models\_latency\`, meta `kind: latency_stub`)으로 쟀다 — 시간·결정성은 가중치와 무관하다.
G2 의 학습 모델 확인은 학습 뒤 다시 한다. 합성 회차의 쌍은 실제보다 짧다(256·384 시간이 같음). 실제 Q1 검증 쌍 5,830개(장면 45)는
base fp16 19.3 s(256) / 24.4 s(384), large 55.2 s / 70.0 s.

### G0 길이 실패 원인

토큰 p95 를 키우는 쪽은 **가설**이다(Q1 실제 쌍: 가설 p95 191 · 전제 180 토큰, 셜록 209 · 138). 가설 = 머리(엔티티·필드·값·근거 인용) +
`사건:` 요약 + `원문:` 160자 발췌. 발췌만 줄였을 때(학습·검증 작품만 측정, `build_pairs(excerpt_chars=…)`):

| 작품 | 160 | 120 | 96 | 80 | 64 |
|---|---|---|---|---|---|
| wm | 222 | 222 | 222 | 222 | 221 |
| 셜록 | 320 | 296 | 279 | 268 | 255 |
| q1 | 348 | 308 | 283 | 266 | 247 |

p95 ≤ 256 은 발췌 64자에서야 된다. 160자는 B-027(원문 수치 증발 방지)에서 온 값이라 64자로 줄이는 것은 그 결정을 되돌린다.
선택지(판단 세션 결정 필요, 어느 쪽이든 PAIRS_VERSION 이 바뀌거나 학습 조건이 바뀐다):
(가) 발췌 64~80자, (나) 가설의 `사건:` 요약을 원문 발췌가 있을 때 빼기 등 가설 머리 축소, (다) `--max-length 384`
(지연 GPU base Q1 검증 쌍 +26%, 회차 기준 여유 큼 — 설계안 §2-3 의 "256 목표는 지연과 같은 결정"을 GPU 기본 전제에서 다시 보는 것).

### 재개: max_length 384 (판단 세션 결정 2026-11-07)

GPU 경로 `max_length` 기본 384(HFBackend meta 없을 때·`train_judge`·`judge_latency`), 발췌 160자·사건 요약 유지, 버킷 (128, 256, 384, 512) 그대로.
쌍 텍스트는 바뀌지 않아 `data_q3fold` 를 다시 내보내지 않았다(meta 의 pairs_version 2·데이터 sha256 이 위 표와 같음을 확인).
CPU 대체 경로(small 116 s · base 233 s @256)는 60 s 미달 — 기록만, 후속(int8·할당량 축소).

| 관문 | 값 | 기준 | 판정 |
|---|---|---|---|
| G0 길이 @384 | p95 wm 222 · q1 348 · 셜록 320 (q3 394 — 평가 작품, 기록만) · p99 240/377/356 · >384 0.0%/0.6%/0.2% (q3 7.7%) | p95 ≤ 384 | 통과 |
| G1 @384 GPU fp16 | 합성 회차 base 6.3 s · large 18.9 s(합성 쌍은 짧아 256 과 같음) · 실제 Q1 쌍 5,830개(45장면): base 19.3→24.4 s(+26%), large 55.2→70.0 s(+27%) | ≤ 60 s/30사건 | 통과 |
| G2 | 측정용 사본 기준 통과(위). 학습 모델로는 **미측정**(중단) | | — |
| G3 영 학습 기준선(Q1) | KLUE-NLI 기초(base, 2에폭 4.7분, KLUE-NLI 검증 정확도 0.857 fp16·eager) → `score_pairs`: 장면 recall **2/20 = 0.10** @ FPR 1/25, 기준값 0.993 · 쌍 PR-AUC 0.028(= 양성 비율 165/5,830) · ECE top 0.091 / 모순 0.147. 하네스 `--mode learned` @0.5: recall 17/20 · 오탐 25/25 | 기록만 | — |
| 본 학습 시드 1/2/3 | Q1 장면 recall 0.05 / 0.05 / 0.00 (TP 1/1/0, FP 1/1/0) · 쌍 PR-AUC 0.038 / 0.037 / 0.038 · 최선 에폭 2/1/1(조기 종료 4/3/3) | | |
| G4 지름길 | hyp-only 시드 1 이 2에폭째(val NLL 0.392→0.408)에 **중단** — PR-AUC 차이 미측정 | ≥ 0.10 | 미판정 |
| G5 안정성 | 시드 recall 범위 0.05(전부 0 근처) | ≤ 0.20 | 형식상 통과 |
| G6 검증 성능 | 선택 시드 Q1 recall 0.05 vs Q1 동결 열 0/20 | ≥ 동결 − 0.10 | 형식상 통과(동결 열이 0 이라 기준이 비어 있음) |

선택: 시드 2(검증 recall 동률 → 검증 쌍 NLL 0.227 최소), 온도 1.345, 장면 기준값 0.8569, 검증 쌍 ECE top 0.011 / 모순 0.024,
NLL 0.227(보정 전 0.247). 모델 `C:\judge_models\judge_base_q3\`, model_dir_hash `ed1582285d21aaf0e1de27ec94604eba7b994831b2a48d2e4eb8c82806ba13a8`
(`judge_base_q3.sha256`). 기초 모델 `C:\judge_models\nli_klue_roberta_base\_nli\` hash `048a3c0a31e17ea601bd40388841019b534abca0453513ceba4cd781be1e8c9b`.
학습 시간: 에폭당 약 240~270 s(16,359행, 배치 16, VRAM 11.9 GB 거의 가득), 시드 3개 59분.

읽는 법: 학습 손실은 0.25→0.02 로 떨어지는데 검증 NLL 은 1~2 에폭 뒤 오른다 — 주입 템플릿을 외우고 Q1 실제 쌍으로 옮겨 가지 못한다.
검증 쌍 PR-AUC 가 양성 비율과 거의 같아(0.037 vs 0.028) 쌍 단위 신호가 사실상 없고, 장면 recall 은 영 학습 기준선(0.10)보다도 낮다.
보정(ECE)만 좋아졌다. G5·G6 은 recall 이 전부 0 근처라 형식상 통과일 뿐 의미가 없다. 평가 Q3 는 돌리지 않았다(G4 미판정).
남은 것: G4(hyp-only) 재실행, 학습 모델 G2, 학습 곡선(`--train-frac`), 데이터 부족 판단(설계안 §2-2 학습 곡선 → 갈래 B).

### 이 세션의 코드 변경

- `HFBackend`: meta 의 `precision`(GPU 기본 fp16 → `model.half()`, CPU fp32)·`attn_implementation`(기본 eager)·`padding`·`max_length` 로 로드,
  `cudnn.benchmark=False`. 설정 `WEAVE_JUDGE_MAX_LENGTH`·`WEAVE_JUDGE_PADDING` 은 비우면 meta 값.
- `train_judge.py`: 검증 logits 를 서비스 조건(GPU fp16 = half 복제본, 그 밖 fp32)으로, `--attn-implementation`(기본 eager),
  KLUE-NLI 기초 모델을 `<out>/_nli` 에 `judge_meta.json`·`.sha256` 과 함께 보존, `--nli-from`·`--nli-only`·`--nli-dataset`.
- 순전파 `return_dict=True` — `klue/roberta-small` 설정은 튜플을 돌려줘 HFBackend·학습 둘 다 터졌다(이 세션에서 발견).
- `inject.py`: `--registry`·`--work`·`registry_live.json` 자동 탐지, manifest 에 레지스트리 경로·sha256.
- 새 스크립트: `judge_latency.py`(G1·G2), `score_pairs.py`(G3 영 학습 기준선·검증 지표, 평가 작품 거부). `pair_coverage.py --tokenizer`.

재현:

```
PYTHONIOENCODING=utf-8 .venv-train/Scripts/python scripts/eval/pair_coverage.py --tokenizer klue/roberta-base
CUBLAS_WORKSPACE_CONFIG=:4096:8 .venv-train/Scripts/python scripts/eval/judge_latency.py --model klue/roberta-base \
    --stub-dir C:/judge_models/_latency --precision fp16 --device cuda --repeat 3
```

## 라벨 v2 재채점·재학습

> 2026-11-07 세션 K(지시문 K). 접기는 첫 접기와 같다(평가 Q3 · 학습 WM+셜록 · 검증 Q1). 바꾼 것은 **실제 장면 쌍 라벨뿐**이다
> (쌍 텍스트·PAIRS_VERSION 2·주입 사례·정상 장면 라벨 불변). 데이터 `C:\judge_models\data_q3fold_lv2\`(주입 manifest 는 `data_q3fold` 것 재사용).
> 꼬리표: [측정] 이 세션에서 돌린 값, [기록] 이전 세션 기록, [추정] 계산 없이 판단, [감사] 작업 세션이 읽고 판정(사람 아님).

### 왜

`export_pairs.real_label`(v1)은 전제 쪽만 봤다 — `premise_id ∈ 근거 사건`이면 가설과 무관하게 '모순'. 양성 장면의 모든 가설 ×
근거 사건에서 나온 모든 전제 문장이 '모순'이 되어 Q1 양성 쌍 165개(17장면) 가운데 진짜는 일부였다 [기록: 판단 세션 표본 10쌍 중 2~4].
학습의 실제 양성과 검증 쌍 지표(조기 종료·온도·시드 동률)가 잡음이었다.

### 라벨 v2 규칙 (`export_pairs.label_scene_v2`, 양성 장면만)

- **가설 키**: 가설이 `seeded_text` 를 담는다. 상태 가설은 `(근거: …)` 인용, 인용 없는 상태 가설·사건 가설은 `원문:` 발췌.
  공백·구두점 제거 뒤 포함(인용 ≥ 8자면 인용 ⊂ seeded_text 도), 또는 seeded_text 2-gram 포함률 ≥ τ_h = 0.5.
  지시문은 "근거 인용 **또는** 원문"이었는데, WM·셜록은 원문 발췌가 장면 대부분을 담아 상태 가설에 원문을 쓰면 양성 장면 행의 50% 이상이
  가설 키가 됐다 [측정: 원문 포함률 중앙값 WM·셜록 모두 1.0] — 그래서 상태 가설은 근거 인용만 본다. 인용 ⊂ seeded_text 는 추출기가
  심은 문장 여러 개 중 하나만 인용하는 경우(WM 03·26·27·29, 포함률 0.44~0.49)를 잡는다.
- **전제 키**: `premise_id ∈ 근거` 그리고 (checker 전제 | 그 가설의 근거 사건 전제(checker 제외)가 하나뿐 |
  전제 머리(` | ` 앞, via·since 괄호 제거)와 violated_fact 또는 evidence_quote 하나의 2-gram Dice ≥ τ_p = 0.15).
- 둘 다 → 모순 · 가설 키만 / 근거 전제 / v1 제외 조건 → 제외 · 나머지 → 무관. 정상 장면은 v1 그대로.
- τ 는 학습 작품 쌍 분포만 보고 정했고 감사 뒤 **조정 0회**. 행에 `label_version: 2`·`label_v1`, 출력 옆 `<파일>.meta.json`.

### old → new 라벨 수 [측정]

| 작품 | v1 모순/정상/무관/제외 | v2 모순/정상/무관/제외 | 모순 쌍 남은 양성 장면 v1 → v2 | 전이 |
|---|---|---|---|---|
| WM | 111 / 20 / 975 / 334 | 45 / 20 / 625 / 750 | 18 → 16 (/20) | 모순→제외 66, 무관→제외 350 |
| 셜록 | 57 / 305 / 5,395 / 750 | 19 / 305 / 4,774 / 1,409 | 10 → 10 (/20) | 모순→제외 38, 무관→제외 621 |
| Q1 | 165 / 386 / 5,279 / 1,315 | 21 / 386 / 4,824 / 1,914 | 17 → 12 (/20) | 모순→제외 144, 무관→제외 455 |

(실제 장면 행만. 주입 행 WM 19,882·셜록 18,606 은 라벨 불변.) 확인 [측정]: 세 파일 모두 옛 파일과 행 수·쌍 텍스트·순서 동일,
`label_v1` = 옛 라벨, 주입·정상 장면 라벨 동일. 학습 분할: 모순 1,288 → 1,184(실제 168 → 64), 무관 39,898 → 38,927, 제외 1,084 → 2,159;
검증 Q1 모순 165 → 21, 정상 386, 무관 5,279 → 4,824, 제외 1,315 → 1,914.

### 감사 [감사: 작업 세션 판정, 사람 아님]

새 '모순' 쌍(시드 고정 무작위 30개, 30 이하면 전부)을 읽고 "전제가 그 가설과 쌍만으로 충돌하는가"를 판정. 판정표: 공유 노트 `session-K-labelfix-audit.md`.

| 작품 | 표본 | 진짜 모순 | 정밀도 | 주된 오류 |
|---|---|---|---|---|
| WM (학습) | 30/45 | 29 | **0.97** | 획득 경로 부재형(25) — 전제 하나로 성립 안 함 |
| 셜록 (학습) | 19/19 | 16 | **0.84** | 근거 사건의 다른 전제(19·22) |
| Q1 (검증) | 21/21 | 9 | **0.43** | 근거 지도 matched 후보의 뒤집힌 옛 사실(17·29·06), 가설 머리가 다른 필드(13·14·22), 유일 규칙(02) |

학습 작품 ≥ 0.7 → τ 조정 없이 진행. **Q1 쌍 라벨은 여전히 절반 이상 잡음**이다 — Q1 의 근거 사건 집합(LLM 저작 + 문자열 대조 지도)이
학습 작품보다 넓다. Q1 은 검증 작품이라 τ 를 Q1 로 고치지 않았다. 영향: Q1 쌍 지표(PR-AUC·NLL → 조기 종료·온도)는 v1 보다 덜하지만
여전히 잡음이다. 장면 지표(아래 Q1 관문)는 쌍 라벨과 무관하다.

### 장면 지표 계산 변경 — '제외' 행 포함

v2 는 양성 장면에만 '제외'를 늘린다. 장면 점수 max 를 '제외' 뺀 행으로 재면(첫 접기까지의 방식) 쌍 라벨이 장면 지표를 바꾼다
(예: `judge_base_q3` Q1 recall @FPR 1/25 가 v1 행 0.05 → v2 행 0.00 [측정]). 서비스는 장면의 모든 쌍을 보므로 `score_pairs`·`train_judge` 의
장면 점수·기준값을 **장면의 모든 실제 쌍('제외' 포함)** 으로 바꿨다. 쌍 지표(PR-AUC·NLL·ECE·온도)는 계속 '제외'를 뺀다.
옛 방식 값은 `scene_drop_excluded` 로 함께 남긴다.

### 기존 모델 재채점 (학습 없음) [측정]

`score_pairs.py --work q1`(GPU fp16·eager·384, bootstrap 층화 2,000회 시드 0). 장면 20 양성 / 25 정상.

| 모델 | Q1 라벨 | 쌍 양성(비율) | 쌍 PR-AUC | 쌍 NLL | 장면 AUROC [95%] | recall@FPR≤1/25 (기준값) | 옛 방식 recall |
|---|---|---|---|---|---|---|---|
| `_nli` (영 학습) | v1 | 165 (0.028) | 0.028 | 0.806 | 0.402 [0.244, 0.572] | 0.10 = 2/20 (0.9931) | 0.10 |
| `_nli` | v2 | 21 (0.0040) | 0.0059 | 0.800 | 0.402 [0.244, 0.572] | 0.10 = 2/20 (0.9931) | 0.10 |
| `judge_base_q3` (J, v1 학습) | v1 | 165 (0.028) | 0.037 | 0.227 | 0.484 [0.316, 0.650] | 0.05 = 1/20 (0.8569) | 0.05 |
| `judge_base_q3` | v2 | 21 (0.0040) | 0.016 | 0.154 | 0.484 [0.316, 0.650] | 0.05 = 1/20 (0.8569) | 0.00 |

장면 지표는 v1·v2 라벨에서 같다(확률 sha256 도 같음: `_nli` 409cd78a…, `judge_base_q3` 9dea1177…) — 쌍 텍스트 버그 없음.
옛 방식 recall 의 v1 값(0.10·0.05)은 J 기록 [기록] 과 같다. `_nli` meta 기준값 0.5 에서는 recall 17/20 · 오탐 25/25 [측정, J 하네스 기록과 같음].
두 모델 모두 장면 AUROC 가 0.5 아래~근처 — 정상 장면 max(p50 `_nli` 0.978, base 0.245)가 양성 장면 max(0.947, 0.232)와 같거나 높다.

### r1 — 라벨만 바꿔 재학습 [측정]

`train_judge.py --train data_q3fold_lv2/pairs_{wm,sherlock,q1}.jsonl --val-work q1 --train-works wm sherlock --eval-work q3
--nli-from nli_klue_roberta_base/_nli --seeds 2`(나머지 기본값 = J 레시피: base, lr 2e-5, 배치 16, 무관 비율 2, weighted, 384, 6에폭·인내 2).
에폭당 16,047행 ~232 s. 모델 `C:\judge_models\judge_base_q3_lv2_r1\` hash `037fbdb831edd66edda478fa06008b47f843b754e21e6907b5bf0b867e183687`.

| 에폭 | 학습 손실 | 검증 쌍 NLL(보정 전) |
|---|---|---|
| 1 | 0.223 | **0.200** (최선) |
| 2 | 0.031 | 0.410 |
| 3 | 0.018 | 0.314 → 조기 종료 |

| 지표 (Q1, `score_pairs`) | 값 |
|---|---|
| 장면 AUROC [95%] | **0.562 [0.394, 0.728]** |
| recall@FPR≤1/25 | 0.25 = 5/20 (FP 1/25, 기준값 0.94894) |
| meta 기준값(0.948941, train_judge 검증)에서 | 0.20 = 4/20 (FP 1) |
| 쌍 PR-AUC (양성 비율) | 0.020 (0.0040) |
| 쌍 NLL · ECE top/모순 | 0.160 · 0.015 / 0.026 (T 1.569) |
| 옛 방식('제외' 뺌) recall · AUROC | 0.20 · 0.308 |

**Q1 관문(사전 확정: AUROC ≥ 0.65 그리고 하한 > 0.5) — 실패.** recall 은 영 학습(0.10)·J(0.05)보다 높지만 구간이 0.5 를 넓게 덮는다.
기준값 0.94894 바로 위·아래에 양성 장면 하나가 걸려 train_judge(fp16 half 복제본)와 HFBackend(fp16 로드)의 확률 미세 차(~6e-6)로
4/20 ↔ 5/20 이 갈린다 [측정] — 장면 20개에서 recall 한 칸은 0.05 다.
학습 손실이 1에폭 뒤 0.03 으로 떨어지고 검증 NLL 이 오른다 — J 와 같은 주입 템플릿 외우기 양상.

### r2 — 주입 비율 축소(r1 실패라 1회) [측정]

r1 과 같은 명령 + `--inject-frac 0.25`(학습 주입 행을 contrast_group 단위로 25%, `--data-seed 42`; 실제 행·검증 그대로, 복제 오버샘플링 없음).
학습 행 44,276 → 15,670(모순 357 · 정상 1,318 · 무관 13,995), 에폭당 5,025행 ~80~100 s.
모델 `C:\judge_models\judge_base_q3_lv2_r2\` hash `ddb11b81b79a2f3a4505aa340f4244f8b6051ecc66303185d3e3ba425d5c74fe`.

| 에폭 | 학습 손실 | 검증 쌍 NLL(보정 전) |
|---|---|---|
| 1 | 0.447 | **0.205** (최선) |
| 2 | 0.135 | 0.345 |
| 3 | 0.049 | 0.744 → 조기 종료 |

| 지표 (Q1, `score_pairs`) | 값 |
|---|---|
| 장면 AUROC [95%] | **0.528 [0.358, 0.694]** (train_judge 내부 0.532 — fp16 경로 차) |
| recall@FPR≤1/25 | 0.00 = 0/20 (FP 0, 기준값 0.7125 = 정상 장면 최댓값) |
| 쌍 PR-AUC (양성 비율) | 0.013 (0.0040) |
| 쌍 NLL · ECE top/모순 | 0.164 · 0.044 / 0.014 (T 1.538) |

**Q1 관문 — 실패.** 양성 장면 max p(모순) 최댓값 0.146 인데 정상 장면 하나가 0.712 — 주입을 줄이자 모델이 거의 아무것도 '모순'으로 부르지 않는다.

**r1·r2 둘 다 관문 실패 → 지시문대로 평가 Q3 는 열지 않았다**(Q3 내보내기·채점·하네스 모두 안 함, Q3 쌍·라벨 미열람). 동결 열 Q3 3/20 [기록].

### 집계 진단 (Q1만 — 주 지표는 max 고정, Q3 에 쓰지 않음) [측정]

| 모델 | 양성 장면 max p50 / p95 | 정상 장면 max p50 / p95 | AUROC max | top-2 평균 | top-3 평균 |
|---|---|---|---|---|---|
| `_nli` | 0.947 / 0.993 | 0.978 / 0.992 | 0.402 | 0.432 | 0.432 |
| `judge_base_q3` (J) | 0.232 / 0.827 | 0.245 / 0.850 | 0.484 | 0.482 | 0.472 |
| r1 | 0.094 / 0.979 | 0.078 / 0.932 | 0.562 | 0.562 | 0.550 |
| r2 | 0.033 / 0.055 | 0.033 / 0.429 | 0.528 | 0.568 | 0.574 |

집계를 바꿔도 AUROC 차이는 ±0.05 안 — 구간 반폭(~0.17)보다 훨씬 작다. 집계가 아니라 쌍 점수 자체에 장면 신호가 없다.
양성·정상 장면 max 중앙값이 네 모델 모두 거의 같다.

### 읽는 법 [추정]

- 라벨 결함은 실제였고 고쳤다(학습 작품 정밀도 0.84~0.97). 그런데 학습 실제 양성이 168 → 64 로 줄었고 학습 신호의 대부분(모순 1,120/1,184)은
  여전히 주입 템플릿이다. r1 은 1에폭 뒤 외우기, r2 는 양성을 거의 안 부른다 — 둘 다 **실제 장면으로 옮겨 가지 않는다**는 J 의 결론과 같다.
- 라벨만 바꿔서는 Q1 장면 AUROC 가 0.5 근처(0.40~0.56)에서 움직이지 않는다. 45장면에서 구간 반폭 ~0.17 이라 0.65·하한 0.5 관문은
  AUROC ≈ 0.70 이상이어야 넘는다.
- Q1 쌍 라벨 정밀도 0.43 → 조기 종료·온도는 여전히 잡음 위에서 고른다(최선 에폭은 r1·r2 모두 1).
- 다음 판단거리(판단 세션): (가) 학습 데이터 쪽 — 실제 양성 확충(갈래 A·B, 유료 LLM 금지 조건), 주입 연산자를 실제 장면 분포에 가깝게,
  (나) Q1 근거 지도 정리(matched 후보 중 뒤집힌 옛 사실 사건 빼기) — 검증 쌍 지표용, (다) 발표용으로는 동결 LLM 판정 유지.

### 진단 D1: 학습 WM → 검증 셜록

> 2026-11-07 세션 K(지시문 K-D1). 질문: r1·r2 의 Q1 실패가 Q1 쪽 원인(근거 지도 정밀도 0.43 등)인가, 모델·데이터 쪽 원인인가?
> 라벨이 깨끗한 두 작품(감사 정밀도 WM 0.97 · 셜록 0.84)끼리 전이되는지 본다. 성능 주장용이 아니다.

조건: r1 과 같은 레시피(`--nli-from nli_klue_roberta_base/_nli --seeds 2`, inject-frac 1.0, 나머지 기본값), 작품 배치만 바꿈 —
`train_judge.py --train data_q3fold_lv2/pairs_{wm,sherlock}.jsonl --val-work sherlock --train-works wm --eval-work q3`.
학습 = WM 실제 + WM 주입(모순 607 = 주입 562 + 실제 45 · 정상 1,944 · 무관 18,021, 제외 750). 검증 = 셜록 실제 장면만
(셜록 주입 18,606행은 빠짐; 쌍 모순 19 · 정상 305 · 무관 4,774, 제외 1,409; 장면 양성 20 · 정상 25). Q1·Q3 는 어디에도 넣지 않았다.
코드 변경 없음. 모델 `C:\judge_models\diag_wm_to_sherlock\` hash `b8b283310a1514b6f2782ca4e25404c1df60e949b37aa480685e5fca329013c0`.
판독 기준(사전 확정): 셜록 장면 AUROC(max, '제외' 포함) ≥ 0.65 그리고 bootstrap 하한 > 0.5 → 전이 신호 있음 / 구간이 0.5 포함 → 없음 / 그 사이 → 판정 불가.

| 지표 (셜록, `score_pairs`, 층화 bootstrap 2,000회 시드 0) | WM 학습 모델 | `_nli` (영 학습) |
|---|---|---|
| 장면 AUROC [95%] | **0.522 [0.350, 0.686]** [측정] | **0.514 [0.346, 0.680]** [측정] |
| recall@FPR ≤ 1/25 (**표본 안** — 기준값을 셜록에서 고름) | 0.10 = 2/20, FP 0 (기준값 0.9315) [측정] | 0.05 = 1/20, FP 1 (기준값 0.9836) [측정] |
| 양성 / 정상 장면 max p50 | 0.126 / 0.158 [측정] | 0.734 / 0.731 [측정] |
| top-2 · top-3 평균 AUROC (진단) | 0.522 · 0.530 [측정] | — |
| 쌍 PR-AUC (양성 비율 0.0037) | 0.0054 [측정] | 0.0080 [측정] |
| 쌍 NLL (보정 후) | 0.326 (T 2.220), ECE top 0.034 / 모순 0.059 [측정] | 0.912 [측정] |
| 학습 손실 / 검증 쌍 NLL(보정 전), 에폭 1·2·3 | 0.284 / **0.535** · 0.048 / 0.934 · 0.019 / 1.008 → 조기 종료, 최선 에폭 1 [측정] | — |

**판정: 전이 신호 없음**(구간이 0.5 를 포함). 라벨이 깨끗한 학습·검증 작품끼리도 장면 AUROC 가 영 학습과 같은 0.5 근처다.
Q1 쪽 원인(근거 지도 잡음)만으로는 r1·r2 실패를 설명하지 못한다 — 원인은 **데이터의 양·질**(학습 실제 양성 45쌍, 신호 대부분이
주입 템플릿이고 1에폭 뒤 외우기)로 본다 [추정: 지시문의 사전 확정 해석을 따름]. 양성·정상 장면 max 중앙값이 거의 같은 양상도 Q1 과 같다.

**대가**: 이 진단은 셜록 실제 장면을 모델로 채점했다. 4접기 중 **"평가 셜록" 접기는 더 이상 블라인드가 아니다**(셜록 장면 점수·기준값을 봤다).
셜록은 R2 주 비교(WM·Q1·Q3 양성 60)에는 들어가지 않는다. Q3 는 계속 봉인(내보내기·채점 안 함).

### 이 세션의 코드 변경

- `export_pairs.py`: 라벨 v2(`label_scene_v2`·`hypothesis_key`·`premise_head`·`dice`), `--label-version`·`--tau-h`·`--tau-p`, 행 `label_version`·`label_v1`,
  `<out>.meta.json`(라벨 표·sha256). v1 은 `--label-version 1` 로 그대로 재현된다(테스트).
- `train_lib.py`: `scene_scores(..., allow_excluded=True)`(scene_positive 있는 '제외' 행만), `pick_threshold_from_scores`, `scene_auroc`,
  `bootstrap_auroc`(층화), `scene_topk_scores`, `inject_fraction`(contrast_group 단위·포개짐), `Split.val_excluded`, `prepare(inject_frac=)`.
- `train_judge.py`: 장면 기준값·recall 을 검증 '제외' 행까지 넣어 계산(손실·온도·NLL 은 그대로), meta seeds[] 에 `scene_auroc`·`scene_drop_excluded`,
  `--inject-frac`.
- `score_pairs.py`: '제외' 포함 장면 지표 + 쌍 지표 분리, 장면 AUROC bootstrap, `--scene-threshold`, `--allow-eval-work`(경고 로그),
  `--agg-diagnostics`(검증 작품만), `--scores-out`.
- 테스트: `tests/eval/test_data_tools.py`(v2 3단·정규화·정상 장면 불변·v1 재현), `test_train_lib.py`, `test_judge_latency.py`.
  전체 suite 497 passed / 1 skipped / 3 xfailed (`TEST_DB_NAME=glitch_noai_test_labelfix`) [측정].

재현:

```
PYTHONIOENCODING=utf-8 .venv/Scripts/python scripts/eval/export_pairs.py --work wm --real \
    --inject C:/judge_models/data_q3fold/manifest_wm.json --out C:/judge_models/data_q3fold_lv2/pairs_wm.jsonl
CUBLAS_WORKSPACE_CONFIG=:4096:8 PYTHONIOENCODING=utf-8 .venv-train/Scripts/python scripts/eval/score_pairs.py \
    --model-dir C:/judge_models/judge_base_q3_lv2_r1 --pairs C:/judge_models/data_q3fold_lv2/pairs_q1.jsonl --work q1 --agg-diagnostics
```

## 실험 E: 외부 데이터 중간 단계

> 2026-11-07 세션 K(지시문 K-E). D1(학습 WM → 검증 셜록, 0.522)에서 전이 신호가 없었다. 주입을 외부 데이터 중간 단계로 바꾸면
> 전이가 생기는지 본다. 개발 벤치는 D1과 같은 WM→셜록이다(셜록 접기는 이미 블라인드가 아니다). **Q1·Q3는 쓰지 않았다.**
> 꼬리표: [측정] · [기록] · [추정] · [감사: 작업 세션 판정, 사람 아님].

### 데이터 (저장소 밖 `C:\judge_models\ext\`, 사용자 승인 다운로드 2026-11-07, sha256 은 `ext\SHA256SUMS`)

| 자료 | 출처 · 라이선스 | 변환 (`scripts/eval/ext_data.py`) | 행 [측정] |
|---|---|---|---|
| KorNLI fiction (`ext_kornli`) | HF `kakaobrain/kor_nli` multi_nli 학습셋(54.7MB) · CC BY-SA 4.0. 장르는 HF `nyu-mll/multi_nli` 학습셋(214MB)의 genre 열 | 행 순서 1:1 정렬. **라벨 열 전체 일치 검사: 392,702행 중 불일치 0.** genre = fiction 만. entailment→정상 · contradiction→모순 · neutral→무관. 묶음 = promptID. 줄이지 않음 | 77,348 (정상 25,784 · 모순 25,782 · 무관 25,782), 묶음 25,786 |
| FlawedFictions + ContraDoc (`ext_ffcd`) | HF `kahuja/flawed-fictions` 단편·장편(0.87MB·1.47MB) · MIT / GitHub `ddhruvkr/CONTRADOC` `ContraDoc.json`(4.4MB) · Apache-2.0 | 모순 = (충돌 문장, 오류 문장). FF 는 `<li>` 목록의 모든 조합(양성 304편, 817쌍), ContraDoc 은 story 양성 중 ref 있는 107편의 (ref 문장, evidence) 156쌍. ref 에 evidence 문장 자체가 든 22쌍은 뺐다. 무관 = 모순 1개당 2개(같은 이야기의 다른 문장으로 전제 또는 가설 한쪽만 바꿈 — 가설만 보고 맞히는 지름길 차단, 시드 7). 정상 없음. 본문 `</p><p>` 태그 제거 | 2,853 (모순 951 · 무관 1,902), 묶음 397 |

- **번역**: 로컬 llama.cpp b12177 `llama-server`(127.0.0.1:8080, `-np 4 -c 8192`), A.X-4.0-Light Q4_K_M, temperature 0, 문장 단위.
  시험 30문장(무작위, 시드 30) [감사]: 뜻을 지킨 번역 26/30. 의미 오류 2건(#23 주어·목적어 뒤바뀜, #11 earldom → 남작위), 영어 단어가 남은 것 2건, 오타 1건.
  쓸 만하다고 보고 Qwen3-14B 로 바꾸지 않았다. 고유 문장 2,720개를 8분 24초에 번역, 빈 번역 0 [측정]. 번역 뒤 서버 종료, 그 다음 학습(GPU 동시 사용 없음).
- **번역 뒤 감사**(모순 쌍 50, 시드 `ffcd-audit:20261107`) [감사]: 쌍만 보고 모순이 읽히는 것 **27/50 = 0.54**.
  번역 때문에 모순이 깨진 것은 약 2/50(#4·#34)이다. 나머지 21건은 **원본 쌍 자체** 문제다. FF 오류 줄이 조각("the whistle", "maybe even in New York,")이거나
  이야기 맥락이 있어야 이해되는 것들이고, 전제=가설 1건도 있었다(이것은 위에서 22쌍을 거른 계기). 즉 FF+CD 의 모순 라벨은 쌍 단위로 보면 절반쯤만 쓸 만하다.
- **누수 점검** [측정]: 외부 80,201행에서 Weave 4작품 원문(`tests/eval/weave_*_corpus` JSON)의 정규화 20자 조각과 겹치는 행 0.

### 실험 (기초 `nli_klue_roberta_base/_nli`, 시드 2, 셜록 실제 장면 채점은 `score_pairs --max-length 384`, 층화 bootstrap 2,000회 시드 0)

외부 단계: `train_judge.py --train <ext>.jsonl --val-frac 0.1 --epochs 2 --max-length 128`. 조기 종료·체크포인트는 그 외부 데이터의 묶음 단위 10%로 정한다
(셜록은 단계 끝 채점에만 쓴다). Weave 단계: D1과 같은 명령(`--val-work sherlock --train-works wm --eval-work q3`, 기본값 384·6에폭·인내 2). 셜록은 이 단계에서만 에폭 선택에 쓴다.
E2 의 KorNLI 단계는 E1 모델을 그대로 썼다(같은 레시피). "WM 실제 쌍만"은 `pairs_wm.jsonl` 에서 source=real 행만 뽑은 파일(1,440행)이다.

| 조건 | 단계 | 모델 hash | 단계 검증 NLL (에폭 1 → 2 · 3) | 셜록 장면 AUROC [95%] | recall@FPR≤1/25 (표본 안) | 쌍 PR-AUC (양성 0.0037) | 판정 |
|---|---|---|---|---|---|---|---|
| `_nli` | KLUE-NLI 만 | 048a3c0a… | — | 0.514 [0.346, 0.680] [기록: D1] | 1/20 | 0.008 | 없음 |
| E0 = D1 | `_nli` → WM 실제+주입 | b8b28331… | 0.535 → 0.934 · 1.008 | 0.522 [0.350, 0.686] [기록: D1] | 2/20 | 0.005 | 없음 |
| **E1** | `_nli` → KorNLI fiction | 7cfad7ae… | 0.564 → 0.583 (KorNLI 10%) | **0.644 [0.478, 0.798]** [측정] | 2/20, FP 1 [측정] | 0.004 | 없음(구간이 0.5 포함) |
| E2 ② | E1 → FF+CD | 60109409… | 0.566 → 0.605 (FF+CD 10%) | 0.498 [0.330, 0.666] [측정] | 0/20, FP 0 | 0.006 | 없음 |
| **E2** ③ | E2② → WM 실제만(주입 0, 에폭당 195행) | 4d0ef695… | 1.314 → 1.365 · 1.921 | **0.448 [0.282, 0.614]** [측정] | 1/20, FP 1 | 0.008 | 없음 |
| **E3** | E2② → WM 실제+주입 | af78d8d3… | 0.417 → 1.471 · 1.615 | **0.564 [0.390, 0.726]** [측정] | 2/20, FP 1 | **0.062** | 없음 |

(최선 에폭은 모든 단계에서 1. 셜록 장면 양성 20 · 정상 25. 모델 폴더 `C:\judge_models\{E1,E2_ffcd,E2_wmreal,E3}\`, 채점 JSON `C:\judge_models\ext\score\`.)

집계 진단(셜록, top-2·top-3 평균 AUROC) [측정]: E1 0.640·0.634, E2② 0.502·0.508, E2 0.476·0.480, E3 0.568·0.574. max 와 ±0.03 안이다.
양성·정상 장면 max 중앙값: E1 0.467 대 0.307(유일하게 벌어짐), E2② 0.840 대 0.846, E2 0.362 대 0.372, E3 0.373 대 0.305.

**판정: 통과 조건 없음 → 4단계(봉인 접기 재학습·Q1 관문·Q3) 하지 않음. Q3 는 계속 봉인**(내보내기·채점 안 함). 동결 열 Q3 3/20 [기록].

### 읽는 법 [추정]

- 가장 높은 것은 Weave 학습이 **전혀 없는** E1(0.644)이다. 일반 한국어 NLI 를 더 배운 기초가 장면 순위를 조금 낫게 매긴다. 하지만 구간 하한이 0.48 이라
  사전 확정 기준(0.65·하한 > 0.5)을 넘지 못한다. 셜록 20 대 25 장면에서 반폭이 ~0.16 이라 AUROC ≈ 0.68 이상이어야 하한이 0.5 를 넘는다.
- FF+CD 단계(E2②)가 E1 의 이득을 지운다(0.644 → 0.498). 정상 클래스가 없는 2클래스 데이터라 가중치 0인 정상을 잊고, 감사로 본 대로 모순 라벨의 절반은
  쌍만으로는 읽히지 않는다. 그 위에 WM 을 얹으면 실제만(E2, 195행)은 더 나빠지고, 주입을 넣으면(E3) D1 수준(0.56)으로 돌아온다.
- E3 의 쌍 PR-AUC 0.062 는 양성 비율의 17배로, 지금까지의 셜록 채점 중 가장 높다. 쌍 단위 신호는 조금 생겼지만 장면 max 순위로는 이어지지 않는다
  (정상 장면에도 높은 쌍이 하나씩 있다).
- 결론: 외부 중간 단계는 이 규모·품질로는 전이 신호를 만들지 못한다. 그나마 나은 신호는 Weave 미세조정이 아니라 일반 NLI 기초 쪽에서 나왔다.
  다음 판단거리: (가) KorNLI 를 fiction 이 아닌 전체로 늘린 기초 + Weave 없이(또는 아주 짧게), (나) FF+CD 를 쓰려면 정상 쌍(음성판 정렬)과 조각 줄 걸러내기,
  (다) 발표용으로는 동결 LLM 판정 유지.

코드: `scripts/eval/ext_data.py`(kornli · ffcd-en · translate · ffcd-build · leak), `score_pairs.py --max-length`(외부 단계 모델은 meta 128 이라 Weave 쌍을 384 로 채점),
테스트 `tests/eval/test_ext_data.py`. 학습 연결은 기존 `--val-frac`·`--init-model`·`--nli-from`·`--max-length` 로 충분했다(train_judge 변경 없음).
전체 suite 506 passed / 1 skipped / 3 xfailed (`TEST_DB_NAME=glitch_noai_test_labelfix`) [측정].

## 실험 E4: large 기초

> 2026-11-07 세션 K(지시문 K-E4). 발표(11-09) 전 마지막 학습 judge 실험이다. E1 레시피(`_nli` → KorNLI fiction)에서 **모델 크기 하나만** 바꿨다
> (klue/roberta-base → klue/roberta-large). Weave 작품으로는 학습하지 않았다. 개발 벤치는 실험 E와 같은 셜록 실제 장면이다(블라인드 아님).
> llama-server 는 띄우지 않았다. 판독 규칙은 E와 같고 미리 정해 두었다(장면 AUROC ≥ 0.65 **그리고** 하한 > 0.5 → 통과, 구간이 0.5를 포함하면 없음, 그 사이는 판정 불가).
> 꼬리표: [측정] · [기록] · [추정].

### 학습 (RTX 3060 12GB, fp16 · eager · 배치 8, gradient checkpointing 없이 VRAM 부족 없음)

| 단계 | 명령 요지 | 시간 [측정] | 검증 NLL (에폭 1 → 2) [측정] | 모델 hash |
|---|---|---|---|---|
| large `_nli` | `train_judge.py --nli-only --nli-epochs 2 --base-model klue/roberta-large --batch-size 8 --seeds 1` (KLUE-NLI 24,998행, 128) | 600s + 596s | — (검증 없음, 학습 loss 0.523 → 0.245) | d0f52b88… |
| **E4** | `--train ext/pairs_ext_kornli.jsonl --val-frac 0.1 --nli-from <large _nli> --base-model klue/roberta-large --batch-size 8 --seeds 2 --epochs 2 --max-length 128` | 1695s + 1697s | 0.559 → 0.657 (KorNLI 10%, 최선 에폭 1, 온도 후 0.547, T 1.231) | 82a96e8f… |

참고 [기록: 실험 E]: base E1 은 같은 단계에서 0.564 → 0.583, T 1.152. large 쪽이 KorNLI 검증에서 조금 낫고 2에폭째 과적합이 더 크다.
KorNLI 검증 쌍 PR-AUC(모순) 0.898 [측정].

### 셜록 실제 장면 채점 (`score_pairs --max-length 384 --agg-diagnostics`, 층화 bootstrap 2,000회 시드 0, 양성 20 · 정상 25 장면, 쌍 5,098)

| 조건 | 장면 AUROC [95%] | recall@FPR≤1/25 | 쌍 PR-AUC (양성 0.0037) | 쌍 NLL | 장면 max 중앙값 양성 · 정상 | top-2 · top-3 평균 AUROC | 판정 |
|---|---|---|---|---|---|---|---|
| base E1 [기록: 실험 E] | 0.644 [0.478, 0.798] | 2/20, FP 1 | 0.004 | — | 0.467 · 0.307 | 0.640 · 0.634 | 없음 |
| large `_nli` [측정] | **0.410 [0.254, 0.578]** | 0/20, FP 0 (기준값 0.9985) | 0.007 | 2.026 | 0.845 · 0.893 | 0.422 · 0.432 | 없음 |
| **E4** [측정] | **0.650 [0.484, 0.802]** | 2/20, FP 1 (기준값 0.9112) | 0.006 | 1.329 | 0.464 · 0.303 | 0.658 · 0.656 | **없음**(점 추정은 0.65 에 닿지만 구간이 0.5 포함) |

(AUROC 는 20×25 = 500 장면 쌍의 비율이라 0.650 은 정확히 325/500 이다. 채점 JSON `C:\judge_models\ext\score\{large_nli,E4}_sherlock.json`.)

**셜록 벤치 사용 횟수: 이번까지 8개 조건** — base `_nli` 0.514, D1 0.522, E1 0.644, E2② 0.498, E2 0.448, E3 0.564, large `_nli` 0.410, E4 0.650.
같은 45장면으로 8개 조건 중 하나를 고르는 것이므로, 가장 높은 값(E4 0.650)은 그 자체로 위로 치우친 추정이다 [추정]. 셜록 수치는 개발 벤치 값이지 일반화 수치가 아니다.

**판정: 없음 → Q1 관문·Q3 하지 않음. Q3 는 계속 봉인**(내보내기·채점 안 함). `judge_eval.py --mode learned` 도 돌리지 않았다(Q3 를 여는 경로라서). 동결 열 Q3 3/20 [기록].

### 읽는 법 [추정]

- 크기를 키워도 E1 과 거의 같다(0.644 → 0.650, 500 쌍 중 3쌍 차이). recall·오탐(2/20, FP 1)과 장면 max 분포(양성 0.46 대 정상 0.30)도 같다.
  KorNLI 단계가 만드는 신호는 모델 크기가 아니라 데이터 쪽 한계에 묶여 있다고 본다.
- large `_nli`(KLUE-NLI 만, 온도 1.0)는 0.41 로 base `_nli`(0.514)보다 낮다. 장면 max 가 양성·정상 모두 0.85~0.89 로 포화해, 거의 모든 장면에 "모순" 확신이 높은 쌍이 있다.
  KLUE-NLI 만 배운 large 는 Weave 쌍 분포에서 모순 쪽으로 과신한다. KorNLI fiction 단계가 이것을 바로잡는다(쌍 NLL 2.03 → 1.33).
- 셜록 20 대 25 장면에서 구간 반폭이 ~0.16 이라, 이 벤치로 통과하려면 AUROC ≈ 0.68 이상이 필요하다(실험 E 와 같은 계산). E1·E4 는 그 아래다.
- 발표 결론: 학습형 judge 는 이 데이터 규모에서 동결 LLM 판정을 대신할 근거가 없다. 발표용 판정은 동결 열을 유지한다.

코드 변경 없음(기존 `train_judge.py --base-model --batch-size`, `score_pairs.py --max-length` 그대로). 모델 폴더 `C:\judge_models\nli_klue_roberta_large\_nli`, `C:\judge_models\E4\`.

## 실험 L: Claude 데이터

> 2026-11-07 밤 세션 K(지시문 K-L). 사전 확정은 `docs/learned-judge-training-design.md` §0-7(c)(main fb23bae·2501c66)이다. **조건 하나만** 돌렸고,
> 결과를 본 뒤 조건을 바꾸거나 더하지 않았다. 유료 API 호출 0, llama-server 안 띄움.
> 꼬리표: [측정] · [기록] · [추정] · [감사: 작업 세션 판정, 사람 아님].
> **사후 변경 꼬리표:** 주 평가를 Q1(사전 등록 검증 작품)에서 셜록으로 바꾼 것은 결과를 본 뒤의 변경이다. **셜록 벤치 사용은 이번이 9번째다.**
> 이번 조건은 사전 확정한 단일 조건이다.

### 데이터 (저장소 밖 `C:\judge_models\L\`, 생성 = Claude Code 작업 세션(L-gen), 감사 = 생성하지 않은 별도 세션(L-audit))

| 묶음 | 내용 | 행 [기록: L-gen] | 감사 50쌍 [감사] | 학습 / 떼어 둔 10% [측정] |
|---|---|---|---|---|
| L1 `l1_wm.jsonl` | WM 반사실. 확정 회차·그 시점 원장의 실제 전제 하나와 충돌하는 모순판, 같은 자리의 일관판(어려운 음성). 전제 문장은 스크립트가 premise_id 로 원장에서 복사 | 모순 259 · 정상 259, 장면 24 (거른 38) | **49/50 (0.98)**, 경계까지 틀림으로 치면 44/50 | 452 / 66 (장면 3) |
| L2 `l2_ffcd.jsonl` | 외부 정리. FF+CD 모순을 원문 맥락에서 혼자 읽히는 한국어 Weave 형식 쌍으로 다시 쓰고 일관판을 짝지음 | 모순 172 · 정상 172, 이야기 172 (묶음 10/17에서 멈춤, 거른 128) | **47/50 (0.94)**, 경계 포함 43/50 | 308 / 36 (이야기 18) |
| WM 실제 쌍 v2 | `data_q3fold_lv2/pairs_wm.jsonl` 의 source=real 1,440행(E2 의 파일과 같음). 주입 0 | 모순 45 · 정상 20 · 무관 625 · 제외 750 | — (라벨 v2 감사 WM 0.97 [기록]) | 637 / 53 (장면 3), 제외는 손실에서 뺌 |

- 누수 [감사]: 고유 인물명·지명 0건. 셜록·Q3·Q1 원문과 원장은 생성 세션에 주지 않았다. Q1 은 이 실험 어디에도 쓰지 않았다.
- 감사에서 정한 거르기 규칙은 없다. 그래서 데이터를 그대로 썼다. 파일 sha256 앞 16자: l1 9f04514668e3c05a · l2 e8f50637fe5331bd · wm_real_v2 17bf7414863679fc.
- 유형 쏠림 [기록: L-gen]: L1 은 앎·규칙 합이 40%, L2 는 관계·행동·소유·상태가 55%다. L1 전제가 L2 보다 길다(출처 지름길 가능 — 가설만 기준선은 이번 단일 조건에 없다).

### 학습 (한 단계, `C:\judge_models\L\model\`, hash 80416bcc…)

`train_judge.py --train l1_wm.jsonl l2_ffcd.jsonl wm_real_v2.jsonl --val-groups val_groups.txt --scene-unit pair --init-model C:\judge_models\E1 --max-length 384 --seeds 2`
(나머지 기본값: 6에폭 상한·인내 2·배치 16·lr 2e-5·가중 손실·무관 ≤ (모순+정상)×2).

- **떼어 둔 10%** = `group_id`(장면·이야기) 단위. `claude_pairs.assign_val_groups(frac 0.1, seed 20261107)` 를 L1·L2·WM 실제에 각각 적용했다
  (L1·L2 는 L-gen 의 `validate_report.json` 목록과 같다). 묶음 24개(L1 3 · L2 18 · WM 3: scene_18 정상 · 23·26 모순). 검증 155쌍 = 모순 56 · 정상 53 · 무관 46.
  기존 `--val-frac` 은 `contrast_group` 우선으로 묶어 한 장면의 대조 짝이 갈라진다. `--val-jsonl` 은 같은 작품을 양쪽에 두지 못한다. 그래서 `--val-groups` 를 더했다.
- 에폭·온도·장면 기준값은 이 10% 로만 골랐다. 셜록은 고르는 데 쓰지 않았다. 장면 기준값은 **"쌍 = 장면 하나" 근사**로 골랐다
  (`--scene-unit pair`: 쌍 라벨이 모순이면 양성, 제외 행은 쓰지 않음, 오탐 상한 0.05).
- 결과 [측정]: 검증 NLL 1.854 → **1.439** → 1.623 → 1.793(최선 에폭 2, 4에폭에서 조기 종료). 온도 **T 3.41** 이 붙은 뒤 0.934 다(과신이 크다).
  검증 쌍 AUROC 0.828, 쌍 PR-AUC 0.792. 기준값 0.583 에서 검증 recall 31/56, 오탐 4/99. 학습 시간 에폭당 17초.
- 참고: L1 떼어 둔 장면(WM 회차 조각)과 WM 실제 학습 장면은 같은 작품·같은 원장이라 전제 문장이 겹친다. 검증 값은 고르는 데만 쓰므로 셜록 판정에는 영향이 없다 [추정].

### 셜록 실제 장면 1회 (`score_pairs --max-length 384 --agg-diagnostics`, '제외' 포함 max, 층화 bootstrap 2,000회 시드 0, 양성 20 · 정상 25)

| 조건 | 장면 AUROC [95%] | recall · 오탐 @떼어 둔 10% 기준값 0.583 (표본 밖) | 참고: 셜록 안에서 고른 FPR≤1/25 기준값 | 쌍 PR-AUC (양성 0.0037) | 장면 max 중앙값 양성 · 정상 | 판정 |
|---|---|---|---|---|---|---|
| E1 (시작 모델) [기록: 실험 E] | 0.644 [0.478, 0.798] | — | 2/20, FP 1 | 0.004 | 0.467 · 0.307 | 없음 |
| **L** [측정] | **0.570 [0.400, 0.728]** | **11/20 · 10/25** | 0/20, FP 0 (기준값 0.689) | **0.033** | 0.585 · 0.568 | **없음**(구간이 0.5 포함) |

(top-2·top-3 평균 AUROC 0.578·0.584, '제외'를 뺀 옛 방식 0.522 [측정]. 채점 JSON `C:\judge_models\ext\score\L_sherlock.json`.)

**판정: 없음 → Q3 는 열지 않았다**(내보내기·채점 안 함, E1 의 Q3 나란히 채점도 안 함). 동결 열 Q3 3/20 [기록].

### 읽는 법 [추정]

- 쌍 단위 신호는 있다(쌍 PR-AUC 0.033 = 양성 비율의 9배, 셜록 채점 중 E3 0.062 다음이고 E1 0.004 보다 크다). 하지만 장면 max 순위로는 이어지지 않는다.
  양성·정상 장면의 max 중앙값이 0.585 대 0.568 로 붙어 있다. 정상 장면에도 모순 확신이 높은 쌍이 하나씩 있다(E3 와 같은 양상).
- 떼어 둔 10% 의 쌍 기준값(0.583)을 장면 max 에 그대로 쓰면 오탐이 10/25 다. 장면 하나에 쌍이 ~110개라 쌍 단위 오탐 4% 가 장면 단위로 쌓인다.
  "쌍 = 장면 하나" 근사의 예상된 한계다. 장면 기준값은 실제 장면 단위의 검증이 있어야 정할 수 있다.
- 시작 모델 E1(0.644)보다 낮아졌다. 떼어 둔 생성 쌍에서는 잘 맞췄다(쌍 AUROC 0.83). 그러니 생성 쌍의 형식은 배웠지만 셜록 실제 장면의 쌍 분포로 옮겨 가지 않았다.
  T 3.41 은 같은 형식 안에서도 과신이 크다는 뜻이다.
- 발표 결론은 바뀌지 않는다. 학습형 judge 는 이 규모에서 동결 LLM 판정을 대신할 근거가 없다.

코드: `train_judge.py --val-groups`(group_id 목록 분할)·`--scene-unit pair`, `train_lib.split_by_group_ids`·`pair_scene_scores`, meta `threshold_fit_unit`. 테스트 `tests/eval/test_train_lib.py` 4개.
L-gen 브랜치 `claude/judge-l-gen-a4c84c`(4a46bb1, `claude_pairs.py`)는 이 브랜치에 **`git merge --ff-only`** 로 받았다(5171815 → 4a46bb1).
전체 suite 538 passed / 1 skipped / 3 xfailed (`TEST_DB_NAME=glitch_noai_test_labelfix`) [측정].

## 진단 S: 셜록 벤치 상한

> 2026-11-08 세션 K(지시문 K-S). 진단 전용이다. GPU 를 쓰지 않았고, 새로 학습하지 않았다. Q3 는 봉인 상태다.
> 모든 조건이 셜록 0.65 근처에서 멈췄다. 그 원인이 **벤치 상한**(쌍 분류기가 완벽해도 못 넘는 값)인지 **집계**(정상 장면 max 가 높음)인지 가린다.
> 꼬리표: [측정] · [기록] · [추정] · [감사: 작업 세션 판정, 사람 아님].

### 1. 양성 장면 20개 분류 [측정]

방법 (`C:\judge_models\ext\score\diag_s_scenes.json`, 스크립트는 세션 scratchpad):
- 근거 지도는 `evidence_event_map.json` 의 matched 후보다. 내보내기와 같다.
- 할당량 쌍은 `build_pairs` 기본(QUOTAS, 가설당 24)으로 만들었다. 장면별 쌍 수가 `pairs_sherlock.jsonl` 과 20/20 일치하고, 모순 라벨 수도 20/20 일치한다.
- 전제 풀은 같은 `build_pairs` 에 할당량 없음·상한 없음을 준 것이다.
- 키는 라벨 v2 그대로다(τ_h 0.5 · τ_p 0.15). 가설 키 = seeded_text 를 담은 가설. 전제 키 = 근거 사건 전제 ∧ (checker ∨ 그 가설의 유일 근거 ∨ 머리 Dice ≥ τ_p).
- "두 키 쌍"은 같은 쌍에 가설 키와 전제 키가 다 있는 쌍이다. 가설 키 ∧ 근거 사건(전제 키의 유일·Dice 조건 없이)으로 넓혀도 할당량 쌍에서 짚을 수 있는 장면 집합은 같다.

| 장면 | 유형 | 근거 사건 | 가설 키 가설 | 근거 전제 쌍 (할당량 / 풀) | 두 키 쌍 (할당량) | 분류 | 비고 |
|---|---|---|---|---|---|---|---|
| 03 | 지식 | 1 | 3 | 0 / 0 | 0 | 전제 누락(원장) | 근거 사건(홈즈가 왓슨에게 한 말)은 원장에 있으나 가설 인물(메리)과 엮이지 않아 풀에 안 들어온다. 논리는 "알 경로가 없음"(경로 부재) |
| 06 | 상태 | 2 | 2 | 3 / 4 | 1 | 짚을 수 있음 | past |
| 12 | 설정 | 0 | 2 | 0 / 0 | 0 | 다중 전제/설정형 | 지형 제약(못은 농장·영지로만 접근). 근거 인용 unmatched |
| 13 | 설정 | 1 | 3 | 9 / 25 | 1 | 짚을 수 있음 | knowledge·past |
| 16 | 상태 | 1 | 2 | 0 / 4 | 0 | **전제 누락(할당량)** | 근거(화2 결혼, past)가 풀에는 4쌍 있으나 할당량에서 밀림 |
| 19 | 설정 | 1 | 1 | 3 / 5 | 1 | 짚을 수 있음 | 감사에서 키 쌍이 **아님** [기록: 감사] |
| 20 | 지식 | 0 | 2 | 0 / 0 | 0 | **애매** | 근거 인용 ambiguous(후보 2). 후보를 근거로 치면 두 키 쌍 6 |
| 22 | 상태 | 1 | 2 | 2 / 11 | 2 | 짚을 수 있음 | 감사에서 키 쌍 2개 모두 **아님** [기록: 감사] |
| 25 | 상태 | 1 | 2 | 5 / 14 | 4 | 짚을 수 있음 | |
| 26 | 지식 | 0 | 2 | 0 / 0 | 0 | 전제 누락(원장) | 근거 대사("모르겠는데") unmatched — 원장에 사건이 없다 |
| 27 | 설정 | 0 | 2 | 0 / 0 | 0 | 다중 전제/설정형 | 보수 원칙(규칙). 근거 인용 unmatched |
| 28 | 상태 | 1 | 1 | 2 / 3 | 2 | 짚을 수 있음 | ledger·past |
| 31 | 상태 | 0 | 1 | 0 / 0 | 0 | **애매** | 근거 ambiguous(후보 2), 후보를 치면 두 키 쌍 6. 논리는 날짜 계산(사망 2년 전) — 다중 전제형이기도 하다 |
| 34 | 상태 | 1 | 1 | 6 / 10 | 2 | 짚을 수 있음 | |
| 36 | 설정 | 1 | 1 | 1 / 10 | 1 | 짚을 수 있음 | |
| 38 | 지식 | 0 | 1 | 0 / 0 | 0 | 전제 누락(원장) | 증인 진술 unmatched |
| 40 | 지식 | 0 | 2 | 0 / 0 | 0 | **애매** | 근거 ambiguous, 후보를 치면 두 키 쌍 1. 논리는 경로 부재(조건부 문서 비공개) |
| 41 | 상태 | 0 | 2 | 0 / 0 | 0 | **애매** | 근거 ambiguous, 후보를 치면 두 키 쌍 3 |
| 42 | 지식 | 1 | 1 | 15 / 24 | 1 | 짚을 수 있음 | |
| 43 | 설정 | 2 | 2 | 11 / 46 | 4 | 짚을 수 있음 | |

**분류 합계:** 짚을 수 있음 **10** · 가설 누락 **0** · 전제 누락(할당량) **1** · 전제 누락(원장) **3** · 다중 전제/설정형 **2** · 애매(근거 지도 ambiguous) **4**.

**R = 10/20.** 감사까지 맞은 것은 8이다(19·22 는 키 쌍이 틀림 [기록: 감사]). 가설 누락은 0 이다. 20장면 모두 추출 가설 중 seeded_text 를 잡은 것이 있다.
놓친 원인은 거의 전부 **전제 쪽**(근거 지도·원장·할당량)이다. 할당량을 풀어서 되찾을 수 있는 장면은 1개(16)뿐이다.

### 2. 오라클 상한 [측정: 계산, `train_lib.bootstrap_auroc` 층화 2,000회 시드 0]

가정: 짚을 수 있는 양성 장면은 모든 정상 장면보다 높다. 나머지 양성 장면은 정상과 동률(0.5)이다. AUROC = (R·25 + (20−R)·12.5)/500.

| R | 근거 | 오라클 AUROC [95%] | 관문(≥ 0.65 그리고 하한 > 0.5) |
|---|---|---|---|
| 8 | 감사로 맞은 키 장면 | 0.700 [0.600, 0.800] | 통과 |
| **10** | 라벨 v2 키 | **0.750 [0.650, 0.850]** | **통과** |
| 14 | 낙관: ambiguous 후보까지 근거로 친 값(이 쌍들은 벤치에서 '제외' 라벨) | 0.850 [0.750, 0.950] | 통과 |

('제외' 쌍이 있는 장면을 모두 짚을 수 있다고 치면 20/20 = 1.0 이 된다. 모든 양성 장면에 가설 키 쌍('제외')이 있기 때문이다. 이 값은 상한으로서 뜻이 없다.)

**벤치 상한은 관문보다 높다.** 완벽한 쌍 분류기라면 R=10 으로 0.75 [0.65, 0.85] 이고 관문을 넘는다. 감사 기준 R=8 이라도 0.70 [0.60, 0.80] 이다.
지금까지의 최고 0.650(E4)·0.644(E1)는 이 상한보다 0.10 낮다. 0.65 에서 멈춘 것이 벤치 상한 때문만은 아니다 [추정].
다만 관문이 사실상 요구하는 0.68 과 상한 0.75 사이의 여유는 짚을 수 있는 장면 3개분 정도뿐이다(장면 하나 = 0.025) [추정].

### 3. 집계 진단 (E1 · E4 · L, 재현 채점 쌍 점수)

**재현:** 기존 채점 JSON 에는 쌍별 점수가 없었다. 그래서 판단 세션 승인으로 같은 모델·같은 조건(GPU cuda fp16 · eager, `score_pairs --max-length 384`)을
`--scores-out` 만 붙여 다시 돌렸다. **같은 모델·조건의 재계산이고, 새 조건이 아니다. 셜록 사용 횟수는 불변이다(진단용 재현).**
대조 [측정]: 셋 다 probs sha256·장면 AUROC 구간·recall·쌍 PR-AUC 가 기존 JSON 과 같다. → **재현 일치.** 판정 수치는 기존 값 그대로다.
파일: `C:\judge_models\ext\score\{E1,E4,L}_rep_scores.jsonl`, 집계는 `diag_s_agg.json`.

| 항목 [측정] | E1 | E4 | L |
|---|---|---|---|
| 정상 25장면 max 를 만든 쌍의 kind | 과거 14 · 규칙 4 · 앎 4 · 원장 2 · checker 1 | 과거 11 · 앎 6 · 원장 4 · 규칙 4 | 원장 11 · 과거 6 · checker 3 · 규칙 2 · 앎 2 · 장면 1 |
| 그 쌍의 라벨 | 무관 24 · 정상 1 | 무관 22 · 정상 3 | 무관 13 · 정상 12 |
| 정상 장면 max 중앙값 (장면당 쌍 중앙값 120) | 0.307 | 0.303 | 0.568 |
| 짚을 수 있는 10장면에서 키 쌍(최선)의 장면 안 순위 | 6, 7, 7, 22, 33, 34, 57, 66, 74, 76 | 1, 3, 4, 12, 23, 64, 78, 82, 97, 127 | 1, 1, 2, 3, 6, 10, 37, 37, 98, 180 |
| 키 쌍이 장면 max (1위) | 0/10 | 1/10 | 2/10 |
| 키 쌍 p 가 정상 장면 max 보다 높은 비율 (10×25) | 59/250 = 0.24 | 54/250 = 0.22 | 94/250 = 0.38 |
| 같은 10장면의 장면 max 가 정상 장면 max 보다 높은 비율 | 164/250 = 0.66 | 161/250 = 0.64 | 167/250 = 0.67 |
| Spearman(장면 쌍 수, 장면 max) 정상 · 양성 | 0.37 · 0.04 | 0.50 · 0.33 | 0.48 · 0.32 |

세 모델을 합친 정상 장면 max 쌍 kind 상위: **과거 31 · 원장 17**(그다음 앎 12 · 규칙 10 · checker 4 · 장면 1 / 75).

### 읽는 법 [추정]

- **벤치 상한이 원인은 아니다.** 오라클은 0.75 [0.65, 0.85] 로 관문을 넘는다. 모델들은 상한까지 가는 데 실패한다.
- **실패는 쌍 판별이다.** 짚을 수 있는 장면에서도 키 쌍이 장면 1위인 경우가 10개 중 0~2개다. 키 쌍 점수가 정상 장면 max 를 넘는 비율도 0.22~0.38 이다.
  그런데 같은 장면의 장면 max 는 0.64~0.67 의 비율로 정상 장면을 넘는다. 즉 지금 셜록 AUROC(0.57~0.65)를 만드는 것은 키 쌍이 아니라 **키가 아닌 쌍들의 점수**다.
  양성 장면이 정상 장면보다 조금 더 "모순스러운" 문장을 많이 품고 있다는 뜻일 수 있다. 모순을 짚어서 나온 신호라고 보기 어렵다.
- **집계도 불리하게 작동한다.** 정상 장면 max 는 장면 쌍 수와 양의 상관(0.37~0.50)이다. max 를 만든 쌍은 대부분 무관 라벨의 과거 사건(E1·E4)이나 원장(L) 전제다.
  쌍이 ~120개인 장면에서 쌍 단위 오탐이 조금만 있어도 장면 max 가 오른다(L 의 정상 max 중앙값 0.57).
- 다음 판단거리(이 진단의 범위 밖):
  (가) 장면 점수를 max 에서 바꾸는 것이다. 예: 가설별 근거 전제 묶음 점수, 또는 과거 kind 의 가중을 낮춤. 단, 셜록에서 고르면 벤치를 또 쓰게 된다.
  (나) 근거 지도 보강이다. ambiguous 4장면을 확정하면 R 이 최대 14 가 된다.
  (다) 할당량 조정으로 되찾을 장면은 1개뿐이라 효과가 작다.

## 실험 M: 어려운 음성 + 가설 단위 집계

> 2026-11-08 세션 K(지시문 K-M). 사전 확정은 `docs/learned-judge-training-design.md` §0-8(main 276fa1b)이고, 사용자 지시는 "M-2까지"다.
> 조건은 하나만 돌렸고, 결과를 본 뒤 바꾸지 않았다. 유료 API 0, llama-server 안 띄움. 셜록·Q3 는 0~3단계에 쓰지 않았다.
> 꼬리표: [측정] · [기록] · [추정].
> **꼬리표: 셜록 사용 10번째.** 후보 (iii)은 진단 S(셜록 쌍 분포)를 본 뒤 만든 것이다. 그래서 셜록 값은 **개발 벤치 값**으로만 적는다.

### 0. Q1 장면 분할 (`random.Random("M:q1split:20261108").shuffle`, 정렬한 정상 25장면, 앞 절반(내림) = Q1-mine) [측정]

- **Q1-mine (12):** scene_04, 05, 11, 25, 27, 30, 31, 34, 36, 40, 41, 42
- **Q1-sel 정상 (13):** scene_07, 10, 12, 16, 18, 19, 20, 23, 26, 33, 39, 43, 44
- **Q1-sel 양성 (20):** Q1 양성 전부. Q1-sel 은 33장면 4,807쌍이고, 학습에는 넣지 않았다.
- Q1 은 장면 라벨(심은 모순 유무)만 썼다. 목록 파일은 `C:\judge_models\M\q1_split.json` 이다.

### 1. M-1 어려운 음성 채굴 [측정]

- 채굴원은 WM 정상 10장면 + Q1-mine 12장면의 실제 쌍 2,866개다. 정상 장면이라 v2 '모순' 0 · '제외' 0 이다(있으면 안 되는 것 — 0 확인).
- 채점은 L 모델(`score_pairs --max-length 384`, GPU fp16 · eager, 온도 3.41 적용)로 했다.
- 장면마다 p(모순) 상위 20쌍을 골랐다. 상한은 학습 모순 양성 476(L1 259 + L2 172 + WM 실제 45)의 2배인 952 이고, 고른 쌍은 22장면 × 20 = **440** 이라 상한 안이다. 그래서 줄이지 않았다.

| | 장면 | 쌍 | 라벨 | kind (고른 440 / 채굴원 2,866) | 고른 쌍 p 범위 (최소 · 중앙 · 최대) |
|---|---|---|---|---|---|
| WM 정상 | 10 | 200 | 무관 183 · 정상 17 | — | — |
| Q1-mine | 12 | 240 | 무관 190 · 정상 50 | — | — |
| 합 | 22 | 440 | 무관 373 · 정상 67 | 앎 144 · 과거 130 · 원장 117 · 규칙 36 · checker 12 · 장면 1 / 앎 1,045 · 과거 755 · 규칙 480 · 원장 415 · checker 94 · 장면 77 | 0.119 · 0.270 · 0.688 |

- 진단 S 의 셜록 정상 max 쌍(과거·원장 쏠림)과 견주면, L 이 채굴원에서 높게 매긴 쌍도 원장(채굴원 14% → 고른 것 27%)과 과거(26% → 30%) 쪽으로 쏠린다.
- WM 정상 장면의 어려운 음성 200쌍은 L 학습에 이미 있는 WM 실제 쌍과 같은 행이다. 그래서 이 행들은 학습에서 사실상 2배 가중이 된다(지시대로 L 데이터에 더했다) [기록].

### 2. 학습 (`C:\judge_models\M\model\`, hash 44359215…)

`train_judge.py --train l1_wm l2_ffcd wm_real_v2 hardneg --val-groups M/val_groups.txt --scene-unit pair --init-model E1 --max-length 384 --seeds 2`
(나머지 기본값 — L 과 같은 명령에 `hardneg.jsonl` 만 더했다.)

- 떼어 둔 10% 는 L 의 24묶음 그대로다. 여기에 Q1-mine 어려운 음성 묶음 2개(scene_11 · 31, `assign_val_groups` 0.1 · 20261107)를 더했다.
  WM 어려운 음성은 장면 group_id 로 L 목록을 따른다(scene_18 이 검증). 어려운 음성 중 60쌍이 검증에 간다.
- 학습 1,777쌍(모순 420 · 정상 456 · 무관 901, 무관 감축 없음), 검증 215쌍(모순 56 · 정상 62 · 무관 97) [측정].
- 검증 NLL 은 **1.157** → 1.631 → 1.776 이다(최선 에폭 1, 3에폭에서 조기 종료) [측정]. 온도 T 2.558 을 붙이면 0.884 다.
  참고 [기록: 실험 L]: L 은 1.439(에폭 2), T 3.41.

### 3. M-2 집계 선택 — Q1-sel (33장면: 양성 20 · 정상 13, 층화 bootstrap 2,000 시드 0) [측정]

같은 쌍 점수(온도 적용 p)로 계산했다. 가설 id 는 쌍 행의 (event_id, entity, field, 가설 텍스트) 묶음이다. 행에 별도 가설 id 필드는 없다(`train_lib.hypothesis_id` = export_pairs `_hyp_id`).
기준값은 "정상 장면 오탐 ≤ 1/13 에서 recall 최대, 동률은 높은 기준값"으로 정했다. 선택은 M 모델로만 했다.

| 모델 | (i) max | (ii) 가설 대비 | (iii) 가설 대비 · 과거 ×0.5 |
|---|---|---|---|
| **M** | 0.512 [0.308, 0.715] · thr 0.724 → 4/20, FP 1/13 | **0.542 [0.327, 0.758]** · thr 0.633 → 1/20, FP 0/13 | 0.527 [0.319, 0.738] · thr 0.644 → 1/20, FP 0/13 |
| E1 (비교) | 0.492 [0.285, 0.692] · 5/20, FP 1 | 0.546 [0.342, 0.750] · 3/20, FP 0 | 0.512 [0.315, 0.708] · 4/20, FP 1 |
| L (비교) | 0.446 [0.254, 0.646] · 4/20, FP 1 | 0.335 [0.158, 0.527] · 3/20, FP 1 | 0.354 [0.181, 0.554] · 2/20, FP 0 |

**고른 집계: (ii) 가설 대비**(M 0.542). 기준값 0.6334 (Q1-sel recall 1/20 · 오탐 0/13). 세 후보 모두 Q1-sel 구간이 0.5 를 넓게 포함한다.
채점 JSON 은 `C:\judge_models\M\sel\`.

### 4. 셜록 1회 (M · (ii) 가설 대비 · T 2.558 · 기준값 0.6334 고정, 384, 층화 bootstrap 2,000 시드 0) [측정]

| 조건 | 셜록 장면 AUROC [95%] | recall · 오탐 @Q1-sel 기준값 | 참고: 셜록 안에서 고른 FPR≤1/25 | 쌍 PR-AUC (양성 0.0037) | 판정 |
|---|---|---|---|---|---|
| **M · 가설 대비** | **0.570 [0.398, 0.734]** | **0/20 · 0/25** | 1/20, FP 1 | 0.016 | **없음**(구간이 0.5 포함) |

**판정: 없음 → Q3 는 열지 않았다**(E1·L 의 Q3 나란히 채점도 안 함). 동결 열 Q3 3/20 [기록].

진단(선택·판정에 쓰지 않음, 같은 1회 채점의 쌍 점수에서 계산) [측정]:
- 같은 M 쌍 점수의 셜록 장면 AUROC 는 (i) max 0.546 · (ii) 0.570 · (iii) 0.628 이다.
  (iii) 이 가장 높지만 Q1-sel 에서는 (ii) 가 골라졌다. 셜록을 본 뒤 (iii) 으로 바꾸면 사후 선택이 되므로 바꾸지 않는다. (iii) 0.628 도 관문 아래다.
- 정상 장면 max 쌍의 kind 는 원장 12 · 과거 6 · 규칙 2 · 앎 2 · checker 2 · 장면 1 이다. 짚을 수 있는 10장면의 키 쌍 순위는 2, 6, 6, 6, 10, 31, 38, 47, 125, 194 다
  (진단 S 의 L: 1, 1, 2, 3, 6, 10, 37, 37, 98, 180).

### 읽는 법 [추정]

- 어려운 음성을 더해도 Q1-sel 장면 순위는 나아지지 않았다(M max 0.512 대 E1 max 0.492, 구간이 겹친다). 셜록도 L 과 같은 0.57 이다.
  키 쌍 순위가 1위로 올라오지 않았고, 정상 장면 max 를 만드는 쌍은 여전히 원장·과거 전제다.
- 가설 대비 집계는 Q1-sel 에서 M·E1 모두 max 보다 0.03~0.05 높다. 그러나 구간 폭(±0.21)에 비하면 작다. 장면 33개로는 집계 간 차이를 가를 수 없다.
- Q1-sel 의 선택 결과(recall 1/20 @ 오탐 0/13)부터 이미 기준값이 매우 높다. 셜록에서 recall 0/20 이 된 것은 그 결과다.
- 이번 M 으로 학습형 judge 트랙의 사전 확정 실험은 모두 "없음"이다. 발표 결론은 동결 LLM 판정 유지로 바뀌지 않는다.

코드:
- `train_lib.AGGREGATES` · `hypothesis_id` · `scene_scores_agg`
- `score_pairs.py --aggregate {max,hyp_contrast,hyp_contrast_past05}`(장면 지표·기준값 모두)
- 테스트 `tests/eval/test_train_lib.py` 2개, 전체 suite 540 passed / 1 skipped / 3 xfailed (`TEST_DB_NAME=glitch_noai_test_labelfix`) [측정]
- 데이터·모델: `C:\judge_models\M\`(q1_split.json · mine_src.jsonl · mine_scores_{wm,q1}.jsonl · hardneg.jsonl · val_groups.txt · pairs_q1sel.jsonl · model · sel · M_sherlock.json)

## 진단 T: 시간 인지 전제

> 2026-11-08 세션 K(지시문 K-T). 진단 전용이고 새 학습은 하지 않았다. **셜록·Q3 는 쓰지 않았다**(채점도, 기존 셜록 점수 파일 분석도 안 함). 대상은 WM 과 Q1 뿐이다.
> 묻는 것: 진단 S 에서 정상 장면 max 를 만든 쌍이 과거·원장 전제였다. 그 전제가 장면 시점에 **이미 바뀐 옛 상태**이고, judge 가 '바뀜'을 '모순'으로 읽는가?
> 꼬리표: [측정] · [기록] · [추정].

### 분류 규칙 (`C:\judge_models\T\diag_t.json`, 스크립트 세션 scratchpad `diag_t.py`)

장면 시점은 judge_eval 재생과 같다. 실제 장면 사건에는 episode 가 없다. 원장은 5화까지 확정한 것이므로 장면 시점 k = 6 이고, 원장 `state` 는 그 시점의 최신 값이다.

| 분류 | 규칙 |
|---|---|
| 현재(원장) | `premise_kind = ledger`. `pairs._ledger_premises` 는 원장 `state` 의 지금 값만 낸다(possesses [상실됨]·knows 누적도 지금 상태다). |
| **옛 상태** | `past` 이고, 그 사건의 `state_changes` 중 하나 이상에서 같은 (엔티티, 필드)의 원장 값 `since` 가 **더 뒤 사건**(global_idx 큼)이다. knows 는 누적이라 대체로 보지 않는다. |
| 현재(과거 사건) | `past` 이고, `state_changes` 의 원장 값 `since` 가 바로 그 사건이다(그 사건이 지금 값을 확립). |
| 사건(시점 고정) | `past` 이고 `state_changes` 가 없다. |
| 앎 · 규칙 · checker · 장면 | 그대로 둔다. |
| 애매 | `past` 인데 원장 슬롯이 없거나 `since` 가 더 앞이다. **세 묶음 모두 0건이었다.** |

- **같은 슬롯:** 가설(entity, field)과 전제가 같은 (엔티티, 필드)를 말하는 쌍이다. 원장 전제는 필드가 일치하면, 과거 사건은 그 슬롯의 `state_changes` 가 있으면, checker 는 항상 해당한다.
- **값 바뀜 꼴:** 같은 슬롯이면서 가설의 assumes 와 전제 값(ID→이름)이 정규화한 뒤 서로를 포함하지 않는 쌍이다. 자유 서술과 ID 값의 비교라서 거의 모든 같은 슬롯 쌍이 "다름"으로 나왔다(Q1-sel 625/630).
  그래서 아래 표에는 **같은 슬롯** 비율만 적는다. 값 비교 규칙은 근사로만 기록한다.

전체 쌍 분포 [측정]:
- Q1-sel 4,807쌍: 앎 1,749 · 현재(원장 + 과거 사건) 1,066 · 규칙 804 · **옛 상태 750 (15.6%)** · 사건 167 · checker 138 · 장면 133
- Q1-mine 2,338쌍: 옛 상태 361 (15.4%)
- WM 1,440쌍: 옛 상태 96 (6.7%)

### 재현 대조 [측정]

- 쌍 점수는 같은 모델·조건(GPU fp16 · eager, `score_pairs --max-length 384`)에 `--scores-out` 을 붙여 다시 냈다.
- Q1-sel 은 L · M · E1 셋 다 probs sha256 이 실험 M 의 `sel/*_max.json` 과 같다. Q1-mine 의 L 도 실험 M 채굴 채점과 sha256 이 같다.
- WM 정상 528쌍의 L 점수는 4쌍이 ≤ 6.1e-5 다르다. 파일이 달라 fp16 버킷 배치 구성이 다르기 때문이다. 장면 max 는 모두 같다.
- **재현 일치**(WM 은 장면 지표 일치).

### 쏠림 — 정상 장면 (a) 장면 max 쌍 · (b) 장면별 상위 5쌍 · (c) 전체(기준) [측정]

| 모델 · 장면 | 옛 상태 (a) · (b) · (c) | 같은 슬롯 (a) · (b) · (c) | (a) 의 분류 |
|---|---|---|---|
| **M · Q1-sel 정상 13** | **0.00** · 0.05 · 0.16 | **0.92** · 0.85 · 0.10 | 현재(원장) 12 · 현재(과거 사건) 1 |
| **L · Q1-mine 12** | **0.00** · 0.05 · 0.15 | 0.33 · 0.37 · 0.14 | 현재(원장) 7 · 앎 3 · 현재(과거 사건) 2 |
| **L · WM 정상 10** | **0.10** · 0.06 · 0.08 | 0.60 · 0.36 · 0.05 | 현재(원장) 5 · 사건 3 · 옛 상태 1 · checker 1 |
| L · Q1-sel 정상 13 | 0.00 · 0.11 · 0.16 | 0.31 · 0.39 · 0.10 | 현재(원장) 9 · 앎 2 · checker 1 · 현재(과거 사건) 1 |
| M · Q1-mine 12 (학습에 쓴 장면) | 0.00 · 0.03 · 0.15 | 0.83 · 0.72 · 0.14 | 현재(원장) 10 · 규칙 1 · 현재(과거 사건) 1 |
| M · WM 정상 10 | 0.00 · 0.12 · 0.08 | 0.50 · 0.34 · 0.05 | 현재(원장) 5 · 앎 2 · 사건 2 · 규칙 1 |
| E1 · Q1-sel 정상 13 | 0.15 · 0.20 · 0.16 | 0.08 · 0.03 · 0.10 | 현재(원장) 4 · 앎 4 · 옛 상태 2 · 규칙 2 · 장면 1 |
| **E1 · Q1-mine 12** | **0.33** · 0.18 · 0.15 | 0.00 · 0.05 · 0.14 | **옛 상태 4** · 앎 3 · 규칙 3 · 사건 1 · 현재(원장) 1 |
| E1 · WM 정상 10 | 0.00 · 0.02 · 0.08 | 0.10 · 0.08 · 0.05 | 사건 4 · 현재(원장) 3 · checker 2 · 현재(과거 사건) 1 |

양성 장면의 키 쌍(v2 모순 라벨) [측정]:
- Q1-sel 21쌍 = 앎 8 · 현재(과거 사건) 4 · **옛 상태 4** · 현재(원장) 3 · 사건 2. 키 쌍이 있는 장면은 12/20 이고, 키 쌍이 모두 옛 상태인 장면은 1(scene_08)이다.
- WM 45쌍 중 옛 상태는 4 다. 모두 옛 상태인 장면은 2(scene_23 · 29)다.

### 빼 보기 — 옛 상태 전제 쌍을 빼고 장면 점수 재계산 (점수 그대로, 층화 bootstrap 2,000 시드 0) [측정]

Q1-sel 에서 750쌍, WM 에서 96쌍을 뺐다. 빠져서 없어진 장면은 없다(33 → 33, 30 → 30).

| 모델 · 집계 | Q1-sel 빼기 전 | Q1-sel 뺀 뒤 | WM 빼기 전 (참고: 학습 작품) | WM 뺀 뒤 |
|---|---|---|---|---|
| M · max | 0.512 [0.308, 0.715] | 0.512 [0.308, 0.715] | 0.815 | 0.810 |
| **M · 가설 대비** | **0.542 [0.327, 0.758]** | **0.515 [0.308, 0.723]** | 0.590 | 0.600 |
| L · max | 0.446 [0.254, 0.646] | 0.442 [0.250, 0.642] | 0.890 | 0.895 |
| L · 가설 대비 | 0.335 [0.158, 0.527] | 0.346 [0.169, 0.542] | 0.870 | 0.870 |
| E1 · max | 0.492 [0.285, 0.692] | 0.500 [0.292, 0.700] | 0.475 | 0.475 |
| E1 · 가설 대비 | 0.546 [0.342, 0.750] | 0.512 [0.312, 0.708] | 0.445 | 0.440 |

- 키 쌍이 옛 상태라서 같이 빠지는 양성 장면: Q1-sel 1(scene_08), WM 2(scene_23 · 29). 실제 사용에서도 놓치는 경우다.
- 이것은 진단이지 선택이 아니다. 셜록·Q3 에는 적용하지 않았다.

### 부정어 · 겹침 (같은 쌍, (a) 장면 max 쌍 대 (c) 전체) [측정]

- 부정어 규칙: 정규식 `않|없|아니|못|모르|몰라|몰랐|모름|(?<![가-힣])안(?=\s)` 이 가설·전제 각각에 있는지 본다.
- 겹침: 정규화 문자 2-gram Jaccard 중앙값.

| 모델 · 장면 | 가설 부정어 (a) · (c) | 전제 부정어 (a) · (c) | 2-gram Jaccard 중앙 (a) · (c) |
|---|---|---|---|
| M · Q1-sel 정상 | 0.62 · 0.60 | **0.85 · 0.61** | 0.070 · 0.055 |
| L · Q1-mine | 0.42 · 0.39 | 0.75 · 0.57 | 0.054 · 0.051 |
| L · WM 정상 | 0.30 · 0.32 | **0.50 · 0.21** | 0.060 · 0.037 |
| E1 · Q1-sel 정상 | 0.69 · 0.60 | 0.77 · 0.61 | 0.054 · 0.055 |

### 읽는 법 [추정]

- **가설은 Weave 쌍으로 학습한 모델(L·M)에서는 기각된다.** L·M 의 정상 장면 max 쌍에는 옛 상태가 거의 없다(0~10%. 기준 8~16% 와 비슷하거나 낮다 — Q1 은 0 대 0.15~0.16).
  대신 **같은 슬롯의 현재 원장 값**에 쏠린다(M Q1-sel 0.92 대 기준 0.10).
  즉 L·M 은 "가설이 말하는 (엔티티, 필드)의 원장 값이 쌍에 있다"는 형식 자체를 모순 신호로 읽는다. 바뀐 상태가 원인이 아니다.
  학습 데이터 L1(WM 반사실: 원장 전제 하나와 충돌하는 가설)의 형식이 이 지름길을 가르쳤을 수 있다.
- **Weave 학습이 없는 E1 에서는 약하게 맞다.** Q1-mine 에서 max 쌍의 옛 상태가 0.33(기준 0.15, 2.2배)이다. 다만 Q1-sel 0.15(기준 0.16)·WM 0.00 이라 일관되지 않다(장면 10~13개).
- **빼 보기는 효과가 없다.** Q1-sel 장면 AUROC 변화는 −0.03 ~ +0.01 이고, 모두 구간 안에 있다. 옛 상태 전제를 빼거나 표시하는 것은 지금 모델에서 싼 개선이 되지 않는다.
- 전제 부정어가 max 쌍에서 더 많다(M Q1-sel 0.85 대 0.61, L WM 0.50 대 0.21). 문자 겹침은 조금 높다. 부정 표현이 든 전제가 모순 쪽으로 끌리는 편향이 있다.
- 다음 후보(이 진단 범위 밖): 같은 슬롯 원장 전제의 일치판(정상)을 학습에 늘린다 — 어려운 음성을 원장 같은 슬롯에서 직접 만든다. 또는 묶음 입력·학습형 집계로 간다.

### 쓰지 않은 것

셜록·Q3 는 쓰지 않았다(점수 파일 분석도 안 함). 새 학습은 없다. 쌍 점수 파일: `C:\judge_models\T\{L,M,E1}_{q1sel,q1mine,wm}_scores.jsonl`.

## 실험 N: 형식 지름길 제거 (자연어 쌍 v3 + 같은 슬롯 일치 음성)

> 2026-11-08 세션 K(지시문 K-N). 사전 확정과 그 뒤 추가 규칙:
> - 사전 확정: `docs/learned-judge-training-design.md` §0-9(main 7a1ec6a)
> - 학습 전 보정: main c17fcd5
> - 규칙 명확화: main be68ebb — 학습 전에 기록했다.
>
> 유료 API 0, llama-server 안 띄움, 타이타닉·Q3 는 열지 않았다. 셜록은 개발 참고 1회(**11번째**)다.
> 꼬리표: [측정] · [기록] · [추정] · [감사: 작업 세션 판정, 사람 아님].

### N-0 원인 확인 (학습 없음) [측정] — `C:\judge_models\N\n0.json`

**값 추출 규칙:**
- L1·L2 가설은 `hyp_spec.assumes` 다(사건 가설은 제목 + 요약).
- 전제는 렌더링 글에서 kind 별 값 부분을 쓴다: 원장 `= 값 (since` · 앎 `앎: 정보 (via` · 규칙 `: 내용` · checker `원장 값` · 과거·장면 `제목: 요약`.
- 실제 쌍은 v2 글에 같은 규칙을 적용했다. 지표는 정규화 문자 2-gram Jaccard, 부정어는 진단 T 정규식이다.

| 묶음 | n | Jaccard p25 · **p50** · p75 | 부정어 가설 · 전제 |
|---|---|---|---|
| L1 모순 | 259 | 0.035 · **0.093** · 0.227 | 0.22 · 0.21 |
| L1 일관 | 259 | 0.088 · **0.160** · 0.455 | 0.11 · 0.21 |
| L2 모순 / 일관 | 172 / 172 | p50 0.045 / 0.073 | 0.14 · 0.20 / 0.13 · 0.20 |
| WM 실제 모순 / 정상 / 무관 | 45 / 20 / 625 | p50 0.051 / 0.000 / 0.009 | 가설 0.40 / 0.15 / 0.27 |
| 같은 슬롯 실제 정상 (WM 정상 · Q1-mine) | 20 · 245 | p50 **0.000 · 0.000** | 0.15 · 0.04 |

**규칙 판정: L1 일관 / 모순 = 1.72 ≥ 1.5 → (a) 확인, N-2b 실행.**
실제 같은 슬롯 정상 쌍은 값 겹침이 0 이다(자유 서술 대 원장 값). 학습 데이터의 "일관 = 원장 문구와 겹침"과 정반대다.

### N-1 쌍 렌더링 v3 (커밋 ae9f413)

- `pairs.build_pairs(render="v3")`, `PAIRS_VERSION_V3 = "3"`. 서비스 기본은 v2 다. `learned_judge` 는 모델 meta 의 `pairs_version`(2 | 3)에 맞춰 렌더링을 고른다.
- **전제 선택은 v2 와 같다.** 관련도·중복 제거는 v2 문자열로 계산하고, v3 글은 같은 (kind, id, v2 본문)에 대응시켜 바꿔 넣는다.
- **확인:**
  - v2 출력은 기존 내보내기와 바이트 단위로 같다(WM 1,440 · Q1 7,145 행 전부).
  - v3 의 (kind, premise_id, entity, field, event_id) 목록은 v2 와 같다(테스트 고정).
  - 라벨은 v2 글로 계산한다. 그래서 v3 내보내기의 라벨은 v2 와 행마다 같다(WM · Q1 · 셜록).
- **형식:**
  - 상태 가설 `[M화] “근거” → 이름의 필드: 값`, 사건 가설 `[M화] 제목: 요약. 원문: “발췌”`
  - 원장 `N화 기준 이름의 필드: 값. 원문: “since 발췌”`(상실은 `(지금은 잃었다)`, 일시적은 `(일시적이며, 해소되면 …)`, 승격은 `(앞서 추정된 전제)`)
  - 앎 `이름은(는) 정보을(를) 안다 (N화)`(보유하지 않으면 `알았으나 지금은 모른다`), 과거·장면 `[N화] 제목: 요약. 원문: “…”`
  - 규칙 `세계관 규칙: 내용`, checker `이름의 필드: 전제 X / 원장 Y`
  - 자유 서술 안의 레지스트리 ID 는 이름으로 바꾼다. 이름이 없는 ID 는 그대로다(WM 가설 24쌍에 남음).
- **필드 한국어 대응표(`FIELD_KO`):** location 위치 · body_status 몸 상태 · possesses 소지품 · knows 앎 · condition 상태 · alive 생사 · ability 능력 · relationship 관계 · affiliation 소속 ·
  occupation 직업 · appearance 외모 · emotion 감정 · belief 믿음 · health 건강 · role 역할 · status 처지 · age 나이 · name 이름 · identity 정체 · goal 목표 · mental_state 마음 상태.
  모르는 필드는 원문 그대로 둔다.
- **연결:** `export_pairs --pairs-version 3`, `claude_pairs to-v3`(v2 재렌더링이 저장본과 같은지 먼저 확인), `score_pairs`(쌍 파일 판 ↔ meta 대조, `--ignore-meta-pairs-version` 은 영 학습 기준선용),
  `train_judge`(meta pairs_version 을 데이터에서 읽고, 섞여 있으면 거부).

### N-2 학습 데이터 (v3, 주입 0) — `C:\judge_models\N\train_v3\`, `n2_report.json`

- **N-2a 같은 슬롯 일치 음성:** 진단 T 정의(v2 행 기준, 선택이 같으므로 같은 쌍)로 WM 정상 9장면 26쌍과 Q1-mine 12장면 315쌍, 합 **341** 을 '정상'으로 넣었다.
  원래 라벨은 정상 265 · 무관 76 이고, kind 는 원장 212 · 과거 76 · checker 53 이다. WM 의 26쌍은 WM 실제 행에서 옮겼다(중복 없음). Q1-sel 장면은 0 이다.
- **어려운 음성:** M 의 440 을 v3 행으로 찾았다(누락 0). 그중 N-2a 와 겹친 75 는 N-2a(정상)로 두고 빼서 **365**(전부 무관)가 남았다.
- **N-2b:** L-gen 생성 일관 224 · 모순 224 다. 코드 `n2b_pairs.py` 는 L-gen 브랜치 357a7cb 를 **cherry-pick** 해 받았다(29c4f33). 이 브랜치에 새 커밋이 있어 ff 가 안 된다.
  감사 [감사]: 일관 30/30 · 모순 26/30 · 누수 0. v2 글을 같은 쌍의 v3 로 바꿨다(v2 재렌더링 일치 확인).
- **학습 전 보정 (1) — 머리·인용 불일치 제거:**
  - 규칙: N-2b 모순 행에서 바뀐 값 토큰(가설 값 = assumes 또는 요약의 토큰 중 전제 값에 없는 것; 조사 떼기, 한글 수사→숫자, 3자 이상은 끝 1자 뺀 어간 일치)이 근거 인용에 하나도 없으면 버린다.
  - 결과: **104/224 를 버렸다**(남은 모순 120). 감사 표본에서는 감사가 짚은 5개(모6 · 13 · 15 · 23 · 30)와 아님 판정인 모26 이 걸렸다.
    감사가 예·예△ 로 본 **6행(모9 · 10 · 11 · 17 · 18 · 27)도 같이 빠졌다** — 동의어로 풀어 쓴 인용이다. 사전 규칙대로 그대로 갔다 [기록].
- **학습 전 보정 (2) — 겹침 단일 특징 AUROC:**
  - 특징은 v3 글 기준, 가설 문자 2-gram 이 전제에 든 비율이다(감사와 같은 정의). '제외'는 뺐다.
  - 규칙 명확화(be68ebb)에 따라 검사 대상은 **모순 대 정상**이다. 0.481 이 범위 [0.40, 0.60] 안이라 **줄이지 않았다**.

| 겹침 단일 특징 AUROC [측정] | 값 |
|---|---|
| 혼합 전체, 모순 대 비모순(정상 + 무관), 포함률 | 0.652 |
| 같은 대상, Jaccard | 0.695 |
| 부정어(가설 또는 전제) 단일 특징, 모순 대 비모순 | 0.525 (보고만) |
| **모순 대 정상, 포함률 (검사 대상)** | **0.481** — 범위 안 |

| 묶음 [측정] | n | 겹침(포함률) p25 · p50 · p75 |
|---|---|---|
| L1 모순 / 정상 | 259 / 259 | 0.154 · 0.261 · 0.333 / 0.208 · 0.333 · 0.414 |
| L2 모순 / 정상 | 172 / 172 | 0.089 · 0.127 · 0.179 / 0.119 · 0.175 · 0.250 |
| N-2b 모순 / 정상 (보정 1 뒤) | 120 / 224 | 0.293 · 0.419 · 0.562 / 0.125 · 0.188 · 0.269 |
| N-2a 같은 슬롯 정상 | 341 | 0.172 · 0.238 · 0.333 |
| 어려운 음성 무관 | 365 | 0.040 · 0.093 · 0.141 |
| WM 실제 무관 | 619 | 0.026 · 0.065 · 0.122 |
| **실제 양성 (WM v2 모순 키 쌍)** | 45 | **0.077 · 0.131 · 0.200** |

0.652 를 만드는 것은 무관 쪽의 낮은 겹침이다. 이것은 관련/무관 신호이고 실제 서비스 쌍에서도 성립한다(be68ebb 사유).

**최종 행 수:**

| L1 | L2 | N-2b | WM 실제 | N-2a | 어려운 음성 | 합 |
|---|---|---|---|---|---|---|
| 모순 259 · 정상 259 | 모순 172 · 정상 172 | 모순 120 · 정상 224 | 모순 45 · 무관 619 · 제외 750 | 정상 341 | 무관 365 | 모순 596 · 정상 996 · 무관 984 · 제외 750 |

- 음성 1,980 ≤ 4 × 596 이라 줄이지 않았다.
- 떼어 둔 10% 는 M 과 같은 `val_groups.txt` 다. N-2b 는 L1 장면 group_id 를 따른다(3장면).

### N-3 학습·선택 (`C:\judge_models\N\model\`, hash d1633791…, pairs_version "3")

`train_judge.py --train l1 l2 n2b wm_real n2a hardneg(train_v3) --val-groups M/val_groups.txt --scene-unit pair --init-model E1 --max-length 384 --seeds 2`

- 학습 2,218 · 검증 358쌍이다(검증: 모순 68 · 정상 196 · 무관 94).
- 검증 NLL 은 **0.482** → 0.517 → 0.707 이다(최선 에폭 1, 3에폭에서 조기 종료) [측정]. 온도 T 1.292 를 붙이면 0.461 이다.
  참고 [기록]: L 은 1.439(T 3.41), M 은 1.157(T 2.56). 이번이 보정이 가장 잘 된 상태다.

**Q1-sel 집계 선택** (33장면: 양성 20 · 정상 13, 층화 bootstrap 2,000 시드 0, 기준값 = 정상 오탐 ≤ 1/13 에서 recall 최대) [측정]:

| 모델 | (i) max | (ii) 가설 대비 |
|---|---|---|
| **N** | **0.658 [0.462, 0.838]** · thr 0.9326 → 6/20, FP 1/13 | 0.562 [0.358, 0.758] · thr 0.775 → 5/20, FP 1/13 |
| E1 영 학습 v3 (비교) | 0.681 [0.473, 0.865] · thr 0.9328 → 3/20, FP 1/13 | 0.558 [0.350, 0.754] · 6/20, FP 1/13 |
| 참고 [기록: 실험 M, v2 글] | M max 0.512 · E1 max 0.492 | M 0.542 · E1 0.546 |

**고른 집계: (i) max.** 기준값은 0.9326(Q1-sel)이다.

### N-4 개발 관문 (사전 확정: 둘 다) [측정] — `C:\judge_models\N\gate_sameslot.json`

Q1-sel 정상 13장면의 쌍 분류(진단 T 와 같은 규칙):

| 모델 | 같은 슬롯 (a) max 쌍 · (b) 상위 5 · (c) 기준 | 옛 상태 (a) · (b) · (c) | (a) 의 분류 |
|---|---|---|---|
| **N** | **0.154** · 0.277 · 0.104 | 0.077 · 0.185 · 0.157 | 앎 7 · 현재(원장) 2 · 현재(과거 사건) 1 · 옛 상태 1 · 규칙 1 · 사건 1 |
| E1 영 학습 v3 | 0.231 · 0.231 · 0.104 | **0.615** · 0.292 · 0.157 | 옛 상태 8 · 현재(원장) 3 · 앎 1 · 현재(과거 사건) 1 |
| 참고 [기록: 진단 T, v2 글] M | 0.923 · 0.846 · 0.104 | 0.000 · 0.046 · 0.157 | 현재(원장) 12 · 현재(과거 사건) 1 |

| 관문 | 값 | 판정 |
|---|---|---|
| (1) Q1-sel 장면 AUROC ≥ 0.65 (점추정) | **0.658** | 통과 |
| (2) Q1-sel 정상 장면 max 쌍의 같은 슬롯 비율 ≤ 0.30 | **0.154** | 통과 |
| **개발 관문** | | **통과** |

- 짚을 수 있는 Q1-sel 양성 12장면의 키 쌍 순위(N): 1, 2, 5, 11, 14, 22, 28, 41, 45, 66, 72, 88.
- 관문은 통과했지만 **타이타닉·Q3 는 열지 않았다.** 최종 1회는 판단 세션이 따로 지시한다.

### 셜록 개발 참고 1회 (11번째, N · max · T 1.292 · 기준값 0.9326 고정, 384, 층화 bootstrap 2,000 시드 0) [측정]

- v3 로 다시 내보냈다(`export_pairs --work sherlock --real --pairs-version 3`, 6,507쌍). 라벨·쌍 키는 v2 내보내기와 행마다 같다.

| 조건 | 셜록 장면 AUROC [95%] | recall · 오탐 @Q1-sel 기준값 | 참고: 셜록 안 FPR≤1/25 | 쌍 PR-AUC (양성 0.0037) |
|---|---|---|---|---|
| **N · max (v3)** | **0.640 [0.472, 0.798]** | **8/20 · 4/25** | 6/20, FP 1 | 0.040 |
| 기존 표 [기록] | 0.410(large `_nli`) ~ 0.650(E4) | — | 0/20 ~ 2/20 | 0.004 ~ 0.062 |

셜록은 관문이 아니다. 지금까지 셜록을 쓴 11개 조건 중 하나를 보여 주는 값이다(개발 벤치, 진단 S·T 에서 쌍 분포까지 본 벤치). 일반화 수치가 아니다.

### 읽는 법 [추정]

- **형식 지름길은 사라졌다.** 정상 장면 max 쌍의 같은 슬롯 비율이 M 0.92 에서 N 0.15 로 내려 기준(0.10) 근처다. max 쌍은 대부분 앎 전제로 옮겨 갔다.
- **v3 렌더링 자체가 큰 몫이다.** Weave 쌍을 본 적 없는 E1 에 v3 글만 줘도 Q1-sel max 가 0.492(v2) → 0.681(v3)로 오른다.
  N(학습) 0.658 은 E1 v3 영 학습과 구간이 크게 겹친다. Q1-sel 에서 학습의 이득은 보이지 않는다.
  다만 E1 v3 의 max 쌍은 옛 상태로 쏠린다(0.615 대 기준 0.157). N 은 이 쏠림도 없앴다(0.077). 그리고 기준값에서 recall 6/20 대 3/20 이다.
- 셜록 참고값 0.640 은 기존 최고(0.650)와 같은 수준이다. 하지만 셜록 안에서 고른 기준값의 recall 이 6/20 으로, 지금까지(0~2/20)보다 높다. Q1-sel 기준값에서는 8/20 · 오탐 4/25 다.
- 양성 표본이 작아(Q1-sel 20 · 셜록 20) 구간 반폭이 ~0.18 이다. 관문 통과는 점추정 기준이다.

코드 커밋: ae9f413(v3 렌더링·연결·테스트), 29c4f33(n2b_pairs, cherry-pick). 데이터·모델: `C:\judge_models\N\`. 전체 suite 551 passed / 1 skipped / 3 xfailed (`TEST_DB_NAME=glitch_noai_test_labelfix`) [측정].

## 실험 N — Q3 최종 1회

> 2026-11-08 세션 K. 사용자 승인. 조건은 채점 전에 `docs/learned-judge-training-design.md` §0-9 "Q3 최종 1회"(main 81e87ac)에 확정했다.
> 결과를 본 뒤 아무것도 바꾸지 않았다. **Q3 는 이 1회로 끝이다.** 유료 API 0, 타이타닉 안 씀.
> 꼬리표:
> - Q3 평가 장면 45개 중 40개는 claude-opus-5 집필(사람 5)이고, N 학습 데이터에도 Claude 생성분이 있다. 치우친다면 N 에 유리한 쪽이다.
> - [측정] · [기록] · [추정].

### 쌍 (처음 열었다) [측정] — `C:\judge_models\Q3final\`

- `export_pairs --work q3 --real --pairs-version 2|3`, 라벨 v2. `export_pairs` 에는 `--allow-eval-work` 가 없다(평가 작품 거부는 `score_pairs` 에 있다).
- 두 벌 모두 5,112쌍이다. 쌍 키·라벨은 행마다 같다.
- 장면 45 = 양성 20 · 정상 25. v2 모순 키 쌍이 있는 양성 장면은 16 이다.
- 라벨 분포:

| | 모순 | 정상 | 무관 | 제외 |
|---|---|---|---|---|
| 양성 장면 | 52 | — | 518 | 1,878 |
| 정상 장면 | — | 443 | 2,221 | — |

### 채점 (각 1회, `score_pairs --allow-eval-work --max-length 384 --aggregate max`, 층화 bootstrap 2,000 시드 0) [측정]

| 조건 | 글 | 온도 · 기준값(Q1-sel) | **Q3 장면 AUROC [95%]** | recall · 오탐 @기준값 | 쌍 PR-AUC |
|---|---|---|---|---|---|
| **N (후보)** | v3 | 1.292 · 0.9326 | **0.590 [0.420, 0.748]** | **3/20 · 1/25** | 0.033 |
| E1 영 학습 | v3 | 1.152 · 0.9328 | 0.514 [0.344, 0.676] | 0/20 · 0/25 | 0.019 |
| E1 영 학습 | v2 | 1.152 · 0.7572 (실험 M Q1-sel) | 0.550 [0.382, 0.714] | 0/20 · 2/25 | 0.016 |
| 동결 LLM 열 [기록] | — | — | — | 3/20 · 1/25 | — |

**판정: N 의 Q3 장면 AUROC 0.590, 하한 0.420 — 구간이 0.5 를 포함하므로 "없음".**

### 함께 보고 [측정] — `analysis.json`, `judge_eval_learned_N.json`

- **동결 열과의 대응 비교(같은 양성 20장면):** 둘 다 3/20 이지만 **잡은 장면이 하나도 겹치지 않는다.**
  - N 만 잡음: scene_12 · 35 · 41
  - 동결만 잡음: scene_02 · 25 · 44
  - 엇갈린 장면 6, 둘 다 잡은 장면 0. 합치면 6/20 이다.
  - 오탐은 N scene_01, 동결 scene_26 이다.
  - 하네스 대응 bootstrap(10,000회): recall 차 +0.000, 95% CI [−0.250, +0.250].
- **N − E1 v3 장면 AUROC 차(대응 층화 bootstrap 2,000 시드 0):** +0.076 [−0.136, +0.272]. 0 을 포함하므로 학습의 몫은 가르지 못한다.
  참고로 E1 v3 − E1 v2 는 −0.036 [−0.216, +0.148] 이다. Q3 에서는 렌더링 이득도 보이지 않는다(Q1-sel 의 0.492 → 0.681 과 다르다).
- **정상 장면 max 쌍의 같은 슬롯 비율:**

| 모델 | 같은 슬롯 (a) max · (b) 상위 5 · (c) 기준 | 옛 상태 (a) · (b) · (c) | (a) 의 분류 |
|---|---|---|---|
| N | 0.20 · 0.38 · 0.20 | 0.16 · 0.08 · 0.10 | 앎 10 · 규칙 6 · 옛 상태 4 · 현재(원장) 4 · 현재(과거 사건) 1 |
| E1 v3 | 0.36 · 0.24 · 0.20 | 0.08 · 0.16 · 0.10 | 현재(원장) 10 · 규칙 5 · 현재(과거 사건) 4 · 사건 2 · 앎 2 · 옛 상태 2 |
| E1 v2 | 0.16 · 0.16 · 0.20 | 0.12 · 0.09 · 0.10 | 현재(과거 사건) 8 · 앎 5 · 현재(원장) 5 · 옛 상태 3 · 규칙 2 · checker 2 |

  N 은 Q3 에서도 기준(0.20)에 머문다. 같은 슬롯 지름길은 Q3 에서도 다시 나타나지 않았다.
- **키 쌍 순위(N, 키 쌍 있는 양성 16장면):** 3, 5, 8, 10, 11, 11, 14, 16, 20, 23, 25, 26, 42, 68, 96, 112. 장면 1위는 0 이다.
- **하네스 경로 확인:** `judge_eval.py --mode learned --works q3 --compare-recorded`(WEAVE_JUDGE_BACKEND=hf, MODEL_DIR=N, THRESHOLD=0.9326, cuda)로 돌렸다.
  - 결과는 Recall 3/20 · 오탐 1/25 로 `score_pairs` 와 같다(놓친 17장면 목록도 같다).
  - 하네스는 meta `pairs_version` "3" 을 읽어 v3 로 렌더링한다.
  - 미탐 귀속: 원장 부재 8 / 판정자 9. 동결 열은 원장 부재 7 / 판정자 10 [기록].
  - 사람 집필 모순 3장면에서 N 의 TP 는 0 이다.

### 읽는 법 [추정]

- 개발 관문(Q1-sel 0.658, 같은 슬롯 0.154)은 넘었지만, **Q3 에서는 0.590 [0.420, 0.748] 로 "없음"이다.** Q1-sel 의 이득 대부분은 Q1 에 맞춘 값이었다.
  33장면 선택 벤치와 v3 영 학습 이득(0.681)이 Q3 로 옮겨 가지 않았다(E1 v3 0.514).
- recall·오탐은 동결 LLM 열과 같다(3/20 · 1/25). 하지만 잡는 장면이 완전히 다르다. 두 판정기를 합치면 6/20 이 될 수 있다는 뜻이다. 다만 이 결합은 사전 확정이 아니므로 결과로 쓰지 않는다.
- N 은 Q3 의 v3 영 학습보다 +0.076 높다. 그러나 구간이 0 을 넓게 포함한다(양성 20).
- 정리: 학습형 judge 트랙의 사전 확정 최종 평가는 **"없음"**으로 끝났다. 발표 시연은 동결 LLM judge 녹화·재생 경로다(§0-7(c) 시간 상자 결정 그대로).

## 실험 N — 타이타닉 최종 1회

> 2026-11-08 세션 K, 지시문 K-T1. 조건은 채점 전에 `docs/learned-judge-training-design.md` §0-9 "타이타닉 최종 1회"·"추출 재실행"(main ace3c2d·36e54a2·8da0fcf)에 확정했다.
> 결과를 본 뒤 아무것도 바꾸지 않았다. **타이타닉은 이 1회로 끝이다.** LLM judge·유료 API 0회, push·main 병합 없음.
> 꼬리표:
> - 모순 작성자(Sonnet 5.5)와 N 학습 데이터 생성기(Sonnet 5.5)가 같다. 치우친다면 N 에 유리한 쪽이다. 원문은 사람 저작이고, 사용자 검토와 다른 LLM 검증을 거쳤다.
> - **구조상 오라클 상한 0.625 [0.542, 0.708](R 6/24)은 관문 0.65 보다 낮다. 이는 채점 전부터 알려진 사실이다** [기록: M3-E2].
> - 추출 설정은 타이타닉 구조 진단(judge 점수 없음)을 보고 고쳤다(인물 시드·ID–이름 가드). 그래서 추출 recall 은 낙관 값이다.
> - [측정] · [기록] · [추정].

### 준비 [측정] — `C:\judge_models\T1final\`

- **봉인 대조:** `SEAL.json` sha256 `5b2f2e0f…1d69686` 은 지시값과 같다. 등재 113파일의 sha256 도 모두 같다. `titanic\` 안에 등재되지 않은 파일은 없다.
  등재 파일에는 `../work/chunk_map.json` 과 `../work/conditional_events.json` 도 들어 있다.
  코퍼스는 재추출본(run2)이고, `work\run1\` 은 쓰지 않았다.
- **코드 받기:** M3 브랜치 `claude/titanic-m3-extract-725000`(17753bd 까지, 6a366cc 에서 분기)를 받았다.
  K 브랜치와 갈라져 ff 가 불가능했으므로 **cherry-pick** 11커밋(c0e125c…a9fc890)으로 받았다.
  `export_pairs.py` 충돌은 둘 다 살려 풀었다(`pairs_version` 인자 + `JE.WORKS` 검사). 나머지 7파일은 M3 브랜치와 같다. 관련 테스트 236 통과.
- **쌍:** `export_pairs --work titanic --corpus-dir C:\judge_models\M3\titanic --real --pairs-version 2|3`, 라벨 v2.
  - 두 벌 모두 22,828쌍이다. 쌍 키·라벨은 행마다 같다. M3-E2 측정과도 같다.
  - 장면 48 = P 24 · N 24. 모순 키 쌍이 있는 P 장면은 6(06·10·19·24·30·38)으로, R 6/24 와 같다.
  - 라벨 분포:

| | 모순 | 정상 | 무관 | 제외 |
|---|---|---|---|---|
| P 장면 | 8 | — | 9,862 | 1,833 |
| N 장면 | — | 872 | 10,253 | — |

### 채점 (각 1회, `score_pairs --eval-work titanic --allow-eval-work --max-length 384 --aggregate max`, cuda, 층화 bootstrap 2,000 시드 0) [측정]

| 조건 | 글 | 온도 · 기준값(Q1-sel) | **장면 AUROC [95%]** | recall · 오탐 @기준값 | 쌍 PR-AUC (양성 비율 0.0004) |
|---|---|---|---|---|---|
| **N (후보)** | v3 | 1.292 · 0.9326 | **0.636 [0.471, 0.785]** | **11/24 · 3/24** | 0.0004 |
| E1 영 학습 | v3 | 1.152 · 0.9328 | 0.602 [0.431, 0.763] | 3/24 · 2/24 | 0.0008 |
| E1 영 학습 | v2 | 1.152 · 0.7572 | 0.551 [0.381, 0.714] | 11/24 · 8/24 | 0.0007 |
| 구조 오라클 [기록 M3-E2] | — | — | 0.625 [0.542, 0.708] | — | — |
| 고친 흔적 \|길이 차\| [기록 M3-E] | — | — | 0.624 [0.464, 0.773] | — | — |

**판정: N 의 타이타닉 장면 AUROC 0.636 (< 0.65), 하한 0.471 (< 0.5) — 두 조건 모두 못 넘으므로 "없음".**
LLM judge 비교는 사전 확정("못 넘으면 LLM judge는 돌리지 않는다")에 따라 돌리지 않았다.

### 함께 보고 [측정] — `analysis.json`, `judge_eval_learned_N.json`

- **N 의 TP 11 · FP 3:**
  - TP 장면: 10·11·12·19·24·25·38·39·41·42·46. 키 쌍이 있는 6장면 중 4(10·19·24·38)를 잡았고, **나머지 7장면에는 키 쌍이 없다.**
  - FP 장면: 05·31·40. 각각 EVENT_069·054·071 의 N 장면이고, 세 사건 모두 쌍둥이 P 장면도 TP 다.
- **N − E1 v3 장면 AUROC 차(대응 층화 bootstrap 2,000):** +0.035 [−0.143, +0.200]. E1 v3 − E1 v2 는 +0.050 [−0.127, +0.217] 이다.
- **고친 흔적 지름길 기준선(M3 E 단계, 쌍 없이 P/N 수정 문장만):**
  - \|길이 차\| 0.624 [0.464, 0.773], 부호 있는 길이 차 0.559, 편집 거리 0.534, 정규화 편집 거리 0.530.
  - **N 0.636 은 이 기준선과 구간이 거의 겹친다.**
- **추론 한 단계 장면을 뺀 값(보조, 사건 048·056·062·063·067):**

| 모델 | P·N 10장면 뺌 (19/19) | P 5장면만 뺌 (19/24) |
|---|---|---|
| N | 0.544 [0.357, 0.734] | 0.578 [0.404, 0.757] |
| E1 v3 | 0.615 [0.424, 0.791] | 0.612 [0.432, 0.781] |
| E1 v2 | 0.553 [0.366, 0.729] | 0.538 [0.364, 0.721] |

  N 의 TP 11 중 4(11·24·38·46)가 이 사건들의 P 장면이다.
- **정상 장면 max 쌍의 같은 슬롯 비율(진단 T 정의):**

| 모델 | 같은 슬롯 (a) max · (b) 상위 5 · (c) 기준 | 옛 상태 (a) · (b) · (c) | (a) 의 분류 |
|---|---|---|---|
| N | **0.417** · 0.342 · 0.115 | 0.21 · 0.13 · 0.21 | 현재(원장) 10 · 옛 상태 5 · 앎 3 · checker 2 · 사건 2 · 현재(과거 사건) 1 · 장면 1 |
| E1 v3 | 0.208 · 0.167 · 0.115 | 0.17 · 0.21 · 0.21 | 앎 11 · 현재(과거 사건) 6 · 옛 상태 4 · 현재(원장) 3 |
| E1 v2 | 0.208 · 0.125 · 0.115 | 0.17 · 0.18 · 0.21 | 현재(과거 사건) 6 · 앎 5 · 옛 상태 4 · checker 3 · 현재(원장) 3 · 장면 2 · 사건 1 |

  **타이타닉에서는 N 의 같은 슬롯 쏠림이 다시 나타났다(기준의 3.6배, 현재 원장 10/24).** Q1-sel 은 0.154, Q3 는 기준 0.20 과 같은 0.20 이었다.
- **키 쌍 순위(N, 키 쌍 있는 6장면, 순위/장면 쌍 수):** 06 573/614 · 10 67/1210 · 19 184/240 · 24 73/348 · 30 312/799 · 38 88/156. 1위는 0 이다.
  N 이 잡은 키 장면 4도 키 쌍 덕분이 아니다.
- **하네스 경로 확인:** `judge_eval --mode learned --works titanic`(`register_corpus` 후 `main` 호출; WEAVE_JUDGE_BACKEND=hf, MODEL_DIR=N, THRESHOLD=0.9326, cuda)로 돌렸다.
  - 결과는 Recall 11/24 [0.279–0.649] · 오탐 3/24 [0.043–0.310] 로, 놓친 13장면 목록까지 `score_pairs` 와 같다.
  - 유형별: 상태 3/8 · 설정 3/8 · 지식 5/8. 동결 LLM 열은 타이타닉에 없다(`--compare-recorded` 대상 아님).

### 보조 지표 — 쌍둥이 대응 (사전 확정, 관문 불변) [측정]

- `chunk_map.json` 의 고친 조각(`contains_edit`)을 썼다. 장면마다 정확히 1개이고, 모두 공유되지 않는다.
- 그 조각의 사건 id 에서 나온 가설 쌍만 모아 p(모순) max 를 냈다. 24사건 모두 P·N 양쪽에 쌍이 있다.
- d 평균 구간은 사건 단위 bootstrap 2,000(시드 0), 부호 검정은 양측 이항(같음 제외)이다.

| 모델 · 범위 | d 중앙값 · 평균 | P>N · 같음 · P<N | 부호 검정 p | **평균 d 95%** | 장면 max 쌍둥이 P>N · 같음 · P<N |
|---|---|---|---|---|---|
| **N · 24사건** | 0.010 · 0.031 | 13 · 0 · 11 | 0.84 | **[−0.020, +0.097]** | 13 · 1 · 10 |
| E1 v3 · 24사건 | 0.011 · 0.030 | 15 · 1 · 8 | 0.21 | [−0.004, +0.068] | 15 · 3 · 6 |
| N · 조건부 10사건 | −0.006 · 0.044 | 5 · 0 · 5 | 1.00 | [−0.057, +0.188] | 5 · 1 · 4 |
| E1 v3 · 조건부 10사건 | 0.072 · 0.065 | 8 · 0 · 2 | 0.11 | [+0.011, +0.119] | 7 · 1 · 2 |

**사전 읽는 법:** N 의 d 구간이 0 을 포함하므로 **"모순 자체를 못 본다" → 다음 수는 모델·데이터**다(집계가 아니다).

### 보조 분석 — 추출 조건부 10사건 (사전 확정 8da0fcf, 관문 아님) [측정]

- 사건: EVENT_048·049·050·051·057·060·061·062·063·071(`conditional_events.json`, SEAL 대조), 장면 20 = P 10 · N 10.

| 모델 | 장면 AUROC [95%] | recall · 오탐 @기준값 |
|---|---|---|
| **N** | **0.685 [0.435, 0.900]** | 5/10 · 1/10 |
| E1 v3 | 0.695 [0.420, 0.920] | 0/10 · 0/10 |
| E1 v2 | 0.390 [0.140, 0.650] | 2/10 · 3/10 |

- N − E1 v3 (같은 부분집합, 대응): −0.010 [−0.295, +0.250].
- **알려진 비대칭:** LLM judge 는 사건 원문 전체를 받는다. 학습 judge 는 160자 발췌와 assumed_states 가설만 받는다(진단 2b 3건은 LLM judge 에만 보인다). 이번에는 LLM judge 를 돌리지 않았다.

### 읽는 법 [추정]

- 학습형 judge 의 사전 확정 최종 평가 둘(Q3·타이타닉)이 모두 **"없음"**이다. Q3 는 0.590 [0.420, 0.748], 타이타닉은 0.636 [0.471, 0.785] 이다.
- 타이타닉 0.636 은 구조 오라클(0.625)과 고친 흔적 기준선(0.624) 수준이다. 그리고 N 의 TP 11 중 7은 키 쌍이 없는 장면이며, 키 쌍은 어디서도 상위에 오르지 않는다.
  그러니 이 신호는 모순 판정이라기보다 **수정이 추출 결과를 흔든 흔적**에 가깝다.
  - 쌍둥이 d 구간이 0 을 포함한다는 점과 맞다.
  - 쌍 PR-AUC 0.0004 = 양성 비율이라는 점과도 맞다.
- 같은 슬롯 쏠림이 Q3 에서는 없었지만 타이타닉에서 다시 나타났다. v3 와 같은 슬롯 음성이 지름길을 없앤 것이 아니라 Q1/Q3 분포에서 눌렀을 뿐일 수 있다.
- 조건부 10사건에서는 N·E1 v3 모두 0.69 근처지만 구간이 넓고(양성 10), N − E1 v3 는 0 근처다.
  E1 v3 만 조건부 d 구간이 0 위인데, 이는 사전 후보가 아니라 결과로 쓰지 않는다.
- 정리: 학습형 judge 트랙은 사전 확정 최종 평가 두 번 모두 "없음"으로 끝났다. 발표 시연은 동결 LLM judge 녹화·재생 경로다(§0-7(c)).

## 타이타닉 LLM judge 1회 (무료 nemotron)

> 2026-11-08 세션 K, 지시문 K-T2. 사전 확정은 `docs/learned-judge-training-design.md` §0-9 "LLM judge 타이타닉 1회"(main 21a580d)다.
> 결과를 본 뒤 아무것도 바꾸지 않았다. 재실행 0회. **타이타닉은 이 1회로 끝이다.** 유료 모델 0회, 프롬프트·`weave_core/` 수정 없음, push·main 병합 없음.
> 꼬리표:
> - **사후 변경:** 원래 규칙은 "학습 judge가 통과할 때만 LLM judge"였는데, 학습 judge 결과를 본 뒤 사용자가 바꿨다(§0-9).
>   LLM judge 설정은 타이타닉에 맞추지 않았다.
> - 모순 작성자는 Sonnet 5.5 다.
> - 추출 recall 은 낙관 값이다(인물 시드).
> - [측정] · [기록] · [추정] · [감사: 작업 세션 판정, 사람 아님].

### 실행 [측정] — `C:\judge_models\T2llm\`

- **입력:** 봉인을 대조했다(SEAL sha256 `5b2f2e0f…` · 113파일 모두 일치).
  - 입력은 봉인된 run2 원장 4화·장면 48·레지스트리(`registry_live.json`)다. 하네스 `load_context` 재생이고, 장면은 5화로 넣어 N−1 = 4화 캐논 시점이다.
  - `WEAVE_DATA_DIR`·녹화 폴더는 T2llm 아래에 두었다. M3 work 상태는 읽지도 쓰지도 않았다. 판정 경로가 원장 파일만 읽기 때문이다.
- **경로:** 하네스 `AdapterLLMJudge` 와 같다.
  - `app.pipeline.weave.run_checks_parallel`(코어 run_checks·프롬프트·aggregate 그대로)에 M3-D 래퍼 `extract_titanic.CachedLLM` 을 붙였다(녹화 우선, `:free` 아니면 거부, 429·한도면 중단).
  - 앞단에 분당 18회 간격과 예산 상한 880호출을 두었다(오늘 M3-D ~97호출).
- **설정(서비스 기본값 그대로):**
  - openrouter `nvidia/nemotron-3-ultra-550b-a55b:free`, reasoning `{"enabled": false}`, effort 미전송, max_tokens 8192
  - judge k 3 · 다수결 2 · unit scene · retrieval full · include_facts false · 동시성 3
- **호출:** 714회(예약 714, 장면 48 · 패킷 238 × k 3). 실패 0, 파싱 재요청 0, 429 0, finish=stop 714.
  - 입력 12.77M · 출력 0.27M 토큰. 2026-11-08 11:51–12:37(46.5분), API 누적 7,895초. 비용 0.
- **패킷 단위(판단 세션 결정 (가)):** 장면 사건에 조각 안쪽 scene_id(`c1:scene_5_1` …)가 남아 있어, 장면 하나가 패킷 1~16개로 나뉜다.
  run 마다 패킷 판정을 OR 하고(양성 판정 하나라도 있으면 그 run 양성) k 3 중 2 이상이면 장면 양성이다.
  **동결 Q3 열은 장면당 패킷 1개였다**(Q3 장면 사건 1개). 이 점이 다르다.

### 결과 [측정]

| | 장면 AUROC [95%, 층화 2,000 시드 0] | recall · 오탐 |
|---|---|---|
| **LLM judge (이진, 하네스 규칙)** | **0.521 [0.375, 0.667]** | **15/24 · 14/24** |
| LLM judge 확신도 max (보조) | 0.511 [0.359, 0.664] | — |
| N (실험 N 타이타닉, 참고) | 0.636 [0.471, 0.785] | 11/24 · 3/24 |

**판정: 0.521 (< 0.65), 하한 0.375 (< 0.5) → "없음".**
- recall 0.625 [0.427, 0.788] · 오탐률 0.583 [0.388, 0.755](Wilson), d = 0.042.
- 48장면 모두 3/3 또는 0/3 으로 판정이 안정했다(양성 29 · 음성 19).

### 함께 보고

- **정답 근거 일치 recall: 0/24** [감사: 작업 세션 판정, 사람 아님]. 하네스에 근거 일치 규칙이 없어 아래처럼 판정했다.
  - **기계 규칙 A:** 양성 판정의 `evidence_event_ids` 가 근거 지도의 원장 사건을 포함하는지 본다. 3/24(scene_10·12·15) [측정].
  - 세 건 모두 같은 사건 id 를 **다른 이유(인물 위치)**로 인용한 것이었다.
  - TP 15장면의 양성 판정 이유를 모두 읽고 심은 사실의 핵심어로 찾아봤다. 심은 문장이나 위반한 사실을 다룬 이유는 하나도 없다.
  - 예: scene_12 의 심은 사실은 머리빗 장식이 비취 나비 → 붉은 장미로 바뀐 것인데, 이유는 로벳 위치 불일치였다.
  - `grounding_review.json` 에 장면별 이유 전문을 남겼다.
- **양성 판정 이유의 내용 [측정]:** 양성 판정 153건 중 98%(150)가 잭의 '수갑 채워져 체포됨'(EVENT_4_53 이후 풀린 기록 없음, 58%) 또는 인물 위치가 갱신되지 않은 것(93%)을 근거로 든다.
  [추정] 각본은 인물 이동이 잦고 원장이 이동·해제를 다 담지 못한다. 그래서 LLM judge 는 오래된 원장 상태와 충돌하는 것을 모순으로 본다. 이는 P·N 을 가리지 않는다.
- **패킷 수 점검(오탐 기회) [측정]:**
  - 장면당 패킷: P 평균 5.0 · N 4.92. 쌍둥이 P−N 평균 +0.08(P>N 5 · 같음 13 · P<N 6). 패킷 수만으로 P/N 을 가르는 AUROC 는 0.517 이다.
  - **패킷 수가 LLM 양성 판정을 가르는 AUROC 는 0.819 다.** 정상 장면에서 오탐 14장면의 평균 패킷은 6.29, 참음성 10장면은 3.0 이다.
  - 패킷 단위 다수결 양성: P 장면 29/120, N 장면 23/118 이다.
  - 패킷이 많을수록 오래된 상태와 부딪칠 기회가 늘어, run 단위 OR 집계에서 양성이 된다.
- **N 과의 대응 비교(같은 48장면):**
  - LLM 이진 − N = −0.116 [−0.311, +0.096]. 확신도 max − N = −0.125 [−0.339, +0.109].
  - 잡은 장면 겹침: 둘 다 7(10·12·19·24·39·41·42), LLM 만 8(02·06·08·09·14·15·16·30), N 만 4(11·25·38·46).
  - 오탐 겹침은 2(31·40)다. LLM 오탐 14, N 오탐 3.
- **추출 조건부 10사건(장면 20):** 이진 0.600 [0.400, 0.800], 7/10 · 5/10. 확신도 0.595. LLM − N −0.085 [−0.375, +0.220].
- **추론 장면 제외(048·056·062·063·067):**
  - P·N 둘 다 빼면(19/19) 0.526 [0.368, 0.684], 13/19 · 12/19.
  - P 만 빼면(19/24) 0.550 [0.409, 0.696].
- **유형별 recall:** 상태 6/8 · 설정 6/8 · 지식 3/8. 근거 일치로는 모두 0 이다.
- **알려진 비대칭:** LLM judge 는 사건 원문 전체·관련 과거 사건·원장을 본다. N 은 160자 발췌와 assumed_states 가설만 본다.

### 읽는 법 [추정]

- 서비스 judge(동결 LLM 경로)는 사람이 쓴 각본(타이타닉)에서 **장면 판별력이 없다(0.521).** 양성 판정도 심은 모순을 가리키지 않는다(근거 일치 0/24).
- recall 15/24 는 정답을 맞힌 것이 아니라 원장 상태 갱신 누락에 반응한 결과다. 오탐 14/24 도 같은 원인이다.
- Q3(Claude 집필, 장면 사건 1개)에서 동결 열이 낸 3/20 · 1/25 와 성격이 다르다. 각본형 원문에서는 추출된 원장의 **시간 갱신(이동·해제)** 이 judge 보다 앞선 병목이다.
- 학습 judge 결과(실험 N 타이타닉 "없음")는 바꾸지 않는다. 이 값은 "서비스 judge의 사람 각본 성능"으로만 쓴다.

## 개선 P — judge 앞 단계 (학습 judge 측정, LLM judge 대기)

> 2026-11-08 세션 K, 지시문 K-P. 사전 확정은 `docs/learned-judge-training-design.md` §0-9 "원인 조사"·"judge 앞 단계 개선 P"(main 18f5b20)다.
> **이 절의 모든 값은 개발 값이다(타이타닉 재사용, 사후 설계).** 타이타닉은 이미 두 judge 에 썼고, 개선안은 타이타닉 진단을 보고 정했다. 일반화 주장에 쓰지 않는다.
> 지킨 것: `weave_core/` 무수정, 유료 0, 재학습·재추출 0, 봉인 파일 무수정(읽기만), push·main 병합 없음.
> 꼬리표: [측정] · [기록] · [추정].

### 구현 [기록]

- **새 코드:** `app/pipeline/prejudge.py`, `scripts/eval/prejudge_eval.py`, `tests/test_prejudge.py`.
- **설정:** `WEAVE_PREJUDGE_F1|F2|F3`(기본 꺼짐)를 넣었다.
- **어댑터 연결:**
  - F2 는 추출 조각 병합 끝에 붙였다.
  - F1·F3 는 `run_checks_parallel` 의 기존 패치 자리(코어 `run_checks` 호출을 잠그는 곳)에서 `weave_pipeline.build_judge_prompt` 를 잠깐 감싸는 방식이다.
  - 학습 judge 는 `judge_group(snapshots=…)` 로 받는다.
- **E0 순차 맥락(평가):** 사건 e 의 P·N 장면을 모두 같은 맥락에서 본다.
  - 맥락 = 1–4화 원장 + 사건 048…e−1 의 N 장면(봉인 run2)을 순서대로 이어 붙인 5화. 현재 장면은 6화로 넣는다(048 은 앞 사건이 없어 5화).
  - 장면 파일마다 같은 `EVENT_5_n` 충돌은 ID 를 다시 매겨 풀었다(앞 장면은 누적 번호, 현재 장면은 `EVENT_6_n`). 행에는 `event_id_orig` 를 남겼다.
  - 맥락 목록은 `C:\judge_models\P_prejudge\pairs_*.meta.json` 의 `contexts` 에 있다.
  - **확인:** 앞 사건이 없는 048 의 두 장면(38·28)은 (B) 쌍이 원 내보내기와 행 단위로 같다.
- **F1 화 안 증분 원장:** 원장 사본에 같은 화의 앞 사건을 `ledger.apply_event` 로 적용한다. 학습 judge 쌍은 `build_pairs` 를 사건마다 그 시점 원장으로 돌려 그 사건의 쌍만 모은다. 쌍 규칙·순서는 그대로다.
  - **해석:** 사전 확정 문구는 "패킷 앞 사건"이지만, 원인 (2)가 "같은 화·같은 패킷 안"이라 **사건 단위**로 했다(패킷 앞 사건 + 패킷 안 앞 사건). 학습 judge 하네스는 장면 파일 하나를 한 패킷으로 보므로, 패킷 단위라면 F1 이 아무 효과도 없다.
- **F2 추출 후처리(규칙 4개):**
  - ① 구속 계열 body_status → transient. 키워드 상수: `구속, 수갑, 체포, 기절, 실신, 마취, 취함, 잠듦`.
  - ② 목격자(`witness_character_ids`)·주체(`main_actor_character_id`) 중 참여자·명시적 위치 변경 인물이 아닌 사람을 `auto_location_extra` 에 단다. 재생 때 `apply_event_f2` 가 source `auto_witness` 로 위치를 갱신한다. 이 키가 없는 사건에는 원래 `apply_event` 와 같다.
  - ③ location → unknown 은 버린다.
  - ④ `NEW:` 장소는 레지스트리 이름·별칭과 정확히 같을 때(밑줄 = 공백) 그 ID 로 해소한다.
- **F3 프롬프트 후처리:**
  - ① (e)절 "지금까지의 모든 사건이 아래에 있다"를 사실대로 바꿨다.
  - ② 스냅샷 줄 끝에 `[since 이후 N사건 · 출처 자동|명시]` 를 붙였다.
  - ③ `## (f) 판정 지시` 바로 아래에 지침 한 줄을 넣었다(설계 문서 문구 그대로).
- **테스트:** 규칙별 단위 테스트 8개, 어댑터 2개.
  - 플래그를 모두 끄면 판정 프롬프트·추출 결과·검수 결과가 **패치 없는 경로와 바이트 단위로 같다.**
  - 켜면 지침·경과 태그가 프롬프트에 들어간다.
  - 전체 suite(`TEST_DB_NAME=glitch_noai_test_labelfix`)는 595 passed, 1 skipped, 3 xfailed 다.

### F2 적용 수 [측정] (정규화 뒤, 문서마다 한 번)

| 규칙 | 1–4화 원장 | 장면 48 |
|---|---|---|
| ① 구속 → transient | 1 (EVENT_4_53 수갑) | 9 |
| ② 목격자·주체 위치 자동 갱신(인물 수) | 41 | 47 |
| ③ location→unknown 버림 | 3 | 6 |
| ④ NEW: 장소 해소 | 0 | 0 |

④가 0 인 이유: 남은 `NEW:` 장소 76건 중 레지스트리 이름과 정확히 같은 것이 없다(예: `NEW:보트_갑판_로비`).

### 프롬프트 앞뒤 비교 [측정] — scene_41(EVENT_053), 패킷 `c1:scene_5_4`, E0 맥락

(B) → (P). 다른 줄만, 발췌:
```
-    location = NEW:치안_책임자_사무실 (since EVENT_5_21, certainty explicit)
-    body_status = 수갑 착용, 손 피부 헐음 (since EVENT_5_21, certainty explicit)
+    location = NEW:치안_책임자_사무실 (since EVENT_5_21, certainty explicit) [since 이후 4사건 · 출처 자동]
+    body_status = 수갑 착용, 손 피부 헐음 (since EVENT_5_21, certainty explicit) [일시적, 해소 사건 미기록 — 해소 시 None] [since 이후 4사건 · 출처 명시]
-    location = LOC_044 (앤드루스 앞) (since EVENT_5_22, certainty explicit)
+    location = LOC_025 (since EVENT_6_3, certainty explicit) [since 이후 0사건 · 출처 명시]
-검색으로 선별하지 않았다. 지금까지의 모든 사건이 아래에 있다.
+검색으로 선별하지 않았다. 아래는 확정된 이전 회차의 사건이다. 같은 회차에서 이 사건보다 앞선 사건은 이 목록에 없다. 원장과 이 목록은 추출된 변화만 담으므로, 기록되지 않은 이동·해제 같은 변화가 있을 수 있다.
+변화 기록이 없다는 것만으로 모순이라 판정하지 않는다. 일시 상태·오래된 위치는 참고로만 쓴다.
```
- 줄별 출처: 수갑 줄의 transient 표시는 F2, 위치 갱신(`EVENT_6_3`)은 F1, 경과·출처 태그와 (e)절·지침은 F3 이다.
- 이 장면의 10패킷 합계: F1 스냅샷 10, 태그 단 스냅샷 줄 400, (e)절 10, 지침 10.
- `해소 시 None` 은 코어 `_snapshot_lines` 가 decays_to 없이도 그대로 찍는 표기다(코어 무수정이라 남김).
- 비교 전문: `prompt_diff_scene_41.json`.

### 학습 judge N (v3 · T 1.29 · 기준값 0.9326 · max, 조건마다 1회) [측정, 개발 값]

**쌍:**

| 조건 | 쌍 | 모순 · 정상 · 무관 · 제외 |
|---|---|---|
| 원 결과(E0 없음, 참고) | 22,828 | 8 · 872 · 20,115 · 1,833 |
| (B) 현행 + E0 | 23,145 | 6 · 915 · 20,656 · 1,568 |
| (P) E0 + F1–F3 | 23,170 | 6 · 918 · 20,673 · 1,573 |

**판정 지표:**

| 조건 | **장면 AUROC [95%]** | recall · 오탐 | R/24 · 오라클 | 조건부 10사건 | 추론 장면 제외 |
|---|---|---|---|---|---|
| 원 결과(참고) | 0.636 [0.471, 0.785] | 11/24 · 3/24 | 6 · 0.625 | 0.685 | 0.544 |
| **(B)** | **0.604 [0.434, 0.762]** | 12/24 · 7/24 | 6 · 0.625 [0.542, 0.708] | 0.600 [0.33, 0.83] | 0.565 |
| **(P)** | **0.601 [0.432, 0.759]** | 12/24 · 7/24 | 6 · 0.625 [0.542, 0.708] | 0.590 [0.33, 0.82] | 0.560 |

**쌍둥이 d·같은 슬롯:**

| 조건 | 쌍둥이 d(24) 평균 [95%] · P>N/같음/P<N | 같은 슬롯 (a) max · (c) 기준 |
|---|---|---|
| 원 결과(참고) | 0.031 [−0.020, +0.097] · 13/0/11 | 0.417 · 0.115 |
| **(B)** | 0.034 [−0.008, +0.097] · 12/0/12 | 0.375 · 0.121 |
| **(P)** | 0.026 [−0.022, +0.092] · 11/0/13 | 0.375 · 0.121 |

- **대응 차이:** (P) − (B) = −0.004 [−0.016, 0.000]. (B) − 원 결과 = −0.032 [−0.163, +0.090].
- **잡은 장면:** (B)·(P) 모두 같다.
  - TP: 10·12·15·19·23·24·25·38·39·41·42·46
  - 오탐: 04·05·26·31·32·40·47
- **키 쌍 순위(P):** 06 572/618 · 10 741/1244 · 19 183/240 · 24 79/360 · 30 326/803 · 38 87/156. 1위는 0 이다. (B)도 거의 같다.
- **쌍둥이 조건부 10사건:** (B) [−0.025, +0.188], (P) [−0.056, +0.179].
- 같은 슬롯은 E0 맥락별 원장으로 분류했다(진단 T 정의, v2 글).

### LLM judge — 실행하지 않음(판단 세션 결정 대기) [측정·추정]

- **예상 호출:** 서비스 설정(k 3, 2/3, unit scene, retrieval full)에서 패킷은 238 이다. F1–F3 은 패킷 묶음을 바꾸지 않으므로 **조건마다 714호출, 두 조건 1,428호출**이다.
  조건부 10사건(장면 20)만 돌리면 조건마다 318호출이다.
- **남은 무료 한도(오늘, UTC 2026-11-08):** 1,000 − M3-D 약 97 − T2 714 = **약 189** [추정: 97은 판단 세션 수치].
  한 조건도 오늘 안에는 다 못 돈다. UTC 자정(한국 09:00)에 한도가 다시 차면 하루 한 조건(714)이다.
  T2 실측 속도(분당 18 간격, 실패 0)로 조건마다 약 47분 [추정].

### 읽는 법 [추정, 개발 값]

- 학습 judge 에서는 개선 P 가 거의 효과가 없다((P) − (B) ≈ 0, 같은 장면).
  N 의 병목은 낡은 상태가 아니라 쌍 판별이다. 키 쌍이 위로 오르지 못하는 것은 E0·F1·F2 뒤에도 그대로다.
  F3 는 프롬프트 전용이라 학습 judge 에 닿지 않는다.
- E0 는 오탐을 3 → 7 로 늘렸다. 맥락에 앞 장면이 들어오면 정상 장면의 max 쌍이 늘어난 전제(5화)와 겹친다 [추정].
- 낡은 상태가 98% 를 차지한 쪽은 LLM judge 다. 그래서 개선 P 의 주 비교는 사전 확정대로 **LLM (P) − (B) 오탐 수와 정답 근거 일치 recall** 이다. 호출 예산이 정해지면 돌린다.

## 개선 P2 — 코어 점검 결함 보정 (학습 judge·서비스 화면 지표, LLM judge 대기)

> 2026-11-08 세션 K, 지시문 K-P2. 사전 확정: `docs/learned-judge-training-design.md` §0-9 "코어 점검"·"개선 P2"(main 7dc3a51). 근거: 공유 노트 `core-audit.md`.
> **모든 값은 개발 값이다(타이타닉 재사용, 사후 설계).** 지킨 것: `weave_core/` 무수정, 유료·LLM 호출 0, 재학습·재추출 0, 봉인 파일 읽기만, push·병합 없음.
> 판단 세션이 개선 P 의 F1 사건 단위 해석을 승인했다. P2 에서는 F1 경계가 F5 묶음을 따른다.
> 꼬리표: [측정] · [기록] · [추정].

### 구현 [기록]

모두 `WEAVE_PREJUDGE_*` 설정 플래그이고 기본은 꺼짐이다. 코드는 `app/pipeline/prejudge.py` 에 있고, `weave.py` 에서 연결한다.

| 항목 | 근거 | 무엇을 | 어디서 |
|---|---|---|---|
| F2⑤ `F2_5` | H1 | field=location 인 assumed_state 를 버린다 | 추출 병합 끝(F2 자리) |
| F4 `F4` | H2 | 경계 인식 정규화(라벨 뒤 글자가 `[가-힣A-Za-z0-9_]` 면 치환 안 함) 1회. 그 뒤 코어 `run_checks` 에는 `mapping={}` | `_norm`(confirmed_episodes·episode_to_delta·검수), `run_checks_parallel` |
| F5 `F5` | H3 | 같은 화에서 연속이고 location_id 가 같은 사건을 최대 6개까지 한 패킷으로 묶는다. 사본의 scene_id 를 `F5_<k>` 로 바꾼다(`c{n}:` 접두 제거). unknown·없음은 혼자 묶음. F1 경계도 이 묶음 | `run_checks_parallel`(코어 `scene_groups` 앞) |
| F6 `F6` | M1 | memory·dream·vision 사건은 재생과 F1 에서 state_changes·위치 자동 갱신(목격자 포함)을 뺀다. knowledge 는 둔다. 과거 사건 목록에는 그대로 둔다 | `replay_confirmed` 입력, `Snapshots(view=)` |
| F7 `F7` | M4·F3 보정 | 스냅샷 블록의 ID 뒤에 `(이름)` 을 붙인다(레지스트리 이름). decays_to 없는 transient 문구를 `[일시 상태(지속 여부 불명)]` 로 바꾼다 | 판정 프롬프트 후처리 |
| F8 `F8` | M2·M6 | 비actual 사건의 structural_*·possesses_removed 를 '참고'(needs_judge, 검수 요망)로 내린다. 그러면 서비스 화면에 0.9 로 뜨지 않는다 | `run_checks_parallel` 결과 |
| F9 `F9` | M5① | 같은 화에서 그 묶음 앞의 사건을 (e)절 끝에 붙인다. F3 가 켜져 있으면 (e)절 문구도 "목록 끝에 따로 붙였다"로 바꾼다 | 판정 프롬프트 후처리 |

- **학습 judge 쌍(평가):** F2⑤·F4·F6·F1(F5 경계) 이 쌍에 닿는다.
  F5 가 켜지면 서비스 학습 judge 처럼 묶음마다 쌍 그룹을 만든다. 장면 점수는 그 max 다. F7–F9 는 LLM 프롬프트 전용이다.
- **테스트:** 규칙별 6개를 더해 `tests/test_prejudge.py` 는 16개다.
  - 모든 플래그를 끄면 판정 프롬프트·추출·검수 결과가 패치 없는 경로와 바이트 단위로 같다. 이 테스트는 P2 함수가 불리면 실패한다.
  - (B)·(P) 쌍 내보내기를 P2 코드로 다시 만들어도 이전 파일과 바이트 단위로 같다.
  - 전체 suite: 601 passed, 1 skipped, 3 xfailed.

### 규칙별 적용 수 [측정] (정규화 뒤, 문서마다 한 번)

| 규칙 | 1–4화 원장 | 장면 48 |
|---|---|---|
| F2⑤ location 전제 버림 | 11 | 22 |
| F4 코어 정규화와 결과가 다른 문자열 칸(깨짐 방지) | 115 | 66 |
| F6 재생 제외 사건(그중 state_change) | 4 (0) | 10 (7) |
| F2 ①–④ (개선 P 와 같음) | 1 · 41 · 3 · 0 | 9 · 47 · 6 · 0 |

- **서비스 경로(E0 맥락, 장면 48, 패킷 프롬프트 1벌):** F8 내림 structural 3 · possesses_removed 2. F9 로 붙인 앞 사건 줄은 사건 프롬프트 252개에 걸쳐 714건.
- 코어 점검의 135곳은 서비스 매핑(`mapping_of(reg) | label_map`) 기준이다. 여기 F4 수는 하네스 매핑(레지스트리 new_label) 기준이라 다르다(L6).

### 학습 judge N (v3 · T 1.29 · 기준값 0.9326 · max, 1회) [측정, 개발 값]

| 조건 | 쌍(모순·정상·무관·제외) | **장면 AUROC [95%]** | recall · 오탐 | 쌍둥이 d(24) 평균 [95%] | 같은 슬롯 (a)·(c) | 조건부 10 | 추론 제외 |
|---|---|---|---|---|---|---|---|
| (B) 현행+E0 | 23,145 (6·915·20,656·1,568) | 0.604 [0.434, 0.762] | 12/24 · 7/24 | 0.034 [−0.008, +0.097] | 0.375 · 0.121 | 0.600 | 0.565 |
| (P) E0+F1–F3 | 23,170 | 0.601 [0.432, 0.759] | 12/24 · 7/24 | 0.026 [−0.022, +0.092] | 0.375 · 0.121 | 0.590 | 0.560 |
| **(P2) E0+F1–F9** | 21,845 (6·961·19,372·1,506) | **0.591 [0.422, 0.750]** | **11/24 · 7/24** | 0.020 [−0.032, +0.087] | 0.375 · 0.128 | 0.535 [0.26, 0.785] | 0.553 |

- **대응 차이:** (P2) − (B) = −0.013 [−0.047, +0.014].
- **잡은 장면:** (B) TP 에서 scene_10 이 빠졌고, 오탐 7 은 (B) 와 같은 장면이다.
- **구조 지표:** R 6/24, 오라클 0.625 로 변하지 않았다.
- **키 쌍 순위(P2):** 06 479/525 · 10 539/1015 · 19 182/240 · 24 75/360 · 30 267/709 · 38 84/151. 1위는 0 이다.

### 서비스 화면 지표 — checker 강한 모순 (E0 맥락, 서비스 경로 호출 0) [측정, 개발 값]

| 조건 | 강한 모순 수 정상 장면 · 모순 장면 | 강한 모순이 있는 장면 정상 · 모순 | 강한 모순 유형 |
|---|---|---|---|
| (B) | **15 · 15** | 10/24 · 8/24 | assumed_state 22 · structural_alive 4 · possesses_removed 2 · structural_location 2 |
| **(P2)** | **1 · 3** | 1/24 · 2/24 | structural_alive 2 · assumed_state 1 · structural_location 1 |

- 약한 의심은 두 조건 모두 0 이다.
- 강한 모순은 서비스 화면에 신뢰도 0.9 로 뜨는 checker 항목이다. 장면 AUROC 로는 보이지 않는다(코어 점검 H1·M2·M6).
- 이 표는 정상 장면 오탐뿐 아니라 모순 장면의 강한 모순도 함께 센다. 그것이 심은 모순을 가리키는지는 따로 보지 않았다.

### 패킷·호출·비용 (LLM judge 실행 안 함) [측정·추정]

| 조건 | 패킷 (P · N 장면 평균) | 호출(k 3) | 입력 글자(1벌) | 추정 입력 토큰 | 추정 출력 토큰 | 유료 같은 모델 비용 [추정] |
|---|---|---|---|---|---|---|
| (B) | 238 (5.00 · 4.92) | 714 | 10.48M | 16.9M | 0.27M | **$7.25** |
| **(P2)** | **201** (4.25 · 4.12) | **603** | 10.93M | 17.7M | 0.23M | **$7.47** |

- **토큰 환산:** T2 녹화본 714호출의 실제 입력 토큰 ÷ 프롬프트 글자 = 0.5385 토큰/글자 [측정]. 출력은 T2 평균 379 토큰/호출이다.
  가격은 DeepInfra $0.40/M 입력 · $1.76/M 출력(판단 세션 지시값)이다. T2 실측 토큰을 같은 가격으로 환산하면 $5.58 이다.
- E0 는 앞 장면 사건을 (e)절에 넣어 프롬프트가 T2 보다 길다(호출당 약 4.4만 글자).
  P2 는 패킷이 16% 적지만, 이름·앞 사건·경과 태그로 패킷당 글이 길어져 비용은 비슷하다.
- **F5 가 238 → 201 에 그친 이유:** 각본 사건은 위치가 자주 바뀐다. 연속·같은 위치 조건으로는 크게 묶이지 않는다.

### 프롬프트 발췌 (P2) — scene_41, 패킷 `F5_4`
```
- CHAR_046(잭 도슨):
    body_status = 수갑 착용, 손 피부 헐음 (since EVENT_5_21, certainty explicit) [일시 상태(지속 여부 불명)] [since 이후 4사건 · 출처 명시]
…
- (같은 회차의 앞선 사건 — 원장 스냅샷에 반영된 범위)
- EVENT_6_1 (6화, reality_status actual)
    로즈, 엘리베이터 강제 탑승: …
```
(B)에서는 같은 장면이 9패킷 `c1:scene_5_1…c2:scene_5_2` 였고 (P2)에서도 9패킷 `F5_1…F5_9` 다. 프롬프트 전문은 `C:\judge_models\P_prejudge\prompts_{B,P2}_scene_41.json` 에 있다.

### 읽는 법 [추정, 개발 값]

- **학습 judge:** P2 도 거의 바꾸지 못했다((P2) − (B) −0.013). 병목은 앞 단계가 아니라 쌍 판별이라는 P 결과 ①과 같다.
- **서비스 화면:** checker 강한 모순이 정상 장면에서 15 → 1, 모순 장면에서 15 → 3 으로 줄었다.
  줄어든 몫은 대부분 H1(자유 서술 location 전제)이다. 사용자 화면에 가장 먼저 보이던 오탐 경로를 막았다는 뜻이다.
  다만 강한 모순이 줄어든 만큼, 모순 장면에서 checker 가 정답으로 잡던 것도 함께 사라졌는지는 근거 대조 전까지 모른다.
- **LLM judge:** (B)·(P2) 의 주 비교(오탐 수·정답 근거 일치 recall·낡은 상태 근거 비율)는 호출 방식이 정해지면 각 1회 돌린다.
  - 무료: 조건마다 하루, 각 약 40–47분.
  - 유료: 두 조건 합계 약 $15 [추정].

## 학습 judge v2 — 코드·모델 준비 (보고 ①)

> 2026-11-08 세션 K, 지시문 V2-K. 사전 확정은 `docs/learned-judge-training-design.md` §10 "학습 judge v2"(main acaf169)다.
> 지킨 것: `C:\judge_models\V2\eval\` 열지 않음, `weave_core/` 무수정, 유료 0, push·병합 없음, 내려받기 0(모든 가중치·KLUE 데이터는 로컬 캐시).
> 학습·선택은 PD-train 감사 통과 뒤 판단 세션 신호를 기다린다. 꼬리표: [측정] · [기록] · [추정].

### 코드 [기록]

- **`app/pipeline/judge_v2/`** — 서비스에 붙일 수 있는 부분이다(Weave 추출과 독립). 버전 상수는 `TEXT_VERSION`·`RETRIEVE_VERSION`·`RENDER_VERSION` 이며 모두 "1" 이다.
  - **`text`:** 문장 분리. 규칙:
    - 문단(줄) 경계는 항상 문장 경계다.
    - 따옴표 안의 종결 부호에서는 끊지 않는다.
    - 대사 뒤에 서술이 이어지면 한 문장이다(`“어디 가?” 하고 물었다.`). 대사 다음이 문단 끝이거나 새 대사면 대사 하나가 한 문장이다.
    - 말줄임표(…)는 문장 안 쉼으로 본다. 대사 끝에서는 종결로 본다.
    - 글자 2자 미만 조각은 앞 문장에 붙인다.
  - **`chunk_prefix`:** 앞선 원문을 문단 경계로 약 300자 조각으로 나눈다. 450자를 넘는 문단은 문장 경계로 나눈다.
  - **`retrieve`:** 문자 2-gram(한글·영숫자만) BM25(k1 1.2, b 0.75) + 인명 겹침 가산(이름당 1.5, 최대 2개).
    - 인명은 앞선 원문에서 "2–4글자+조사" 꼴로 3번 이상 나온 말이다(불용어 제외, 근사).
    - 상위 k=4, 점수 > 0, 동점은 앞 조각이다.
  - **`render`:** 문장 쌍 `[CLS] [주장] 문장 [SEP] [맥락] 조각1 … 조각k [SEP]`, 512토큰. **입력 순서는 설계 문서에 적힌 대로 주장이 첫째 칸이다(`CLAIM_FIRST`).**
    - 주장은 160토큰까지다.
    - 맥락은 점수 순으로 남은 예산을 받는다. 다 안 들어가는 조각은 32토큰 이상 남을 때만 앞부분을 넣고 거기서 멈춘다.
    - 표시는 앞선 원문 안의 위치 순이다.
  - **`scene`:** 대상 구간 문장 전부 × 검색 맥락으로 만든다. 장면 점수 = 문장 p(모순) max.
- **`scripts/v2/`:**
  - **`train_v2.py`:**
    - 1단계 `--stage pairs`: 3분류 NLI 헤드에서 이진 로짓 `l_모순 − logsumexp(나머지)` 에 BCE 를 건다. dev 작품 행 BCE 로 조기 종료한다.
    - 2단계 `--stage mil`: S 쌍둥이 → 장면. 문장 로짓을 no_grad 로 낸 뒤 argmax 문장만 grad 로 다시 계산해 장면 BCE 를 건다(max 의 기울기와 같고 메모리만 작다). dev 장면 BCE 로 조기 종료한다.
    - 끝나면 dev S 로 온도, 기준값('정상 오탐 ≤ 10% 에서 recall 최대'), 장면 AUROC 를 `v2_meta.json` 에 남긴다. 기존 폴더는 덮어쓰지 않는다.
  - **`score_v2.py`:** 장면 AUROC + 층화 bootstrap(2,000, 시드 0), recall·오탐 @기준값, 쌍둥이 d(평균·중앙값·부호·bootstrap 구간), 근거 일치(1위 문장이 심은 문장인가 — 포함 관계 또는 2-gram Dice ≥ 0.5), 유형별 recall.
    - 영 학습 3분류 모델(E1·E1L)도 같은 읽기(softmax[모순])로 채점한다.
  - **`make_d.py`:** D 실제 검색 음성 생성기.
  - **`v2_common.py`:** 공통 함수.
- **`scripts/eval/nli_dev_acc.py`:** NLI dev 정확도(KLUE-NLI validation, KorNLI fiction 검증 10%).
- **테스트:** `tests/test_judge_v2.py` 13개.
  - 문장 분리, 조각, 검색(결정성·인명 가산), 렌더러 절단 규칙, 장면, 기준값·온도, 근거 일치, D 규칙.
  - **학습 스모크(작은 BERT, CPU):** 1단계 → MIL → 채점. 앱 venv 에 torch 가 없어 suite 에서는 건너뛰고, `.venv-train` 에서 따로 통과시켰다.
- **GPU 확인 [측정]:** E1L(large)·512토큰·배치 8·누적 2 로 1단계 80행이 6.6초다. 6,000행이면 1에폭 약 8분이다 [추정]. VRAM 부족은 없었다.

### 모델 E1L [측정]

**E1L = 실험 E4 모델 그대로다.**
- E4 는 E1 레시피(`_nli` = KLUE-NLI 2에폭 → KorNLI fiction `pairs_ext_kornli`, 10% 검증, 2에폭 최선 1)에서 기초 모델만 klue/roberta-large 로 바꾼 것이다. 지시문의 후보 (b)와 같은 방법이라 다시 학습하지 않고 `C:\judge_models\E1L\` 로 복사했다(새 폴더, sha256 c0a69daa…, E4 와 같음).
- 다시 학습하면 약 1시간에 같은 레시피의 모델이 하나 더 나온다.

| 모델 | KLUE-NLI validation (3,000) | KorNLI fiction 검증 10% (7,732) |
|---|---|---|
| E1 (base, 후보 a) | 0.748 | 0.778 |
| **E1L (large, 후보 b)** | **0.783** | **0.785** |
| 참고: large `_nli`(KorNLI 전) | 0.887 | 0.671 |

- KorNLI fiction 적합이 KLUE-NLI 를 깎는다(0.887 → 0.783). 그 대신 소설 문체 KorNLI 를 올린다(0.671 → 0.785).
- 토큰 상한은 각 모델 meta 값이다(E1·E1L 128, `_nli` 384). 입력 순서는 (premise, hypothesis)다.

### D 실제 검색 음성 [측정]

- **원문·분할:** V2-T 의 `source\` 와 `split.json`(28작품: 학습 24 · dev 4 — w03 술 권하는 사회, w12 자기를 찾기 전, w18 논이야기, w27 권태)을 썼다. split 에 없는 원문 8편은 건너뛰었다.
- **고정 규칙:**
  - 앞부분 3,000자 이상인 지점부터 1,500자 간격으로 대상 구간(문단 경계)을 잡는다.
  - 그 구간의 원문 문장(글자 10자 이상, 검색 조각 1개 이상)마다 앞 원문 전체에서 검색한 맥락을 붙인다. 라벨은 무모순이다.
  - 시드 `V2:D:20261108` 로 섞고, 작품당 상한 300(15%)을 둔다.
- **처음 결과(정리 전 원문, 아래 "D 다시 만들기"로 대체 — 지금은 `gen\D.old.jsonl`):** 2,000행(학습 1,687 · dev 313), 후보 7,136.
  - 행마다 맥락 평균 3.96조각이다. 주장 길이 중앙값은 33자다.
  - 주장↔맥락(이어 붙인 것) 2-gram Jaccard 중앙값은 0.015 다.
  - 작품당 16–300행이다(w20 이 상한).

### D 다시 만들기 — 정리된 원문 (판단 세션 지시) [측정]

- **처음 D:** 위 표의 2,000행은 정리 전 원문(장 제목 `== … ==`·장식 `🙝🙟` 포함, '지주회시' 포함)과 28작품 split 으로 만든 것이다.
  - 찌꺼기가 들어간 행이 467개였다(V2-T 확인).
  - 맥락 조각이 문단 줄바꿈을 지녀, 공백 한 칸으로 이은 A/B/C 와 글 모양이 달랐다.
  - `gen\D.old.jsonl` 로 옮겼고 학습에는 쓰지 않는다.
- **줄바꿈 차이 처리:** E1·E1L 토크나이저는 줄바꿈과 공백에 같은 ID 를 낸다 [측정]. 그래서 모델 입력에서는 원래 지름길이 아니다.
  그래도 출처별 글 모양을 맞추려고 두 곳을 고쳤다.
  - 렌더러에 공백 접기(`clean`)를 넣었다. 토큰 ID 는 바뀌지 않는다.
  - D 의 주장·맥락 글을 공백 하나로 저장한다(`MAKE_D_VERSION` 2).
  - D.meta 에 검색 상수(K·BM25·인명 가산)를 적었다.
  - 원문 해시는 V2-T 규칙(LF 로 맞춘 글의 sha256)으로 적었고, `source_meta.json` 과 35/35 일치한다.
- **새 D:** `gen\D.jsonl`, 정리된 원문 35편, 갱신 split(학습 30 · dev 5: w03·w12·w18·w27·w32), 같은 규칙·시드.
  - 2,000행(학습 1,729 · dev 271), 후보 9,616. 행이 있는 작품은 34/35 다. w29 피아노는 2,543자라 앞부분 3,000자 규칙에 못 미친다.
  - 작가별: 채만식 815 · 나도향 660 · 이상 321 · 현진건 204. 작품당 2–300행(중앙값 38.5)이다.
  - dev 작품: w03 39 · w12 87 · w18 63 · w27 44 · w32 38.
  - 맥락 조각 수: 4개 1,966 · 3개 17 · 2개 11 · 1개 6. 찌꺼기 0행, 줄바꿈 0행.
  - 다시 돌려도 바이트 단위로 같다.
- **라벨별 겹침(주장 ↔ 이어 붙인 맥락, 문자 2-gram Jaccard 중앙값)** — V2-T 의 현재 A/B/C 108행과 같은 함수로 쟀다:

| 유형 · 라벨 | 행 | 중앙값 |
|---|---|---|
| D · 무모순 | 2,000 | 0.0160 |
| A · 모순 / 무모순 | 27 / 27 | 0.0172 / 0.0193 |
| B · 모순 / 무모순 | 22 / 22 | 0.0216 / 0.0210 |
| C · 모순 / 무모순 | 5 / 5 | 0.0143 / 0.0162 |
| **라벨 전체: 무모순 / 모순** | 2,054 / 54 | **0.0161 / 0.0182** (차 0.0021 ≤ 0.05) |

  A/B/C 는 생성 중간값(108행)이라, 최종 점검은 V2-T 가 전체 행으로 다시 한다.

### 입력 순서 결정 반영 (판단 세션, main dd50160) [기록·측정]

- **결정:** 맥락이 앞(전제), 주장이 뒤(가설)다. 토크나이저 쌍 입력에 `truncation="only_first"` 를 써서 맥락만 자른다.
- **렌더러 `RENDER_VERSION` 2:**
  - 첫째 칸 `[맥락] 조각1 … 조각k`(앞선 원문 안 위치 순), 둘째 칸 `[주장] 문장`, 512토큰.
  - 주장은 160토큰을 넘으면 그 길이로 줄인다. 점수 순 조각 배정 규칙(버전 1)은 없앴다. 검색 점수 순위(`priority`·`context_priority`)는 기록용으로만 남긴다.
  - D 데이터는 글만 담고 있어 다시 만들 필요가 없다.
- **검증:**
  - 실제 BERT 토크나이저 테스트에서 주장은 둘째 칸에 통째로 들어가고, 넘치면 맥락 끝만 잘린다.
  - 학습 스모크(1단계 → MIL → 채점)도 `.venv-train` 에서 다시 통과했다.
- **절단 비율 [측정, E1L 토크나이저]:** 4조각(약 1,200자)은 대개 512토큰을 넘는다.
  - 운수 좋은 날 표본 28문장 중 25문장이 잘린다.
  - D 앞 400행 중 381행(95%)이 잘린다(평균 508토큰).
  - 위치 순 표시와 only_first 가 만나면 **가장 뒤(대상 구간에 가장 가까운) 조각의 끝부분이 주로 잘린다.**

- **판단 세션 확인:** 이 절단은 사전 확정 규칙(k ≤ 4, 512, 맥락만 자름) 안이므로 그대로 둔다.
  **실효 k 는 약 3 이다** — D dev 271행에서 통째로 들어간 조각이 평균 2.87, 일부라도 들어간 조각이 3.79 다(분포 4개 9 · 3개 218 · 2개 43 · 1개 1, 잘린 행 257).

### 영 학습 기준선 — D dev 오탐률 (v2 형식, 학습 없음) [측정]

- **입력:** `scripts/v2/score_rows_v2.py`, D dev 271행(dev 5작품, 전부 무모순), v2 렌더러(맥락 첫째 칸, only_first, 512).
- **읽기:** p(모순) = softmax[모순], 온도 1(v2 meta 없음).
- 무모순 행뿐이라 오탐률만 낸다. 기준값은 아직 없다(dev S 로 학습 뒤 정한다). 그래서 고정값 0.5·0.7·0.9 와 3분류 argmax 를 함께 적는다.

| 모델 | p(모순) 평균 · 중앙값 · p90 · p99 | 오탐 @0.5 | @0.7 | @0.9 | argmax = 모순 |
|---|---|---|---|---|---|
| E1 (base) | 0.331 · 0.318 · 0.618 · 0.806 | **22.1%** | 4.4% | 0.0% | 35.4% |
| E1L (large) | 0.239 · 0.180 · 0.474 · 0.956 | **9.2%** | 5.2% | 1.5% | 14.4% |

- 결과 파일: `C:\judge_models\V2\models\zeroshot_D_dev_{E1,E1L}.json`(작품별 오탐률 포함).
- D 는 원문 무모순 가정(잡음 감수)이라, 이 값은 실제 검색 맥락에서 영 학습 모델이 "모순"으로 기우는 정도의 기준선이다 [추정].
- E1L 이 E1 보다 덜 기운다. 다만 꼬리(p99 0.956)는 더 두껍다.

### 확인해 줄 것 [기록]

- **입력 순서(해결됨, 위 절):** 설계 문서 순서대로 **주장이 첫째 칸, 맥락이 둘째 칸**이다. E1·E1L 은 (premise=맥락, hypothesis=주장) 순서로 학습됐으므로, 영 학습 기준선과 초기화 이점에는 반대 순서가 맞을 수 있다.
  학습 전(신호 전)에 판단 세션이 정하면 상수 하나(`CLAIM_FIRST`)로 바꾼다.

### 시범 학습(코드 점검) — 지시문 V2-K2 [측정, **선택에 쓰지 않음**]

- **데이터:** `pd_train.jsonl` A·B·C(학습 2,584행) + D 학습 1,729행(조각화 바꾸기 전 D v2), 합계 4,313행. dev 352행(pd_train dev)이다.
- **설정:** 1단계 이진, 1 epoch, 시드 2, 512토큰, 맥락 먼저, 배치 8 · 누적 2, fp16 autocast. 산출물은 `C:\judge_models\V2\pilot\` 에 있다.

| 모델 | 1 epoch | GPU 최대 | NaN/inf | 저장↔불러오기 로짓 차 | 매개변수 변화 L2 (분류 헤드) |
|---|---|---|---|---|---|
| E1 | 114.5초 | 3,595 MiB | 0/540 | 0.0 | 0.982 (0.076) |
| E1L | 332.0초 | 10,012 MiB | 0/540 | 0.0 | 1.652 (0.157) |

- E1L 은 배치 8 · 누적 2 그대로 VRAM 12GB 안에 들어갔다(축소 안 함).
- **손실 곡선**(25배치 평균, 학습 BCE): E1 0.603 → 0.46 근처, E1L 0.576 → 0.46 근처다. dev 쌍 BCE 는 E1 0.702 · E1L 0.720 이다.
- **MIL 경로(가짜 S 5쌍, E1 1단계 결과에서):** 장면 max → BCE → 역전파 → 저장이 정상이다. NaN 0, 매개변수 변화 0.148, 2.2초.
  - 가짜 S 는 B 쌍(같은 맥락, c+/c−)을 앞부분 + 대상 구간 쌍둥이로 흉내 낸 것이다. A 쌍은 맥락이 달라 S 형식(같은 앞부분)에 맞지 않는다.
- **점검 지표(dev 623행 = pd_train dev + D dev, @0.5, 선택에 쓰지 않음):**
  - E1: 정확도 A 0.53 · B 0.53 · C 0.55 · D 0.99, 쌍 성질(모순 행 p > 무모순 행 p) A 0.79 · B 0.76 · C 0.87.
  - E1L: 정확도 A 0.49 · B 0.50 · C 0.45 · D 0.99, 쌍 성질 A 0.52 · B 0.47 · C 0.42.
  - D 는 맞히고 A·B·C 는 우연 수준이다. **출처(조각 수) 지름길을 배운 모양**이다. 감사가 예측한 지름길과 같고, 아래 조립 v2 의 맥락 채우기가 겨냥하는 것이다.
- **서비스 연결 뼈대(기본 꺼짐):**
  - 설정 `JUDGE_V2_ENABLED=false`·`JUDGE_V2_MODEL_DIR`·`JUDGE_V2_THRESHOLD`·`JUDGE_V2_TEMPERATURE`·`JUDGE_V2_DEVICE`·`JUDGE_V2_MAX_ITEMS`.
  - 판정 모듈은 `app/pipeline/judge_v2/service.py`(`JudgeV2`, 점수기 주입)와 `hf.py`(HF 점수기, torch 지연 import)다. 공장 `get_judge_v2()` 는 꺼져 있으면 None 이고 모델 모듈도 읽지 않는다.
  - 워커 `run_review` 는 켜져 있을 때만 저장된 앞 회차(회차 번호 < N, saved) 원문으로 판정을 더한다. `AnalysisPipeline` 인터페이스는 그대로다.
  - 결과는 `ContradictionOut` 으로 낸다. span 은 문장 위치, conflict_ref 는 검색 1위 구간의 회차·앞부분, confidence 는 p(모순), explanation 은 `[judge_v2]` 로 시작한다(source 칸이 없어 글머리로 남긴다).
  - **테스트:** 꺼짐에서는 v2 경로가 불리지 않고 저장된 모순이 파이프라인 출력과 같은 필드 값이다. 켜짐에서는 가짜 점수기로 v2 항목이 덧붙고 근거 회차가 1화다.
  - 실제 HF 점수기도 시범 E1 모델로 `JudgeV2.review` 를 통과했다(`.venv-train`).

### PD-train 조립 v2 — 지시문 V2-K3 (설계 문서 §10 "데이터 규칙 수정", main 6dd9557) [측정]

- **도구:** `scripts/v2/build_v2.py`. 원본 `pd_train.jsonl` 은 그대로 두고 `pd_train_v2.jsonl` 을 새로 쓴다.
  - **판정 걸러내기(2)는 L-audit 의 `filter_judgments.jsonl` 이 나오면 `--judgments` 로 적용한다.** 지금 파일은 적용 전 미리보기 `pd_train_v2.pre.jsonl` 이다.
- **조각화 통일(혼합 점검 실패에 대한 조치):**
  - 처음 조립에서 혼합 점검이 **맥락 길이 단일 특징 AUROC 0.851 로 실패**했다.
  - 원인: A·B 맥락은 생성 조각화(L-gen `make_chunks`, 220–450자, 조각 중앙값 423자)이고, D 맥락은 v2 `chunk_prefix`(약 300자, 중앙값 273자)였다. 출처가 곧 라벨이라 길이가 라벨을 가른다.
  - 조치: v2 `chunk_prefix` 를 생성 조각화와 글자 하나까지 같게 바꿨다(`TEXT_VERSION` 2). 조립 도구는 모든 행의 맥락이 이 조각화와 글자 그대로 맞는지 대조한다(전부 일치).
  - D 를 같은 조각화로 다시 만들었다(`make_d` 3, 2,000행 · 학습 1,728 · dev 272, 이전 D 는 `gen\D.v2.jsonl`).
- **(1) A− 기계 걸러내기(어절 단위 diff, 사이 같은 어절 1개 이하는 한 영역):** A 688쌍 중 **322쌍이 남는다.**
  - 사유(중복 가능): (i) 겹침 0 — 307, (iii) 편집 2곳 이상 — 93, (ii) 원래 값 재등장 — 15.
  - 처음 글자 단위 diff 는 숫자·낱말 치환을 글자 하나로 쪼개 (i)·(iii) 을 부풀렸다(남은 205쌍). 그래서 어절 단위로 바꿨다.
  - (i) 은 규칙대로 적용했지만, 의미로만 충돌하는 편집도 버린다(예: "볕에 그을린 험상궂은 얼굴" ↔ 편집 "백지장같이 희멀건"). 규칙 조정은 판단 세션 몫이다.
- **(3) C:** 205쌍 전부 출력에서 뺐다(C− 폐기, C+ 는 g′ 재생성 때까지 보류).
- **(4) 맥락 채우기:**
  - 같은 BM25+인명 검색(D 와 같은 규칙)으로 주장 조각 앞의 원문 조각에서 채운다. 쌍의 두 행에 같은 채움·같은 순서를 쓴다.
  - A− 쌍은 원래 f 값이 든 조각을 뺀다(96조각 제외). 핵심 조각 자리는 시드 `V2:fill:20261108` 고정 무작위다.
  - 결과 조각 수: 4개 849 · 3개 40 · 2개 6 · 1개 2(쌍 기준).
- **(5) 혼합 점검(D 3,794행 섞음, 모순 897행, 방향 무관 AUROC):**

| 특징 | AUROC | 방향 무관 |
|---|---|---|
| 맥락 길이 | 0.473 | 0.527 |
| 조각 수 | 0.488 | 0.512 |
| 줄바꿈 | 0.500 | 0.500 |
| 겹침(주장↔맥락 2-gram Jaccard) | 0.523 | 0.523 |

  최악 0.527 로 목표(≤ 0.60) 달성, gate pass 다(판정 걸러내기 전 값).
- **미리보기 수량(판정 걸러내기 전):** A 322쌍 · B 575쌍 · C 0 → 1,794행.
- **조각화 변경의 부수 효과 [측정]:** 조각이 커져(약 420자) 4조각이 512토큰을 크게 넘는다.
  - D v3 dev 272행에서 통째로 든 조각은 평균 1.66, 일부라도 든 조각은 2.62 다(잘린 행 270). **실효 k 는 약 2 로 줄었다(이전 약 3).**
  - 같은 D v3 dev 의 영 학습 오탐 @0.5 는 E1 30.2% · E1L 11.4% 다(이전 D: 22.1% · 9.2%).

- **상태(2026-11-08, 사용자 지시로 일시 정지):** 판정 걸러내기(2) 적용·최종 pd_train_v2.jsonl·본 학습은 하지 않았다. 재개 신호를 기다린다.

### 걸러내기 적용 → pd_train_v2.jsonl (지시문 V2-K3 재개, 판정 결과 main c6063dc) [측정]

- **입력:** `pd_train.jsonl`(1,468쌍), `filter_judgments.jsonl`(1,263행: A− 충돌 251 · 근거 없음 426 · 일치 11, B− 충돌 181 · 근거 없음 394), `gen\D.jsonl`.
  - D 는 지시문의 2e83b91 본이 아니라 **조각화를 맞춘 D v3(99d5912)** 다. v2 본은 혼합 길이 AUROC 0.851 로 실패했다.
  - 원본 두 파일은 바꾸지 않았다. 해시는 매니페스트(`pd_train_v2.meta.json`)에 적었다.
- **순서:** 판정 걸러내기 → A− 기계 걸러내기 → C 제외 → 맥락 채우기 k=4(시드 `V2:fill:20261108`).
- **판정 걸러내기:** 충돌인 쌍만 남긴다. **A 251 · B 181 쌍으로 기대와 같다**(`--expect` 대조).
- **A− 기계 걸러내기:** 251쌍 중 119쌍이 빠지고 **132쌍이 남는다.** 사유(중복 가능): (i) 겹침 0 — 90, (iii) 편집 2곳 이상 — 36, (ii) 원래 값 재등장 — 5.
- **C:** 205쌍 모두 뺐다. `--c-v2` 는 자리만 있다(넣으면 멈춘다).
- **결과:** **A 132쌍(학습 121 · dev 11) · B 181쌍(학습 156 · dev 25)**, 626행.
  - 작가별(모순 쌍): A 현진건 23 · 나도향 32 · 채만식 37 · 이상 40, B 34 · 50 · 58 · 39.
  - 채움 뒤 조각 수(쌍): 4개 296 · 3개 12 · 2개 3 · 1개 2. A− 쌍에서 원래 f 값이 든 채움 후보 68조각을 뺐다.
- **쌍 성질:** 위반 0 이다. A 두 행 주장은 바이트까지 같고, B 두 행 맥락은 채움 뒤에도 같고, 쌍마다 채움 조각·자리가 같다.
- **혼합 점검(D 학습 1,728행 섞음, 방향 무관 단일 특징 AUROC):**

| 묶음 | 행 · 모순 | 맥락 길이 | 조각 수 | **주장 길이** | 줄바꿈 | 겹침 |
|---|---|---|---|---|---|---|
| 전체 + D | 2,354 · 313 | 0.543 | 0.514 | **0.621** | 0.500 | 0.587 |
| A | 264 · 132 | 0.500 | 0.500 | 0.500 | 0.500 | 0.550 |
| A + D | 1,992 · 132 | 0.555 | 0.505 | **0.647** | 0.500 | **0.633** |
| B | 362 · 181 | 0.500 | 0.500 | 0.503 | 0.500 | 0.544 |
| B + D | 2,090 · 181 | 0.541 | 0.523 | **0.620** | 0.500 | 0.573 |

  - **최대 0.647(A + D, 주장 길이)** 이다. 멈춤 기준(0.70) 아래라 gate 는 pass 지만, **목표(≤ 0.60)는 주장 길이(세 묶음)와 A + D 겹침에서 못 맞췄다.**
  - 원인: 주장 길이 중앙값이 A 49 · B 44 자인데 D 는 35 자다. D 는 원문 문장 전부(10자 이상)에서 뽑았고, A·B 주장은 생성 규칙(12–220자)으로 고른 문장이다 [측정].
  - 유형 안(A, B 단독)은 모두 0.55 이하다. 쌍 구조라 주장·맥락 모양이 라벨과 무관하다.
- 학습은 하지 않았다(S 없음, §10).

### D 층화 재표본 — 주장 길이·겹침 맞춤 (판단 세션 지시) [측정]

- **승인 기록:** D v3 사용을 판단 세션이 승인했다.
  - v3 sha256 은 `fa83cdb0284a3bf69c78c3e03a460b592e3f5202e3768a88d781398302dda55e`(`gen\D.v3.jsonl` 로 보존)다.
  - 2e83b91 본을 쓰지 않은 이유: 조각화가 달라 맥락 길이 AUROC 가 0.851 이었다.
  - A− 기계 걸러내기는 사전 확정대로 두고, A 132쌍(< 200)은 남은 작품분으로 보충한다.
- **방법:** `make_d --match pd_train_v2.jsonl`. D v3 후보 9,617개 전부에서 A·B 626행의 (주장 길이 5분위 × 겹침 5분위) 25칸 비율대로 2,000행을 나눠, 시드 `V2:D:strata:20261108` 로 뽑는다.
  - 작품당 상한(15%)과 생성 규칙은 그대로다.
  - 겹침 정의는 혼합 점검과 같은 함수 `v2_common.claim_overlap`(주장 ↔ 이어 붙인 맥락 2-gram Jaccard)다.
  - 경계: 주장 길이 31 · 41 · 52 · 68자, 겹침 0.0094 · 0.0133 · 0.0168 · 0.0221.
- **결과:** **1,999행(학습 1,740 · dev 259).** 모자란 칸은 하나다(짧은 주장 × 겹침 최상위: 할당 3, 후보 2, 1행 부족).
- **혼합 점검 다시(같은 방법, D 학습 1,740행, 방향 무관 AUROC):**

| 묶음 | 행 · 모순 | 맥락 길이 | 조각 수 | 주장 길이 | 줄바꿈 | 겹침 |
|---|---|---|---|---|---|---|
| All + D | 2,366 · 313 | 0.531 | 0.518 | **0.503** | 0.500 | 0.522 |
| A | 264 · 132 | 0.500 | 0.500 | 0.500 | 0.500 | 0.550 |
| A + D | 2,004 · 132 | 0.541 | 0.509 | **0.528** | 0.500 | **0.520** |
| B | 362 · 181 | 0.500 | 0.500 | 0.503 | 0.500 | 0.544 |
| B + D | 2,102 · 181 | 0.528 | 0.527 | **0.515** | 0.500 | 0.548 |

  **최대 0.550(A 단독, 겹침) 으로 모든 특징이 목표(≤ 0.60) 안이다.** 주장 길이 0.647 → 0.528, A + D 겹침 0.633 → 0.520.
  - A 단독 겹침은 쌍 구조 안의 값이라 D 와 무관하다.
  - 이전 값은 `pd_train_v2.meta.json` 의 `mix_check_history` 에 남겼다.
- **모순 : 무모순:** 학습 277 : 2,005(1:7.24) → 277 : 2,017(1:7.28), dev 36 : 308 → 36 : 295. 무모순 = A·B 무모순 행 + D.
- 학습은 하지 않았다.

### 최종 재조립 — A·B 1·2차 + C_v2 + S (지시문 V2-K4, main 2b70dbb) [측정]

- **입력:**
  - `pd_train.jsonl`(A 818 · B 679 · C 234쌍; C v1 은 쓰지 않음)
  - 판정 `filter_judgments.jsonl`(1차) + `filter_judgments_r2.jsonl`(2차: A− 충돌 24 · B− 충돌 41)
  - `gen\C_v2.jsonl`(96쌍) + `filter_judgments_c.jsonl`(C− 충돌 31)
  - `pd_train_S.jsonl`(179쌍), D 층화본(4052d52)
  - 해시는 매니페스트 `pd_train_v2.meta.json` 에 있다. 이전 K3 결과는 `pd_train_v2.k3.jsonl`·`.k3.meta.json` 으로 남겼다.
- **A·B:** 1·2차 판정을 합쳐 충돌만 남겼다(같은 id 판정 충돌 0). A− 기계 걸러내기는 같은 코드로 1·2차 모두에 적용했다.
  - A: 판정 뒤 275쌍 → 기계 걸러내기 119쌍 빠짐 → **156쌍**. 2차 대상 밖(기계 탈락 61)은 판정이 없어 빠진다.
  - B: **222쌍**. **둘 다 기대와 같다**(`--expect-kept A=156,B=222`).
- **C_v2:** C− 충돌 **31쌍**. A·B 와 같은 채움(k=4, 쌍 공유, 시드 자리)이고, 원래 g 가 든 조각은 채움에서 뺐다. `c_relaxed` 는 3쌍이다. C+ 맥락은 모두 원문 조각과 글자 그대로 일치한다.
- **결과 `pd_train_v2.jsonl`(818행):**

| 유형 | 쌍 | 학습 · dev | 작가(현진건 · 나도향 · 채만식 · 이상) |
|---|---|---|---|
| A | 156 | 141 · 15 | 23 · 37 · 56 · 40 |
| B | 222 | 183 · 39 | 34 · 69 · 80 · 39 |
| C | 31 | 21 · 10 | 4 · 10 · 12 · 5 |

  채움 뒤 조각 수(쌍): 4개 391 · 3개 13 · 2개 3 · 1개 2.
- **쌍 성질:** 위반 0 이다(409쌍). A 주장은 바이트까지 같고, B 맥락은 채움 뒤에도 같다. C 는 주장이 같고 맥락이 g ↔ g′ 구간만 다르다. 모든 쌍의 채움 조각·자리가 같다.
- **작가 분할 누수:** 0 이다. 모든 행이 split.json 학습 작가 작품이고 split 이 일치하며, 평가 작가 이름은 없다.
- **혼합 점검(D 층화본 학습 1,740행 섞음, 방향 무관 AUROC):**

| 묶음 | 행 · 모순 | 맥락 길이 | 조각 수 | 주장 길이 | 줄바꿈 | 겹침 |
|---|---|---|---|---|---|---|
| All + D | 2,558 · 409 | 0.519 | 0.513 | 0.517 | 0.500 | 0.507 |
| A | 312 · 156 | 0.500 | 0.500 | 0.500 | 0.500 | 0.550 |
| A + D | 2,052 · 156 | 0.528 | 0.507 | **0.566** | 0.500 | 0.543 |
| B | 444 · 222 | 0.500 | 0.500 | 0.502 | 0.500 | 0.544 |
| B + D | 2,184 · 222 | 0.519 | 0.521 | 0.509 | 0.500 | 0.536 |
| C | 62 · 31 | 0.501 | 0.500 | 0.500 | 0.500 | 0.515 |
| C + D | 1,802 · 31 | 0.502 | 0.510 | 0.507 | 0.500 | 0.503 |

  **최대 0.566(A + D, 주장 길이)** 이라 목표(≤ 0.60) 안이다. D 는 다시 뽑지 않았다.
- **S(MIL, 파일 그대로 `pd_train_S.jsonl`):** 179쌍(학습 153 · dev 26), 형식 이상 0(P ≠ N, 필드·검증 표시 모두 있음).
  - 대상 구간 문장 수: 최소 1 · 중앙 9 · 최대 27, 주장 3,326개. 그중 47개(1.4%)는 검색 조각이 없다.
  - P/N 단일 특징 AUROC(원래 구간 대비): 편집 거리 **0.582** · 길이 차 0.525 · 겹침(대상 ↔ 앞부분) 0.504. 목표(≤ 0.60) 안이다.
  - S 의 `planted` 칸은 심은 문장이 아니라 설명 글이다. 근거 일치(1위 문장 = 심은 문장)를 낼 때는 P/N 차이 문장을 써야 한다(채점 코드 보완 필요).
- 학습은 하지 않았다(재감사 뒤 판단 세션 지시).

---

# 반복 추출 일치도

> 2026-11-06 추가. 코드: `scripts/eval/extract_agreement.py` · `tests/eval/test_extract_agreement.py`.

## 데이터

WM 30장면 × 4벌. 원본(`weave_checker_corpus/scene_NN_event.json`) + Weave Track 2 격리 재추출 3벌
(`weave_judge_corpus/wm/reextract/scene_NN_event_v2_r{1,2,3}.json`: claude-opus 서브에이전트, 추출 프롬프트 v2_1,
장면마다 1회 단독 호출). **원본은 재추출과 같은 조건이 아니다** — Weave Phase 4 진단대로 원본 추출은 심어 둔 모순을
조준해 항목이 적다(assumed_states 슬롯 28개 대 재추출 174~182개). 그래서 "재추출끼리"(같은 조건 반복)와
"원본 대 재추출"(조건 차이 + 반복)을 나눠 적는다. 서비스 조건(nemotron · 프롬프트 v2_3)의 반복 일치도가 아니다.

## 정의

- 장면 단위 집합: 사건 분할이 벌마다 달라도 비교되게 장면의 모든 사건 항목을 합친다.
- 단위: assumed_states 슬롯 (entity, field) · 값 (entity, field, assumes) / state_changes 슬롯 (entity, field) ·
  도착값 (entity, field, to) · 전이 (entity, field, from, to).
- 정규화: NEW: 라벨 → WM 레지스트리 정식 ID(코어 `ledger.normalize`), 값은 공백 정리·소문자, alive는 코어 `norm_alive`.
  자유 서술 값은 **글자 그대로** 비교한다(값 일치는 하한).
- 쌍 일치도 J(A,B) = Σ장면 |A∩B| / Σ장면 |A∪B|. 묶음 = 그 쌍들의 평균 [최소–최대].
- 만장일치율 = r1~r3 중 하나라도 낸 단위 가운데 셋 모두 낸 비율.
- 값 유사도(서술용, 문턱 없음) = 두 벌에 모두 있는 슬롯에서 값마다 상대 벌 같은 슬롯 값과의 문자 2-gram Dice 최댓값의 평균(양방향).

## 결과

`python scripts/eval/extract_agreement.py` 출력 그대로:

```
| 단위 | 재추출끼리 J 평균 [최소–최대] | 원본 대 재추출 J 평균 [최소–최대] | 재추출 만장일치율 (3벌 모두 / 1벌 이상) |
|---|---|---|---|
| assumed_states 슬롯 (entity, field) | 0.777 [0.754–0.791] | 0.147 [0.141–0.154] | 0.684 (145/212) |
| assumed_states 값 (entity, field, 값) | 0.013 [0.012–0.015] | 0.000 [0.000–0.000] | 0.003 (3/871) |
| state_changes 슬롯 (entity, field) | 0.567 [0.562–0.575] | 0.049 [0.044–0.054] | 0.419 (36/86) |
| state_changes 도착값 (entity, field, to) | 0.248 [0.233–0.258] | 0.025 [0.024–0.026] | 0.135 (22/163) |
| state_changes 전이 (entity, field, from, to) | 0.229 [0.201–0.244] | 0.025 [0.023–0.026] | 0.118 (20/169) |

assumed_states 값 유사도(같은 슬롯 안 문자 2-gram Dice 최대 쌍 평균, 문턱 없음): 재추출끼리 0.439 · 원본 대 재추출 0.275

| field | o / r1 / r2 / r3 항목 수 | 슬롯 J (rr) | 값 J (rr) | 값 J (o-r) | 값 유사도 (rr) |
|---|---|---|---|---|---|
| ability | 6 / 61 / 60 / 58 | 0.697 | 0.011 | 0.000 | 0.376 |
| body_status | 8 / 63 / 54 / 55 | 0.721 | 0.006 | 0.000 | 0.407 |
| condition | 1 / 3 / 3 / 2 | 1.000 | 0.000 | 0.000 | 0.316 |
| knows | 9 / 150 / 145 / 138 | 0.859 | 0.014 | 0.000 | 0.449 |
| possesses | 4 / 32 / 33 / 34 | 0.844 | 0.026 | 0.000 | 0.562 |
```

읽는 법:

- **무엇을 적을지(슬롯)는 꽤 안정적이다**: 같은 조건 반복에서 assumed_states 슬롯 J 0.78, 셋 모두 같은 슬롯 68%.
  state_changes 슬롯은 0.57로 낮다 — 상태 변화를 사건으로 볼지가 벌마다 갈린다.
- **값은 자유 서술이라 글자 일치가 거의 없다**(J 0.01). 표현만 다른 경우가 많아 문자 유사도는 0.44지만, 이것은 일치 판정이 아니다.
  값을 비교하는 판정기(LLM judge든 학습 judge든)는 표현 변동을 견뎌야 하고, 규칙 checker가 자유 서술 필드를
  judge로 넘기는(B-002) 이유와 같다.
- 원본 대 재추출은 슬롯 J 0.15 — 원본이 조준 추출이라 항목 수부터 6배 차이 난다. 동결 열 WM 패킷(`judge_requests_v4c`)은
  이 원본 장면 사건으로 만들어졌으므로(Weave `judge_requests_v4c/EVENT_S02_1.txt`의 assumed_states가 원본과 같은 1건)
  동결 열 WM 15/20은 "조준된 추출 위의 판정"이라는 조건부 수치로 읽어야 한다.
- 이 측정은 WM 한 작품, Claude 추출, 프롬프트 v2_1이다. Q1·Q3(nemotron C=2500) 원장의 반복 일치도는 재추출 벌이 없어 재지 못했다.
