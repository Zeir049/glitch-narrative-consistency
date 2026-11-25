# -*- coding: utf-8 -*-
"""judge 층 평가 하네스 — Weave 종단 측정 채점기를 이 저장소로 옮기고 판정기를 바꿔 끼울 수 있게 했다.

출처(event_sourcing 9180c3a, 채점 규칙 무변경):
  score_longitudinal.py  합산·작품별·유형별·집필주체별, 작품 하나 빼기(LOWO), §2-7 미탐 귀속, 재실행 재현율,
                         가정 유병률 조건부 정밀도
  score_b_track.py       load_raw(status=ok 줄만), majority_preds(다수결 2/3), metrics
  score_phase2.py        wilson(95%), POSITIVE_VERDICTS, scene_of
  score_track1.py        per_run_scene_pred(WM: 사건 → 장면 접기, 회차별 OR)
더한 것: 판정기 교체(recorded | learned | llm), 담당 층(expected_layer)별 칸, 대응 bootstrap(recall 차이 95% CI).

판정 규칙(원본 그대로):
  - 양성 verdict = {모순, 설정누수, 지식범위위반}. 그 밖(정상 등)은 음성. status != ok 인 회차는 버린다.
  - 장면 예측: 회차마다 그 장면 사건 중 하나라도 양성이면 그 회차 양성(WM 접기). Q1·Q3·셜록은 event_id = 장면.
  - 다수결: 양성 회차 수 >= majority (동결 열 k=3, majority=2). learned 는 k=1, majority=1.
  - 미완료(회차 수 < k) 장면이 있으면 집계를 거부한다(--allow-partial 로 확인만).

모드:
  recorded  동결 열 원시 판정 결과(tests/eval/weave_judge_corpus/baseline/nemotron_ultra_long_*)를 재생.
            작품 WM·Q1·Q3(셜록은 동결 열에 없음). 합산 Recall 18/60 · 오탐 3/60 재현이 인수 조건
            (tests/eval/test_judge_baseline.py).
  learned   app.pipeline.learned_judge 가 있으면 judge_from_settings() 로 판정(WEAVE_JUDGE_BACKEND 등은 그 모듈 설정),
            없으면 항상 '정상'을 내는 stub. 원장은 코퍼스의 확정 원장 5화를 재생(knows_mode 기본 accumulate),
            checker 항목(needs_judge 포함)을 함께 넘긴다. LLM 호출 0회.
  llm       어댑터 경로(app.pipeline.weave.run_checks_parallel, k=WEAVE_JUDGE_K)로 실제 LLM 판정. API 키 필요.
            WEAVE_LLM_RECORD=record|replay 와 같이 쓰면 녹화·재생된다.

대응 bootstrap: 같은 양성 장면 집합을 복원 추출해 두 판정기의 recall 차이(B − A)를 다시 계산한다.
  작품별 층화(작품마다 그 작품의 양성 장면 수만큼 뽑는다), 기본 10,000회, 시드 고정 → 같은 입력이면 같은 구간.
  구간은 백분위(2.5%, 97.5%).

실행:
  PYTHONIOENCODING=utf-8 python scripts/eval/judge_eval.py --mode recorded
  PYTHONIOENCODING=utf-8 python scripts/eval/judge_eval.py --mode learned --compare-recorded
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
import tempfile
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CORPUS_DIR = ROOT / "tests" / "eval" / "weave_judge_corpus"
WM_DIR = ROOT / "tests" / "eval" / "weave_checker_corpus"
BASELINE_DIR = CORPUS_DIR / "baseline"
WEAVE_COMMIT = "9180c3a"

POSITIVE_VERDICTS = {"모순", "설정누수", "지식범위위반"}
TYPES = ("상태", "설정", "지식")
FROZEN_TAG = "nemotron_ultra"
FROZEN_WORKS = ("wm", "q1", "q3")
ALL_WORKS = ("wm", "q1", "q3", "sherlock")
FROZEN_COLUMN = ("판정 openrouter/nvidia/nemotron-3-ultra-550b-a55b:free · reasoning off · k=3 다수결 2/3 · "
                 "full 조건 · 원장: WM=Claude 추출본(v4c 패킷), Q1·Q3=nemotron-3-ultra C=2500")
WM_AUTHOR = "llm:claude (WM 소급)"


@dataclass(frozen=True)
class WorkSpec:
    name: str
    fold_events: bool          # WM: 사건 ID(EVENT_Sxx_n) → 장면으로 접는다
    labels: Path
    evidence_map: Path | None
    ledger_dir: Path           # episode_01..05.json
    registry: Path             # 판정·재생에 쓰는 레지스트리
    scene_events: Path         # scene_NN_event.json 폴더
    # 원장 timeline 칸. None = 코어 기본(ledger.STATE_TIMELINES: real_present·future_world·unknown).
    # WM 코퍼스는 Weave 원본의 다중 timeline 으로 추출돼 장면 사건이 'future_world' 다 — checker.check_event 는
    # 사건 timeline 이름 그대로 원장 칸을 찾으므로 ["main"] 으로 재생하면 WM checker 항목이 9개로 줄어든다(31개가 맞다).
    # Q1·Q3·셜록은 서비스 추출본(timeline 'main')이라 ["main"]. (docs/learned-judge-branch-review-G.md §4a)
    timelines: tuple[str, ...] | None = ("main",)


WORKS: dict[str, WorkSpec] = {
    "wm": WorkSpec("wm", True, WM_DIR / "labels.json", None, WM_DIR, WM_DIR / "entity_registry.json", WM_DIR,
                   timelines=None),
    "q1": WorkSpec("q1", False, CORPUS_DIR / "q1" / "labels.json", CORPUS_DIR / "q1" / "evidence_event_map.json",
                   CORPUS_DIR / "q1" / "ledger_nemotron_ultra",
                   CORPUS_DIR / "q1" / "ledger_nemotron_ultra" / "registry_live.json",
                   CORPUS_DIR / "q1" / "ledger_nemotron_ultra" / "scene_events"),
    "q3": WorkSpec("q3", False, CORPUS_DIR / "q3" / "labels.json", CORPUS_DIR / "q3" / "evidence_event_map.json",
                   CORPUS_DIR / "q3" / "ledger_nemotron_ultra",
                   CORPUS_DIR / "q3" / "ledger_nemotron_ultra" / "registry_live.json",
                   CORPUS_DIR / "q3" / "ledger_nemotron_ultra" / "scene_events"),
    "sherlock": WorkSpec("sherlock", False, CORPUS_DIR / "sherlock" / "labels.json",
                         CORPUS_DIR / "sherlock" / "evidence_event_map.json",
                         CORPUS_DIR / "sherlock" / "ledger_v2_3",
                         CORPUS_DIR / "sherlock" / "ledger_v2_3" / "registry_live.json",
                         CORPUS_DIR / "sherlock" / "ledger_v2_3" / "scene_events"),
}


def register_corpus(name: str, corpus_dir: Path, *, ledger: str = "ledger_nemotron_ultra") -> WorkSpec:
    """저장소 밖 코퍼스(셜록 폴더 구조: labels.json·evidence_event_map.json·<ledger>/…)를 작품으로 등록한다.

    M-3 타이타닉처럼 원문 저작권 때문에 저장소에 둘 수 없는 평가 코퍼스용(`--corpus-dir`). 이미 있는 이름은 덮어쓴다.
    """
    d = Path(corpus_dir)
    spec = WorkSpec(name, False, d / "labels.json", d / "evidence_event_map.json", d / ledger,
                    d / ledger / "registry_live.json", d / ledger / "scene_events")
    WORKS[name] = spec
    return spec


def episode_count(spec: WorkSpec) -> int:
    """원장 폴더의 화 수(episode_NN.json). Q1·Q3·셜록은 5, 타이타닉은 4."""
    return len(list(spec.ledger_dir.glob("episode_[0-9][0-9].json")))


# ================================================================ 통계

def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson 95% 신뢰구간. n=0이면 (0,1). (score_phase2.wilson 그대로)"""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def cond_precision(recall: float | None, fpr: float | None, p: float) -> float | None:
    if recall is None or fpr is None:
        return None
    den = recall * p + fpr * (1 - p)
    return round(recall * p / den, 3) if den else None


