# 학습 judge 설계 문서 검증 기록

2026-11-06. 검증 전용 세션의 결과물. 대상은 미커밋 문서 세 개 —
[`learned-judge-prior-work.md`](learned-judge-prior-work.md)(선행 연구), [`learned-judge-training-design.md`](learned-judge-training-design.md)(설계안, §0 개정),
[`learned-judge-plan.md`](learned-judge-plan.md)(계획서 v2, 오늘 개정 행). 문서의 주장·숫자·출처를 원문(arXiv html, GitHub, HF)·코드·데이터로 대조했다.

판정 표기: **확인됨** / **틀림**(정정값·출처) / **근거 부족** / **조건 차이**(수치는 맞지만 우리 설정과 비교가 안 됨).
선행 연구 원문 대조는 하위 조사 둘이 arXiv html 전문을 열어 문장을 인용한 결과이고, 결론에 영향이 큰 항목(ConStory 표 6, FactTrack 표 3·18,702·P=R=60%)은 이 세션이 다시 열어 확인했다.
측정은 `.venv` 파이썬으로 `judge_eval.load_context` + `build_pairs`를 그대로 불렀다(스크립트는 세션 임시 폴더, 저장소 밖).
모르는 것은 모른다고 적었다.

---

## 0. 틀린 것·고쳐야 할 것 한눈에

