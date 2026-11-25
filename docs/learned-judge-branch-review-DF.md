# D·F(+E) 브랜치 검증 보고 (2026-11-06 / E는 11-07 추가)

검증 전용 세션의 결과. 코드는 고치지 않았고 이 문서 하나만 만들었다(커밋·push 없음). 브랜치 E는 뒤의 **§E** 절.

| 항목 | 값 |
|---|---|
| 기준 | main `b1a4fb3` |
| D | `claude/learned-judge-pairs-v2-0dd6bb` — `2fb53b4`(쌍 생성기 v2·HFBackend) · `ed16d0d`(`scripts/eval/pair_coverage.py`) |
| F | `claude/learned-judge-cleanup-8810a7` — `a16cbce`(preflight 인코딩) · `35dbaee`(API 계약 auto_save) · `0a6c980`(learned 가짜 백엔드 회귀 게이트) · `e9aaf39`(녹화·재생 플레이크) |
| 두 브랜치의 merge-base | 둘 다 `b1a4fb3` |
| 환경 | `.venv` Python 3.12.6, transformers 미설치, `PYTHONIOENCODING=utf-8` |

## 판정 요약

**합쳐도 된다 — 조건 하나.** D와 F는 파일이 겹치지 않아 어느 순서로도 충돌 없이 합쳐진다. 다만 F의 회귀 게이트
(`tests/eval/test_judge_learned_fake_baseline.py`)는 main b1a4fb3 의 쌍 생성기 출력을 고정한 것이라 D를 합치면 의도된 변화로
3개 테스트가 실패한다. **합칠 때 §8의 갱신값으로 `BASELINE_*` 상수와 `docs/EVAL.md` 표를 함께 바꿔야 한다**(F 자체의
갱신 절차 그대로). 그 밖에 병합을 막는 결함은 없다.

설계안과 다른 점(병합을 막지는 않지만 기록·후속이 필요한 것)은 §12에 모았다. 핵심은 둘이다: (1) D의 `HFBackend`는 정밀도를
**fp32로 고정**하고 모델 메타(`judge_meta.json`)에서 읽지 않는다 — 설계안 §0-4(a) "GPU fp16, 메타에 기록"과 다르다.
(2) §2-5 표의 "SDPA 백엔드 고정"이 코드에 없다.

## 0. 범위 확인

`git diff --name-only b1a4fb3..<branch>`:

- D(7개): `.env.example` `app/core/config.py` `app/pipeline/learned_judge.py` `app/pipeline/pairs.py` `app/pipeline/weave.py`
  `scripts/eval/pair_coverage.py` `tests/test_learned_judge.py` — 과제가 적은 목록과 일치. `weave.py`의 변경은 `_judge_learned`
  한 함수 안(판정 회차 계산 3줄 + `judge_env` 기록 2줄)뿐이다.
- F(5개): `docs/EVAL.md` `docs/glitch_api_contract.md` `tests/eval/test_judge_learned_fake_baseline.py` `tests/eval/test_llm_record.py`
  `tests/test_public_preflight.py` — 일치.
- `app/pipeline/weave_core/` 변경: **양쪽 모두 없음**. 범위 밖 파일: 없음.

## 1. D-1 `pair_coverage.py` 재현 (D 워크트리에서 직접 실행)

`PYTHONIOENCODING=utf-8 python scripts/eval/pair_coverage.py` (current_episode=6, 재생은 `judge_eval.load_context`):

| 작품 | 해석 | 근거 있는 양성 | v1@32 (개정 전) | v2@24 (개정 후) | v1@∞ (도달 가능 최대) |
|---|---|---|---|---|---|
| wm | matched | 20 | 18 | 19 | 19 |
| q1 | matched | 19 | 13 | 18 | 19 |
| q3 | matched | 18 | 13 | 17 | 18 |
| sherlock | matched | 12 | 4 | 10 | 11 |
| **합** | matched | 69 | **48** (69.6%) | **64** (92.8%) | 67 (97.1%) |
| wm | all | 20 | 18 | 19 | 19 |
| q1 | all | 20 | 14 | 19 | 20 |
| q3 | all | 19 | 14 | 18 | 19 |
| sherlock | all | 16 | 7 | 14 | 15 |
| **합** | all | 75 | **53** (70.7%) | **70** (93.3%) | 73 (97.3%) |

- 설계안 목표("전부" 해석 ≥ 66/75)는 **달성**: 70/75. 개정 전 53/75(설계안 §2-0 값과 일치). matched만은 48/69 → 64/69.
- 쌍 수(장면당 평균/합): wm 64→48 / 1,920→1,440 · q1 211→159 / 9,503→7,142 · q3 152→114 / 6,816→5,112 ·
  sherlock 185→145 / 8,301→6,507. 글자 수 p99: q1 1,962→686, q3 1,788→752(발췌 160자 자르기 효과).
- kind 분포가 설계안의 진단대로 바뀌었다: q3의 rule 3,607→875, knowledge 0→1,727, past 0→1,242.

