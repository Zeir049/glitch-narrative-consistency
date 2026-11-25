"""모델 폴더 하나로 내보낸 쌍(JSONL)을 채점한다 — 영 학습 기준선(G3)·학습 모델의 검증 작품 지표(설계안 §2-4·§3-3).

train_judge.py 검증과 같은 계산: 작품 W 의 실제 장면 행(split=real) → HFBackend.predict(서비스와 같은 로드 조건:
meta precision·attn·padding·max_length, 온도) → p(모순).
  장면 지표  p_scene = 장면의 **모든** 실제 쌍('제외' 포함 — 서비스는 장면의 쌍을 다 본다)의 max. 쌍 라벨과 무관하다.
             "정상 장면 오탐 ≤ target-fpr 에서 recall 최대" 기준값과 그 지표, meta threshold(또는 --scene-threshold)에서의 지표,
             장면 AUROC 와 층화 bootstrap(양성·음성 장면 각각 복원 추출) 95% 구간. 옛 방식('제외' 뺀 max)은 scene_drop_excluded.
  쌍 지표    '제외' 뺀 행: 쌍 PR-AUC(모순 대 나머지)와 양성 비율, NLL, 쌍 ECE(train_lib.pair_calibration).
  --agg-diagnostics  정상·양성 장면 max 분포(p50/p95), 상위 2·3개 평균 집계의 장면 AUROC(진단만 — 주 지표는 max).
확률 배열(float64)의 sha256 을 남긴다 — 같은 입력을 다른 프로세스에서 다시 돌려 비교하면 재기동 결정성(G2)이다.

**평가 작품에는 쓰지 않는다**(G6 전, 설계안 §2-6). --work 가 --eval-work 면 거부하고, 마지막 1회만 --allow-eval-work 로
우회한다(경고 로그). 그때 기준값은 검증 작품에서 정한 값을 --scene-threshold 로 넘긴다(평가 작품에서 다시 고르지 않는다).

  CUBLAS_WORKSPACE_CONFIG=:4096:8 PYTHONIOENCODING=utf-8 python scripts/eval/score_pairs.py \\
      --model-dir C:/judge_models/nli_klue_roberta_base/_nli --pairs C:/judge_models/data_q3fold/pairs_q1.jsonl --work q1
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import train_lib as TL  # noqa: E402

logger = logging.getLogger("score_pairs")


def all_real_rows(rows: list[dict], work: str) -> list[dict]:
    """작품의 실제 장면 행 전부('제외' 포함) — 장면 점수용."""
    return [r for r in rows if r.get("work") == work and r.get("split") == "real"]


def real_rows(rows: list[dict], work: str) -> list[dict]:
    return [r for r in all_real_rows(rows, work) if r["label"] != TL.EXCLUDED]


def check_rows_pairs_version(rows: list[dict], model_dir: str) -> str:
    """쌍 파일의 pairs_version(없으면 "2")이 하나이고 모델 meta 의 판과 같아야 한다(v2 모델에 v3 글을 넣지 않게)."""
    got = {str(r.get("pairs_version") or "2") for r in rows}
    meta_path = Path(model_dir) / "judge_meta.json"
    want = str(json.loads(meta_path.read_text(encoding="utf-8")).get("pairs_version") or "2") if meta_path.exists() else None
    if len(got) != 1 or (want is not None and got != {want}):
        raise SystemExit(f"쌍 파일 pairs_version {sorted(got)} 이 모델 meta {want!r} 와 다르다: {model_dir}")
    return got.pop()


def agg_diagnostics(rows: list[dict], p_contra: list[float]) -> dict:
    """집계 진단(검증 작품만): 장면 max 분포와 top-k 평균 집계의 장면 AUROC."""
    mx = TL.scene_scores(rows, p_contra, allow_excluded=True)
    out = {"max_dist": {name: TL.quantiles([s for s, g in mx.values() if g == flag], (0.5, 0.95))
                        for name, flag in (("positive", True), ("normal", False))},
           "auroc": {"max": TL.scene_auroc(mx)}}
    for k in (2, 3):
        out["auroc"][f"top{k}_mean"] = TL.scene_auroc(TL.scene_topk_scores(rows, p_contra, k))
    return out


def score(rows: list[dict], p_contra: list[float], probs: list[list[float]], target_fpr: float,
          meta_threshold: float | None, *, given_threshold: float | None = None, bootstrap: int = 2000,
          bootstrap_seed: int = 0, diagnostics: bool = False, aggregate: str = "max") -> dict:
    """torch 불필요(테스트 대상). rows 에 '제외' 행이 있어도 된다 — 장면 지표에만 들어가고 쌍 지표에서는 빠진다."""
    keep = [i for i, r in enumerate(rows) if r["label"] != TL.EXCLUDED]
    prow = [rows[i] for i in keep]
    pp, pprobs = [p_contra[i] for i in keep], [probs[i] for i in keep]
    gold = [TL.L2I[r["label"]] for r in prow]
    is_pos = [g == TL.L2I["모순"] for g in gold]
    scores = TL.scene_scores_agg(rows, p_contra, aggregate)
    thr, at_fpr = TL.pick_threshold_from_scores(scores, target_fpr)
    thr_d, at_fpr_d = TL.pick_scene_threshold(prow, pp, target_fpr)
    out = {"n_pairs": len(prow), "n_pairs_all": len(rows), "n_excluded": len(rows) - len(prow),
           "n_scenes": len(scores), "scene_rows": "all_real_rows_incl_excluded", "aggregate": aggregate,
           "scene_threshold": thr, "scene_at_target_fpr": at_fpr,
           "scene_auroc": TL.bootstrap_auroc(scores, bootstrap, bootstrap_seed) if bootstrap else
           {"auroc": TL.scene_auroc(scores)},
           "scene_drop_excluded": at_fpr_d | {"threshold": thr_d,
                                             "auroc": TL.scene_auroc(TL.scene_scores(prow, pp))},
           "pair_pr_auc": TL.pr_auc(pp, is_pos), "pair_pos_rate": TL._r(sum(is_pos) / len(is_pos)) if is_pos else None,
           "pair_n_pos": sum(is_pos),
           "pair_calibration": TL.pair_calibration(pprobs, gold), "nll": TL.nll(pprobs, gold)}
    if meta_threshold is not None:
        out["scene_at_meta_threshold"] = TL.scene_metrics_at(scores, meta_threshold) | {"threshold": meta_threshold}
    if given_threshold is not None:
        out["scene_at_given_threshold"] = TL.scene_metrics_at(scores, given_threshold) | {"threshold": given_threshold}
    if diagnostics:
        out["aggregation"] = agg_diagnostics(rows, p_contra)
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--pairs", type=Path, required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--eval-work", default="q3", help="이 작품이면 거부한다(G6 전 평가 작품 금지)")
    ap.add_argument("--allow-eval-work", action="store_true", help="평가 작품 1회 채점(마지막 단계만, 경고 로그)")
    ap.add_argument("--scene-threshold", type=float, help="이 기준값에서의 장면 지표도(평가 작품은 검증 작품 값을 넘긴다)")
    ap.add_argument("--bootstrap", type=int, default=2000, help="장면 AUROC 층화 bootstrap 횟수(0 이면 생략)")
    ap.add_argument("--bootstrap-seed", type=int, default=0)
    ap.add_argument("--agg-diagnostics", action="store_true", help="max 분포·top-k 평균 AUROC(검증 작품 진단용)")
    ap.add_argument("--scores-out", type=Path, help="행별 p(모순)·장면·라벨을 JSONL 로")
    ap.add_argument("--target-fpr", type=float, default=0.05)
    ap.add_argument("--aggregate", choices=list(TL.AGGREGATES), default="max",
                    help="장면 점수 집계(장면 지표·기준값 모두): max | hyp_contrast(가설별 max−평균의 최댓값) | hyp_contrast_past05(과거 p×0.5)")
    ap.add_argument("--device", choices=["auto", "cuda", "cpu"], default="auto")
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--max-length", type=int, help="토큰 상한 덮어쓰기(기본 meta) — 외부 단계 모델(meta 128)을 Weave 쌍 384 로 채점할 때")
    ap.add_argument("--hyp-only", action="store_true", help="전제를 빈 문자열로(지름길 기준선 채점)")
    ap.add_argument("--json-out", type=Path)
    ap.add_argument("--ignore-meta-pairs-version", action="store_true",
                    help="모델 meta 의 pairs_version 과 쌍 파일 판을 대조하지 않는다(Weave 쌍을 본 적 없는 기초 모델의 v3 영 학습 채점)")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if a.work == a.eval_work:
        if not a.allow_eval_work:
            raise SystemExit(f"평가 작품({a.eval_work})은 G6 전에 채점하지 않는다(마지막 1회만 --allow-eval-work)")
        logger.warning("평가 작품 %s 를 채점한다(--allow-eval-work). 기준값·온도는 검증 작품 값만 쓴다 — 이 결과로 다시 고르지 않는다",
                       a.eval_work)
        if a.agg_diagnostics:
            raise SystemExit("--agg-diagnostics 는 검증 작품에만 쓴다")
    if os.environ.get("CUBLAS_WORKSPACE_CONFIG") is None:
        os.environ["CUBLAS_WORKSPACE_CONFIG"] = ":4096:8"
    import numpy as np

    from app.pipeline.learned_judge import HFBackend
    rows = all_real_rows(TL.read_jsonl(a.pairs), a.work)
    if not rows:
        raise SystemExit(f"{a.pairs} 에 {a.work} 실제 장면 행이 없다")
    if a.ignore_meta_pairs_version:
        logger.warning("쌍 파일 pairs_version 과 모델 meta 를 대조하지 않는다(--ignore-meta-pairs-version) — 영 학습 기준선용")
    else:
        check_rows_pairs_version(rows, a.model_dir)
    if a.hyp_only:
        rows = TL.hyp_only(rows)
    be = HFBackend(a.model_dir, batch_size=a.batch_size, device=a.device, max_length=a.max_length)
    pairs = [SimpleNamespace(premise_text=r["premise"], hypothesis_text=r["hypothesis"]) for r in rows]
    t0 = time.perf_counter()
    probs = be.predict(pairs)
    sec = time.perf_counter() - t0
    arr = np.asarray(probs, dtype=np.float64)
    pc = [p[0] for p in probs]
    lv = sorted({r.get("label_version", 1) for r in rows})
    out = {"model_dir": a.model_dir, "model_hash": be.model_hash, "work": a.work, "pairs_file": str(a.pairs),
           "pairs_sha256": TL.sha256_file(a.pairs), "label_version": lv, "hyp_only": a.hyp_only,
           "eval_work_scored": a.work == a.eval_work,
           "rows_pairs_version": sorted({str(r.get("pairs_version") or "2") for r in rows}),
           "ignore_meta_pairs_version": a.ignore_meta_pairs_version, "max_length_override": a.max_length, "predict_sec": round(sec, 2),
           "probs_sha256": hashlib.sha256(arr.tobytes()).hexdigest(), "temperature": be.meta.get("temperature"),
           **score(rows, pc, probs, a.target_fpr, be.meta.get("threshold"), given_threshold=a.scene_threshold,
                   bootstrap=a.bootstrap, bootstrap_seed=a.bootstrap_seed, diagnostics=a.agg_diagnostics,
                   aggregate=a.aggregate),
           "env": be.env()}
    if a.scores_out:
        a.scores_out.parent.mkdir(parents=True, exist_ok=True)
        with a.scores_out.open("w", encoding="utf-8", newline="\n") as f:
            for r, x in zip(rows, pc):
                f.write(json.dumps({"scene_id": r.get("scene_id"), "scene_positive": r.get("scene_positive"),
                                    "label": r["label"], "label_v1": r.get("label_v1"), "p_contra": x},
                                   ensure_ascii=False) + "\n")
    text = json.dumps(out, ensure_ascii=False, indent=1)
    print(text)
    if a.json_out:
        a.json_out.parent.mkdir(parents=True, exist_ok=True)
        a.json_out.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
