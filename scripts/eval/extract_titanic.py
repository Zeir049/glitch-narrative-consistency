#!/usr/bin/env python
"""타이타닉 평가 코퍼스 추출 (M-3 D 단계) — 서비스 어댑터(app/pipeline/weave.py)의 회차 추출 경로 그대로.

  ledger   episodes_src/episode_01..04.txt → 1→4화 순서로 extract_episode(서비스가 회차를 추출할 때 쓰는 함수).
           레지스트리는 서비스처럼 빈 상태에서 시작해 조각마다 병합된다. 결과는 corpus/ledger_nemotron_ultra/.
  scenes   scenes/scene_NN.txt → 서비스와 같은 조각 분할(chunk_text, C=2500)을 하고, 같은 글자의 조각은 한 번만 추출해
           P/N 이 공유한다. 조각마다 원장 최종 레지스트리를 읽기 전용으로 쓴다(Weave G5 장면 추출과 같은 취지).
  plan     호출 없이 조각 수만 센다.
  status   누적 호출·실패·시간.

서비스 경로와 다른 점(README 에도 적는다):
  1. 장면 파일은 문단이 한 줄바꿈뿐이라 chunk_text(빈 줄 경계)가 통째로 한 조각으로 본다 → 줄마다 빈 줄을 넣은 사본으로 나눈다
     (episodes_src 와 같은 모양. 장면 파일 자체는 건드리지 않는다).
  2. 장면은 조각마다 같은 원장 레지스트리에서 시작한다(서비스는 화 안에서 조각 간 레지스트리를 누적). 같은 조각이 P/N 에서
     같은 프롬프트를 받아야 공유할 수 있기 때문이다. 새로 만들어진 엔티티 ID 는 NEW:라벨로 되돌린다(원장을 갱신하지 않는다).
  3. 조각별 사건을 장면으로 이을 때 서비스와 같은 규칙(EVENT_<화>_<n> 재부여, 조각이 둘 이상이면 scene_id 앞에 c<k>:)을 쓴다.
  4. 호출 래퍼: 녹화본에 있으면 API 를 부르지 않고(재개), 없으면 record 모드로 부른다. 429·한도 응답이면 즉시 멈춘다.
     모델명이 ':free' 로 끝나지 않으면 호출 전에 거부한다(유료 모델 호출 차단).

코어(weave_core/)와 어댑터는 고치지 않는다. 원문은 저장소 밖(--root)에만 있다.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

DEFAULT_ROOT = Path(r"C:\judge_models\M3")
DEFAULT_KEY_FILE = Path(r"%USERPROFILE%\Downloads\event_sourcing\.openrouter_key")
MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"
MAX_TOKENS = 16384          # Q1·Q3 원장(run_free_extract --max-tokens 기본)과 같은 값
CHUNK_CHARS = 2500
SCENE_EP = 5                # 장면은 '원장 다음 화' 번호로 추출(Weave: 5화 원장 → 6)
PROJECT = "titanic"
N_EPISODES = 4
LEDGER_DIRNAME = "ledger_nemotron_ultra"
WORK = "work"
DECL = ("worldview_rules", "characters", "locations", "objects", "times")


class StopExtraction(RuntimeError):
    """429·한도 응답 — 더 부르지 않고 멈춘다. 녹화본으로 이어 간다."""


# ---------------------------------------------------------------- 설정 / 불러오기

def configure(root: Path, key_file: Path | None = DEFAULT_KEY_FILE) -> None:
    """app.core.config 를 import 하기 전에 환경변수로 서비스 설정을 맞춘다(저장소 밖 경로 포함)."""
    if "app.core.config" in sys.modules:
        raise RuntimeError("app.core.config 가 이미 import 됐다 — configure() 는 그 전에 불러야 한다.")
    work = root / WORK
    env = {
        "WEAVE_LLM_PROVIDER": "openrouter", "WEAVE_LLM_MODEL": MODEL, "WEAVE_EXTRACT_MODEL": "",
        "WEAVE_MAX_TOKENS": str(MAX_TOKENS), "WEAVE_REASONING_OFF": "true",
        "WEAVE_CHUNK_CHARS": str(CHUNK_CHARS), "WEAVE_DATA_DIR": str(work / "weave_data"),
        "WEAVE_LLM_RECORD": "record", "WEAVE_LLM_RECORD_DIR": str(work / "weave_llm_record"),
    }
    for k, v in env.items():
        os.environ[k] = v
    if key_file is not None and not os.environ.get("OPENROUTER_API_KEY") and key_file.is_file():
        os.environ["OPENROUTER_API_KEY"] = key_file.read_text(encoding="utf-8").strip()   # 값은 출력하지 않는다


def load_weave():
    from app.pipeline import weave as W
    return W


# ---------------------------------------------------------------- 호출 래퍼

def is_stop_message(msg: str) -> bool:
    m = (msg or "").lower()
    return "429" in m or "rate limit" in m or "rate-limit" in m or "free-models-per-day" in m or "quota" in m


class CachedLLM:
    """w_llm.complete 자리에 끼우는 호출 래퍼. 녹화본 우선, 없으면 안쪽(record 모드 녹화기)을 부른다.

    같은 키를 같은 프로세스에서 두 번째로 부르면(JSON 파싱 실패 재요청) 녹화본의 두 번째 항목을 쓰고,
    없으면 실제로 부른다 — 녹화기 replay 의 커서와 같은 규칙.
    """

    def __init__(self, recorder, log_path: Path) -> None:  # noqa: ANN001
        self.recorder = recorder
        self.log_path = log_path
        self.tag = ""
        self._cursor: dict[str, int] = defaultdict(int)
        self.api_calls = 0
        self.failures = 0
        self.hits = 0
        self.seconds = 0.0
        self.tokens_in = 0
        self.tokens_out = 0
        self.stopped = ""
        self.demotions = 0
        self.keys: list[str] = []      # 호출된 녹화 키(role:sha) — 조각별 지도용

    @staticmethod
    def _parses(resp: dict) -> bool:
        try:
            load_weave().w_llm.extract_json(resp.get("text") or "")
            return True
        except Exception:  # noqa: BLE001
            return False

    def _log(self, row: dict) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "tag": self.tag, **row}
        with self.log_path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def __call__(self, system, user, llm, *, role="judge", base=None):  # noqa: ANN001
        model = str(llm.get(f"{role}_model") or llm.get("model") or "")
        if not model.endswith(":free"):
            raise RuntimeError(f"유료 가능 모델 호출 거부: {model!r} (':free' 만 허용)")
        from app.pipeline.llm_record import prompt_sha256
        sha = prompt_sha256(system, user)
        key = f"{role}:{sha}"
        self.keys.append(key)
        path = self.recorder.path_of(role, sha)
        idx = self._cursor[key]
        self._cursor[key] += 1
        if path.exists():
            entries = json.loads(path.read_text(encoding="utf-8")).get("entries") or []
            while idx < len(entries) and not self._parses(entries[idx]["response"]):
                # JSON 으로 못 읽는 녹화 응답은 재개 때 다시 쓰지 않는다(같은 실패를 되풀이하지 않게) — 새로 부른다
                self._log({"event": "skip_unparsable", "role": role, "sha": sha[:12], "idx": idx})
                idx += 1
                self._cursor[key] += 1
            if idx < len(entries):
                self.hits += 1
                self._log({"event": "hit", "role": role, "sha": sha[:12], "idx": idx})
                return copy.deepcopy(entries[idx]["response"])
        t0 = time.time()
        res = self.recorder(system, user, llm, role=role, base=base)
        el = round(time.time() - t0, 1)
        self.api_calls += 1
        self.seconds += el
        usage = (res.get("usage") or {}) if isinstance(res, dict) else {}
        self.tokens_in += int(usage.get("input") or 0)
        self.tokens_out += int(usage.get("output") or 0)
        ok = isinstance(res, dict) and res.get("status") == "ok"
        if not ok:
            self.failures += 1
        self._log({"event": "api", "role": role, "sha": sha[:12], "status": res.get("status"), "sec": el,
                   "attempts": res.get("attempts"), "usage": usage or None,
                   "finish": res.get("finish_reason"),
                   "message": (res.get("message") or "")[:200] if not ok else None})
        if not ok and is_stop_message(res.get("message", "")):
            self.stopped = res.get("message", "")[:200]
            raise StopExtraction(self.stopped)
        return res

    def summary(self) -> dict:
        return {"api_calls": self.api_calls, "failures": self.failures, "cache_hits": self.hits,
                "api_seconds": round(self.seconds, 1), "tokens_in": self.tokens_in, "tokens_out": self.tokens_out,
                "stopped": self.stopped or None, "demotions": self.demotions}


SPLITS_FILE = "chunk_splits.json"   # work/ — 끝까지 실패한 조각만 문단 경계에서 둘로 나눈다(C=2500 규칙의 예외, README 에 적는다)


def split_in_two(chunk: str) -> list[str]:
    """문단 경계에서 글자 수가 반반에 가장 가깝게 둘로 나눈다."""
    sep = chr(10) * 2
    paras = chunk.split(sep)
    if len(paras) < 2:
        raise RuntimeError("문단이 하나뿐이라 나눌 수 없다")
    acc, gaps = 0, []
    for i, p in enumerate(paras[:-1], 1):
        acc += len(p) + 2
        gaps.append((abs(acc - len(chunk) / 2), i))
    best = min(gaps)[1]
    return [sep.join(paras[:best]), sep.join(paras[best:])]


# ---------------------------------------------------------------- A1 인물 시드 (이름만 보고 정리)

# 초안 인물 이름 → 시드 항목. 같은 사람은 한 ID(이름·별칭만 근거). 직함·역할만 있는 일반어(여러 사람이 될 수 있는 칸)는 넣지 않는다.
SEED_MERGES: list[tuple[str, list[str]]] = [          # (대표 이름, 별칭 — 초안의 다른 표기)
    ("로즈", ["늙은 로즈", "젊은 로즈"]),
    ("브록 로벳", ["로벳", "브록"]),
    ("스미스 선장", ["스미스"]),
    ("그레이시 대령", ["그레이시"]),
    ("플리트", ["플릿"]),
]
SEED_DROP_GENERIC = ["카메라맨", "아나운서", "짐꾼", "남자", "여인", "여자", "신사", "웨이터", "선원", "스튜어드",
                     "펍 주인", "보안관", "예일대생", "치안 책임자"]


def build_seed_registry(draft: dict) -> tuple[dict, dict]:
    """초안(인물만 사용) → 시드 레지스트리 + 정리 기록. 장소·물건·시간은 넣지 않는다. 라벨·근거 지도는 보지 않는다."""
    names = [c["name"] for c in draft.get("characters", [])]
    merged_away = {a for _rep, als in SEED_MERGES for a in als}
    reps = {rep: als for rep, als in SEED_MERGES}
    out, log = [], {"merged": [], "dropped_generic": [], "renamed": []}
    for n in names:
        if n in SEED_DROP_GENERIC:
            log["dropped_generic"].append(n)
            continue
        if n in merged_away:
            continue
        als = list(reps.get(n, []))
        out.append((n, als))
    for rep, als in SEED_MERGES:                       # 대표 이름이 초안에 없는 경우(브록 로벳, 로즈 등)
        if rep not in names and any(a in names for a in als):
            out.append((rep, [a for a in als if a in names]))
            log["renamed"].append({"name": rep, "aliases": [a for a in als if a in names]})
        elif rep in names and reps[rep]:
            log["merged"].append({"name": rep, "aliases": list(reps[rep])})
    chars = [{"id": f"CHAR_{i:03d}", "name": n, "entity_type": "person", "first_episode": 1,
              **({"aliases": als} if als else {})} for i, (n, als) in enumerate(out, 1)]
    reg = {k: v for k, v in draft.items() if k not in ("characters", "locations", "objects", "times")}
    reg.update({"description": "타이타닉(M3) 추출 시드 — 초안의 인물 이름·별칭만(같은 사람은 한 ID). 장소·물건·시간 없음.",
                "characters": chars, "locations": [], "objects": [], "times": [], "last_episode": 0})
    reg.setdefault("worldview_rules", [])
    return reg, log


# ---------------------------------------------------------------- A2 ID–이름 충돌 가드 (추출 스크립트 래퍼에만)

ID_KEYS = {"characters": "character_id", "locations": "location_id", "objects": "object_id", "times": "time_id"}
ID_RE = re.compile(r"(CHAR|LOC|OBJ|TIME)_\d+$")


def _nm(s: str | None) -> str:
    return re.sub(r"[\s_()]+", "", str(s or ""))


def demote_conflicts(doc: dict, reg: dict, rewrite) -> list[dict]:  # noqa: ANN001
    """응답이 선언한 정식형 ID(CHAR_NNN 등)의 이름이 레지스트리의 이름·별칭과 겹치지 않거나 레지스트리에 없는 번호면
    `NEW:이름`으로 강등하고 문서 전체의 그 ID 를 치환한다. doc 를 제자리 수정. 반환: 강등 기록."""
    by_id = {e["id"]: e for coll in ID_KEYS for e in reg.get(coll, []) or [] if e.get("id")}
    table: dict[str, str] = {}
    log: list[dict] = []
    for coll, key in ID_KEYS.items():
        for d in doc.get(coll) or []:
            if not isinstance(d, dict):
                continue
            eid = str(d.get(key) or "")
            if not ID_RE.match(eid) or eid in table:
                continue
            name = str(d.get("name") or d.get("expression") or "")
            r = by_id.get(eid)
            known = [_nm(x) for x in ([r.get("name"), r.get("expression")] + list(r.get("aliases") or [])) if x] if r else []
            ok = bool(r) and bool(_nm(name)) and any(k in _nm(name) or _nm(name) in k for k in known)
            if not ok and name:
                table[eid] = "NEW:" + re.sub(r"\s+", "_", name.strip())
                log.append({"id": eid, "declared": name, "registry": r.get("name") if r else None, "to": table[eid]})
    if table:
        new = rewrite(doc, table)
        doc.clear()
        doc.update(new)
    return log


def install(root: Path):
    """CachedLLM 을 코어 complete 자리에 건다. 반환: (W, CachedLLM)."""
    W = load_weave()
    sp = root / WORK / SPLITS_FILE
    forced = set(json.loads(sp.read_text(encoding="utf-8"))) if sp.exists() else set()
    if forced:
        base_chunk = W.chunk_text

        def chunk_text(text, limit, *a, **k):  # noqa: ANN001, ANN202
            out = []
            for c in base_chunk(text, limit, *a, **k):
                out.extend(split_in_two(c) if sha16(c) in forced else [c])
            return out
        W.chunk_text = chunk_text
    W.forced_splits = forced
    cached = CachedLLM(W.llm_recorder, root / WORK / "extract_calls.jsonl")
    W.w_llm.complete = cached
    guard_log = root / WORK / "guard_log.jsonl"
    orig_extract = W.w_extract.extract

    def guarded(text, reg, ep, cfg, out_dir, *a, **k):  # noqa: ANN001, ANN202
        res = orig_extract(text, reg, ep, cfg, out_dir, *a, **k)
        if res.get("status") == "ok":
            dem = demote_conflicts(res["document"], reg, W._rewrite)
            cached.demotions += len(dem)
            for d in dem:
                with guard_log.open("a", encoding="utf-8", newline=chr(10)) as f:
                    f.write(json.dumps({"tag": cached.tag, **d}, ensure_ascii=False) + chr(10))
        return res
    W.w_extract.extract = guarded
    return W, cached


def add_totals(root: Path, cached: CachedLLM, label: str) -> None:
    """실행 한 번의 호출 수를 누계 파일에 더한다(재개해도 합산)."""
    p = root / WORK / "extract_totals.json"
    tot = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"runs": []}
    tot["runs"].append({"label": label, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), **cached.summary()})
    keys = ("api_calls", "failures", "cache_hits", "api_seconds", "tokens_in", "tokens_out", "demotions")
    tot["total"] = {k: round(sum(r.get(k, 0) for r in tot["runs"]), 1) for k in keys}
    p.write_text(json.dumps(tot, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------------- 조각 분할 (장면)

def scene_extract_text(raw: str) -> str:
    """장면 파일(문단=한 줄바꿈) → 빈 줄로 문단을 가른 추출 입력(episodes_src 와 같은 모양)."""
    return "\n\n".join(l for l in raw.strip().split("\n") if l.strip()) + "\n"


def sha16(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def plan_scene_chunks(scene_texts: dict[str, str], chunk_text) -> tuple[dict[str, list[str]], dict[str, str]]:  # noqa: ANN001
    """장면 → 조각 sha 목록, 조각 sha → 조각 글. 같은 글자의 조각은 한 번만 센다."""
    per_scene: dict[str, list[str]] = {}
    chunks: dict[str, str] = {}
    for sid in sorted(scene_texts):
        parts = chunk_text(scene_extract_text(scene_texts[sid]), CHUNK_CHARS)
        per_scene[sid] = []
        for c in parts:
            h = sha16(c)
            chunks.setdefault(h, c)
            per_scene[sid].append(h)
    return per_scene, chunks


# ---------------------------------------------------------------- 조각 → 장면 사건

def registry_ids(reg: dict) -> set[str]:
    return {e["id"] for coll in ("characters", "locations", "objects", "times", "worldview_rules")
            for e in reg.get(coll, []) or [] if e.get("id")}


def revert_new_ids(doc: dict, reg_after: dict, frozen_ids: set[str], rewrite) -> dict:  # noqa: ANN001
    """원장 레지스트리에 없던 새 엔티티 ID 를 NEW:라벨로 되돌린다(장면 추출은 원장을 갱신하지 않는다)."""
    table = {e["id"]: e["new_label"] for coll in ("characters", "locations", "objects", "times", "worldview_rules")
             for e in reg_after.get(coll, []) or []
             if e.get("id") and e["id"] not in frozen_ids and e.get("new_label")}
    return rewrite(doc, table) if table else doc


def extract_chunk(W, text: str, ledger_store, scratch_root: Path, cfg: dict) -> dict:  # noqa: ANN001
    """조각 하나를 서비스 추출 함수로 추출한다(원장 레지스트리 읽기 전용)."""
    if len(W.chunk_text(text, CHUNK_CHARS)) != (2 if sha16(text) in getattr(W, "forced_splits", ()) else 1):
        raise RuntimeError("조각이 다시 나뉜다 — 분할이 멱등이 아니다")
    scratch = W.WeaveStore(f"chunk_{sha16(text)}", scratch_root)
    scratch.dir.mkdir(parents=True, exist_ok=True)
    reg0 = ledger_store.load_registry()
    scratch.write_json(scratch.registry_path, reg0)
    scratch.write_json(scratch.label_map_path, ledger_store.load_label_map())
    frozen = registry_ids(reg0)
    with scratch.lock():
        doc, reg, _lm, _pending = W.extract_episode(text, SCENE_EP, scratch, cfg)
    return revert_new_ids(doc, reg, frozen, W._rewrite)


def assemble_scene(chunk_docs: list[dict], rewrite) -> dict:  # noqa: ANN001
    """조각별 사건 → 장면 문서. 서비스 extract_episode 와 같은 규칙(사건 ID 위치 순서 재부여, scene_id 앞 c<k>:)."""
    events: list[dict] = []
    merged: dict | None = None
    multi = len(chunk_docs) > 1
    for ci, d in enumerate(chunk_docs, 1):
        evs = [e for e in d.get("events", []) if isinstance(e, dict)]
        table = {e["event_id"]: f"EVENT_{SCENE_EP}_{len(events) + j + 1}"
                 for j, e in enumerate(evs) if e.get("event_id")}
        d2 = rewrite(copy.deepcopy(d) | {"events": evs}, table)
        for e in d2["events"]:
            if multi and e.get("scene_id"):
                e["scene_id"] = f"c{ci}:{e['scene_id']}"
        if merged is None:
            merged = {k: v for k, v in d2.items() if k != "events"}
            for coll in DECL:
                merged[coll] = []
        keymap = {"worldview_rules": "rule_id", "characters": "character_id", "locations": "location_id",
                  "objects": "object_id", "times": "time_id"}
        for coll, key in keymap.items():
            have = {x.get(key) for x in merged[coll]}
            for x in d2.get(coll) or []:
                if isinstance(x, dict) and x.get(key) not in have:
                    merged[coll].append(x)
                    have.add(x.get(key))
        for e in d2["events"]:
            e["order"] = len(events) + 1
            events.append(e)
    assert merged is not None
    merged["events"] = events
    merged["episode"] = SCENE_EP
    merged["_chunks"] = len(chunk_docs)
    return merged


# ---------------------------------------------------------------- 실행

def corpus_dir(root: Path) -> Path:
    return root / "titanic"


def write_json(p: Path, data) -> None:  # noqa: ANN001
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def run_ledger(root: Path) -> int:
    W, cached = install(root)
    cfg = W.weave_config()
    store = W.WeaveStore(PROJECT, Path(os.environ["WEAVE_DATA_DIR"]))
    out = corpus_dir(root) / LEDGER_DIRNAME
    out.mkdir(parents=True, exist_ok=True)
    if not store.registry_path.exists():
        seed = corpus_dir(root) / "entity_registry_titanic.json"
        store.dir.mkdir(parents=True, exist_ok=True)
        store.write_json(store.registry_path, json.loads(seed.read_text(encoding="utf-8")))   # A1: 인물 시드로 시작
    rc = 0
    for ep in range(1, N_EPISODES + 1):
        if (out / f"episode_{ep:02d}.json").exists():
            print(f"[ep{ep}] 이미 확정 — 건너뜀")
            continue
        text = (corpus_dir(root) / "episodes_src" / f"episode_{ep:02d}.txt").read_text(encoding="utf-8")
        done = False
        for attempt in (1, 2):
            cached.tag = f"ledger ep{ep}"
            t0 = time.time()
            try:
                with store.lock():
                    doc, reg, label_map, pending = W.extract_episode(text, ep, store, cfg)
                    store.write_json(store.label_map_path, label_map)
                    store.write_json(store.registry_path, reg)
                    store.write_json(store.episode_path(ep), doc)
            except StopExtraction as e:
                print(f"[ep{ep}] 중단(한도): {e}")
                rc = 3
                break
            except W.WeaveError as e:
                print(f"[ep{ep}] 시도 {attempt} 실패: {e}")
                continue
            write_json(out / f"episode_{ep:02d}.json", doc)
            write_json(out / "registry_live.json", reg)
            write_json(out / "label_map.json", label_map)
            print(f"[ep{ep}] ok {time.time() - t0:.0f}s 사건 {len(doc['events'])}건 조각 {doc['_chunks']} "
                  f"대기 라벨 {len(pending)} | {cached.summary()}", flush=True)
            done = True
            break
        if not done:
            rc = rc or 2
            break
    add_totals(root, cached, "ledger")
    print(json.dumps(cached.summary(), ensure_ascii=False))
    return rc


def load_scene_texts(root: Path) -> dict[str, str]:
    return {p.stem: p.read_text(encoding="utf-8") for p in sorted((corpus_dir(root) / "scenes").glob("scene_*.txt"))}


def run_scenes(root: Path, *, plan_only: bool = False) -> int:
    W, cached = install(root)
    cfg = W.weave_config()
    out = corpus_dir(root) / LEDGER_DIRNAME
    missing = [i for i in range(1, N_EPISODES + 1) if not (out / f"episode_{i:02d}.json").exists()]
    texts = load_scene_texts(root)
    per_scene, chunks = plan_scene_chunks(texts, W.chunk_text)
    total = sum(len(v) for v in per_scene.values())
    print(f"장면 {len(texts)}개 → 조각 {total}개, 글자 다른 조각 {len(chunks)}개 (공유로 {total - len(chunks)}회 절약)")
    if plan_only:
        return 0
    if missing:
        print(f"[중단] 원장 미완성: episode {missing}")
        return 2
    store = W.WeaveStore(PROJECT, Path(os.environ["WEAVE_DATA_DIR"]))
    scratch_root = root / WORK / "weave_scratch"
    cache_dir = root / WORK / "scene_chunks"
    rc, fails = 0, 0
    for n, (h, text) in enumerate(chunks.items(), 1):
        p = cache_dir / f"{h}.json"
        if p.exists():
            continue
        cached.tag = f"scene-chunk {h}"
        cached.keys = []
        t0 = time.time()
        try:
            doc = extract_chunk(W, text, store, scratch_root, cfg)
        except StopExtraction as e:
            print(f"[chunk {n}/{len(chunks)} {h}] 중단(한도): {e}")
            rc = 3
            break
        except W.WeaveError as e:
            fails += 1
            print(f"[chunk {n}/{len(chunks)} {h}] 실패: {e}", flush=True)
            continue
        write_json(p, doc)
        write_json(cache_dir / f"{h}.keys.json", sorted(set(cached.keys)))
        print(f"[chunk {n}/{len(chunks)} {h}] ok {time.time() - t0:.0f}s {len(text)}자 사건 {len(doc['events'])}건",
              flush=True)
    todo = [h for h in chunks if not (cache_dir / f"{h}.json").exists()]
    if todo and rc == 0:
        rc = 2
    if not todo:
        rep = {}
        for sid, hs in per_scene.items():
            docs = [json.loads((cache_dir / f"{h}.json").read_text(encoding="utf-8")) for h in hs]
            doc = assemble_scene(docs, W._rewrite)
            write_json(out / "scene_events" / f"{sid}_event.json", doc)
            rep[sid] = {"chunks": hs, "events": len(doc["events"])}
        write_chunk_map(root, per_scene, chunks, cache_dir)
        write_json(out / "scene_extract_report.json", {
            "conditions": f"서비스 어댑터 extract_episode · v2_3 · {MODEL} · reasoning off · C={CHUNK_CHARS} · "
                          f"max_tokens={MAX_TOKENS} · 조각 공유(글자 같은 조각 1회)",
            "scene_ep": SCENE_EP, "unique_chunks": len(chunks), "total_chunks": total, "scenes": rep})
        print(f"장면 {len(rep)}개 조립 완료")
    add_totals(root, cached, "scenes")
    print(json.dumps(cached.summary(), ensure_ascii=False))
    return rc


def build_chunk_map(per_scene: dict[str, list[str]], chunks: dict[str, str], validated: list[dict],
                    n_events: dict[str, int], keys: dict[str, list[str]], texts: dict[str, str]) -> dict:
    """장면별 조각 지도. 문자 범위는 scene_extract_text(빈 줄 사본) 기준 [start, end).
    shared = 같은 사건의 짝(P↔N) 장면에도 글자가 같은 조각이 있다. contains_edit = 수정 문장이 이 조각 안에 있다(공백 무시)."""
    info = {v["scene_id"]: v for v in validated}
    by_event: dict[str, list[str]] = defaultdict(list)
    for v in validated:
        by_event[v["event"]].append(v["scene_id"])
    nospace = lambda t: "".join(t.split())  # noqa: E731
    out = {}
    for sid, hs in per_scene.items():
        v = info.get(sid, {})
        mates = [m for m in by_event.get(v.get("event"), []) if m != sid]
        mate_chunks = {h for m in mates for h in per_scene.get(m, [])}
        full = scene_extract_text(texts[sid])
        pos, first, rows = 0, 0, []
        for ci, h in enumerate(hs, 1):
            start = full.find(chunks[h], pos)
            end = start + len(chunks[h])
            pos = end
            ids = [f"EVENT_{SCENE_EP}_{first + j + 1}" for j in range(n_events[h])]
            first += n_events[h]
            rows.append({"chunk": ci, "chunk_id": h, "char_range": [start, end], "record_keys": keys.get(h, []),
                         "shared": h in mate_chunks,
                         "contains_edit": bool(v) and nospace(v["replacement"]) in nospace(chunks[h]),
                         "event_ids": ids})
        out[sid] = {"event": v.get("event"), "kind": v.get("kind"), "mates": mates, "chunks": rows}
    return {"note": "scene_events 의 사건 id 는 서비스와 같은 규칙(EVENT_<화>_<n>, 조각 순서)으로 재부여됨. 조각이 둘 이상이면 scene_id 앞에 c<k>:",
            "scene_ep": SCENE_EP, "scenes": out}


def write_chunk_map(root: Path, per_scene, chunks, cache_dir: Path) -> None:  # noqa: ANN001
    val = json.loads((root / WORK / "seed_sheet.validated.json").read_text(encoding="utf-8"))["scenes"]
    n_events = {h: len([e for e in json.loads((cache_dir / f"{h}.json").read_text(encoding="utf-8")).get("events", [])
                        if isinstance(e, dict)]) for h in chunks}
    keys = {h: json.loads((cache_dir / f"{h}.keys.json").read_text(encoding="utf-8"))
            for h in chunks if (cache_dir / f"{h}.keys.json").exists()}
    write_json(root / WORK / "chunk_map.json",
               build_chunk_map(per_scene, chunks, val, n_events, keys, load_scene_texts(root)))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["ledger", "scenes", "plan", "status", "seed"])
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    ap.add_argument("--key-file", type=Path, default=DEFAULT_KEY_FILE)
    a = ap.parse_args(argv)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    if a.cmd == "status":
        p = a.root / WORK / "extract_totals.json"
        print(p.read_text(encoding="utf-8") if p.exists() else "{}")
        return 0
    if a.cmd == "seed":
        draft = a.root / "work" / "run1" / "entity_registry_titanic.draft.json"
        reg, log = build_seed_registry(json.loads(draft.read_text(encoding="utf-8")))
        write_json(corpus_dir(a.root) / "entity_registry_titanic.json", reg)
        write_json(a.root / WORK / "seed_cleanup.json", log)
        print(len(reg["characters"]), "인물 시드", json.dumps(log, ensure_ascii=False))
        return 0
    configure(a.root, a.key_file)
    if a.cmd == "ledger":
        return run_ledger(a.root)
    return run_scenes(a.root, plan_only=a.cmd == "plan")


if __name__ == "__main__":
    sys.exit(main())
