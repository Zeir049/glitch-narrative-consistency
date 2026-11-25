# 관련 연구 — LLM 추출기의 검증 (일반 정보추출 + 장편 서사)

2026-11-06. 판단 세션이 조사·정리했다. 배경은 [`learned-judge-plan.md`](learned-judge-plan.md)의 R1(관문이 거르지 못하는
누락·환각·오매핑)과 [`learned-judge-decisions.md`](learned-judge-decisions.md) §2·§8(추출이 성능 상한이라는 실측).
"측정"은 이 세션이 Weave 원본 저장소(`%USERPROFILE%\Downloads\event_sourcing`, main 9180c3a) 데이터로 직접 잰 것,
"기록"은 논문·문서에 적힌 것. 측정 스크립트는 [`scripts/eval/probe/`](../scripts/eval/probe/README.md)에 보존했다(2026-11-07, 재실행해 같은 수치 확인).

## 0. 한 줄 결론

**장편 서사 상태 추출기의 산출물을 라벨로 직접 검증한 선행은 없다.** 있는 것은 (a) 추출 산출물을 쓰는 하류 과제
(요약 사실성·플롯홀·생성 일관성)의 평가, (b) 추출 품질의 대리 지표(장면 간 반복, 안정성, LLM 점수), (c) 검증 없는 상용 도구다.
검증 수단은 영역을 가리지 않고 세 가지로 수렴한다 — 장면·샘플 간 반복, 원문 대조(인용·NLI), 결정적 규칙(흡수 상태·스키마).
이 저장소는 블라인드 이중 라벨 165장면·미탐 귀속·반복 추출 3벌을 갖고 있어 추출 층을 직접 검증할 재료가 있다.

## 1. 장편 서사 영역

