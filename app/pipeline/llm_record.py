"""Weave LLM 호출 녹화·재생 래퍼 (WEAVE_LLM_RECORD=off|record|replay).

`weave_core/weave_llm.complete` 자리에 끼운다(`app/pipeline/weave.py` "LLM 호출 보강" 구역).
코어의 `complete_json` 은 모듈 전역 `complete` 를 부르므로, 그 전역을 이 래퍼로 바꾸면
추출(role="extract")과 판정(role="judge") 양쪽 호출이 모두 여기를 지난다.

  off     그대로 안쪽 함수를 부른다(기본값 — 제품 동작 무변경).
  record  안쪽 함수를 부르고, 응답이 status="ok" 면 디스크에 남긴다(오류·한도 초과 응답은 남기지 않는다).
  replay  디스크에서만 돌려준다. 안쪽 함수를 부르지 않으므로 네트워크 호출 0회.
          없는 키를 만나면 즉시 `ReplayMiss`(키·role·프롬프트 앞 200자 포함).

키 = role + sha256(system + "\\x00" + user). system 이 None 이면 빈 문자열로 본다.
구분자(\\x00)는 system/user 경계를 옮겨도 같은 해시가 나오지 않게 넣은 것이다.

같은 프롬프트가 여러 번 불릴 수 있다(판정 k=3, JSON 파싱 실패 재요청). record 는 그때마다 항목을 덧붙이고,
replay 는 그 키의 항목을 녹화 순서대로 돌려주며 끝까지 쓰면 처음부터 다시 쓴다(같은 입력을 두 번 재생해도
같은 결과 — R3). k회가 서로 같은 프롬프트라 순서가 섞여도 다수결 결과는 바뀌지 않는다.

저장 위치: <WEAVE_LLM_RECORD_DIR>/<role>/<sha 앞 2자>/<sha>.json
  {"key", "role", "prompt_sha256", "system", "user",
   "entries": [{"recorded_at", "provider", "model", "response": {...complete 반환 dict 전체}}]}
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

RECORD_MODES = ("off", "record", "replay")
PROMPT_HEAD_CHARS = 200


class ReplayMiss(RuntimeError):
    """replay 모드에서 녹화되지 않은 호출을 만났다."""

    def __init__(self, key: str, role: str, prompt_head: str, path: Path) -> None:
        self.key, self.role, self.prompt_head, self.path = key, role, prompt_head, path
        super().__init__(
            f"LLM 녹화 재생 miss: key={key} role={role} (찾은 파일: {path}) "
            f"프롬프트 앞 {PROMPT_HEAD_CHARS}자: {prompt_head!r}")


def prompt_sha256(system: str | None, user: str) -> str:
    return hashlib.sha256(((system or "") + "\x00" + (user or "")).encode("utf-8")).hexdigest()


def record_key(role: str, system: str | None, user: str) -> str:
    return f"{role}:{prompt_sha256(system, user)}"


def _settings_mode() -> str:
    from app.core.config import settings
    return settings.weave_llm_record


def _settings_dir() -> str:
    from app.core.config import settings
    return settings.weave_llm_record_dir


class LLMRecorder:
    """`complete(system, user, llm, *, role, base)` 와 같은 모양의 호출 가능 객체.

    mode·root 를 주지 않으면 호출할 때마다 settings(WEAVE_LLM_RECORD·WEAVE_LLM_RECORD_DIR)를 읽는다
    — 테스트·하네스가 실행 중에 바꿔도 따라간다.
    """

    def __init__(self, inner: Callable[..., dict], *, mode: str | None = None,
                 root: str | Path | None = None) -> None:
        if mode is not None and mode not in RECORD_MODES:
            raise ValueError(f"알 수 없는 녹화 모드: {mode} (off|record|replay)")
        self.inner = inner
        self._mode = mode
        self._root = Path(root) if root is not None else None
        self._lock = threading.Lock()
        self._cache: dict[Path, dict] = {}
        self._cursor: dict[str, int] = {}

    # ---------------------------------------------------------------- 설정
    @property
    def mode(self) -> str:
        m = self._mode if self._mode is not None else _settings_mode()
        if m not in RECORD_MODES:
            raise ValueError(f"알 수 없는 WEAVE_LLM_RECORD: {m} (off|record|replay)")
        return m

    @property
    def root(self) -> Path:
        return self._root if self._root is not None else Path(_settings_dir())

    def path_of(self, role: str, sha: str) -> Path:
        return self.root / role / sha[:2] / f"{sha}.json"

    def reset(self) -> None:
        """재생 커서와 읽기 캐시를 비운다(같은 프로세스에서 처음부터 다시 재생할 때)."""
        with self._lock:
            self._cache.clear()
            self._cursor.clear()

    # ---------------------------------------------------------------- 호출
    def __call__(self, system: str | None, user: str, llm: dict, *, role: str = "judge",
                 base: Any = None) -> dict:
        mode = self.mode
        if mode == "off":
            return self._call_inner(system, user, llm, role, base)
        sha = prompt_sha256(system, user)
        key = f"{role}:{sha}"
        path = self.path_of(role, sha)
        if mode == "replay":
            return self._replay(key, role, path, system, user)
        res = self._call_inner(system, user, llm, role, base)
        if isinstance(res, dict) and res.get("status") == "ok":
            self._record(key, role, sha, path, system, user, llm, res)
        return res

    def _call_inner(self, system: str | None, user: str, llm: dict, role: str, base: Any) -> dict:
        if base is None:
            return self.inner(system, user, llm, role=role)
        return self.inner(system, user, llm, role=role, base=base)

    def _replay(self, key: str, role: str, path: Path, system: str | None, user: str) -> dict:
        with self._lock:
            doc = self._cache.get(path)
            if doc is None and path.exists():
                doc = json.loads(path.read_text(encoding="utf-8"))
                self._cache[path] = doc
            entries = (doc or {}).get("entries") or []
            if not entries:
                raise ReplayMiss(key, role, _head(system, user), path)
            i = self._cursor.get(key, 0)
            self._cursor[key] = i + 1
            entry = entries[i % len(entries)]
        logger.debug("weave llm replay: %s (#%s/%s)", key, i + 1, len(entries))
        return copy.deepcopy(entry["response"])

    def _record(self, key: str, role: str, sha: str, path: Path, system: str | None, user: str,
                llm: dict, res: dict) -> None:
        llm = llm or {}
        entry = {
            "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "provider": llm.get("provider"),
            "model": llm.get(f"{role}_model") or llm.get("model"),
            "response": res,
        }
        with self._lock:
            if path.exists():
                doc = json.loads(path.read_text(encoding="utf-8"))
            else:
                doc = {"key": key, "role": role, "prompt_sha256": sha,
                       "system": system, "user": user, "entries": []}
            doc["entries"].append(entry)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
            tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, path)
            self._cache.pop(path, None)
        logger.info("weave llm record: %s (%s건째)", key, len(doc["entries"]))


def _head(system: str | None, user: str) -> str:
    text = (f"{system}\n{user}" if system else (user or ""))
    return text[:PROMPT_HEAD_CHARS]