def paired_bootstrap_recall_diff(pred_a: dict[str, bool], pred_b: dict[str, bool],
                                 positives: Iterable[str], *, strata: dict[str, str] | None = None,
                                 n_boot: int = 10_000, seed: int = 20261106,
                                 alpha: float = 0.05) -> dict:
    """두 판정기(A=기준, B=비교)의 recall 차이 B − A 의 대응 bootstrap 신뢰구간.

    positives: 모순 장면 키(예: "q1:scene_03"). 두 예측 모두에 있어야 한다.
    strata: 키 → 층(작품). 주면 층마다 그 층 크기만큼 복원 추출(작품 구성 보존). 없으면 전체에서.
    같은 표본의 같은 장면을 두 판정기 모두에 쓰므로 '대응'이다.
    """
    pos = sorted(positives)
    missing = [s for s in pos if s not in pred_a or s not in pred_b]
    if missing:
        raise ValueError(f"두 예측 모두에 있어야 하는 양성 장면이 빠졌다: {missing[:5]}")
    if not pos:
        raise ValueError("양성 장면이 없다")
    a = [1 if pred_a[s] else 0 for s in pos]
    b = [1 if pred_b[s] else 0 for s in pos]
    groups: dict[str, list[int]] = defaultdict(list)
    for i, s in enumerate(pos):
        groups[(strata or {}).get(s, "_all")].append(i)
    order = sorted(groups)
    rng = random.Random(seed)
    n = len(pos)
    diffs = []
    for _ in range(n_boot):
        da = db = 0
        for g in order:
            idx = groups[g]
            for _j in range(len(idx)):
                i = idx[rng.randrange(len(idx))]
                da += a[i]
                db += b[i]
        diffs.append((db - da) / n)
    diffs.sort()
    lo = diffs[int(math.floor(alpha / 2 * n_boot))]
    hi = diffs[min(n_boot - 1, int(math.ceil((1 - alpha / 2) * n_boot)) - 1)]
    return {"n_pos": n, "recall_a": sum(a) / n, "recall_b": sum(b) / n,
            "diff": (sum(b) - sum(a)) / n, "ci": (lo, hi),
            "b_only": sum(1 for x, y in zip(a, b) if y and not x),
            "a_only": sum(1 for x, y in zip(a, b) if x and not y),
            "n_boot": n_boot, "seed": seed, "stratified": strata is not None}