| 연구·도구 | 추출 대상 | 추출을 어떻게 검증하나 (기록) | 이 저장소와의 관계 |
|---|---|---|---|
| **NarrativeFactScore** (Jeong 외, EMNLP 2025) | 캐릭터 지식그래프 — 별칭 병합 names graph + 장면별 (주어, 술어, 목적어) | 장면별로 여러 번 샘플링(self-consistency), 같은 (인물쌍, 술어)가 **τ회 이상 반복**될 때만 간선 유지. τ값·정밀/재현 미공개. 유일한 직접 검사는 Conan 데이터셋 BERTScore 95.65(단일 추출 86.26). 간접 검사 = FABLES·STORYSUMM 사람 사실성 점수와 Spearman 0.47·0.43 | 반복 추출 일치 필터와 같은 발상. 우리 측정([`EVAL.md`](EVAL.md) 반복 추출 일치도)에서 슬롯 J 0.78·0.57, **값 J 0.01** — 자유 서술 값에는 그들 임계를 그대로 못 쓰고 슬롯 단위로만 |
| **FactTrack** (Lyu·Yang·Kong·Klein 2024) | 아웃라인 사건 → pre/post 원자 사실 + [0,1] 유효 구간 | 사람 라벨링을 **포기**(비용·노이즈·주석자가 GPT-4 사용) → GPT-4 1~5점 대리 평가. 모순 판정 = Contriever 검색 → DeBERTa NLI(GPT-4 라벨 18.7k쌍 학습, P≈R≈0.6), **두 체크포인트 모두 모순일 때만** 보고. 저자가 꼽은 주 병목 = 분해(추출) 오류. ContraDoc F1 57.3(직접 프롬프트 10.5) | 원장 구조(from/to·since·superseded)와 가장 가깝다. 두 체크포인트 규칙은 보존 검사(§4)의 오탐 억제에 참고. 그들이 못 한 사람 라벨을 우리는 갖고 있다 |
| **FlawedFictions** (Ahuja·Sclar·Tsvetkov 2025) | 1막 명제 추출 → 부정 → 2·3막 재작성(플롯홀 벤치마크) | 명제에 **중요도 1~4점**을 매겨 중간만 채택, 변경 5개 초과 폐기, LLM 심판 5회 중 4회 합의, Prolific 3인 다수결(약 300건 중 207건 수용 ≈ 70%). 모델은 평균 2,700단어에서 거의 무작위(always-no 0.51 대 최고 0.53) | 주입 생성기(`scripts/eval/inject.py`)와 같은 발상. 명제 중요도 필터·4/5 합의·사람 다수결 절차를 주입 데이터 검수에 빌릴 수 있다 |
| **SCORE** (2025) | 사물 상태 {active, lost, destroyed} | 추출 검증 **없음**. lost/destroyed를 흡수 상태로 두고 active 복귀를 연속성 오류로 표시, 이전 상태 유지. 평가는 GPT 생성 스토리 1,000편에서 LLM 채점(상태 추적 제거 시 일관성 89.7→72.1) | checker(생사·위치)와 같은 규칙형. 검증 부재 |
| **TLDM — Too Long, Didn't Model** (Cornell 2025) | 인물 최종 위치·경과 시간·줄거리 | 정답 없음. 장 단위 출력을 이어 붙인 것과 전체 입력 출력의 **안정성**만 비교. 소설 40권, 64k 토큰 넘으면 7개 프론티어 모델 전부 불안정 | 회차 단위 추출이 맞다는 근거. 안정성 지표는 우리 일치도 측정과 같은 류 |
| **FABLES** (Kim·Chang 외 2024) | 책 요약의 주장 3,158건(26권) | 사람 주석($5.2K), 근거 인용 필수. 자동 검증기는 "책 전체 + 주장 하나"를 긴 문맥 LLM에 넣는 방식이 최선, 대부분 다중 홉 필요 | 검증기를 LLM으로 만들 때의 비용·상한 |
| **NstAgent** (2026 preprint) | 서사 상태(인물·과거 사건·향후 요구) | 초록만 확인. 상태 검증 언급 없음, 평가는 일관성 벤치마크 확장 | 생성용. 참고만 |
| **Auto-Graph** (오픈소스) | 인물(이름·별칭·위치·생사·관계)·타임라인·플롯 스레드 상태 파일 | LLM 연속성 검사 워커가 장별 델타를 내고 코드가 결정적으로 병합. **추출 검증 없음**, 검사는 비차단, 검사기는 최근 5장만 본다 | 상태 파일 스키마가 우리 닫힌 어휘와 유사. 검증 부재의 전형 |
| **Granthika**(2017~), **Book Check Genie**, Sudowrite Story Bible, Novelcrafter Codex | 스토리 바이블·지식그래프·연속성 경고 | 전부 사람 유지보수 전제. 측정 공개 없음 | 상용 선례는 있다 → "최초"는 못 쓴다 |
| 국내 | 노벨나라 "이중 검증 게이트" 등 | 생성 품질용. 설정 모순 전담 서비스·한국어 웹소설 연구 미발견 | 한국어·웹소설 특화 선행 없음 |

**공통 패턴**
- 추출 층을 직접 검증한 연구가 없다. 전부 하류 과제 상관이나 LLM 대리 점수로 간접 확인한다. 사람 라벨을 시도한 FactTrack은 포기했다.
- 분해·추출 오류가 병목이라는 자백이 공통이다(FactTrack 명시, TLDM 길이 불안정, FlawedFictions 긴 글에서 무작위).
- 서사 특화 검증 수단은 없다. 일반 정보추출과 같은 세 수단을 쓴다.

## 2. 일반 정보추출 영역

