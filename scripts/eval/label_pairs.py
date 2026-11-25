# -*- coding: utf-8 -*-
"""갈래 A — LLM 쌍 라벨링 (FactTrack식). 확정 회차를 재생해 build_pairs 로 쌍을 뽑고 LLM 두 번으로 라벨을 단다.

근거: docs/learned-judge-prior-work.md §2-1 표 갈래 A, docs/learned-judge-training-design.md §0·§2-2.

절차
  1) 쌍 풀    작품마다 k=2..5: '1..k-1화 확정 원장 + k화 사건'을 재생(datagen_common.replay_upto —
              judge_eval.load_context 와 같은 매핑·timelines(timelines_of)·knows accumulate·checker 항목, 미래 규칙 제외)하고
              장면 묶음마다 app.pipeline.pairs.build_pairs(서비스 추론과 같은 함수)로 쌍을 만든다.
  2) 표본    가설당 전제 상위 --top-n(기본 24, build_pairs 순서 = kind 우선순위), 장면당 --scene-cap,
              작품당 --target(기본 5,000) 쌍. 전제 kind 분포를 유지하는 층화 표본(최대 잉여 배분), --seed 고정.
              풀이 목표보다 작으면 전부 쓴다.
  3) 라벨    쌍 하나당 JSON {"label": 모순|정상|무관|판단불가, "confidence", "reason", "evidence_quote"}.
              두 번(--models a,b 면 모델 2개, 하나면 같은 모델 2회). 두 번째는 전제·가설 순서를 바꾼 변형 프롬프트
              (녹화 키가 겹치지 않게, 위치 편향을 덜게).
  4) 병합    두 라벨이 같고 판단불가가 아니며 둘 다 --min-confidence 이상 → 그 라벨. 그 밖 → '제외'(사유 기록).
              일치율·Cohen κ 를 보고한다.
  5) 출력    --out JSONL(모순/정상/무관만 — train_judge.py 가 그대로 읽는다) + <out>.excluded.jsonl('제외')
              + <out>.meta.json(표본·일치율·토큰·비용·설정). --dry-run 이면 LLM 없이 표본과 예상 비용만.

출력 한 줄(export_pairs.py 와 호환 + 추가 필드)
  premise, hypothesis, label, premise_kind, premise_id, event_id, entity, field,
  work, scene_id, episode, group_id, case_id(=group_id, train_judge 의 분할 단위), contrast_group(None),
  source="llm_label", split(--split, 기본 train), keyed=True, pair_id, confidence, llm_labels[...]

녹화: WEAVE_LLM_RECORD=record 로 남기고, 다시 돌릴 때 replay 면 네트워크 0회(녹화 키 = role + sha256(프롬프트)).
role 은 'pair_label'. 녹화 키에 모델 이름이 없다 — record 모드의 재사용(datagen_common.call_json)은 녹화 항목의
`model` 이 지금 모델과 다르면 miss 로 보고 새로 부른다. 코어 replay 모드는 모델을 보지 않으므로, replay 로 다른 모델을
재생하려면 녹화 폴더(WEAVE_LLM_RECORD_DIR)를 나눈다.
대량 실행 가드: 표본이 시험 규모(TRIAL_MAX=50쌍)를 넘으면 --run 없이는 LLM 을 부르지 않고 추정만 보여 주고 멈춘다
(전체 실행은 승인 뒤 --run).

실행
  PYTHONIOENCODING=utf-8 python scripts/eval/label_pairs.py --work wm --target 50 --dry-run
  WEAVE_LLM_RECORD=record WEAVE_LLM_RECORD_DIR=build/llm_record \\
    PYTHONIOENCODING=utf-8 python scripts/eval/label_pairs.py --work wm --target 50 --out build/llm_pairs/wm.jsonl
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import random
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
if str(EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(EVAL_DIR))

import datagen_common as C  # noqa: E402

from app.pipeline.pairs import build_pairs  # noqa: E402

logger = logging.getLogger("label_pairs")

LABELS = ("모순", "정상", "무관")      # train_judge.LABELS 와 같은 순서
LLM_LABELS = LABELS + ("판단불가",)
EXCLUDED = "제외"
ROLE = "pair_label"
LABEL_MAX_TOKENS = 600
TRIAL_MAX = 50          # README "시험 규모" — 이보다 큰 실행은 --run 이 있어야 LLM 을 부른다

SYSTEM = """당신은 장편 서사의 설정 모순을 가려내는 검수자입니다. 이야기의 '전제'(앞 회차까지 확정된 사실·규칙·지식·사건)
하나와 '가설'(이번 회차 사건이 전제로 삼는 상태 또는 사건 서술) 하나를 받아 둘의 관계를 한 가지 라벨로 판정합니다.