# ================================================================ 라벨·원시 결과

def scene_of(event_id: str) -> str:
    """EVENT_S02_1 → scene_02 (score_phase2.scene_of)"""
    core = event_id.split("_")[1]
    return f"scene_{int(core[1:]):02d}"


def load_gold(work: str) -> dict[str, dict]:
    spec = WORKS[work]
    data = json.loads(spec.labels.read_text(encoding="utf-8"))["labels"]
    labels = {l["scene_id"]: l for l in data} if isinstance(data, list) else data
    return {s: {"pos": bool(l["has_contradiction"]), "type": l.get("type"),
                "difficulty": l.get("difficulty"), "layer": l.get("expected_layer"),
                "author": l.get("authored_by") or (WM_AUTHOR if work == "wm" else None)}
            for s, l in labels.items()}


def load_raw(path: Path) -> dict[str, list[dict]]:
    """main.jsonl → {event_id: [회차 dict, ...]} (status=ok 만, 파일 순서). score_b_track.load_raw"""
    results: dict[str, list[dict]] = defaultdict(list)
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("status") != "ok":
            continue
        results[r["event_id"]].append({
            "run_index": r["run_index"], "verdict": r.get("verdict"),
            "confidence": float(r.get("confidence") or 0.0),
            "evidence_event_ids": r.get("evidence_event_ids") or []})
    return dict(results)


def scene_votes(results: dict[str, list[dict]], fold_events: bool) -> dict[str, list[bool]]:
    """{scene_id: [회차별 양성 여부]}.
    fold_events: 회차 번호마다 그 장면 사건들의 OR (score_track1.per_run_scene_pred).
    아니면 event_id 가 곧 장면, ok 회차마다 1표 (score_b_track.majority_preds)."""
    if fold_events:
        by: dict[str, dict[int, bool]] = defaultdict(dict)
        for eid, runs in results.items():
            sid = scene_of(eid)
            for r in runs:
                pos = r["verdict"] in POSITIVE_VERDICTS
                by[sid][r["run_index"]] = by[sid].get(r["run_index"], False) or pos
        return {sid: [v[i] for i in sorted(v)] for sid, v in by.items()}
    return {sid: [r["verdict"] in POSITIVE_VERDICTS for r in runs] for sid, runs in results.items()}


def majority_preds(votes: dict[str, list[bool]], majority: int) -> dict[str, tuple[int, int, bool]]:
    return {sid: (sum(v), len(v), sum(v) >= majority) for sid, v in votes.items()}


def metrics(preds: dict[str, tuple[int, int, bool]], gold_pos: dict[str, bool]) -> dict:
    pos = [s for s in preds if gold_pos[s]]
    neg = [s for s in preds if not gold_pos[s]]
    tp = sum(1 for s in pos if preds[s][2])
    fp = sum(1 for s in neg if preds[s][2])
    rec = tp / len(pos) if pos else None
    fpr = fp / len(neg) if neg else None
    return {"n_pos": len(pos), "n_neg": len(neg), "TP": tp, "FP": fp,
            "recall": rec, "recall_ci": wilson(tp, len(pos)) if pos else None,
            "fpr": fpr, "fpr_ci": wilson(fp, len(neg)) if neg else None,
            "d": (rec - fpr) if rec is not None and fpr is not None else None}


