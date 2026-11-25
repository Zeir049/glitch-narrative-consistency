# -*- coding: utf-8 -*-
"""
weave_llm.py — LLM 백엔드 추상화 (제품 계층)

provider 하나만 바꾸면 추출·판정의 호출 대상이 바뀐다.
**프롬프트는 이 모듈이 만들지 않는다** — 호출자가 만든 것을 그대로 보낸다.
"바뀌는 것은 누가 판정하나뿐"을 코드 수준에서 보장하기 위한 경계다.

provider
--------
  nim        NVIDIA NIM (OpenAI 호환 /chat/completions). glm.py의 배관을 재사용
  anthropic  Anthropic Messages API
  manual     호출하지 않고 프롬프트를 파일로 떨어뜨린다

키
--
  NIM        .nvidia_key 파일  →  NVIDIA_API_KEY 환경변수  →  (호환) .glm_key, ~/.nvidia_api_key
  Anthropic  ANTHROPIC_API_KEY 환경변수

  **파일이 환경변수보다 우선한다.**
  키 문자열은 반환값·로그·예외 메시지 어디에도 넣지 않는다.

glm.py와 다른 점
----------------
glm.py는 CLI라서 실패 시 `sys.exit()`하고 응답을 stdout에 스트리밍한다.
라이브러리에서는 둘 다 곤란하므로:
  · 실패는 예외/결과 dict로 돌려주고 프로세스를 죽이지 않는다
  · 기본 non-stream, 출력 없음
  · 재시도가 한 번이라도 있었으면 결과에 retried=True를 남긴다
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import time
import urllib.error
import urllib.request
from concurrent import futures as _futures
from datetime import datetime, timezone

BASE = pathlib.Path(__file__).resolve().parent

# --- NIM 기본값 (glm.py와 동일한 출처) ---------------------------------
NIM_BASE_URL = os.environ.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
NIM_MODEL = os.environ.get("GLM_MODEL", "z-ai/glm-5.2")

# --- OpenRouter (OpenAI 호환 — NIM 배관을 base_url/키만 바꿔 재사용) ----
# 무료(:free) 엔드포인트 특성 (2026-08-28 실측·조사):
#   · 레이트 리밋 헤더를 내려주지 않는다 → 429/503 수신 후 대응뿐
#   · 무충전 계정 50요청/일, 20요청/분 — 러너가 일 단위 분할을 계획할 것
#   · 업스트림 혼잡 시 429가 정상적으로 발생한다 (RETRY_STATUS 백오프 대상)
OPENROUTER_BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "z-ai/glm-5.2:free")

# 무료 티어 분당 요청 제한 대비 — 호출 간 최소 간격
MIN_INTERVAL_SEC = float(os.environ.get("GLM_MIN_INTERVAL", "2"))

# glm_v1_scoring.md §3-4에서 고정한 샘플링 파라미터
DEFAULT_TEMPERATURE = 0.2
DEFAULT_TOP_P = 0.95
DEFAULT_EFFORT = "high"
DEFAULT_MAX_TOKENS = 4096
HTTP_TIMEOUT = float(os.environ.get("WEAVE_HTTP_TIMEOUT", "900"))
# WEAVE_LLM_VERBOSE=1 이면 호출 시작/끝을 stderr에 남긴다 (키는 출력하지 않는다)
VERBOSE = os.environ.get("WEAVE_LLM_VERBOSE", "") == "1"

# 벽시계 기준 하드 상한. urllib의 timeout은 **소켓 연산 단위**라서 서버가
# 데이터를 조금씩 흘려보내면 발동하지 않는다 (실측: 900s 설정에 20분 무응답).
# 총 소요가 이 값을 넘으면 그 시도를 포기한다.
HARD_DEADLINE = float(os.environ.get("WEAVE_HARD_DEADLINE", "420"))

MAX_RETRIES = 3                      # 지수 백오프 (5s → 10s → 20s)
RETRY_STATUS = {408, 429, 500, 502, 503, 504}

_last_call_at = 0.0


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _vlog(msg: str) -> None:
    """진행 로그. **키 문자열은 절대 넘기지 않는다.**"""
    import sys
    print(f"[weave_llm {now()}] {msg}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------- 키

class MissingKey(RuntimeError):
    """키를 찾지 못함. 메시지에 키 값을 넣지 않는다."""


def load_nim_key(base: pathlib.Path = BASE) -> str:
    """.nvidia_key 파일을 최우선으로 읽는다. 없으면 환경변수, 그다음 호환 경로."""
    for cand in (base / ".nvidia_key", pathlib.Path(".nvidia_key")):
        try:
            if cand.is_file():
                k = cand.read_text(encoding="utf-8").strip()
                if k:
                    return k
        except OSError:
            pass
    k = os.environ.get("NVIDIA_API_KEY", "").strip()
    if k:
        return k
    for cand in (base / ".glm_key", pathlib.Path(".glm_key"),
                 pathlib.Path.home() / ".nvidia_api_key"):
        try:
            if cand.is_file():
                k = cand.read_text(encoding="utf-8").strip()
                if k:
                    return k
        except OSError:
            continue
    raise MissingKey(
        "NVIDIA API 키를 찾지 못했습니다. 아래 중 하나로 설정하세요.\n"
        "  1) 프로젝트 루트에 .nvidia_key 파일 — 키 한 줄만 (.gitignore에 등록됨)\n"
        "  2) 환경변수 NVIDIA_API_KEY\n"
        "  키 발급: https://build.nvidia.com/settings/api-keys")


def key_source(base: pathlib.Path = BASE) -> str:
    """어디서 키를 읽었는지만 알려준다 (키 값은 절대 반환하지 않는다)."""
    for cand in (base / ".nvidia_key", pathlib.Path(".nvidia_key")):
        if cand.is_file() and cand.read_text(encoding="utf-8").strip():
            return ".nvidia_key 파일"
    if os.environ.get("NVIDIA_API_KEY", "").strip():
        return "NVIDIA_API_KEY 환경변수"
    for cand in (base / ".glm_key", pathlib.Path(".glm_key"),
                 pathlib.Path.home() / ".nvidia_api_key"):
        try:
            if cand.is_file() and cand.read_text(encoding="utf-8").strip():
                return f"{cand.name} (호환 경로)"
        except OSError:
            continue
    return "없음"


def available(provider: str, base: pathlib.Path = BASE) -> tuple[bool, str]:
    if provider == "manual":
        return True, "manual"
    if provider == "nim":
        try:
            load_nim_key(base)
        except MissingKey as e:
            return False, str(e).splitlines()[0]
        return True, f"NIM {NIM_MODEL} (키 출처: {key_source(base)})"
    if provider == "anthropic":
        if not load_anthropic_key(base):
            return False, ".anthropic_key 파일/ANTHROPIC_API_KEY 환경변수 없음"
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False, "anthropic 라이브러리 미설치"
        return True, "Anthropic API (키 출처: 파일 또는 환경변수)"
    if provider == "openrouter":
        if not load_openrouter_key(base):
            return False, ".openrouter_key 파일/OPENROUTER_API_KEY 환경변수 없음"
        return True, f"OpenRouter {OPENROUTER_MODEL}"
    return False, f"알 수 없는 provider: {provider}"


# ---------------------------------------------------------------- NIM 호출

def _nim_request(payload: dict, api_key: str, base_url: str | None = None) -> str:
    stream = bool(payload.get("stream"))
    req = urllib.request.Request(
        f"{base_url or NIM_BASE_URL}/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json",
                 "Accept": "text/event-stream" if stream else "application/json"},
        method="POST")
    if not stream:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            body = json.loads(resp.read().decode())
        if body.get("error"):
            # OpenRouter는 HTTP 200 본문에 error를 실어 보내는 경우가 있다
            # (업스트림 429/503 등). RETRY_STATUS 백오프를 타도록 HTTPError로 변환.
            err = body["error"]
            code = err.get("code") if isinstance(err.get("code"), int) else 500
            import io as _io
            raise urllib.error.HTTPError(
                req.full_url, code, str(err.get("message"))[:200], None,
                _io.BytesIO(json.dumps(err).encode()))
        choice = (body.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        text = msg.get("content") or ""
        if not text:
            # 일부 모델은 본문이 비고 reasoning(_content)만 오는 경우가 있다.
            text = msg.get("reasoning_content") or msg.get("reasoning") or ""
        return text, choice.get("finish_reason"), body.get("usage") or {}

    # --- 스트리밍 (SSE) --------------------------------------------------
    # 작업 2 게이트 §4-3: 비스트리밍 장시간 생성은 무응답으로 끊겼다.
    # 스트리밍이면 urllib의 timeout(소켓 연산 단위)이 그대로 **유휴 타임아웃**이
    # 된다 — 청크가 timeout초 동안 안 오면 예외가 난다. 총량 상한은 바깥의
    # 하드 데드라인이 계속 맡는다.
    chunks, reasoning, finish, usage = [], [], None, {}
    last_progress = time.time()
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                ev = json.loads(data)
            except json.JSONDecodeError:
                continue
            if ev.get("usage"):
                usage = ev["usage"]
            ch = (ev.get("choices") or [{}])[0]
            delta = ch.get("delta") or {}
            if ch.get("finish_reason"):
                finish = ch["finish_reason"]
            if delta.get("content"):
                chunks.append(delta["content"])
            elif delta.get("reasoning_content"):
                reasoning.append(delta["reasoning_content"])
            if VERBOSE and time.time() - last_progress >= 30:
                last_progress = time.time()
                _vlog(f"스트리밍 수신 중 content={sum(map(len, chunks))}자 "
                      f"reasoning={sum(map(len, reasoning))}자")
    text = "".join(chunks) or "".join(reasoning)
    return text, finish, usage


def call_nim(system: str | None, user: str, *, model: str | None = None,
             temperature: float = DEFAULT_TEMPERATURE,
             top_p: float = DEFAULT_TOP_P,
             effort: str | None = DEFAULT_EFFORT,
             max_tokens: int = DEFAULT_MAX_TOKENS,
             stream: bool = False,
             base: pathlib.Path = BASE,
             base_url: str | None = None,
             api_key: str | None = None,
             extra: dict | None = None) -> dict:
    """OpenAI 호환 1회 호출 (내부 재시도 포함). 기본은 NIM.

    base_url/api_key를 넘기면 다른 OpenAI 호환 엔드포인트(OpenRouter 등)로
    간다 — call_openrouter가 이 경로를 쓴다.

    반환: {"status": "ok"|"error", "text", "retried", "attempts",
           "finish_reason", "usage", "elapsed_sec", "message"}
    """
    global _last_call_at
    api_key = api_key or load_nim_key(base)

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})

    payload = {"model": model or NIM_MODEL, "messages": messages,
               "temperature": temperature, "top_p": top_p,
               "max_tokens": max_tokens, "stream": bool(stream)}
    if effort:
        payload["reasoning_effort"] = effort
    if extra:
        payload.update(extra)

    started = time.time()
    attempts, retried, last_err = 0, False, ""
    for attempt in range(MAX_RETRIES + 1):
        gap = MIN_INTERVAL_SEC - (time.time() - _last_call_at)
        if gap > 0:
            time.sleep(gap)
        attempts += 1
        if VERBOSE:
            _vlog(f"호출 {attempts} model={payload['model']} "
                  f"max_tokens={payload['max_tokens']} timeout={HTTP_TIMEOUT:.0f}s")
        try:
            # ★ with 문을 쓰지 않는다 — ThreadPoolExecutor.__exit__는
            #   shutdown(wait=True)라서 데드라인이 걸려도 스레드가 끝날 때까지
            #   다시 블록한다 (실측: 300s 데드라인이 발동하지 않았다).
            ex = _futures.ThreadPoolExecutor(max_workers=1)
            try:
                fut = ex.submit(_nim_request, payload, api_key, base_url)
                try:
                    text, finish, usage = fut.result(timeout=HARD_DEADLINE)
                except _futures.TimeoutError:
                    raise TimeoutError(
                        f"하드 데드라인 {HARD_DEADLINE:.0f}s 초과 (응답 없음)")
            finally:
                ex.shutdown(wait=False, cancel_futures=True)   # 스레드는 버린다
            if VERBOSE:
                _vlog(f"응답 {len(text)}자 finish={finish} "
                      f"({round(time.time()-started,1)}s)")
            _last_call_at = time.time()
            return {"status": "ok", "text": text, "retried": retried,
                    "attempts": attempts, "finish_reason": finish,
                    "usage": {"input": usage.get("prompt_tokens"),
                              "output": usage.get("completion_tokens")},
                    "elapsed_sec": round(time.time() - started, 2)}
        except urllib.error.HTTPError as e:
            _last_call_at = time.time()
            detail = e.read().decode("utf-8", "replace")[:300]
            # reasoning_effort를 거부하는 엔드포인트면 빼고 재시도 (glm.py와 동일)
            if e.code == 400 and "reasoning_effort" in payload \
                    and "reason" in detail.lower():
                payload.pop("reasoning_effort", None)
                retried = True
                continue
            last_err = f"HTTP {e.code}: {detail}"
            if VERBOSE:
                _vlog(last_err[:140])
            if e.code in RETRY_STATUS and attempt < MAX_RETRIES:
                retried = True
                time.sleep(5 * (2 ** attempt))
                continue
            break
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            _last_call_at = time.time()
            last_err = f"연결 실패: {e}"
            if VERBOSE:
                _vlog(f"{last_err[:100]} (시도 {attempts})")
            if attempt < MAX_RETRIES:
                retried = True
                time.sleep(5 * (2 ** attempt))
                continue
            break
        except json.JSONDecodeError as e:
            _last_call_at = time.time()
            last_err = f"응답 본문 파싱 실패: {e}"
            if attempt < MAX_RETRIES:
                retried = True
                time.sleep(5 * (2 ** attempt))
                continue
            break

    return {"status": "error", "text": "", "retried": retried,
            "attempts": attempts, "message": last_err,
            "elapsed_sec": round(time.time() - started, 2)}


# ---------------------------------------------------------------- OpenRouter

def load_openrouter_key(base: pathlib.Path = BASE) -> str | None:
    """.openrouter_key 파일 → OPENROUTER_API_KEY 환경변수. 값은 로그에 남기지 않는다."""
    for cand in (base / ".openrouter_key", pathlib.Path(".openrouter_key")):
        try:
            if cand.is_file():
                k = cand.read_text(encoding="utf-8").strip()
                if k:
                    return k
        except OSError:
            pass
    return os.environ.get("OPENROUTER_API_KEY") or None


def call_openrouter(system: str | None, user: str, *, model: str | None = None,
                    temperature: float = DEFAULT_TEMPERATURE,
                    top_p: float = DEFAULT_TOP_P,
                    effort: str | None = None,
                    max_tokens: int = DEFAULT_MAX_TOKENS,
                    stream: bool = False,
                    base: pathlib.Path = BASE,
                    extra: dict | None = None) -> dict:
    """OpenRouter 1회 호출 — call_nim의 배관(재시도·백오프·하드 데드라인)을
    base_url/키만 바꿔 그대로 탄다. effort 기본 None (reasoning_effort 미전송).
    extra: OpenRouter 전용 파라미터 (예: {"reasoning": {"enabled": False}})."""
    key = load_openrouter_key(base)
    if not key:
        return {"status": "error", "text": "", "retried": False, "attempts": 0,
                "message": ".openrouter_key 파일/OPENROUTER_API_KEY 환경변수 없음"}
    return call_nim(system, user, model=model or OPENROUTER_MODEL,
                    temperature=temperature, top_p=top_p, effort=effort,
                    max_tokens=max_tokens, stream=stream, base=base,
                    base_url=OPENROUTER_BASE_URL, api_key=key, extra=extra)


# ---------------------------------------------------------------- Anthropic

def load_anthropic_key(base: pathlib.Path = BASE) -> str | None:
    """.anthropic_key 파일 → ANTHROPIC_API_KEY 환경변수. 값은 로그에 남기지 않는다."""
    for cand in (base / ".anthropic_key", pathlib.Path(".anthropic_key")):
        try:
            if cand.is_file():
                k = cand.read_text(encoding="utf-8").strip()
                if k:
                    return k
        except OSError:
            pass
    return os.environ.get("ANTHROPIC_API_KEY") or None


def call_anthropic(system: str | None, user: str, *, model: str | None = None,
                   temperature: float | None = None,
                   max_tokens: int = DEFAULT_MAX_TOKENS,
                   thinking: str | None = None,
                   base: pathlib.Path = BASE) -> dict:
    started = time.time()
    try:
        import anthropic
    except ImportError:
        return {"status": "error", "text": "", "retried": False, "attempts": 0,
                "message": "anthropic 라이브러리 미설치"}
    key = load_anthropic_key(base)
    if not key:
        return {"status": "error", "text": "", "retried": False, "attempts": 0,
                "message": ".anthropic_key 파일/ANTHROPIC_API_KEY 환경변수 없음"}
    client = anthropic.Anthropic(api_key=key)
    kw = {"model": model or "claude-sonnet-4-5", "max_tokens": max_tokens,
          "messages": [{"role": "user", "content": user}]}
    if temperature is not None:
        kw["temperature"] = temperature
    if thinking == "disabled":
        kw["thinking"] = {"type": "disabled"}
    if system:
        kw["system"] = system
    def _run():
        # SDK는 장시간 요청(큰 max_tokens)에 스트리밍을 요구한다.
        # 항상 스트리밍으로 받고 최종 메시지를 조립한다.
        with client.messages.stream(**kw) as s:
            for _ in s.text_stream:
                pass                            # 소비만 한다 (출력 없음)
            return s.get_final_message()

    retried = False
    try:
        try:
            resp = _run()
        except Exception as e1:                 # noqa: BLE001
            # 일부 모델(sonnet-5 등)은 temperature 파라미터 자체를 거부한다.
            # NIM의 reasoning_effort 폴백과 같은 방식으로 빼고 1회 재시도.
            if "temperature" in kw and "temperature" in str(e1):
                kw.pop("temperature")
                retried = True
                resp = _run()
            else:
                raise
    except Exception as e:                      # noqa: BLE001 — 어떤 예외든 살려서 돌려준다
        return {"status": "error", "text": "", "retried": retried, "attempts": 1,
                "message": f"{type(e).__name__}: {e}",
                "elapsed_sec": round(time.time() - started, 2)}
    # Sonnet 5는 thinking 블록을 함께 반환할 수 있다 — text 블록만 취한다
    text = "".join(b.text for b in resp.content
                   if getattr(b, "type", None) == "text")
    return {"status": "ok", "text": text, "retried": retried,
            "attempts": 1, "finish_reason": getattr(resp, "stop_reason", None),
            "usage": {"input": resp.usage.input_tokens,
                      "output": resp.usage.output_tokens},
            "elapsed_sec": round(time.time() - started, 2)}


# ---------------------------------------------------------------- 통합 진입점

def complete(system: str | None, user: str, llm: dict,
             *, role: str = "judge", base: pathlib.Path = BASE) -> dict:
    """provider 설정에 따라 호출한다.

    llm = {"provider": "nim|anthropic|manual", "model": ...,
           "judge_model": ..., "extract_model": ...,
           "temperature": ..., "max_tokens": ...}
    role = "judge" | "extract"  → <role>_model이 있으면 그것을 쓴다
    """
    llm = llm or {}
    provider = llm.get("provider", "manual")
    model = llm.get(f"{role}_model") or llm.get("model")
    max_tokens = int(llm.get("max_tokens") or DEFAULT_MAX_TOKENS)

    if provider == "manual":
        return {"status": "manual", "text": "", "retried": False, "attempts": 0,
                "message": "manual 모드 — 호출하지 않았습니다."}
    if provider == "nim":
        return call_nim(system, user, model=model,
                        temperature=float(llm.get("temperature", DEFAULT_TEMPERATURE)),
                        top_p=float(llm.get("top_p", DEFAULT_TOP_P)),
                        effort=llm.get("effort", DEFAULT_EFFORT),
                        max_tokens=max_tokens,
                        stream=bool(llm.get("stream", False)), base=base)
    if provider == "anthropic":
        t = llm.get("temperature")
        return call_anthropic(system, user, model=model,
                              temperature=(float(t) if t is not None else None),
                              max_tokens=max_tokens,
                              thinking=llm.get("thinking"), base=base)
    if provider == "openrouter":
        return call_openrouter(system, user, model=model,
                               temperature=float(llm.get("temperature", DEFAULT_TEMPERATURE)),
                               top_p=float(llm.get("top_p", DEFAULT_TOP_P)),
                               effort=llm.get("effort"),  # 기본 None
                               max_tokens=max_tokens,
                               stream=bool(llm.get("stream", False)), base=base,
                               extra=llm.get("extra"))
    return {"status": "error", "text": "", "retried": False, "attempts": 0,
            "message": f"알 수 없는 provider: {provider}"}


# ---------------------------------------------------------------- JSON 파싱

def extract_json(text: str) -> dict:
    """응답에서 JSON 객체 하나를 꺼낸다. 코드펜스·앞뒤 설명을 허용한다."""
    t = (text or "").strip()
    if not t:
        raise ValueError("빈 응답")
    fence = re.search(r"```(?:json)?\s*\n(.*?)\n```", t, re.S)
    if fence:
        t = fence.group(1).strip()
    start = t.find("{")
    if start < 0:
        raise ValueError("JSON 객체를 찾지 못했습니다")
    # 중괄호 균형을 맞춰 첫 완결 객체를 잘라낸다 (뒤에 설명이 붙어도 안전)
    depth, in_str, esc = 0, False, False
    for i, ch in enumerate(t[start:], start):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(t[start:i + 1])
    raise ValueError("JSON 객체가 닫히지 않았습니다 (응답 절단 가능성)")


def complete_json(system: str | None, user: str, llm: dict, *,
                  role: str = "judge", base: pathlib.Path = BASE,
                  reask: bool = True) -> dict:
    """JSON 응답을 기대하는 호출. 파싱 실패 시 1회 재요청한다.

    반환: {"status": "ok"|"failed"|"manual"|"error", "data", "raw",
           "retried", "attempts", "parse_retried", "message", ...}
    """
    res = complete(system, user, llm, role=role, base=base)
    if res["status"] != "ok":
        return res | {"data": None, "raw": res.get("text", "")}

    try:
        return res | {"status": "ok", "data": extract_json(res["text"]),
                      "raw": res["text"], "parse_retried": False}
    except Exception as e1:                     # noqa: BLE001
        if not reask:
            return res | {"status": "failed", "data": None, "raw": res["text"],
                          "parse_retried": False, "message": f"JSON 파싱 실패: {e1}"}
        # 같은 프롬프트로 1회 재요청 (glm_v1_scoring.md §3-5)
        res2 = complete(system, user, llm, role=role, base=base)
        if res2["status"] != "ok":
            return res2 | {"status": "failed", "data": None,
                           "raw": res.get("text", ""), "parse_retried": True,
                           "retried": True,
                           "message": f"재요청 실패: {res2.get('message')}"}
        try:
            return res2 | {"status": "ok", "data": extract_json(res2["text"]),
                           "raw": res2["text"], "parse_retried": True,
                           "retried": True}
        except Exception as e2:                 # noqa: BLE001
            return res2 | {"status": "failed", "data": None, "raw": res2["text"],
                           "parse_retried": True, "retried": True,
                           "message": f"JSON 파싱 실패(재요청 후): {e2}"}


# ---------------------------------------------------------------- 설정 병합

DEFAULT_LLM = {
    "provider": "manual",
    "model": NIM_MODEL,
    "extract_model": None,
    "judge_model": None,
    "temperature": DEFAULT_TEMPERATURE,
    "top_p": DEFAULT_TOP_P,
    "effort": DEFAULT_EFFORT,
    "max_tokens": DEFAULT_MAX_TOKENS,
}


def llm_of(cfg: dict) -> dict:
    """project.json의 llm 절을 기본값과 합친다.

    llm 절이 없으면 기존 extraction.mode를 존중해 하위 호환을 유지한다:
      extraction.mode == "auto"  → 키가 있으면 anthropic, 없으면 manual
    """
    llm = dict(DEFAULT_LLM)
    given = (cfg or {}).get("llm")
    if isinstance(given, dict):
        llm.update({k: v for k, v in given.items() if v is not None})
        return llm
    mode = ((cfg or {}).get("extraction") or {}).get("mode", "auto")
    if mode == "manual":
        llm["provider"] = "manual"
    elif mode == "api" or (mode == "auto" and os.environ.get("ANTHROPIC_API_KEY")):
        llm["provider"] = "anthropic"
        llm["model"] = "claude-sonnet-4-5"
    return llm


def describe(llm: dict, base: pathlib.Path = BASE) -> str:
    """설정 요약 한 줄. **키를 포함하지 않는다.**"""
    p = llm.get("provider", "manual")
    ok, why = available(p, base)
    model = llm.get("model") or "-"
    return (f"provider={p} model={model} "
            f"temp={llm.get('temperature')} effort={llm.get('effort')} "
            f"max_tokens={llm.get('max_tokens')} | {'사용 가능' if ok else why}")


if __name__ == "__main__":
    import sys
    prov = sys.argv[1] if len(sys.argv) > 1 else "nim"
    ok, why = available(prov)
    print(f"provider={prov}  가용={ok}  {why}")
    if ok and prov == "nim":
        r = complete_json(
            "You reply with JSON only.",
            'Reply exactly: {"ok": true, "note": "weave_llm 연결 확인"}',
            {"provider": "nim", "max_tokens": 256})
        print("status:", r["status"], "| retried:", r.get("retried"),
              "| attempts:", r.get("attempts"), "| elapsed:", r.get("elapsed_sec"), "s")
        print("data:", r.get("data"))
        if r["status"] != "ok":
            print("message:", r.get("message"))
            print("raw:", (r.get("raw") or "")[:300])
