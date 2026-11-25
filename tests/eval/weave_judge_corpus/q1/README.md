# q1 — Q1 금호탕 — 1987년 가족극(장편소설 일반)

원본: Weave 저장소 `event_sourcing` 커밋 **`9180c3a`** (`%USERPROFILE%/Downloads/event_sourcing`, main)의 `corpus_q1_geumhotang/`.
작업 트리가 아니라 **커밋에서** `git show 9180c3a:<경로>`로 꺼냈다(작업 트리는 autocrlf로 줄 끝만 CRLF — 내용 동일).
크기 722,890 바이트.

## 복사한 파일

| 이 폴더 | 원본 (`corpus_q1_geumhotang/…`) | 내용 |
|---|---|---|
| `labels.json` | `labels.json` | 45장면 라벨(블라인드 이중 라벨 G7 인수분). `q1_geumhotang-v1.0-accepted-20260929` |
| `evidence_event_map.json` | `evidence_event_map.json` | 라벨 근거 인용 → 원장 사건 ID 매핑, `ledger_absent_scenes`(원장 부재 장면 6건) 포함 |
| `entity_registry_q1.json` | `entity_registry_q1.json` | 코퍼스 제작 시 엔티티 레지스트리 |
| `scenes/scene_NN.txt` (45개) | `scenes/` | 장면 원문(심어진 모순 포함) |
| `ledger_nemotron_ultra/episode_01..05.json` | `ledger_nemotron_ultra/` | 1~5화 확정 원장(추출 사건 로그) |
| `ledger_nemotron_ultra/registry_live.json` | `ledger_nemotron_ultra/` | 원장 추출 때 병합된 레지스트리 — 판정 패킷이 실제로 쓴 것(`build_b_packets.py`) |
| `ledger_nemotron_ultra/scene_events/scene_NN_event.json` (45개) | `ledger_nemotron_ultra/scene_events/` | 장면별 추출 사건(판정 대상) |

## 원장 선택 근거

`ledger_nemotron_ultra/`. 근거: 동결 열 패킷을 만든 `handoff/long_packet_meta_q1.json`의 `ledger_dir`가 `corpus_q1_geumhotang/ledger_nemotron_ultra`이고, `handoff/longitudinal_run_record.json` `packets.q1`이 "확정 원장 ledger_nemotron_ultra"라고 적는다. `labels.json` description도 "확정 원장 = ledger_nemotron_ultra/". `score_longitudinal.py`는 원장을 직접 읽지 않고 이 원장으로 만든 패킷의 판정 결과(`free_judge_raw/nemotron_ultra_long_q1`)만 읽는다. `ledger_nemotron_ultra_replay_v2/`는 개선 1차(B-026 knows 누적)의 재생 스냅샷(같은 `episode_0N.json`을 다시 재생한 산출물)이라 원장 자체가 아니므로 제외했다.

## 제외한 것

`_discarded*`, `raw/`(LLM 원 응답), `chunks/`, `sources*/`(회차 원문), `labels_draft*.json`, `*_report.json`·`new_id_mapping.json`(중간 산출물),
`scene_events/extract_raw_ep06.txt`, `scene_events_positive_control/`(G5 양성 대조 재추출 — `scene_events`가 45건 다 있어 패킷에서 쓰이지 않음),
`worldview_rules_v2*.json`(개선 열 rnc 패킷의 수작업 규칙 — 동결 열 아님), 제작 문서(`setting_book.md`·README·검증 스크립트 등).

## 라벨 분포 (유형 × expected_layer × has_contradiction)

| 유형 | expected_layer | has_contradiction | n |
|---|---|---|---|
| 상태 | checker | true | 2 |
| 상태 | judge | true | 6 |
| 설정 | checker | true | 2 |
| 설정 | judge | true | 4 |
| 없음 | - | false | 25 |
| 지식 | checker | true | 2 |
| 지식 | judge | true | 4 |
| **합계** | | | **45** |

집필 주체(authored_by): `human` 5, `llm:claude-opus-5` 40 (모순 장면 중 `human` 3, `llm:claude-opus-5` 17).

> 원본 `labels.json` description: "expected_layer는 제작 측 추정으로 격하 — 분석에 쓰지 않는다"(Q1·Q3).
> 분포 표에는 적되, 담당 층(checker/judge) 분할을 주 지표로 쓸 때는 이 단서를 함께 적을 것.
