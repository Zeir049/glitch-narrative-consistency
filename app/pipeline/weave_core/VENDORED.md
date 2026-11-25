# weave_core — Weave 엔진 코어 (원본 그대로 복사)

출처: `%USERPROFILE%\Downloads\event_sourcing` (Weave 저장소), 커밋 `8d89349` 시점(B-039 knows 누적 연결).
복사 당시 아래 파일 모두 그 커밋과 차이 없음(작업 트리 깨끗).

| 파일 | 역할 |
|---|---|
| `ledger.py` | 이벤트 소싱 원장 — 확정 사건 재생, 상태 원장 |
| `validator.py` | 재생 검증 |
| `checker.py` | 규칙 검사기 — 사건 전제 ↔ 원장 대조 (LLM 없음) |
| `ers.py` | 관련 과거 사건 검색 |
| `judge.py` | 판정 프롬프트 구성 |
| `weave_pipeline.py` | 검사 실행: checker → ERS → judge k회 |
| `weave_registry.py` | 엔티티 레지스트리 (NEW:라벨 → 정식 ID) |
| `weave_extract.py` | 원문 → 사건 JSON 추출 프롬프트 |
| `weave_llm.py` | LLM 백엔드 (openrouter / nim / anthropic) |
| `추출_프롬프트_v2_3.md` | 추출 시스템 프롬프트 (v2_3) |

**규칙: 이 폴더의 파일은 고치지 않는다.** 판정 규칙·프롬프트는 Weave 저장소에서 측정으로
검증된 상태다. 몸통 쪽 맞춤은 전부 `app/pipeline/weave.py`(어댑터)에서 한다.
Weave 쪽이 갱신되면 같은 파일을 다시 복사하고 이 표의 커밋을 바꾼다.

측정 범위 주의(Weave README_APP.md "알려진 제약"): 판정 성능은 Weave 평가 코퍼스에서만
측정됐다. 다른 작품·장르, 그리고 이 몸통 안에서 쓰는 모델·설정 조합의 성능은 측정되지 않았다.
체커 계층(LLM 없음)은 이 저장소 `tests/eval/`에서 같은 dev set(30장면)으로 회귀 게이트를 돈다
([docs/EVAL.md](../../../docs/EVAL.md)). 판정(judge) 계층은 여전히 미측정.