def _recall_cell(keys: list[str], preds: dict) -> dict:
    tp = sum(1 for k in keys if preds[k][2])
    return {"n": len(keys), "TP": tp, "recall": round(tp / len(keys), 3) if keys else None,
            "ci": wilson(tp, len(keys)) if keys else None}


# ================================================================ 판정기

class Judge(Protocol):
    """작품 하나의 장면별 회차 표를 낸다. k·majority 는 다수결 규칙."""
    name: str
    k: int
    majority: int
    works: tuple[str, ...]

    def votes(self, work: str) -> dict[str, list[bool]]: ...


class RecordedJudge:
    """동결 열 원시 판정 결과 재생 (LLM 호출 없음)."""

    def __init__(self, tag: str = FROZEN_TAG, *, rerun: bool = False, base: Path = BASELINE_DIR):
        self.tag, self.rerun, self.base = tag, rerun, base
        self.name = f"recorded:{tag}{'_rerun' if rerun else ''}"
        self.k, self.majority = 3, 2
        self.works = tuple(w for w in ALL_WORKS if self.path(w).exists())

    def path(self, work: str) -> Path:
        return self.base / f"{self.tag}_long_{work}{'_rerun' if self.rerun else ''}" / "main.jsonl"

    def votes(self, work: str) -> dict[str, list[bool]]:
        p = self.path(work)
        if not p.exists():
            return {}
        return scene_votes(load_raw(p), WORKS[work].fold_events)


@dataclass
class WorkContext:
    """작품 하나의 판정 입력 — 확정 원장 5화 재생 + 장면별 사건(정규화) + checker 항목."""
    work: str
    ledger: Any
    mapping: dict
    corpus: list[dict]
    registry: dict
    scenes: dict[str, list[dict]] = field(default_factory=dict)       # scene_id → 정규화된 사건
    checker_items: dict[str, list[dict]] = field(default_factory=dict)  # scene_id → run_checks 모양 항목


def _core():
    import app.pipeline.weave  # noqa: F401 - weave_core 경로를 연다(코어 파일 무수정)
    import checker as w_checker
    import weave_pipeline as w_pipe
    from ledger import compile_ledger, normalize
    return w_checker, w_pipe, compile_ledger, normalize


def timelines_of(work: str) -> list[str] | None:
    """작품 재생에 쓰는 compile_ledger(timelines=...) 값. WORKS 에 없는 작품(dracula 등 서비스 추출본)은 ["main"].

    재생하는 곳(load_context · export_pairs.base_replay/export_inject · datagen_common.replay_upto ·
    counterfactual.fact_candidates)은 모두 이 함수를 쓴다 — 학습 데이터와 평가가 같은 원장 칸에서 돈다."""
    spec = WORKS.get(work)
    if spec is None:
        return ["main"]
    return None if spec.timelines is None else list(spec.timelines)


def collect_episode_rules(episodes: list[tuple[int, dict]]) -> list[dict]:
    """화 문서들의 worldview_rules 를 판정 패킷 (b)절용 규칙 목록으로 모은다.

    episodes: (화 번호, normalize 된 화 문서) — 화 순서대로.
    중복 제거는 제품 경로 app/pipeline/weave.py judge_registry 와 같은 **본문 정규화(weave_registry.norm_text)**
    기준이다. rule_id 기준으로 거르면 안 된다 — nemotron 원장은 회차마다 RULE_007·RULE_008 같은 ID 를 다른 규칙에
    재사용해서(Q3 3·4·5화의 RULE_007 본문이 전부 다름) 뒤 회차 규칙이 통째로 빠졌다(2026-11-07 수정 전 결함).
    ID 가 앞 규칙과 겹치면 `<rule_id>@ep<화>`(예 RULE_007@ep4)로 구분한다. ID 는 앞 회차만 보고 정해지므로
    k화 이전만 자르는 호출(datagen_common._rules_before)과 전체 호출에서 같은 규칙은 같은 ID 를 받는다."""
    _core()  # weave_core 경로를 연다
    from weave_registry import norm_text

    rules: list[dict] = []
    seen_text: set[str] = set()
    used_ids: set[str] = set()
    for n, ep in episodes:
        for r in ep.get("worldview_rules", []) or []:
            content = (r.get("content") or "").strip()
            norm = norm_text(content)
            if not content or norm in seen_text:
                continue
            seen_text.add(norm)
            base = r.get("rule_id") or "RULE_U"
            rid, j = (base if base not in used_ids else f"{base}@ep{n}"), 2
            while rid in used_ids:
                rid, j = f"{base}@ep{n}#{j}", j + 1
            used_ids.add(rid)
            rules.append({"id": rid, "content": content,
                          "certainty": r.get("certainty", "explicit"), "first_episode": n})
    return rules


