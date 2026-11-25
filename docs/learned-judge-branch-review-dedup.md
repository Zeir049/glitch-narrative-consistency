# 규칙 중복 제거 브랜치 검증 보고 (2026-11-07)

검증 전용 세션의 결과. 코드는 고치지 않았고 이 문서 하나만 만들었다. 테스트는 `TEST_DB_NAME=glitch_noai_test_verify3`로 분리된 DB에서 돌렸다.
앞선 보고서: [learned-judge-branch-review-I.md](learned-judge-branch-review-I.md).

| 항목 | 값 |
|---|---|
| 브랜치 | `claude/judge-rules-dedup` — `59c3b48`(fix) · `1dad041`(게이트 상수·EVAL.md) · `04e7bf8`(새 테스트). 트리는 최초 단일 커밋 `40c4763`과 동일(확인) |
| 분기점 | main `44c80ed`(merge-base 확인). 검증 시점 main도 `44c80ed` — 시험 병합 트리 = 브랜치 트리 |
| 환경 | `.venv` Python 3.12.6, torch·transformers 미설치 |

## 판정 요약

**합쳐도 된다 — 조건 없음.** 사용자 승인에 따라 이 보고서와 함께 main에 바로 합쳤다(push 안 함).
결함 설명은 데이터와 맞고(§2), 기준은 제품 경로 `judge_registry`와 같다(§2). 주인 세션이 보고한 전후 수치는 **전부 독립 재현**됐다(§3).
recorded 기준선 불변, 가짜 게이트 상수 = `print_current()`, pair_coverage 합 69/75 → **68/75**(목표 ≥ 66 유지). 틀린 항목은 없고 §5에 참고 관찰 3건만.

## 1. 범위

`git diff --name-only 44c80ed..claude/judge-rules-dedup` 5개 파일(+178 / −26):
`scripts/eval/judge_eval.py`(`collect_episode_rules` 신설, `load_context`가 사용) · `scripts/eval/datagen_common.py`(`_rules_before` → 같은 함수) ·
`tests/eval/test_judge_learned_fake_baseline.py`(q1 쌍 수·지문 갱신 + 주석) · `tests/eval/test_judge_rule_assembly.py`(신규) · `docs/EVAL.md`(절 하나 + 표 각주).
`app/`·`weave_core/`·코퍼스 무변경. 워크트리 `C:\glitch_judge_rules_dedup` 작업 트리 깨끗함.

## 2. 결함 설명 vs 데이터·제품 경로

확정 원장 화 문서의 `worldview_rules` 행 수 / 고유 `rule_id` / 고유 `norm_text` 본문(브랜치 트리에서 직접 셈):

| 작품 | 행 | 고유 ID | 고유 본문 | 레지스트리 규칙 | `load_context` (b)절 규칙 전 → 뒤 |
|---|---|---|---|---|---|
| WM | 13 | 13 | 13 | 12 | 12 → 12 (레지스트리 규칙을 그대로 씀 — 조립 함수 미사용) |
| Q1 | 4 | **3** | 4 | 0 | 3 → **4** (`NEW:열쇠_관리_규칙@ep3`) |
| Q3 | 41 | **38** | 41 | 0 | 38 → **41** (`RULE_007@ep4` · `RULE_007@ep5` · `RULE_008@ep5`) |
| 셜록 | 9 | 9 | 9 | 0 | 9 → 9 |

- Q3 `RULE_007`은 3·4·5화에서 본문이 전부 다르다(3화 "규정 7조 … 큐 오픈 시각 미정", 4화 "기지장은 … 격벽 관리 권한 임시 변경", 5화 "규정 4조 안전 보고 … 승인 없이"). `RULE_008`은 4·5화 둘. Q1은 `NEW:열쇠_관리_규칙` ID가 두 화에 다른 본문. 옛 코드(`rid not in seen`)가 이 4개를 버렸다는 설명이 맞다.
- 고유 본문 수 = 행 수(4작품 모두)이므로 본문 기준으로 바꿔도 **빠지는 규칙은 없고 더해지기만** 한다 — 주인 세션의 주장 확인.
- 제품 경로 `app/pipeline/weave.py judge_registry`: `norm = w_reg.norm_text(text); if not text or norm in seen: continue` — **같은 함수, 같은 기준**. 다른 점은 ID 표기만: 제품은 캐논 `entity_key`(RULE_ 접두면 그대로, 아니면 `RULE_Uxxx`), 하네스는 `rule_id`가 앞 규칙과 겹치면 `<id>@ep<화>`(`#2`…). 판정 패킷에서 ID는 표시용이라 기준 일치가 핵심이고 그것은 맞다.
- `collect_episode_rules`는 앞 회차만 보고 ID를 정하므로 `_rules_before(k)`(k화 이전 자르기)와 전체 호출이 같은 규칙에 같은 ID를 준다 — 새 테스트 `test_rules_before_matches_load_context_prefix`가 k=2..6 전부 고정 ○. 학습 데이터 경로가 전부 `_rules_before`를 거치는지: `datagen_common.load_episodes`(154행) · `export_pairs.base_replay`(101행) · `counterfactual`(151행) ○ — `label_pairs`·`replay_upto`는 `load_episodes` 경유.
- 새 테스트 `test_dedup_by_normalized_text_not_id`의 두 본문("규정 7조: 통신은 22시" / "규정_7조:  통신은 22시 (원문)")은 `norm_text`(NFKC·괄호 제거·밑줄→공백·공백 압축·소문자)로 같아진다 ○.