| 갈래 | 대표 | 핵심 (기록) | 이 저장소에서 |
|---|---|---|---|
| 스팬 앵커링 + 인용 대조 | AEVS(MDPI Computers 2026), FullCite(arXiv 2606.07130), llmground | 항목마다 원문 글자 범위를 묶고 문자열 대조로 환각을 거름. 부분 일치만 LLM. 문서 수준 귀속은 쉽고 **스팬 수준이 어렵다** | `source_text` 필수·`locate()` 존재. 단 §4-1 측정: 이 추출기의 인용은 환각이 아니라 이어 붙인 문장이라 **등급 신호 가치 없음** |
| 반복 샘플 일치 필터 | 속성 추출(AIMS 2026), UMD 강의 자료, 일치 기반 교정 논문 | k회 추출해 한 벌에만 나온 항목을 걸러냄. "일관된 환각"은 못 잡고 recall을 깎는다. 손실을 수치화한 연구는 없음 | 슬롯 단위만 가능(값 J 0.01). 비용 2배 |
| 반복 추출(gleaning) | arXiv 2404.04068, GraphRAG(권장 1~2회), ROGRAG | "아직 안 뽑은 것을 더 찾아라" 반복. 첫 1회가 이득의 대부분, 3회부터 정체·환각 지표 악화(bias avoidance 0.56→0.46) | 진짜 누락(§4-2: 13건 중 2건)에만 효과. 1회 상한 |
| 자기 검증과 한계 | CRITIC, Chain-of-Verification, SycEval | 외부 피드백 없는 자기 수정은 불안정. 유도 질문은 아첨 편향(틀린 쪽으로 번복 14.7%) | "X를 놓쳤나?"식 재질문 금지. 문장 묶음만 주는 중립 재추출 |
| 교차 모델·NLI 검증 | ICLR 2026 fragment-level verification, Cross-Model Disagreement(arXiv 2603.25450), FActScore 계열 | 다른 계열 모델은 오류 상관이 낮다(ρ 0.54 대 0.77). 함의 모델로 "인용 ⊨ 주장" 채점 | 세션 C의 klue/roberta 쌍 분류기와 같은 틀. 후순위 |

## 3. 빌릴 것 · 빌리지 말 것 · 주장 수위

| | 항목 | 어디에 |
|---|---|---|
| 빌림 | FactTrack의 pre/post 사실 + 유효 구간, 두 체크포인트 규칙 | 보존 검사·원장 시점 조회의 오탐 억제 |
| 빌림 | NarrativeFactScore의 반복 임계 τ — **슬롯 단위로만** | 반복 추출 일치 필터(비용 허용 시) |
| 빌림 | FlawedFictions의 명제 중요도 필터, 4/5 합의, 사람 다수결 | 주입 데이터 검수, "승격 후보" 선별 |
| 빌림 | FABLES의 "주장마다 근거 인용 필수" 주석 규격 | 이미 코퍼스 `evidence_quotes`가 같은 규격 |
| 안 빌림 | GPT-4 대리 점수를 기준선으로 | R2 기준선은 사람 라벨(동결 열 재현값) |
| 안 빌림 | 사건 단위 통째 인용으로 커버리지 계산 | 1,443자 문단 인용이 모든 문장을 "덮음"(§4-1) |
| 수위 | "최초" ✗ → "추출 층을 사람 라벨로 직접 검증한 사례" ○ | Granthika·Book Check Genie가 상용 선례 |

## 4. 이 세션이 측정한 것 (2026-11-06, Q1·Q3 nemotron 원장 `ledger_nemotron_ultra/` + `chunks/` 원문)

### 4-1. 검증기 제안의 전제 검증

| 전제 | 측정 | 판정 |
|---|---|---|
| 인용 대조로 환각을 거를 수 있다 | 통째 문자열 검사로는 항목의 12~20%가 "못 찾음". 문장 단위로 쪼개고 따옴표·줄표를 정규화하면 **Q1 5/530(0.9%), Q3 1/574(0.2%)**. 못 찾는 이유 = 떨어진 문장을 이어 붙인 인용, 따옴표 차이, `...` 생략 | 등급 신호에서 제외. 순진 구현은 상태 변화 12%를 잠정으로 떨어뜨려 recall 손실 허용치(5%p) 초과 |
| 수치 문장 미커버 탐지기가 누락을 잡는다 | 항목 단위 인용 기준 수치 문장 미커버 **Q1 회차당 36문장, Q3 54문장**. 커버됐으나 구조값에 수치가 없는 경우 회차당 18~20건. 원장 부재 13건 중 발화 6(Q1 1, Q3 5), 그중 우연 일치 2 | 장르 의존 큼. 주 수단 아님. "수치 보존 검사"(요약 손실 탐지)만 유효 |
| 엔티티 커버리지(별칭이 본문에 있는데 참조 없음) | 미참조율 29~31%, 대부분 장소. 레지스트리에 없는 사물(사진)은 원리상 못 잡음 | 보조 신호 |

