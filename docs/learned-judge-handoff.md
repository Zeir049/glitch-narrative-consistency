# 설계·판단 세션 인수인계 — 학습형 judge · 추출 관문

2026-11-07 작성. 지금까지 설계·판단을 맡아 온 세션(Fable)이 사용량 한계로 역할을 넘긴다. 이 문서는 **다음 설계·판단 세션(Opus 5.5)**이
첫 턴에 읽을 것 하나로 충분하도록 쓴다. 코드·문서의 사실은 저장소가 진실이고, 이 문서는 "왜 그렇게 돼 있는가"와 "지금 어디까지 왔는가"만 담는다.

## 1. 세션 역할 체계 (사용자가 정한 것 — 지켜라)

| 역할 | 누가 | 하는 일 | 하지 않는 일 |
|---|---|---|---|
| **설계·판단** | 이 문서를 읽는 세션 | 설계 결정, 지시문 작성, 보고 판독, 문서 개정·커밋(docs만) | 코드 수정, 테스트 실행, 병합 |
| 구현 | Opus 작업 세션(각자 워크트리·브랜치) | 지시문대로 코드·테스트 | 범위 밖 파일, push, `weave_core/` |
| 검증·병합 | 검증 세션(지금은 "검증: I 브랜치…" 세션을 재활용 중) | 브랜치 검증 보고서 → 사용자 승인 뒤 main 병합 | 코드 수정 |
| 설명·정리 | "정리·설명: 학습 judge 프로젝트 상태 안내" 세션(Opus, 읽기 전용) | 사용자에게 상태 설명, 선택지 정리 | 결정 |

- 사용자는 설명 세션에서 상의하고 **결정은 설계·판단 세션에서** 받는다. 설명 세션이 `[사용자 요청]`으로 판단을 보내오면 결정해서 회신한다.
- 세션 시작은 사용자의 말로만. 다른 세션의 메시지로 시작된 턴에서는 `start_session`이 거부되니 `own_initiative` 카드로 제안한다.
- 유료 API 키는 **검증 단계에만**(사용자 결정). 학습 데이터 생성에 유료 LLM 금지. 로컬 LLM(RTX 3060 12GB, llama.cpp)은 가능하되 7B는 품질 부족, Qwen3-14B는 갈래 A만 가능.
- 다운로드·설치(수 GB)는 작업 세션이 **사용자에게 직접** 승인을 받는다. 설계 세션이 대신 승인하지 않는다.
- Postgres 테스트 DB는 세션별 `TEST_DB_NAME`으로 분리해 돌린다(동시 실행 충돌 전례 2회).
- 커밋 attribution: Fable 세션은 `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`; Opus 세션은 자기 모델명으로.

## 2. 목표와 주장 수위 (계획서 요약)

판정 단계의 LLM을 학습 모델(klue/roberta 쌍 분류기)로 바꾸고 추출 관문을 두는 작업. 발표 주장은 **R2**(제품 동결 열 Recall 0.30 대비
비열등, 대응 bootstrap 하한 > −10%p)이고 "LLM보다 낫다"·"작가 검수 없이"·"문헌 수준"은 쓰지 않는다. 내부 목표는 recall 0.5·정상 장면 오탐 ≤ 0.05.
근거 문서: `docs/learned-judge-plan.md`(사전 확정 표 — 결과 본 뒤 못 바꿈), `learned-judge-decisions.md`, `learned-judge-prior-work.md`,
`learned-judge-training-design.md`(**§0 개정 1~6이 본문을 덮어쓴다** — §0부터 읽어라), 검증 기록 `learned-judge-design-review.md`·
`learned-judge-branch-review-{DF,G,I}.md`, `EVAL.md`, `scripts/eval/README.md`, `local-llm.md`, `related-work-extraction-verification.md`.

## 3. 지금 상태 (main 9a09999 기준)

main에 들어간 것: 녹화·재생(`app/pipeline/llm_record.py`), judge 층 하네스(`scripts/eval/judge_eval.py`, 4작품 165장면, 동결 열 18/60·3/60 재현 고정),
추출 관문(`extract_gate.py`, `WEAVE_GATE_MODE`), 학습 judge 연결(`pairs.py` v2 할당량 24·관련도, `learned_judge.py` Fake/HF),
학습 데이터 도구(`inject.py`, `export_pairs.py --work`, `label_pairs.py`, `counterfactual.py`), 학습 스크립트(`train_lib.py`·`train_judge.py`:
접기 분할·제외 라벨·가중 CE·장면 단위 기준값), 회귀 게이트 둘, WM 재생 timeline 수정(I). 전체 테스트 462 통과. **실제 학습·측정 결과는 아직 없다.**

