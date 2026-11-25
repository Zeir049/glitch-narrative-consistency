# Glitch (백엔드 몸통 + 프론트)

장편 서사 모순 탐지 서비스 **Glitch**. 이 저장소는 백엔드 게이트웨이(인증 / 프로젝트·회차 관리 /
원문 저장 / 파이프라인 중개 / 캐논 병합)와, 그 백엔드가 같은 오리진 `/`로 서빙하는 프론트(`frontend/`)를 담는다.

- 처음 읽을 문서: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) (요청 흐름·상태 머신·결정 기록)
- API 계약(프론트 연동): [`docs/glitch_api_contract.md`](docs/glitch_api_contract.md)
- 코딩 규칙: [`CLAUDE.md`](CLAUDE.md) · 프론트 구조·데모 로그인: [`INTEGRATION.md`](INTEGRATION.md)

## 스택
Python 3.11+ · FastAPI(async) · PostgreSQL 16 · SQLAlchemy 2.0 · Alembic · arq(Redis) · 프론트는 빌드 없는 ES 모듈

## 빠른 시작 (Windows, Docker Desktop)

PostgreSQL을 따로 설치하지 않는다. DB(127.0.0.1:5433)와 Redis(127.0.0.1:6379)는 compose 컨테이너로 띄운다.

```powershell
# 1) 클론 + 가상환경 + 의존성
git clone <repo> glitch_integrated
cd glitch_integrated
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"

# 2) 환경변수 — 복사한 뒤 REDIS_PASSWORD 와 REDIS_URL 의 change-me 를 같은 값으로 바꾼다
copy .env.example .env
notepad .env

# 3) DB + Redis 컨테이너 (REDIS_PASSWORD 가 없으면 compose 가 기동을 거부한다)
docker compose up -d db redis

# 4) 마이그레이션
.\.venv\Scripts\python.exe -m alembic upgrade head

# 5) 분석 워커 + 서버 (창 두 개. 반드시 127.0.0.1 — 0.0.0.0 금지)
$env:PYTHONUTF8 = "1"
.\.venv\Scripts\python.exe -m arq app.workers.worker.WorkerSettings
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

3~5단계는 `start_integrated.bat` 한 번으로 대신할 수 있다(db·redis 컨테이너 → 워커 창 → 서버).

- 접속 주소 (서버는 `127.0.0.1`에만 바인딩 — `localhost` 접속도 그 바인딩으로 들어온다):
  - **구글 로그인:** <http://localhost:8000/> — Firebase 승인 도메인이 `localhost`라 `127.0.0.1`에서는 팝업이 거부된다
  - **데모 로그인:** <http://127.0.0.1:8000/?demo=1> 또는 <http://localhost:8000/?demo=1>
  - API 문서 `/docs` · 상태 `/health`
- **데모 로그인**(구글 팝업 없이, `?demo=1` 옵트인): `.env`에서 `FIREBASE_MODE=fake`로 바꾸고 서버를 다시 띄운 뒤
  `?demo=1` 주소로 접속 → "시작하기". 시드 계정 `cho@example.com`으로 로그인된다. 탭을 닫거나 `?demo=0`이면 해제.
  fake 모드는 토큰을 검증하지 않으므로 **로컬 전용**(공개 URL 금지).
- `.env.example`은 `PIPELINE_MODE=fake`(고정 예시 결과)다. 실제 분석은 아래 "AI 서버까지 함께 돌리기".

### (선택) 네이티브 PostgreSQL 사용

호스트에 PostgreSQL이 이미 있으면 compose `db` 대신 쓸 수 있다. DB·계정을 만들고
`.env`의 `DATABASE_URL` / `DATABASE_URL_SYNC` 포트·이름만 바꾼다(예: `localhost:5432`).

```powershell
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" -U postgres -h localhost -c "CREATE USER glitch WITH PASSWORD 'glitch';"
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" -U postgres -h localhost -c "CREATE DATABASE glitch_integrated OWNER glitch;"
```

이때 `start_integrated.bat`이 띄우는 `db` 컨테이너는 쓰이지 않지만 127.0.0.1에만 열리므로 무해하다.
`docker compose up -d redis`만 띄워도 된다.

## 구조 (의존 방향: 위 → 아래)
```
app/api/         FastAPI 라우터. HTTP 입출력·검증만.
  app/service/   비즈니스 로직 · 트랜잭션 경계 · 파이프라인 호출.
    app/repository/  DB 접근(SQLAlchemy). SQL은 여기에만.
      app/models/    ORM 모델