재현: 원문 = `corpus_*/chunks/episode_NN_c*.txt` 연결, 원장 = `ledger_nemotron_ultra/episode_NN.json`의
`state_changes·assumed_states·knowledge_gained[].source_text`(사건 단위 `source_text`는 제외), 정규화 = 공백·따옴표·줄표 제거,
인용은 `...`·`…`·`/`로 쪼갠 뒤 문장 단위로 다시 쪼개 부분 문자열 검색. Windows 콘솔은 `sys.stdout.reconfigure(encoding="utf-8")` 필요.

### 4-2. "원장 부재 13건" 재귀속 (2026-11-07 정정판 — 사람 확인 필요)

Weave 종단 측정(원본 저장소 `handoff/final_report.md`·`longitudinal_report.md` §2-7, 미탐 귀속 "원장 부재 13 / 판정자 24")의 13건.
첫 판(11-06)은 원장 항목을 키워드로 뒤져 "자유 서술 칸에 묻힌 7건"을 보존 손실로 봤으나, **Weave 진단 파일
`handoff/longitudinal_miss_diagnosis_nemotron_ultra.json`의 `gold_in_sections`를 보면 정답 사건 대부분이 판정 패킷의 (d) 지식 절에 이미 실려 있었다**
(knows accumulate가 `knowledge_gained`를 (d)절로 나른다). "원장 부재"(S5b)는 인용 하나라도 사건 매핑이 없으면 다른 분류보다 먼저 붙는 꼬리표라
"패킷에 없음"과 같은 뜻이 아니다. 아래가 정정판이다.

| 분류 (진단 파일 `gold_in_sections` 기준) | 건수 | 장면 (정답 사건 → 실린 절) |
|---|---|---|
| 정답 사건이 패킷 (b)(c)(d)(e) 어딘가에 있음 → **판정자 미탐** | 10 | Q1 s03(5_2→cd)·s09(3_14, 3_3→d)·s13(3_14→d)·s22(3_6→de)·s35(1_3→d, 규칙은 (b)), Q3 s04(1_15→de, 3_4·5_9→cd)·s05(1_5, 3_2→d)·s28(1_9→d, 4_7→de)·s36(2_4→d)·s40(4_1→cd) |
| 정답 사건이 어느 절에도 없음 → **진짜 보존 손실** | 3 | Q1 s21 EVENT_1_4(요금 600 — `assumed_states`에만), Q3 s04 EVENT_3_5, Q3 s42 EVENT_5_4(종이 일지) |
| 매핑된 사건 없음 (규칙 40분은 (b)에 있음) | 1 | Q3 s13 |

주의:
- `evidence_event_map.json`은 문자열 대조 후보이고 파일 스스로 "사람 확인 필수"라고 적는다. Q1 s13(사진 위치)은 원장 어디에도 사진 항목이 없는데(키워드 0)
  지도는 EVENT_3_14가 (d)에 있다고 하므로 매핑 오류일 가능성이 크다. 그래서 위 표도 사람 확인 전엔 근사다.
- "패킷에 있다"가 쓸 만한 형태라는 뜻은 아니다. s35는 "월 2회 휴무"로 숫자가 빠진 채 (d)에 있었다(수치 손실, §4-1).
- 제품 어댑터 경로도 같다: `episode_to_delta`가 `worldview_rules`를 캐논 델타에 넣고 `judge_registry`가 캐논+델타에서 (b)절 규칙을 만든다.
  `NEW:` 라벨은 `RULE_U###`로 실리므로 레지스트리 미해소(`unmapped_new`)와 무관하다.