def load_context(work: str, knows_mode: str = "accumulate") -> WorkContext:
    """build_b_packets.work_resources 와 같은 재생: 레지스트리 매핑으로 5화를 정규화해 작품별 timelines 로 재생.

    timelines(timelines_of): WM = None(코어 기본 STATE_TIMELINES), Q1·Q3·셜록 = ["main"].
    WM 은 체커 게이트 tests/eval/checker_eval.py 의 `compile_ledger(base=corpus_dir)`(timelines 미지정 →
    STATE_TIMELINES)와 같은 timeline 조건이다. 체커 게이트와 다른 점은 둘: 여기는 레지스트리 new_label 매핑으로
    화·장면 사건을 normalize 하고 knows_mode 기본이 "accumulate"(서비스 기본)인데, 체커 게이트는 원본 그대로
    knows_mode "legacy"(코어 기본)다. 그래서 WM checker 항목 수는 timeline 조건만 맞춘 값이지 게이트의 장면 집합과
    같다고 가정하지 않는다."""
    w_checker, w_pipe, compile_ledger, normalize = _core()
    spec = WORKS[work]
    reg = json.loads(spec.registry.read_text(encoding="utf-8"))
    mapping = {e["new_label"]: e["id"]
               for c in ("characters", "locations", "objects", "times", "worldview_rules")
               for e in reg.get(c, []) or [] if e.get("new_label")}
    episodes = [normalize(json.loads((spec.ledger_dir / f"episode_{i:02d}.json").read_text(encoding="utf-8")), mapping)
                for i in range(1, episode_count(spec) + 1)]
    rules = collect_episode_rules(list(enumerate(episodes, 1)))
    registry = dict(reg)
    if not registry.get("worldview_rules"):
        registry["worldview_rules"] = rules
    ledger = compile_ledger(episodes=episodes, timelines=timelines_of(work), knows_mode=knows_mode)
    corpus = []
    for ep in episodes:
        for ev in sorted(ep["events"], key=lambda e: e.get("order", 0)):
            item = dict(ev)
            item["episode"] = ep["episode"]
            item["global_idx"] = len(corpus)
            corpus.append(item)
    ctx = WorkContext(work, ledger, mapping, corpus, registry)
    for path in sorted(spec.scene_events.glob("scene_*_event.json")):
        sid = path.name.replace("_event.json", "")
        doc = json.loads(path.read_text(encoding="utf-8"))
        evs = [normalize(e, mapping) for e in doc.get("events", [])]
        ctx.scenes[sid] = evs
        items = []
        for e in evs:
            for f in w_checker.check_event(e, ledger, mapping):
                if f.get("severity") == w_checker.SEV_INFO and not f.get("needs_judge"):
                    continue
                items.append({"kind": "checker", "event_id": e.get("event_id"), "scene_id": e.get("scene_id"),
                              "severity": f.get("severity"), "type": f.get("type"), "entity": f.get("entity"),
                              "field": f.get("field"), "premise": f.get("premise"),
                              "ledger_value": f.get("ledger_value"), "since": f.get("since"),
                              "detail": f.get("detail"), "needs_judge": bool(f.get("needs_judge")),
                              "evidence_trace": w_pipe.trace_evidence(ledger, corpus, f)})
        ctx.checker_items[sid] = items
    return ctx


class _AlwaysNormal:
    """app.pipeline.learned_judge 가 없을 때의 stub — 항상 '정상'."""
    judged_by = "stub / always-normal"

    def judge_group(self, group: dict, ledger, corpus, registry, checker_items) -> dict:  # noqa: ANN001
        return {"run_index": 1, "status": "ok", "verdict": "정상", "confidence": 0.0,
                "evidence_event_ids": [], "judged_by": self.judged_by}


def learned_backend() -> Any:
    try:
        from app.pipeline import learned_judge
    except ImportError:
        return _AlwaysNormal()
    return learned_judge.judge_from_settings()