## 3. 재측정 재현

**(a) recorded 기준선**: `tests/eval`(test_judge_baseline 포함) 통과 — 패킷 (b)절을 쓰지 않으니 구조적으로 불변.

**(b) 가짜 게이트 상수 = `print_current()`**(브랜치 트리): total·per_work·detected·pairs·fingerprint 다섯 항목 전부 동일.
쌍 수 wm 1440 · q1 **7145**(전 7142) · q3 5112 · sherlock 6507, 지문 q1 **`0fd1d4381d44d8dc`**(전 `65f3a1c60bcf05ef`), 나머지 셋 그대로.

**(c) `pair_coverage` 4작품 전후**(main `44c80ed` vs 브랜치, 전수 실행 diff): 바뀐 행은 Q1과 합만.

| | 전 | 뒤 |
|---|---|---|
| Q1 근거 포함 v2@24 matched / all | 18 / 19 | **17 / 18** (v1@32 13/14 · v1@∞ 19/20 그대로) |
| 합 v2@24 matched / all | 63/69 · 69/75 | **62/69 · 68/75** (G0 목표 ≥ 66/75 유지) |
| Q1 v2@24 kind rule / past | 897 / 2133 | 1196 / 1845 (knowledge 2587→2579) |
| Q3 v2@24 | 전부 그대로(규칙 할당량 4가 이미 차 있음) — v1@∞ rule 8094→8733만 | |
| WM·셜록 | 변화 없음 | |

장면 단위(스크래치 `scene_cov.py`, 두 트리에서 전 장면·전 조건 비교): 근거 포함이 뒤집힌 장면은 **Q1 scene_32 하나**(v2@24 matched·all, hit `EVENT_1_3` → 없음).
그 장면 쌍 288개 구성: rule 36 → 48(규칙 3→4개 × 가설 12), past 84 → 72. 즉 전에는 규칙이 3개라 할당량 4의 남은 칸 1개가 다음 kind로 넘어가 past 7번째
전제(EVENT_1_3)가 들어갔는데, 복구된 `NEW:열쇠_관리_규칙@ep3`가 그 칸을 채웠다. **조립 결함이 아니라 할당량 넘김의 부수 효과**이고, 복구 규칙이 들어간 것이 맞는
동작이다(v1@∞ 19 그대로). 주인 세션·EVAL.md 설명과 일치.

복구 규칙이 들어간 장면(v2@24): Q1 45/45(규칙 4개 ≤ 할당량 4라 전 장면), Q3 **33**/45 — 보고와 같음. Q3 scene_04에 `RULE_008@ep5` 포함 ○. 그 라벨의
`violated_fact`("… 5월 10일 본부 회신이 현 운영 체제 유지를 명시")가 복구 본문("본부 청문 결과 전까지 현 운영 체제 유지…")과 맞는다 — 전에는 이 정답 근거 규칙이
패킷에 없었다.

## 4. 시험 병합·테스트

스크래치 워크트리(detach, main `44c80ed`)에 `merge --no-ff` → 충돌 없음, **병합 트리 = 브랜치 트리**(`git diff --quiet` 확인).
`pytest tests/eval tests/test_learned_judge.py`(분리 DB): **127 passed**(23초). 전체 suite는 J 세션이 busy(GPU 작업 가능)라 지시대로 돌리지 않았다 —
이 브랜치는 `scripts/eval`·`tests/eval`·docs만 바꾸고, 그 밖의 suite는 직전 main(`44c80ed`와 코드 동일한 스크래치)에서 462 통과한 상태다.

본 병합: main에 `git merge --no-ff claude/judge-rules-dedup` + 이 보고서 커밋. 그 뒤 main에서 같은 테스트 재실행(결과는 세션 보고에).

## 5. 틀린 항목

없음. 참고 관찰 3건(병합과 무관, 고칠 필요 없음):

1. 지시문의 "Q3 +4"는 실제로 Q1 +1 · Q3 +3(합 +4)이다 — 주인 세션이 이미 바로잡았다.
2. WM은 화 문서 규칙 13행인데 레지스트리 규칙 12개를 쓴다(`registry["worldview_rules"]`가 있으면 조립 함수를 안 탐). 이 브랜치 이전부터의 동작이며 WM 규칙
   출처는 레지스트리로 고정돼 있으므로 그대로 둔다. 13번째 행이 무엇인지는 WM 코퍼스 쪽에서 볼 일.
3. `rule_id`가 없는 규칙은 `RULE_U`(첫 것은 접미 없이, 다음부터 `RULE_U@ep<n>`·`#2`)가 된다 — 코퍼스엔 그런 행이 없어 지금은 닿지 않는 경로.

## 참고: 임시 파일(저장소 밖, 스크래치패드)

`scene_cov.py`·`cov_before.json`·`cov_after.json`(장면 단위 전후) · `pc_dedup_before.md`·`pc_dedup_after.md`(pair_coverage 전후) · `merge_dedup_eval_tests.txt`.
스크래치 워크트리 `merge_dedup`은 지웠다.