| # | 문서 · 위치 | 판정 | 정정 |
|---|---|---|---|
| 1 | prior-work 1장 표 ConStory 행 "전문 웹소설 작가 F1 0.23~0.28" · §2-7 "0.23~0.28 ... 남이 쓴 1만 단어 생성물" | 조건 차이 + 근거 부족 | 논문 자체가 불일치: 표 6 Total 행 F1 **0.229**(recall 0.139), 본문 "Overall F1=0.281"(표에서는 Temporal 행 값). "표 6 기준 0.23, 본문 기준 0.28 — 논문 내 불일치"로 적는다. **"1만 단어"는 논문에 없다** — 길이 미기재. 작가가 읽은 것은 Qwen3-235B가 생성한 합성 이야기 200편(사람이 쓴 웹소설 아님) |
| 2 | prior-work FactTrack 행 "DeBERTa-v3-large NLI(GPT-4 라벨 18,702쌍으로 미세조정)" | 조건 차이 | 바닐라 DeBERTa가 아니라 **이미 NLI 학습된 `MoritzLaurer/DeBERTa-v3-large-mnli-fever-anli-ling-wanli`** 를 추가 미세조정한 것(부록 B.4). 18,702·GPT-4 라벨은 맞음. 테스트 정답도 GPT-4 라벨이라 사람 정답 대비 수치가 아님 |
| 3 | prior-work FactTrack 행 "ContraDoc에서 F1 57% (직접 프롬프트 10%)" | 확인됨(조건 보충) | 57.32% vs 10.48%(표 3). 둘 다 **GPT-4o-mini** 기준. 직접 프롬프트는 P 89.3 / R 5.6, FactTrack은 P 52.7 / R 62.8 — "F1 차이"는 거의 recall 차이 |
| 4 | prior-work FlawedFictions 행 "o3-mini 0.63, Llama-70B 0.57, 8B는 우연 수준" | 조건 차이 | 지표는 이진 **정확도**. o3-mini 0.63은 **reasoning high** 조건만(low 0.55, medium 0.62). 70B는 표상 **Llama-3.3-70B**. 8B "우연 수준"은 표(0.50, 랜덤 0.48~0.50)에서 추론한 것이고 논문 문장은 없다 |
| 5 | prior-work FlawedFictions 행 "LiteReason: 7B를 과제 데이터로 학습하면 0.87 — 과제 특화 학습이 규모를 이긴다" | 조건 차이 | 87.42% 정확도, Qwen2.5-7B-Instruct, **FlawedFictions 자체의 70/15/15 분할**(같은 생성기·같은 분포). 같은 논문의 평범한 RL 학습 기준선은 **88.71**로 더 높다. "분포 내 과제 학습이 영 학습 대형 모델을 이긴다"까지만 말할 수 있고, 우리처럼 작품 간 일반화 설정이 아니다 |
| 6 | prior-work MiniCheck 행 "RoBERTa-large / DeBERTa-v3-large / Flan-T5-large ... 14K + 21K" · §2-2 "large 인코더" | 조건 차이 | RoBERTa 판(Rbta)은 **14K만** 쓰고 AlignScore 미세조정본에서 출발(Table 3 학습 데이터 열 14K/35K/35K). Flan-T5-large는 770M 인코더-디코더 — "large 인코더"가 아니다. 14K = 7,076 + 7,319 = 14,395 |
| 7 | prior-work ContraDoc 행 "GPT-4가 최고지만 불안정" | 틀림(표현) | 원문은 "**unreliable**". 이진 판정에서 GPT-4는 P 97.0 / **R 8.0** / F1 15.6(표 2) — "최고"는 증거 적중률(70.2%) 기준 |
| 8 | prior-work §2-3 "최고 LLM 파이프라인도 recall 0.55~0.64(ConStory, FIND)" | 조건 차이 | FIND(gpt-5 64%)는 **금융·기술 문서**(전문가 삽입), 서사 아님. 서사 범위의 수치는 ConStory 0.55와 FactTrack-on-ContraDoc 0.63 |
| 9 | prior-work NoCha 행·§4 출처 | 근거 부족(공개 여부 미기재) | 수치는 맞음. **전체 데이터는 비공개**(저작권·오염 방지, 각주 4). GitHub에는 고전 4권 표본만(MIT). GPT-4o 55.8%는 모델이 답한 617쌍 기준 |
| 10 | prior-work STAGE 행 "앞 사실을 조용히 잃는다" | 조건 차이(표현) | 논문은 "silent forgetting" = 재귀 갱신에서 **prior developments 누락(coverage 하락)**; 모순율은 안정("Contradiction rates remain stable"). 151편 = 영 109 + 중 42 |
| 11 | prior-work ConWriter 행 "직접 생성·DOME보다 일관성 우위 / 위험 신호로 검증 대상 선별" | 조건 차이 | 표 1은 "matches or reduces" — GPT-5.4-nano에서 직접 생성과 동률(0.0). 위험 신호는 **예산·우선순위** 배정용이고 판정은 상태·전이 제약 검사(§2.6 "not used as a direct consistency judgment") |
| 12 | prior-work ConStory 행 "분류표 5범주 19소분류" · §2-5 대응표 | 조건 차이(이름) | 범주명은 Timeline & Plot Logic(**6**: Absolute Time, Duration, Simultaneity, Causeless Effects, Causal Logic, **Abandoned Plot Elements**) / Characterization(4) / World-building & Setting(3) / Factual & Detail Consistency(3) / Narrative & Style(3). 대응표에 Abandoned Plot Elements가 빠졌다 |
| 13 | prior-work 1장 표 "한국어 — 해당 데이터셋·연구 없음" | 근거 부족 | 부재는 이 검증으로 확인할 수 없다(별도 체계적 검색을 하지 않았다). "찾지 못했다"로 쓴다. KoCoNovel: 50편·NER·상호참조 **+ 화자 식별**, 라이선스 CC BY 4.0 |
| 14 | prior-work §2-4 "근거 적중 ... 하네스에 근거 집합이 이미 있다" | 근거 부족 | 근거 사건 ID 라벨은 **WM만** 있다. Q1·Q3·셜록은 `evidence_event_map.json`의 **문자열 대조 후보**("확정 전 사람 확인 필수", matched/ambiguous/unmatched)이고, 하네스는 이 지도에서 `ledger_absent_scenes`만 읽는다([`judge_eval.py:440`](../scripts/eval/judge_eval.py)). 근거 적중 지표는 사람 확정이 선행돼야 한다 |
| 15 | prior-work §2-4 "쌍 정확도(NoCha) ... 대조 묶음의 모순·정상이 둘 다 맞아야" | 근거 부족 | 4코퍼스 실제 라벨에는 **대조 쌍(같은 전제의 모순·정상 짝) 필드가 없다**. 쌍 정확도는 구조 주입 대조 묶음(학습·검증 전용)에서만 계산 가능 → 평가 지표가 아니라 학습 진단 지표로 적어야 한다 |
| 16 | prior-work §2-1 표 A "4코퍼스 + Weave 확정 회차에서 build_pairs로 쌍을 뽑고 LLM에 라벨" | 근거 부족(누수) | 평가 작품의 확정 회차·장면에서 뽑은 쌍을 LLM이 라벨해 학습에 쓰면 **접기별 평가 작품 누수**. "접기의 학습 작품 확정 회차에서만, 평가 장면은 라벨링 금지"가 명시돼야 한다(§2-1 참고) |
| 17 | design §2-1 "버린 대안. large: 정확도 **+1.2점** 대비 연산 3.5배" | 틀림(§0와 충돌) | STS F1 열(86.63−85.40) 오독의 잔재. NLI ACC로는 **+4.3점**(89.17−84.83). §0가 이 문장을 덮지만 본문에 남아 있다 |
| 18 | design 본문 §0 충돌 문장 다수 | — | §3-4 목록 참조(§1-2·§1-5·§1-9·§1-10·§2-2-6·§2-5·§3-2 셀 4·6·10·§3-3 G1·§4·§5) |
| 19 | plan 검증 표 R2 행 "회차당 **CPU** 시간 ≤ 60초" (95행 중 84행) | 틀림(같은 문서 내 충돌) | 사전 확정 표는 오늘 "워커 GPU 기본, CPU는 대체 경로"로 바뀌었는데 검증 표는 그대로 |
| 20 | plan ② 학습 judge 행 "쌍 생성은 결정적(정렬·**상한 32**)" | 근거 부족(설계안과 불일치) | 설계안 §2-3은 kind별 할당량 합 24(지연 허락 시 32)로 바꾼다. 둘 중 하나로 맞춘다 |
| 21 | plan 검증 표 R2 "4코퍼스 **judge 담당 장면**에서" · decisions §3 "judge 담당 54건" | 근거 부족(설계안과 불일치) | 설계안 §2-6은 양성 **60 전부**를 주 비교로, 54는 참고 칸으로 못 박았다(라벨 `expected_layer`는 "제작 측 추정"). 계획서 문구를 60으로 |
| 22 | plan 사전 확정 "분할 — 작품 단위 ... 주 결과는 '작품 하나 빼기'로도 보고" | 조건 차이 | 설계안 §2-6은 작품 하나 빼기 **교차 적합 4접기의 합산 예측이 주 결과**다("로도"가 아니라 주). 표현을 맞춘다 |
| 23 | design §2-5 "정밀도: fp32만(추론에서 fp16·bf16 금지)" + §0 "GPU 추론 기본, large 후보" + 60초 상한 | 근거 부족(충돌 가능) | 3060 fp32 비텐서코어 연산(≈13 TFLOPS 피크)으로 large 3,550쌍·256토큰(≈0.6 PFLOP)은 **약 50~90초** [추정, §3-2]. fp32 규칙을 GPU에도 적용하면 상한을 넘을 수 있다. GPU 추론 시간 추정이 문서 어디에도 없다 |