**v2@24가 놓치는 5장면**(두 해석에서 같은 5개; `scratchpad/uncovered.py`로 확인):

| 작품 | 장면 | 근거 사건 | v1@32 | v1@∞에서 근거가 실리는 kind | 판단 |
|---|---|---|---|---|---|
| wm | scene_07 | EVENT_3_6 | ✗ | (없음) | 어떤 선택으로도 도달 불가 — 전제 후보 자체에 없음 |
| sherlock | scene_03 | EVENT_3_22·25·28 | ✗ | (없음) | 위와 같음 |
| q1 | scene_01 | EVENT_3_14 | **○** | ledger, past | v2에서 **잃은 1건** — 관련도 정렬이 이 사건을 ledger 6·past 6 밖으로 밀었다 |
| q3 | scene_42 | EVENT_5_4 | ✗ | past | past 할당 6에 못 들어감 |
| sherlock | scene_16 | EVENT_2_1 | ✗ | past (쌍 2,083개 중) | past 할당 6에 못 들어감 |

즉 도달 가능한 73 중 70. 빠지는 kind는 전부 **past**(q1은 ledger도). 설계안 §2-3은 "못 미치면 관련도 함수를 고친다"고 했는데
목표를 넘겼으니 지금 고칠 일은 아니다. 다만 q1 scene_01은 개정 전엔 잡히던 것이므로 학습 작품 쪽 관련도 점검 때 참고할 사례다.

## 2. D-2 결정성과 `quotas=None` 동일성

4작품 165장면 전부를 대상으로 `build_pairs` 출력을 JSON 직렬화해 sha256으로 비교했다(`scratchpad/dump_pairs.py`).

| 조건 | 트리 | 쌍 합계 | sha256 (앞 16자) |
|---|---|---|---|
| main 기본(상한 32) | `b1a4fb3` 체크아웃 | 26,540 | `91465f805ec7456e` |
| D `quotas=None, current_episode=6, excerpt_chars=10` | D 워크트리 | 26,540 | `91465f805ec7456e` |
| D 기본(v2@24) 두 번 | D 워크트리 | 20,201 | `f870fe04b75d07a0` (두 번 완전 동일) |

- `quotas=None`은 b1a4fb3 출력과 **바이트 단위로 같다**. `current_episode`·`excerpt_chars`를 줘도 v1 경로는 무시한다(설계대로).
- v2는 같은 입력 두 번 → 같은 순서(165장면 전부 `==`). 테스트는 corpus·checker 입력 순서를 섞어도 같음을 추가로 확인한다.
- `tests/test_learned_judge.py`의 `V1_TOY_SHA256`(`28985a83…`)을 **main의 `pairs.py`로 직접 재계산**해 일치함을 확인했다
  (`scratchpad/toy_digest_main.py`, 96쌍). 즉 D의 "개정 전과 같음" 테스트는 실제 개정 전 코드를 기준으로 한 것이다.

## 3. D-3 `HFBackend` 코드 읽기 (`app/pipeline/learned_judge.py`)

| 검사 | 결과 |
|---|---|
| 버킷 패딩 | `bucket_plan`: 토큰 길이로 버킷(`buckets_for(max_length)` = 128/256/384/512 중 max_length 이하 + max_length) 배정, **버킷 오름차순·버킷 안은 입력 순서**로 `batch_size`씩. `run_plan`이 인덱스로 원래 자리에 되돌리고 빠진 쌍·개수 불일치는 `RuntimeError`. 같은 입력 → 같은 계획. **설계 §2-5 표대로** |
| `use_deterministic_algorithms(True)` | `configure_torch`에서 항상 호출 ○ |
| `cudnn.benchmark=False` | GPU 경로(`device != cpu`)에서만 ○ |
| `CUBLAS_WORKSPACE_CONFIG` | GPU 경로에서 비어 있으면 `:4096:8`로 넣고 경고(CUDA 초기화 뒤면 효과 없을 수 있다고 적음). `.env.example`에 워커 환경에 미리 넣으라고 안내 ○ |
| 스레드 | CPU 경로: `set_num_threads(N)`(설정 있을 때), `set_num_interop_threads(1)`(실패 시 경고). `OMP/MKL_NUM_THREADS`·`MKL_CBWR`은 표의 "위치"가 워커 기동/환경이라 코드엔 없고 `env()`가 `MKL_CBWR`를 기록 ○ |
| SDPA 백엔드 고정 | **없음** — §0-4(b)·§2-5 표(위치: HFBackend)에 있는 항목이 빠졌다 → §12 |
| `pairs_version` 불일치 | `check_pairs_version`: 메타 값이 `"2"`와 다르거나 **없으면** `RuntimeError`, torch import 전에 검사 ○ |
| 정밀도 | **모델 메타에서 오지 않는다.** `from_pretrained(..., torch_dtype=torch.float32)`로 고정, docstring도 "fp32만(fp16·autocast 금지)". §0-4(a) "GPU 기본 fp16, 메타에 고정 기록"과 **불일치** → §12 |
| transformers 없이 import | ○ (`import app.pipeline.learned_judge` 성공, torch/transformers는 `HFBackend.__init__` 안에서만) |
| 기타 | `max_length` 기본 512→256(설정 `WEAVE_JUDGE_MAX_LENGTH=256`), `padding`·`device` 값 검증, `judge_from_settings` 캐시 키에 새 설정 4개 포함, `env()`에 torch/transformers 판·장치·스레드·CPU명·MKL_CBWR·결정적 여부(+GPU면 이름·CUDA·CUBLAS 설정) |

