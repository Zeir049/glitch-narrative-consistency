# 분리 배포 — 몸통은 클라우드 VM, AI 서버는 개발 PC

몸통(api·worker·db·redis)은 GCP VM 에서 돌리고, AI 서버(LLM 호출·BGE-M3 임베딩)는 GPU 가 있는 개발 PC 에서 돌린다.
둘 사이는 **Tailscale(개발진 tailnet)** 으로만 잇는다.

```
브라우저 ──Tailscale serve──▶ VM: api(127.0.0.1:8000) · worker ──Tailscale──▶ PC: tailscale serve :8101 ──▶ AI 서버(127.0.0.1:8101)
```

## 지켜야 할 것

- **PC 는 공인 IP 직결이다.** AI 서버는 `127.0.0.1` 에만 띄우고, 밖으로는 `tailscale serve`(tailnet 안에서만 보임)로만 낸다.
  `--host 0.0.0.0`, 방화벽 포트 허용, `tailscale funnel`(인터넷 공개)은 쓰지 않는다.
- **공유 토큰은 필수.** VM `.env` 의 `AI_SERVER_TOKEN` 과 PC `ai_server/.env.deploy` 의 `AI_SERVER_TOKEN` 이 같아야 한다.
- **PC 가 꺼지면 새 분석만 멈춘다.** 조회·저장된 캐논은 그대로. 분석 버튼은 사전 점검에서 "AI 서버에 연결할 수 없습니다"로 막힌다.
  쓰는 시간에는 PC 절전·Windows 업데이트 재시작을 꺼 둔다.
- **작품별 AI 상태(`ai_server/Data/deploy_stories/<project_id>`)는 PC 에, DB 는 VM 에 있다.** 둘은 짝이라 백업은 같은 시점에 함께 받는다.

## PC 쪽 (한 번)

1. Tailscale 설치 → 개발진 tailnet 으로 로그인. PC 의 tailnet IP 확인: `tailscale ip -4` (예: `100.101.102.103`)
2. `ai_server\.env.deploy.example` 을 `ai_server\.env.deploy` 로 복사 → `AI_SERVER_TOKEN` 채우기.
   LLM 키·모델은 기존 `ai_server\.env` 값을 그대로 쓴다.
3. tailnet 에 8101 을 연다(설정이 남으므로 한 번이면 된다):
   ```bash
   tailscale serve --bg --http=8101 http://127.0.0.1:8101
   ```
   확인: `tailscale serve status`. 끄기: `tailscale serve --http=8101 off`

## PC 쪽 (쓸 때마다)

- `ai_server\run_deploy.bat` 실행 — 127.0.0.1:8101, `.env.deploy` 우선, 상태는 `Data\deploy_stories`.
  로컬 개발용 `run.bat`(8100, `Data\stories`)과 동시에 띄워도 섞이지 않는다.

## VM 쪽

1. VM 도 같은 tailnet 에 넣는다(`tailscale up`).
2. 루트 `.env` 에 추가:
   ```
   AI_SERVER_TOKEN=<PC 와 같은 값>
   AI_SERVER_URL=http://<PC tailnet IP>:8101
   ```
   MagicDNS 이름(`jin-pc` 등)은 컨테이너 안 DNS 에서 안 풀릴 수 있어 IP 를 쓴다.
3. ai 컨테이너 없이 기동:
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build api worker
   ```
4. 연결 확인(워커 컨테이너 안에서 — 실제로 AI 서버를 부르는 쪽):
   ```bash
   docker exec glitch-worker python -c "import asyncio; from app.pipeline.factory import get_pipeline; print(asyncio.run(get_pipeline().health()))"
   ```
   `ok=True ... AI 서버 연결됨` 이면 끝. `연결할 수 없습니다` 면 PC 의 run_deploy.bat·`tailscale serve status`,
   `인증에 실패` 면 두 토큰을 본다.

## 다시 한 VM 으로 합칠 때

`.env` 의 `AI_SERVER_URL` 을 지우고 `up -d --build api worker ai` 로 띄운다. PC 의 `Data\deploy_stories` 는
VM 의 `glitch_ai_data` 볼륨으로 옮겨야 기존 작품의 검수가 이어진다.
