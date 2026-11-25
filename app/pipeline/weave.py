"""구현 W — Weave 엔진을 몸통 프로세스 안에서 실행하는 어댑터 (PIPELINE_MODE=weave).

Weave(서사 모순 검출: 이벤트 소싱 원장 + 규칙 검사기 + LLM 판정)의 코어 모듈은
app/pipeline/weave_core/ 에 **원본 그대로** 들어 있다(출처: VENDORED.md). 이 파일은
그 코어를 호출만 하고, 입출력을 몸통 계약(interface.py)에 맞춰 옮긴다.

  extract_chapter  원문 → (분할) LLM 추출 → 엔티티 레지스트리 병합 → 화별 사건 로그 저장
                   → 사건·인물·장소·시간·세계관 규칙을 canon_delta 로 변환
                   → 인물 관계는 Weave 가 뽑지 않으므로 '같은 사건에 함께 등장'으로 잇는다(LLM 없음)
  review_chapter   저장(확정)된 앞 회차들의 사건 로그로 원장 재생
                   → 이번 회차 사건별 규칙 검사(checker) + 장면 단위 LLM 판정 k회
                   → 검출 항목을 ContradictionOut 으로 변환

작품별 내부 상태는 settings.weave_data_dir/<project_id>/ 에 파일로 쌓인다.
  registry.json                  엔티티 레지스트리 (NEW:라벨 → CHAR_/LOC_/... 정식 ID)
  label_map.json                 추출기가 낸 NEW:라벨 → 정식 ID (별칭으로 매칭된 라벨 포함)
  episodes/episode_NN.json       회차별 최신 추출 사건 로그
  episodes/rev/episode_NN_<rev>  추출할 때마다 남는 판본 — 저장된 판본만 원장에 들어간다
  raw/epNN/cMM/                  LLM 원 응답 (추출 조각별)
  reviews/review_NN.json         검수 결과 원본(검사기 항목 + 판정 k회 전부)
  gates/gate_NN_<rev>.json       추출 관문 등급(판본별 — 사건·상태 변화의 확정/잠정과 그 이유)

'확정' 기준: Weave 는 사람이 확정한 회차만 원장에 넣는다. 몸통에서는 저장(saved)된 회차만
캐논에 기여하므로, 검수 시 CanonSnapshot 의 사건(entity_key = 사건 ID, attrs.weave_rev = 판본)을
확정분으로 본다. 1차 검토에서 지운 사건, 저장하지 않은 재추출본은 원장에 들어가지 않는다.

코어 호출은 동기(urllib) 코드라 asyncio.to_thread 로 돌리고, 작업이 취소되면(arq job_timeout)
스레드에 취소 신호를 보내 남은 LLM 호출과 파일 쓰기를 멈춘다(진행 중인 HTTP 1건은 끝까지 기다린 뒤 버린다).
"""
from __future__ import annotations

import asyncio
import ctypes
import hashlib
import http.client
import json
import logging
import os
import re
import socket
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from itertools import combinations
from pathlib import Path

from app.core.config import settings
from app.pipeline import extract_gate as w_gate
from app.pipeline.extract_verifier import merge as w_vmerge
from app.pipeline.extract_verifier import report as w_verifier
from app.pipeline.interface import (
    CanonDelta,
    CanonSnapshot,
    CharacterDelta,
    ConflictRef,
    ContradictionOut,
    EventDelta,
    ExtractionOutput,
    PipelineHealth,
    RelationDelta,
    ReviewOutput,
    SpaceDelta,
    Span,
    TimelineDelta,
    WorldviewDelta,
)

logger = logging.getLogger(__name__)

CORE_DIR = Path(__file__).resolve().parent / "weave_core"
EXTRACT_PROMPT = "추출_프롬프트_v2_3.md"

# 코어 모듈은 평면 import(`from ledger import ...`)를 쓴다 — 원본 무수정을 위해 경로만 연다.
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

import checker as w_checker  # noqa: E402
import weave_extract as w_extract  # noqa: E402
import weave_llm as w_llm  # noqa: E402
import weave_pipeline as w_pipe  # noqa: E402
import weave_registry as w_reg  # noqa: E402
from ledger import normalize as w_normalize  # noqa: E402
from app.pipeline import prejudge as w_prejudge  # noqa: E402

w_prejudge.install_apply_event_f2()  # auto_location_extra 가 없는 사건에는 원래 apply_event 와 같다


def _norm(value, mapping: dict):  # noqa: ANN001, ANN202
    """F4(경계 인식 정규화)가 켜지면 그것을, 아니면 코어 normalize 를 쓴다."""
    if settings.weave_prejudge_f4:
        return w_prejudge.normalize_bounded(value, mapping)
    return w_normalize(value, mapping)