## 4. D-4 환경 기록 위치

`weave.py::_judge_learned`: 패킷 루프 밖에서 `checks["config"]["judge_env"] = judge.backend.env()` **한 번**. `judge_group` 반환
dict에는 `env`가 없고 `pairs_version`·`top2`가 더해졌다. 테스트가 `saved["config"]["judge_env"]["backend"] == "fake"`와
`"env" not in runs[0]`를 확인한다 ○. 판정 회차(`current_episode`)는 사건에 episode가 없을 때 corpus 최대 회차+1로 계산 —
하네스(5화 확정 → 6)·서비스(N-1화 원장 → N) 모두 맞다.

## 5. D-5 테스트 커버리지 (설계안 §3-1 "테스트" 항목)

| §3-1 항목 | 테스트 | 비고 |
|---|---|---|
| 할당량 결정성 | `test_pairs_quota_deterministic_and_counts` | 두 번 동일 + 입력 순서 셔플 + kind 순서·할당량 카운트(남는 칸 past로 넘김) |
| `quotas=None` 동일 | `test_pairs_quotas_none_matches_v1`, `test_pairs_order_and_cap_v1` | sha 고정(§2에서 main 코드로 검증) |
| pairs_version 에러 | `test_hf_backend_rejects_pairs_version_mismatch` | 값 다름·없음·잘못된 padding |
| 버킷 순서 복원 | `test_bucket_plan_restores_order` | 가짜 infer로 계획·복원·개수 불일치 에러 |
| 경과 회차·발췌 자르기 | `test_pairs_episode_elapsed_and_excerpt` | `[M화]`, `경과 M−N화`, 160자+`…`, 이후 사건 80자, episode 우선, 회차 모를 때 생략 |
| (추가) | `test_pairs_quota_overflow_and_relevance`, `test_pairs_event_hypothesis_ledger_capped`, `test_judge_group_reports_top2_and_version` | 관련도 정렬·사건 가설 ledger 6개·top2 |

전부 덮는다. D 워크트리에서 `tests/test_learned_judge.py` **18 passed**.

## 6. F-6 preflight

- 변경은 `subprocess.run(..., encoding="utf-8")` 한 줄(주석 포함)뿐. `scripts/public_preflight.py` 무변경(`git diff --stat` 빈 출력).
- F 워크트리에서 `tests/test_public_preflight.py` 통과(아래 §11 실행 기록).

## 7. F-7 API 계약 문서 vs 코드

| 문서 주장 | 코드 | 일치 |
|---|---|---|
| analyze 바디 `{auto_save}` 기본 false, review는 기본 true | `schemas/analysis.py` `AnalyzeRequest.auto_save=False`, `ReviewRequest.auto_save=True`; `api/chapters.py`가 `_enqueue_or_fail(..., auto_save=opts.auto_save)` | ○ |
| saved 조건 ① 추출 성공(실패 시 failed, failed_stage=extract, 검수·저장 없음) | `tasks.run_full_analysis`: `run_extraction` False면 return; 실패는 `_record_failure(..., "extract")` | ○ |
| ② 검수 성공해 reviewed 커밋 | `run_review`: contradictions 저장 → status reviewed → commit → `committed=True` | ○ |
| ③ 직후 저장 성공; 실패하면 reviewed 유지, failed 아님 | `_auto_save`: `save_chapter` 예외 시 rollback + 로그만. reviewed는 이미 커밋됨 | ○ |
| 저장 실패 예: 추출 결과 없음 409 | `save_chapter`: canon_delta 없으면 `ConflictError` | ○ |
| 폴링에 잠깐 reviewed 보일 수 있음 | reviewed 커밋 뒤 저장 | ○ |
| 진행 중이면 409 + current_status | `_guard_not_running`(고착이면 failed로 정리 후 통과) | ○ |
| **saved 회차는 409** "이미 저장된 회차는 재분석할 수 없습니다." | `start_analysis` 문자열 그대로 | ○ |
| 그 외 상태는 이전 결과·모순을 지우고 처음부터 | `_begin_pipeline` → `clear_for_chapter` + `create_pending` | ○ |
| 상태도 줄 `uploaded·extracted·reviewed·failed → [analyze]` | saved·진행 중만 막으므로 그 넷 | ○ |

틀린 서술 없음.