라벨 정의
- 모순: 전제가 참이면 가설이 성립할 수 없다. 같은 대상·같은 시점에 대해 둘이 동시에 참일 수 없어야 한다.
- 정상: 전제와 가설이 같은 대상·사실에 관련되고, 서로 양립한다(전제가 가설을 뒷받침하거나 충돌 없이 이어진다).
- 무관: 전제에 가설을 판단하는 데 쓸 정보가 없다(다른 인물·다른 사실·다른 주제).
- 판단불가: 관련은 있지만 주어진 글만으로는 모순인지 정상인지 정할 수 없다.

어려운 경우 지침 (오탐을 내지 마세요)
- 회복·변화: 전제의 상태(부상·소지·위치·관계)가 그 뒤에 바뀔 수 있는 종류이고, 가설이 시간이 지난 뒤의 일이면
  바뀐 것만으로는 모순이 아니다. 전제가 '영구적'이거나 '아직 해소되지 않음'이 분명할 때만 모순이다.
- 일시적 상태: [일시적 — 해소 시 …] 표시가 있는 전제는 해소된 뒤의 값과 비교한다.
- 장면 전환·시점 차이: 다른 장소·다른 시점의 서술은 위치·상태가 달라도 모순이 아니다.
- 지식: 인물이 어떤 정보를 '안다'는 전제는, 그 인물이 모른 척하거나 말하지 않는 것과 모순이 아니다.
  인물 스스로 '모른다'고 진술하고 서술이 그것을 사실로 다룰 때만 모순이다. 거짓말·떠보기·반어는 정상이다.
- 습관·합의·경향을 말하는 규칙은 한 번 어긴 행동과 모순이 아니다. 물리·제도적으로 불가능하다고 정한 규칙만 모순 근거가 된다.
- 표현 차이(같은 대상의 다른 이름·어림수·요약)는 정상이다.
- 같은 주제라도 전제가 가설의 핵심 주장에 대해 아무 말도 하지 않으면 무관이다.

