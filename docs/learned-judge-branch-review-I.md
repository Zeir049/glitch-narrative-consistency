# I 브랜치 검증 보고 (2026-11-07)

검증 전용 세션의 결과. 코드는 고치지 않았고 이 문서 하나만 만들었다(커밋·push 없음). 테스트는 `TEST_DB_NAME=glitch_noai_test_verify3`로
분리된 DB에서 돌렸다. 앞선 보고서: [learned-judge-branch-review-G.md](learned-judge-branch-review-G.md)(§4a가 이 수정의 출처).

| 항목 | 값 |
|---|---|
| I | `claude/learned-judge-wm-timelines-c9bfa2` — 커밋 1개 `392777d` "fix(judge_eval): 작품별 재생 timelines — WM 은 코어 기본 STATE_TIMELINES" |
| 분기점 | `e227e50`(merge-base 확인. 지시문의 fa9d981이 아니다 — I는 main의 docs 커밋 e227e50 위에서 분기했다). 검증 시점 main은 `33653c6`(docs 커밋 3개 뒤) |
| 환경 | `.venv` Python 3.12.6, torch·transformers 미설치(설치하지 않았다). 다른 세션 idle 확인 뒤 전체 suite 1회 |

## 판정 요약

**합쳐도 된다 — 조건 하나.** main(`33653c6`)에 충돌 없이 합쳐지고(§4), `tests/eval` + `tests/test_learned_judge.py` 113개와 전체 suite가 통과한다.
동결 열 recorded 재현(18/60·3/60)과 가짜 백엔드 게이트 상수는 I의 보고대로 **전부 불변**이고, I가 적은 전후 수치(checker 9→31, pair_coverage
18/19/19→17/18/19, 합 70/75→69/75, export 14→49행·7→20장면)는 **모두 독립 재현**됐다(§3). 설계안 §2-3 목표(v2@24 전부 해석 ≥ 66/75)는 69/75로 유지.

조건: `docs/learned-judge-branch-review-G.md`가 main에 **아직 커밋돼 있지 않다**(`git status`에 `??`). I의 코드 주석·docstring·새 테스트·`docs/EVAL.md`가
이 문서 §4a를 근거로 인용하므로, I와 같은 PR이나 그 이전에 커밋해야 인용이 살아 있다(시험 병합 트리에는 그 파일이 없었다).

틀린 항목은 §5의 4건이며 전부 문서·docstring 수준이다(병합을 막지 않는다). **3(c) 원인 판정**: 할당량 밀림이 아니라 **후보 자체가 사라진 것**(§6).

## 1. 범위

`git diff --name-only e227e50..I` 8개 파일(+136 / −16):

| 파일 | 변경 | 허용 목록 |
|---|---|---|
| `scripts/eval/judge_eval.py` | `WorkSpec.timelines`(기본 `("main",)`, WM=`None`) · `timelines_of(work)` · `load_context`가 그 값을 `compile_ledger`에 넘김 | ○ |
| `scripts/eval/export_pairs.py` | 모듈 상수 `TIMELINES` 삭제 → `base_replay`·`export_inject` 둘 다 `JE.timelines_of(work)`; `inject_timeline` docstring(wm 'unknown'→'future_world') | ○ (지시문엔 없지만 "같은 재생을 쓰는지"가 확인 항목이므로 범위 안) |
| `scripts/eval/datagen_common.py` | `replay_upto` → `JE.timelines_of(work)`; docstring | ○ (같음) |
| `scripts/eval/label_pairs.py` | docstring 1줄 | ○ |
| `tests/eval/test_judge_learned_fake_baseline.py` | 주석 3줄(상수 불변) | ○ |
| `tests/eval/test_data_tools.py` | `inject_timeline(wm) == "future_world"` (1줄) | ○ (기존 테스트의 필요한 수정) |
| `tests/eval/test_judge_eval_timelines.py` | 신규 56줄: `timelines_of` 스펙, 작품별 (checker 항목, needs_judge) 수, WM 장면 timeline ⊆ 원장 칸 | ○ |
| `docs/EVAL.md` | "작품별 재생 timelines" 절 + 재측정 메모 36줄 | ○ |

