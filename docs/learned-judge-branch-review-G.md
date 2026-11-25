# G 브랜치 검증 보고 (2026-11-07)

검증 전용 세션의 결과. 코드는 고치지 않았고 이 문서 하나만 만들었다(커밋·push 없음). 테스트는 `TEST_DB_NAME=glitch_noai_test_verify`로
분리된 DB에서 돌렸다. 앞선 보고서: [learned-judge-branch-review-DF.md](learned-judge-branch-review-DF.md).

| 항목 | 값 |
|---|---|
| G | `claude/learned-judge-train-v2-6c1d23` — `bedd5db`(테스트 DB 이름 환경변수) · `f1baa84`(train_judge 개정 + `train_lib.py`) · `978440b`(export_pairs 재생 일치·제외 라벨) · `c7eb73d`(label_pairs 후속: §E-12 1~5) |
| 분기점 | `c52ae3d` (merge-base 확인). 검증 시점의 main은 한 커밋 뒤 `bbf2c13`(docs만) — 시험 병합은 그 위에 했다 |
| 환경 | `.venv` Python 3.12, torch·transformers 미설치(학습 루프는 코드 읽기만) |

## 판정 요약

**합쳐도 된다 — 조건 없음.** main(`bbf2c13`)에 충돌 없이 합쳐지고, `tests/eval` + `tests/test_learned_judge.py` 107개와 전체 suite가
통과한다(§5). 설계안과 대조한 로직(접기 분할·제외·가중치·무관 감축·장면 기준값)은 전부 맞다. 틀린 항목은 §6의 4건이며 모두 문서·메타
수준이다(병합을 막지 않음). **4(a) 영향 판정**: WM 장면의 timeline(`future_world`) 때문에 하네스의 WM checker 항목이 9개뿐인 것은
사실이다. 동결 열 재현(recorded)과 learned 가짜 게이트에는 **영향 없음**(수치 동일), `pair_coverage` WM만 **1장면 차이**(§4a).

## 1. 범위

`git diff --stat c52ae3d..G` 11개 파일(+1,426 / −249):
`docs/EVAL.md`(3줄: TEST_DB_NAME 안내) · `scripts/eval/README.md` · `datagen_common.py` · `export_pairs.py` · `label_pairs.py` ·
`train_lib.py`(신규 444줄) · `scripts/train_judge.py` · `tests/conftest.py` · `tests/eval/test_data_tools.py` · `tests/eval/test_train_lib.py`(신규) ·
**`tests/test_learned_judge.py`**(요청 목록에 없던 파일 — `export_pairs --base`→`--work` 변경과 `pick_threshold` 제거에 따른 기존 테스트
수정 20줄. 필요한 변경이며 범위 안으로 본다). `app/`·`weave_core/` 무변경.

`tests/conftest.py`: `_TEST_DB = os.environ.get("TEST_DB_NAME", "").strip() or "glitch_noai_test"` + 영숫자·밑줄 1~63자 검사. 환경변수가
없으면 **기본값 그대로**(`glitch_noai_test`) — 다른 동작 변화 없음(`_ensure_test_db`는 docstring만 바뀜). `.env`·CI 영향 없음.

## 2. `train_lib.py` · `train_judge.py` vs 설계안

| 설계안 | 코드 | 일치 |
|---|---|---|
| §2-2-2 가중 CE: w ∝ 빈도^−½, 평균 1 정규화 | `class_weights`: `n^-0.5`, 있는 클래스 평균 1, 없는 클래스 0; `ce`면 전부 1. 빈도는 **감축 뒤** 에폭당 수(`sampled_counts`) | ○ |
| §2-2-2 묶음 단위 샘플링, 무관 = (모순+정상)×2 상한, 시드 고정 | `sample_epoch`: 상한 `floor(rel×ratio)`, 묶음(`group_key` = contrast_group → group_id → case_id)별 라운드로빈, `Random(f"unrelated:{seed}:{epoch}")`, 입력 순서 유지. 테스트가 같은 시드·에폭 동일, 묶음당 4개 균등, 작은 묶음도 1개 확보를 확인 | ○ |
| §2-2-3 '제외' 라벨: 손실에서 뺀다 | `prepare`: 학습·검증·holdout 모두 `drop_excluded`; `scene_scores`는 '제외'가 오면 `ValueError`; 온도는 검증(제외 제거 뒤)으로 | ○ 손실·보정·기준값 전부 |
| §2-4-1 온도: 검증 쌍 logits 단일 T(NLL 최소) | `fit_temperature`: LBFGS on log T | ○ |
| §2-4-2 장면 기준값: p_scene = max p(모순), 정상 장면 오탐 ≤ 0.05에서 recall 최대, 동률 높은 값; 쌍 F1 버림 | `pick_scene_threshold`: 후보 = 장면 값 전부 + nextafter(최댓값); `fpr ≤ target`에서 `(recall, t)` 최대 → 동률은 높은 t. `pick_threshold` 삭제(테스트가 `not hasattr` 확인). 정상 장면 없으면 fpr 0 | ○ |
| §0-4c·§2-6 누수: eval 작품은 학습·검증 모두 제외, 같은 작품 양쪽 금지 | `split_by_work`: eval_work 행 `dropped["eval_work"]`, val∈train·eval∈{val,train}·`work` 없는 행 → `ValueError`; holdout도 eval_work 제외; `prepare` 끝에 불변식 assert. 기본 `val_sources="real"`이라 검증 작품의 주입·LLM 행은 학습에도 검증에도 안 들어간다 | ○ |
| §3-1 hyp-only, train-frac(0.25/0.5/1, 포개짐), seeds, 조기 종료(검증 NLL, patience 2), ECE·PR-AUC, meta 확장 | 전부 있음. `train_fraction`은 묶음 단위·같은 시드에서 부분집합. `select_seed` recall → NLL → 앞선 시드. `EarlyStopper` 최선 에폭 복원 | ○ |
| §2-6 접기 순환 고정(wm→sherlock, q1→wm, q3→q1, sherlock→q3) | 코드가 강제하지 않는다(인자로 준다) | △ 운영 규칙 |

