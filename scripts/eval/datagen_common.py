# -*- coding: utf-8 -*-
"""학습 데이터 생성 도구(label_pairs.py · counterfactual.py · extract_work.py) 공용 부품.

  · 작품 재생   replay_upto(work, k): 확정 원장 1..k-1화 재생 + k화 사건(정규화) + checker 항목.
                judge_eval.load_context 와 같은 방식(registry_live 매핑, timelines = judge_eval.timelines_of(work) — WM 은
                코어 기본 STATE_TIMELINES, 나머지 main — , knows accumulate)이고,
                원장·코퍼스·세계관 규칙을 k화 **이전**으로 자른 것만 다르다(미래 사실 누출 방지).
  · LLM 호출   call_json(): 어댑터가 쓰는 코어 경로(weave_core/weave_llm.complete_json)를 그대로 부른다.
                app.pipeline.weave 를 import 하면 그 전역 complete 가 녹화 래퍼(LLMRecorder)로 바뀌어 있으므로
                WEAVE_LLM_RECORD=record|replay 가 그대로 먹는다. 새 SDK·URL 없음.
  · 비용       Usage: 호출마다 입력·출력 토큰을 모아 단가표(PRICES, 1M 토큰당 USD)로 금액을 낸다.
"""
from __future__ import annotations