---

## 1. 선행 연구 인용의 정확성 (prior-work 1장 표)

열람 판: FactTrack v2(2025-01-31), MiniCheck v2(2024-10-01), NoCha v3(2024-10-22), ConStory v1(2026-03-06), FlawedFictions v3(2025-12-18), LiteReason v2(2026-06-09), ContraDoc, FIND, STAGE v9, ConWriter v2, 모두 2026-11-06 열람.

### FactTrack (2407.16347)

| 문서 주장 | 판정 | 원문 |
|---|---|---|
| 아웃라인 사건마다 원자 사실(pre/post, 유효 구간) → 세계 상태 갱신 → 반대 방향 쌍 대조 | 확인됨 | "four-step pipeline ... decompose the event into directional atomic facts; determine the validity interval ...; detect contradictions ...; add new facts" / "Detect Contradictions are only between facts in different directions". 평가 도메인은 LLaMA2-7B-Chat이 만든 depth-3 아웃라인 90개 — 사람 서사가 아니다 |
| Contriever 유사도 0.5로 쌍 필터 | 확인됨 | 부록 B.3 "Filter(p,P) ... sim_contriever(p,P) > 0.5" |
| DeBERTa-v3-large NLI, GPT-4 라벨 18,702쌍 | 조건 차이 | #2. 라벨 쌍은 학습 아웃라인 50개의 856,748쌍에서 모델 확신도 층화 추출 후 중복 제거 → 18,702 |
| 양성 2.98%, 상위 3% 기준값 | 확인됨 | "overall positive rate ... 2.98%" / "percentile threshold ... at 3% (NLI_Score ≥ 0.2359)" |
| 테스트 P≈R≈0.60 | 확인됨(조건) | "precision = 60% and recall = 60% on our GPT-4-annotated test set" — 쌍 단위, 정답은 GPT-4. 아웃라인 종단 과제(부록 D)는 P 0.41 / R 0.27 |
| ContraDoc F1 57% vs 직접 10% | 확인됨(조건) | #3 |
| §2-6 "두 시점 모두에서 모순일 때만 표시" | 확인됨 | "only flag a contradiction when a contradiction is detected on both checkpoints" (l1 ≤ l2 ≤ r1 ≤ r2) |
| 공개·라이선스 | 근거 부족 | 코드 GitHub cogito233/fact-track(**LICENSE 파일 없음**), 데이터·미세조정 모델은 Google Drive 링크. 표에 라이선스가 비어 있음 |

### MiniCheck (2404.10774)

