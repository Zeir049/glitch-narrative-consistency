# scripts/eval/probe — 추출 검증기 설계를 위한 읽기 전용 탐침

2026-11-06~07 판단 세션이 쓴 진단 스크립트. **채점기가 아니다**(동결 수치 아님). 결과와 해석은
[`docs/related-work-extraction-verification.md`](../../../docs/related-work-extraction-verification.md) §4(전제 검증·13건 재귀속)·§5(학습 데이터 후보).
LLM 호출 없음, 파일 쓰기 없음. 저장소 코드(`app/`)를 import 하지 않는다.

## Weave 원본 데이터 탐침 (인자: event_sourcing 저장소 경로)

입력: `corpus_q1_geumhotang/`·`corpus_q3_domec4/`의 `chunks/episode_NN_c*.txt`(원문), `ledger_nemotron_ultra/episode_NN.json`(원장), `labels.json`, `entity_registry_q?.json`, `setting_book.md`.

| 스크립트 | 무엇을 재나 |
|---|---|
| `grounding_probe.py` | ① 원장 `source_text`(사건·상태 변화·전제·지식)가 원문에서 찾아지는 비율 ② 수치 문장 탐지기의 반응률·미커버율 ③ 원장 부재 13건의 근거 문장이 탐지기에 걸리고 미커버인지 |
| `ungrounded_quotes.py` | 못 찾은 인용의 성격(다른 회차 / 설정집 / 앞 8자만 일치 / 어디에도 없음) + 최근접 원문 문장. "커버됐는데 원장 부재"인 Q1 문장을 덮는 원장 항목 |
| `number_preservation.py` | 항목 단위 인용만으로 커버리지 재계산 + **수치 보존 검사**(문장이 덮였는데 구조값에 그 수치가 없음) + 문장부호 정규화 뒤 진짜 못 찾은 인용 수 |
| `entity_coverage.py` | 레지스트리 엔티티가 원문에 등장하는데 그 회차 사건이 참조하지 않는 수(장소·사물·인물별) |

정규화 규약(세 스크립트 공통): 공백 제거, 따옴표·줄표 제거(`number_preservation`), 인용은 `...`·`…`·`⋯`·`(중략)`·` / `로 쪼갠 뒤 문장 단위로 다시 쪼개 부분 문자열 검색.
한국어 수사 → 숫자 변환은 `number_preservation.py`의 `kor2int`(일~만 단위, 한·두·세…, 첫째~넷째, 요일).

## 외부 데이터 탐침 (인자: 내려받은 파일 경로)

| 스크립트 | 입력 | 비고 |
|---|---|---|
| `inspect_contradoc.py` | `ContraDoc.json` (github.com/ddhruvkr/CONTRADOC, Apache-2.0) | pos/neg·doc_type·contra_type·scope·`ref sentences` 채움 |
| `inspect_fables.py` | `FABLES.json`이 든 폴더 (github.com/mungg/FABLES `data/FABLES.json.zip` 해제, MIT) | 주장 수·라벨 분포·근거 인용 실재 |
| `inspect_flawed_fictions.py` | HF `kahuja/flawed-fictions`의 parquet 4개가 든 폴더 (MIT) | **pyarrow 필요** |
| `external_pairs_count.py` | parquet 폴더 + `ContraDoc.json` | 뽑을 수 있는 (오류 문장, 충돌 문장) 쌍 수. **pyarrow 필요** |

pyarrow는 프로젝트 의존성이 아니다. 별도 폴더에 `pip install --target <폴더> pyarrow` 뒤 `PYTHONPATH=<폴더>`로 돌린다.
내려받은 데이터는 저장소에 넣지 않는다(각각 빈 폴더에 두고, 그 폴더 안에서 인터프리터를 띄우지 않는다).

## 실행 메모

- Windows 콘솔(cp949)에서는 각 스크립트가 `sys.stdout.reconfigure(encoding="utf-8")`를 한다. `python -I`로 돌려도 된다(환경변수 무시).
- 2026-11-07 보존 시점에 전부 한 번씩 다시 돌려 11-06 출력과 같은 수치를 냈다(§4-1 표: 인용 못 찾음 Q1 5/530·Q3 1/574, 수치·미커버 Q1 36·Q3 54/회차, 미참조율 0.29·0.31; §5-2: 쌍 526+291, ContraDoc story 125).