`fa9d981..I`로 보면 `docs/extract-verifier-brief.md`가 더 나오는데 그것은 main의 `e227e50`(I의 분기점) 것이지 I의 변경이 아니다.
코퍼스(`tests/eval/weave_checker_corpus/`·`weave_judge_corpus/`)·`app/`·`weave_core/` 무변경. I 워크트리 작업 트리 깨끗함.

## 2. 구현 확인

### 2-1. timelines 값이 `compile_ledger`에 닿는가

`WORKS["wm"].timelines=None` → `timelines_of("wm")` → `None` → `compile_ledger(timelines=None)` → `Ledger(None)` → `normalize_timelines(None)` =
`STATE_TIMELINES`(`real_present`·`future_world`·`unknown`). 나머지는 `("main",)` → `["main"]`, WORKS 밖(dracula)은 `["main"]`. 실측(§3-2) 원장 칸이 그대로다.

`compile_ledger(` 호출부 전수(I 워크트리, `app/pipeline/weave_core/` 제외):

| 호출부 | timelines | 공유 재생? |
|---|---|---|
| `judge_eval.load_context` | `timelines_of(work)` | 기준 |
| `export_pairs.base_replay` (`--real`·`--inject` 기반) | `JE.timelines_of(work)` | ○ |
| `export_pairs.export_inject` (setup 6화 더한 재생) | `JE.timelines_of(work)` | ○ |
| `datagen_common.replay_upto` (`label_pairs.py`·`counterfactual.py`·`extract_work.py` 공용) | `JE.timelines_of(work)` | ○ |
| **`counterfactual.fact_candidates`** (`counterfactual.py:89`) | **`["main"]` 하드코딩** 그대로, 바로 아래서 `ledger.state.get("main")`을 읽음 | **✗ 자체 재생 경로가 남아 있다** — `timelines_of` docstring은 이 함수도 쓴다고 적었다(§5-1) |
| `tests/eval/checker_eval.py` | 미지정(`base=`) → STATE_TIMELINES | 별도 재생(게이트) |
| `app/pipeline/extract_gate.py:118` · `app/pipeline/weave.py:1185` | 서비스 경로 `["main"]` | 범위 밖 |

`counterfactual.py`의 영향: `--work` 선택지에 `wm`이 있다. WM에 `["main"]`을 주면 WM 사건은 전부 `unknown` 칸으로 가고 `state["main"]`은 비므로
상태 사실 후보가 0개다 — I 이전부터 그랬고(지식·규칙 후보는 timeline 무관) I가 더 나쁘게 만들지는 않았다. `timelines_of`로 바꾸기만 하면
`state["main"]` 읽기도 같이 고쳐야 하므로(칸이 `future_world`·`real_present`가 된다) 후속 과제. 이번 병합의 조건은 아니다.

`inject_timeline(wm)`이 'future_world'가 되는 이유: `base.corpus` 사건 timeline이 원장 칸에 있으면 그 이름, 아니면 `unknown`을 다수결 — 이제 WM 원장에
`future_world` 칸이 있으므로 다수 칸이 그대로 `future_world`. 테스트 `test_export_inject_on_work_replay`가 이를 고정한다 ○.

### 2-2. 체커 게이트(`checker_eval.py`)와 조건 비교

| | 하네스 `load_context("wm")` (I) | 체커 게이트 `checker_eval.run` |
|---|---|---|
| timelines | `None` → STATE_TIMELINES | 미지정 → STATE_TIMELINES — **같다** |
| 화 정규화 | `entity_registry.json`의 `new_label → id` 매핑으로 `normalize` | `compile_ledger(base=)` → `load_episodes` → `load_registry(base)` 매핑으로 `normalize` — **같은 매핑, 같은 함수** |
| 장면 사건 정규화 | `normalize(e, mapping)` 뒤 `check_event` | `check_event` 안에서 같은 mapping으로 normalize — 같다 |
| knows_mode | `accumulate`(서비스 기본) | 생략 → `legacy`(코어 기본) — **다르다** |
| 쓰는 결과 | needs_judge 항목(SEV_INFO·needs_judge=false는 버림) | `conflicts_of` hard/soft 집합 |

즉 게이트와 다른 점은 **knows_mode 하나**다. I의 `load_context` docstring과 `docs/EVAL.md`가 "매핑·knows 둘이 다르다(게이트는 원본 그대로)"라고 적은 것은
매핑 쪽이 틀렸다(§5-2). timeline 조건이 같다는 핵심 주장은 맞다.

