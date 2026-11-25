# -*- coding: utf-8 -*-
"""학습 judge 학습(scripts/train_judge.py)의 순수 파이썬 부품 — torch·transformers 없이 import·테스트된다.

근거: docs/learned-judge-training-design.md §0-4c(누수 규칙), §2-2(불균형·'제외'), §2-4(보정·장면 기준값),
§2-6(접기 분할), §3-1 `train_judge.py` 항목.

  · 접기 분할    split_by_work: JSONL 의 `work` 로 학습·검증을 나눈다. 같은 작품이 양쪽에 있으면 에러,
                 --eval-work 작품의 행은 학습·검증 모두에서 뺀다(누수 규칙).
  · '제외' 라벨  drop_excluded: 손실·보정·기준값 어디에도 넣지 않는다.
  · 가중치       class_weights: 빈도^−½, (있는 클래스의) 평균 1로 정규화.
  · 무관 감축    sample_epoch: 에폭마다 무관 쌍을 (모순+정상)×비율로 줄인다. 묶음(group_key) 단위 라운드로빈, 시드·에폭 고정.
  · 장면 기준값  pick_scene_threshold: 장면(scene_key)별 p(모순) 최댓값, 정상 장면 오탐 ≤ 목표에서 recall 최대, 동률은 높은 기준값.
  · 그 밖        hyp_only, train_fraction(묶음 단위·포개짐), ECE(동일 개수 구간), NLL, PR-AUC, 분위수, 시드 선택, 조기 종료.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

LABELS = ["모순", "정상", "무관"]  # app.pipeline.learned_judge.LABELS 와 같은 순서
L2I = {lab: i for i, lab in enumerate(LABELS)}
EXCLUDED = "제외"                   # export_pairs·label_pairs 가 쓰는 라벨 — 학습·보정·기준값에서 뺀다
CONTRA, NORMAL, UNREL = LABELS
KNOWN_LABELS = set(LABELS) | {EXCLUDED}


# ---------------------------------------------------------------- 입출력

def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def group_key(r: dict) -> str:
    """샘플링·분할 단위. 대조 묶음 → 장면 묶음(group_id) → 사례(case_id)."""
    return str(r.get("contrast_group") or r.get("group_id") or r.get("case_id") or "")


def scene_key(r: dict) -> str:
    """장면 기준값의 단위(판정 패킷 하나). 작품이 다르면 같은 scene_id 라도 다른 장면이다."""
    sid = r.get("scene_id") or r.get("group_id") or r.get("case_id") or ""
    return f"{r.get('work') or ''}|{sid}"


def check_labels(rows: Iterable[dict]) -> None:
    bad = Counter(r.get("label") for r in rows if r.get("label") not in KNOWN_LABELS)
    if bad:
        raise ValueError(f"모르는 라벨: {dict(bad)} (허용: {sorted(KNOWN_LABELS)})")


# ---------------------------------------------------------------- 분할

@dataclass
class Split:
    train: list[dict]
    val: list[dict]
    holdout: list[dict]
    report: dict = field(default_factory=dict)
    val_excluded: list[dict] = field(default_factory=list)  # 검증 '제외' 행 — 손실·보정엔 안 쓰고 장면 점수(max)에만


def split_by_work(rows: list[dict], *, val_work: str, train_works: Sequence[str] | None = None,
                  eval_work: str | None = None) -> tuple[list[dict], list[dict], dict]:
    """(학습, 검증, 보고). train_works 가 None 이면 val·eval 이 아닌 작품 전부.

    에러: 같은 작품이 학습·검증 양쪽, eval_work 가 학습·검증 중 하나와 같음, `work` 없는 행(접기 분할을 쓸 수 없다).
    eval_work 의 행은 어디에도 넣지 않는다 — 그 수를 보고에 남긴다."""
    tw = list(train_works) if train_works is not None else None
    if tw is not None and val_work in tw:
        raise ValueError(f"작품 {val_work!r} 이 학습(--train-works)과 검증(--val-work)에 동시에 있습니다")
    if eval_work is not None and (eval_work == val_work or (tw is not None and eval_work in tw)):
        raise ValueError(f"평가 작품 {eval_work!r} 은 학습·검증에 들어갈 수 없습니다(누수 규칙 §0-4c)")
    missing = sum(1 for r in rows if not r.get("work"))
    if missing:
        raise ValueError(f"`work` 필드가 없는 행 {missing}개 — 접기 분할에는 export_pairs.py --work 출력이 필요합니다")
    train, val, dropped = [], [], Counter()
    for r in rows:
        w = r["work"]
        if w == eval_work:
            dropped["eval_work"] += 1
        elif w == val_work:
            val.append(r)
        elif tw is None or w in tw:
            train.append(r)
        else:
            dropped[f"other:{w}"] += 1
    if not val:
        raise ValueError(f"검증 작품 {val_work!r} 의 행이 없습니다")
    if not train:
        raise ValueError("학습 행이 없습니다")
    report = {"val_work": val_work, "train_works": sorted({r["work"] for r in train}), "eval_work": eval_work,
              "dropped": dict(sorted(dropped.items()))}
    return train, val, report


def split_val_groups(rows: list[dict], frac: float, seed: int) -> tuple[list[dict], list[dict]]:
    """(접기 분할을 못 쓸 때) 묶음 단위로 frac 만큼 검증으로 떼어 낸다 — 같은 묶음이 양쪽에 걸치지 않게."""
    groups = sorted({group_key(r) for r in rows})
    rng = random.Random(seed)
    rng.shuffle(groups)
    val = set(groups[:max(1, int(len(groups) * frac))]) if groups else set()
    return [r for r in rows if group_key(r) not in val], [r for r in rows if group_key(r) in val]


def split_by_group_ids(rows: list[dict], val_groups: Iterable[str]) -> tuple[list[dict], list[dict], dict]:
    """(학습, 검증, 보고). 행의 `group_id`(장면·이야기)가 val_groups 에 있으면 검증 — 미리 정해 둔 묶음 목록으로 뗀다.
    group_key(대조 묶음 우선)가 아니라 group_id 를 본다: 한 장면의 대조 짝들이 학습·검증에 갈라지지 않게.
    에러: group_id 없는 행, 목록의 묶음이 데이터에 없음, 검증이나 학습이 빈 경우."""
    vg = set(val_groups)
    missing = sum(1 for r in rows if not r.get("group_id"))
    if missing:
        raise ValueError(f"`group_id` 가 없는 행 {missing}개 — --val-groups 를 쓸 수 없습니다")
    present = {r["group_id"] for r in rows}
    unknown = sorted(vg - present)
    if unknown:
        raise ValueError(f"--val-groups 의 묶음 {len(unknown)}개가 데이터에 없습니다: {unknown[:5]}")
    train = [r for r in rows if r["group_id"] not in vg]
    val = [r for r in rows if r["group_id"] in vg]
    if not val or not train:
        raise ValueError("--val-groups 분할에서 학습 또는 검증이 비었습니다")
    report = {"val_groups": len(vg), "val_groups_by_work": dict(sorted(Counter(
        w for w, g in {(r.get("work"), r["group_id"]) for r in val}).items()))}
    return train, val, report


def drop_excluded(rows: list[dict]) -> tuple[list[dict], int]:
    kept = [r for r in rows if r.get("label") != EXCLUDED]
    return kept, len(rows) - len(kept)


def prepare(rows: list[dict], *, val_work: str | None = None, train_works: Sequence[str] | None = None,
            eval_work: str | None = None, val_rows: list[dict] | None = None, val_frac: float = 0.15,
            seed: int = 42, keyed_only: bool = False, no_real: bool = False, hyp_only_mode: bool = False,
            train_frac: float = 1.0, val_sources: str = "real", inject_frac: float = 1.0,
            val_groups: Iterable[str] | None = None) -> Split:
    """학습 스크립트의 데이터 준비 전부(학습 루프 전까지). 순서:
    라벨 검사 → (eval_work 행 제거) → keyed_only → holdout 분리 → 분할(작품 / 검증 파일 / 묶음 비율)
    → (val_work 일 때) 검증은 실제 장면(split=real)만 — val_sources="all" 이면 그 작품의 주입·LLM 라벨 행도
    → '제외' 제거(학습·검증·holdout 모두) → train_frac(학습만) → hyp_only(모두).
    검증 작품의 주입·LLM 라벨 행은 학습에도 넣지 않는다(검증 작품은 학습에 쓰지 않는다 — §2-6)."""
    check_labels(rows)
    if val_rows is not None:
        check_labels(val_rows)
    if eval_work is not None:
        n_eval = sum(1 for r in rows if r.get("work") == eval_work)
    else:
        n_eval = 0
    if keyed_only:
        rows = [r for r in rows if r.get("keyed")]
    holdout = [r for r in rows if r.get("split") == "holdout" and r.get("work") != eval_work]
    pool = [r for r in rows if r.get("split") != "holdout" and not (no_real and r.get("split") == "real")]
    report: dict = {}
    if val_work is not None:
        if val_rows is not None:
            raise ValueError("--val-work 와 --val-jsonl 은 함께 쓸 수 없습니다")
        train, val, report = split_by_work(pool, val_work=val_work, train_works=train_works, eval_work=eval_work)
        if val_sources not in ("real", "all"):
            raise ValueError(f"val_sources 는 real|all: {val_sources!r}")
        if val_sources == "real":
            n0 = len(val)
            val = [r for r in val if r.get("split") == "real"]
            report["dropped"]["val_non_real"] = n0 - len(val)
            if not val:
                raise ValueError(f"검증 작품 {val_work!r} 에 실제 장면(split=real) 행이 없습니다 — export_pairs.py --real")
    else:
        if train_works is not None:
            raise ValueError("--train-works 는 --val-work 와 함께 씁니다")
        pool = [r for r in pool if r.get("work") != eval_work] if eval_work is not None else pool
        if val_rows is not None and val_groups is not None:
            raise ValueError("--val-jsonl 과 --val-groups 는 함께 쓸 수 없습니다")
        g_report: dict = {}
        if val_rows is not None:
            train = pool
            val = [r for r in val_rows if eval_work is None or r.get("work") != eval_work]
        elif val_groups is not None:
            train, val, g_report = split_by_group_ids(pool, val_groups)
        else:
            train, val = split_val_groups(pool, val_frac, seed)
        report = {"val_work": None, "train_works": sorted({r.get("work") for r in train if r.get("work")}),
                  "eval_work": eval_work, "dropped": {"eval_work": n_eval} if n_eval else {}, **g_report}
        tr_w = {r.get("work") for r in train if r.get("work")}
        va_w = {r.get("work") for r in val if r.get("work")}
        if val_rows is not None and tr_w & va_w:
            raise ValueError(f"같은 작품이 학습·검증에 동시에 있습니다: {sorted(tr_w & va_w)}")
    if eval_work is not None and any(r.get("work") == eval_work for r in train + val + holdout):
        raise AssertionError("eval_work 행이 남았습니다")  # 위 규칙의 불변식
    train, ex_tr = drop_excluded(train)
    val_excluded = [r for r in val if r.get("label") == EXCLUDED]
    val, ex_va = drop_excluded(val)
    holdout, ex_ho = drop_excluded(holdout)
    report["excluded_label"] = {"train": ex_tr, "val": ex_va, "holdout": ex_ho}
    if train_frac < 1.0:
        n0 = len(train)
        train = train_fraction(train, train_frac, seed)
        report["train_frac"] = {"frac": train_frac, "rows_before": n0, "rows_after": len(train)}
    if inject_frac < 1.0:
        n0 = len(train)
        train = inject_fraction(train, inject_frac, seed)
        report["inject_frac"] = {"frac": inject_frac, "rows_before": n0, "rows_after": len(train)}
    if hyp_only_mode:
        train, val, holdout = hyp_only(train), hyp_only(val), hyp_only(holdout)
        val_excluded = hyp_only(val_excluded)
    report["counts"] = {name: dict(Counter(r["label"] for r in rs))
                        for name, rs in (("train", train), ("val", val), ("holdout", holdout))}
    return Split(train, val, holdout, report, val_excluded)


# ---------------------------------------------------------------- 변환·표본

def hyp_only(rows: list[dict]) -> list[dict]:
    """전제를 빈 문자열로 — 가설만 보는 지름길 기준선(G4)."""
    return [dict(r) | {"premise": ""} for r in rows]


def train_fraction(rows: list[dict], frac: float, seed: int) -> list[dict]:
    """학습 곡선용 부분 집합. 묶음 단위로 고르고, 같은 시드에서 작은 비율은 큰 비율의 부분집합이다(포개짐)."""
    if not 0 < frac <= 1:
        raise ValueError("train_frac 은 (0, 1]")
    if frac >= 1.0:
        return list(rows)
    groups = sorted({group_key(r) for r in rows})
    random.Random(f"train_frac:{seed}").shuffle(groups)
    keep = set(groups[:max(1, math.ceil(len(groups) * frac))])
    return [r for r in rows if group_key(r) in keep]


def unrelated_budget(rows: Sequence[dict], ratio: float | None) -> int | None:
    """에폭당 무관 쌍 수 상한 = floor((모순+정상) × ratio). ratio None/≤0 이면 감축 없음."""
    if ratio is None or ratio <= 0:
        return None
    rel = sum(1 for r in rows if r["label"] in (CONTRA, NORMAL))
    return int(math.floor(rel * ratio))


def sample_epoch(rows: list[dict], ratio: float | None, seed: int, epoch: int) -> list[dict]:
    """한 에폭의 학습 행. 모순·정상은 전부, 무관은 상한까지.

    무관 선택은 묶음 단위 라운드로빈: 묶음 순서를 (seed, epoch) 로 섞고, 묶음마다 무관 행을 섞은 뒤 한 바퀴에
    묶음당 하나씩 뽑는다 — 큰 묶음이 상한을 독차지하지 않는다. 결과는 입력 순서를 유지한다(결정적)."""
    budget = unrelated_budget(rows, ratio)
    unrel_idx = [i for i, r in enumerate(rows) if r["label"] == UNREL]
    if budget is None or len(unrel_idx) <= budget:
        return list(rows)
    rng = random.Random(f"unrelated:{seed}:{epoch}")
    by_group: dict[str, list[int]] = defaultdict(list)
    for i in unrel_idx:
        by_group[group_key(rows[i])].append(i)
    order = sorted(by_group)
    rng.shuffle(order)
    queues = []
    for g in order:
        q = list(by_group[g])
        rng.shuffle(q)
        queues.append(q)
    chosen: set[int] = set()
    depth = 0
    while len(chosen) < budget:
        progressed = False
        for q in queues:
            if depth < len(q):
                chosen.add(q[depth])
                progressed = True
                if len(chosen) == budget:
                    break
        if not progressed:
            break
        depth += 1
    return [r for i, r in enumerate(rows) if r["label"] != UNREL or i in chosen]


def sampled_counts(rows: Sequence[dict], ratio: float | None) -> dict[str, int]:
    """감축 뒤 에폭당 클래스 수(무관은 상한으로 자른 값) — 가중치 계산에 쓴다."""
    c = Counter(r["label"] for r in rows)
    out = {lab: c.get(lab, 0) for lab in LABELS}
    budget = unrelated_budget(rows, ratio)
    if budget is not None:
        out[UNREL] = min(out[UNREL], budget)
    return out


def class_weights(counts: dict[str, int], scheme: str = "weighted") -> list[float]:
    """LABELS 순서의 가중치. weighted: w_c ∝ n_c^−½, 학습에 있는 클래스의 평균이 1. 없는 클래스는 0.
    ce: 전부 1."""
    if scheme == "ce":
        return [1.0] * len(LABELS)
    if scheme != "weighted":
        raise ValueError(f"loss 는 ce|weighted: {scheme!r}")
    raw = [counts.get(lab, 0) ** -0.5 if counts.get(lab, 0) > 0 else 0.0 for lab in LABELS]
    present = [w for w in raw if w > 0]
    if not present:
        raise ValueError("학습 행이 없습니다")
    mean = sum(present) / len(present)
    return [w / mean for w in raw]


# ---------------------------------------------------------------- 지표

def softmax(row: Sequence[float], temperature: float = 1.0) -> list[float]:
    z = [x / temperature for x in row]
    m = max(z)
    e = [math.exp(x - m) for x in z]
    s = sum(e)
    return [x / s for x in e]


def nll(probs: Sequence[Sequence[float]], gold: Sequence[int]) -> float:
    if not gold:
        return float("nan")
    return -sum(math.log(max(p[g], 1e-12)) for p, g in zip(probs, gold)) / len(gold)


def ece(conf: Sequence[float], correct: Sequence[bool], bins: int = 10) -> float | None:
    """동일 개수 구간 ECE: 신뢰도 순으로 정렬해 bins 개로 거의 같게 나누고 |정확도 − 평균 신뢰도| 의 가중 평균."""
    n = len(conf)
    if n == 0:
        return None
    order = sorted(range(n), key=lambda i: conf[i])
    total = 0.0
    for b in range(bins):
        lo, hi = (b * n) // bins, ((b + 1) * n) // bins
        idx = order[lo:hi]
        if not idx:
            continue
        acc = sum(1 for i in idx if correct[i]) / len(idx)
        avg = sum(conf[i] for i in idx) / len(idx)
        total += len(idx) / n * abs(acc - avg)
    return total


def pair_calibration(probs: Sequence[Sequence[float]], gold: Sequence[int], bins: int = 10) -> dict:
    """쌍 단위 보정 지표: NLL, top-label ECE, '모순' 이진 ECE(p(모순) 대 모순 여부)."""
    top = [max(range(len(p)), key=lambda k: p[k]) for p in probs]
    ci = L2I[CONTRA]
    return {"n": len(gold), "nll": _r(nll(probs, gold)),
            "ece_top": _r(ece([p[t] for p, t in zip(probs, top)], [t == g for t, g in zip(top, gold)], bins)),
            "ece_contra": _r(ece([p[ci] for p in probs], [g == ci for g in gold], bins)),
            "bins": bins, "binning": "equal_count"}


def pr_auc(p: Sequence[float], gold: Sequence[bool]) -> float | None:
    """평균 정밀도(모순 대 나머지). 동점 점수는 한 묶음으로 처리."""
    pos = sum(gold)
    if pos == 0:
        return None
    pairs = sorted(zip(p, gold), key=lambda x: -x[0])
    ap, tp, fp, i = 0.0, 0, 0, 0
    while i < len(pairs):
        j = i
        tp_b = fp_b = 0
        while j < len(pairs) and pairs[j][0] == pairs[i][0]:
            tp_b += pairs[j][1]
            fp_b += not pairs[j][1]
            j += 1
        tp += tp_b
        fp += fp_b
        ap += tp_b / pos * (tp / (tp + fp))
        i = j
    return ap


def quantiles(values: Sequence[int], qs: Sequence[float] = (0.5, 0.9, 0.95, 0.99)) -> dict:
    """최근접 순위 분위수 + 최대·개수. 토큰 길이 보고용."""
    if not values:
        return {"n": 0}
    v = sorted(values)
    out = {f"p{int(q * 100)}": v[min(len(v) - 1, max(0, math.ceil(q * len(v)) - 1))] for q in qs}
    return out | {"max": v[-1], "n": len(v)}


def _r(x: float | None, nd: int = 6) -> float | None:
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(x, nd)


# ---------------------------------------------------------------- 장면 기준값

def scene_scores(rows: Sequence[dict], p: Sequence[float], *,
                 allow_excluded: bool = False) -> dict[str, tuple[float, bool]]:
    """{장면: (max p(모순), 모순 장면 여부)}. 장면 정답은 행의 `scene_positive`(export_pairs 실제 장면)가 있으면
    그것, 없으면 그 장면에 '모순' 행이 하나라도 있는지. '제외' 행은 기본으로 거부한다.
    allow_excluded=True 면 '제외' 행도 장면 max 에 넣는다(서비스는 장면의 모든 쌍을 본다 — 쌍 라벨과 무관한 장면 지표).
    그때 '제외' 행에는 scene_positive 가 있어야 한다(장면 정답을 쌍 라벨에서 끌어오지 않게)."""
    if len(rows) != len(p):
        raise ValueError("rows 와 p 의 길이가 다릅니다")
    best: dict[str, float] = {}
    pos: dict[str, bool] = {}
    for r, pr in zip(rows, p):
        if r.get("label") == EXCLUDED and not (allow_excluded and r.get("scene_positive") is not None):
            raise ValueError("'제외' 행은 기준값 계산에 넣지 않습니다(allow_excluded 는 scene_positive 가 있는 행만)")
        k = scene_key(r)
        best[k] = max(best.get(k, -1.0), float(pr))
        given = r.get("scene_positive")
        flag = bool(given) if given is not None else r.get("label") == CONTRA
        pos[k] = pos.get(k, False) or flag
    return {k: (best[k], pos[k]) for k in best}


AGGREGATES = ("max", "hyp_contrast", "hyp_contrast_past05")


def hypothesis_id(r: dict) -> tuple:
    """쌍 행의 가설 식별자: (event_id, entity, field, 가설 텍스트) — export_pairs._hyp_id 와 같은 묶음(행에 별도 가설 id 는 없다)."""
    return (r.get("event_id"), r.get("entity"), r.get("field"), r.get("hypothesis"))


def scene_scores_agg(rows: Sequence[dict], p: Sequence[float], aggregate: str = "max") -> dict[str, tuple[float, bool]]:
    """장면 점수 집계(실험 M-2, 설계안 §0-8). 장면 정답·'제외' 처리는 scene_scores(allow_excluded=True)와 같다.
      max                 장면 모든 쌍의 max p(모순)
      hyp_contrast        가설마다 [그 가설 전제들의 p 최댓값 − 평균], 장면은 그 최댓값
      hyp_contrast_past05 위와 같되 premise_kind=past 전제의 p 에 0.5 를 곱한 뒤 계산"""
    if aggregate not in AGGREGATES:
        raise ValueError(f"aggregate 는 {AGGREGATES}: {aggregate!r}")
    base = scene_scores(rows, p, allow_excluded=True)   # 정답·검사(제외 행 scene_positive)
    if aggregate == "max":
        return base
    by: dict[str, dict[tuple, list[float]]] = defaultdict(lambda: defaultdict(list))
    for r, pr in zip(rows, p):
        v = float(pr) * (0.5 if aggregate == "hyp_contrast_past05" and r.get("premise_kind") == "past" else 1.0)
        by[scene_key(r)][hypothesis_id(r)].append(v)
    return {k: (max(max(vs) - sum(vs) / len(vs) for vs in by[k].values()), base[k][1]) for k in base}


def pair_scene_scores(rows: Sequence[dict], p: Sequence[float]) -> dict[str, tuple[float, bool]]:
    """'쌍 = 장면 하나' 근사: {쌍: (p(모순), 쌍 라벨이 모순)}. 검증 쌍이 실제 장면 단위가 아닐 때(떼어 둔 생성 쌍) 기준값용.
    scene_positive 는 보지 않는다(쌍 라벨만). '제외' 행은 쌍 정답이 없어 거부한다."""
    if len(rows) != len(p):
        raise ValueError("rows 와 p 의 길이가 다릅니다")
    out: dict[str, tuple[float, bool]] = {}
    for i, (r, pr) in enumerate(zip(rows, p)):
        if r.get("label") == EXCLUDED:
            raise ValueError("'제외' 행은 쌍 단위 기준값에 넣지 않습니다")
        out[f"{i}|{r.get('id') or r.get('case_id') or ''}"] = (float(pr), r.get("label") == CONTRA)
    return out


def scene_metrics_at(scores: dict[str, tuple[float, bool]], threshold: float) -> dict:
    tp = sum(1 for s, g in scores.values() if g and s >= threshold)
    fn = sum(1 for s, g in scores.values() if g and s < threshold)
    fp = sum(1 for s, g in scores.values() if not g and s >= threshold)
    tn = sum(1 for s, g in scores.values() if not g and s < threshold)
    return {"recall": _r(tp / (tp + fn)) if tp + fn else None, "fpr": _r(fp / (fp + tn)) if fp + tn else None,
            "tp": tp, "fn": fn, "fp": fp, "tn": tn, "n_pos": tp + fn, "n_neg": fp + tn}


def pick_scene_threshold(rows: Sequence[dict], p: Sequence[float], target_fpr: float = 0.05, *,
                         allow_excluded: bool = False) -> tuple[float, dict]:
    """장면 기준값(§2-4 사전 고정 규칙): p_scene = 장면의 max p(모순).
    후보 = 장면 p_scene 값 전부 + '아무것도 안 잡는' 값(최댓값 바로 위). 정상 장면 오탐율 ≤ target_fpr 인 후보 중
    recall 최대, 동률이면 높은 기준값. 정상 장면이 없으면 오탐율 0 으로 본다."""
    scores = scene_scores(rows, p, allow_excluded=allow_excluded)
    return pick_threshold_from_scores(scores, target_fpr)


def pick_threshold_from_scores(scores: dict[str, tuple[float, bool]], target_fpr: float = 0.05) -> tuple[float, dict]:
    """pick_scene_threshold 의 본체(장면 점수에서)."""
    if not scores:
        return 0.5, {"recall": None, "fpr": None, "n_pos": 0, "n_neg": 0, "target_fpr": target_fpr}
    vals = sorted({s for s, _ in scores.values()})
    cands = vals + [math.nextafter(vals[-1], math.inf)]
    best_t, best_m, best_key = cands[-1], None, None
    for t in cands:
        m = scene_metrics_at(scores, t)
        fpr = m["fpr"] or 0.0
        if fpr > target_fpr + 1e-12:
            continue
        key = (m["recall"] or 0.0, t)
        if best_key is None or key > best_key:
            best_t, best_m, best_key = t, m, key
    assert best_m is not None  # 맨 위 후보는 오탐 0
    return best_t, best_m | {"target_fpr": target_fpr, "n_scenes": len(scores)}


def scene_auroc(scores: dict[str, tuple[float, bool]]) -> float | None:
    """장면 AUROC(Mann–Whitney, 동점 0.5). 양성·음성 장면 중 하나라도 없으면 None."""
    pos = [s for s, g in scores.values() if g]
    neg = [s for s, g in scores.values() if not g]
    if not pos or not neg:
        return None
    return _auc(pos, neg)


def _auc(pos: Sequence[float], neg: Sequence[float]) -> float:
    win = 0.0
    for a in pos:
        for b in neg:
            win += 1.0 if a > b else 0.5 if a == b else 0.0
    return win / (len(pos) * len(neg))


def bootstrap_auroc(scores: dict[str, tuple[float, bool]], n: int = 2000, seed: int = 0,
                    alpha: float = 0.05) -> dict:
    """층화 bootstrap(양성·음성 장면을 각각 복원 추출, 수 유지) 장면 AUROC 의 (1−alpha) 백분위 구간."""
    pos = sorted(s for s, g in scores.values() if g)
    neg = sorted(s for s, g in scores.values() if not g)
    if not pos or not neg:
        return {"auroc": None, "lo": None, "hi": None, "n": n, "seed": seed}
    rng = random.Random(f"bootstrap_auroc:{seed}")
    vals = sorted(_auc([rng.choice(pos) for _ in pos], [rng.choice(neg) for _ in neg]) for _ in range(n))
    lo = vals[max(0, int(math.floor(alpha / 2 * n)))]
    hi = vals[min(n - 1, int(math.ceil((1 - alpha / 2) * n)) - 1)]
    return {"auroc": _r(_auc(pos, neg)), "lo": _r(lo), "hi": _r(hi), "n": n, "seed": seed,
            "n_pos": len(pos), "n_neg": len(neg), "stratified": True}


def scene_topk_scores(rows: Sequence[dict], p: Sequence[float], k: int) -> dict[str, tuple[float, bool]]:
    """집계 진단용: 장면 점수 = 상위 k 개 p(모순)의 평균(쌍이 k 개보다 적으면 있는 것의 평균). '제외' 행 포함."""
    by: dict[str, list[float]] = defaultdict(list)
    pos: dict[str, bool] = {}
    for r, pr in zip(rows, p):
        key = scene_key(r)
        by[key].append(float(pr))
        given = r.get("scene_positive")
        pos[key] = pos.get(key, False) or (bool(given) if given is not None else r.get("label") == CONTRA)
    return {key: (sum(sorted(v, reverse=True)[:k]) / len(sorted(v, reverse=True)[:k]), pos[key]) for key, v in by.items()}


def inject_fraction(rows: list[dict], frac: float, seed: int) -> list[dict]:
    """source=inject 행만 contrast_group(없으면 group_key) 단위로 frac 만큼 남긴다. 나머지 행은 그대로.
    같은 시드에서 작은 비율은 큰 비율의 부분집합(train_fraction 과 같은 포개짐)."""
    if not 0 < frac <= 1:
        raise ValueError("inject_frac 은 (0, 1]")
    if frac >= 1.0:
        return list(rows)

    def g(r: dict) -> str:
        return f"{r.get('work') or ''}|{r.get('contrast_group') or group_key(r)}"
    groups = sorted({g(r) for r in rows if r.get("source") == "inject"})
    random.Random(f"inject_frac:{seed}").shuffle(groups)
    keep = set(groups[:max(1, math.ceil(len(groups) * frac))]) if groups else set()
    return [r for r in rows if r.get("source") != "inject" or g(r) in keep]


# ---------------------------------------------------------------- 시드·조기 종료

def select_seed(results: Sequence[dict]) -> dict:
    """검증 장면 recall(오탐 ≤ 목표에서) 최대, 동률은 검증 쌍 NLL 이 낮은 것, 그래도 같으면 앞선 시드."""
    if not results:
        raise ValueError("시드 결과가 없습니다")

    def key(r: dict) -> tuple:
        rec = r.get("scene_recall")
        n = r.get("val_nll")
        return (-(rec if rec is not None else -1.0), n if n is not None else math.inf)
    return min(results, key=key)


@dataclass
class EarlyStopper:
    """검증 쌍 NLL 이 patience 에폭 연속 나아지지 않으면 멈춘다. best_epoch 은 0부터."""
    patience: int
    best: float = math.inf
    best_epoch: int = -1
    bad: int = 0
    history: list[float] = field(default_factory=list)
    improved: bool = False      # 마지막 step 이 최선값을 갱신했는가

    def step(self, epoch: int, value: float) -> bool:
        """True 면 멈춘다. 이번 값이 최선이면 self.improved 가 True."""
        self.history.append(value)
        self.improved = value < self.best - 1e-9
        if self.improved:
            self.best, self.best_epoch, self.bad = value, epoch, 0
            return False
        self.bad += 1
        return self.patience > 0 and self.bad >= self.patience


def package_versions(names: Sequence[str] = ("torch", "transformers", "tokenizers", "datasets")) -> dict:
    from importlib import metadata
    out = {}
    for n in names:
        try:
            out[n] = metadata.version(n)
        except metadata.PackageNotFoundError:
            out[n] = None
    return out