PROVIDER_KEY_ENV = {
    "openrouter": "OPENROUTER_API_KEY",
    "nim": "NVIDIA_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}

# 판정 verdict → 몸통 모순 type (setting|character|timeline|space|event|foreshadow)
VERDICT_TYPE = {"모순": "event", "설정누수": "setting", "지식범위위반": "character"}
SEVERITY_CONFIDENCE = {w_checker.SEV_HARD: 0.9, w_checker.SEV_SOFT: 0.6}


class WeaveError(RuntimeError):
    """사용자에게 그대로 보여줄 수 있는 실패 (키 값 등 비밀은 넣지 않는다)."""


class WeaveCancelled(WeaveError):
    """작업이 취소됐다 — 남은 호출·쓰기를 하지 않고 빠져나온다."""


# ---------------------------------------------------------------- LLM 호출 보강

_core_complete = w_llm.complete


def _complete_resilient(system, user, llm, *, role="judge", base=CORE_DIR):  # noqa: ANN001
    """코어 complete 를 감싼다: 응답 본문이 중간에 끊기면(http.client.IncompleteRead 등)
    코어는 재시도 없이 예외를 던진다 — 연결 실패와 같게 백오프 재시도 후 error 결과로 돌려준다."""
    last = ""
    tries = w_llm.MAX_RETRIES + 1
    for attempt in range(tries):
        try:
            return _core_complete(system, user, llm, role=role, base=base)
        except http.client.HTTPException as e:
            last = f"연결 실패: 응답이 중간에 끊겼습니다 ({type(e).__name__})"
            logger.warning("weave llm: %s (시도 %s/%s)", last, attempt + 1, tries)
            if attempt < tries - 1:
                w_llm.time.sleep(5 * (2 ** attempt))
    return {"status": "error", "text": "", "retried": True, "attempts": tries, "message": last}


from app.pipeline.llm_record import LLMRecorder  # noqa: E402

# complete_json 이 모듈 전역 complete 를 부른다 → 추출·판정 호출이 모두 녹화·재생 래퍼를 지난다.
# WEAVE_LLM_RECORD=off(기본)면 그대로 _complete_resilient, replay 면 안쪽을 부르지 않는다(네트워크 0회).
llm_recorder = LLMRecorder(_complete_resilient)
w_llm.complete = llm_recorder


# ---------------------------------------------------------------- 설정

def _api_key_ready() -> None:
    """settings.weave_api_key 가 있으면 provider 환경변수로 넘긴다(코어가 환경변수를 읽는다)."""
    env = PROVIDER_KEY_ENV.get(settings.weave_llm_provider)
    if env and settings.weave_api_key and not os.environ.get(env):
        os.environ[env] = settings.weave_api_key


def weave_config() -> dict:
    """Weave project.json 과 같은 모양의 설정 — 코어에 인자로 주입한다.

    측정 조건과 같게: 추출은 weave_extract 가 llm_of() 로 병합하므로 effort 기본값(high)이 실리고,
    판정은 run_free_judge 처럼 effort 를 보내지 않는다(_judge_llm).
    """
    llm = {
        "provider": settings.weave_llm_provider,
        "model": settings.weave_llm_model,
        "extract_model": settings.weave_extract_model or None,
        "judge_model": settings.weave_judge_model or None,
        "temperature": settings.weave_temperature,
        "max_tokens": settings.weave_max_tokens,
    }
    if settings.weave_llm_provider == "openrouter" and settings.weave_reasoning_off:
        llm["extra"] = {"reasoning": {"enabled": False}}
    return {
        "name": "glitch",
        "timelines": ["main"],
        "judge": {
            "k": settings.weave_judge_k,
            "unit": settings.weave_judge_unit,
            "include_facts": False,
            "retrieval": settings.weave_retrieval,
            "majority": settings.weave_judge_majority,
        },
        "extraction": {"prompt": EXTRACT_PROMPT, "mode": "api"},
        "llm": llm,
    }


def _judge_llm(cfg: dict) -> dict:
    llm = w_llm.llm_of(cfg or {})
    llm["effort"] = ""  # 빈 값 → reasoning_effort 미전송 (llm_of 는 None 을 기본값 high 로 되돌린다)
    return llm


# ---------------------------------------------------------------- 작품 상태 (파일)

def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return k32.GetLastError() == 5  # 접근 거부 = 살아 있음
        try:
            code = ctypes.c_ulong()
            k32.GetExitCodeProcess(h, ctypes.byref(code))
            return code.value == 259  # STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# 이 프로세스가 지금 쥐고 있는 잠금 토큰. 잠금 파일의 pid 가 내 pid 인데 토큰이 여기 없으면
# 같은 pid 를 받은 이전 프로세스가 남긴 잠금이다 — 컨테이너 재시작 뒤 worker 가 다시 pid 1 이 되는 경우.
_HELD_TOKENS: set[str] = set()
_HELD_TOKENS_LOCK = threading.Lock()


class WeaveStore:
    """weave_data_dir/<project_id>/ 의 파일 배치."""

    # 잠금을 쥔 쪽은 LOCK_HEARTBEAT_SECONDS 마다 잠금 파일 mtime 을 갱신한다(백그라운드 스레드).
    # mtime 이 LOCK_STALE_SECONDS 넘게 멈췄으면 쥔 프로세스가 죽은 것 — host·pid 와 무관하게 회수.
    LOCK_HEARTBEAT_SECONDS = 20.0
    LOCK_STALE_SECONDS = 180.0

    def __init__(self, project_id: str, root: Path | None = None):
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", str(project_id))
        self.dir = Path(root or settings.weave_data_dir).resolve() / safe
        self._lock_token: str | None = None

    @property
    def registry_path(self) -> Path:
        return self.dir / "registry.json"

    @property
    def label_map_path(self) -> Path:
        return self.dir / "label_map.json"

    @property
    def lock_path(self) -> Path:
        return self.dir / ".lock"

    def episode_path(self, ep: int) -> Path:
        return self.dir / "episodes" / f"episode_{ep:02d}.json"

    def revision_path(self, ep: int, rev: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", rev)
        return self.dir / "episodes" / "rev" / f"episode_{ep:02d}_{safe}.json"

    def raw_dir(self, ep: int, chunk: int) -> Path:
        return self.dir / "raw" / f"ep{ep:02d}" / f"c{chunk:02d}"

    def review_path(self, ep: int) -> Path:
        return self.dir / "reviews" / f"review_{ep:02d}.json"

    def gate_path(self, ep: int, rev: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", rev)
        return self.dir / "gates" / f"gate_{ep:02d}_{safe}.json"

    @property
    def requests_dir(self) -> Path:
        return self.dir / "judge_requests"

    def _read(self, path: Path, default):
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default

    def load_registry(self) -> dict:
        return self._read(self.registry_path, None) or w_reg.empty_registry("Glitch 작품 엔티티 레지스트리")

    def load_label_map(self) -> dict:
        return self._read(self.label_map_path, {})

    def mapping(self, reg: dict | None = None) -> dict:
        """NEW:라벨 → 정식 ID. 레지스트리의 첫 라벨 + 이후 별칭으로 매칭된 라벨 전부."""
        return w_reg.mapping_of(reg if reg is not None else self.load_registry()) | self.load_label_map()

    def load_episode(self, ep: int) -> dict | None:
        return self._read(self.episode_path(ep), None)

    def load_revision(self, ep: int, rev: str) -> dict | None:
        return self._read(self.revision_path(ep, rev), None)

    def load_gate(self, ep: int, rev: str) -> dict | None:
        return self._read(self.gate_path(ep, rev), None)

    def write_json(self, path: Path, data) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        for attempt in range(20):  # Windows: 다른 쪽이 잠깐 열고 있으면 replace 가 거부된다
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                time.sleep(0.05 * (attempt + 1))
        tmp.unlink(missing_ok=True)
        raise WeaveError(f"작품 상태 파일을 쓰지 못했습니다: {path.name}")

    # -- 잠금 ------------------------------------------------------------
    def _lock_is_stale(self) -> bool:
        try:
            info = json.loads(self.lock_path.read_text(encoding="utf-8") or "{}")
            age = time.time() - self.lock_path.stat().st_mtime
        except FileNotFoundError:
            return False
        except (ValueError, OSError):
            return True  # 깨진 잠금 파일
        if not isinstance(info, dict):
            return True  # 옛 형식(pid 숫자만)
        if age > self.LOCK_STALE_SECONDS:
            return True  # heartbeat 가 끊겼다
        if info.get("host") != socket.gethostname():
            return False  # 다른 기계의 pid 는 확인할 수 없다 — heartbeat 로만 본다
        pid = int(info.get("pid") or 0)
        if pid == os.getpid():
            with _HELD_TOKENS_LOCK:
                return info.get("token") not in _HELD_TOKENS
        return not _pid_alive(pid)

    @contextmanager
    def lock(self, cancel: threading.Event | None = None, timeout: float = 900.0):
        """작품 단위 잠금(프로세스 간). 같은 작품의 레지스트리를 두 작업이 동시에 쓰지 않게.

        잠금 파일 = {pid, host, token}. 다음이면 버려진 잠금으로 보고 걷어낸다:
          · 같은 기계에서 잡은 프로세스가 죽었다 (pid 확인)
          · pid 가 내 pid 인데 이 프로세스가 쥔 토큰이 아니다 (재시작으로 pid 가 재사용됨)
          · heartbeat(mtime)가 LOCK_STALE_SECONDS 넘게 멈췄다 (SIGKILL 등 — host 무관)
        풀 때는 내 토큰일 때만 지운다(남의 잠금을 지우지 않게).
        """
        self.dir.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        body = json.dumps({"pid": os.getpid(), "host": socket.gethostname(), "token": token})
        # 파일을 쓰기 전에 등록한다 — 같은 프로세스의 대기자가 쓰인 직후의 내 잠금을 버려진 것으로 오판하지 않게
        with _HELD_TOKENS_LOCK:
            _HELD_TOKENS.add(token)
        try:
            deadline = time.monotonic() + timeout
            while True:
                if cancel is not None and cancel.is_set():
                    raise WeaveCancelled("작업이 취소되었습니다.")
                try:
                    fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        f.write(body)
                    break
                except FileExistsError:
                    if self._lock_is_stale():
                        stale = self.lock_path.with_name(f".lock.stale.{token}")
                        try:  # rename 은 한 쪽만 성공한다 — 두 대기자가 동시에 걷어내는 경쟁 방지
                            os.replace(self.lock_path, stale)
                            stale.unlink(missing_ok=True)
                            logger.warning("weave: stale lock removed (%s)", self.dir.name)
                        except OSError:
                            pass
                        continue
                    if time.monotonic() > deadline:
                        raise WeaveError("같은 작품의 다른 분석이 끝나지 않았습니다. 잠시 후 다시 시도하세요.")
                    time.sleep(0.5)
            self._lock_token = token
            beat_stop = threading.Event()

            def beat() -> None:
                while not beat_stop.wait(self.LOCK_HEARTBEAT_SECONDS):
                    self.heartbeat()

            beater = threading.Thread(target=beat, name=f"weave-lock-{self.dir.name}", daemon=True)
            beater.start()
            try:
                yield
            finally:
                beat_stop.set()
                beater.join(timeout=5)
                self._lock_token = None
                try:
                    if json.loads(self.lock_path.read_text(encoding="utf-8")).get("token") == token:
                        self.lock_path.unlink(missing_ok=True)
                except (FileNotFoundError, ValueError, OSError):
                    pass
        finally:
            with _HELD_TOKENS_LOCK:
                _HELD_TOKENS.discard(token)

    def heartbeat(self) -> None:
        if self._lock_token:
            try:
                os.utime(self.lock_path)
            except OSError:
                pass


# ---------------------------------------------------------------- 추출

def chunk_text(text: str, limit: int, min_chunk: int = 1000, tail_merge: int = 800) -> list[str]:
    """문단 경계로 limit 자 안팎씩 나눈다 (Weave chunk_sources.chunk_text 와 같은 규칙)."""
    paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks, cur = [], []
    for p in paras:
        cur_len = sum(len(x) for x in cur) + 2 * max(len(cur) - 1, 0)
        if cur and cur_len >= min_chunk and cur_len + 2 + len(p) > limit:
            chunks.append("\n\n".join(cur))
            cur = []
        cur.append(p)
    if cur:
        chunks.append("\n\n".join(cur))
    if len(chunks) >= 2 and len(chunks[-1]) < tail_merge:
        chunks[-2] = chunks[-2] + "\n\n" + chunks[-1]
        chunks.pop()
    return chunks or [text]


def _rewrite(obj, table: dict):
    """정확히 일치하는 문자열만 치환 (부분 문자열 치환으로 'NEW:남자_2' 가 깨지지 않게)."""
    if isinstance(obj, dict):
        return {k: _rewrite(v, table) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_rewrite(v, table) for v in obj]
    if isinstance(obj, str) and obj in table:
        return table[obj]
    return obj


DECL_KEYS = {
    "worldview_rules": "rule_id",
    "characters": "character_id",
    "locations": "location_id",
    "objects": "object_id",
    "times": "time_id",
}


def _check_cancel(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise WeaveCancelled("작업이 취소되었습니다.")


# 사건에서 인물 ID 가 들어가는 칸. 여기 쓰인 라벨은 종류가 '인물'로 정해진다.
CAST_FIELDS = ("main_actor_character_id", "participant_character_ids", "witness_character_ids")


def cast_ids(ev: dict) -> list[str]:
    """사건 하나의 인물 ID(주체·참여·목격) — 등장 순서 유지, 중복 없음."""
    out: list[str] = []
    for f in CAST_FIELDS:
        v = ev.get(f)
        for x in (v if isinstance(v, list) else [v]):
            if isinstance(x, str) and x and x not in out:
                out.append(x)
    return out


def declare_cast_labels(doc: dict) -> list[str]:
    """사건의 인물 칸에만 쓰이고 characters 선언이 빠진 NEW: 라벨을 인물로 선언해 넣는다.

    추출기가 선언을 빠뜨리면 코어 merge 는 그 라벨을 '종류 모름'으로 검토 큐에만 올린다
    (사건 본문 어디에 나왔는지 모르기 때문). 하지만 인물 칸에 쓰였다면 종류는 인물로 확정이다.
    선언을 보충해 두면 merge 가 기존 인물과 맞추거나 새로 등재한다. doc 를 제자리 수정.
    반환: 보충한 라벨 목록.
    """
    chars = doc.setdefault("characters", [])
    if not isinstance(chars, list):
        return []
    declared = {c.get("character_id") for c in chars if isinstance(c, dict)}
    added: list[str] = []
    for ev in doc.get("events") or []:
        if not isinstance(ev, dict):
            continue
        for label in cast_ids(ev):
            if not label.startswith("NEW:") or label in declared:
                continue
            chars.append({"character_id": label, "name": w_reg.label_body(label),
                          "entity_type": "unknown", "_declared_by": "adapter"})
            declared.add(label)
            added.append(label)
    return added


def extract_episode(text: str, ep: int, store: WeaveStore, cfg: dict,
                    cancel: threading.Event | None = None) -> tuple[dict, dict, dict, list]:
    """원문 → 화 사건 로그. 조각별로 추출하고 매 조각 뒤 레지스트리를 병합한다.

    조각마다:
      · 사건 ID 를 위치 순서로 EVENT_<화>_<n> 재부여 (조각 안 상호 참조도 함께 치환)
      · scene_id 앞에 조각 번호를 붙여 조각 간 장면 ID 충돌을 막는다 (각 조각이 scene_<화>_1 부터 매김)
      · NEW:라벨을 정식 ID 로 치환 (merge 가 별칭·이름으로 매칭한 라벨 포함). 애매한 라벨은 NEW: 그대로
    반환: (episode_doc, registry, label_map, pending_labels). 디스크에는 쓰지 않는다.
    """
    reg = store.load_registry()
    label_map = store.load_label_map()
    merged: dict | None = None
    events: list[dict] = []
    pending: list[dict] = []
    chunks = chunk_text(text, settings.weave_chunk_chars)
    for ci, chunk in enumerate(chunks, 1):
        _check_cancel(cancel)
        store.heartbeat()
        res = w_extract.extract(chunk, reg, ep, cfg, store.raw_dir(ep, ci), base=CORE_DIR)
        _check_cancel(cancel)
        if res["status"] == "manual":
            raise WeaveError(
                f"LLM 키가 설정되지 않아 추출할 수 없습니다 (provider={settings.weave_llm_provider}). "
                "WEAVE_API_KEY 를 설정하세요."
            )
        if res["status"] != "ok":
            raise WeaveError(f"추출 실패 ({ci}/{len(chunks)} 조각): {res.get('message')}")
        doc = res["document"]
        evs = [e for e in (doc.get("events") or []) if isinstance(e, dict)]
        table: dict[str, str] = {}
        new_ids = []
        for ev in evs:
            new_id = f"EVENT_{ep}_{len(events) + len(new_ids) + 1}"
            new_ids.append(new_id)
            if ev.get("event_id") and ev["event_id"] not in table:
                table[ev["event_id"]] = new_id
        doc = _rewrite(doc | {"events": evs}, table)
        for ev, new_id in zip(doc["events"], new_ids):  # 같은 원래 ID 가 두 번 나와도 위치대로
            ev["event_id"] = new_id
            if len(chunks) > 1 and ev.get("scene_id"):
                ev["scene_id"] = f"c{ci}:{ev['scene_id']}"
        declare_cast_labels(doc)
        m = w_reg.merge(doc, reg, ep)
        reg = m["registry"]
        label_map.update(m["mapping"])
        pending.extend(m["pending"])
        doc = _rewrite(doc, {k: v for k, v in (w_reg.mapping_of(reg) | label_map).items()
                             if k.startswith("NEW:")})
        if merged is None:
            merged = {k: v for k, v in doc.items() if k != "events"}
            for coll in DECL_KEYS:
                merged[coll] = []
        for coll, key in DECL_KEYS.items():
            have = {d.get(key) for d in merged[coll]}
            for d in doc.get(coll) or []:
                if isinstance(d, dict) and d.get(key) not in have:
                    merged[coll].append(d)
                    have.add(d.get(key))
        for ev in doc["events"]:
            ev["order"] = len(events) + 1
            events.append(ev)
    assert merged is not None
    merged["events"] = events
    merged["episode"] = ep
    merged["_chunks"] = len(chunks)
    merged["_pending_labels"] = [p.get("label") for p in pending]
    if settings.weave_prejudge_f2 or settings.weave_prejudge_f2_5:  # F2 추출 후처리 — 플래그가 꺼지면 문서는 그대로
        merged, f2_counts = w_prejudge.f2_doc(merged, reg, rules=w_prejudge.f2_rules(
            base=settings.weave_prejudge_f2, assumed_location=settings.weave_prejudge_f2_5))
        merged["_prejudge_f2"] = dict(f2_counts)
    reg["last_episode"] = max(int(reg.get("last_episode") or 0), ep)
    return merged, reg, label_map, pending


def _ident(raw_id: str | None, mapping: dict) -> str | None:
    if not raw_id:
        return None
    return mapping.get(raw_id, raw_id)


CO_APPEARANCE = "co_appearance"
CO_APPEARANCE_LABEL = "함께 등장"


def co_appearance_key(a: str, b: str) -> str:
    """공동 등장 관계의 entity_key — 방향이 없으므로 두 인물 ID 를 정렬해 하나로."""
    x, y = sorted((a, b))
    return f"{x}~{y}"


def episode_to_delta(doc: dict, reg: dict, mapping: dict, rev: str | None,
                     known_relations: frozenset[str] | set[str] = frozenset(),
                     gate: w_gate.GateResult | None = None) -> CanonDelta:
    """Weave 화 사건 로그 → 몸통 canon_delta (6기준). 같은 entity_key 는 한 번만.

    관계: Weave 추출 스키마에는 인물 관계가 없다. 같은 사건의 참여 인물(주체 포함)끼리
    '함께 등장' 관계로 잇는다 — 관계의 종류·감정은 담지 않는다(추출 프롬프트·LLM 호출 무변경).
    앞 회차가 이미 캐논에 넣은 쌍(known_relations)은 다시 내지 않는다. 내용이 회차마다
    달라 새 버전이 쌓이면 '설정 변경'처럼 보이기 때문이다.

    신뢰 등급(gate, 추출 관문 결과): 사건 attrs.trust = 그 사건의 등급, 관계 attrs.trust = 근거 사건 중
    하나라도 확정이면 confirmed. 이 값은 캐논에 INSERT 될 때의 값이며 나중에 승격하지 않는다.
    gate 가 없으면(관문 실패) trust 키를 넣지 않는다.
    """
    by_id = {c: {e["id"]: e for e in reg.get(c, [])} for c in w_reg.COLLECTIONS}

    def name_of(coll: str, decl: dict, eid: str, field: str) -> str:
        return (by_id.get(coll, {}).get(eid, {}).get(field)
                or decl.get(field) or w_reg.label_body(decl.get(DECL_KEYS[coll]) or "") or eid)

    delta = CanonDelta()
    seen: dict[str, set] = {}

    def fresh(cat: str, key: str | None) -> bool:
        s = seen.setdefault(cat, set())
        if not key or key in s:
            return False
        s.add(key)
        return True

    for d in doc.get("worldview_rules") or []:
        eid = _ident(d.get("rule_id"), mapping)
        if not fresh("worldview", eid):
            continue
        delta.worldview.append(WorldviewDelta(
            entity_key=eid, item=name_of("worldview_rules", d, eid, "content"),
            detail=d.get("certainty"),
            attrs={"weave_id": eid, "certainty": d.get("certainty")},
            quote=d.get("source_text"),
        ))
    for d in doc.get("characters") or []:
        eid = _ident(d.get("character_id"), mapping)
        if not fresh("characters", eid):
            continue
        item = by_id["characters"].get(eid, {})
        delta.characters.append(CharacterDelta(
            entity_key=eid, name=name_of("characters", d, eid, "name"),
            group_name=d.get("entity_type") or item.get("entity_type"),
            attrs={"weave_id": eid, "aliases": item.get("aliases") or d.get("aliases") or [],
                   "description": d.get("description")},
        ))
    # 이 화의 사건에 나온 기존 인물. 추출의 characters 에는 '처음 나온' 인물만 선언되므로
    # 이것을 더하지 않으면 2화부터 회차 인물 목록에서 기존 인물이 빠진다.
    # description 키는 넣지 않는다 — 캐논 누적 병합이 None 으로 앞 회차 설명을 덮지 않고,
    # 내용이 같으면 새 버전도 생기지 않는다(unchanged).
    for ev in doc.get("events") or []:
        for cid in cast_ids(_norm(ev, mapping)):
            item = by_id["characters"].get(cid)
            if item is None or not fresh("characters", cid):
                continue
            delta.characters.append(CharacterDelta(
                entity_key=cid, name=item.get("name") or cid,
                group_name=item.get("entity_type"),
                attrs={"weave_id": cid, "aliases": item.get("aliases") or []},
            ))
    for d in doc.get("locations") or []:
        eid = _ident(d.get("location_id"), mapping)
        if not fresh("spaces", eid):
            continue
        delta.spaces.append(SpaceDelta(
            entity_key=eid, place=name_of("locations", d, eid, "name"),
            attrs={"weave_id": eid, "description": d.get("description")},
        ))
    for d in doc.get("times") or []:
        eid = _ident(d.get("time_id"), mapping)
        if not fresh("timeline", eid):
            continue
        delta.timeline.append(TimelineDelta(
            entity_key=eid, order_index=len(delta.timeline) + 1,
            time_value=name_of("times", d, eid, "expression"),
            attrs={"weave_id": eid, "timeline": d.get("timeline")},
        ))
    pairs: dict[tuple[str, str], list[dict]] = {}
    for ev in doc.get("events") or []:
        if not fresh("events", ev.get("event_id")):
            continue
        n = _norm(ev, mapping)
        cast = set(n.get("participant_character_ids") or [])
        if n.get("main_actor_character_id"):
            cast.add(n["main_actor_character_id"])
        # 레지스트리에 정식 등록된 인물만 (애매해서 NEW: 로 남은 라벨은 잇지 않는다)
        cast = sorted(c for c in cast if isinstance(c, str) and c in by_id["characters"])
        for a, b in combinations(cast, 2):
            pairs.setdefault((a, b), []).append(ev)
        delta.events.append(EventDelta(
            entity_key=ev["event_id"], label=ev.get("title") or ev["event_id"],
            stage=ev.get("reality_status"), order_index=int(ev.get("order") or 0),
            attrs={
                "summary": ev.get("summary"), "cause": ev.get("cause"), "result": ev.get("result"),
                "scene_id": ev.get("scene_id"), "location_id": n.get("location_id"),
                "time_id": n.get("time_id"), "certainty": ev.get("certainty"),
                "source_text": ev.get("source_text"),
                "participants": n.get("participant_character_ids") or [],
                "weave_rev": rev,
            } | ({"trust": gate.grade_of(ev["event_id"]) or w_gate.CONFIRMED} if gate else {}),
            quote=ev.get("source_text"),
        ))

    ep = doc.get("episode")
    for (a, b), evs in pairs.items():
        key = co_appearance_key(a, b)
        if key in known_relations:
            continue
        first = evs[0]
        where = f"{ep}화 " if ep is not None else ""
        more = f" 등 사건 {len(evs)}개" if len(evs) > 1 else ""
        delta.relations.append(RelationDelta(
            entity_key=key, from_char=a, to_char=b, relation=CO_APPEARANCE_LABEL,
            attrs={
                "basis": CO_APPEARANCE,
                "from_name": by_id["characters"][a].get("name") or a,
                "to_name": by_id["characters"][b].get("name") or b,
                "description": f"{where}「{first.get('title') or first.get('event_id')}」{more}에 함께 등장",
                "event_ids": [e.get("event_id") for e in evs],
                "source_text": first.get("source_text"),
            } | ({"trust": w_gate.CONFIRMED if any(
                gate.grade_of(e.get("event_id")) != w_gate.TENTATIVE for e in evs)
                else w_gate.TENTATIVE} if gate else {}),
            quote=first.get("source_text"),
        ))
    return delta


# ---------------------------------------------------------------- 검수

def confirmed_episodes(canon: CanonSnapshot, before: int, store: WeaveStore,
                       mapping: dict, *, gate_mode: str = "off",
                       gate_info: dict | None = None) -> tuple[list[dict], list[str]]:
    """저장된 회차의 '저장된 판본'만, 저장된 사건만 골라 원장 재생용 화 목록을 만든다.

    캐논 사건의 attrs.weave_rev 로 그 회차를 저장할 때의 추출 판본을 찾는다(재추출 후 저장하지
    않은 판본은 쓰지 않는다). 캐논에 남은 사건 ID 만 쓴다(1차 검토에서 지운 사건 제외).

    관문(gate_mode, 기본 off = 등급 파일을 읽지 않는다 — 이전 동작 그대로):
      annotate  원장은 그대로. 사건을 가져온 판본의 등급 파일로 잠정 사건 ID 를 gate_info 에 모은다.
      exclude   같은 등급 파일로 잠정 사건·상태 변화를 빼고 재생용 화를 만든다(extract_gate.filter_episode).
    등급 파일이 없는 판본(관문 도입 전)은 전부 확정으로 본다. gate_info 를 주면 다음 키를 채운다:
      tentative_event_ids, excluded_event_ids, excluded_state_changes, missing_gates
    반환: (episodes, notes)
    """
    by_ch: dict[int, dict[str, set]] = {}
    for e in canon.events:
        sc = e.get("source_chapter")
        if not isinstance(sc, int) or sc >= before:
            continue
        rev = (e.get("attrs") or {}).get("weave_rev")
        slot = by_ch.setdefault(sc, {"ids": set(), "revs": set()})
        slot["ids"].add(e.get("entity_key"))
        if rev:
            slot["revs"].add(rev)
    out, notes = [], []
    gated = gate_mode in ("annotate", "exclude")
    info = {"tentative_event_ids": [], "excluded_event_ids": [], "excluded_state_changes": 0,
            "missing_gates": []}
    for n in sorted(by_ch):
        slot = by_ch[n]
        pairs = [(r, store.load_revision(n, r)) for r in sorted(slot["revs"])]
        pairs = [(r, d) for r, d in pairs if d]
        docs = [d for _, d in pairs]
        if not docs:
            notes.append(f"{n}화: Weave 추출 판본 없음(다른 파이프라인으로 저장된 회차) — 원장에서 제외")
            continue
        events, have = [], set()
        for r, d in pairs:
            gate = store.load_gate(n, r) if gated else None
            if gated and gate is None:
                info["missing_gates"].append(f"{n}:{r}")
            tentative = set((gate or {}).get("tentative_event_ids") or [])
            for ev in d.get("events") or []:
                eid = ev.get("event_id")
                if eid not in slot["ids"] or eid in have:
                    continue
                have.add(eid)
                if eid in tentative:
                    info["tentative_event_ids"].append(eid)
                if gate_mode == "exclude":
                    ev, n_sc = w_gate.filter_event(ev, gate)
                    if ev is None:
                        info["excluded_event_ids"].append(eid)
                        continue
                    info["excluded_state_changes"] += n_sc
                events.append(ev)
        base = dict(docs[-1]) | {"events": sorted(events, key=lambda x: x.get("order", 0))}
        out.append(_norm(base, mapping))
    if gate_info is not None:
        info["tentative_event_ids"] = sorted(set(info["tentative_event_ids"]))
        info["excluded_event_ids"] = sorted(set(info["excluded_event_ids"]))
        gate_info.update(info)
    return out, notes


def _verify_into(gate_dict: dict, doc: dict, confirmed_eps: list[dict], mapping: dict,
                 reg: dict | None, text: str | None) -> dict:
    """추출 검증기(LLM 0회) 신호를 등급 파일 dict 에 옆 키로 더한 새 dict. 등급·잠정 집합은 그대로다(안 B).

    검증기가 실패하면 관문 결과를 그대로 돌려주고 로그만 남긴다 — 관문이 실패해도 추출을 살리는 것과 같은 층위."""
    try:
        vres = w_verifier.verify_episode(doc, confirmed_eps, mapping, registry=reg, text=text,
                                         knows_mode=settings.weave_knows_mode)
        return w_vmerge.merge_verifier(gate_dict, vres)
    except Exception:  # noqa: BLE001 - 검증기는 보조 표시다
        logger.exception("weave verifier failed: ep=%s rev=%s", doc.get("episode"), doc.get("_rev"))
        return gate_dict


def judge_registry(reg: dict, canon: CanonSnapshot, delta: CanonDelta) -> dict:
    """판정 프롬프트 (b)절용 레지스트리 — 세계관 규칙은 저장된 캐논 + 이번 회차 확정 델타만.

    레지스트리에는 저장하지 않은 회차·지운 규칙도 남아 있으므로 그대로 쓰지 않는다.
    """
    out = {k: v for k, v in reg.items() if k != "worldview_rules"}
    rules, seen = [], set()
    items = [(w.get("entity_key"), w.get("item"), (w.get("attrs") or {}).get("certainty"),
              w.get("source_chapter")) for w in canon.worldview]
    items += [(w.entity_key, w.item, (w.attrs or {}).get("certainty"), None) for w in delta.worldview]
    for i, (key, text, cert, ch) in enumerate(items, 1):
        text = (text or "").strip()
        norm = w_reg.norm_text(text)
        if not text or norm in seen:
            continue
        seen.add(norm)
        rid = key if isinstance(key, str) and key.startswith("RULE_") else f"RULE_U{i:03d}"
        rules.append({"id": rid, "content": text, "certainty": cert or "explicit",
                      "first_episode": ch if ch is not None else "이번 화"})
    out["worldview_rules"] = rules
    return out


def locate(text: str, quote: str | None) -> tuple[Span, bool]:
    """인용 원문을 회차 본문에서 찾아 span 으로. 반환 (span, 찾았는지)."""
    q = (quote or "").strip().strip('"“”')
    if not text:
        return Span(start=0, end=0, quote=q), False
    if q:
        i = text.find(q)
        if i >= 0:
            return Span(start=i, end=i + len(q), quote=q), True
        pattern = r"\s+".join(re.escape(t) for t in q.split())
        m = re.search(pattern, text) if pattern else None
        if m:
            return Span(start=m.start(), end=m.end(), quote=text[m.start():m.end()]), True
        for n in (30, 15, 8):
            head = q[:n]
            if len(head) >= 4:
                i = text.find(head)
                if i >= 0:
                    end = min(len(text), i + len(q))
                    return Span(start=i, end=end, quote=text[i:end]), True
    end = min(len(text), 40)
    return Span(start=0, end=end, quote=text[:end]), False


def _checker_type(item: dict) -> str:
    field, entity = item.get("field") or "", item.get("entity") or ""
    if field == "location" or entity.startswith("LOC_"):
        return "space"
    if entity.startswith("CHAR_") or item.get("type", "").startswith("knowledge"):
        return "character"
    return "setting"


def _ref(corpus_by_id: dict, ids: list, current_ep: int) -> ConflictRef:
    for eid in ids:
        ev = corpus_by_id.get(eid)
        if ev and ev.get("episode") != current_ep:
            return ConflictRef(chapter_no=ev.get("episode"), quote=ev.get("source_text"))
    return ConflictRef()


NOT_FOUND_NOTE = " (근거 문장을 본문에서 찾지 못해 회차 첫머리에 표시)"


TENTATIVE_NOTE = " (낮은 확신 — 근거가 잠정 사실)"


def _tentative(ids: list, tentative_event_ids: frozenset[str]) -> str:
    """근거 사건 ID 가 하나 이상 있고 전부 잠정 사실이면 꼬리표."""
    ids = [i for i in ids if i]
    return TENTATIVE_NOTE if ids and all(i in tentative_event_ids for i in ids) else ""


def checks_to_contradictions(checks: dict, events: list[dict], corpus: list[dict],
                             text: str, ep: int, k: int, *,
                             tentative_event_ids: frozenset[str] = frozenset()) -> list[ContradictionOut]:
    by_event = {e.get("event_id"): e for e in events}
    corpus_by_id = {e.get("event_id"): e for e in corpus}
    learned = (checks.get("config") or {}).get("judge_mode") == "learned"
    out: list[ContradictionOut] = []

    for it in checks["checker"]:
        sev = it.get("severity")
        if sev not in SEVERITY_CONFIDENCE:
            continue
        ev = by_event.get(it.get("event_id"), {})
        span, found = locate(text, ev.get("source_text"))
        expl = (f"[규칙 검사·{sev}] {it.get('entity')}.{it.get('field')} — "
                f"이번 회차 전제: {it.get('premise')} / 원장 값: {it.get('ledger_value')}")
        if it.get("detail"):
            expl += f" ({it['detail']})"
        out.append(ContradictionOut(
            span=span, conflict_ref=_ref(corpus_by_id, [it.get("since")], ep),
            type=_checker_type(it),
            explanation=(expl + ("" if found else NOT_FOUND_NOTE)
                         + _tentative([it.get("since")], tentative_event_ids)),
            confidence=SEVERITY_CONFIDENCE[sev],
        ))

    for it in checks["judge"]:
        if learned:
            c = _learned_contradiction(it, by_event, corpus_by_id, text, ep, tentative_event_ids)
            if c is not None:
                out.append(c)
            continue
        runs = it.get("runs", [])
        answered = [r for r in runs if r.get("verdict")]
        positives = [r for r in answered if r.get("verdict") in w_pipe.POSITIVE_VERDICTS]
        # 검출률은 '요청한 k회' 기준 — 실패한 회차를 빼고 나누면 1/1 이 100%로 부풀려진다
        rate = len(positives) / max(k, 1)
        if not positives or rate < settings.weave_min_detection_rate:
            continue
        rep = max(positives, key=lambda r: r.get("confidence") or 0)
        first = by_event.get((it.get("event_ids") or [None])[0], {})
        agg = it.get("aggregate") or {}
        evid = [e for e in agg.get("evidence_union", []) if e not in by_event]
        verdict = rep.get("verdict")
        failed = k - len(answered)
        note = f"판정 {k}회 중 {len(positives)}회 검출"
        if failed > 0:
            note += f", {failed}회는 호출 실패"
        if len(positives) not in (0, len(answered)):
            note += ", 판정이 갈린 경계 사례"
        span, found = locate(text, first.get("source_text"))
        out.append(ContradictionOut(
            span=span, conflict_ref=_ref(corpus_by_id, evid, ep),
            type=VERDICT_TYPE.get(verdict, "event"),
            explanation=(f"[LLM 판정·{verdict}] {rep.get('reason') or ''} ({note})".strip()
                         + ("" if found else NOT_FOUND_NOTE) + _tentative(evid, tentative_event_ids)),
            confidence=round(rate, 3),
        ))
    return out


def _learned_contradiction(it: dict, by_event: dict, corpus_by_id: dict, text: str, ep: int,
                           tentative_event_ids: frozenset[str]) -> ContradictionOut | None:
    """학습 판정(k=1) 항목 → 모순. confidence 는 모델의 모순 확률, '판정 k회 중' 문구 없음."""
    rep = next((r for r in it.get("runs", []) if r.get("verdict") in w_pipe.POSITIVE_VERDICTS), None)
    if rep is None:
        return None
    verdict = rep.get("verdict")
    prem = rep.get("evidence_premise") or {}
    evid = [e for e in rep.get("evidence_event_ids") or [] if e not in by_event]
    first = by_event.get(prem.get("event_id")) or by_event.get((it.get("event_ids") or [None])[0], {})
    span, found = locate(text, first.get("source_text"))
    expl = f"[학습 판정·{verdict}] 근거 전제({prem.get('kind')} {prem.get('id')}): {prem.get('text') or ''}"
    if prem.get("hypothesis"):
        expl += f" ↔ 이번 회차: {prem['hypothesis']}"
    return ContradictionOut(
        span=span, conflict_ref=_ref(corpus_by_id, evid, ep),
        type=VERDICT_TYPE.get(verdict, "event"),
        explanation=expl + ("" if found else NOT_FOUND_NOTE) + _tentative(evid, tentative_event_ids),
        confidence=round(float(rep.get("confidence") or 0.0), 3),
    )


# ---------------------------------------------------------------- 판정 병렬 실행

_PATCH_LOCK = threading.Lock()


def _judge_run(group: dict, prompt: str, i: int, cfg: dict) -> dict:
    """판정 1회. 결과 dict 모양은 코어 weave_pipeline.judge_packet 의 api 경로와 같다."""
    llm = _judge_llm(cfg)
    model = llm.get("judge_model") or llm.get("model")
    r = w_llm.complete_json(None, prompt, llm, role="judge")
    d = r.get("data") or {}
    ok = r["status"] == "ok" and bool(d.get("verdict"))
    return {
        "run_index": i,
        "status": "ok" if ok else "failed",
        "verdict": d.get("verdict") if ok else None,
        "reason": d.get("reason"),
        "evidence_event_ids": d.get("evidence_event_ids", []) or [],
        "confidence": d.get("confidence"),
        "judged_by": f"{llm['provider']} / {model}",
        "judged_at": w_pipe.now(),
        "batch_id": f"{group['key']}-r{i}",
        "retried": r.get("retried", False),
        "parse_retried": r.get("parse_retried", False),
        "attempts": r.get("attempts"),
        "elapsed_sec": r.get("elapsed_sec"),
        "message": r.get("message") if r["status"] != "ok" else
                   (None if ok else "응답에 verdict 가 없습니다"),
        "raw": None if ok else (r.get("raw") or "")[:2000],
    }


def run_checks_parallel(events, ledger, mapping, corpus, cfg, request_dir, *,
                        registry, concurrency: int = 3,
                        cancel: threading.Event | None = None) -> dict:
    """코어 run_checks 를 그대로 쓰되, 판정 호출(장면 수 × k)을 한 풀에서 보낸다.

    1) 코어 run_checks 를 judge_packet 을 '예약만 하는' 대역으로 바꿔 돌린다
       → 검사기·패킷 구성·프롬프트는 코어 그대로, LLM 호출은 0회.
    2) 예약된 (패킷, 회차) 전부를 스레드 풀(동시 concurrency 개)로 호출해 자리를 채운다.
       취소되거나 HTTP 429(한도 초과)가 나면 아직 안 보낸 호출은 보내지 않는다.
    3) 코어 aggregate_runs / trace_evidence 로 집계를 다시 계산한다.
    판정 규칙·프롬프트·집계는 바뀌지 않는다 — 바뀌는 것은 호출 순서(동시성)뿐이다.
    """
    learned = settings.weave_judge_mode == "learned"
    if learned:  # 학습 judge 는 결정적 — 반복 판정이 의미 없다. k=1·majority=1 강제
        cfg = dict(cfg) | {"judge": dict(cfg.get("judge") or {}) | {"k": 1, "majority": 1}}
    booked: list[tuple[dict, str, int, list]] = []
    pf = settings
    p2 = pf.weave_prejudge_f4 or pf.weave_prejudge_f5
    starts = None
    if p2:  # F4: 경계 인식 정규화 1회 뒤 코어에는 mapping={} / F5: 패킷 재묶음(사본의 scene_id)
        events = [_norm(e, mapping) for e in events]
        if pf.weave_prejudge_f5:
            events, starts = w_prejudge.f5_regroup(events)
    core_mapping = {} if pf.weave_prejudge_f4 else mapping
    view = w_prejudge.f6_view if pf.weave_prejudge_f6 else None
    names = None
    if pf.weave_prejudge_f7:
        from app.pipeline.pairs import _names
        names = _names(registry)

    def book(group, prompt, k, mode, request_dir, cfg=None, run_offset=0):  # noqa: ANN001
        slots = [{"run_index": i, "status": "pending"} for i in range(1, k + 1)]
        booked.append((group, prompt, k, slots))
        return slots

    with _PATCH_LOCK:  # 모듈 전역을 잠깐 바꾸므로 같은 프로세스의 다른 검수와 겹치지 않게
        original = w_pipe.judge_packet
        w_pipe.judge_packet = book
        try:
            with w_prejudge.judge_prompt_patch(
                    w_pipe, f1=settings.weave_prejudge_f1, f3=settings.weave_prejudge_f3,
                    episode_events=[_norm(e, mapping) for e in events], corpus=corpus,
                    f7=pf.weave_prejudge_f7, f9=pf.weave_prejudge_f9, starts=starts, view=view, names=names) as pj:
                checks = w_pipe.run_checks(events, ledger, core_mapping, corpus, cfg, request_dir,
                                           judge_mode="api", registry=registry)
        finally:
            w_pipe.judge_packet = original

    if pf.weave_prejudge_f8:
        checks["checker"] = w_prejudge.f8_downgrade(checks["checker"], {e.get("event_id"): e for e in events}, pj)
    if pj:
        checks["config"]["prejudge"] = dict(pj)
    if learned:
        snaps = (w_prejudge.Snapshots(ledger, [_norm(e, mapping) for e in events], starts=starts, view=view)
                 if settings.weave_prejudge_f1 else None)
        _judge_learned(booked, checks, ledger, corpus, registry, cancel, snapshots=snaps)
        return _finish_checks(checks, ledger, corpus, cfg, concurrency=1, stopped=None, learned=True)

    stop = threading.Event()
    stop_reason: list[str] = []

    def one(g, p, i):  # noqa: ANN001
        if cancel is not None and cancel.is_set():
            return {"run_index": i, "status": "skipped", "verdict": None, "message": "작업 취소로 보내지 않음"}
        if stop.is_set():
            return {"run_index": i, "status": "skipped", "verdict": None,
                    "message": f"보내지 않음 — {stop_reason[0] if stop_reason else '중단'}"}
        res = _judge_run(g, p, i, cfg)
        if "HTTP 429" in (res.get("message") or ""):
            if not stop.is_set():
                stop_reason.append("LLM 호출 한도 초과(HTTP 429)")
                stop.set()
        return res

    jobs = [(g, p, i, slots) for g, p, k, slots in booked for i in range(1, k + 1)]
    if jobs:
        with ThreadPoolExecutor(max_workers=max(1, min(concurrency, len(jobs)))) as ex:
            futs = {ex.submit(one, g, p, i): (slots, i) for g, p, i, slots in jobs}
            for fut in as_completed(futs):
                slots, i = futs[fut]
                try:
                    res = fut.result()
                except Exception as e:  # noqa: BLE001 - 한 회차 실패가 전체를 죽이지 않게
                    res = {"run_index": i, "status": "failed", "verdict": None,
                           "message": f"{type(e).__name__}: {e}"}
                slots[i - 1].clear()
                slots[i - 1].update(res)

    return _finish_checks(checks, ledger, corpus, cfg, concurrency=concurrency,
                          stopped=stop_reason[0] if stop_reason else None)


def _judge_learned(booked: list, checks: dict, ledger, corpus: list, registry,  # noqa: ANN001
                   cancel: threading.Event | None, snapshots=None) -> None:  # noqa: ANN001
    """예약된 패킷마다 학습 judge 1회(LLM 없음). 패킷 순서대로 하나씩 — 결정성."""
    from app.pipeline.learned_judge import judge_from_settings

    judge = judge_from_settings()
    # 판정 회차: 사건에 episode 가 없으면 확정 corpus 의 마지막 회차 + 1(검수는 N-1화 시점 원장)
    eps = [int(e["episode"]) for e in corpus or [] if isinstance(e.get("episode"), int)]
    current_episode = max(eps) + 1 if eps else None
    for group, _prompt, _k, slots in booked:
        if cancel is not None and cancel.is_set():
            res = {"run_index": 1, "status": "skipped", "verdict": None, "message": "작업 취소로 판정하지 않음"}
        else:
            ids = {e.get("event_id") for e in group["events"]}
            items = [c for c in checks["checker"] if c.get("event_id") in ids]
            res = judge.judge_group(group, ledger, corpus, registry, items, current_episode=current_episode,
                                    **({"snapshots": snapshots} if snapshots is not None else {}))
        slots[0].clear()
        slots[0].update(res)
    checks["config"]["judge_model_hash"] = judge.backend.model_hash
    checks["config"]["judge_threshold"] = judge.threshold
    env = getattr(judge.backend, "env", None)
    checks["config"]["judge_env"] = env() if callable(env) else None  # 패킷마다가 아니라 회차에 한 번


def _finish_checks(checks: dict, ledger, corpus: list, cfg: dict, *, concurrency: int,  # noqa: ANN001
                   stopped: str | None, learned: bool = False) -> dict:
    """코어 aggregate_runs / trace_evidence 로 집계를 다시 계산한다."""
    majority = int((cfg.get("judge") or {}).get("majority", 2))
    for it in checks["judge"]:
        it["aggregate"] = w_pipe.aggregate_runs(it["runs"], majority)
        it["evidence_trace"] = w_pipe.trace_evidence(
            ledger, corpus, {"evidence": it["aggregate"].get("evidence_union", [])})
    checks["config"]["concurrency"] = concurrency
    checks["config"]["stopped"] = stopped
    if learned:
        checks["config"]["judge_mode"] = "learned"
    return checks


# ---------------------------------------------------------------- 파이프라인

async def _in_thread(fn, *args):
    """to_thread + 취소 전달. 코루틴이 취소되면 스레드에 신호를 주고 CancelledError 를 다시 던진다."""
    cancel = threading.Event()
    try:
        return await asyncio.to_thread(fn, *args, cancel)
    except asyncio.CancelledError:
        cancel.set()
        raise


class WeavePipeline:
    def __init__(self, data_dir: str | None = None):
        self.root = Path(data_dir or settings.weave_data_dir)
        _api_key_ready()

    async def health(self) -> PipelineHealth:
        provider = settings.weave_llm_provider
        ok, why = w_llm.available(provider, CORE_DIR)
        info = {"provider": provider, "model": settings.weave_llm_model,
                "judge_k": settings.weave_judge_k, "judge_unit": settings.weave_judge_unit,
                "retrieval": settings.weave_retrieval}
        if provider not in PROVIDER_KEY_ENV:
            return PipelineHealth(ok=False, mode="weave", info=info,
                                  message=f"알 수 없는 WEAVE_LLM_PROVIDER: {provider}")
        if not ok:
            return PipelineHealth(ok=False, mode="weave", info=info,
                                  message="Weave 분석용 LLM 키가 없습니다 (WEAVE_API_KEY 설정 필요).")
        return PipelineHealth(ok=True, mode="weave", info=info,
                              message=f"Weave 엔진 준비됨 ({provider} / {settings.weave_llm_model})")

    # -- 1차 추출 ---------------------------------------------------------
    async def extract_chapter(self, chapter_text: str, canon: CanonSnapshot,
                              title: str | None = None, *,
                              chapter_no: int | None = None) -> ExtractionOutput:
        ep = chapter_no or (canon.up_to_chapter + 1)
        if (title or "").startswith("[규칙]"):
            # 설정 문서는 서사가 아니다 — LLM 없이 줄 단위로 세계관 규칙에 넣는다.
            from app.pipeline.fake import FakePipeline

            return ExtractionOutput(highlighted_text=chapter_text,
                                    canon_delta=FakePipeline._rules_canon_delta(chapter_text))
        # 앞 회차가 캐논에 넣은 관계 — 같은 쌍을 다시 내지 않는다(episode_to_delta)
        known = frozenset(r.get("entity_key") for r in canon.relations
                          if r.get("entity_key") and (r.get("source_chapter") or 0) < ep)
        return await _in_thread(self._extract_sync, chapter_text, canon, ep, known)

    def _extract_sync(self, text: str, canon: CanonSnapshot, ep: int,
                      known_relations: frozenset[str] = frozenset(),
                      cancel: threading.Event | None = None) -> ExtractionOutput:
        project_id = canon.project_id
        store = WeaveStore(project_id, self.root)
        cfg = weave_config()
        with store.lock(cancel):
            doc, reg, label_map, pending = extract_episode(text, ep, store, cfg, cancel)
            _check_cancel(cancel)  # 취소됐으면 뒤늦은 결과를 쓰지 않는다
            rev = time.strftime("%Y%m%dT%H%M%S") + "-" + hashlib.sha1(
                json.dumps(doc, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:8]
            doc["_rev"] = rev
            mapping = w_reg.mapping_of(reg) | label_map
            gate = self._grade(doc, canon, ep, store, mapping, rev, text=text, reg=reg)
            store.write_json(store.revision_path(ep, rev), doc)
            store.write_json(store.label_map_path, label_map)
            store.write_json(store.registry_path, reg)
            store.write_json(store.episode_path(ep), doc)
        logger.info("weave extract: project=%s ep=%s rev=%s events=%s chunks=%s pending=%s tentative=%s",
                    project_id, ep, rev, len(doc["events"]), doc["_chunks"], len(pending),
                    len(gate.tentative_event_ids) if gate else "-")
        return ExtractionOutput(highlighted_text=text, canon_delta=episode_to_delta(
            doc, reg, mapping, rev, known_relations, gate=gate))

    @staticmethod
    def _grade(doc: dict, canon: CanonSnapshot, ep: int, store: WeaveStore, mapping: dict,
               rev: str, *, text: str | None = None, reg: dict | None = None) -> w_gate.GateResult | None:
        """① 추출 관문: 확정 회차(검수와 같은 원장 — exclude 면 잠정분을 뺀 원장) + 새 회차를 재생해
        등급을 매기고 gates/ 에 저장한다. 관문이 실패해도 추출은 살린다(등급 없음 → trust 미기록).
        WEAVE_VERIFIER=annotate 면 저장 전에 검증기 표시 신호를 옆 키로 더한다(text·reg 는 그 입력, 등급 불변)."""
        try:
            confirmed_eps, _ = confirmed_episodes(canon, ep, store, mapping,
                                                  gate_mode=settings.weave_gate_mode)
            gate = w_gate.grade_episode(doc, confirmed_eps, mapping,
                                        knows_mode=settings.weave_knows_mode,
                                        unknown_from=settings.weave_gate_unknown_from)
        except Exception:  # noqa: BLE001 - 관문은 보조 신호다
            logger.exception("weave gate failed: project=%s ep=%s rev=%s", canon.project_id, ep, rev)
            return None
        gate_dict = gate.to_dict()
        if settings.weave_verifier == "annotate" and settings.weave_gate_mode != "off":
            gate_dict = _verify_into(gate_dict, doc, confirmed_eps, mapping, reg, text)
        store.write_json(store.gate_path(ep, rev), gate_dict | {
            "rev": rev, "confirmed_episodes": [d.get("episode") for d in confirmed_eps],
            "graded_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
        return gate

    # -- 2차 검수 ---------------------------------------------------------
    async def review_chapter(self, chapter_text: str, canon: CanonSnapshot, delta: CanonDelta,
                             title: str | None = None, *,
                             chapter_no: int | None = None) -> ReviewOutput:
        if (title or "").startswith("[규칙]"):
            return ReviewOutput(contradictions=[])
        ep = chapter_no or (canon.up_to_chapter + 1)
        return await _in_thread(self._review_sync, chapter_text, canon, delta, ep)

    def _review_sync(self, text: str, canon: CanonSnapshot, delta: CanonDelta, ep: int,
                     cancel: threading.Event | None = None) -> ReviewOutput:
        store = WeaveStore(canon.project_id, self.root)
        cfg = weave_config()
        k = int(cfg["judge"]["k"])
        # 이번 회차는 1차 결과(델타)가 가리키는 판본으로 — 그 뒤 재추출했어도 사용자가 본 것을 판정한다
        revs = {(e.attrs or {}).get("weave_rev") for e in delta.events} - {None}
        doc = store.load_revision(ep, sorted(revs)[-1]) if revs else None
        doc = doc or store.load_episode(ep)
        if doc is None:
            raise WeaveError(f"{ep}화의 Weave 추출 결과가 없습니다. 1차 추출을 다시 실행하세요.")
        reg = store.load_registry()
        mapping = store.mapping(reg)

        # 판정 대상은 델타에 남은 사건뿐이다 — 사용자가 1차 검토에서 지운 사건은 뺀다.
        # 델타가 통째로 비었으면 사용자가 항목을 전부 지운 것이다(1차 결과 행이 없으면 몸통의
        # start_review 가 409 로 막으므로 여기까지 오지 않는다). 이때 추출 사건 전부를 판정하면
        # 지운 사건으로 LLM 을 부르고 모순을 띄우게 되므로, 판정할 사건 없음으로 본다.
        kept = {e.entity_key for e in delta.events}
        events = [e for e in (doc.get("events") or []) if e.get("event_id") in kept]

        gate_mode = settings.weave_gate_mode
        gate_info: dict = {}
        confirmed_eps, notes = confirmed_episodes(canon, ep, store, mapping,
                                                  gate_mode=gate_mode, gate_info=gate_info)
        # 근거가 잠정 사실뿐인 모순에 '낮은 확신' 꼬리표를 붙일 때 쓸 집합(checks_to_contradictions 의
        # tentative_event_ids). off 면 비어 있다 — 이전 동작 그대로.
        tentative_event_ids: set[str] = set(gate_info.get("tentative_event_ids") or [])
        ledger, validator = w_pipe.replay_confirmed(
            [w_prejudge.f6_doc(d) for d in confirmed_eps] if settings.weave_prejudge_f6 else confirmed_eps,
            timelines=["main"], knows_mode=settings.weave_knows_mode)
        corpus = []
        for d in confirmed_eps:
            for e in sorted(d.get("events", []), key=lambda x: x.get("order", 0)):
                corpus.append(dict(e) | {"episode": d.get("episode"), "global_idx": len(corpus)})

        checks = run_checks_parallel(events, ledger, mapping, corpus, cfg, store.requests_dir,
                                     registry=judge_registry(reg, canon, delta),
                                     concurrency=settings.weave_judge_concurrency, cancel=cancel)
        _check_cancel(cancel)
        norm_events = [_norm(e, mapping) for e in events]
        contradictions = checks_to_contradictions(checks, norm_events, corpus, text, ep, k,
                                                 tentative_event_ids=frozenset(tentative_event_ids))

        runs = [r for it in checks["judge"] for r in it.get("runs", [])]
        failed = [r for r in runs if r.get("status") != "ok"]
        unanswered = [it["packet_key"] for it in checks["judge"]
                      if not any(r.get("verdict") for r in it.get("runs", []))]
        store.write_json(store.review_path(ep), {
            "episode": ep, "rev": doc.get("_rev"),
            "confirmed_episodes": [d.get("episode") for d in confirmed_eps], "notes": notes,
            "replay_warnings": validator.warnings, "config": checks["config"],
            "checker": checks["checker"], "judge": checks["judge"],
            "judge_failed_runs": len(failed), "judge_total_runs": len(runs),
            "unanswered_packets": unanswered,
        } | ({"gate": self._gate_section(store, ep, doc, gate_mode, gate_info)}
             if gate_mode != "off" else {}))
        if unanswered:
            # 판정을 한 번도 받지 못한 장면이 있으면 '모순 없음'으로 넘기지 않는다
            msgs = sorted({(r.get("message") or "")[:160] for r in failed if r.get("message")})
            why = checks["config"].get("stopped") or "; ".join(msgs)[:300]
            raise WeaveError(
                f"LLM 판정을 받지 못한 장면이 있습니다 ({len(unanswered)}/{len(checks['judge'])}개). "
                f"원인: {why} — 잠시 후 검수를 다시 실행하세요.")
        logger.info("weave review: project=%s ep=%s confirmed=%s checker=%s judge=%s -> %s "
                    "(failed runs %s/%s)%s", canon.project_id, ep, len(confirmed_eps),
                    len(checks["checker"]), len(checks["judge"]), len(contradictions),
                    len(failed), len(runs), f" notes={notes}" if notes else "")
        return ReviewOutput(contradictions=contradictions)

    @staticmethod
    def _gate_section(store: WeaveStore, ep: int, doc: dict, mode: str, info: dict) -> dict:
        """review_NN.json 의 gate 절 — 판정 원장에서 잠정으로 본(exclude 면 뺀) 것과 이번 회차의 등급."""
        cur = store.load_gate(ep, doc["_rev"]) if doc.get("_rev") else None
        bundle = w_vmerge.screen_bundle(cur)  # 검증기를 돌린 등급 파일만: high 후보 목록 · mid 건수 · low 없음
        return {"mode": mode, **info,
                "current_tentative_event_ids": (cur or {}).get("tentative_event_ids"),
                "current_gate_found": cur is not None} | ({"verifier": bundle} if bundle else {})