### 2-3. "서비스와 같은 조건"인가 (설계 판단 ①의 전제)

서비스 어댑터는 `app/pipeline/weave.py:160`(`"timelines": ["main"]`)·`:1185`(`replay_confirmed(..., timelines=["main"])`)로 **모든 작품을 `["main"]`**으로
재생한다. 따라서 WM=STATE_TIMELINES는 "서비스와 같은 조건"이 아니라 **"그 코퍼스가 추출된 조건(Weave 원본 다중 timeline)과 같은 조건"**이다.
I의 `docs/EVAL.md` 표는 WM의 이유를 "코퍼스가 Weave 원본 다중 timeline으로 추출됨 · 체커 게이트와 같은 조건"으로 적어 **정확**하고, 서비스 일치는 Q1·Q3·셜록
(`["main"]`, 서비스 추출본)에만 해당한다. 설명 세션 노트(`explainer-wm-timeline-options.md`)의 "서비스는 weave_config의 timelines를 씀"은 그 값이 늘 `["main"]`이라는
뜻으로 읽어야 한다. 판단 ①의 논거를 문서에 남길 때는 "서비스 조건"이 아니라 "추출 조건 일치 + 게이트 일치"로 적는 것이 맞다.

## 3. 재측정 재현 (I 워크트리 `392777d`)

### 3-(a) 동결 열 recorded 재현

`tests/eval/test_judge_baseline.py` + `test_judge_learned_fake_baseline.py` + `test_judge_eval_timelines.py` + `test_data_tools.py` + `tests/test_learned_judge.py`:
**69 passed**(20초). recorded 재현은 녹화 판정 파일 재생이라 checker 항목·원장 칸을 쓰지 않는다 — 불변이 구조적으로 보장된다.

### 3-(b) 가짜 백엔드 게이트 상수 = `print_current()`

| 항목 | 상수 | `current()` 출력 |
|---|---|---|
| `BASELINE_TOTAL` | (74, 80, 82, 85) | 같음 |
| `BASELINE_PER_WORK` | wm (15,20,8,10) · q1 (20,20,25,25) · q3 (20,20,25,25) · sherlock (19,20,24,25) | 같음 |
| `BASELINE_PAIRS` | wm 1440 · q1 7142 · q3 5112 · sherlock 6507 | 같음 |
| `BASELINE_FINGERPRINT` | wm `a00db0ca604c806f` · q1 `65f3a1c60bcf05ef` · q3 `e398085567d40c8e` · sherlock `c5a2a5d189f35cb2` | 같음 |
| `BASELINE_DETECTED` | 4작품 집합 | 같음 |

WM checker 전제가 14→49로 바뀌어도 지문이 같은 이유(I의 설명 그대로 확인): 지문은 장면별 (쌍 수, 판정, 확신도, 근거 전제 ID)만 보고, 쌍 수는 가설당 24로 포화라
같고, 가짜 백엔드의 판정·확신도·근거 ID도 같다. 쌍 **구성**은 `test_judge_eval_timelines.py`의 (31, 28)이 고정한다.

### 3-(c) `pair_coverage` WM 전후 — 장면 단위

`scripts/eval/pair_coverage.py` 전수 실행(main=전, I=후)의 diff는 WM 행과 합 행만 바뀌고 Q1·Q3·셜록 행은 동일:

| WM | 전(main) | 후(I) |
|---|---|---|
| 근거 포함 v1@32 / v2@24 / v1@∞ (matched = all) | 18 / 19 / 19 | 17 / 18 / 19 |
| 합 all v2@24 | 70/75 | **69/75** (목표 ≥ 66 유지) · matched 64/69 → 63/69 |
| kind v2@24 checker / ledger / knowledge / past | 14 / 217 / 620 / 343 | 49 / 199 / 605 / 341 |
| kind **v1@∞** ledger | 297 | **227** (상한 없이도 ledger 후보가 줄었다 — §6의 증거) |