함의(정정):
- S5b 귀속으로 "R1 주입 분류표"를 뽑으면 안 된다(`learned-judge-plan.md` 사전 확정 항목 수정 대상). 13건의 2/3 이상이 근거가 패킷에 있는 판정자 미탐이다.
- 재추출로 살릴 것은 거의 없다. 보존 검사의 표적은 첫 판의 "자유 서술 칸 7건"이 아니라 **`assumed_states`에만 있는 지속성 사실**
  (같은 수치·사물 상태가 `knowledge_gained`·`state_changes`·규칙 어디에도 없는 것)로 좁힌다. 규모는 3건 안팎이고, LLM 0회·결정적으로 검사할 수 있다.
- 첫 작업은 검사기 구현이 아니라 **전제 확인**이다: `scripts/eval/judge_eval.py` 하네스(동결 열 재현 = 제품 경로 패킷)가 만드는 패킷에서 13건의
  정답 사실이 어느 절에 어떤 문장으로 실리는지 사람이 읽어 "assumed_states에만 있음" 집합을 고정한다. 검사기는 그 집합에만 맞춘다.
- 승격 후보를 판정 원장에 넣는 경로는 미설계이며 Weave 원장 개선(round2-graph)과 겹친다 — AI 담당과 조율.

#### 4-2-1. 2차 보완 (2026-11-07, 구현 세션 T1 판독 — `C:\glitch_extract_verifier/docs/t1_premise_check.md`, 사람 확인 전)

구현 세션이 제품 경로 패킷을 LLM 0회로 재구성해 13건을 **문장 단위로** 읽었다. 위 정정판은 진단 파일의 `gold_in_sections`(정답 사건 **ID**가 절에 있는지)에 의존했는데,
**ID가 있어도 그 줄이 같은 사건의 다른 사실인 경우가 5건**(Q1 s03·s09·s13, Q3 s05·s28)이라 판정자 미탐을 과대(10→6), "어디에도 없음"을 과소(0→4) 셌다.
판단 세션이 Q1 s21·s35, Q3 s04·s05·s28·s42를 원장에서 표본 대조해 판독이 맞음을 확인했다.

| 분류 (T1 판독) | 건수 | 장면 | 검사기 표적? |
|---|---|---|---|
| 판정자 미탐 (정답 **문장**이 패킷에 있음) | 6 | Q1 s22·s35, Q3 s04·s13·s36·s40 | 아니오 |
| `assumed_states`에만 — 수치형 | 2 | Q1 s21 요금 600, Q3 s28 UPS 45분 | **예** (`assumed_only_persistent`) |
| `assumed_states`에만 — 비수치 배타 사실 | 1 | Q3 s05 "콘솔은 도현뿐" | 실험 신호(`assumed_only_exclusive`), 지표 분모 밖 |
| 엔티티 **선언**(description)에만 | 1 | Q3 s42 종이 일지 (s28 45분도 OBJ_015 description에) | **예** (`declaration_only_persistent`) |
| 사건 인용·요약에만 (구조값 어디에도 없음) | 2 | Q1 s03(state_change 인용), Q1 s09(사건 요약 + ERS 미선별) | 아니오 — 비수치, 검색 문제 |
| 매핑 오류 + 진짜 누락 | 1 | Q1 s13 사진 위치 | 아니오 |

바뀐 것: Q3 s04 EVENT_3_5는 손실이 아니다(EVENT_3_4가 (c)(d)로 운반). Q1 s35는 (b) 규칙에 "첫째·셋째"가 있어 (d)의 "월 2회"는 손실이 아니다 —
`number_lost`는 **회차 전체에서 한 번이라도 수치가 보존되면 끈다**. 제품 경로와 동결 열 패킷은 13건 모두 같은 분류(차이는 (c) knows 전 이력만).
결정(판단 세션, 11-07): 1단계 신호 다섯 개와 T3 분모는 [`extract-verifier-brief.md`](extract-verifier-brief.md) §3에 반영. 13행 표의 사람 확인은 사용자 몫으로 남아 있다.

## 5. 학습 데이터 후보 (2026-11-06 밤 조사·실측)

학습 judge([`learned-judge-training-design.md`](learned-judge-training-design.md))의 쌍 형식은 (전제, 가설, 3클래스 모순·정상·무관)이고
가설은 사건 요약·`source_text`·전제 항목, 전제는 원장값·since·규칙·지식이다. **사용자 결정(2026-11-06): 당분간 한국어로 간다** — 기반 모델
klue/roberta 유지, 다국어 모델 전환 없음. 따라서 아래 영어 자료는 **오프라인 번역을 거쳐야만** 쓸 수 있다.

