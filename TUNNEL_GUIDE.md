# Glitch 공개 테스트 가이드 (start_tunnel.bat)

> ## ⚠️ 먼저 확인
> - **`.env`가 `FIREBASE_MODE=fake`인 상태로 공개 URL을 열지 마세요.** fake 모드는 서버가
>   `fake:<email>` 토큰을 검증 없이 믿기 때문에, 주소를 아는 누구나 **아무 계정으로나** 로그인해
>   그 계정의 프로젝트·원고를 읽고 지울 수 있습니다. 공개 테스트는 `FIREBASE_MODE=google`로 합니다.
> - DB·Redis·Adminer는 외부에 열지 않습니다. 터널로 나가는 것은 웹 앱(`http://127.0.0.1:8000`) 하나뿐입니다.

## 0. 공개 전 체크리스트 (`.env`)

`start_tunnel.bat`은 `APP_ENV=prod`로 서버·워커를 띄운다(`.env`의 값보다 우선). 창을 열기 전에
`python -m scripts.public_preflight`로 점검하고, **막히면 아무 창도 띄우지 않고 멈춘다.**

| `.env` 항목 | 공개 시 값 | 어기면 |
|---|---|---|
| `JWT_SECRET` | 32자 이상 실제 비밀값 (아래 명령으로 생성) | 기동 거부 |
| `FIREBASE_MODE` | `google` | 기동 거부 |
| `SEED_ON_STARTUP` | `false` | 기동 거부 |
| `ENABLE_SUBSCRIPTION` · `ENABLE_SHARING` · `ENABLE_CHAT` · `ENABLE_NOTIFICATIONS` | `false` | 경고 후 확인(아무 키 = 계속, Ctrl+C = 중단) |

```powershell
# JWT_SECRET 생성 — 출력값을 .env 의 JWT_SECRET= 에 넣는다(바꾸면 기존 로그인은 모두 풀린다)
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
```

**이미 만들어진 시드 계정.** `SEED_ON_STARTUP=false`로 바꿔도 예전에 시드를 켜고 띄운 DB에는
`cho@example.com`(비밀번호 `glitch123`, README에 공개)이 남아 있어 누구나 로그인할 수 있다.
점검 스크립트는 이 계정이 공개 비밀번호로 로그인되면 막는다. 둘 중 하나로 정리한다(대상은 `.env`의 `DATABASE_URL`):

```powershell
# 비밀번호만 무효화 — 계정·"금도끼 은도끼" 프로젝트는 남는다
.\.venv\Scripts\python.exe -m scripts.disable_seed_account
# 계정 삭제 — 그 계정의 프로젝트·회차·캐논까지 함께 지워진다
.\.venv\Scripts\python.exe -m scripts.disable_seed_account --delete
```

SQL로 직접 하려면: `UPDATE users SET password_hash = '!disabled-seed-account' WHERE email = 'cho@example.com';`
(삭제는 `DELETE FROM users WHERE email = 'cho@example.com';` — 프로젝트 등은 CASCADE로 지워진다.)
로컬 데모용 시드가 다시 필요하면 계정을 지운 뒤 `start_integrated.bat`(dev)에서 `SEED_ON_STARTUP=true`로 한 번 띄운다.

`start_tunnel.bat`을 더블클릭하거나 터미널에서 실행하면:

1. `docker compose up -d db redis` — DB·Redis 컨테이너(둘 다 127.0.0.1에만 열림)
2. 창 **[Glitch Backend]** — `uvicorn --host 127.0.0.1 --port 8000` (루프백 전용)
3. 창 **[Glitch Worker]** — 분석 작업 실행(arq, `QUEUE_MODE=arq`)
4. 창 **[Glitch Tunnel]** — `cloudflared tunnel --url http://127.0.0.1:8000`

> [Glitch Worker]가 없으면 분석이 "추출 중"에서 멈춘 채 진행되지 않으니 닫지 마세요.

> bat 파일 안내문이 영어인 이유: cmd가 한글이 든 bat을 콘솔 코드페이지에 따라
> 오파싱해서 깨진 명령을 실행하는 문제가 있어(2026-07-29 두 차례 발생),
> bat은 ASCII 전용으로 유지하고 한글 안내는 이 파일로 분리했습니다.

## 1. 웹 공유 (상대방에게 줄 주소)

[Glitch Tunnel] 창에서 `https://xxxx.trycloudflare.com` 주소를 찾아 공유하세요.

- 그 주소 자체 = 웹 화면(UI)
- 그 주소 + `/docs` = API 문서(Swagger)
- 로그인: 구글 로그인(`FIREBASE_MODE=google`, 프론트 기본값). 주소를 그대로 공유하면 됩니다
  (`?demo=1`을 붙여 공유하지 마세요 — 서버가 fake 모드가 아니면 401). Firebase 콘솔의 승인 도메인에
  `*.trycloudflare.com` 주소가 없으면 팝업이 `auth/unauthorized-domain`으로 막힙니다 —
  콘솔 > Authentication > Settings > 승인된 도메인에 그 주소를 추가하세요.

상대방이 로그인 → 프로젝트 생성 → 새로고침 후에도 데이터가 남아 있으면 웹과 DB가 정상 연결된 것입니다.

## 2. 공유/비밀번호 재설정 링크까지 테스트하려면

quick tunnel 주소는 실행할 때마다 바뀝니다. 새 웹 터널 주소를 `.env`의
`PUBLIC_BASE_URL`에 넣고 [Glitch Backend] 창을 Ctrl+C 후 재실행하세요.

## 3. 테스트가 끝나면

- [Glitch Tunnel] 창부터 닫아 공개 주소를 끊고, [Glitch Backend]·[Glitch Worker] 창을 닫습니다(각각 Ctrl+C 또는 창 닫기).
- 컨테이너까지 내리려면 `docker compose stop`.
- 로컬 개발만 할 때는 `start_integrated.bat`을 씁니다(터널 없이 http://127.0.0.1:8000).
- DB 내용을 직접 보고 싶으면 로컬에서만: `docker compose --profile tools up -d adminer` → http://127.0.0.1:8081
  (서버 `db`, 사용자/비밀번호 `glitch`, DB `glitch_integrated`). Adminer를 터널로 열지 마세요.