**torch 루프 코드 읽기**(실행 못 함): 라벨 순서 `TL.LABELS = ["모순","정상","무관"]` = `learned_judge.LABELS` ○, `id2label`·`label2id`를 그 순서로
저장 ○. KLUE-NLI 매핑 `{0:정상, 1:무관, 2:모순}` = entailment/neutral/contradiction ○. meta 키: `labels`·`temperature`·`threshold`(=장면
기준값; `judge_from_settings`가 `meta["threshold"]`를 읽는다)·`pairs_version`(app.pipeline.pairs에서) ○. 조기 종료 최선 상태 복원·시드 폴더
정리·`model_dir_hash` ○. `DataLoader(generator=seed)`로 셔플 고정 ○. **precision**: meta에 `precision`을 쓰지만 현재 main의 `HFBackend`는
fp32 고정이고 읽지 않는다(DF 보고서 §12-1) — 도움말 "HFBackend 가 읽는다"는 아직 거짓(§6-1). CPU에서 `--precision fp16`이면 경고만 내고
fp32로 학습하면서 meta엔 `fp16`을 적는다(§6-2). 명백한 실행 오류는 보지 못했다.

## 3. `export_pairs` 재생 일치 테스트

`test_export_replay_matches_load_context`는 **4작품 parametrize**로 `ledger_digest(base_replay(work).ledger) == ledger_digest(load_context(work).ledger)`
(`Ledger.to_dict()` sha256)와 corpus·registry·mapping 동등을 비교한다 ○. `test_export_real_uses_harness_packets_and_labels`는 장면 하나 = 패킷
하나로 쌍 수가 하네스(`LearnedJudge.votes`)와 같음과 라벨 규칙(근거 → 모순, 설정 장면 rule → 제외, 같은 (엔티티,필드)/근거 사건 언급 → 제외)을
확인 ○. 설계안이 지적한 옛 불일치(`timelines` 없이, `load_registry(base)`)는 사라졌다: `base_replay`가 `C.load_episodes` + `compile_ledger(timelines=["main"],
knows_mode="accumulate")`, 규칙은 `_rules_before(…, 10**6)`(= load_context의 "레지스트리에 없으면 화 문서에서") ○.

## 4. G가 찾은 문제 두 개의 독립 확인

### 4(a) WM 장면 timeline과 checker 항목 — 사실. 영향은 제한적

`scratchpad/wm_timeline_probe.py`(판정 코드 무수정, `ledger.compile_ledger`만 감싸 timelines를 바꿈):

| 작품 | 장면 사건 timeline | 하네스(`timelines=["main"]`) checker 항목 / needs_judge | WM 고유 timelines(`real_present·future_world·unknown`) |
|---|---|---|---|
| wm | future_world 31 · real_present 1 | **9 / 7** | **31 / 28** |
| q1 | main 81 | 103 / 92 | — |
| q3 | main 48 | 108 / 100 | — |
| sherlock | main 77 | 130 / 119 | — |

원인: `checker.check_event`(185행)는 사건의 timeline 이름 그대로 `ledger.get(timeline, …)`를 찾는데, `timelines=["main"]` 원장엔 `main`·`unknown`
칸만 있어 `future_world` 조회가 비어 항목이 안 나온다. 반면 `pairs._timeline`은 `tl in ledger.state` 아니면 `unknown`으로 떨어지고 WM 사건은 적용
때 `unknown` 칸에 들어가 있으므로 **ledger 전제는 정상**이다(checker 전제만 준다).