## 8. F-8 회귀 게이트

**main b1a4fb3 재현**: F 워크트리(app 코드 = main)에서 `tests/eval/test_judge_learned_fake_baseline.py` 5개 전부 통과.
별도 스크립트로 같은 계산을 main 체크아웃에서 돌려도 TP 71/80 · FP 80/85 · 쌍 1,920/9,503/6,816/8,301 · 지문 4개가
테스트 상수와 같았다.

**D를 합쳤을 때의 값**(D 워크트리에서 계산, 병합 트리의 실제 테스트 실패 메시지와 같음 — 합칠 때 이 값으로 갱신):

| 작품 | TP / 양성 | FP / 정상 | 검출 장면 변화(b1a4fb3 대비) | 판정 쌍 합계 | 지문 |
|---|---|---|---|---|---|
| wm | **15** / 20 (12→15) | 8 / 10 | + scene_12, 14, 17 | **1,440** | `a00db0ca604c806f` |
| q1 | 20 / 20 | 25 / 25 | 없음 | **7,142** | `65f3a1c60bcf05ef` |
| q3 | 20 / 20 | 25 / 25 | 없음 | **5,112** | `e398085567d40c8e` |
| sherlock | 19 / 20 | **24** / 25 (22→24) | + scene_09, 32 (빠지는 장면은 28·29만) | **6,507** | `c5a2a5d189f35cb2` |
| **합** | **74 / 80** | **82 / 85** | | **20,201** | |

테스트 상수로 쓰면: `BASELINE_TOTAL = (74, 80, 82, 85)`, `BASELINE_PER_WORK["wm"] = (15, 20, 8, 10)`,
`["sherlock"] = (19, 20, 24, 25)`, `BASELINE_DETECTED["wm"]`에 12·14·17 추가, `["sherlock"]`은 `not in (28, 29)`,
`BASELINE_PAIRS = {"wm": 1440, "q1": 7142, "q3": 5112, "sherlock": 6507}`, `BASELINE_FINGERPRINT` 위 표.
쌍 합계가 §1 pair_coverage의 v2@24 합과 정확히 같다(두 경로가 같은 `build_pairs`를 쓴다는 교차 확인).
`judged_by`(`learned / 21e3281f48f8145b`)는 변하지 않는다 — FakeBackend 해시는 쌍 생성기와 무관.

## 9. F-9 녹화·재생 플레이크

**원인 설명은 코드와 맞다.**

- `weave.py:1113` `rev = time.strftime("%Y%m%dT%H%M%S") + "-" + sha1(json.dumps(doc, sort_keys=True))[:8]` — 지역 시각 **초** 단위.
- `weave_core/weave_extract.py:172` `doc["_extraction"]["produced_at"] = now()` — `now()`(39행)는 UTC 초 단위. 이 doc이 해시에 들어간다.
- 따라서 record 실행과 replay 실행이 초 경계를 넘으면 `attrs.weave_rev`가 달라 `a.model_dump() == b.model_dump()`(`test_llm_record.py:165`)가 깨진다.
- 다른 벽시계 값은 없나: `_extraction.elapsed_sec`·`attempts`는 LLM 응답 dict에서 오는데, 재생은 녹화된 **응답 dict 전체**를
  deepcopy로 돌려주므로(`llm_record.py:144`, 녹화 항목 설명 21행) 두 실행에서 같다. 초 경계만 남는다 — 설명과 일치.

**고친 방식**: `_freeze_clock`이 `time.strftime`을 인자 tuple이 없을 때만 고정 struct를 쓰게 감싸고(명시 tuple은 그대로 →
logging 등 무해), `W.w_extract`·`W.w_pipe`·`W.w_llm` 모듈의 `now` **속성**을 monkeypatch한다. `weave_extract.py`의 `now()`는
호출 시 모듈 전역을 찾으므로 속성 교체가 먹는다. `weave_core/` 파일은 건드리지 않는다 ○. 함께 넣은 autouse `_isolate_recorder`는
settings 3개를 기본값으로 고정하고 전역 recorder를 비운다(격리용, 플레이크 원인과 별개라고 명시).

**같은 플레이크가 가능한 다른 테스트**(파이프라인을 두 번 돌려 비교하는 것, grep):

| 테스트 | 비교 대상 | 영향 |
|---|---|---|
| `tests/test_extract_gate.py::test_same_input_twice_gives_same_gate` | 두 추출의 gate 파일 | `rev`·`graded_at`을 **pop하고** 비교 → 이미 면역 |
| `tests/test_extract_gate.py::test_gate_off_review_matches_previous_behavior` | 같은 out1/out2로 검수 두 번 | 추출은 한 번(rev 동일) → 해당 없음 |
| `tests/eval/test_weave_checker_gate.py:51` | 원장 재생 두 번 | 순수 함수, rev 없음 |
| `tests/test_weave_pipeline.py`·`test_weave_failures.py`·`test_weave_knows_mode.py`·`test_learned_judge.py` | out1/out2는 1화·2화 연쇄 | 같은 입력을 두 번 추출해 비교하는 곳 없음 |

