# weave_judge_corpus — judge 층 평가 코퍼스

Weave 저장소 `event_sourcing` 커밋 **`9180c3a`**(main)에서 커밋 기준으로 꺼낸 4작품 실제 코퍼스와 동결 열 원시 판정 결과.
원문에 심어진 모순 + 실제 추출 원장이다. 구조 주입 평가셋이 아니다(`docs/learned-judge-plan.md` 사전 확정).
코어(`app/pipeline/weave_core/`) 기준 커밋은 `8d89349`이고 코퍼스는 그 뒤 커밋 `9180c3a`에서 꺼냈다 —
두 커밋 사이에 이 폴더에 들어온 파일의 차이는 없다(`git diff 8d89349 9180c3a -- corpus_* eval_corpus free_judge_raw/nemotron_ultra_long_*` 빈 출력).

| 폴더 | 작품 | 장면 (모순/정상) | 원장 | 크기(바이트) |
|---|---|---|---|---|
| `wm/` | WM 근미래 SF 웹소설 (재추출 3벌만 — 나머지는 `tests/eval/weave_checker_corpus/`) | 30 (20/10) | Claude 추출본 | 934,155 |
| `q1/` | Q1 금호탕 1987 가족극 | 45 (20/25) | `ledger_nemotron_ultra` (nemotron-3-ultra C=2500) | 722,890 |
| `q3/` | Q3 돔 C-4 2071 남극 기지 스릴러 | 45 (20/25) | `ledger_nemotron_ultra` | 706,752 |
| `sherlock/` | 셜록 홈즈의 모험 완역 5편 | 45 (20/25) | `ledger_v2_3` | 1,074,935 |
| `baseline/` | 동결 열 원시 판정 결과(WM·Q1·Q3) | — | — | 710,260 |

총 4,148,992 바이트(약 4.0 MiB, README 제외). 각 폴더 README에 복사한 파일·원장 선택 근거·라벨 분포가 있다.

쓰는 곳: `scripts/eval/judge_eval.py`(judge 층 하네스), `scripts/eval/extract_agreement.py`(반복 추출 일치도),
`tests/eval/test_judge_baseline.py`(동결 열 재현 고정). 설명은 `docs/EVAL.md`.
