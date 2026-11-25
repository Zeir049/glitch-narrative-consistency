@echo off
REM ----------------------------------------------------------------------
REM  Glitch public test launcher (backend + Cloudflare quick tunnels)
REM  Korean guide: TUNNEL_GUIDE.md (same folder)
REM
REM  KEEP THIS FILE ASCII-ONLY. Korean text in .bat files gets misparsed
REM  by cmd depending on the console codepage (broke twice on 2026-07-29).
REM ----------------------------------------------------------------------
cd /d "%~dp0"

REM App DB is the native PostgreSQL on localhost:5432 (see .env). Docker runs only redis
REM (arq queue, 127.0.0.1:6379, loopback-only). Only the web app goes out via the tunnel.
echo [1/3] Starting redis container...
where docker >nul 2>&1
if errorlevel 1 set "PATH=%LOCALAPPDATA%\Programs\DockerDesktop\resources\bin;%PATH%"
docker info >nul 2>&1
if errorlevel 1 (
  echo.
  echo [ERROR] Docker Desktop is not running - db/redis cannot start.
  echo         Launch Docker Desktop, wait for it to finish starting,
  echo         then run this script again.
  echo.
  pause
  exit /b 1
)
docker compose up -d redis

echo [2/3] Starting backend on 127.0.0.1:8000 (loopback only; cloudflared proxies it) + analysis worker...
REM NB: PYTHONUTF8 is set here (inherited by the started windows). Do NOT use
REM "set PYTHONUTF8=1 && ..." inside the start command: cmd includes the
REM space before && in the value ("1 "), which is a fatal error for Python.
set PYTHONUTF8=1
REM cloudflared puts the real client IP in CF-Connecting-IP; the rate limiter trusts it only when told to.
set TRUSTED_CLIENT_IP_HEADER=cf-connecting-ip
REM Public URL = production rules. APP_ENV=prod (overrides .env) makes api/worker REFUSE to start
REM with a placeholder JWT_SECRET, FIREBASE_MODE=fake or SEED_ON_STARTUP=true.
set APP_ENV=prod
REM Preflight before any window opens, so a refused start never leaves a lone tunnel window.
REM Exit 1 = blocked (bad .env, or seed account still has its public password); 2 = warning.
REM Checklist (Korean): TUNNEL_GUIDE.md
.venv\Scripts\python.exe -m scripts.public_preflight
if errorlevel 2 goto preflight_warn
if errorlevel 1 goto preflight_block
goto preflight_ok
:preflight_block
echo.
echo [ERROR] Preflight failed - fix .env (see TUNNEL_GUIDE.md checklist). Nothing was started.
echo.
pause
exit /b 1
:preflight_warn
echo.
echo [WARN] See the warning above. Press Ctrl+C to abort, or any key to open the tunnel anyway.
pause
:preflight_ok
start "Glitch Backend" cmd /k ".venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
REM Analysis worker (arq): pulls jobs from Redis. Required when QUEUE_MODE=arq.
start "Glitch Worker" cmd /k ".venv\Scripts\python.exe -m arq app.workers.worker.WorkerSettings"

echo       Waiting 8s for backend startup...
timeout /t 8 >nul

echo [3/3] Starting Cloudflare tunnel (web, port 8000)...
REM No --logfile: with it cloudflared stops printing the trycloudflare URL box in the window.
REM Current URL can also be read with: curl http://127.0.0.1:20241/quicktunnel
start "Glitch Tunnel" cmd /k "cloudflared tunnel --url http://127.0.0.1:8000"

echo.
echo ----------------------------------------------------------------------
echo  Done. Three windows are now open ([Glitch Worker] runs the analysis jobs).
echo   1) [Glitch Tunnel] window: find https://xxxx.trycloudflare.com
echo      - that URL         = web UI
echo      - that URL + /docs = API docs (Swagger)
echo      - share the URL as is (Google sign-in; do not add ?demo=1)
echo      - NEVER open the tunnel with FIREBASE_MODE=fake in .env (no token check)
echo   2) To test share/password-reset links, put the web tunnel URL
echo      into PUBLIC_BASE_URL in .env, then restart [Glitch Backend].
echo.
echo  Full Korean guide: TUNNEL_GUIDE.md
echo ----------------------------------------------------------------------
pause