| 영향 받는가 | 수치 | 결과 |
|---|---|---|
| 동결 열 재현(`--mode recorded`, `test_judge_baseline.py`) | 녹화된 판정 파일 재생 — checker 항목을 쓰지 않음 | **없음** |
| learned 가짜 게이트(`test_judge_learned_fake_baseline.py`) WM | TP 15/20 · FP 8/10 · 쌍 1,440 · 지문 `a00db0ca604c806f` | **동일** — FakeBackend의 충돌 규칙이 ledger 전제도 받고, 쌍 수는 가설당 24로 포화라 대표 쌍이 안 바뀐다 |
| `pair_coverage` WM(근거 포함) | v2@24 19/20 → **18/20**, v1@32 18 → 17, v1@∞ 19 → 19; checker 쌍 14 → 49 | **1장면 감소**(checker 전제가 상한 안의 다른 전제를 밀어냄). 합 70/75 → 69/75, 목표 66 유지 |
| checker 게이트(`test_weave_checker_gate.py`, `checker_eval.py`) | `compile_ledger(base=…)` — timelines 기본(WM 고유) | **없음**(별도 재생) |
| 서비스 경로 | 프로젝트 timelines는 `weave_config`에서 — 하네스 문제 아님 | 없음 |

즉 하네스의 WM 재생은 원본 Weave WM 조건(STATE_TIMELINES)과 달라 **checker 층이 빠진 조건**이다. 숫자 게이트는 안 움직이지만, 학습 데이터
(export_pairs `--work wm --real`)와 pair_coverage엔 checker 전제가 거의 없다. 고치려면 `load_context`가 작품별 timelines(WM = STATE_TIMELINES)를
쓰게 하고 pair_coverage WM 행과 export 산출물을 다시 내면 된다 — 게이트 상수는 그대로일 가능성이 높지만 돌려서 확인할 것.

### 4(b) inject 사건 timeline 변경 — 맞다. WM만이 아니라 전 작품에 필요

`inject_timeline(base)` = 기반 corpus 사건이 들어간 원장 칸의 최빈값(WM `unknown`, 나머지 `main`), setup·test 사건을 그 이름으로 바꾼다.
`scratchpad/inject_keyed_probe.py`(manifest: `inject.main --per-operator 2 --seed 3`, 사례 수 동일):

| 작품 | 수정 전 keyed / checker 행 | 수정 후 keyed / checker 행 |
|---|---|---|
| wm | 116 / 0 | **139** / 27 |
| q1 | 117 / 0 | **134** / 20 |

수정 전엔 주입 사건이 `future_world`라 **모든 작품에서** checker 전제가 0이었다(원장 칸 이름이 아니므로). 바꾼 칸 이름은 기반 사건이 실제로
들어간 칸이라 원장 상태는 같고 checker 조회만 살아난다 — 올바른 수정. G가 적은 175→205는 다른 manifest 크기 기준이며 방향·비율이 같다.

## 5. 시험 병합과 테스트

- 스크래치 워크트리(main `bbf2c13`) + `git merge --no-edit G` → 머지 커밋, **충돌 없음**.
- `tests/eval` + `tests/test_learned_judge.py`(소켓 차단 플러그인, `TEST_DB_NAME=glitch_noai_test_verify`): **107 passed**, 외부 연결 0.
  (`test_train_lib.py` 28 = 함수 25 + parametrize 3, `test_data_tools.py` 33 = 22 + 11, 둘 다 torch 없이.)
- 전체 suite(같은 DB 분리, ListAgents 11개 세션 전부 idle·pytest 프로세스 0 확인 뒤 한 번, 02:58~03:04, 318초):
  **456 passed, 1 skipped, 3 xfailed, 실패 0**, 외부 연결 0 — G 자체 측정(456)과 같다.
- H 브랜치(`claude/learned-judge-local-llm-711431`, `c52ae3d`에서 미진행)는 합치지 않았다.

## 6. 틀린·빠진 항목

1. `train_judge.py --precision` 도움말 "서비스 추론 정밀도(meta 기록 — HFBackend 가 읽는다)": 현재 main의 `HFBackend`는 `torch_dtype=float32`
   고정이고 meta `precision`을 읽지 않는다(DF 보고서 §12-1). 읽게 고치거나 도움말을 "앞으로 읽을 값"으로. (H 또는 후속에서 HFBackend 쪽을 맞춘다.)
2. CPU에서 `--precision fp16`(기본)이면 fp32로 학습하면서 meta에 `fp16`을 적는다. 실제 학습 정밀도를 meta에 따로 기록하거나 CPU면 인자를 fp32로 바꿀 것.
3. §2-6 접기 순환(wm→sherlock, q1→wm, q3→q1, sherlock→q3)을 코드가 강제하지 않는다 — README/설계안의 명령 예시로만. 접기별 실행 스크립트에
   표로 박아 두면 실수를 막는다(사소).
4. 하네스 WM 재생의 timeline(4a)은 G가 export_pairs docstring에 "주의"로만 적었다. `judge_eval.load_context`를 작품별 timelines로 바꾸는 후속을 따로
   둘 것(게이트 재측정 포함). G의 범위 밖이므로 병합 조건은 아니다.

## 참고: 이 세션이 만든 임시 파일(저장소 밖, 스크래치패드)

`wm_timeline_probe.py`·`wm_probe.json`(4a 측정), `inject_keyed_probe.py`(4b 측정), `nonet/nonet.py`(외부 소켓 차단 플러그인),
`full_suite_merge_G.txt`(전체 suite 로그). 스크래치 워크트리 `merge_G`는 끝나고 지웠다.