진행 중·대기:
- **규칙 중복 제거 수정**(브랜치 `claude/judge-rules-dedup`, 커밋 59c3b48·1dad041·04e7bf8): 하네스가 세계관 규칙을 rule_id로 중복 제거해
  Q3 3·4·5화의 RULE_007(본문 셋 다 다름) 등이 빠지던 결함. 본문 정규화 기준으로 수정, 규칙 수 Q1 3→4·Q3 38→41, recorded 불변, 근거 포함 68/75.
  **검증 통과 → main 77bf021에 병합 완료**(검증 기록 `learned-judge-branch-review-dedup.md`, 6cdd8cd). 틀린 항목 없음. Q1 scene_32 근거 1건이 할당량 넘김 부수 효과로
  빠진 것은 조립 결함이 아님. 검증 세션이 J에 알렸다 — J는 main을 머지한 뒤 G0·내보내기를 한다.
- **J: 첫 실제 학습**(세션 "J: 첫 실제 학습 — …", 브랜치 `claude/pensive-wing-…`): 설계안 §0-6 그대로 — `.venv-train`(CUDA torch, 승인 필요) →
  정밀도·어텐션 통일(fp16 `model.half()`·eager, meta 기록, `HFBackend`가 읽음) → G3 기초 모델 보존 → `judge_latency.py` → 주입 누수 규칙 →
  관문 G0·G1·G2·G3 → 1접기(평가 **Q3**, train WM+셜록, val Q1, base, 시드 3). 평가 작품은 G6 전에 돌리지 않는다.
  J에는 "규칙 중복 제거가 main에 들어온 뒤 main을 머지하고 G0·내보내기를 하라"고 알려 두었다.
- 설명 세션이 짚은 확인 사항: dedup 보고의 커밋 `40c4763`은 쪼개기 전 단일 커밋이고 지금 브랜치 끝은 `04e7bf8` — 불일치 아님(주인 세션이 설명).

## 3-1. J 첫 접기 결과 (2026-11-07, 브랜치 `claude/pensive-wing-5159d2` HEAD af71e53, 미병합) — **다음 설계 세션의 첫 과제**