출력: 아래 JSON 객체 하나만. 설명문·코드펜스 없이.
{"label": "모순|정상|무관|판단불가", "confidence": 0~1 사이 숫자, "reason": "한 문장", "evidence_quote": "판정 근거가 된 전제의 발췌(없으면 빈 문자열)"}"""


def user_prompt(premise: str, hypothesis: str, variant: int) -> str:
    """variant 0: 전제 → 가설, 1: 가설 → 전제(같은 정보, 순서만 다름)."""
    p = f"[전제]\n{premise}"
    h = f"[가설]\n{hypothesis}"
    body = f"{p}\n\n{h}" if variant == 0 else f"{h}\n\n{p}"
    return f"{body}\n\n전제와 가설의 관계를 판정해 JSON 하나로 답하세요."


# ---------------------------------------------------------------- 쌍 풀·표본

def pair_id(work: str, k: int, p) -> str:  # noqa: ANN001
    raw = "\x00".join([work, str(k), p.event_id, p.premise_id, p.premise_text, p.hypothesis_text])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def pool_rows(work: str, *, ks: tuple[int, ...] = (2, 3, 4, 5), top_n: int = 24, scene_cap: int = 400,
              knows_mode: str = "accumulate", ledger_dir: Path | None = None,
              registry_path: Path | None = None, context_loader=None) -> list[dict]:  # noqa: ANN001
    """작품의 후보 쌍(라벨 없음). 같은 입력 → 같은 목록(순서까지)."""
    _w_checker, w_pipe, _c, _n = C.JE._core()
    load = context_loader or (lambda w, k: C.replay_upto(w, k, knows_mode=knows_mode, ledger_dir=ledger_dir,
                                                         registry_path=registry_path))
    rows: list[dict] = []
    for k in ks:
        try:
            ctx = load(work, k)
        except ValueError as e:
            logger.warning("%s", e)
            continue
        for g in w_pipe.scene_groups(ctx.events, "scene"):
            ids = {e.get("event_id") for e in g["events"]}
            pairs = build_pairs(g, ctx.ledger, ctx.corpus, ctx.registry,
                                [c for c in ctx.checker_items if c["event_id"] in ids])
            per_hyp: Counter = Counter()
            kept = 0
            gid = f"{work}:ep{k}:{g['key']}"
            for p in pairs:
                hk = (p.event_id, p.entity, p.field, p.hypothesis_text)
                if per_hyp[hk] >= top_n:
                    continue
                if kept >= scene_cap:
                    break
                per_hyp[hk] += 1
                kept += 1
                rows.append({"premise": p.premise_text, "hypothesis": p.hypothesis_text,
                             "premise_kind": p.premise_kind, "premise_id": p.premise_id,
                             "event_id": p.event_id, "entity": p.entity, "field": p.field,
                             "work": work, "scene_id": g["key"], "episode": k, "group_id": gid,
                             "case_id": gid, "contrast_group": None, "pair_id": pair_id(work, k, p),
                             "premise_rank": per_hyp[hk]})
    return rows


def allocate(counts: dict[str, int], target: int) -> dict[str, int]:
    """kind 분포 비례 배분(최대 잉여). 각 kind 는 자기 풀 크기를 넘지 않는다. 결정적."""
    total = sum(counts.values())
    if target >= total:
        return dict(counts)
    quota = {k: target * n / total for k, n in counts.items()}
    out = {k: min(counts[k], int(q)) for k, q in quota.items()}
    rest = target - sum(out.values())
    order = sorted(counts, key=lambda k: (-(quota[k] - int(quota[k])), k))
    while rest > 0:
        moved = False
        for k in order:
            if rest == 0:
                break
            if out[k] < counts[k]:
                out[k] += 1
                rest -= 1
                moved = True
        if not moved:
            break
    return out


def stratified_sample(rows: list[dict], target: int, seed: int) -> list[dict]:
    """premise_kind 층화. 결과는 pair_id 정렬(입력 순서와 무관하게 같은 집합 → 같은 순서)."""
    by_kind: dict[str, list[dict]] = defaultdict(list)
    for r in sorted(rows, key=lambda r: r["pair_id"]):
        by_kind[r["premise_kind"]].append(r)
    alloc = allocate({k: len(v) for k, v in by_kind.items()}, target)
    rng = random.Random(seed)
    out = []
    for kind in sorted(by_kind):
        out += rng.sample(by_kind[kind], alloc[kind])
    return sorted(out, key=lambda r: r["pair_id"])


# ---------------------------------------------------------------- 라벨

def parse_label(res: dict) -> dict:
    """complete_json 결과 → {"label", "confidence", "reason", "evidence_quote", "status"}."""
    if res.get("status") != "ok" or not isinstance(res.get("data"), dict):
        return {"status": "failed", "label": None, "confidence": None, "reason": res.get("message") or "",
                "evidence_quote": ""}
    d = res["data"]
    label = str(d.get("label") or "").strip()
    if label not in LLM_LABELS:
        return {"status": "bad_label", "label": label or None, "confidence": None,
                "reason": str(d.get("reason") or ""), "evidence_quote": ""}
    try:
        conf = max(0.0, min(1.0, float(d.get("confidence"))))
    except (TypeError, ValueError):
        conf = None
    return {"status": "ok", "label": label, "confidence": conf, "reason": str(d.get("reason") or "").strip(),
            "evidence_quote": str(d.get("evidence_quote") or "").strip()}


def merge_labels(a: dict, b: dict, *, min_confidence: float = 0.0) -> tuple[str, str | None, float | None]:
    """(최종 라벨, 제외 사유, 신뢰도). 규칙:
      둘 중 하나라도 실패/형식 오류 → 제외(실패)
      판단불가가 하나라도 → 제외(판단불가)
      라벨 불일치 → 제외(불일치)
      신뢰도가 하나라도 min_confidence 미만(값이 없으면 0으로 본다) → 제외(저신뢰)
      그 밖 → 그 라벨, 신뢰도 = 두 값의 평균
    """
    if a.get("status") != "ok" or b.get("status") != "ok":
        return EXCLUDED, "실패", None
    if "판단불가" in (a["label"], b["label"]):
        return EXCLUDED, "판단불가", None
    if a["label"] != b["label"]:
        return EXCLUDED, "불일치", None
    ca, cb = a.get("confidence") or 0.0, b.get("confidence") or 0.0
    if min(ca, cb) < min_confidence:
        return EXCLUDED, "저신뢰", None
    return a["label"], None, round((ca + cb) / 2, 4)


def agreement(pairs: list[tuple[dict, dict]]) -> dict:
    """두 라벨러 모두 형식이 맞은 쌍에서 일치율과 Cohen κ(4범주)."""
    both = [(a["label"], b["label"]) for a, b in pairs if a.get("status") == "ok" and b.get("status") == "ok"]
    n = len(both)
    if not n:
        return {"n_both_ok": 0, "agreement": None, "kappa": None}
    po = sum(1 for x, y in both if x == y) / n
    ca, cb = Counter(x for x, _ in both), Counter(y for _, y in both)
    pe = sum(ca[c] * cb[c] for c in LLM_LABELS) / (n * n)
    kappa = (po - pe) / (1 - pe) if pe < 1 else 1.0
    confusion = Counter(f"{x}/{y}" for x, y in both)
    return {"n_both_ok": n, "agreement": round(po, 4), "kappa": round(kappa, 4),
            "confusion": dict(sorted(confusion.items()))}


def label_rates(labeled: list[dict]) -> dict:
    """보고 필수 비율. 불일치를 버리면 어려운 쌍이 빠져 분포가 쉬워지므로 제외율을 숨기지 않는다.
    원작 원장은 내부적으로 거의 일관하므로 '모순' 라벨은 대부분 오탐일 수 있다 — 표본 검수 대상."""
    n = len(labeled)
    kept = [r for r in labeled if r["label"] != EXCLUDED]
    why = Counter(r.get("excluded_reason") for r in labeled if r["label"] == EXCLUDED)
    both_ok = [r for r in labeled if all(x.get("status") == "ok" for x in r["llm_labels"])]
    return {"n": n,
            "contradiction_rate_kept": round(sum(r["label"] == "모순" for r in kept) / len(kept), 4) if kept else None,
            "contradiction_any_labeler": sum(any(x.get("label") == "모순" for x in r["llm_labels"]) for r in labeled),
            "excluded_rate": round((n - len(kept)) / n, 4) if n else None,
            "disagreement_rate_among_both_ok": (round(why["불일치"] / len(both_ok), 4) if both_ok else None),
            "undecidable_rate": round(why["판단불가"] / n, 4) if n else None,
            "failed_rate": round(why["실패"] / n, 4) if n else None}


def label_rows(rows: list[dict], models: list[str], *, concurrency: int = 3, usage: C.Usage | None = None,
               caller=None, min_confidence: float = 0.0) -> tuple[list[dict], list[tuple[dict, dict]]]:  # noqa: ANN001
    """rows 에 두 번 라벨을 단다. models 가 하나면 같은 모델 2회(variant 0, 1).
    caller(system, user, llm, role) 를 주면 그것으로 부른다(테스트용 — 기본은 C.call_json)."""
    m2 = (models + models)[:2]
    llms = [C.llm_for(m, max_tokens=LABEL_MAX_TOKENS) for m in m2]
    call = caller or (lambda s, u, llm, role: C.call_json(s, u, llm, role=role, usage=usage))

    def one(job: tuple[int, int]) -> tuple[int, int, dict]:
        i, v = job
        r = rows[i]
        res = call(SYSTEM, user_prompt(r["premise"], r["hypothesis"], v), llms[v], ROLE)
        return i, v, parse_label(res) | {"model": m2[v], "variant": v}

    jobs = [(i, v) for i in range(len(rows)) for v in (0, 1)]
    got: dict[tuple[int, int], dict] = {}
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
        for n, (i, v, lab) in enumerate(ex.map(one, jobs), 1):
            got[(i, v)] = lab
            if n % 50 == 0 or n == len(jobs):
                logger.info("라벨 %d/%d 호출 (%.0fs)", n, len(jobs), time.time() - t0)
    out, pairs = [], []
    for i, r in enumerate(rows):
        a, b = got[(i, 0)], got[(i, 1)]
        pairs.append((a, b))
        label, why, conf = merge_labels(a, b, min_confidence=min_confidence)
        out.append(r | {"label": label, "excluded_reason": why, "confidence": conf, "keyed": label != EXCLUDED,
                        "llm_labels": [a, b]})
    return out, pairs


# ---------------------------------------------------------------- 비용 추정

def estimate(rows: list[dict], models: list[str], *, chars_per_token: float, out_tokens: int,
             price: tuple[float | None, float | None]) -> dict:
    sys_tok = C.chars_to_tokens(len(SYSTEM), chars_per_token)
    in_tok = sum(sys_tok + C.chars_to_tokens(len(user_prompt(r["premise"], r["hypothesis"], 0)), chars_per_token)
                 for r in rows) * 2
    out_tok = len(rows) * 2 * out_tokens
    m2 = (models + models)[:2]
    usd, unknown = 0.0, []
    for m in m2:
        p = C.price_of(m, price)
        if p is None:
            unknown.append(m)
            continue
        usd += (in_tok / 2) / 1e6 * p[0] + (out_tok / 2) / 1e6 * p[1]
    return {"pairs": len(rows), "calls": len(rows) * 2, "input_tokens": in_tok, "output_tokens": out_tok,
            "chars_per_token": chars_per_token, "out_tokens_per_call": out_tokens,
            "usd": round(usd, 4), "unpriced_models": unknown}


# ---------------------------------------------------------------- 실행

def guard_run_size(n_pairs: int, run: bool, est: dict) -> None:
    """대량 실행 가드: --run 없이는 시험 규모(TRIAL_MAX 쌍)까지만 부른다(README '승인 필요 지점')."""
    if n_pairs > TRIAL_MAX and not run:
        raise SystemExit(f"표본 {n_pairs}쌍 > 시험 규모 {TRIAL_MAX}쌍 — 호출 {est['calls']}회, 추정 ${est['usd']} "
                         f"(입력 {est['input_tokens']}·출력 {est['output_tokens']} 토큰). 승인을 받은 뒤 --run 을 붙이거나 "
                         f"--target {TRIAL_MAX} 이하로 돌리세요.")


def per_call(tokens: int, calls: int) -> float | None:
    return round(tokens / calls, 1) if calls else None


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--work", required=True, choices=C.ALL_DATA_WORKS)
    ap.add_argument("--ledger-dir", type=Path, help="원장 폴더(episode_NN.json) — 기본은 작품별 확정 원장")
    ap.add_argument("--registry", type=Path, help="레지스트리 파일 — 기본은 작품별 registry_live/entity_registry")
    ap.add_argument("--episodes", default="2,3,4,5", help="판정 회차 k 목록")
    ap.add_argument("--top-n", type=int, default=24, help="가설당 전제 상한")
    ap.add_argument("--scene-cap", type=int, default=400, help="장면(판정 패킷)당 쌍 상한")
    ap.add_argument("--target", type=int, default=5000, help="작품당 목표 쌍 수")
    ap.add_argument("--seed", type=int, default=20261106)
    ap.add_argument("--models", default="", help="쉼표 구분 1~2개. 비우면 설정 judge 모델 2회")
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--min-confidence", type=float, default=0.0)
    ap.add_argument("--split", default="train")
    ap.add_argument("--out", type=Path, help="JSONL (dry-run 이 아니면 필수)")
    ap.add_argument("--dry-run", action="store_true", help="LLM 없이 표본·예상 비용만")
    ap.add_argument("--run", action="store_true",
                    help=f"시험 규모({TRIAL_MAX}쌍)를 넘는 실행 허용 — 비용 추정을 보고하고 승인받은 뒤에만")
    ap.add_argument("--chars-per-token", type=float, default=2.4,
                    help="추정용 — 기본은 시험 실측(WM 50쌍, cpt 2.4)")
    ap.add_argument("--out-tokens", type=int, default=95, help="추정용 호출당 출력 토큰 — 기본은 시험 실측 95")
    ap.add_argument("--price-in", type=float, help="1M 입력 토큰당 USD(단가표에 없는 모델)")
    ap.add_argument("--price-out", type=float, help="1M 출력 토큰당 USD")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    ks = tuple(int(x) for x in a.episodes.split(",") if x.strip())
    models = [m.strip() for m in a.models.split(",") if m.strip()] or [C.default_model()]
    if len(models) > 2:
        raise SystemExit("--models 는 1~2개")
    price = (a.price_in, a.price_out)
    pool = pool_rows(a.work, ks=ks, top_n=a.top_n, scene_cap=a.scene_cap, ledger_dir=a.ledger_dir,
                     registry_path=a.registry)
    rows = stratified_sample(pool, a.target, a.seed)
    sample_meta = {"work": a.work, "episodes": list(ks), "top_n": a.top_n, "scene_cap": a.scene_cap,
                   "target": a.target, "seed": a.seed, "pool": len(pool), "sampled": len(rows),
                   "pool_kinds": dict(Counter(r["premise_kind"] for r in pool)),
                   "sample_kinds": dict(Counter(r["premise_kind"] for r in rows)),
                   "models": (models + models)[:2],
                   "prompt_sha256": hashlib.sha256(SYSTEM.encode("utf-8")).hexdigest()}
    est = estimate(rows, models, chars_per_token=a.chars_per_token, out_tokens=a.out_tokens, price=price)
    if a.dry_run:
        print(json.dumps(sample_meta | {"estimate": est}, ensure_ascii=False, indent=2))
        return
    if a.out is None:
        raise SystemExit("--out 이 필요합니다(또는 --dry-run)")
    guard_run_size(len(rows), a.run, est)

    from app.core.config import settings
    usage = C.Usage()
    t0 = time.time()
    labeled, pairs = label_rows(rows, models, concurrency=a.concurrency, usage=usage,
                                min_confidence=a.min_confidence)
    for r in labeled:
        r.update({"source": "llm_label", "split": a.split})
    keep = [r for r in labeled if r["label"] != EXCLUDED]
    drop = [r for r in labeled if r["label"] == EXCLUDED]
    write_jsonl(a.out, keep)
    excl_path = a.out.with_suffix(".excluded.jsonl")
    write_jsonl(excl_path, drop)
    u = usage.to_dict(price)
    meta = sample_meta | {
        "elapsed_sec": round(time.time() - t0, 1), "record_mode": settings.weave_llm_record,
        "provider": settings.weave_llm_provider, "agreement": agreement(pairs),
        "labels": dict(Counter(r["label"] for r in labeled)),
        "excluded_reasons": dict(Counter(r["excluded_reason"] for r in drop)),
        "rates": label_rates(labeled),
        "usage": u, "estimate_before_run": est,
        "observed_chars_per_token": (round(sum(len(SYSTEM) + len(user_prompt(r["premise"], r["hypothesis"], 0))
                                               for r in rows) * 2 / u["input_tokens"], 3)
                                     if u["input_tokens"] else None),
        # 분모 = usage 가 있는 호출(실패·한도 초과로 usage 가 없는 호출은 토큰 0 이라 평균을 깎는다 — README 실측과 같은 정의)
        "calls_with_usage": u["calls"] - u["missing_usage"],
        "observed_in_tokens_per_call": per_call(u["input_tokens"], u["calls"] - u["missing_usage"]),
        "observed_out_tokens_per_call": per_call(u["output_tokens"], u["calls"] - u["missing_usage"]),
        "observed_out_tokens_per_call_all_calls": per_call(u["output_tokens"], u["calls"]),
        "files": {"pairs": str(a.out), "excluded": str(excl_path)}}
    a.out.with_suffix(".meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("라벨 %s · 제외 %s · 일치율 %s · 토큰 in %s out %s · $%s", meta["labels"], meta["excluded_reasons"],
                meta["agreement"].get("agreement"), u["input_tokens"], u["output_tokens"], u["cost"]["usd"])
    print(json.dumps({k: meta[k] for k in ("sampled", "labels", "excluded_reasons", "rates", "agreement", "usage")},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