app/schemas/     Pydantic 요청/응답 (ORM과 분리)
app/pipeline/    AnalysisPipeline 인터페이스 + fake/weave/local/remote/replay 구현 (weave_core/ 는 수정 금지)
app/workers/     arq 큐·워커·작업(추출/검수)
frontend/        프론트 (store.js → api.js 로만 서버 데이터에 접근)
```
전체 파일 목록은 `CLAUDE.md` "폴더 구조".

## 파이프라인 교체 포인트
`PIPELINE_MODE=fake|weave|local|remote|replay`로 주입한다. 몸통은 `AnalysisPipeline`
(`extract_chapter` / `review_chapter` / `health`, `app/pipeline/interface.py`)만 안다.

## Weave 엔진으로 실제 분석하기 (`PIPELINE_MODE=weave`)
AI 서버 없이 몸통 프로세스 안에서 Weave 엔진(`app/pipeline/weave_core/`, 출처는 `VENDORED.md`)을 돌린다.
추가 의존성은 없다(표준 라이브러리만).

| 단계 | Weave 가 하는 일 | LLM 호출 |
|---|---|---|
| 1차 추출 | 원문 → 사건 JSON(2500자 단위 분할) → 엔티티 레지스트리 병합 → canon_delta | 조각 수만큼 |
| 2차 검수 | 저장된 앞 회차로 원장 재생 → 규칙 검사기(LLM 없음) + 장면별 LLM 판정 k회 | 장면 수 × k |

```powershell
# .env
#   PIPELINE_MODE=weave
#   WEAVE_LLM_PROVIDER=openrouter        # openrouter | nim | anthropic
#   WEAVE_LLM_MODEL=nvidia/nemotron-3-ultra-550b-a55b:free
#   WEAVE_API_KEY=<provider 키>           # .env 는 git 에 올라가지 않는다
#   JOB_TIMEOUT_SECONDS=3600              # 무료 모델은 느리다
#   WEAVE_KNOWS_MODE=accumulate           # accumulate(기본) | legacy
```

- **코어 사본 기준: event_sourcing `8d89349`** (B-039). `app/pipeline/weave_core/`는 그 커밋의 코어와 내용이 같다(`weave_pipeline.py`만 교체, 나머지는 줄바꿈 차이뿐).
- `WEAVE_KNOWS_MODE`: `accumulate`(기본)는 인물이 알게 된 정보(knows)의 획득 이력 전체를 판정 프롬프트 (c)절에 싣는다.
  `legacy`는 덮어써 최종 1건만 보인다(이전 동작). 그 밖의 값이면 서버 시작이 거부된다.
  accumulate에서는 (c)절이 길어지고, 재생 경고 `knows_op_coerced`·`knows_value_missing`이 리뷰 데이터의 `replay_warnings`에 붙을 수 있다.
  기존 작품도 다음 검수부터 새 모드로 재생된다. 규칙 검사기 결과는 바뀌지 않는다(`ledger.knowledge`를 읽음).
- 작품별 내부 상태는 `WEAVE_DATA_DIR`(기본 `weave_data/`)에 쌓인다 — **DB 와 같이 백업**해야 한다.
  DB 만 복원하면 이후 검수의 원장 재생이 어긋난다.
- 원장에는 **저장(saved)된 회차만** 들어간다(Weave 의 "사람이 확정한 회차만" 원칙).
- 모순 설명 앞머리: `[규칙 검사·강한 모순|약한 의심]` = 원장 대조, `[LLM 판정·…]` = k회 중 n회 검출.
- 1차 검토에서 지운 사건은 검수에서 빠진다. 사건 내용 수정은 아직 판정에 반영되지 않는다(추출 원본 기준).
- 무료(`:free`) 모델은 계정당 하루 50회 남짓이다 — 회차 하나에 수십 회가 들 수 있어 **서비스 운영에는 유료 모델**이 필요하다.
- 측정된 성능은 Weave 평가 코퍼스 기준뿐이다. 다른 장르·모델 조합은 측정되지 않았다.

**LLM 호출이 실패하면**

| 상황 | 동작 |
|---|---|
| 연결 끊김·응답 중간 절단·408/429/5xx | 5·10·20초 간격으로 3번 재시도 → 그래도 안 되면 그 회차 `failed` + 사유 표시 |
| 응답 없음 | 시도당 420초(`WEAVE_HARD_DEADLINE`)에서 포기 → 위와 같이 재시도 |
| JSON 이 깨짐(출력 절단) | 1번 다시 요청 → 그래도 깨지면 `failed` |
| 추출 도중 실패 | 작품 상태(레지스트리·사건 로그)를 **하나도 바꾸지 않는다** — 같은 회차를 다시 추출하면 된다 |
| 판정 일부 실패 | 남은 회차로 집계하되 검출률은 요청한 k회 기준, 설명에 "n회는 호출 실패"를 적는다 |
| 한 장면이라도 판정을 전혀 못 받음 | "모순 없음"으로 넘기지 않고 검수를 `failed` 로 — 다시 검수 |
| 429(한도 초과) | 아직 안 보낸 판정 호출은 보내지 않고 멈춘다 |
| 작업 취소(arq `JOB_TIMEOUT_SECONDS`) | 남은 호출을 보내지 않고, 뒤늦게 온 응답으로 파일을 쓰지 않는다 |
| 작업 중 프로세스가 죽음 | 남은 작품 잠금은 다음 작업이 pid 로 확인해 바로 걷어낸다 |

## 기능 플래그 (켜고 끄기)
`.env`에서 끄면 해당 API가 OpenAPI에서 통째로 사라진다:
`ENABLE_SUBSCRIPTION` · `ENABLE_SHARING` · `ENABLE_CHAT` · `ENABLE_NOTIFICATIONS` · `ENABLE_FIREBASE_AUTH`

모두 기본 false다. 구독·공유·채팅·알림은 프론트가 아직 부르지 않는 미완성 API이고
(설정 메뉴의 "구독 관리"는 "준비 중" 안내만 띄운다), 구독은 `PUT /me/subscription`으로 누구나 pro가 되는 표시용이라
공개 배포에서는 켜지 않는다. 테스트는 `tests/conftest.py`가 전부 켜고 돌린다.

API 문서(`/docs`·`/redoc`·`/openapi.json`)는 `ENABLE_API_DOCS`(기본 true, 배포 compose 는 false 로 강제).

## 교체 가능한 인프라 (구현체만 갈아끼움)
| 경계 | 설정 | 지금 | 나중 |
|------|------|------|------|
| 문서 파서 | (레지스트리 `app/parsing/`) | txt/md/docx/pdf | 확장자 추가 |
| 메일 발송 | `EMAIL_MODE` | console(로그) | smtp |
| 파일 저장 | `STORAGE_MODE` | local(`/uploads`) | s3 |
| 채팅 어시스턴트 | `CHAT_MODE` | fake | remote |
| 분석 파이프라인 | `PIPELINE_MODE` | fake | remote/local |
| 작업 큐 | `QUEUE_MODE` | arq(Redis) | (inline은 테스트 전용) |

## 데모 계정 (시드)
`SEED_ON_STARTUP=true`면 첫 기동 시 자동 주입(Settings 기본값은 false, `.env.example`이 로컬용으로 true로 켠다):
- 이메일 `cho@example.com` / 비밀번호 `glitch123` (plan: pro)
- 프로젝트 "금도끼 은도끼" — 1~3화 저장됨, 4화 신규

비밀번호가 여기 공개돼 있으므로 **공개 URL·배포에서는 `SEED_ON_STARTUP=false`**.
`APP_ENV=prod`(배포 compose가 강제)에서 true면 기동이 거부된다. 이미 만들어진 시드 계정은 지워지지 않으니
예전에 시드를 켜고 띄운 DB라면 그 계정을 직접 지우거나 비밀번호를 바꾼다.

## DB 직접 보기 (Adminer, 로컬 전용)
`docker compose --profile tools up -d adminer` → <http://127.0.0.1:8081> (서버 `db`, 사용자·비밀번호 `glitch`, DB `glitch_integrated`).
compose `db` 컨테이너용이다. 다 보면
`docker compose --profile tools stop adminer`. tools 프로필이라 `docker compose up -d`에는 뜨지 않는다. 터널로 열지 않는다.

## 백업
`backup.bat` — `.env`의 `DATABASE_URL_SYNC`가 가리키는 DB를 `backups\glitch_<시각>.sql`로 덤프한다.

## 테스트
```powershell
.\.venv\Scripts\python.exe -m pytest -q
```
테스트는 Redis db 1을 쓴다(`docker compose up -d redis` 필요).

## 브랜치
이 코드의 기준 브랜치는 `backend`다. 작업 브랜치(`fusion-*` 등)는 `backend`에서 만들고 `backend`로 PR을 연다.
(`main`·`AI`·개인 브랜치는 팀 원격의 다른 작업 줄기로, 지금은 `backend`의 조상이 아니다.)
