# scripts/eval — 학습 judge 평가·데이터 도구

| 파일 | 하는 일 | LLM |
|---|---|---|
| `judge_eval.py` | judge 층 평가 하네스(4코퍼스 45장면 라벨) | `--mode llm` 만 |
| `extract_agreement.py` | 반복 추출 일치도 | 없음 |
| `inject.py` · `export_pairs.py` | 구조 주입 사례·실제 장면 → 라벨 붙은 쌍 JSONL(`--work`, 하네스와 같은 재생) | 없음 |
| `train_lib.py` | `train_judge.py` 의 순수 파이썬 부품(접기 분할·제외·가중치·무관 감축·장면 기준값) | 없음 |
| `label_pairs.py` | **갈래 A** — LLM 쌍 라벨링(FactTrack식) | 있음 |
| `counterfactual.py` | **갈래 B** — 한국어 반사실 회차(FlawedFictionsMaker식) | 있음 |
| `extract_work.py` | 라벨 없는 작품(dracula) 확정 원장 추출(어댑터 경로) | 있음(`--run`) |
| `datagen_common.py` | 위 세 도구의 공용 부품(재생·호출·비용 집계) | — |

근거: `docs/learned-judge-prior-work.md` §2-1(갈래 A·B 표), `docs/learned-judge-training-design.md` §0·§2-2·§3-1.

LLM 호출은 모두 어댑터가 쓰는 코어 경로(`weave_core/weave_llm.complete_json`)를 지난다. 새 SDK·URL 없음.
provider·모델·키는 `WEAVE_*` 설정 그대로(`WEAVE_LLM_PROVIDER`, `WEAVE_JUDGE_MODEL` 또는 `WEAVE_LLM_MODEL`, `WEAVE_API_KEY`).

## 공통 규칙

- **누수 규칙(고정).** 쌍 라벨링(A)과 반사실 회차(B)는 "그 접기의 학습 작품" 확정 회차에서만 만든다. 평가 작품의
  회차·장면은 라벨링·생성에 쓰지 않는다. 출력 행마다 `work` 가 있고, `train_judge.py --val-work … --train-works … --eval-work …`
  가 접기별로 걸러 낸다(아래 "학습 스크립트·접기").
  B 산출물은 `intended_use: "train_only"` 로 표기한다(평가용 반사실은 평가 전용 작품으로 따로 만든다).
- **녹화.** 항상 `WEAVE_LLM_RECORD=record WEAVE_LLM_RECORD_DIR=build/llm_record` 로 돌린다. 이 도구들은 record 모드에서
  **이미 녹화된 프롬프트는 다시 부르지 않는다**(`datagen_common.call_json`) — 한도 초과로 끊긴 실행을 같은 명령으로 다시
  돌리면 빠진 호출만 나간다. 완전 재생은 `WEAVE_LLM_RECORD=replay`(네트워크 0회, 빠진 호출은 즉시 예외).
  녹화 키는 role + sha256(프롬프트)라 **모델 이름이 들어가지 않는다** — record 모드 재사용은 녹화 항목의 `model` 이
  지금 모델과 다르면 miss 로 보고 새로 부른다. 코어 replay 모드는 모델을 안 보므로 replay 로 다른 모델을 돌릴 때는 폴더를 나눈다.
- **승인 필요 지점.** 모든 `--dry-run` 과 테스트는 호출 0회. 아래 "시험 규모"를 넘는 실행(작품 전체 `--target`,
  `counterfactual.py --n` 2 이상, `extract_work.py --run`)은 비용 추정을 보고하고 승인을 받은 뒤에 한다.
  `label_pairs.py` 는 표본이 50쌍을 넘으면 `--run` 없이는 추정만 보여 주고 멈춘다.
- `PYTHONIOENCODING=utf-8`, 가상환경 `.venv`. 저장소 밖 `.env` 를 쓸 때는 키를 출력하지 말고 환경변수로만 넘긴다.

## 갈래 A — `label_pairs.py`

1. **쌍 풀.** k=2..5 마다 "1..k-1화 확정 원장 + k화 사건"을 재생(`judge_eval.load_context` 와 같은 매핑·`timelines=["main"]`·
   knows accumulate·checker 항목, 세계관 규칙은 `first_episode < k` 만)하고 장면 묶음마다 `app.pipeline.pairs.build_pairs`.
2. **표본.** 가설당 전제 상위 `--top-n 24` · 장면당 `--scene-cap 400` · 작품당 `--target 5000` · premise_kind 층화(최대 잉여) ·
   `--seed` 고정. 풀이 목표보다 작으면 전부(WM 은 1,308쌍이 전부다).
