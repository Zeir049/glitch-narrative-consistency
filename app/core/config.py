"""환경 의존값은 전부 여기로. 코드에 하드코딩/비밀값 금지 (CLAUDE.md 규칙)."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# jwt_secret 기본값 — 저장소에 공개된 자리표시자. prod 에서 이 값이면 기동을 거부한다.
JWT_SECRET_PLACEHOLDER = "change-me-in-production-please-32chars-min"
JWT_SECRET_MIN_LENGTH = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # 실행 환경. prod 면 위험한 설정(자리표시자 JWT 비밀값, fake Firebase, 데모 시드)으로는
    # 기동 자체를 거부한다(아래 _refuse_unsafe_prod) — api·worker 모두 Settings 를 만들 때 걸린다.
    # docker-compose.prod.yml 이 prod 로 강제한다. 로컬 개발은 dev(기본).
    app_env: Literal["dev", "prod"] = "dev"

    # DB
    database_url: str = "postgresql+asyncpg://glitch:glitch@localhost:5432/glitch"
    database_url_sync: str = "postgresql+psycopg2://glitch:glitch@localhost:5432/glitch"

    # 인증(JWT)
    jwt_secret: str = JWT_SECRET_PLACEHOLDER
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440

    # 파이프라인 주입
    pipeline_mode: str = "fake"  # fake | weave | local | remote | replay
    pipeline_remote_url: str = ""
    # remote 모드: AI 서버와 나눠 갖는 비밀 토큰(AI 서버의 AI_SERVER_TOKEN 과 같은 값).
    # "Authorization: Bearer <토큰>" 으로 보낸다. 비우면 헤더 없음 — 로컬 개발 전용.
    pipeline_remote_token: str = ""
    # remote 모드: 추출·검수 HTTP 요청 1건 상한(초). 시간 사슬: LLM 1회 < 이 값 < job_timeout_seconds.
    # 검수는 LLM 최대 2회(추출 보충) + 임베딩이라 AI 서버 ANTHROPIC_TIMEOUT_SECONDS(420)×2 + 120 이상.
    # 이보다 짧으면 몸통이 먼저 포기해도 AI 서버는 계속 LLM 을 부른다(비용·작품 잠금 점유).
    pipeline_timeout_seconds: int = 960
    # remote 모드: AI 서버가 echo(고정 샘플 응답)면 분석을 거부한다 — 샘플이 캐논에 섞이지 않게.
    # 배선 확인용으로 일부러 echo 를 쓸 때만 true.
    pipeline_allow_echo: bool = False
    # replay 모드: 단계(추출/검수)마다 기다리는 시간(초) — 진행 상태가 화면에 보이게
    replay_delay_seconds: float = 3.0
    # replay 모드: 어느 작품의 자료를 돌려줄지(app/pipeline/replay_data/<작품>/) — inception | interstellar
    replay_story: str = "inception"

    # weave 모드: Weave 엔진(app/pipeline/weave_core/)을 같은 프로세스에서 실행한다.
    #   추출(LLM) → 엔티티 레지스트리 → 확정 원장 재생 → 규칙 검사기(checker) → LLM 판정 k회
    # 작품별 내부 상태(레지스트리·화별 사건 로그·판정 원문)는 weave_data_dir/<project_id>/ 에 쌓인다.
    weave_data_dir: str = "weave_data"
    weave_llm_provider: str = "openrouter"  # openrouter | nim | anthropic
    weave_llm_model: str = "nvidia/nemotron-3-ultra-550b-a55b:free"
    weave_extract_model: str = ""  # 비우면 weave_llm_model
    weave_judge_model: str = ""    # 비우면 weave_llm_model
    # 키는 provider 에 맞는 것 하나만. 비우면 OPENROUTER_API_KEY / NVIDIA_API_KEY / ANTHROPIC_API_KEY 환경변수를 본다.
    weave_api_key: str = ""
    weave_max_tokens: int = 8192
    weave_temperature: float = 0.2
    weave_reasoning_off: bool = True  # openrouter 전용: reasoning 끔(Weave 무료 열 측정 조건)
    weave_judge_k: int = 3          # 판정 반복 횟수 (Weave 표준 k=3)
    weave_judge_majority: int = 2   # k회 중 몇 회 이상이면 '검출'
    weave_judge_unit: str = "scene"  # scene | event | both
    # 판정 호출 동시 실행 수(장면×k회 전체를 한 풀에서). 1 이면 원래대로 하나씩. 무료 한도는 분당 20회
    weave_judge_concurrency: int = 3
    weave_retrieval: str = "full"    # full | ers | full_source
    # 학습 judge v2(설계 문서 §10) 서비스 연결 — 기본 꺼짐. 켜면 검수 때 회차 문장 ↔ 앞 회차(저장된 회차) 원문 판정을 더한다
    judge_v2_enabled: bool = False
    judge_v2_model_dir: str = ""                # 모델 폴더(저장소 밖). 비면 켜도 쓰지 않는다
    judge_v2_threshold: float | None = None     # 없으면 모델 v2_meta.json 의 dev 기준값
    judge_v2_temperature: float | None = None   # 없으면 모델 v2_meta.json 의 온도(그것도 없으면 1)
    judge_v2_device: str = "auto"               # auto | cuda | cpu
    judge_v2_max_items: int = 5                 # 회차당 올리는 v2 모순 수 상한
    # judge 앞 단계 개선 P(app/pipeline/prejudge.py, 설계 문서 §0-9). 기본 전부 꺼짐 = 기존 출력과 바이트 단위로 같다
    weave_prejudge_f1: bool = False  # F1 화 안 증분 원장(사건마다 같은 화 앞 사건을 적용한 스냅샷)
    weave_prejudge_f2: bool = False  # F2 추출 후처리(구속→transient·목격자/주체 위치·location→unknown 버림·NEW: 장소 해소)
    weave_prejudge_f3: bool = False  # F3 judge 프롬프트 후처리((e)절 문구·스냅샷 경과/출처·지침 한 줄)
    # 개선 P2(설계 문서 §0-9 "개선 P2", 코어 점검 H1–H3·M1·M2·M4–M6). 기본 전부 꺼짐
    weave_prejudge_f2_5: bool = False  # F2⑤ location assumed_state 버림(H1)
    weave_prejudge_f4: bool = False    # F4 경계 인식 정규화 후 코어에 mapping={} (H2)
    weave_prejudge_f5: bool = False    # F5 패킷 재묶음: 연속·같은 location_id, 최대 6 (H3). F1 경계도 이 묶음
    weave_prejudge_f6: bool = False    # F6 memory·dream·vision 의 state·위치 재생 제외(M1)
    weave_prejudge_f7: bool = False    # F7 스냅샷 ID 뒤 (이름)·transient 문구 보정(M4)
    weave_prejudge_f8: bool = False    # F8 비actual structural_*·possesses_removed → 참고(서비스 화면, M2·M6)
    weave_prejudge_f9: bool = False    # F9 같은 화 앞 사건을 (e)절 끝에(M5①)
    weave_chunk_chars: int = 2500    # 추출 분할 크기. nemotron 무료 열의 확정 조건 C=2500 (5000은 출력 절단)
    # 판정 결과 중 화면에 올릴 최소 검출률(0~1). 0 이면 k회 중 1회라도 양성이면 올린다.
    weave_min_detection_rate: float = 0.34
    # 인물이 알게 된 정보(knows)의 원장 재생 방식(B-039). accumulate=획득 이력 전체를 판정 프롬프트 (c)절에 싣는다,
    # legacy=덮어써 최종 1건(이전 동작). 그 밖의 값은 시작 시 거부된다.
    weave_knows_mode: Literal["legacy", "accumulate"] = "accumulate"
    # Weave LLM 호출 녹화·재생(app/pipeline/llm_record.py). off=그대로 호출, record=호출하고 ok 응답을
    # weave_llm_record_dir 에 남김, replay=녹화본만 돌려줌(네트워크 0회, 없는 호출이면 즉시 예외). 평가·회귀 게이트용.
    weave_llm_record: Literal["off", "record", "replay"] = "off"
    weave_llm_record_dir: str = "weave_llm_record"
    # ① 추출 관문(app/pipeline/extract_gate.py). 추출할 때마다 사건·상태 변화를 확정/잠정으로 등급 매겨
    # weave_data_dir/<project>/gates/ 에 남기고, 캐논 사건 attrs.trust 에 INSERT 시점 등급을 적는다.
    # 검수의 판정 원장: off=등급 무시(이전 동작) | annotate=원장 그대로, 잠정 사건 ID 만 review_NN.json 에 기록
    #                   | exclude=저장된 회차의 잠정 사실을 빼고 원장을 재생한다
    weave_gate_mode: Literal["off", "annotate", "exclude"] = "off"
    # 결정적 추출 검증기(app/pipeline/extract_verifier, LLM 0회). annotate=등급 파일에 표시(mark) 신호·승격 후보를
    # 옆 키로 더한다(등급·exclude·attrs.trust 는 그대로) | off=돌리지 않음. WEAVE_GATE_MODE=off 면 무시한다
    weave_verifier: Literal["off", "annotate"] = "off"
    # 원장에 값이 있는데 from="unknown" 인 상태 변화를 잠정으로 볼지
    weave_gate_unknown_from: bool = True
    # 판정기: llm(LLM k회, 기본) | learned(학습 judge — 판정 LLM 0회, k=1·majority=1 강제)
    weave_judge_mode: Literal["llm", "learned"] = "llm"
    # learned 백엔드: hf(transformers cross-encoder, pip install -e .[judge]) | fake(결정적 휴리스틱, 테스트용)
    weave_judge_backend: Literal["hf", "fake"] = "hf"
    weave_judge_model_dir: str = ""   # hf 모델 폴더(저장소 밖). 결과에는 파일 sha256 만 남는다
    # 모순 확률 기준값. 비우면 모델 폴더 judge_meta.json 의 threshold(없으면 0.5)
    weave_judge_threshold: float | None = None
    weave_judge_batch_size: int = 16  # 추론 배치 크기 — 결정성을 위해 고정
    # hf 추론 결정성(설계안 §2-5). threads: CPU 스레드 수(비우면 torch 기본, interop 은 1로 고정)
    weave_judge_threads: int | None = None
    # 비우면 모델 폴더 judge_meta.json 값(학습과 같은 조건, 없으면 384·bucket). 정밀도·어텐션은 meta 만 따른다
    weave_judge_max_length: int | None = None    # 쌍 토큰 상한(학습과 같은 값)
    weave_judge_padding: Literal["bucket", "max_length"] | None = None  # bucket=길이 버킷(128/256/384/512)
    weave_judge_device: Literal["auto", "cpu", "cuda"] = "auto"      # auto=cuda 가 있으면 cuda

    # 분석 작업 큐: inline(서버 프로세스 안에서 실행 — 테스트/단순 개발용)
    #            | arq(Redis 큐 + 별도 워커 프로세스 — 서버 재시작에도 작업 보존,
    #                  수평 확장 가능, 긴 LLM 호출이 API 서버를 점유하지 않음)
    queue_mode: str = "inline"
    redis_url: str = "redis://127.0.0.1:6379/0"  # compose 가 IPv4 루프백에만 연다(localhost=::1 이면 멈춤)
    # arq 작업 1건 상한(초). 파이프라인 HTTP 타임아웃(pipeline_timeout_seconds)보다 넉넉해야 한다
    # (사전 점검 health + DB 저장 여유). 넘으면 arq 가 작업을 끊는다. 워커가 기동 때 순서를 점검한다.
    job_timeout_seconds: int = 1200
    # 워커 동시 분석 수. CPU 임베딩(배포 VM)에서는 2 — 많으면 서로 느려져 타임아웃에 걸린다.
    worker_max_jobs: int = 2
    # 진행 중(extracting/reviewing) 회차가 이 시간(분) 넘게 안 바뀌면 워커가 죽은 것으로 본다.
    # 그 회차에 새 분석 요청이 오면 failed 로 기록하고 받아준다(영구 409 방지).
    # 실제 기준은 max(이 값, job_timeout_seconds//60 + 5) — 살아 있는 작업을 고착으로 오판하지 않게.
    analysis_stale_minutes: int = 20

    @property
    def analysis_stale_threshold_minutes(self) -> int:
        return max(self.analysis_stale_minutes, self.job_timeout_seconds // 60 + 5)

    # CORS (쉼표 구분 문자열 → 리스트)
    cors_origins: str = "http://localhost:3000,http://localhost:5173"

    # 시드 데이터 — 비밀번호가 README 에 공개된 데모 계정(cho@example.com, plan=pro)을 만든다.
    # 기본 off. 로컬 개발은 .env(.env.example 을 복사)가 true 로 켠다. prod 에서 true 면 기동 거부.
    seed_on_startup: bool = False

    # /docs·/redoc·/openapi.json — 개발 편의로 기본 켬. 배포는 false(prod compose 가 강제)
    enable_api_docs: bool = True

    # ── 기능 플래그 (켜고 끄기) ──────────────────────────────
    # 구독·공유·채팅·알림은 프론트가 아직 부르지 않는 미완성 API 라 기본 off(끄면 라우터 자체가 없다).
    # 특히 구독은 PUT /me/subscription 으로 누구나 pro 가 되는 표시용이다 — 공개 배포에서 켜지 말 것.
    enable_subscription: bool = False
    enable_sharing: bool = False
    enable_chat: bool = False
    enable_notifications: bool = False
    enable_firebase_auth: bool = False  # 구글 로그인(Firebase ID 토큰) 교환 엔드포인트
    # 시연 계정(읽기 전용): 켜면 POST /auth/demo 가 demo_account_email 계정 하나에만 토큰을 준다.
    # 계정은 DB에 미리 있어야 한다(만들지 않음). 켜져 있는 동안 그 계정은 조회만 가능(쓰기 403).
    enable_demo_account: bool = False
    demo_account_email: str = "demo@glitch.local"
    # 접속 허용 이메일(쉼표 구분, 대소문자 무관). 비우면 누구나(기본). 설정하면 목록 밖 계정은
    # 로그인·가입이 403 이고, 이미 받은 토큰도 막힌다. 시연 계정(켜져 있을 때)은 예외.
    allowed_emails: str = ""
    # 이메일·비밀번호 방식(/auth/register·login·forgot-password·reset-password). 끄면 404 — 구글 로그인만 남는다.
    enable_password_auth: bool = True

    # ── Firebase 검증기 주입: google(실검증) | fake(테스트 전용) ──
    firebase_mode: str = "google"
    firebase_project_id: str = ""

    # ── 교체 가능 인프라 ────────────────────────────────────
    email_mode: str = "console"      # console | smtp(추후)
    storage_mode: str = "local"      # local | s3(추후)
    upload_dir: str = "uploads"      # 썸네일/원문 파일 저장 루트(local 모드)
    # 회차 업로드 허용 확장자 (파서 레지스트리에 등록된 것)
    allowed_chapter_exts: str = "txt,docx,md,pdf"
    # 회차 업로드 상한 — 공개 터널에서 대용량 업로드로 메모리를 소진하지 못하게
    max_upload_bytes: int = 10 * 1024 * 1024   # 파일 10MB
    max_chapter_chars: int = 500_000           # 추출/입력 텍스트 50만 자
    max_thumbnail_bytes: int = 5 * 1024 * 1024  # 프로젝트 썸네일 이미지 5MB

    # Content-Security-Policy 전체 문자열. 비우면 app/core/security_headers.py 의 DEFAULT_CSP
    # (프론트가 쓰는 Firebase 출처만 허용). Firebase 프로젝트·도메인을 바꿀 때만 덮어쓴다.
    content_security_policy: str = ""

    # 레이트리밋(분당, IP별) — 0이면 비활성. /auth/*는 무차별 대입 방지용으로 별도.
    rate_limit_per_minute: int = 240
    rate_limit_auth_per_minute: int = 30
    # 클라이언트 IP 를 읽을 헤더. 앞단 프록시가 "덮어쓰는" 헤더만 지정한다
    # (Caddy/nginx: x-real-ip, cloudflared/Cloudflare: cf-connecting-ip). 목록이면 마지막 값.
    # 비우면 접속 IP(request.client.host) — 프록시 없이 받는 헤더는 누구나 지어낼 수 있어 믿지 않는다.
    trusted_client_ip_header: str = ""
    # 재설정/공유 링크에 쓰는 외부 접근 URL
    public_base_url: str = "http://localhost:8000"
    reset_token_expire_minutes: int = 60
    # 채팅 어시스턴트 주입: fake | remote(추후)
    chat_mode: str = "fake"

    @model_validator(mode="after")
    def _refuse_unsafe_prod(self) -> "Settings":
        problems = production_config_problems(self)
        if problems:
            raise ValueError(
                "APP_ENV=prod 인데 안전하지 않은 설정이 있어 기동을 거부합니다:\n- "
                + "\n- ".join(problems)
            )
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def allowed_email_set(self) -> set[str]:
        return {e.strip().lower() for e in self.allowed_emails.split(",") if e.strip()}

    @property
    def allowed_chapter_ext_list(self) -> list[str]:
        return [e.strip().lower() for e in self.allowed_chapter_exts.split(",") if e.strip()]


def production_config_problems(s: Settings) -> list[str]:
    """prod 에서 막아야 할 설정 목록(사람이 읽는 문장). dev 면 항상 빈 목록."""
    if s.app_env != "prod":
        return []
    problems: list[str] = []
    if s.jwt_secret == JWT_SECRET_PLACEHOLDER:
        problems.append(
            "JWT_SECRET 이 저장소에 공개된 자리표시자입니다 — "
            'python -c "import secrets; print(secrets.token_urlsafe(48))" 로 만든 값을 .env 에 넣으세요.'
        )
    elif len(s.jwt_secret) < JWT_SECRET_MIN_LENGTH:
        problems.append(f"JWT_SECRET 이 {JWT_SECRET_MIN_LENGTH}자 미만입니다({len(s.jwt_secret)}자).")
    if s.firebase_mode.lower() == "fake":
        problems.append("FIREBASE_MODE=fake 는 토큰을 검증하지 않습니다 — google 로 바꾸세요.")
    if s.seed_on_startup:
        problems.append(
            "SEED_ON_STARTUP=true 는 비밀번호가 공개된 데모 계정(cho@example.com)을 만듭니다 — false 로 바꾸세요."
        )
    return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