장면 단위(스크래치 `scene_probe.py`: I 트리에서 `timelines_of("wm")`만 `["main"]`으로 바꿔 '전'을 만들고 같은 `build_pairs`로 비교):
세 조건 모두 **뒤집힌 장면은 `scene_16` 하나**(v1@32·v2@24에서 전 hit `{EVENT_1_4}` → 후 `{}`; v1@∞는 전후 모두 `{EVENT_1_3, EVENT_1_4}`). 원인은 §6.

Q1·Q3·셜록 원장 digest(`Ledger.to_dict()` sha256) main vs I: `3f407a88…`·`ff2abc16…`·`e9b4ab8e…` **동일**, WM만 `f33bccb5…`→`915f1d33…`. checker 항목(needs_judge)
q1 103(92)·q3 108(100)·sherlock 130(119) 전후 동일 = 새 테스트 `EXPECTED` = review-G §4a 측정값. WM 9(7)→31(28).

`export_pairs --work wm --real` 재현(main vs I): 전체 1,440행 라벨 106/17/335/982 → 111/20/334/975(모순/정상/제외/무관), checker 전제 행 14(2/2/2/8)·7장면 →
49(12/9/5/23)·20장면 — I 커밋 메시지와 숫자 전부 일치. **부수 관찰**: I 트리의 scene_16은 48행 전부 '무관'(모순 행 0) — 양성 장면인데 근거 전제가 할당량 안에 없어
학습 데이터에 모순 쌍이 하나도 없다(전에는 EVENT_1_4 ledger 전제 1행이 모순이었다). §6 끝에 적은 쌍 생성기 과제와 같은 문제.

## 4. 시험 병합

스크래치 워크트리(detach, main `33653c6`)에 `git merge --no-ff I` → 충돌 없음(merge `6493608`, I의 8개 파일만 변경). 그 트리에서:

| 실행 | 결과 |
|---|---|
| `pytest tests/eval tests/test_learned_judge.py` | **113 passed** (22초) |
| 전체 `pytest` (분리 DB, 다른 세션 idle 확인 뒤 1회) | **462 passed, 1 skipped, 3 xfailed** (5분 17초, 실패 0) — I 보고의 462와 같다 |

스크래치 워크트리는 끝나고 지웠다(`git worktree remove` + `prune`). main·I 브랜치는 건드리지 않았다.

## 5. 틀린 항목 (전부 문서·docstring 수준, 병합을 막지 않음)

1. **`judge_eval.timelines_of` docstring**: "재생하는 곳(… · counterfactual.fact_candidates)은 모두 이 함수를 쓴다" — `counterfactual.py:89`는 `["main"]`
   하드코딩이고 `state["main"]`을 직접 읽는다(§2-1). docstring에서 그 항목을 빼거나, counterfactual을 칸 이름에 무관하게 고치는 후속을 따로 둘 것.
2. **`load_context` docstring · `docs/EVAL.md` "체커 게이트와 다른 점은 … 매핑·knows"**: 매핑은 같다(§2-2, 둘 다 `entity_registry.json`의 `new_label→id`로 `normalize`).
   다른 것은 knows_mode(`accumulate` vs `legacy`) 하나. 따라서 "WM checker 항목 31은 게이트의 장면 집합과 같다고 가정하지 않는다"는 결론은 맞지만 이유는 knows_mode다.
3. **`scripts/eval/README.md:37`**: 쌍 풀 설명이 아직 "`timelines=["main"]`"이다(`label_pairs.py` docstring은 고쳤는데 README는 빠졌다).
4. **`docs/EVAL.md`·`WorkSpec` 주석의 근거 문서 `docs/learned-judge-branch-review-G.md`가 main에 미커밋**(`??`). I의 문제가 아니라 병합 순서 문제 — 판정 요약의 조건.

(I 이전부터 있던 것, 참고만) `load_context` docstring 첫 줄의 `build_b_packets.work_resources`는 저장소에 없는 이름이다.

## 6. 3(c) 원인 판정 — scene_16, 19→18

**판정: 할당량 24 안에서 checker 항목이 ledger·knowledge 전제를 밀어낸 것이 아니다. 전에 근거를 맞히던 유일한 전제가 timeline 칸 분리로 후보 목록에서 사라졐다.**