추가로 고칠 테스트는 없다.

## 10. 시험 병합

스크래치 워크트리(`git worktree add --detach <scratchpad>/merge_DF main`)에서 D → F 순서로 `git merge --no-edit`:

- D: fast-forward(main == merge-base). F: 머지 커밋 생성. **충돌 없음**(파일 집합이 서로소이므로 역순도 충돌할 수 없다).
- 병합 트리 `git diff --name-only b1a4fb3..HEAD` = D 7개 + F 5개 = 12개.
- 병합 트리에서 `tests/test_learned_judge.py` + `tests/test_public_preflight.py` + `tests/eval`: **47 passed, 3 failed** —
  실패는 전부 §8의 회귀 게이트(`test_detected_scene_sets`, `test_counts`, `test_pair_fingerprint`)이고 값은 §8 표와 같다.
  `test_backend_is_fake`·`test_settings_restored`와 F의 녹화·재생 테스트, D의 테스트는 모두 통과.
- 전체 suite: §11.

스크래치 워크트리는 끝나고 지웠다.

## 11. 테스트 실행 기록 (Postgres 테스트 DB 공유 주의)

`tests/conftest.py`는 import 시점에 `_ensure_test_db()`(DB 생성 + `alembic upgrade head`)를 돌리므로 어떤 pytest 호출도
공유 테스트 DB(`glitch_noai_test`)를 건드린다. 드롭은 하지 않는다.

| 시점 | 어디서 | 무엇 | 다른 세션 상태 | 결과 |
|---|---|---|---|---|
| 1차 | D 워크트리 | `tests/test_learned_judge.py` | E 작업 중(DB 비사용 테스트만 골라 실행) | 18 passed |
| 1차 | F 워크트리 | preflight · `test_llm_record` · 회귀 게이트 | E 작업 중 | 20 passed |
| 2차 | 병합 트리 | 위 둘 + `tests/eval` | E 작업 중 | 47 passed, 3 failed(§10) |
| 3차 | 병합 트리(D+F) | **전체 `pytest`** | ListAgents로 **8개 세션 전부 idle 확인 후** 한 번(23:54~00:00, 311초) | **391 passed, 3 failed, 1 skipped, 3 xfailed** — 실패 3개는 §8의 회귀 게이트뿐 |
| 4차 | E 워크트리 | `tests/eval/test_data_tools.py`(외부 소켓 차단 플러그인) | 전부 idle | 22 passed, 차단된 연결 0 |
| 5차 | 병합 트리(D+F+E) | `tests/eval` + D·F 테스트(소켓 차단) | 전부 idle | 69 passed, 3 failed(회귀 게이트) |
| 6차 | 병합 트리(D+F+E) | **전체 `pytest`**(소켓 차단) | 전부 idle 확인 후 한 번(01:56~02:02, 315초) | **413 passed, 3 failed, 1 skipped, 3 xfailed**, 차단된 외부 연결 0 — 실패 3개는 §8의 회귀 게이트뿐 |

## 12. 틀린·빠진 항목 (병합을 막지 않음)

1. **정밀도 fp32 고정** — D `HFBackend`는 `torch_dtype=float32`로 고정하고 메타의 정밀도를 읽지 않는다. 설계안 §0-4(a)·§2-5 표
   ("GPU 기본 fp16, 모델마다 `judge_meta.json`에 기록")과 다르다. 모델이 아직 없으니 지금은 동작 차이가 없지만, GPU 학습·추론
   단계에서 메타 `precision` 읽기 + fp16 로드를 더해야 한다(§0-4(a) 지연 추정 25~40초 vs 50~90초의 전제).
2. **SDPA 백엔드 고정 없음** — §2-5 표(HFBackend 위치)에 있는 항목이 코드에 없다. 결정성 확인 테스트(§2-5 "확인 테스트")를
   모델이 생긴 뒤 돌릴 때 함께 넣을 것.
3. **회귀 게이트 상수 갱신 필요** — F의 `BASELINE_*`와 `docs/EVAL.md` 표는 b1a4fb3 기준. D 병합 시 §8 값으로 갱신(F 문서의
   갱신 절차 그대로, 커밋 메시지에 wm +12·14·17, sherlock +9·32 적기). 갱신하지 않고 합치면 CI/로컬에서 3개 실패.
4. **q1 scene_01 근거 손실** — v2@24에서 개정 전엔 잡히던 근거 1건을 잃는다(§1). 목표는 넘겼으므로 수정 대상은 아니고 기록만.
5. (사소) `pair_coverage.py` 머리 설명은 D 세션이 잰 수치와 같다. `docs/EVAL.md`에 pair_coverage 사용법은 아직 없다(D는 docs를
   건드리지 않았다) — 합칠 때 §1 표를 설계안 §2-3 "불확실한 점" 뒤에 적어 두면 좋다.