> 겹침 주의: 같은 날 다른 판단 세션이 쓴 [`learned-judge-prior-work.md`](learned-judge-prior-work.md)가 judge 쪽 선행(FactTrack·MiniCheck·
> FlawedFictions·ConStory-Checker·NoCha·LiteReason)과 **데이터 전략 A(LLM 쌍 라벨링 1~2만) · B(한국어 반사실 회차)** 를 다룬다.
> 이 절은 추출 검증기 조사에서 나온 **공개 데이터셋의 실파일 확인**에 한정한다. 두 문서의 데이터 권고(저쪽: LLM 라벨 대량 생성 + large 인코더,
> 이쪽: 사람 검증 영어 쌍 번역을 중간 단계로 + 외부 데이터 없이 첫 실험)는 설계 세션에서 하나로 맞춰야 한다.

### 5-1. 후보와 판정

| 데이터 | 규모·라벨 | 쌍 형식 적합성 | 언어·라이선스 | 판정 |
|---|---|---|---|---|
| **FlawedFictions** | 사람 검증 양성 207(단편) + 97(장편), 음성 207+103. 양성마다 오류 문장·충돌 원문 문장 | **가장 가깝다.** (오류 문장, 충돌 문장) → 모순 쌍. 장편 연속성 오류라는 과제가 동일 | en · MIT | 중간 학습 1순위 |
| **ContraDoc** | 자기모순 문서 449 + 비모순 442. doc_type story/news/wiki. `evidence`(모순 문장)·`ref sentences`(충돌 문장)·`scope`(global/local/intra) | (evidence, ref) → 모순 쌍. scope는 우리 전제 kind(ledger·scene·past)와 대응 | en · Apache-2.0 | 중간 학습 2순위, story 부분만 |
| **FactTrack** NLI 쌍 | GPT-4 라벨 원자 사실 쌍 약 18.7k(2클래스). 아웃라인은 LLaMA2 생성 | 입도는 우리 원장 사실(pre/post·from/to)과 가장 비슷. 2클래스라 정상·무관 구분 불가 | en · 라이선스 없음 · Google Drive | **보류** |
| **FABLES** | 주장 3,158 × 사람 라벨 + 근거 인용, 실제 소설 26권 | (주장, 인용, 라벨) 함의 쌍. "No"가 모순이 아니라 미지원 | en · MIT | 검증기(인용 ⊨ 주장) 평가용. judge엔 후순위 |
| **Conan** | 추리 서사 24편, 인물 관계 그래프 사람 주석 | 관계 추출 평가용. NLI 쌍 아님 | zh(추리 게임) | 추출기 검증 **평가** 참고만 |
| NarrativeFactScore / SCORE / TLDM / NstAgent / Auto-Graph | 새 라벨 데이터 없음 | — | — | 없음 |
| gswamy/plot_holes (HF) | 15,751 (프롬프트, 원본, 반사실 이야기) | 이야기 단위, 문장 쌍 아님, 미검증 | en | 주입 생성기 참고 정도 |
| **KLUE-NLI** | 학습 24,998 / 검증 3,000, 사람 작성 | 일반 NLI 3클래스 | ko · CC BY-SA 4.0 | 기초 학습 단계(계획대로) |
| **KorNLI** | SNLI·MNLI·XNLI 기계번역 학습셋, dev·test 사람 번역 | 일반 NLI 3클래스 | ko · CC BY-SA 4.0 | 기초 단계 양 보강(기계번역 잡음) |

한국어 서사 모순 쌍 데이터는 조사 범위에서 **없다**. 양성의 주 공급원은 변함없이 Weave 4작품 코퍼스 교차 적합 + 주입 생성기(`scripts/eval/inject.py`).
AI Hub·모두의 말뭉치의 소설 코퍼스는 조사하지 않았다(모순 라벨은 없을 것이고 이용 약관 제약 있음).