3. **이중 라벨.** `--models a,b`(2개) 또는 하나(같은 모델 2회). 두 번째는 전제·가설 순서를 바꾼 변형 프롬프트.
   두 라벨이 같고 판단불가가 아니며(`--min-confidence` 이상) → 그 라벨, 그 밖 → `제외`(사유: 불일치·판단불가·실패·저신뢰).
4. **출력.** `<out>.jsonl`(모순/정상/무관 — `train_judge.py` 가 그대로 읽는다, `case_id = group_id` 가 분할 단위),
   `<out>.excluded.jsonl`(`제외` — 학습에서 뺀다), `<out>.meta.json`(표본·`rates`·일치율·κ·토큰·비용).
5. **보고 필수.** `rates.contradiction_rate_kept`(모순 라벨 비율)와 `rates.excluded_rate`·`disagreement_rate_among_both_ok`.
   원작 원장은 내부적으로 거의 일관하므로 A 의 '모순'은 대부분 오탐일 수 있다 — A 는 정상·무관(관련도) 공급원이고
   참 양성의 주 공급원은 B 다. 불일치를 버리면 어려운 쌍이 빠져 분포가 쉬워지므로 제외율을 숨기지 않는다.

```bash
PYTHONIOENCODING=utf-8 python scripts/eval/label_pairs.py --work q1 --dry-run
```

```bash
WEAVE_LLM_RECORD=record WEAVE_LLM_RECORD_DIR=build/llm_record PYTHONIOENCODING=utf-8 python scripts/eval/label_pairs.py --work wm --target 50 --out build/llm_pairs/wm_trial50.jsonl
```

**dracula** 는 원장이 없다. 먼저 `extract_work.py`(추정 → 승인 → `--run`)로 `build/dracula_ledger/` 를 만든 뒤
`label_pairs.py --work dracula`. 추출 원장은 사람 확정 단계가 없으므로 4코퍼스 원장보다 잡음이 크다.

## 갈래 B — `counterfactual.py`

사례 하나 = 사실 하나 → **모순 장면 1 + 대조 정상 장면 1**(둘 다 LLM 재작성 — FlawedFictions 의 cf_negs. 원문 장면만
음성으로 두면 "재작성 문체 = 모순" 단서가 생긴다. 대조판은 같은 호출에서 위반 대목만 사실에 맞게 고친 최소 편집판).

1. 사실 후보: 1..k화 원장의 상태(alive·body_status·condition·possesses)·지식(retained)·규칙. 점수 = 그 뒤(1..k화 안)
   같은 엔티티가 나오고 사실 내용어를 공유하는 사건 수(결정적). location·일시적 상태 제외. 점수 순위의 `--band 0.3,0.7`
   (중간 중요도)에서 kind 를 시드로 돌려 고른다(지식 쏠림 방지).
2. 생성: (k+1)화 장면 1~3개(1,200~2,500자), k화 끝 2,500자를 문체 참고로. 보존 사실 14개는 지킨다.
3. 자기일관성: 같은 모델이 번호 붙은 사실 목록(대상 + 보존 사실, 시드 섞기) + 장면을 보고 어긴 사실 번호를 답한다(온도 0.7).
   모순판 대상 번호 적중 ≥ 4/5 이고 대조판 유효 응답 ≥ 4, 대상 오탐 ≤ 1 이면 `passed`.
4. 저장: `<out>/<work>/scenes/scene_NN.txt`, `labels.json`(WM labels 형식 + `contrast_group`·`intended_use`),
   `manifest.json`(`human_verified: null` — 사람 검증 큐는 `passed=true` 만), `raw/`.
5. 이후: 장면 파일을 어댑터 추출(`WEAVE_LLM_RECORD=record`)로 사건 JSON 으로 만들어야 쌍·하네스 평가에 들어간다.

```bash
PYTHONIOENCODING=utf-8 python scripts/eval/counterfactual.py --work q1 --k 4 --dry-run
```

```bash
WEAVE_LLM_RECORD=record WEAVE_LLM_RECORD_DIR=build/llm_record PYTHONIOENCODING=utf-8 python scripts/eval/counterfactual.py --work q1 --k 4 --out build/counterfactual
```

원문 위치: Weave 원본(읽기 전용) `%USERPROFILE%/Downloads/event_sourcing/<corpus>/sources/`, WM 은 `WM_1-5.txt` 한 파일.

## 학습 스크립트·접기 — `train_judge.py`