class LearnedJudge:
    """판정 패킷(장면) 하나당 1회. group·ledger·corpus·registry·checker 항목은 어댑터와 같은 모양."""

    def __init__(self, backend: Any = None, *, works: tuple[str, ...] = ALL_WORKS,
                 knows_mode: str = "accumulate",
                 context_loader: Callable[[str, str], WorkContext] = load_context):
        self.backend = backend if backend is not None else learned_backend()
        self.name = f"learned:{getattr(self.backend, 'judged_by', type(self.backend).__name__)}"
        self.k, self.majority, self.works = 1, 1, works
        self.knows_mode, self._load = knows_mode, context_loader
        self.runs: dict[str, dict[str, dict]] = {}

    def votes(self, work: str) -> dict[str, list[bool]]:
        ctx = self._load(work, self.knows_mode)
        out, raw = {}, {}
        for sid, evs in ctx.scenes.items():
            group = {"key": sid, "unit": "scene", "events": evs}
            r = self.backend.judge_group(group, ctx.ledger, ctx.corpus, ctx.registry,
                                         [it for it in ctx.checker_items.get(sid, [])])
            raw[sid] = r
            out[sid] = [r.get("status") == "ok" and r.get("verdict") in POSITIVE_VERDICTS]
        self.runs[work] = raw
        return out


class AdapterLLMJudge:
    """어댑터 경로(app.pipeline.weave.run_checks_parallel) 그대로 장면 판정 k회. API 키 필요(또는 녹화 재생)."""

    def __init__(self, *, works: tuple[str, ...] = ALL_WORKS, knows_mode: str = "accumulate"):
        from app.core.config import settings
        self.k, self.majority = settings.weave_judge_k, settings.weave_judge_majority
        self.name = f"llm:{settings.weave_llm_provider}/{settings.weave_judge_model or settings.weave_llm_model}"
        self.works, self.knows_mode = works, knows_mode

    def votes(self, work: str) -> dict[str, list[bool]]:
        from app.core.config import settings
        from app.pipeline import weave as W
        W._api_key_ready()
        ctx = load_context(work, self.knows_mode)
        cfg = W.weave_config()
        cfg["judge"]["unit"] = "scene"
        out = {}
        with tempfile.TemporaryDirectory() as tmp:
            for sid, evs in ctx.scenes.items():
                for e in evs:
                    e.setdefault("scene_id", sid)
                checks = W.run_checks_parallel(evs, ctx.ledger, ctx.mapping, ctx.corpus, cfg, Path(tmp),
                                               registry=ctx.registry,
                                               concurrency=settings.weave_judge_concurrency)
                by_run: dict[int, bool] = {}
                for it in checks["judge"]:
                    for r in it["runs"]:
                        if r.get("status") == "ok" and r.get("verdict"):
                            by_run[r["run_index"]] = by_run.get(r["run_index"], False) or \
                                r["verdict"] in POSITIVE_VERDICTS
                out[sid] = [by_run[i] for i in sorted(by_run)]
        return out


# ================================================================ 채점