---

# §E. 브랜치 E 검증 (추가 요청, 2026-11-07)

| 항목 | 값 |
|---|---|
| E | `claude/learned-judge-data-7afeaa` — `7f72a95`(갈래 A `label_pairs.py` + 공용 `datagen_common.py` + `extract_work.py`) · `3aa646a`(갈래 B `counterfactual.py` + 테스트) · `00817ed`(record 재사용·비율 보고·train_only·README) |
| merge-base | `b1a4fb3` |

## E 판정 요약

**합쳐도 된다 — 조건 없음(코드 충돌·테스트 실패 없음).** D → F → E 순서로 스크래치 병합: 충돌 없음, E 테스트 22개는
외부 소켓을 막은 상태에서 통과. 다만 §E-12의 문서·기본값 불일치 4건(README의 추정 단가 기본값, 대량 실행 가드가 절차뿐,
녹화 재사용이 모델을 안 봄, 접기 필터가 아직 `train_judge.py`에 없음)은 합친 뒤 고치거나 README를 바로잡아야 한다.

## E-1. 범위

`git diff --name-only b1a4fb3..E` = `scripts/eval/README.md` `counterfactual.py` `datagen_common.py` `extract_work.py` `label_pairs.py`
`tests/eval/test_data_tools.py` — 6개 **전부 신규**(1,691줄 추가, 삭제 0). `app/`·`weave_core/` 무변경. 범위 밖 파일 없음.
E 워크트리에는 gitignore 된 `build/`(WM 50쌍 시험 실행 산출물·녹화)가 남아 있다(커밋 대상 아님).

## E-2. LLM 호출 경로와 녹화

- 네 스크립트와 테스트에 `httpx`·`openai`·`anthropic`·`requests`·URL 문자열이 없다(grep). 호출은 `datagen_common.call_json` →
  `W.w_llm.complete_json(system, user, llm, role=role, base=W.CORE_DIR)` 하나. `complete_json`은 모듈 전역 `complete`를 부르고
  `weave.py:130`이 그 자리를 `llm_recorder`로 바꿔 두므로 `WEAVE_LLM_RECORD`가 그대로 먹는다 ○. `extract_work.py`는
  어댑터 `W.extract_episode`·`WeaveStore` 그대로 ○. 모델·키는 `W._judge_llm(weave_config())`에서 받아 `model`만 바꾼다 ○.
- **record 모드 재사용**: `recorded_response`가 `llm_recorder.path_of(role, prompt_sha256(system, user))` 파일의
  `entries[occurrence]["response"]`를 읽어 `status=="ok"`면 다시 부르지 않는다. 파일 배치·키(`role:sha256`)·`entries` 누적 순서가
  `llm_record.py`의 `_record`와 같다 ○. 녹화는 ok 응답만 남기므로 실패한 호출은 재시도된다 ○. 재사용분은 `usage.reused`로
  따로 세고 토큰은 녹화 당시 값 ○(테스트 `test_record_mode_reuses_recorded_calls`).
- **모델이 키에 없는 문제**: E가 적은 대로다. 녹화 항목에는 `model`이 **기록돼 있는데**(`_record`의 `entry["model"]`)
  `recorded_response`는 그것을 `llm["model"]`과 비교하지 않는다. 한 줄 보강: `entries[occurrence].get("model") != llm.get("model")`
  이면 miss로 보고 새로 부르기(코어 replay 모드는 그대로 두고 E의 재사용 경로만). 그러면 "모델을 바꾸면 폴더도 바꾼다"는 운영 규칙에
  기대지 않아도 된다 → §E-12.

## E-3. 누수 규칙

- `label_pairs.py` 출력 행마다 `work`(+`scene_id`·`episode`·`group_id=case_id`) ○. **그러나 접기별 제외 수단은 아직 없다**:
  main `scripts/train_judge.py`에 `--val-work`·`--train-works`(설계안 §3-1)가 구현돼 있지 않다(grep: `LABELS`·`read_jsonl`·`split_val`만).
  README의 "학습 스크립트가 접기별로 걸러 낸다"는 현재 참이 아니다 — E의 범위 밖이지만 README 문장은 고쳐야 한다 → §E-12.
- `counterfactual.py`: `INTENDED_USE = "train_only"`를 labels.json 각 항목·manifest 항목에 넣는다 ○(테스트 `test_counterfactual_marks_train_only`).
- 평가 장면 미사용: 스크립트에 `scene_events`·`scene_*_event.json` 참조가 없다(grep 0건). A의 "k화 사건"은 확정 원장 `episode_0k.json`의
  사건이고, B의 입력은 확정 원장 + 회차 원문(`WEAVE_SRC`)이다 ○. 다만 두 도구 모두 4평가작품을 `--work`로 받으므로, 누수 방지는
  "접기에서 평가 작품의 산출물을 빼는" 하류 필터(위 항목)에 달려 있다.