| 문서 주장 | 판정 | 원문 |
|---|---|---|
| GPT-4 합성 14K + ANLI 어려운 21K | 확인됨(조건) | C2D 7,076 + D2C 7,319; "subset (21K) of the ANLI training data, selecting examples where their trained entailment models made incorrect predictions". 라운드 미기재. **Rbta는 14K만** (#6) |
| RoBERTa-large / DeBERTa-v3-large / Flan-T5-large | 조건 차이 | #6 |
| 문서별 최댓값 집계, 기준값 0.5 고정 | 확인됨 | "max_j M(D_i,j, c_i)" / "threshold ... 0.5 for most fact-checkers"; 코드 `inference.py` max·0.5 |
| GPT-4 75.3 / FT5 74.7 / AlignScore 70.4 | 확인됨 | v2 Table 2(테스트셋, 기준값 튜닝 없음). 튜닝 시 AlignScore 72.2(Table 7). 현재 리더보드는 모델이 더 많다 |
| 비용 400배 | 확인됨 | $107 vs $0.24(13K 테스트셋) ≈ 446배 |
| 라이선스 | 근거 부족(미기재) | GitHub Apache-2.0, HF `lytang/MiniCheck-Flan-T5-Large` MIT |

### ConStory-Bench / Checker (2603.05890)

| 문서 주장 | 판정 | 원문 |
|---|---|---|
| ACL Findings 2026, 5범주 19소분류 | 확인됨 | aclanthology 2026.findings-acl.410 (pp. 8400–8428); "five error categories with 19 fine-grained subtypes" |
| 진단셋 200편·오류 1,000 | 확인됨(조건) | 부록 A.2: Qwen3-235B-A22B-Thinking 생성 200편, 범주당 200개 주입 |
| o4-mini: 범주별 구간 추출 → 쌍 대조 → 근거 사슬 | 확인됨 | §2.3 "We adopt o4-mini"; Category-Guided Extraction → Contradiction Pairing → Evidence Chains → JSON Reports |
| recall 0.55 / precision 0.88 / F1 0.68, 인물 F1 0.74 | 확인됨 | 표 6 Total: GT 1000, Pred 622, TP 550, R 0.550, P 0.884, F1 0.678; Character Consistency F1 0.742 |
| 작가 F1 0.23~0.28 | 조건 차이 | #1 — 이 세션이 표 6을 다시 열어 확인: Experts(Avg) Pred 210.0 / TP 138.5 / R 0.139 / P 0.660 / **F1 0.229**; 본문 "Overall F1=0.281", "171 detections (17.1% recall)"은 표와 안 맞는다 |
| MIT, 주입 진단셋 미공개 | 확인됨 | HF 카드·GitHub Picrew/ConStory-Bench MIT. HF에는 prompts·stories·evaluations만; 200편 진단셋 공개 언급 없음. checker 코드(`judge.py`)·프롬프트 5종은 공개 |

### FlawedFictions (2504.11900) · LiteReason (2512.02240)

| 문서 주장 | 판정 | 원문 |
|---|---|---|
| 1막 명제 부정 → 2·3막 재작성, 1막 원본 유지, 사람 3명 검증, 채택 약 70% | 확인됨 | "concatenating A1 from the original story and the subsequent acts from the counterfactual"; "at least 3 annotators per instance", 과반; "207 stories (70% acceptance rate)" from ≈300. 사전 필터 "4 out of the 5 completions" |
| 이진 검출 + 근거 위치(CEEval) | 확인됨 | Accuracy + "CEEval-Full"(CEEval-Pos도) |
| 사람 0.76, Claude 3.5 0.76, o3-mini 0.63, Llama-70B 0.57, 8B 우연 | 조건 차이 | #4. Table 1(a) Accuracy: Human 0.76, Claude 3.5 Sonnet 0.76, o3-mini High 0.63, Llama-3.3-70B 0.57, Llama-3.1-8B 0.50 |
| 긴 판 2,700단어에서 전부 하락 | 확인됨 | FlawedFictionsLong 평균 2,703단어(200편, FairyTaleQA); 최고 o1 CEEval-Full 0.53 vs "Always No Error" 0.51 |
| MIT, 생성 코드 미공개 | 확인됨 | README To-Do "FlawedFictionsMaker algorithm" 미완 |
| LiteReason 7B 0.87 | 조건 차이 | #5 |

### NoCha (2406.16264)

| 문서 주장 | 판정 | 원문 |
|---|---|---|
| 소설 67권, 최소 차이 참/거짓 쌍 1,001개 | 확인됨 | "1,001 minimally different pairs ... about 67 recently-published English fictional books"(신간 63 + 고전 4; 주장 2,002개) |
| 책 전체 + 주장 하나씩, 둘 다 맞아야 정답 | 확인됨 | "Models get credit only if they label both the true and false claim in a pair correctly" |
| GPT-4o 55.8%, 공개 모델 우연 이하 | 확인됨 | Table 3: GPT-4o 55.8 (344/617), Random 25.0; "All open-weight models perform below random (25%)" |
| 공개 | 근거 부족 | #9 |

### ContraDoc (2311.09182) · FIND (2512.18601) · 2601.02627

- ContraDoc: GPT-4(0314) 삽입 + 주석자 2명 검증, 449 양성 / 442 음성 — 확인됨. "불안정" → "unreliable"(#7).
- FIND: "manually inserted by a financial expert", 125 dev / 375 test, "best-performing model (gpt-5) recovered 64%"(LLM 채점 task score, recall 성격) — 수치 확인됨, 도메인 조건 차이(#8).
- 2601.02627 "Improved Evidence Extraction and Metrics for Document Inconsistency Detection with LLMs" — ContraDoc를 주 평가셋으로 쓰는 후속(다른 저자). 확인됨.

### STAGE (2601.08510) · ConWriter (2608.05169) · KoCoNovel

- STAGE: 151편 확인됨; 발견의 표현은 #10.
- ConWriter: 존재·EMNLP 2026 Findings 확인됨; 비교 표현·위험 신호 역할은 #11.
- KoCoNovel: 50편(위키문헌 근현대 중단편) 확인됨; 논문 2404.01140은 상호참조 중심, 178K 토큰. 라이선스 CC BY 4.0(코드 MIT). 저장소는 `storidient/KoCoNovel`이 본가이고 KoBookNLP README에도 실려 있다.

### "찾지 못한 것" 세 줄 (prior-work 21행)

부재 주장이라 이 검증으로 확인할 수 없다. 근거 부족으로 둔다(하위 조사는 인용 대조만 했고 신규 검색을 하지 않았다).

---

## 2. 적용 판단의 논리 (prior-work 2장)

### 2-1. "LLM 쌍 라벨링·반사실 회차를 1단계로" — 근거가 결론을 일부만 받친다

받치는 것: MiniCheck(35K)·FactTrack(18.7K)이 수천~수만 라벨을 썼다는 사실은 맞다(§1).

빠진 반론:

1. **양성 공급원이 다르다.** FactTrack의 2.98%는 LLM이 만든 아웃라인(모순이 자연 발생) 안의 비율이고, MiniCheck의 양성·음성은 생성 단계에서 정답이 확정된다. 우리 갈래 A는 "4코퍼스 + Weave 확정 회차" — **원작 원문에서 재생한 원장은 내부적으로 거의 일관**하므로 build_pairs 쌍의 참 모순은 극히 드물다. LLM이 "모순"이라고 붙인 라벨의 대부분이 LLM 오탐이 될 위험이 있고, 그 잡음은 소수 클래스에 집중된다. 갈래 B(반사실)만이 참 양성을 공급한다. 문서의 "양성 3~5%" 목표가 어디서 오는지 적혀 있지 않다.
2. **라벨 잡음.** FactTrack 자체가 "GPT-4 라벨 대비 P=R=0.60"이고 GPT-4 라벨의 사람 일치율은 보고하지 않는다. "라벨 일치 2회 중 불일치는 버림"은 어려운 쌍을 체계적으로 버려 쉬운 분포로 기운다(설계안 §2-2가 인용한 Proebsting & Poliak의 가설만 편향 경고가 LLM 라벨에도 그대로 해당하는데, §0는 이를 다루지 않는다 — 설계안 §2-2 근거 "걸러지지 않은 생성물은 지름길의 원천"과 §0-3이 서로 긴장한다).
3. **평가 누수.** #16. 4접기 교차 적합에서 평가 작품의 회차·장면에서 뽑은 LLM 라벨 쌍이 학습에 들어가면 안 된다. 또 갈래 B "채택분은 원문 주입 평가셋으로도 쓸 수 있다"는 바로 위 "둘 다 학습 전용"과 충돌한다.
4. **비용·시간 미기재.** 1~2만 쌍 × 글자 수 중앙 400~720자(§4 측정) × 라벨 2회 ≈ 수백만~천만 토큰 입력. 금액은 모델에 따라 수십 달러 수준으로 추정되나 문서에 추정이 없다. 반사실 회차 200~300건의 **사람 검증 인력·시간**(FlawedFictions는 건당 주석자 3명)도 없다.
5. **한국어 전이·문체 단서.** 반사실 뒷 회차를 LLM이 다시 쓰면 앞 회차(원문)와 문체가 갈려 그 자체가 단서가 된다. FlawedFictions는 음성에도 재작성본을 두는 변형(`error_resolved_negs`, `cf_negs`)을 실험했다. 우리 설계에는 "음성도 재작성"이 없다.
6. **추출 의존.** 반사실 회차는 Weave 추출(LLM)을 거쳐야 쌍이 된다 — 추출 비용과 추출 잡음(EVAL.md 반복 추출 일치도: 값 J 0.01)이 학습 라벨에 섞인다. 문서에 언급 없음.

### 2-2. "base → large, small은 대체" — 방향은 맞고, 인용 하나는 무관하다

KLUE-NLI 격차(5.5점·4.3점)는 확인됨(§3-1). "FlawedFictions에서 8B LLM은 우연 수준"은 **영 학습 디코더 LLM** 결과라 미세조정 인코더의 크기 선택 근거가 못 된다(같은 논문 후속 LiteReason은 7B를 학습해 0.87). "소형 모델이 되었다는 보고는 없다"는 부재 주장(§1). 결론 자체(둘 다 학습해 검증 작품에서 고른다)는 방어 가능하다.

### 2-3. "목표 recall 0.5·오탐 0.05 = 문헌 수준" — 조건 차이

표의 세 행은 **지표가 다르다**: ConStory precision 0.88은 합성 이야기 안 주입 오류 1,000개에 대한 **오류 단위 정밀도**, FactTrack 0.60/0.60은 GPT-4 라벨 **쌍 단위**, 우리 "오탐 0.05"는 **정상 장면 단위 거짓 양성률**. recall 0.55·0.60도 각각 주입 오류·쌍 기준이다. "문헌 수준"이라는 말은 할 수 없고, 쓸 수 있는 말은 "같은 설정의 선행 수치는 없다"다. 또 R2 통과선(동결 열 −0.10 → 0.20)과 목표 0.5가 다른 수위인데 어느 쪽을 발표 주장으로 쓰는지 적혀 있지 않다.

### 2-4. "쌍 정확도·근거 적중" — 둘 다 전제가 빠졌다

#14, #15. 근거 적중은 Q1·Q3·셜록의 지도가 사람 확정 전이고(matched 32/55/14, ambiguous 4/1/6, unmatched 6/12/8), 셜록은 `_meta`·`ledger_absent_scenes`조차 없다. 학습 judge가 내는 `evidence_event_ids`는 최고 쌍 전제 ID 하나다(세션 C 노트) — CEEval의 문장 위치 찾기와도 단위가 다르다.

### 2-5. "ConStory 분류표 대응" — 쓸 수 있으나 이름·누락 보정(#12)

진단셋이 비공개라 그들의 주입 데이터를 가져올 수는 없다(checker 프롬프트는 공개).

### 2-6 · 2-7

2-6(검색 필터·두 시점 규칙)은 확인됨. 2-7 작가 수치는 #1 — "1만 단어"를 지우고 0.23/0.28 불일치를 적는다. "자기 작품을 아는 작가와는 조건이 다르다"는 단서는 맞다.

---

## 3. 설계안 §0 개정의 정합성

### 3-1. KLUE-NLI 수치 — 확인됨

`raw.githubusercontent.com/KLUE-benchmark/KLUE/main/README.md` 64~66행(헤더 53행: TC | STS(2열) | NLI | NER(2) | RE(2) | DP(2) | MRC(2) | DST(2)):

| 모델 | TC F1 | STS r | **STS F1** | **NLI ACC** |
|---|---|---|---|---|
| KLUE-RoBERTa-small | 84.98 | 91.54 | 85.16 | 79.33 |
| KLUE-RoBERTa-base | 85.07 | 92.50 | 85.40 | 84.83 |
| KLUE-RoBERTa-large | 85.69 | 93.35 | 86.63 | 89.17 |

원안의 85.16/85.40/86.63이 STS F1 열이라는 §0 진단도 맞다. 덤으로 §3-2 셀 4의 "24,998쌍(재확인 못 함)"은 HF datasets-server(`klue/klue` config `nli`)에서 train **24,998** / validation 3,000으로 확인됨. 라이선스 CC BY-SA 4.0(README·HF 카드).

### 3-2. 3060 12GB에서 `klue/roberta-large` fp16·256토큰·배치 8 — 확인됨 [추정식]

파라미터(vocab 32,000·위치 514·h 1024·24층·FFN 4096): 임베딩 33.3M + 층당 12.60M × 24 ≈ **335.6M**(≈3.4억, 설계안과 같다).

| 항목 | 바이트/파라미터 | 크기 |
|---|---|---|
| fp32 마스터 가중치 | 4 | 1.25 GB |
| fp32 그래디언트 | 4 | 1.25 GB |
| AdamW m·v | 8 | 2.50 GB |
| (HF AMP는 fp16 가중치 사본을 따로 두지 않는다; 둔다면 +2) | 0~2 | 0~0.63 GB |
| 고정 합 | 16~18 | **5.0~5.6 GB** |

활성값(fp16, Megatron 추정식 층당 34·s·b·h + 5·a·s²·b, s=256·b=8·a=16): 체크포인팅 없음 ≈ 2.5 GB, 있음 ≈ 0.2 GB. CUDA 컨텍스트·임시 버퍼 ≈ 0.6~1 GB.
→ 체크포인팅 없이 ≈ **8.8 GB**, 있으면 ≈ **6.4 GB**. 12 GB에 든다. 체크포인팅은 선택(속도 −30% 정도)이지 필수가 아니다. 단, 이 식은 실측이 아니고 PyTorch 캐시 할당자의 파편화는 포함하지 않았다.

**추론 시간은 문서에 없다** (#23). 대략: large 순전파 ≈ 2·336M·256 ≈ 172 GFLOP/쌍, 회차 3,550쌍 ≈ 0.61 PFLOP. 3060 fp16 텐서코어 실효 15~25 TFLOPS면 25~40초, **fp32(§2-5 규칙) 실효 7~12 TFLOPS면 50~90초**. base는 그 1/3. 60초 상한을 GPU에서 지키려면 fp16/TF32 추론을 허용하든지 large를 포기하든지 결정이 필요하다 — §2-5 "fp32만"과 §0 "large 서비스 후보"가 같이 성립하지 않을 수 있다.

### 3-3. GPU 결정성 조건 — 문서 근거

PyTorch 재현성 노트(docs 2.15): "Completely reproducible results are not guaranteed across PyTorch releases, individual commits, or different platforms" / "results may not be reproducible between CPU and GPU executions, even when using identical seeds".
`torch.use_deterministic_algorithms` 문서: 결정적 알고리즘 = "given the same input, and when run on the same software and hardware, always produce the same output"; 없는 연산은 RuntimeError; "This setting alone is not always enough to make an application reproducible".
`CUBLAS_WORKSPACE_CONFIG`: PyTorch **2.4** 문서에는 "A handful of CUDA operations are nondeterministic if the CUDA version is 10.2 or greater, unless ... `:4096:8` or `:16:8` is set"가 있고, **2.15 문서에서는 이 단락이 빠졌다**. cuBLAS 문서(Results Reproducibility): 같은 툴킷 판·같은 아키텍처·같은 SM 수면 "the same bit-wise results at every run"이 설계상 보장이고, 복수 스트림이 한 핸들을 공유할 때만 `CUBLAS_WORKSPACE_CONFIG`가 필요하다. 툴킷 판이 다르면 보장 없음.

따라서 §0의 정의("같은 기계·같은 드라이버·`use_deterministic_algorithms(True)`·`CUBLAS_WORKSPACE_CONFIG=:4096:8`·배치 구성 고정")는 **문서상 충분 조건에 가깝다**. 빠진 것: (a) 같은 **CUDA 툴킷·cuDNN·torch 판**(드라이버만으로 부족), (b) `torch.backends.cudnn.benchmark=False`(기본값; HF Trainer도 안 켬), (c) SDPA/어텐션 백엔드 고정(`use_deterministic_algorithms`가 SDPA 일부 백엔드를 막음), (d) **배치 크기 불변성은 보장되지 않는다**(cuBLAS 커널 선택이 모양에 따라 달라짐 — 설계안 §2-5의 경고와 같다). fp16/fp32 선택은 결정성과 무관하다(같은 조건이면 fp16도 비트 동일). 기계 간 일치는 주장하지 않는다는 §2-5 원칙은 GPU에서 더 강하게 적용된다(GPU 모델·SM 수가 달라지면 보장 없음).

### 3-4. §0 이후 본문에서 §0와 충돌하는 문장 (파일 행 번호)

| 행 | 문장 | 충돌 |
|---|---|---|
| 33 | §1-2 "첫 실험의 첫 관문은 워커 CPU 실측이다" | §0-2: CPU는 1순위 아님 |
| 36 | §1-5 "LLM 오프라인 증강은 2단계에만 하고" | §0-3 |
| 40 | §1-9 결정성 "고정할 것은 스레드 수, MKL_CBWR, fp32, 버킷 패딩" | CPU 전제. GPU 항목(§0-2) 없음 |
| 41 | §1-10 "T4에서 small·base × 4 접기 × 시드 3" | §0-2 로컬 3060, base·large |
| 83·85~86 | §2-1 표 열 이름 "T4 학습" / small·base 행 "쉬움" | 3060 기준으로 고쳐야 |
| 122 | "버린 대안. large: 정확도 +1.2점 대비 연산 3.5배" | #17, §0-1 |
| 135 | §2-2 결정 6 "LLM 오프라인 증강은 2단계에서만" | §0-3 |
| 144 | §2-2 근거 "걸러지지 않은 생성물은 지름길의 원천" | §0-3과 긴장(2-1 반론 2) |
| 148 | "100%에서도 기울기가 가파르면 ... 2단계(증강)로 간다" | §0-3 |
| 220~222 | §2-5 표 "fp32만", 스레드, `MKL_CBWR` | CPU 전용 항목; GPU 행 없음. fp32 규칙은 #23 |
| 232·234 | "처리량 손실은 CPU에서 작다", "PyTorch CPU ... 공식 보장 문서 찾지 못했다" | GPU 경로에 대한 대응 문장 없음 |
| 316 | config "`weave_judge_threads`" 등 | GPU 장치 선택 설정 없음 |
| 335 | inject "변형은 사람이 쓴다(LLM 증강은 2단계)" | §0-3 |
| 373 | §3-2 셀 1 "모델·데이터 폴더는 Drive" | 로컬 |
| 376 | 셀 4 "KLUE-NLI 기초 학습(small·base 각각 1회)" | large 없음 |
| 378 | 셀 6 "접기 4 × 모델 2 × 시드 3 ... small ~6분·base ~12분" | 모델 집합(base·large)·시간(T4) |
| 379·380 | 셀 7·8 "small × 시드 1", "small × 25·50%" | 기본 모델이 base |
| 382 | 셀 10 "평가는 Colab이 아니라 워커와 같은 CPU 환경에서" | §0-2 GPU 기본 |
| 384 | "T4 시간은 ... Colab 세션 한도" | 로컬 |
| 401 | §3-3 G1 "워커 CPU, small·fp32 ≤ 60초" | §0-2 |
| 405 | G5 "2단계(증강) 판단" | §0-3 |
| 417 | §4 위험 1 "회차 60초 초과 — 높음 ... small·버킷 256" | GPU 기본이면 가능성 재평가 필요 |
| 419 | 위험 3 "2단계 증강" | §0-3 |
| 443 | §5 "(2단계) LLM 생성 증강" | §0-3 |

§0가 "본문에서 충돌하는 문장은 이 절이 우선한다"고 선언하므로 오류는 아니지만, 위 24곳은 읽는 이가 매번 §0를 대조해야 한다. §0-2가 단정한 "large는 12GB에서 fp16·256·배치 8(+gradient checkpointing)"은 §3-2에서 확인됐고, 체크포인팅은 선택이다.

---

## 4. 측정 재현 (설계안 §2-0)

실행: `.venv` 파이썬, `judge_eval.load_context(work)`(5화 확정 원장, timelines main, knows accumulate, checker 항목) → 장면마다 `build_pairs({"key","unit":"scene","events"}, ...)`. 상한 32 / 48 / 64 / 128 / 없음(10⁹).

### 4-1. 장면당 쌍 수 — 확인됨

| 작품 | 장면 | 사건/장면 | 가설/장면 | 가설/사건 | 쌍(32) 평균/최대 | 쌍(없음) 평균/최대 | 글자 수 중앙/p90/p99 | 규칙 |
|---|---|---|---|---|---|---|---|---|
| WM | 30 | 1.1 | 2.0 | 1.9 (문서 1.8) | 64 / 128 | 179 / 377 | 257 / 339 / 391 | 12 |
| Q1 | 45 | 1.8 | 6.6 | 3.7 | 211 / 544 | 860 / 2,146 | 721 / 1,143 / 1,962 | 3 |
| Q3 | 45 | 1.1 | 4.7 | 4.4 (문서 4.3) | 151 / 256 | 817 / 1,742 | 495 / 874 / 1,788 | 38 |
| 셜록 | 45 | 1.7 | 6.3 | 3.7 | 184 / 480 | 1,679 / 6,429 | 402 / 576 / 739 | 9 |

문서 표와 전부 일치(가설/사건 두 칸은 반올림 차이 — 합 비율로 계산한 값). "165장면 중 159가 상한에 걸림"은 **"상한 없음 쌍 수 > 상한 32 쌍 수"** 정의로 159 재현(WM 30·Q1 45·Q3 45·셜록 39). 모든 가설이 꽉 찬 장면으로 세면 150.

### 4-2. Q3 kind 분포(상한 32) — 확인됨

ledger 2,949 / rule 3,607 / checker 260, knowledge·past·scene 0. 규칙 38개가 ledger 다음 칸을 다 채운다는 설명도 코드([`pairs.py:34`](../app/pipeline/pairs.py) `KIND_ORDER`)와 맞는다.

### 4-3. 근거 포함률 — 확인됨, 단 해석이 "후보 전부"였음을 명시해야

문서는 "근거 = `evidence_event_map.json`의 후보 사건"이라고만 적었다. 지도의 항목은 `status` ∈ {matched, ambiguous, unmatched}이고 후보가 둘인 ambiguous가 있다. 두 해석으로 다시 셌다(WM은 라벨 `evidence_event_ids`, RULE ID 포함).

| 해석 | 작품 | 근거 있는 양성 | 32 | 48 | 64 | 128 | 없음 | 가장 앞선 정답 전제 kind |
|---|---|---|---|---|---|---|---|---|
| **전부**(matched+ambiguous 후보) | WM | 20 | 18 | 19 | 19 | 19 | 19 | ledger 13 · rule 4 · knowledge 1 · checker 1 |
| | Q1 | 20 | 14 | 18 | 18 | 19 | 20 | knowledge 10 · ledger 7 · past 3 |
| | Q3 | 19 | 14 | 14 | 18 | 19 | 19 | ledger 14 · knowledge 3 · past 2 |
| | 셜록 | 16 | 7 | 7 | 7 | 9 | 15 | knowledge 7 · ledger 5 · past 2 · checker 1 |
| | 합 | 75 | **53** | 58 | 62 | 66 | **73** | |
| **matched만** | WM | 20 | 18 | 19 | 19 | 19 | 19 | 같음 |
| | Q1 | 19 | 13 | 18 | 18 | 19 | 19 | knowledge 11 · ledger 6 · past 2 |
| | Q3 | 18 | 13 | 13 | 17 | 18 | 18 | ledger 13 · knowledge 3 · past 2 |
| | 셜록 | 12 | 4 | 4 | 4 | 6 | 11 | knowledge 6 · past 3 · ledger 2 |
| | 합 | 69 | **48** | 54 | 58 | 62 | **67** | |

문서의 53/75·73/75와 kind 분포, 셜록 순위(79·117·131·151·156·216·463·505)는 **"전부" 해석**에서 그대로 재현됐다(scene_13·16·19·20·25·34·36·43; scene_03은 상한 없이도 미포함). 상한 없이도 빠지는 양성은 WM scene_07·셜록 scene_03(두 해석 공통).
matched만으로 세면 셜록은 4/12로 더 나쁘고 합은 48/69(69.6%)로 비율은 비슷하다(53/75 = 70.7%).
결론은 바뀌지 않지만, 설계안 §2-0 둘째 표 위 문장에 "후보는 matched·ambiguous 전부(ambiguous는 후보 둘 다)"를 적어야 하고, 할당량 목표 "≥ 66/75"도 같은 해석이 전제다.

---

## 5. 계획서 v2 사전 확정 표 대조

| 항목 | 판정 | 비고 |
|---|---|---|
| δ 10%p | 확인됨 | 설계안 §2-6 하한 > −0.10, G7과 같다. 검정력 표(엇갈림 ≤ 10~20건)도 같은 δ |
| 목표 recall 0.5·오탐 0.05 (학습 judge 절) | 조건 차이 | 사전 확정 표에는 없고 학습 judge 절에만 있다. R2 통과선(≥ 0.20)과 다른 수위 — 어느 것이 "하는 말"인지 §"목표와 주장 수위" 표에 없다. 오탐 0.05의 지표 정의(정상 장면 FPR)는 설계안 §2-4와 일치 |
| 분할 | 조건 차이 | #22 |
| 학습 데이터 행(오늘 개정) | 근거 부족 | #16 누수 규칙 미기재. "주입 연산자 1개 홀드아웃"은 설계안(`knowledge_heard`)과 일치. "학습 작품의 실제 장면 포함"은 설계안 §2-6 누수 차단 규칙과 일치 |
| 시간 상한 행(오늘 개정) | 근거 부족 | GPU 추정이 어디에도 없다(#23). 검증 표 84행은 여전히 "CPU"(#19) |
| 학습 judge 절 "상한 32" | 근거 부족 | #20 |
| 검증 표 R2 "judge 담당 장면" | 근거 부족 | #21 |
| 코어 기준 커밋 8d89349 | 확인됨 | 코퍼스는 9180c3a에서 꺼냈으나 두 커밋 사이 코퍼스 파일 차이 없음([`weave_judge_corpus/README.md`](../tests/eval/weave_judge_corpus/README.md) 5~6행) |
| "사전 확정 (결과를 본 뒤 바꾸지 않는다)" 표를 오늘 고침 | 근거 부족(절차) | 결과 전 개정이라 원칙 위반은 아니나 개정 이력(날짜·이전 값)이 표에 없다. decisions.md §3처럼 한 줄 남기는 것이 맞다 |

---

## 6. 이 검증이 하지 않은 것

- prior-work 21행의 "없음/찾지 못함" 세 가지에 대한 독립 검색.
- VRAM·GPU 시간은 식이고 실측이 아니다(transformers 미설치, 설치 금지).
- 선행 연구 중 ConStory 표 6·FactTrack 표 3·18,702·P=R=60% 외의 인용은 하위 조사의 원문 인용을 받아 적었다(인용문은 각 보고에 있음).
- `docs/related-work-extraction-verification.md`는 범위 밖.