J는 설계안 §0-6을 끝까지 수행했고 사용자 지시로 멈췄다(가설만 기준선 G4 학습 2에폭에서 중단). 코드 1~4는 커밋됨(HFBackend meta 반영, train_judge 서비스 조건 검증,
`--nli-from`, `judge_latency.py`, `score_pairs.py`, `pair_coverage --tokenizer`, `inject.py --registry/--work`, roberta-small 튜플 버그 수정, GPU max_length 384).
환경 `.venv-train`(torch 2.9.1+cu128), 모델·데이터는 `C:\judge_models\`(저장소 밖).

| 관문 | 결과 |
|---|---|
| G0 | 근거 포함 68/75 통과. 토큰 p95 WM 222 / Q1 348 / 셜록 320 → 256 초과로 1차 정지 → **설계 결정: GPU max_length 384**(발췌 160자 유지, 설계안 §0-6) → 통과 |
| G1 @384 | GPU fp16: Q1 실제 쌍 5,830개 base 24.4 s(+26%)·large 70.0 s. 30사건 회차 base 6.3 s(@256). CPU 대체 경로 small 116 s·base 233 s — 미달 기록 |
| G2 | 테스트 사본 가중치에서 통과. **학습된 모델에서는 미측정** |
| G3 영 학습(KLUE-NLI 기초, val Q1) | 장면 recall 2/20 = 0.10 @FPR 1/25, 쌍 PR-AUC 0.028(= 양성 비율, 신호 없음) |
| 학습(base, train WM+셜록, val Q1, 시드 3) | **Q1 장면 recall 0.05 / 0.05 / 0.00**, PR-AUC ≈ 0.037. 학습 손실 0.25→0.02인데 검증은 1~2에폭 뒤 나빠짐 → 주입 템플릿을 외우고 실제 Q1 쌍으로 전이하지 않음. 보정만 개선(ECE 0.011/0.024) |
| 선택 모델 | 시드 2, 온도 1.345, 장면 기준값 0.857, `C:\judge_models\judge_base_q3\`(해시 ed158228…). 기초 모델 `nli_klue_roberta_base\_nli\`(KLUE-NLI val 0.857). 3시드 59분 |
| G4·G5·G6 | G4 미판정(중단), G5 범위 0.05(형식상 통과), G6은 Q1 동결 열 0/20 대비라 의미 없음 |

**첫 과제 — 학습이 안 된 원인 판단과 다음 수.** 후보 가설(검증 전): (a) 실제 양성 쌍이 접기당 163개뿐이라 데이터 부족 — 학습 곡선(`--train-frac`)과
G4(가설만 기준선)로 확인. (b) 주입 템플릿(STATES 8·RULES 4·SECRETS 4)이 단조로워 외움 — 템플릿 ≥12·연산자 4개 추가(설계안 §3-1 미구현). (c) 쌍 형식이
구조 템플릿(엔티티.필드=값)이라 KLUE-NLI 전이가 안 됨 — 가설·전제를 자연문으로 바꾸는 변형, 또는 영어 서사 모순 쌍 번역(≈940) 중간 단계.
(d) 장면 단위 기준값 선택이 정상 25장면 중 오탐 ≤1에서 recall 최대라 추정이 거칠다 — 쌍 단위 지표(PR-AUC 0.037)도 낮으니 주 원인은 아님.
(e) 라벨 잡음: "명시 안 됨 = 무관" 근사와 '제외' 규칙. 결정 순서 제안: G4·학습 곡선부터(싸다) → (b)+(c) 중 하나를 1접기로 재시험 → 그래도 안 되면
로컬 Qwen3-14B 갈래 A(κ 0.38) 또는 번역 데이터. 발표 주장은 R2(동결 열 대비 비열등)이고 Q1 동결 열이 0/20이라 "비열등"은 형식상 성립하지만
**그렇게 주장하지 않는다** — 정직한 보고는 "첫 접기에서 학습 judge는 영 학습 기준선(0.10)보다 낮았다"이다.

J 브랜치는 검증 뒤 병합 대상(코드 1~4는 쓸 만하다). 검증 세션("검증: I 브랜치…")이 idle로 남아 있어 재활용 가능.

## 4. 미뤄 둔 결정 (J 결과를 보고 정한다)

1. **추가 데이터**: 학습 곡선(25·50·100%)과 G4(가설만 기준선 대비 PR-AUC ≥ 0.10)·G5(시드 범위 ≤ 0.20) 결과로. 후보 순서: 영어 모순 쌍 번역(FlawedFictions 817 + ContraDoc 125, 로컬 7B 번역 가능) → 주입 연산자 4개·템플릿 ≥ 12 → Qwen3-14B 갈래 A(κ 0.38) → 갈래 B는 프롬프트 분할(계획→장면, 대조 별도) 뒤 재시험.
2. **large 상한**: base 1접기가 G6을 넘으면 large로 상한 측정(12GB fp16·256·배치 8).
3. **나머지 접기**: 4접기 교차 적합 → WM·Q1·Q3 양성 60의 합산 예측으로 R2 bootstrap. `judge_eval --model-dirs` 필요(미구현).
4. **R1 실험**: 관문 off/annotate/exclude 결함 주입 비교. 주입 분류표는 `related-work-extraction-verification.md` §4-2의 재귀속(원장 부재 13건 중 진짜 누락 2) 기준으로 먼저 고정.
5. **R3**: judge 층 회귀 게이트에 실제 모델 값 고정, 결과 JSON에 모델 해시·판·precision.
6. **후속 결함**: `HFBackend` meta 반영(J가 함), `inject.py` registry_live(J가 함), 녹화 키에 모델명, 초 경계 플레이크 후보 테스트 점검.

## 5. 판단할 때 쓰는 기준

- 숫자는 **[측정]/[기록]/[추정]** 꼬리표. 지표 단위가 다른 수치를 "문헌 수준"이라고 비교하지 않는다.
- 평가 누수: 평가 작품의 회차·장면·레지스트리는 어느 접기에서도 학습 데이터(주입 포함)에 쓰지 않는다. 사전 확정 표는 결과 본 뒤 바꾸지 않고, 바꾸면 개정 이력에 남긴다.
- 코퍼스는 추출된 조건으로 재생(WM = Weave STATE_TIMELINES, 나머지 main). 코퍼스 파일은 고치지 않는다.
- 검증 세션 보고의 "확인됨/틀림/근거 부족/조건 차이" 판정을 그대로 받아 문서에 반영한다. 설계 문서는 설계 세션만 고친다.
- 다른 작업 계열(추출 검증기 "LLM 추출 검증기 독립 개발", "LLM 없는 추출기 또는 검증기")이 같은 저장소에서 돈다. `related-work-extraction-verification.md`·`docs/verifier*`는 그쪽 문서. 하네스(`judge_eval.py`)를 건드리는 변경이 거기서 나오면 J·게이트와의 겹침을 먼저 본다.

## 6. 메모리

`%USERPROFILE%\.claude\projects\C--glitch-integrated-noai\memory\`의 `learned-judge-progress.md`(진행 로그), `session-role-judge-only.md`(역할),
`weave-eval-baseline.md`(Weave 원본 저장소·코퍼스·기준선), `user-hardware-gpu.md`(3060). 새 세션은 같은 폴더를 쓰므로 그대로 이어 적는다.