def score(judge: Judge, works: Iterable[str] | None = None, *, rerun: Judge | None = None,
          allow_partial: bool = False, column: str | None = None) -> dict:
    """score_longitudinal.main 의 집계를 판정기 하나에 대해 계산한다(파일 쓰기 없음)."""
    works = tuple(works or judge.works)
    per_work, all_preds, all_gold = {}, {}, {}
    for w in works:
        spec, gold = WORKS[w], load_gold(w)
        preds = majority_preds(judge.votes(w), judge.majority)
        incomplete = sorted(s for s in gold if s not in preds or preds[s][1] < judge.k)
        if incomplete and not allow_partial:
            raise SystemExit(f"[{w}] 미완료 장면 {len(incomplete)}건 — 측정 종료 전 집계 금지 "
                             f"(--allow-partial 로 확인만 가능): {incomplete[:5]}")
        preds = {s: p for s, p in preds.items() if s in gold}
        g = {s: gold[s]["pos"] for s in preds}
        m = metrics(preds, g)
        by_type = {t: _recall_cell([s for s in preds if gold[s]["pos"] and gold[s]["type"] == t], preds)
                   for t in TYPES}
        by_layer = {lay: _recall_cell([s for s in preds if gold[s]["pos"] and gold[s]["layer"] == lay], preds)
                    for lay in ("checker", "judge")}
        by_auth = {}
        for au in sorted({gold[s]["author"] for s in preds if gold[s]["author"]}):
            pos = [s for s in preds if gold[s]["pos"] and gold[s]["author"] == au]
            neg = [s for s in preds if not gold[s]["pos"] and gold[s]["author"] == au]
            by_auth[au] = {"n_pos": len(pos), "TP": sum(1 for s in pos if preds[s][2]),
                           "n_neg": len(neg), "FP": sum(1 for s in neg if preds[s][2])}
        misses = sorted(s for s in preds if gold[s]["pos"] and not preds[s][2])
        absent: set[str] = set()
        if spec.evidence_map and spec.evidence_map.exists():
            absent = set(json.loads(spec.evidence_map.read_text(encoding="utf-8"))
                         .get("ledger_absent_scenes", {}).get("scenes", {}))
        attribution = {"misses": misses,
                       "ledger_absent": sorted(s for s in misses if s in absent),
                       "judge": sorted(s for s in misses if s not in absent),
                       "has_map": bool(absent)}
        rr = None
        if rerun is not None and w in rerun.works:
            rpreds = {s: p for s, p in majority_preds(rerun.votes(w), rerun.majority).items()
                      if s in preds and p[1] >= rerun.k}
            same = sum(1 for s in rpreds if rpreds[s][2] == preds[s][2])
            rr = {"n": len(rpreds), "same_majority": same,
                  "reproducibility": round(same / len(rpreds), 3) if rpreds else None,
                  "changed": sorted(s for s in rpreds if rpreds[s][2] != preds[s][2])}
        per_work[w] = {"metrics": m, "by_type": by_type, "by_layer": by_layer, "by_author": by_auth,
                       "attribution_2_7": attribution, "rerun": rr, "incomplete": incomplete,
                       "predictions": {s: preds[s][2] for s in sorted(preds)}}
        for s in preds:
            all_preds[f"{w}:{s}"] = preds[s]
            all_gold[f"{w}:{s}"] = gold[s]

    g_all = {k: v["pos"] for k, v in all_gold.items()}
    total = metrics(all_preds, g_all)
    total["by_type"] = {t: _recall_cell([k for k in all_preds if all_gold[k]["pos"] and all_gold[k]["type"] == t],
                                        all_preds) for t in TYPES}
    total["by_layer"] = {lay: _recall_cell([k for k in all_preds if all_gold[k]["pos"] and all_gold[k]["layer"] == lay],
                                           all_preds) for lay in ("checker", "judge")}
    human = [k for k in all_preds if all_gold[k]["author"] == "human"]
    total["human_control"] = {"n": len(human), "n_pos": sum(1 for k in human if all_gold[k]["pos"]),
                              "TP": sum(1 for k in human if all_gold[k]["pos"] and all_preds[k][2]),
                              "FP": sum(1 for k in human if not all_gold[k]["pos"] and all_preds[k][2])}
    total["conditional_precision"] = {str(p): cond_precision(total["recall"], total["fpr"], p)
                                      for p in (0.01, 0.02, 0.05, 0.44)}
    if rerun is not None:
        rs = [r["rerun"] for r in per_work.values() if r["rerun"]]
        n, same = sum(r["n"] for r in rs), sum(r["same_majority"] for r in rs)
        total["rerun"] = {"n": n, "same_majority": same, "reproducibility": round(same / n, 3) if n else None}
    lowo = {}
    if len(works) > 1:
        for w in works:
            keep = {k: v for k, v in all_preds.items() if not k.startswith(w + ":")}
            lowo[f"without_{w}"] = metrics(keep, {k: g_all[k] for k in keep})
    return {"harness": "scripts/eval/judge_eval.py (Weave score_longitudinal.py 이식)",
            "weave_commit": WEAVE_COMMIT, "judge": judge.name, "k": judge.k, "majority": judge.majority,
            "column": column, "works": list(works), "partial": allow_partial,
            "total": total, "per_work": per_work, "leave_one_work_out": lowo}


def predictions_of(report: dict) -> dict[str, bool]:
    return {f"{w}:{s}": v for w, r in report["per_work"].items() for s, v in r["predictions"].items()}


def compare(report_a: dict, report_b: dict, **kw: Any) -> dict:
    """두 보고서(같은 작품·장면)에 대한 대응 bootstrap. A=기준(예: 동결 열), B=비교(예: learned)."""
    works = [w for w in report_a["works"] if w in report_b["works"]]
    pa, pb = predictions_of(report_a), predictions_of(report_b)
    positives = [f"{w}:{s}" for w in works for s, l in load_gold(w).items() if l["pos"]]
    strata = {k: k.split(":")[0] for k in positives}
    out = paired_bootstrap_recall_diff(pa, pb, positives, strata=strata, **kw)
    out |= {"a": report_a["judge"], "b": report_b["judge"], "works": works}
    return out


# ================================================================ 출력

def _ci(c: tuple[float, float] | None) -> str:
    return f"[{c[0]:.3f}–{c[1]:.3f}]" if c else "[-]"