## E-4. 재생 조건 (`datagen_common.replay_upto` vs `judge_eval.load_context`)

| 항목 | judge_eval | E | 일치 |
|---|---|---|---|
| 레지스트리 | `WORKS[work].registry`(q1·q3·sherlock `registry_live.json`, wm `entity_registry.json`) | 같은 `JE.WORKS[work]` | ○ |
| 매핑 | `new_label → id`(5개 컬렉션) | 같은 식. `label_map.json`이 레지스트리 옆에 있으면 합치는데 4코퍼스엔 그 파일이 없다(dracula용) | ○ |
| 정규화 | `ledger.normalize(ep, mapping)` | 같음 | ○ |
| 원장 | `compile_ledger(episodes=1..5, timelines=["main"], knows_mode="accumulate")` | `episodes=1..k-1`, 같은 timelines·knows_mode | ○(의도된 절단) |
| corpus | 1..5화, `episode`·`global_idx` 부여 | 1..k-1화, 같은 부여 | ○ |
| 규칙 | 레지스트리 규칙 그대로(없으면 화 문서에서 수집) | `first_episode < k`만 | ○(미래 규칙 제외, 설계대로) |
| checker 항목 | `check_event(e, ledger, mapping)` + `evidence_trace` | 같은 호출, `evidence_trace` 없음 | ○ — `build_pairs`의 `_checker_premises`는 `evidence_trace`를 쓰지 않는다 |
| k화 사건 | 평가 장면 파일 | 확정 원장 k화 사건(`episode=k` 부여) | 설계상 다른 입력 |

설계안이 지적한 `export_pairs.py`의 불일치(`timelines` 없이 `compile_ledger`, `load_registry(base)`)는 **E에는 없다**.
`test_pool_cuts_future_episodes`가 past 전제의 회차 < k 를 확인한다.

## E-5. 라벨 병합·자기일관성 규칙

- A: 두 번째 호출은 `user_prompt(..., variant=1)`로 전제·가설 순서를 바꾼다(테스트 `test_prompt_variants_differ_but_carry_same_text`).
  `merge_labels`: 실패/형식 오류 → 제외(실패), 판단불가 하나라도 → 제외(판단불가), 불일치 → 제외(불일치), 신뢰도 < `--min-confidence` →
  제외(저신뢰), 그 밖 → 그 라벨·평균 신뢰도. README 4항·테스트 `test_merge_labels`(6례)·`test_merge_labels_min_confidence`와 일치 ○.
  제외 파일 `<out>.excluded.jsonl` ○. 일치율·κ(4범주) ○.
- B: `self_consistency`: 모순판 `target_hits ≥ sc_min(4)`, 대조판 `ok ≥ sc_min` **and** `target_hits ≤ control_max(1)`. README 3항·
  테스트 `test_self_consistency_rule`과 일치 ○.
- **"관대한 필터"라는 자평은 맞다.** 세 가지 이유: (1) 검출기에 대상 사실이 포함된 15개 목록을 번호로 보여 주므로 우연 적중이 1/15이고
  "이 중 하나가 어긋났을 것"이라는 사전 기대가 크다. (2) 생성한 모델이 검출도 하므로 같은 편향을 공유한다. (3) 대조판은
  **대상 번호**를 짚은 횟수만 세고(`target_hits`), 모순판은 보존 사실을 어겼는지 보지 않는다 — 보존 사실을 깨뜨린 장면도 통과한다.
  **더 엄격한 대안 한 줄**: 대조판 조건을 `flagged ≤ 1`(어떤 번호든)로, 모순판에 `flagged − target_hits ≤ 1`(보존 사실 위반 없음)을 더하고,
  사실 목록 없이 "k화 원장 요약 + 장면"만 주는 열린 검출 1회에서 대상 사실을 자유 서술로 짚어야 통과로 둔다(Weave 추출·checker 경로를 두 번째
  필터로 쓰는 것은 §E-5 이후 단계 "장면 추출" 때 자연히 생긴다).

## E-6. 비용 추정과 실행 가드

- 식은 README "비용 계산식" 절에 있다(토큰 = 글자 ÷ cpt, 금액 = 입력/1e6×단가 + 출력/1e6×단가; `datagen_common.PRICES`, `:free`=0). 코드
  `label_pairs.estimate`·`Usage.cost`·`extract_work` 추정이 그 식 ○(테스트 `test_cost_estimate_uses_price_table`).
- **시험 실측 811/95**: E 워크트리 `build/llm_pairs/wm_trial50.meta.json`으로 확인. 호출 100 중 **usage 없는 호출 28**(실패 28 = 14쌍×2),
  입력 58,414·출력 6,838 → usage 있는 72호출 기준 **811.3 / 95.0** ○, cpt 2.401 ○. 단 같은 meta의 `observed_out_tokens_per_call`은
  100으로 나눠 68.4 — E 자체 보고 필드가 README 수치와 분모가 다르다. 실패율 28%(무료 모델)와 '모순' 0건·'정상' 2건·'무관' 31건도 기록.