### 5-2. 내려받아 확인한 것 [측정] — 복제본은 세션 임시 폴더, 저장소에 넣지 않음. 코드는 실행하지 않고 데이터만 파싱

| 자료 | 실파일 라이선스 | 실제 내용 | 뽑을 수 있는 쌍 |
|---|---|---|---|
| FlawedFictions (HF `kahuja/flawed-fictions`, parquet 4.2MB) | MIT(저장소·데이터 카드 모두) | 필드 `story, cont_error, cont_error_expl, cont_error_lines, contradicted_lines, example_id`. 단편 414행(양성 207, 중앙 522단어), 장편 200행(양성 97, 중앙 2,212단어). 양성의 오류·충돌 문장은 `<li>` 목록(건당 중앙 1개). `_cf_negs`·`_error_resolved_negs`는 양성이 main과 같은 이야기(154/207 확인)고 음성만 다름. **음성에는 문장 주석이 없다(NA)** | 사람 검증 모순 쌍 **526(단편) + 291(장편) ≈ 817**. 무관 쌍은 같은 이야기의 다른 문장. "정상" 쌍은 음성에 문장 주석이 없어 양성 문장을 음성 텍스트에 정렬해야 한다(같은 이야기라 문장 유사도로 가능). 두 음성 분할(오류 해소판·반사실 일관판)은 우리 정상 클래스의 어려운 음성과 성격이 같다 |
| ContraDoc (`ContraDoc.json` 4.4MB) | Apache-2.0 | `pos` 449 / `neg` 442. 양성 story 141·news 158·wiki 150. `ref sentences` 377/449 채움. `contra_plug` Replace 271/Insert 178. story는 소설 줄거리 요약(중앙 772단어) | story 양성 141 중 ref 있는 107 → **(evidence, ref) 모순 쌍 125**. news·wiki 합치면 +308. 음성 story 143편은 무관·정상 소스 |
| FABLES (`FABLES.json` 4.0MB) | MIT | 주장 3,158: Yes 2,422 / PartialSupport 419 / No 247 / Inapplicable 70. 근거 인용 실제 포함 3,096건(중앙 91단어·p90 336·최대 1,915) — **저작권 책 발췌의 재배포**이므로 우리가 다시 배포할 때는 별개 문제. 오염 탐지 `canary` 포함 | (주장, 인용, 라벨) 3,096. "No" 230건은 "이 인용이 지지하지 않음"이라 직접 모순이 적다 → judge 모순 클래스엔 약함 |
| FactTrack | 저장소에 라이선스 파일 없음 | 데이터는 Drive `data.tar.gz` 554MB · `data_analyzed.tar.gz` 1.1GB · `models.tar.gz` 1.8GB(바이러스 검사 경고 뒤). 18.7k 쌍이 어느 아카이브인지 README 미기재 | **내려받지 않음**(사용자 결정: 보류) |

합계: 사람 검증 영어 서사 모순 쌍 **약 940(817 + 125)**, 라이선스 MIT·Apache-2.0.

### 5-3. 한국어 결정의 함의

- 영어 쌍은 **문장 단위로만** 번역한다(이야기 전체 불필요, 2천 문장 안쪽). 번역은 계획서가 허용하는 오프라인 생성에 해당하고, 번역 정답 오류율은 표본 200건 검수로 보고한다.
- 외부 쌍은 본 학습에 섞지 않고 **KLUE-NLI → (번역한 영어 서사 모순) → Weave 주입 + 실제 장면** 순의 중간 단계로 둔다. 우리 쌍은 엔티티·필드·값·since 템플릿이고 외부 쌍은 문장 대 문장이라 섞으면 템플릿 지름길이 생긴다. 넣은 조건과 뺀 조건을 나란히 잰다.
- 번역된 영어 고전·요약 문체는 한국어 웹소설 문체와 달라 지름길 위험이 있다 — 트레이드오프 표에 적는다.
- **첫 실험은 외부 데이터 없이** KLUE-NLI → Weave 데이터만으로 돌리고, 학습 곡선(25·50·100%)이 데이터 부족을 가리킬 때 번역 단계를 추가한다. 비용이 가장 낮은 순서다.
- 라벨 매핑: FlawedFictions·ContraDoc은 모순·무관을 만들 수 있고 정상은 정렬 작업 뒤에만, FactTrack은 2클래스라 정상·무관을 못 가린다.