import json
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = Path(__file__).resolve().parent
for p in (str(ROOT), str(EVAL_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

import judge_eval as JE  # noqa: E402

WEAVE_SRC = Path("%USERPROFILE%/Downloads/event_sourcing")  # Weave 원본(읽기 전용)
SOURCES: dict[str, Path] = {
    "wm": WEAVE_SRC / "WM_1-5.txt",  # 한 파일 — '- N화 : ... -' 머리줄로 나눈다

    "q1": WEAVE_SRC / "corpus_q1_geumhotang" / "sources",
    "q3": WEAVE_SRC / "corpus_q3_domec4" / "sources",
    "sherlock": WEAVE_SRC / "corpus_sherlock_adventures" / "sources",
    "dracula": WEAVE_SRC / "dracula" / "sources",
}
# dracula 는 확정 원장이 없다 — extract_work.py 가 만든 폴더(episodes/ + registry.json)를 쓴다.
DRACULA_LEDGER = ROOT / "build" / "dracula_ledger"
ALL_DATA_WORKS = ("wm", "q1", "q3", "sherlock", "dracula")

# 1M 토큰당 USD (입력, 출력). 모르는 모델은 None → --price-in/--price-out 으로 준다.
# openrouter ':free' 열은 0. 아래 Claude 값은 Anthropic 공시 단가(참고용).
PRICES: dict[str, tuple[float, float]] = {
    "claude-sonnet-4-5": (3.0, 15.0),
    "anthropic/claude-sonnet-4.5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "anthropic/claude-haiku-4.5": (1.0, 5.0),
    "claude-opus-4-5": (5.0, 25.0),
    "anthropic/claude-opus-4.5": (5.0, 25.0),
}


def price_of(model: str, override: tuple[float | None, float | None] = (None, None)) -> tuple[float, float] | None:
    if override[0] is not None and override[1] is not None:
        return float(override[0]), float(override[1])
    if model.endswith(":free"):
        return 0.0, 0.0
    return PRICES.get(model)


def load_source(work: str, ep: int, sources: Path | None = None) -> str:
    """회차 원문. 폴더면 episode_NN.txt, 파일이면 '- N화' 머리줄로 나눈 N번째 토막."""
    import re
    src = sources or SOURCES[work]
    if src.is_dir():
        return (src / f"episode_{ep:02d}.txt").read_text(encoding="utf-8")
    text = src.read_text(encoding="utf-8")
    heads = [(int(m.group(1)), m.start()) for m in re.finditer(r"^\s*-\s*(\d+)화", text, re.M)]
    for i, (n, start) in enumerate(heads):
        if n == ep:
            end = heads[i + 1][1] if i + 1 < len(heads) else len(text)
            return text[start:end]
    raise FileNotFoundError(f"{src}: {ep}화 머리줄을 찾지 못했습니다")


# ---------------------------------------------------------------- 작품 재생

@dataclass
class ReplayContext:
    work: str
    k: int                       # 판정 대상 회차
    ledger: Any                  # 1..k-1화 재생 원장
    mapping: dict
    corpus: list[dict]           # 1..k-1화 사건(global_idx)
    registry: dict               # 세계관 규칙은 first_episode < k 만
    events: list[dict]           # k화 사건(정규화)
    checker_items: list[dict] = field(default_factory=list)


def work_spec(work: str, ledger_dir: Path | None = None, registry: Path | None = None) -> tuple[Path, Path]:
    """(원장 폴더, 레지스트리 파일). dracula 는 extract_work.py 산출물."""
    if ledger_dir is not None:
        return ledger_dir, registry or (ledger_dir.parent / "registry.json")
    if work == "dracula":
        return DRACULA_LEDGER / "episodes", DRACULA_LEDGER / "registry.json"
    spec = JE.WORKS[work]
    return spec.ledger_dir, spec.registry


def load_episodes(work: str, ledger_dir: Path | None = None, registry_path: Path | None = None,
                  upto: int = 5) -> tuple[list[dict], dict, dict]:
    """(정규화된 1..upto화, 레지스트리, 매핑)."""
    _w_checker, _w_pipe, _compile, normalize = JE._core()
    ldir, rpath = work_spec(work, ledger_dir, registry_path)
    if not rpath.exists() or not (ldir / "episode_01.json").exists():
        raise SystemExit(f"{work}: 확정 원장이 없습니다 ({ldir}). dracula 면 scripts/eval/extract_work.py 를 먼저 "
                         "(승인 뒤) 실행하세요 — README 참고.")
    reg = json.loads(rpath.read_text(encoding="utf-8"))
    mapping = {e["new_label"]: e["id"]
               for c in ("characters", "locations", "objects", "times", "worldview_rules")
               for e in reg.get(c, []) or [] if isinstance(e, dict) and e.get("new_label")}
    label_map = rpath.parent / "label_map.json"  # extract_work.py(WeaveStore) 산출물
    if label_map.exists():
        mapping |= {k: v for k, v in json.loads(label_map.read_text(encoding="utf-8")).items()
                    if isinstance(v, str)}
    eps = []
    for i in range(1, upto + 1):
        path = ldir / f"episode_{i:02d}.json"
        if path.exists():
            eps.append(normalize(json.loads(path.read_text(encoding="utf-8")), mapping))
    return eps, reg, mapping


def _rules_before(reg: dict, episodes: list[dict], k: int) -> list[dict]:
    """k화 이전에 나온 세계관 규칙만. 레지스트리에 규칙이 있으면 first_episode 로, 없으면 화 문서에서 모은다."""
    given = [r for r in reg.get("worldview_rules") or [] if isinstance(r, dict)]
    if given:
        return [r for r in given if int(r.get("first_episode") or 1) < k]
    # 본문 정규화 기준 중복 제거·겹치는 ID 는 RULE_007@ep4 — judge_eval.load_context 와 같은 조립.
    numbered = [(int(ep.get("episode") or 0), ep) for ep in episodes]
    return JE.collect_episode_rules([(n, ep) for n, ep in numbered if n < k])


def build_corpus(episodes: list[dict]) -> list[dict]:
    corpus = []
    for ep in episodes:
        for ev in sorted(ep.get("events") or [], key=lambda e: e.get("order", 0)):
            corpus.append(dict(ev) | {"episode": ep.get("episode"), "global_idx": len(corpus)})
    return corpus


def replay_upto(work: str, k: int, *, knows_mode: str = "accumulate", ledger_dir: Path | None = None,
                registry_path: Path | None = None) -> ReplayContext:
    """'1..k-1화 원장 + k화 사건'. checker 항목은 export_pairs.checker_items 와 같은 모양."""
    w_checker, w_pipe, compile_ledger, _normalize = JE._core()
    eps, reg, mapping = load_episodes(work, ledger_dir, registry_path)
    before = [e for e in eps if int(e.get("episode") or 0) < k]
    target = [e for e in eps if int(e.get("episode") or 0) == k]
    if not before or not target:
        raise ValueError(f"{work}: {k}화 재생에 필요한 회차가 없습니다")
    registry = dict(reg)
    registry["worldview_rules"] = _rules_before(reg, eps, k)
    ledger = compile_ledger(episodes=before, timelines=JE.timelines_of(work), knows_mode=knows_mode)
    corpus = build_corpus(before)
    events = [dict(e) | {"episode": k} for e in sorted(target[0].get("events") or [], key=lambda e: e.get("order", 0))]
    items = []
    for e in events:
        for f in w_checker.check_event(e, ledger, mapping):
            if f.get("severity") == w_pipe.SEV_INFO and not f.get("needs_judge"):
                continue
            items.append({"kind": "checker", "event_id": e.get("event_id"), "scene_id": e.get("scene_id"),
                          "severity": f.get("severity"), "type": f.get("type"), "entity": f.get("entity"),
                          "field": f.get("field"), "premise": f.get("premise"),
                          "ledger_value": f.get("ledger_value"), "since": f.get("since"),
                          "detail": f.get("detail"), "needs_judge": bool(f.get("needs_judge"))})
    return ReplayContext(work, k, ledger, mapping, corpus, registry, events, items)


# ---------------------------------------------------------------- LLM

@dataclass
class Usage:
    """호출 집계. 녹화 재생(replay)도 녹화 당시 usage 를 그대로 센다 — 'recorded' 로 따로 표시."""
    calls: int = 0
    ok: int = 0
    failed: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    missing_usage: int = 0
    by_model: dict[str, dict[str, int]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    reused: int = 0               # 녹화본 재사용(네트워크 0회) — 토큰은 녹화 당시 값으로 센다

    def add(self, model: str, res: dict, *, reused: bool = False) -> None:
        u = res.get("usage") or {}
        i, o = u.get("input"), u.get("output")
        with self._lock:
            self.calls += 1
            self.reused += int(reused)
            if res.get("status") == "ok":
                self.ok += 1
            else:
                self.failed += 1
            m = self.by_model.setdefault(model, {"calls": 0, "input": 0, "output": 0})
            m["calls"] += 1
            if i is None and o is None:
                self.missing_usage += 1
            self.input_tokens += int(i or 0)
            self.output_tokens += int(o or 0)
            m["input"] += int(i or 0)
            m["output"] += int(o or 0)

    def cost(self, override: tuple[float | None, float | None] = (None, None)) -> dict:
        total, unknown = 0.0, []
        for model, m in self.by_model.items():
            p = price_of(model, override)
            if p is None:
                unknown.append(model)
                continue
            total += m["input"] / 1e6 * p[0] + m["output"] / 1e6 * p[1]
        return {"usd": round(total, 4), "unpriced_models": unknown}

    def to_dict(self, override: tuple[float | None, float | None] = (None, None)) -> dict:
        return {"calls": self.calls, "reused_recordings": self.reused, "ok": self.ok, "failed": self.failed,
                "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "missing_usage": self.missing_usage, "by_model": self.by_model, "cost": self.cost(override)}


def default_model() -> str:
    from app.core.config import settings
    return settings.weave_judge_model or settings.weave_llm_model


def llm_for(model: str, *, max_tokens: int | None = None, temperature: float | None = None) -> dict:
    """어댑터 판정 설정(weave._judge_llm)에서 모델·출력 상한만 바꾼다."""
    from app.pipeline import weave as W
    W._api_key_ready()
    llm = W._judge_llm(W.weave_config())
    llm["model"] = model
    llm["judge_model"] = None
    llm["extract_model"] = None
    if max_tokens is not None:
        llm["max_tokens"] = max_tokens
    if temperature is not None:
        llm["temperature"] = temperature
    return llm


def recorded_model(llm: dict, role: str) -> str | None:
    """녹화 항목의 `model` 과 같은 식(llm_record.LLMRecorder._record)."""
    return llm.get(f"{role}_model") or llm.get("model")


def recorded_response(role: str, system: str, user: str, occurrence: int = 0,
                      model: str | None = None) -> dict | None:
    """녹화본(LLMRecorder 파일 배치)에 이 프롬프트의 occurrence 번째 응답이 있으면 그것. 없으면 None.

    녹화 키(role + sha256(프롬프트))에는 모델이 없다. model 을 주면 그 모델로 녹화된 항목만 센다 — 다른 모델의
    녹화는 miss 로 보고 새로 부른다(새 응답은 같은 파일에 덧붙고, 다음 실행에서는 그것이 재사용된다)."""
    from app.pipeline import weave as W
    from app.pipeline.llm_record import prompt_sha256
    path = W.llm_recorder.path_of(role, prompt_sha256(system, user))
    if not path.exists():
        return None
    entries = json.loads(path.read_text(encoding="utf-8")).get("entries") or []
    if model is not None:
        entries = [e for e in entries if e.get("model") == model]
    return dict(entries[occurrence]["response"]) if occurrence < len(entries) else None


def call_json(system: str, user: str, llm: dict, *, role: str, usage: Usage | None = None,
              occurrence: int = 0) -> dict:
    """weave_llm.complete_json(녹화 래퍼 경유). 반환은 코어 그대로 + usage 집계.

    WEAVE_LLM_RECORD=record 일 때 이미 녹화된 응답(같은 프롬프트·같은 모델의 occurrence 번째)이 있으면 다시 부르지 않는다
    — 한도 초과 등으로 끊긴 실행을 이어 돌려도 빠진 호출만 나간다. 재사용분은 usage 의 recorded 로 따로 센다.
    """
    from app.core.config import settings
    from app.pipeline import weave as W
    if settings.weave_llm_record == "record":
        hit = recorded_response(role, system, user, occurrence, model=recorded_model(llm, role))
        if hit is not None and hit.get("status") == "ok":
            try:
                res = hit | {"data": W.w_llm.extract_json(hit.get("text", "")), "raw": hit.get("text", ""),
                             "reused_recording": True}
            except ValueError:
                res = None
            if res is not None:
                if usage is not None:
                    usage.add(str(llm.get("model")), res, reused=True)
                return res
    res = W.w_llm.complete_json(system, user, llm, role=role, base=W.CORE_DIR)
    if usage is not None:
        usage.add(str(llm.get("model")), res)
    return res


def chars_to_tokens(n_chars: int, chars_per_token: float) -> int:
    return int(round(n_chars / max(chars_per_token, 0.1)))