- **README "A 추정은 이 값으로 낸다"는 코드와 다르다**: `--chars-per-token` 기본 1.6, `--out-tokens` 기본 90 그대로다(시험 실행 전 추정이
  입력 87,644로 실측 58,414보다 50% 과대). 기본값을 2.4/95로 올리거나 README 문장을 "옵션으로 준다"로 고칠 것 → §E-12.
- **대량 실행 가드**: `extract_work.py`만 `--run`으로 막혀 있다. `label_pairs.py`는 `--dry-run`이 없으면 `--out`만 있으면 바로 실행되고
  기본 `--target 5000`(작품당 10,000호출). `counterfactual.py`는 `--n` 기본 1(사례당 11호출)이고 2 이상에 가드 없음. README의 "승인 뒤 실행"은
  절차일 뿐 코드가 막지 않는다 → §E-12.

## E-7. 테스트

`tests/eval/test_data_tools.py`: 함수 17개 + parametrize 5 = **22개**. LLM은 `W.w_llm.complete`를 `FakeLLM`으로 바꿔(코어 `complete_json`
경로는 실제로 지난다) 돌고, 녹화 재사용 테스트는 `LLMRecorder(FakeLLM, mode="record", root=tmp)`를 `W.llm_recorder`에 끼운다.
`OPENROUTER_API_KEY`는 가짜 값으로 환경에 넣는다(`_api_key_ready` 통과용). B 종단 테스트는 `--sources`를 tmp 폴더로 줘 Downloads의
원문에 의존하지 않는다. **네트워크 0 확인**: 루프백 밖 모든 `socket.connect`를 예외로 바꾸는 pytest 플러그인(`scratchpad/nonet/nonet.py`)을
끼워 돌려 **22 passed, 차단된 연결 0**(E 워크트리·병합 트리 모두).

## E-8. 시험 병합 (D → F → E)

- 스크래치 워크트리에서 D(fast-forward) → F(머지 커밋) → E(머지 커밋 `477094e`): **충돌 없음**(E는 신규 파일만).
- D와의 상호작용: `label_pairs.pool_rows`는 `build_pairs(g, ledger, corpus, registry, items)`를 kwargs 없이 부르므로 D 병합 뒤엔 **v2(할당량 24)**
  출력을 쓰고, E가 사건에 `episode=k`를 넣어 두어 `[k화]` 머리가 붙는다. `--top-n 24` 상한은 v2의 24와 같아 사실상 무효. `counterfactual.py`가
  쓰는 `pairs._entities_of`·`_names`는 D에 그대로 있다. 병합 트리에서 E 테스트 22개 통과.
- 병합 트리 `tests/eval` + D·F 테스트: 69 passed, 3 failed — 실패는 §8의 회귀 게이트(D 때문, 갱신값 §8)뿐. 전체 suite: §11 6차.

## E-12. 틀린·빠진 항목 (E)

1. README "학습 스크립트가 접기별로 걸러 낸다" — main `train_judge.py`에 `--val-work`/`--train-works`가 없다. 설계안 §3-1의 미구현 항목이므로
   그것을 구현하기 전까지는 `work` 필드만 있고 제외는 수동이다. README 문장을 "걸러 내야 한다(미구현)"로 고칠 것.
2. README "A 추정은 이 값(811/95, cpt 2.4)으로 낸다" — 코드 기본값은 cpt 1.6·출력 90. 기본값 변경 또는 문장 수정.
3. 대량 실행이 코드로 막혀 있지 않다(`label_pairs.py` 기본 target 5,000, `counterfactual.py --n`). `extract_work.py`처럼 `--run`(또는 시험 규모
   초과 시 `--confirm`)을 두면 README의 승인 절차와 맞는다.
4. record 재사용이 녹화 항목의 `model`을 보지 않는다 — `recorded_response`에서 모델 불일치를 miss로 처리하면 "폴더를 바꿔라" 규칙 없이 안전.
5. (사소) meta의 `observed_out_tokens_per_call`은 전체 호출로 나눈 값(68.4)이라 README 95와 분모가 다르다. usage 있는 호출로 나누거나 두 값을 함께 적을 것.
6. (사소) B 자기일관성은 관대하다(§E-5) — 사람 검증 큐 전 단계이므로 지금은 허용, 더 엄격한 조건은 §E-5의 한 줄.

## 참고: 이 세션이 만든 임시 파일(저장소 밖, 스크래치패드)

`dump_pairs.py`(4작품 쌍 덤프·sha), `toy_digest_main.py`(V1_TOY_SHA256 재계산), `baseline_numbers.py`(회귀 게이트 값 계산),
`uncovered.py`(v2 누락 장면), `nonet/nonet.py`(외부 소켓 차단 pytest 플러그인), `full_suite_merge.txt`·`full_suite_merge_DFE.txt`
(전체 suite 로그). 모두 읽기 전용이며 저장소에 쓰지 않는다.
