"""judge_latency 합성 회차 — 결정적이고 설계안 규모(사건 30·과거 300)이며 서비스 쌍 생성기를 그대로 탄다(torch 불필요)."""
from __future__ import annotations

import importlib.util
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("judge_latency", ROOT / "scripts" / "eval" / "judge_latency.py")
JL = importlib.util.module_from_spec(spec)
sys.modules["judge_latency"] = JL
spec.loader.exec_module(JL)


def test_synthetic_episode_shape_and_determinism():
    ep = JL.synthetic_episode(0)
    assert sum(len(g["events"]) for g in ep["groups"]) == 30 and len(ep["corpus"]) == 300
    a = JL.scene_pairs(ep)
    b = JL.scene_pairs(JL.synthetic_episode(0))
    assert [[(p.premise_id, p.hypothesis_text, p.premise_text) for p in s] for s in a] == \
           [[(p.premise_id, p.hypothesis_text, p.premise_text) for p in s] for s in b]
    total = sum(len(s) for s in a)
    assert 1500 <= total <= 4000  # 설계안 §0-4 추정(할당량 24, 회차 약 2,660쌍)과 같은 규모
    kinds = Counter(p.premise_kind for s in a for p in s)
    assert {"ledger", "knowledge", "rule", "past", "checker"} <= set(kinds)


def test_ensure_model_dir_requires_meta_or_stub(tmp_path):
    import pytest
    with pytest.raises(SystemExit):
        JL.ensure_model_dir(str(tmp_path), None, "fp16", 256, "bucket")
    (tmp_path / "judge_meta.json").write_text("{}", encoding="utf-8")
    assert JL.ensure_model_dir(str(tmp_path), None, "fp16", 256, "bucket") == tmp_path


def test_pair_coverage_token_stats():
    spec2 = importlib.util.spec_from_file_location("pair_coverage", ROOT / "scripts" / "eval" / "pair_coverage.py")
    PC = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(PC)
    t = PC.token_stats(list(range(1, 301)))
    assert (t["n"], t["max"], t["p50"]) == (300, 300, 151) and t["over_256"] == round(44 / 300, 4) and t["over_384"] == 0.0
    assert t["buckets"] == {"<=128": round(128 / 300, 4), "<=256": round(128 / 300, 4), "<=384": round(44 / 300, 4), "<=512": 0.0, ">512": 0.0}
    assert PC.token_stats([])["n"] == 0


def test_score_pairs_refuses_eval_work_and_scores():
    import pytest
    spec3 = importlib.util.spec_from_file_location("score_pairs", ROOT / "scripts" / "eval" / "score_pairs.py")
    SP = importlib.util.module_from_spec(spec3)
    spec3.loader.exec_module(SP)
    with pytest.raises(SystemExit, match="평가 작품"):
        SP.main(["--model-dir", "x", "--pairs", "x", "--work", "q3"])
    with pytest.raises(SystemExit, match="검증 작품에만"):
        SP.main(["--model-dir", "x", "--pairs", "x", "--work", "q3", "--allow-eval-work", "--agg-diagnostics"])
    rows = [{"work": "q1", "split": "real", "label": lab, "scene_id": s, "scene_positive": pos}
            for s, pos, lab in (("A", True, "모순"), ("A", True, "무관"), ("B", False, "정상"), ("C", False, "무관"),
                                ("D", True, "제외"))]
    rr = SP.real_rows(rows + [{"work": "q1", "split": "train", "label": "모순", "scene_id": "Z"}], "q1")
    assert len(rr) == 4
    p = [0.9, 0.1, 0.2, 0.3]
    probs = [[x, (1 - x) / 2, (1 - x) / 2] for x in p]
    out = SP.score(rr, p, probs, 0.05, 0.5)
    assert out["scene_at_target_fpr"]["recall"] == 1.0 and out["scene_at_target_fpr"]["fp"] == 0
    assert out["scene_at_meta_threshold"]["tp"] == 1 and out["n_scenes"] == 3
    assert out["scene_auroc"]["auroc"] == 1.0 and out["pair_n_pos"] == 1
    # '제외' 행은 장면 max 에 들어가고(서비스와 같다) 쌍 지표에서는 빠진다
    ar = SP.all_real_rows(rows, "q1")
    assert len(ar) == 5
    pa = p + [0.95]
    out2 = SP.score(ar, pa, [[x, (1 - x) / 2, (1 - x) / 2] for x in pa], 0.05, 0.5, given_threshold=0.9,
                    diagnostics=True)
    assert out2["n_pairs"] == 4 and out2["n_excluded"] == 1 and out2["n_scenes"] == 4
    assert out2["scene_at_given_threshold"]["tp"] == 2 and out2["scene_drop_excluded"]["n_scenes"] == 3
    assert out2["pair_pr_auc"] == out["pair_pr_auc"]
    assert set(out2["aggregation"]["auroc"]) == {"max", "top2_mean", "top3_mean"}