def format_report(rep: dict) -> str:
    m = rep["total"]
    lines = [f"judge 층 평가 — {rep['judge']} (k={rep['k']}, 다수결 {rep['majority']}/{rep['k']}) · "
             f"작품 {', '.join(rep['works'])} · 코퍼스 event_sourcing {rep['weave_commit']}"]
    if rep.get("column"):
        lines.append(f"조건: {rep['column']}")
    lines.append(f"합산: Recall {m['TP']}/{m['n_pos']}={m['recall']:.3f} {_ci(m['recall_ci'])} | "
                 f"오탐 {m['FP']}/{m['n_neg']}={m['fpr']:.3f} {_ci(m['fpr_ci'])} | d={m['d']:.3f}")
    for w, r in rep["per_work"].items():
        mm, at = r["metrics"], r["attribution_2_7"]
        attr = (f"원장부재 {len(at['ledger_absent'])} / 판정자 {len(at['judge'])}" if at["has_map"]
                else f"판정자 {len(at['judge'])} (매핑 없음)")
        rr = f" | 재실행 {r['rerun']['same_majority']}/{r['rerun']['n']}" if r["rerun"] else ""
        lines.append(f"  {w}: Recall {mm['TP']}/{mm['n_pos']} 오탐 {mm['FP']}/{mm['n_neg']} | 미탐 귀속 {attr}{rr}")
    for name, lm in rep["leave_one_work_out"].items():
        lines.append(f"  LOWO {name}: Recall {lm['TP']}/{lm['n_pos']} {_ci(lm['recall_ci'])} 오탐 {lm['FP']}/{lm['n_neg']}")
    lines.append("  유형별: " + " · ".join(f"{t} {c['TP']}/{c['n']}" for t, c in m["by_type"].items()))
    lines.append("  담당 층별(라벨 expected_layer, 제작 측 추정): "
                 + " · ".join(f"{t} {c['TP']}/{c['n']}" for t, c in m["by_layer"].items()))
    hc = m["human_control"]
    lines.append(f"  사람 집필: 모순 {hc['n_pos']} 중 TP {hc['TP']} · 정상 {hc['n'] - hc['n_pos']} 중 FP {hc['FP']}")
    if "rerun" in m:
        lines.append(f"  재실행 재현율: {m['rerun']['same_majority']}/{m['rerun']['n']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--mode", choices=("recorded", "learned", "llm"), default="recorded")
    ap.add_argument("--works", default=None, help="쉼표 구분 (기본: recorded=wm,q1,q3 / 그 밖=wm,q1,q3,sherlock)")
    ap.add_argument("--knows-mode", default="accumulate", choices=("legacy", "accumulate"))
    ap.add_argument("--allow-partial", action="store_true")
    ap.add_argument("--compare-recorded", action="store_true",
                    help="동결 열(recorded)과 대응 bootstrap 으로 recall 차이 95%% CI (겹치는 작품만)")
    ap.add_argument("--n-boot", type=int, default=10_000)
    ap.add_argument("--out", type=Path, default=None, help="보고서 JSON 저장 경로")
    a = ap.parse_args(argv)
    works = tuple(a.works.split(",")) if a.works else None

    if a.mode == "recorded":
        judge: Judge = RecordedJudge()
        rep = score(judge, works, rerun=RecordedJudge(rerun=True), allow_partial=a.allow_partial,
                    column=FROZEN_COLUMN)
    elif a.mode == "learned":
        judge = LearnedJudge(works=works or ALL_WORKS, knows_mode=a.knows_mode)
        rep = score(judge, allow_partial=a.allow_partial)
    else:
        judge = AdapterLLMJudge(works=works or ALL_WORKS, knows_mode=a.knows_mode)
        rep = score(judge, allow_partial=a.allow_partial)
    print(format_report(rep))
    if a.compare_recorded and a.mode != "recorded":
        base_works = [w for w in rep["works"] if w in FROZEN_WORKS]
        base = score(RecordedJudge(), base_works, column=FROZEN_COLUMN)
        cmp_ = compare(base, rep, n_boot=a.n_boot)
        rep["compare_recorded"] = cmp_
        print(f"대응 bootstrap ({', '.join(cmp_['works'])}, 양성 {cmp_['n_pos']}, {cmp_['n_boot']}회, 작품 층화): "
              f"recall {cmp_['recall_b']:.3f} − {cmp_['recall_a']:.3f} = {cmp_['diff']:+.3f} "
              f"95% CI [{cmp_['ci'][0]:+.3f}, {cmp_['ci'][1]:+.3f}] (B만 {cmp_['b_only']} / A만 {cmp_['a_only']})")
    if a.out:
        a.out.write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=list), encoding="utf-8")
        print("저장:", a.out)


if __name__ == "__main__":
    main()