scene_16(지식 모순, 근거 `EVENT_1_3`·`EVENT_1_4` — 둘 다 1화 `real_present`, '플레이어'(CHAR_002)의 현실 세계 사건). 장면 사건 `EVENT_S16_1`은 `future_world`,
참여자 CHAR_006(슐라우)·CHAR_002(알파).

| | 전 (`["main"]`) | 후 (I, STATE_TIMELINES) |
|---|---|---|
| 장면 사건이 읽는 원장 칸 (`pairs._timeline`) | `future_world` ∉ state → **`unknown`** (WM 사건 전부가 여기 섞여 있다) | **`future_world`** |
| 그 칸의 CHAR_002 | location·body_status·ability·**possesses {OBJ_002, OBJ_003, OBJ_008}** | location·body_status·ability·possesses {OBJ_008} — OBJ_002·OBJ_003(since **EVENT_1_4**)은 `real_present` 칸에 있다 |
| 사건 가설(field=None)의 ledger 전제 6개 | **`EVENT_1_4`**(플레이어.possesses=오류_게임) 포함 → 근거 hit | EVENT_1_4 전제가 **후보에 없음** |
| 그 가설의 kind 구성(v2@24, 24개) | checker 1 · ledger 6 · knowledge 7 · past 6 · rule 4 | checker 3 · ledger 6 · knowledge 6 · past 5 · rule 4 |
| 다른 가설(CHAR_006.knows) | checker 1 · knowledge 13 · past 6 · rule 4 — 근거 없음 | 같음 — 근거 없음 |
| scene_16 checker 항목 | 1 (knows no_info) | 3 (+alive·location transient_state) |
| v1@∞에서 근거에 닿는 경로 | ledger(EVENT_1_4) · knowledge(EVENT_1_4) · past(EVENT_1_4, EVENT_1_3) | knowledge(EVENT_1_4) · past(EVENT_1_4, EVENT_1_3) — ledger 경로만 사라짐 |

- checker 항목 1→3은 knowledge 1칸·past 1칸을 가져갔지만, 전에도 knowledge 7·past 6 안에 EVENT_1_3/1_4는 없었다(관련도 정렬에서 뒤). knowledge·past 후보는
  timeline과 무관(인물별 knowledge·corpus)하고 가설 머리도 같으므로 순서가 전후 동일하다 → checker 밀림으로는 설명되지 않는다.
- 결정적 증거는 **v1@∞ ledger kind 297→227**: 상한이 없는데도 WM ledger 후보 수 자체가 줄었다. `_ledger_premises`가 사건 timeline 칸만 읽으므로 `future_world`
  장면 사건은 `real_present` 칸의 상태(현실 세계의 플레이어)를 더는 보지 못한다.
- 전의 hit는 **우연**이었다: 라벨은 "현실 신상(목요일 공강 대학생)을 미래 세계 인물이 안다"는 지식 위반인데, 맞힌 전제는 "플레이어가 '오류' 게임을 소유(since EVENT_1_4)"라는
  possesses 행 — ID만 같고 내용은 근거가 아니다. I의 커밋 메시지 판독("EVENT_1_4가 우연히 24개 안에 들었다")이 맞다.
- 따라서 18이 **더 정직한 값**이다. 설계안 §2-3 목표(≥ 66/75)는 69/75로 유지되고, G0 관문에 영향 없다.
- 남는 과제(I도 적음, 병합과 무관): timeline을 넘는 지식 근거(현실 세계 사실을 미래 세계 인물이 앎)는 ledger 경로로는 원래 닿을 수 없고 knowledge·past 관련도 순위에
  달려 있다. 이것은 쌍 생성기(`pairs.py`) 관련도·할당량 문제이며 설계안 원칙("할당량이 아니라 관련도 함수를 고친다, 학습 작품에서만")에 따라 별도로 다룬다.

## 참고: 이 세션이 만든 임시 파일(저장소 밖, 스크래치패드)

`scene_probe.py`(장면 단위 전후 비교) · `digest.py`(원장 digest·checker 수) · `pc_main_before.md`·`pc_I_after.md`(pair_coverage 전후) · `xp_main`·`xp_I`(export_pairs WM real
JSONL) · `I_gate_tests.txt`·`merge_eval_tests.txt`·`full_suite_merge_I.txt`(테스트 로그). 스크래치 워크트리 `merge_I`는 지웠다(`git worktree list`에 없음).