설계안 §2-2·§2-4·§2-6·§3-1. 데이터 준비·분할·샘플링·기준값 로직은 `train_lib.py`(torch 없이 테스트됨,
`tests/eval/test_train_lib.py`), 학습 루프만 torch·transformers 를 지연 import 한다.

1. **입력.** `export_pairs.py --work <w>`(주입 + 실제 장면)와 `label_pairs.py` 출력 JSONL 여러 개를 `--train` 에 함께 준다.
   행마다 `work`·`group_id`(샘플링 단위)·`scene_id`(기준값 단위)가 있다. `--work` 재생은 하네스 `judge_eval.load_context` 와
   같다(원장 상태 해시 일치 — `test_export_replay_matches_load_context`).
2. **접기 분할.** `--val-work V --train-works A B --eval-work E`. 같은 작품이 학습·검증 양쪽이면 에러, `E` 의 행은 학습·검증·holdout
   어디에도 넣지 않는다. 사전 고정 순환(평가→검증): wm→sherlock, q1→wm, q3→q1, sherlock→q3, 학습 = 나머지 둘.
   `work` 없는 행이 섞이면 에러(옛 `export_pairs.py --base` 출력은 다시 내보낸다).
   검증은 기본으로 검증 작품의 실제 장면(`split=real`)만 쓴다(`--val-sources all` 이면 주입·LLM 라벨 행도). 검증 작품의 다른 행은 학습에도 안 쓴다.
3. **'제외' 라벨.** `export_pairs` 의 실제 모순 장면(근거와 같은 엔티티·필드, 근거 사건을 가리키는 쌍, 설정 장면의 규칙 전제)과
   `label_pairs` 의 `<out>.excluded.jsonl` 은 손실·온도·기준값 어디에도 들어가지 않는다(넘겨도 된다).
4. **레시피.** 가중 CE(빈도^−½, 평균 1) · `--label-smoothing 0|0.1` · 에폭마다 무관 ≤ (모순+정상)×2(묶음 라운드로빈, 시드·에폭 고정)
   · `--epochs 6 --early-stop-patience 2`(검증 쌍 NLL) · `--max-length 256` · `--seeds 1 2 3`(검증 장면 recall → 쌍 NLL 로 선택)
   · `--precision fp16`. 기준값은 검증 장면 max p(모순)에서 정상 장면 오탐 ≤ 0.05·recall 최대·동률 높은 값.
5. **기준선.** `--hyp-only`(전제 빈 문자열, G4), `--train-frac 0.25|0.5`(학습 곡선, 묶음 단위·포개짐).
   `--prepare-only` 는 분할 보고만 출력한다(torch 불필요).

```bash
PYTHONIOENCODING=utf-8 python scripts/train_judge.py --train build/pairs_wm.jsonl build/pairs_q1.jsonl build/pairs_q3.jsonl build/pairs_sherlock.jsonl --val-work sherlock --train-works q1 q3 --eval-work wm --out D:/judge/judge_base_wm --prepare-only
```

## 비용 계산식

토큰 = 글자 수 ÷ `chars_per_token`. 금액 = 입력 토큰/1e6 × 입력 단가 + 출력 토큰/1e6 × 출력 단가(1M 토큰당 USD).
단가표는 `datagen_common.PRICES`(Claude 공시 단가), `:free` 모델은 0, 그 밖은 `--price-in/--price-out`.

| 항목 | 호출 수 | 입력 토큰 | 출력 토큰 |
|---|---|---|---|
| A 쌍 라벨 | 2 × 쌍 수 | Σ(시스템 + 전제 + 가설 글자) × 2 ÷ cpt | 2 × 쌍 수 × 95 |
| B 생성 | 사례 수 | 약 2,200 | 약 2,000 |
| B 검출 | 10 × 사례 수 | 약 1,400 / 호출 | 약 100 / 호출 |
| B 장면 추출 | 2 × 통과 사례 수 | 약 17,000 / 장면(추출 프롬프트 31.7K자 + 장면 + 레지스트리) | 약 3,500 / 장면 |
| dracula 추출 | 조각 수(208) | 3.49M | 0.73M |

시험 실행 실측(WM 50쌍, nemotron-3-ultra:free): 호출당 입력 811·출력 95 토큰, cpt 2.4. A 추정은 이 값으로 낸다.
B 값은 dry-run 프롬프트 길이 기준 추정이다(실측 전).

## 시험 규모

A 50쌍(WM), B 1건(Q1 또는 셜록). 테스트: `python -m pytest tests/eval/test_data_tools.py`(LLM 은 FakeLLM).
