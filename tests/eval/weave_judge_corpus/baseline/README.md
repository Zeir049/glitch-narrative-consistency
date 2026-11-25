# baseline — 동결 열 원시 판정 결과

원본: Weave 저장소 `event_sourcing` 커밋 **`9180c3a`**의 `free_judge_raw/nemotron_ultra_long_{wm,q1,q3}{,_rerun}/main.jsonl`
(`git show`로 꺼냄, 수정 없음 — 429 실패 줄도 그대로 둔다. 채점기가 `status != "ok"` 줄을 건너뛴다).

| 폴더 | 내용 |
|---|---|
| `nemotron_ultra_long_wm/` | WM 30장면(사건 단위 event_id `EVENT_Sxx_n` → 장면으로 접음) × k=3 |
| `nemotron_ultra_long_q1/`, `nemotron_ultra_long_q3/` | Q1·Q3 45장면(event_id = scene_id) × k=3 |
| `*_rerun/` | 무변경 재실행 20% 표본(시드 20260929): WM 6 · Q1 9 · Q3 9 |

조건(동결 열): 판정 `openrouter/nvidia/nemotron-3-ultra-550b-a55b:free` · reasoning off · max_tokens 8192 · k=3 다수결 2/3 ·
full 조건 · 패킷 `judge_requests_v4c`(WM) / `judge_requests_long_q{1,3}_full` · 원장 WM=Claude 추출본, Q1·Q3=nemotron-3-ultra C=2500.
측정 완료 2026-10-07. 근거 `handoff/longitudinal_report.md`, `handoff/final_report.md` §1.

`scripts/eval/judge_eval.py --mode recorded`가 이 파일로 합산 Recall 18/60 · 오탐 3/60과 작품별 수치를 재현하고,
`tests/eval/test_judge_baseline.py`가 그 값을 고정한다.
셜록은 동결 열에 없다(종단 측정은 WM·Q1·Q3).
