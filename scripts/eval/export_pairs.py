"""학습 judge 학습 데이터 내보내기 — 작품 재생 + (주입 manifest | 실제 장면 라벨) → build_pairs → 라벨 붙은 JSONL.

쌍은 서비스 추론과 **같은 함수**(app.pipeline.pairs.build_pairs)로 만들고, 작품 재생은 평가 하네스와 **같은 재생**을 쓴다
(설계안 §3-1): `judge_eval.load_context(work)` 와 같이 레지스트리(q1·q3·셜록 `registry_live.json`, wm `entity_registry.json`)
매핑으로 1..5화를 정규화하고 `compile_ledger(timelines=judge_eval.timelines_of(work), knows_mode="accumulate")`
(WM = 코어 기본 STATE_TIMELINES, 나머지 = ["main"]), checker needs_judge 항목도 같은 모양.
  --real   실제 장면은 load_context(work) 그 자체(원장·코퍼스·레지스트리·장면 사건·checker 항목)를 쓴다.
  --inject 주입 사례는 같은 기반 재생(base_replay)에 setup(6화)을 더해 다시 재생하고 test(7화)를 판정한다.
           주입 사건의 timeline 은 기반 작품 사건이 들어가는 원장 칸 이름으로 바꾼다(wm: 'future_world', 나머지: 'main') —
           inject_timeline 참고.
  dracula 는 확정 원장이 extract_work.py 산출물이라 --inject 만 된다(장면 라벨 없음).

라벨(learned_judge.LABELS 순서 + 제외): 모순 | 정상 | 무관 | 제외
  주입 사례  manifest labels 의 (event_id, premise_id) → 그 라벨. 나머지 쌍은 '무관'
  실제 장면  근거 = labels 의 evidence_event_ids(wm) ∪ evidence_event_map.json 의 matched 후보(q1·q3·셜록)
             모순 장면: 근거 전제(premise_id ∈ 근거) → 모순
                        type '설정' 장면의 rule 전제 → 제외(지도에 규칙 ID 가 없다)
                        근거 쌍과 같은 (엔티티, 필드), 또는 전제 본문이 근거 사건을 가리키는 쌍 → 제외
                        나머지 → 무관
             정상 장면: 같은 엔티티의 ledger/checker 전제 → 정상, 나머지 → 무관
  '나머지 = 무관'은 근사다. '제외'는 그 근사가 가장 틀리기 쉬운 곳을 손실에서 뺀다(설계안 §2-2-3). --keyed-only 면 명시된 쌍만.
  위가 라벨 v1(--label-version 1)이다. v1 은 전제 쪽만 봐서 양성 장면의 모든 가설 × 근거 사건의 모든 전제 문장이 '모순'이 됐다.
  라벨 v2(기본, 양성 장면만 바뀐다 — 정상 장면·주입 사례는 v1 과 같다):
             가설 키  가설이 심은 문장(labels 의 seeded_text)을 담는다. 상태 가설은 `(근거: …)` 인용, 인용이 없는 상태 가설과
                      사건 가설(field 없음)은 `원문:` 발췌로 본다(WM·셜록은 원문 발췌가 장면 대부분을 담아 상태 가설에 원문을
                      쓰면 장면의 거의 모든 가설이 걸린다). 공백·구두점을 지운 뒤 한쪽이 다른 쪽을 포함(인용이 8자 이상일 때
                      인용 ⊂ seeded_text 도 — 인용이 여러 문장 중 하나만 가져온 경우), 또는 seeded_text 문자 2-gram 중
                      대상에 있는 비율 ≥ τ_h.
             전제 키  premise_id ∈ 근거 그리고 (checker 전제 | 그 가설에 근거 사건 전제(checker 제외)가 하나뿐 |
                      전제 머리(` | ` 앞, via·since 괄호 뺌)와 violated_fact 또는 evidence_quotes 하나의 문자 2-gram Dice ≥ τ_p).
             둘 다 → 모순. 가설 키만 / premise_id ∈ 근거 / v1 의 제외 조건 → 제외. 나머지 → 무관.
  τ_h·τ_p 는 학습 작품(WM·셜록) 감사로 정했다(TAU_H·TAU_P). 행에 label_version 과 label_v1 을 남긴다.

출력 한 줄: {"premise","hypothesis","label","premise_kind","premise_id","event_id","entity","field",
             "work","scene_id","group_id","scene_positive","case_id","contrast_group","operator","type","source","split","keyed"}
group_id: 샘플링 단위 — 주입은 contrast_group(없으면 case_id), 실제 장면은 "<work>:<scene_id>".
split: 주입은 --holdout-operators 의 연산자 → "holdout", 나머지 → "train". 실제 장면은 "real".

실행(설계안 §3-2 셀 2):
  PYTHONIOENCODING=utf-8 python scripts/eval/export_pairs.py --work q1 --real \\
      --inject build/judge_inject/manifest_q1.json --out build/pairs_q1.jsonl --holdout-operators knowledge_heard
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, NamedTuple

ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = Path(__file__).resolve().parent
for _p in (str(ROOT), str(EVAL_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import datagen_common as C  # noqa: E402
import judge_eval as JE  # noqa: E402

from app.pipeline.pairs import build_pairs  # noqa: E402 - 코어 경로도 이 import 가 연다

import checker as w_checker  # noqa: E402
from ledger import FALLBACK_TIMELINE, compile_ledger, normalize  # noqa: E402
from weave_pipeline import SEV_INFO, scene_groups  # noqa: E402

KNOWS_MODE = "accumulate"  # 서비스 기본(settings.weave_knows_mode)·하네스와 같게
EXCLUDED = "제외"
WORKS = C.ALL_DATA_WORKS   # wm q1 q3 sherlock dracula
REAL_WORKS = tuple(JE.WORKS)
LABEL_VERSION = 2
TAU_H = 0.5    # 가설 키: seeded_text 2-gram 포함률 (학습 작품 감사로 정함)
TAU_P = 0.15   # 전제 키: 전제 머리 대 violated_fact·evidence_quote 2-gram Dice (학습 작품 감사로 정함)


def checker_items(events: list[dict], ledger, mapping: dict) -> list[dict]:  # noqa: ANN001
    """weave_pipeline.run_checks 의 checker_items 와 같은 항목(쌍 생성에 쓰는 키만)."""
    out = []
    for e in events:
        for f in w_checker.check_event(e, ledger, mapping):
            if f.get("severity") == SEV_INFO and not f.get("needs_judge"):
                continue
            out.append({"kind": "checker", "event_id": e.get("event_id"), "scene_id": e.get("scene_id"),
                        "severity": f.get("severity"), "type": f.get("type"), "entity": f.get("entity"),
                        "field": f.get("field"), "premise": f.get("premise"),
                        "ledger_value": f.get("ledger_value"), "since": f.get("since"),
                        "detail": f.get("detail"), "needs_judge": bool(f.get("needs_judge"))})
    return out


def ledger_digest(ledger) -> str:  # noqa: ANN001
    """원장 상태(state·knowledge) sha256 — 학습 재생과 평가 재생이 같은지 확인용."""
    raw = json.dumps(ledger.to_dict(), ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- 재생

class BaseReplay(NamedTuple):  # dataclass 는 sys.modules 없이 로드될 때(테스트 _load_script) 깨진다
    work: str
    episodes: list[dict]   # 정규화된 1..5화
    registry: dict         # 세계관 규칙 포함(레지스트리에 없으면 화 문서에서 — load_context 와 같게)
    mapping: dict
    ledger: Any
    corpus: list[dict]


def base_replay(work: str, *, ledger_dir: Path | None = None, registry_path: Path | None = None) -> BaseReplay:
    """judge_eval.load_context 와 같은 기반 재생(장면 사건 없이). dracula 는 extract_work.py 원장."""
    eps, reg, mapping = C.load_episodes(work, ledger_dir, registry_path)
    registry = dict(reg)
    if not registry.get("worldview_rules"):
        registry["worldview_rules"] = C._rules_before(reg, eps, 10 ** 6)
    ledger = compile_ledger(episodes=eps, timelines=JE.timelines_of(work), knows_mode=KNOWS_MODE)
    return BaseReplay(work, eps, registry, mapping, ledger, C.build_corpus(eps))


def inject_timeline(base: BaseReplay) -> str:
    """주입 사건에 붙일 timeline = 기반 작품 사건 다수가 들어간 원장 칸 이름(timelines 밖의 값은 'unknown' 칸).

    주입 사건은 원래 'future_world' 다. timelines=["main"] 원장(q1·q3·셜록·dracula)에는 그 칸이 없어 기반 사건과 같은
    칸에 들게 하려면 이름을 맞춰야 하고, checker.check_event 는 사건의 timeline 이름 그대로 원장을 찾으므로 칸 이름이어야
    needs_judge 항목이 나온다(wm 은 코어 기본 timelines 라 기반 사건 다수가 'future_world' 칸 → 그대로, 나머지는 'main')."""
    buckets = Counter((e.get("timeline") if e.get("timeline") in base.ledger.state else FALLBACK_TIMELINE)
                      for e in base.corpus)
    return buckets.most_common(1)[0][0] if buckets else FALLBACK_TIMELINE


def _retimeline(events: list[dict], tl: str) -> list[dict]:
    return [dict(e) | {"timeline": tl} for e in events]


def pairs_for_events(events: list[dict], ledger, corpus: list[dict], registry: dict, mapping: dict,  # noqa: ANN001
                     unit: str = "scene") -> list[tuple[dict, list]]:
    """정규화된 판정 사건들 → (group, pairs). 서비스와 같이 장면 묶음 → build_pairs."""
    items = checker_items(events, ledger, mapping)
    out = []
    for g in scene_groups(events, unit):
        ids = {e.get("event_id") for e in g["events"]}
        out.append((g, build_pairs(g, ledger, corpus, registry, [c for c in items if c["event_id"] in ids])))
    return out


def _row(p, label: str, **meta) -> dict:  # noqa: ANN001
    return {"premise": p.premise_text, "hypothesis": p.hypothesis_text, "label": label,
            "premise_kind": p.premise_kind, "premise_id": p.premise_id, "event_id": p.event_id,
            "entity": p.entity, "field": p.field, **meta}


# ---------------------------------------------------------------- 주입

def export_inject(work: str, manifest: dict, *, keyed_only: bool, holdout: set[str],
                  base: BaseReplay | None = None) -> list[dict]:
    base = base or base_replay(work)
    tl = inject_timeline(base)
    rows = []
    for case in manifest["cases"]:
        reg = copy.deepcopy(base.registry)
        reg["worldview_rules"] = list(reg.get("worldview_rules") or []) + case["rules"]
        setup = normalize(_retimeline(case["setup_events"], tl), base.mapping)
        eps = base.episodes + ([{"episode": manifest.get("setup_episode", 6), "events": setup}] if setup else [])
        ledger = compile_ledger(episodes=eps, timelines=JE.timelines_of(work), knows_mode=KNOWS_MODE)
        corpus = C.build_corpus(eps)
        test = [normalize(e, base.mapping) for e in _retimeline(case["test_events"], tl)]
        keyed = {(lab["event_id"], lab["premise_id"]): lab["label"] for lab in case["labels"]}
        meta = {"work": work, "scene_id": case["case_id"],
                "group_id": f"{work}:{case.get('contrast_group') or case['case_id']}",
                "scene_positive": any(lab["label"] == "모순" for lab in case["labels"]),
                "case_id": case["case_id"], "operator": case["operator"], "type": case["type"],
                "contrast_group": case["contrast_group"], "source": "inject",
                "split": "holdout" if case["operator"] in holdout else "train"}
        for _g, pairs in pairs_for_events(test, ledger, corpus, reg, base.mapping):
            for p in pairs:
                lab = keyed.get((p.event_id, p.premise_id))
                if lab is None and keyed_only:
                    continue
                rows.append(_row(p, lab or "무관", keyed=lab is not None, **meta))
    return rows


# ---------------------------------------------------------------- 실제 장면

def load_scene_labels(work: str) -> dict[str, dict]:
    data = json.loads(JE.WORKS[work].labels.read_text(encoding="utf-8"))["labels"]
    return {lab["scene_id"]: lab for lab in data} if isinstance(data, list) else dict(data)


def evidence_ids(work: str, labels: dict[str, dict]) -> dict[str, set[str]]:
    """장면별 근거 사건: labels 의 evidence_event_ids ∪ evidence_event_map 의 matched 후보."""
    out = {sid: set(lab.get("evidence_event_ids") or []) for sid, lab in labels.items()}
    emap = JE.WORKS[work].evidence_map
    if emap is not None and emap.exists():
        for sid, quotes in (json.loads(emap.read_text(encoding="utf-8")).get("scenes") or {}).items():
            for q in quotes:
                if q.get("status") == "matched":
                    out.setdefault(sid, set()).update(c["event_id"] for c in q.get("candidates") or [])
    return out


def real_label(p, *, positive: bool, scene_type: str | None, evidence: set[str],  # noqa: ANN001
               evidence_keys: set[tuple[str | None, str | None]]) -> tuple[str, bool]:
    """(라벨, keyed). 모듈 docstring 의 실제 장면 규칙."""
    if not positive:
        return ("정상" if (p.premise_kind in ("ledger", "checker") and p.entity) else "무관"), False
    if p.premise_id in evidence:
        return "모순", True
    if scene_type == "설정" and p.premise_kind == "rule":
        return EXCLUDED, False
    if (p.entity, p.field) in evidence_keys and p.entity is not None:
        return EXCLUDED, False
    if any(eid in (p.premise_text or "") for eid in evidence):
        return EXCLUDED, False
    return "무관", False


# ---------------------------------------------------------------- 라벨 v2 (가설 키 AND 전제 키)

def _norm(s: str | None) -> str:
    return re.sub(r"[\W_]+", "", s or "")


def _bigrams(s: str | None) -> set[str]:
    t = _norm(s)
    return {t[i:i + 2] for i in range(len(t) - 1)}


def dice(a: str | None, b: str | None) -> float:
    x, y = _bigrams(a), _bigrams(b)
    return 2 * len(x & y) / (len(x) + len(y)) if x or y else 0.0


_QUOTE = re.compile(r"\(근거: (.*)\)\s*$")


def hypothesis_target(hypothesis: str, field: str | None) -> str | None:
    """가설 키를 볼 대상: 상태 가설의 `(근거: …)` 인용, 없으면(또는 사건 가설이면) `원문:` 발췌."""
    segs = (hypothesis or "").split(" | ")
    m = _QUOTE.search(segs[0]) if segs else None
    if field and m:
        return m.group(1)
    return next((x[len("원문: "):] for x in segs if x.startswith("원문: ")), None)


def hypothesis_key(hypothesis: str, field: str | None, seeded: str | None, tau_h: float = TAU_H) -> bool:
    target = hypothesis_target(hypothesis, field)
    ns, nt = _norm(seeded), _norm(target)
    if not ns or not nt:
        return False
    if ns in nt or (len(nt) >= 8 and nt in ns):
        return True
    b = _bigrams(seeded)
    return bool(b) and len(b & _bigrams(target)) / len(b) >= tau_h


def premise_head(premise: str) -> str:
    h = (premise or "").split(" | ")[0]
    h = re.sub(r"\((?:via|since) [^)]*\)", "", h)
    return re.sub(r"\[일시적[^\]]*\]", "", h).strip()


def premise_dice(premise: str, refs: list[str]) -> float:
    head = premise_head(premise)
    return max((dice(head, r) for r in refs if r), default=0.0)


def scene_refs(lab: dict) -> list[str]:
    """전제 키 대조 문장: violated_fact + evidence_quotes 의 quote."""
    return [lab.get("violated_fact") or ""] + [q.get("quote") or "" for q in lab.get("evidence_quotes") or []
                                                if isinstance(q, dict)]


def _hyp_id(p) -> tuple:  # noqa: ANN001
    return (p.event_id, p.entity, p.field, p.hypothesis_text)


def label_scene_v2(pairs: list, lab: dict, evidence: set[str], *, tau_h: float = TAU_H,  # noqa: ANN001
                   tau_p: float = TAU_P) -> list[tuple[str, bool]]:
    """장면 하나의 쌍들 → [(라벨, keyed)] (같은 순서). 모듈 docstring 의 라벨 v2."""
    positive = bool(lab.get("has_contradiction"))
    evidence_keys = {(p.entity, p.field) for p in pairs if p.premise_id in evidence}
    v1 = [real_label(p, positive=positive, scene_type=lab.get("type"), evidence=evidence,
                     evidence_keys=evidence_keys) for p in pairs]
    if not positive:
        return v1
    seeded, refs = lab.get("seeded_text"), scene_refs(lab)
    n_ev = Counter(_hyp_id(p) for p in pairs if p.premise_id in evidence and p.premise_kind != "checker")
    hk_cache: dict[tuple, bool] = {}
    out = []
    for p, (lab1, _k) in zip(pairs, v1):
        hid = _hyp_id(p)
        if hid not in hk_cache:
            hk_cache[hid] = hypothesis_key(p.hypothesis_text, p.field, seeded, tau_h)
        hk = hk_cache[hid]
        in_ev = p.premise_id in evidence
        pk = in_ev and (p.premise_kind == "checker" or n_ev[hid] == 1
                        or premise_dice(p.premise_text, refs) >= tau_p)
        if hk and pk:
            out.append(("모순", True))
        elif hk or in_ev or lab1 == EXCLUDED:
            out.append((EXCLUDED, False))
        else:
            out.append(("무관", False))
    return out


def export_real(work: str, *, keyed_only: bool, ctx: JE.WorkContext | None = None,
                label_version: int = LABEL_VERSION, tau_h: float = TAU_H, tau_p: float = TAU_P,
                pairs_version: str = "2") -> list[dict]:
    """pairs_version "3": 글만 v3(build_pairs render=v3). 라벨은 v2 글로 계산한다(렌더링과 무관 — 같은 선택·같은 순서)."""
    if work not in JE.WORKS:
        raise SystemExit(f"--real 은 장면 라벨이 있는 작품만: {tuple(JE.WORKS)}")
    if label_version not in (1, 2):
        raise SystemExit(f"label_version 은 1|2: {label_version}")
    ctx = ctx or JE.load_context(work, KNOWS_MODE)
    labels = load_scene_labels(work)
    ev_map = evidence_ids(work, labels)
    rows = []
    for sid, events in ctx.scenes.items():
        lab = labels.get(sid)
        if lab is None:
            continue
        positive = bool(lab.get("has_contradiction"))
        evidence = ev_map.get(sid, set())
        meta = {"work": work, "scene_id": sid, "group_id": f"{work}:{sid}", "scene_positive": positive,
                "case_id": sid, "operator": None, "type": lab.get("type"), "contrast_group": None,
                "source": "real", "split": "real", "label_version": label_version}
        # 하네스(judge_eval.LearnedJudge.votes)와 같은 판정 패킷: 장면 파일 하나 = group 하나, checker 항목 그대로
        group = {"key": sid, "unit": "scene", "events": events}
        pairs = build_pairs(group, ctx.ledger, ctx.corpus, ctx.registry, list(ctx.checker_items.get(sid, [])))
        out_pairs = pairs
        if str(pairs_version) == "3":
            out_pairs = build_pairs(group, ctx.ledger, ctx.corpus, ctx.registry, list(ctx.checker_items.get(sid, [])),
                                    render="v3")
            if [(p.premise_kind, p.premise_id, p.event_id, p.entity, p.field) for p in pairs] != \
                    [(p.premise_kind, p.premise_id, p.event_id, p.entity, p.field) for p in out_pairs]:
                raise AssertionError(f"v3 선택이 v2 와 다르다: {sid}")
        evidence_keys = {(p.entity, p.field) for p in pairs if p.premise_id in evidence}
        v1 = [real_label(p, positive=positive, scene_type=lab.get("type"), evidence=evidence,
                         evidence_keys=evidence_keys) for p in pairs]
        labs = v1 if label_version == 1 else label_scene_v2(pairs, lab, evidence, tau_h=tau_h, tau_p=tau_p)
        for p, (label, keyed), (label1, _k1) in zip(out_pairs, labs, v1):
            if keyed_only and not keyed:
                continue
            rows.append(_row(p, label, keyed=keyed, label_v1=label1, pairs_version=str(pairs_version), **meta))
    return rows


def label_table(rows: list[dict]) -> dict:
    """실제 장면 행의 v1→v2 라벨 수와 '모순' 쌍이 남은 양성 장면 수(작품별 표용)."""
    real = [r for r in rows if r.get("source") == "real"]
    pos_scenes = {r["scene_id"] for r in real if r["scene_positive"]}

    def scenes_with(key: str) -> int:
        return len({r["scene_id"] for r in real if r.get(key) == "모순"})
    return {"v1": dict(Counter(r.get("label_v1", r["label"]) for r in real)),
            "v2": dict(Counter(r["label"] for r in real)),
            "positive_scenes": len(pos_scenes), "positive_scenes_with_contra_v1": scenes_with("label_v1"),
            "positive_scenes_with_contra_v2": scenes_with("label"),
            "transitions": {f"{a}->{b}": n for (a, b), n in sorted(Counter(
                (r.get("label_v1", r["label"]), r["label"]) for r in real).items())}}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--work", required=True, help=f"기반·장면 작품(judge_eval.load_context 와 같은 재생): {WORKS}, 또는 --corpus-dir 로 등록한 이름")
    ap.add_argument("--corpus-dir", type=Path, help="저장소 밖 코퍼스 폴더(labels.json·evidence_event_map.json·ledger_nemotron_ultra/). "
                    "--work 이름으로 등록한다(--real 전용, 예: M-3 타이타닉)")
    ap.add_argument("--ledger-dir", type=Path, help="(주입 기반만) 원장 폴더 덮어쓰기 — 기본은 작품별 확정 원장")
    ap.add_argument("--registry", type=Path, help="(주입 기반만) 레지스트리 파일 덮어쓰기")
    ap.add_argument("--inject", type=Path, help="inject.py manifest")
    ap.add_argument("--real", action="store_true", help="작품의 실제 장면(scene_*_event.json + labels.json)도")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--keyed-only", action="store_true", help="정답이 명시된 쌍만")
    ap.add_argument("--holdout-operators", nargs="*", default=[])
    ap.add_argument("--label-version", type=int, choices=[1, 2], default=LABEL_VERSION, help="실제 장면 라벨 규칙")
    ap.add_argument("--tau-h", type=float, default=TAU_H)
    ap.add_argument("--tau-p", type=float, default=TAU_P)
    ap.add_argument("--pairs-version", choices=["2", "3"], default="2",
                    help="(실제 장면만) 쌍 글 판: 2 = 지금 서비스 판, 3 = 자연어 v3(같은 선택, 라벨은 v2 글로 계산)")
    a = ap.parse_args(argv)
    if a.corpus_dir is not None:
        if a.inject:
            raise SystemExit("--corpus-dir 는 --real 전용이다(주입 기반은 작품별 확정 원장).")
        JE.register_corpus(a.work, a.corpus_dir)
    elif a.work not in WORKS:
        raise SystemExit(f"--work 는 {WORKS} 중 하나(또는 --corpus-dir 와 함께 새 이름)")
    if not a.inject and not a.real:
        raise SystemExit("--inject 또는 --real 중 하나는 있어야 합니다.")
    rows = []
    if a.inject:
        manifest = json.loads(a.inject.read_text(encoding="utf-8"))
        base = base_replay(a.work, ledger_dir=a.ledger_dir, registry_path=a.registry)
        rows += export_inject(a.work, manifest, keyed_only=a.keyed_only, holdout=set(a.holdout_operators), base=base)
    if a.real:
        if a.inject and a.pairs_version != "2":
            raise SystemExit("--pairs-version 3 은 --real 만(주입 경로는 v2)")
        rows += export_real(a.work, keyed_only=a.keyed_only, label_version=a.label_version, tau_h=a.tau_h,
                            tau_p=a.tau_p, pairs_version=a.pairs_version)
    for r in rows:
        r.setdefault("label_version", a.label_version)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    with a.out.open("w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    meta = {"work": a.work, "label_version": a.label_version, "tau_h": a.tau_h, "tau_p": a.tau_p,
            "pairs_version": a.pairs_version,
            "rows": len(rows), "sha256": hashlib.sha256(a.out.read_bytes()).hexdigest(),
            "inject": str(a.inject) if a.inject else None, "real": a.real,
            "labels": label_table(rows) if a.real else None}
    a.out.with_suffix(".meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    c = Counter((r["split"], r["label"]) for r in rows)
    print(f"{len(rows)} pairs -> {a.out}")
    for (split, label), n in sorted(c.items()):
        print(f"  {split:8s} {label}: {n}")


if __name__ == "__main__":
    main()
