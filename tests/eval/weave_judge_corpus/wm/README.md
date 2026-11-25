# wm — WM(근미래 SF 웹소설, Weave 개발 코퍼스)

원본: Weave 저장소 `event_sourcing` 커밋 **`9180c3a`**의 `eval_corpus/`. `git show 9180c3a:<경로>`로 꺼냈다.

**원문 출처:** WM 이야기(1~5화와 평가 장면)는 문학 연구 논문의 방법을 적용하도록 지시받은 LLM이 쓴 글이다.
제3자 저작물이 아니라 이 프로젝트의 개발용 코퍼스다. 이 저장소에는 원문이 없고 추출된 사건만 있다.
사건 파일의 `source_text` 필드는 그 LLM 작성 원문을 인용한다.

## 이 폴더에 있는 것

| 이 폴더 | 원본 | 내용 |
|---|---|---|
| `reextract/scene_NN_event_v2_r{1,2,3}.json` (30장면 × 3벌 = 90개) | `eval_corpus/scene_NN_event_v2_r{1,2,3}.json` | 같은 장면 원문을 추출 프롬프트 v2로 3번 다시 추출한 결과(반복 추출 일치도용) |

## 이 폴더에 없는 것 (이미 저장소에 있음)

라벨·원본 장면 사건·1~5화 원장·레지스트리는 체커 게이트 코퍼스 `tests/eval/weave_checker_corpus/`를 그대로 쓴다
(`labels.json`, `scene_NN_event.json`, `episode_01..05.json`, `entity_registry.json`).
그 폴더는 커밋 `8d89349`에서 꺼낸 것인데, `git diff 8d89349 9180c3a -- eval_corpus`가 비어 있어 9180c3a와 내용이 같다.
체커 게이트 데이터는 건드리지 않으려고 재추출 3벌만 이 폴더에 따로 두었다.

동결 열(종단 측정)의 WM 판정은 **Claude 추출 원장(v4c 패킷)** 위에서 했다(`handoff/longitudinal_report.md` §0-6) —
Q1·Q3의 nemotron 원장과 추출 열이 다르다.

## 라벨 분포 (유형 × expected_layer × has_contradiction) — `weave_checker_corpus/labels.json`

| 유형 | expected_layer | has_contradiction | n |
|---|---|---|---|
| 상태 | checker | true | 1 |
| 상태 | judge | true | 7 |
| 설정 | judge | true | 6 |
| 없음 | none | false | 10 |
| 지식 | checker | true | 3 |
| 지식 | judge | true | 3 |
| **합계** | | | **30** |

WM 라벨에는 authored_by가 없다(Weave 채점기는 "llm:claude (WM 소급)"으로 채운다).