## 6. 출처

장편 서사
- NarrativeFactScore / Agent-as-Judge — https://arxiv.org/html/2501.09993v3
- FactTrack — https://arxiv.org/html/2407.16347v2
- FlawedFictions — https://arxiv.org/html/2504.11900v3
- SCORE — https://arxiv.org/html/2503.23512v1
- Too Long, Didn't Model — https://arxiv.org/html/2505.14925v1
- FABLES — https://arxiv.org/html/2404.01261v2
- NstAgent — https://arxiv.org/abs/2609.35759v1
- Dialogue-Based Relationship Extraction from Novels — https://arxiv.org/abs/2507.04852v1
- 문학 인물 네트워크 구축 리뷰 — https://www.themoonlight.io/review/the-role-of-natural-language-processing-tasks-in-automatic-literary-character-network-construction
- Auto-Graph — https://github.com/tdrml/auto-graph
- Granthika — https://techpodcasts.com/?p=14688
- Book Check Genie — https://peerlist.io/clydecoetzer/project/book-check-genie
- Sudowrite Story Bible — https://docs.sudowrite.com/using-sudowrite/1ow1qkGqof9rtcyGnrWUBS/what-is-story-bible/jmWepHcQdJetNrE991fjJC
- Novelcrafter Codex — https://aiappsforauthors-com.pages.dev/apps/novelcrafter
- 노벨나라 — https://economist.co.kr/article/view/ecn202610280060

일반 정보추출
- FullCite — https://arxiv.org/pdf/2606.07130
- AEVS — https://www.mdpi.com/2073-431X/15/3/178
- llmground — https://pypi.org/project/llmground/
- Assessing the quality of information extraction — https://arxiv.org/html/2404.04068
- ROGRAG — https://arxiv.org/pdf/2503.06474
- GraphRAG gleanings — https://www.mintlify.com/microsoft/graphrag/concepts/knowledge-graphs
- 반복 추출 일치 임계(속성 추출) — https://www.aimspress.com/article/doi/10.3934/aci.2026010
- Correct and consistent extraction(UMD) — https://building-with-llms-umd.readthedocs.io/en/latest/correct-extraction.html
- CRITIC — https://arxiv.org/abs/2305.11738v4
- Chain-of-Verification — https://chatpaper.com/paper/49958
- SycEval — https://arxiv.org/html/2502.08177v4
- Beyond Self-Checking(ICLR 2026) — https://iclr.cc/virtual/2026/10012629
- Cross-Model Disagreement — https://arxiv.org/html/2603.25450v1
- FActScore — https://arxiv.org/html/2305.14251
- Decomposed Entailment — https://arxiv.org/pdf/2608.05823

학습 데이터 후보 (§5)
- FlawedFictions 저장소 — https://github.com/kabirahuja2431/FlawedFictions · 데이터 — https://huggingface.co/datasets/kahuja/flawed-fictions
- ContraDoc — https://github.com/ddhruvkr/CONTRADOC · 논문 — https://arxiv.org/abs/2311.09182v2
- FactTrack 저장소 — https://github.com/cogito233/fact-track
- FABLES 저장소 — https://github.com/mungg/FABLES
- Conan 논문 — https://arxiv.org/abs/2402.11051v1
- gswamy/plot_holes — https://huggingface.co/datasets/gswamy/plot_holes/blob/main/README.md
- KorNLI·KorSTS 논문 — https://arxiv.org/abs/2004.03289v3 · kor_nli 데이터 카드 — https://huggingface.co/datasets/kakaobrain/kor_nli/blob/cdf18d97fcea5bf4a6d7a6e9365ea20d4024eaae/README.md
- KLUE-NLI 규모(KoBigBird 논문) — https://arxiv.org/pdf/2309.10339
