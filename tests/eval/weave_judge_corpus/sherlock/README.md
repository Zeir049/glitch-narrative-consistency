# sherlock — 셜록 홈즈의 모험 — 고전 추리 5편 한국어 완역(작중 연대순 화1~5)

원본: Weave 저장소 `event_sourcing` 커밋 **`9180c3a`** (`%USERPROFILE%/Downloads/event_sourcing`, main)의 `corpus_sherlock_adventures/`.
작업 트리가 아니라 **커밋에서** `git show 9180c3a:<경로>`로 꺼냈다(작업 트리는 autocrlf로 줄 끝만 CRLF — 내용 동일).
크기 1,074,935 바이트.

## 복사한 파일

| 이 폴더 | 원본 (`corpus_sherlock_adventures/…`) | 내용 |
|---|---|---|
| `labels.json` | `labels.json` | 45장면 라벨(블라인드 이중 라벨 G7 인수분, 2026-08-29) |
| `evidence_event_map.json` | `evidence_event_map.json` | 라벨 근거 인용 → 원장 사건 ID 매핑 (원장 부재 목록 없음) |
| `entity_registry_sherlock.json` | `entity_registry_sherlock.json` | 코퍼스 제작 시 엔티티 레지스트리 |
| `scenes/scene_NN.txt` (45개) | `scenes/` | 장면 원문(심어진 모순 포함) |
| `ledger_v2_3/episode_01..05.json` | `ledger_v2_3/` | 1~5화 확정 원장(추출 사건 로그) |
| `ledger_v2_3/registry_live.json` | `ledger_v2_3/` | 원장 추출 때 병합된 레지스트리 — 판정 패킷이 실제로 쓴 것(`build_b_packets.py`) |
| `ledger_v2_3/scene_events/scene_NN_event.json` (45개) | `ledger_v2_3/scene_events/` | 장면별 추출 사건(판정 대상) |

## 원장 선택 근거

`ledger_v2_3/` (지시대로). `build_b_packets.py` `WORKS["sherlock"]`, `map_evidence_events.py`(evidence_event_map 생성), `handoff/b_track_design.md` §B가 모두 이 원장을 쓴다. **셜록은 동결 열(종단 측정 WM·Q1·Q3)에 들어 있지 않다** — 동결 열 원시 결과가 없으므로 `recorded` 모드 기준선에는 셜록이 없다. learned·llm 모드의 추가 평가 작품으로만 쓴다.

## 제외한 것

`_discarded*`, `raw/`(LLM 원 응답), `chunks/`, `sources*/`(회차 원문), `labels_draft*.json`, `*_report.json`·`new_id_mapping.json`(중간 산출물),
`scene_events/extract_raw_ep06.txt`, `scene_events_positive_control/`(G5 양성 대조 재추출 — `scene_events`가 45건 다 있어 패킷에서 쓰이지 않음),
`worldview_rules_v2*.json`(개선 열 rnc 패킷의 수작업 규칙 — 동결 열 아님), 제작 문서(`setting_book.md`·README·검증 스크립트 등), `probe_events/`, `v23_trial_*`/`v3_trial_*`, `probes/`, 번역 로그.

## 라벨 분포 (유형 × expected_layer × has_contradiction)

| 유형 | expected_layer | has_contradiction | n |
|---|---|---|---|
| 상태 | checker | true | 5 |
| 상태 | judge | true | 3 |
| 설정 | checker | true | 4 |
| 설정 | judge | true | 2 |
| 없음 | - | false | 25 |
| 지식 | judge | true | 6 |
| **합계** | | | **45** |

집필 주체(authored_by): `human` 5, `llm:claude-opus-5` 40 (모순 장면 중 `human` 3, `llm:claude-opus-5` 17).

> 셜록 `labels.json`의 expected_layer도 제작 측 추정이다(Q1·Q3 라벨 description은 "제작 측 추정으로 격하 — 분석에 쓰지 않는다"고 명시).
> 분포 표에는 적되, 담당 층(checker/judge) 분할을 주 지표로 쓸 때는 이 단서를 함께 적을 것.
