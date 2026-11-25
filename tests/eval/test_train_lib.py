"""scripts/eval/train_lib.py — 학습 judge 학습의 데이터 준비·샘플링·기준값 로직(torch·transformers 없이).

설계안 docs/learned-judge-training-design.md §0-4c(누수), §2-2(가중치·무관 감축·제외), §2-4(장면 기준값), §3-1.
"""
from __future__ import annotations

import importlib.util
import json
import math
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EVAL = ROOT / "scripts" / "eval"


def _load(name: str, path: Path):  # noqa: ANN202
    if name in sys.modules:
        return sys.modules[name]
    if str(EVAL) not in sys.path:
        sys.path.insert(0, str(EVAL))
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


TL = _load("train_lib", EVAL / "train_lib.py")
TJ = _load("train_judge", ROOT / "scripts" / "train_judge.py")


def row(work: str, label: str, group: str, *, scene: str | None = None, premise: str = "전제", **kw) -> dict:
    return {"premise": premise, "hypothesis": f"가설-{group}", "label": label, "work": work,
            "group_id": f"{work}:{group}", "scene_id": scene or group, "case_id": group, **kw}


def corpus() -> list[dict]:
    rows = []
    for w in ("wm", "q1", "q3", "sherlock"):
        for g in range(4):
            rows += [row(w, "모순", f"g{g}"), row(w, "정상", f"g{g}")]
            rows += [row(w, "무관", f"g{g}") for _ in range(6)]
            rows += [row(w, "제외", f"g{g}")]
    return rows


def test_module_has_no_heavy_imports():
    """학습 루프 밖의 로직은 torch·transformers 없이 import 된다 — 두 파일 모두 모듈 최상위에서 부르지 않는다."""
    import ast
    for path in (EVAL / "train_lib.py", ROOT / "scripts" / "train_judge.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
        names = {a.name.split(".")[0] for n in top for a in n.names} |             {(n.module or "").split(".")[0] for n in top if isinstance(n, ast.ImportFrom)}
        assert not names & {"torch", "transformers", "datasets", "numpy"}, (path.name, names)
    assert TJ.TL is TL


# ================================================================ 접기 분할

def test_split_by_work_and_eval_exclusion():
    tr, va, rep = TL.split_by_work(corpus(), val_work="sherlock", train_works=["q1", "q3"], eval_work="wm")
    assert {r["work"] for r in tr} == {"q1", "q3"} and {r["work"] for r in va} == {"sherlock"}
    assert rep["dropped"] == {"eval_work": 36}
    # train_works 를 안 주면 val·eval 이 아닌 작품 전부
    tr2, _, _ = TL.split_by_work(corpus(), val_work="sherlock", eval_work="wm")
    assert {r["work"] for r in tr2} == {"q1", "q3"}
    # 지정 밖 작품은 버리고 보고
    _, _, rep3 = TL.split_by_work(corpus(), val_work="sherlock", train_works=["q1"], eval_work="wm")
    assert rep3["dropped"] == {"eval_work": 36, "other:q3": 36}


@pytest.mark.parametrize("kw", [
    {"val_work": "q1", "train_works": ["q1", "q3"]},                     # 같은 작품 양쪽
    {"val_work": "q1", "train_works": ["q3"], "eval_work": "q1"},          # 평가 = 검증
    {"val_work": "q1", "train_works": ["q3", "wm"], "eval_work": "wm"},    # 평가 ⊂ 학습
    {"val_work": "dracula", "train_works": ["q3"]},                        # 검증 행 없음
])
def test_split_by_work_errors(kw):
    with pytest.raises(ValueError):
        TL.split_by_work(corpus(), **kw)


def test_split_requires_work_field():
    rows = corpus() + [{"premise": "p", "hypothesis": "h", "label": "무관", "case_id": "x"}]
    with pytest.raises(ValueError, match="work"):
        TL.split_by_work(rows, val_work="q1")


def test_prepare_drops_excluded_everywhere_and_eval_work():
    rows = corpus() + [row("wm", "모순", "h0", split="holdout"), row("q1", "제외", "h1", split="holdout"),
                       row("q1", "모순", "h1", split="holdout")]
    s = TL.prepare(rows, val_work="sherlock", train_works=["q1", "q3"], eval_work="wm", val_sources="all")
    for rs in (s.train, s.val, s.holdout):
        assert all(r["label"] != "제외" for r in rs)
        assert all(r["work"] != "wm" for r in rs)
    assert s.report["excluded_label"] == {"train": 8, "val": 4, "holdout": 1}
    assert [r["case_id"] for r in s.holdout] == ["h1"]
    assert s.report["counts"]["train"] == {"모순": 8, "정상": 8, "무관": 48}


def test_prepare_val_real_only_by_default():
    rows = corpus() + [row("sherlock", "모순", f"r{i}", split="real") for i in range(3)]
    s = TL.prepare(rows, val_work="sherlock", eval_work="wm")
    assert len(s.val) == 3 and all(r["split"] == "real" for r in s.val)
    assert s.report["dropped"]["val_non_real"] == 36
    assert all(r["work"] != "sherlock" for r in s.train)      # 검증 작품의 주입 행은 학습에도 안 들어간다
    with pytest.raises(ValueError, match="실제 장면"):
        TL.prepare(corpus(), val_work="sherlock")


def test_prepare_val_jsonl_rejects_shared_work_and_unknown_label():
    rows = [r for r in corpus() if r["work"] in ("q1", "q3")]
    val = [r for r in corpus() if r["work"] == "q3"]
    with pytest.raises(ValueError, match="동시에"):
        TL.prepare(rows, val_rows=val)
    with pytest.raises(ValueError, match="모르는 라벨"):
        TL.prepare(rows + [row("q1", "모름", "z")], val_work="q3")


def test_train_judge_prepare_only_cli(tmp_path, capsys):
    p = tmp_path / "pairs.jsonl"
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in corpus()), encoding="utf-8")
    TJ.main(["--train", str(p), "--val-work", "q1", "--train-works", "q3", "sherlock", "--eval-work", "wm",
             "--out", str(tmp_path / "m"), "--prepare-only", "--val-sources", "all"])
    rep = json.loads(capsys.readouterr().out)
    assert rep["val_work"] == "q1" and rep["train_works"] == ["q3", "sherlock"] and rep["eval_work"] == "wm"
    assert not (tmp_path / "m").exists()


def test_train_judge_defaults_match_design():
    a = TJ.build_parser().parse_args(["--train", "x", "--out", "y"])
    assert (a.epochs, a.early_stop_patience, a.max_length, a.loss, a.unrelated_ratio, a.seeds, a.precision,
            a.target_fpr, a.label_smoothing) == (6, 2, 384, "weighted", 2.0, [1, 2, 3], "fp16", 0.05, 0.0)



def test_train_judge_attn_and_nli_args():
    a = TJ.build_parser().parse_args(["--train", "x", "--out", "y"])
    assert (a.attn_implementation, a.nli_dataset, a.nli_from, a.nli_only) == ("eager", "klue/klue", None, False)
    TJ.check_args(a)
    bad = [["--out", "y"],                                             # --train 없음(nli-only 아님)
           ["--out", "y", "--nli-only"],                               # nli-only 인데 에폭 없음
           ["--train", "x", "--out", "y", "--nli-epochs", "1", "--init-model", "z"],
           ["--train", "x", "--out", "y", "--nli-from", "z", "--nli-epochs", "1"],
           ["--train", "x", "--out", "y", "--nli-from", "does/not/exist"]]
    for argv in bad:
        with pytest.raises(SystemExit):
            TJ.check_args(TJ.build_parser().parse_args(argv))
    TJ.check_args(TJ.build_parser().parse_args(["--out", "y", "--nli-only", "--nli-epochs", "2"]))


def test_nli_meta_is_loadable_by_hf_backend_rules():
    """기초 모델 meta 를 HFBackend 가 그대로 읽는다 — pairs_version 검사·정밀도·어텐션(G3 영 학습 기준선 경로)."""
    from app.pipeline import learned_judge as LJ
    from app.pipeline.pairs import PAIRS_VERSION
    a = TJ.build_parser().parse_args(["--out", "y", "--nli-only", "--nli-epochs", "2"])
    m = TJ.nli_meta_for(a, 24998, PAIRS_VERSION, list(TJ.LABELS), {}, "cpu")
    assert m["threshold"] == 0.5 and m["temperature"] == 1.0 and m["labels"] == list(LJ.LABELS)
    LJ.check_pairs_version(m, "y")
    o = LJ.resolve_load_options(m, "cuda")
    assert (o["precision"], o["attn_implementation"], o["max_length"], o["padding"]) == ("fp16", "eager", 384, "bucket")
    assert m["nli"]["epochs"] == 2 and m["nli"]["n"] == 24998

# ================================================================ 가중치·표본

def test_class_weights_inverse_sqrt_mean_one():
    w = TL.class_weights({"모순": 100, "정상": 400, "무관": 1600})
    assert sum(w) / 3 == pytest.approx(1.0)
    assert w[0] / w[1] == pytest.approx(2.0) and w[1] / w[2] == pytest.approx(2.0)
    assert TL.class_weights({"모순": 1, "정상": 9, "무관": 0})[2] == 0.0
    assert sum(TL.class_weights({"모순": 1, "정상": 9, "무관": 0})) / 2 == pytest.approx(1.0)
    assert TL.class_weights({"모순": 1, "정상": 2, "무관": 3}, "ce") == [1.0, 1.0, 1.0]


def test_sampled_counts_caps_unrelated():
    rows = [r for r in corpus() if r["label"] != "제외"]
    c = TL.sampled_counts(rows, 2.0)
    assert c == {"모순": 16, "정상": 16, "무관": 64}
    assert TL.sampled_counts(rows, 0)["무관"] == 96


def test_sample_epoch_ratio_and_determinism():
    rows = [r for r in corpus() if r["label"] != "제외"]
    a = TL.sample_epoch(rows, 2.0, seed=1, epoch=0)
    b = TL.sample_epoch(rows, 2.0, seed=1, epoch=0)
    c = TL.sample_epoch(rows, 2.0, seed=1, epoch=1)
    d = TL.sample_epoch(rows, 2.0, seed=2, epoch=0)
    lab = Counter(r["label"] for r in a)
    assert lab == {"모순": 16, "정상": 16, "무관": 64}
    assert [id(r) for r in a] == [id(r) for r in b]                      # 같은 시드·에폭 → 같은 표본
    assert [id(r) for r in a] != [id(r) for r in c] != [id(r) for r in d]  # 에폭·시드가 바뀌면 다르다
    # 입력 순서 유지(부분 수열)
    pos = {id(r): i for i, r in enumerate(rows)}
    assert [pos[id(r)] for r in a] == sorted(pos[id(r)] for r in a)
    # 묶음 라운드로빈: 16 묶음 × 6 무관에서 64 를 뽑으면 묶음당 4개씩
    per_group = Counter(r["group_id"] for r in a if r["label"] == "무관")
    assert set(per_group.values()) == {4} and len(per_group) == 16


def test_sample_epoch_round_robin_with_uneven_groups():
    rows = [row("q1", "모순", "a")] + [row("q1", "무관", "a") for _ in range(10)] + [row("q1", "무관", "b")]
    out = TL.sample_epoch(rows, 2.0, seed=3, epoch=0)
    unrel = Counter(r["group_id"] for r in out if r["label"] == "무관")
    assert sum(unrel.values()) == 2 and unrel["q1:b"] == 1   # 작은 묶음도 한 바퀴에 하나는 뽑힌다
    assert TL.sample_epoch(rows, None, 3, 0) == rows


def test_train_fraction_nested_and_group_level():
    rows = [r for r in corpus() if r["label"] != "제외"]
    q = TL.train_fraction(rows, 0.25, 7)
    h = TL.train_fraction(rows, 0.5, 7)
    groups = lambda rs: {TL.group_key(r) for r in rs}  # noqa: E731
    assert len(groups(q)) == 4 and len(groups(h)) == 8 and groups(q) <= groups(h)
    assert all(sum(1 for r in rows if TL.group_key(r) == g) == sum(1 for r in q if TL.group_key(r) == g)
               for g in groups(q))   # 묶음은 통째로
    assert TL.train_fraction(rows, 1.0, 7) == rows
    assert TL.train_fraction(rows, 0.25, 7) == q
    s = TL.prepare(corpus(), val_work="sherlock", eval_work="wm", train_frac=0.5, val_sources="all")
    assert s.report["train_frac"]["rows_after"] < s.report["train_frac"]["rows_before"]


def test_hyp_only():
    rows = [row("q1", "모순", "a", premise="원장 사실")]
    out = TL.hyp_only(rows)
    assert out[0]["premise"] == "" and out[0]["hypothesis"] == rows[0]["hypothesis"]
    assert rows[0]["premise"] == "원장 사실"   # 원본은 그대로
    s = TL.prepare(corpus(), val_work="sherlock", eval_work="wm", hyp_only_mode=True, val_sources="all")
    assert all(r["premise"] == "" for r in s.train + s.val)


# ================================================================ 장면 기준값

def scenes(spec: list[tuple[str, bool, list[float]]]) -> tuple[list[dict], list[float]]:
    rows, p = [], []
    for sid, pos, ps in spec:
        for i, x in enumerate(ps):
            rows.append(row("q1", "모순" if pos and i == 0 else "무관", sid, scene=sid))
            p.append(x)
    return rows, p


def test_pick_scene_threshold_max_recall_under_fpr():
    # 양성 4장면, 정상 20장면. 정상 하나가 0.8 로 높다 → 0.05 이하에서는 정상 1건(=0.05)까지 허용
    spec = [(f"p{i}", True, [v, 0.1]) for i, v in enumerate([0.9, 0.7, 0.6, 0.3])]
    spec += [("n0", False, [0.8, 0.2])] + [(f"n{i}", False, [0.05 * i / 20, 0.01]) for i in range(1, 20)]
    rows, p = scenes(spec)
    t, m = TL.pick_scene_threshold(rows, p, 0.05)
    # 정상 n0(0.8) 을 넘기면 오탐 1/20 = 0.05 허용 → 0.3 까지 내려 recall 4/4. 그 아래 정상(≤0.0475)은 아직 안 걸림
    assert t == 0.3 and m["recall"] == 1.0 and m["fp"] == 1 and m["fpr"] == 0.05
    t2, m2 = TL.pick_scene_threshold(rows, p, 0.0)
    assert t2 == 0.9 and m2["recall"] == 0.25 and m2["fp"] == 0   # 0.8(정상) 위에서만 → 0.9 하나


def test_pick_scene_threshold_tie_prefers_higher():
    # 양성 0.6, 정상 0.2. recall 1 이 되는 후보 0.6(이 위는 recall 0). 0.2 로 내려도 recall 같고 오탐 생김
    rows, p = scenes([("p", True, [0.6]), ("n", False, [0.2])])
    t, m = TL.pick_scene_threshold(rows, p, 1.0)   # 오탐 제한이 느슨해도 동률은 높은 값
    assert t == 0.6 and m["recall"] == 1.0 and m["fp"] == 0


def test_pick_scene_threshold_uses_scene_max_and_scene_positive_field():
    rows, p = scenes([("p", True, [0.1, 0.95]), ("n", False, [0.5])])
    t, m = TL.pick_scene_threshold(rows, p, 0.0)
    assert t == 0.95 and m["recall"] == 1.0
    # scene_positive 가 주어지면 그것이 장면 정답(근거 쌍이 쌍 목록에 없던 모순 장면도 양성)
    rows2 = [row("q1", "무관", "s", scene="s", scene_positive=True), row("q1", "무관", "n", scene="n")]
    _, m2 = TL.pick_scene_threshold(rows2, [0.7, 0.2], 0.0)
    assert m2["n_pos"] == 1 and m2["recall"] == 1.0
    # 다른 작품의 같은 scene_id 는 다른 장면
    rows3 = [row("q1", "모순", "s", scene="scene_01"), row("q3", "무관", "s", scene="scene_01")]
    assert len(TL.scene_scores(rows3, [0.9, 0.1])) == 2


def test_pick_scene_threshold_rejects_excluded_and_nothing_positive():
    with pytest.raises(ValueError):
        TL.pick_scene_threshold([row("q1", "제외", "a")], [0.5])
    rows, p = scenes([("n", False, [0.4])])
    t, m = TL.pick_scene_threshold(rows, p, 0.05)
    assert t > 0.4 and m["fp"] == 0 and m["recall"] is None


def test_old_pair_f1_threshold_removed():
    assert not hasattr(TJ, "pick_threshold")
    assert TJ.pick_scene_threshold is TL.pick_scene_threshold


# ================================================================ 지표·시드·조기 종료

def test_ece_equal_count_and_nll():
    conf = [0.9] * 10 + [0.6] * 10
    correct = [True] * 9 + [False] + [True] * 6 + [False] * 4
    assert TL.ece(conf, correct, bins=2) == pytest.approx(0.0)
    assert TL.ece([0.9] * 4, [False] * 4, bins=10) == pytest.approx(0.9)
    assert TL.nll([[0.5, 0.25, 0.25]], [0]) == pytest.approx(math.log(2))
    cal = TL.pair_calibration([[0.7, 0.2, 0.1], [0.1, 0.1, 0.8]], [0, 2])
    assert cal["binning"] == "equal_count" and cal["bins"] == 10 and cal["n"] == 2
    assert TL.softmax([0.0, 0.0, math.log(2)]) == pytest.approx([0.25, 0.25, 0.5])


def test_pr_auc():
    assert TL.pr_auc([0.9, 0.8, 0.1], [True, True, False]) == pytest.approx(1.0)
    assert TL.pr_auc([0.9, 0.8, 0.1], [False, True, False]) == pytest.approx(0.5)
    assert TL.pr_auc([0.1], [False]) is None


def test_quantiles():
    q = TL.quantiles(list(range(1, 101)))
    assert (q["p50"], q["p90"], q["p95"], q["p99"], q["max"], q["n"]) == (50, 90, 95, 99, 100, 100)


def test_select_seed_recall_then_nll():
    res = [{"seed": 1, "scene_recall": 0.5, "val_nll": 0.4}, {"seed": 2, "scene_recall": 0.6, "val_nll": 0.9},
           {"seed": 3, "scene_recall": 0.6, "val_nll": 0.5}]
    assert TL.select_seed(res)["seed"] == 3
    assert TL.select_seed([{"seed": 1, "scene_recall": None, "val_nll": 0.1},
                           {"seed": 2, "scene_recall": 0.0, "val_nll": 0.9}])["seed"] == 2


def test_early_stopper():
    s = TL.EarlyStopper(2)
    assert [s.step(e, v) for e, v in enumerate([1.0, 0.8, 0.85, 0.79, 0.9, 0.95])] == \
        [False, False, False, False, False, True]
    assert s.best_epoch == 3 and s.best == 0.79
    off = TL.EarlyStopper(0)
    assert not any(off.step(e, 1.0) for e in range(5))


def test_package_versions_tolerates_missing():
    v = TL.package_versions(("pytest", "definitely-not-installed-pkg"))
    assert v["pytest"] and v["definitely-not-installed-pkg"] is None


# ---------------------------------------------------------------- 라벨 v2 후속: '제외' 포함 장면 점수 · AUROC · bootstrap · inject_frac

def test_prepare_keeps_val_excluded_for_scene_scores():
    s = TL.prepare(corpus(), val_work="sherlock", train_works=["q1", "q3"], eval_work="wm", val_sources="all")
    assert len(s.val_excluded) == 4 and all(r["label"] == "제외" and r["work"] == "sherlock" for r in s.val_excluded)
    h = TL.prepare(corpus(), val_work="sherlock", train_works=["q1", "q3"], eval_work="wm", val_sources="all",
                   hyp_only_mode=True)
    assert all(r["premise"] == "" for r in h.val_excluded)


def test_scene_scores_allow_excluded_only_with_scene_positive():
    rows = [row("q1", "모순", "a", scene_positive=True), row("q1", "제외", "a", scene_positive=True),
            row("q1", "정상", "b", scene_positive=False)]
    with pytest.raises(ValueError):
        TL.scene_scores(rows, [0.2, 0.9, 0.1])
    sc = TL.scene_scores(rows, [0.2, 0.9, 0.1], allow_excluded=True)
    assert sc == {"q1|a": (0.9, True), "q1|b": (0.1, False)}          # 제외 행이 장면 max 를 바꾼다
    with pytest.raises(ValueError):                                    # scene_positive 없는 제외는 여전히 거부
        TL.scene_scores([row("q1", "제외", "c")], [0.5], allow_excluded=True)
    t, m = TL.pick_scene_threshold(rows, [0.2, 0.9, 0.1], 0.0, allow_excluded=True)
    assert t == 0.9 and m["recall"] == 1.0


def test_scene_auroc_and_stratified_bootstrap():
    sc = {f"p{i}": (v, True) for i, v in enumerate([0.9, 0.8, 0.4])} | {f"n{i}": (v, False) for i, v in enumerate([0.5, 0.1, 0.4])}
    # 양성 3 × 음성 3 = 9: 0.9>all(3) 0.8>all(3) 0.4>0.1, =0.4(0.5) → 7.5/9
    assert TL.scene_auroc(sc) == pytest.approx(7.5 / 9)
    b = TL.bootstrap_auroc(sc, n=500, seed=3)
    assert b["lo"] <= b["auroc"] <= b["hi"] and b["n_pos"] == 3 and b["n_neg"] == 3
    assert TL.bootstrap_auroc(sc, n=500, seed=3) == b                 # 시드 고정 → 같은 구간
    assert TL.scene_auroc({"a": (0.1, True)}) is None and TL.bootstrap_auroc({"a": (0.1, True)})["lo"] is None


def test_scene_topk_scores_mean_of_top_k():
    rows = [row("q1", lab, "a", scene_positive=True) for lab in ("모순", "제외", "무관")] + [row("q1", "정상", "b", scene_positive=False)]
    sc = TL.scene_topk_scores(rows, [0.9, 0.5, 0.1, 0.3], 2)
    assert sc["q1|a"] == (pytest.approx(0.7), True) and sc["q1|b"] == (pytest.approx(0.3), False)


def test_inject_fraction_by_contrast_group_nested():
    rows = [row("wm", "모순", f"c{g}", source="inject", contrast_group=f"cg{g // 2}") for g in range(40)]
    rows += [row("wm", "모순", "r", source="real")]
    q = TL.inject_fraction(rows, 0.25, 7)
    groups = {r["contrast_group"] for r in q if r["source"] == "inject"}
    assert len(groups) == 5 and sum(r["source"] == "real" for r in q) == 1
    assert all(sum(1 for r in q if r.get("contrast_group") == g) == 2 for g in groups)  # 묶음 통째로
    assert groups <= {r["contrast_group"] for r in TL.inject_fraction(rows, 0.5, 7) if r["source"] == "inject"}
    assert TL.inject_fraction(rows, 1.0, 7) == rows
    with pytest.raises(ValueError):
        TL.inject_fraction(rows, 0.0, 7)


def test_prepare_inject_frac_only_touches_train_inject_rows():
    rows = [row("wm", "모순", f"c{g}", source="inject", contrast_group=f"wm-cg{g}") for g in range(8)]
    rows += [row("wm", "무관", "r1", source="real", split="real"), row("q1", "모순", "v1", source="real", split="real",
                                                                      scene_positive=True)]
    s = TL.prepare(rows, val_work="q1", inject_frac=0.25)
    assert sum(r["source"] == "inject" for r in s.train) == 2 and sum(r["source"] == "real" for r in s.train) == 1
    assert s.report["inject_frac"] == {"frac": 0.25, "rows_before": 9, "rows_after": 3} and len(s.val) == 1
    assert "inject_frac" not in TL.prepare(rows, val_work="q1").report


# ---------------------------------------------------------------- 미리 정한 묶음 분할 · 쌍 = 장면 근사 (실험 L)

def _lrows() -> list[dict]:
    rows = []
    for g in ("s1", "s2", "s3"):   # 한 장면 안에 대조 짝이 여럿 — contrast_group 은 쌍마다 다르다
        for k in range(2):
            rows += [row("l1_wm", "모순", g, contrast_group=f"l1_wm:{g}:{k}", id=f"{g}:{k}c"),
                     row("l1_wm", "정상", g, contrast_group=f"l1_wm:{g}:{k}", id=f"{g}:{k}n")]
    return rows + [row("wm", "무관", "w1", split="real", scene_positive=False),
                   row("wm", "제외", "w1", split="real", scene_positive=False)]


def test_split_by_group_ids_keeps_whole_scene_together():
    tr, va, rep = TL.split_by_group_ids(_lrows(), ["l1_wm:s2", "wm:w1"])
    assert {r["group_id"] for r in va} == {"l1_wm:s2", "wm:w1"} and len(va) == 6
    assert not {r["group_id"] for r in tr} & {r["group_id"] for r in va}
    assert rep["val_groups"] == 2 and rep["val_groups_by_work"] == {"l1_wm": 1, "wm": 1}
    with pytest.raises(ValueError, match="데이터에 없습니다"):
        TL.split_by_group_ids(_lrows(), ["nope"])
    with pytest.raises(ValueError, match="group_id"):
        TL.split_by_group_ids([{"label": "모순"}], ["x"])


def test_prepare_val_groups_and_conflicts():
    sp = TL.prepare(_lrows(), val_groups=["l1_wm:s1", "wm:w1"])
    assert {r["group_id"] for r in sp.val} == {"l1_wm:s1", "wm:w1"} and len(sp.val_excluded) == 1
    assert all(r["label"] != "제외" for r in sp.train + sp.val) and sp.report["val_groups"] == 2
    with pytest.raises(ValueError, match="함께 쓸 수 없습니다"):
        TL.prepare(_lrows(), val_groups=["l1_wm:s1"], val_rows=_lrows()[:2])


def test_pair_scene_scores_uses_pair_label_not_scene():
    rows = [row("l1_wm", "모순", "s1"), row("l1_wm", "정상", "s1"), row("wm", "무관", "w1", scene_positive=True)]
    sc = TL.pair_scene_scores(rows, [0.9, 0.2, 0.7])
    assert sorted(sc.values()) == [(0.2, False), (0.7, False), (0.9, True)]   # scene_positive 무시, 같은 장면도 따로
    thr, m = TL.pick_threshold_from_scores(sc, 0.0)
    assert thr == 0.9 and (m["tp"], m["fp"], m["n_neg"]) == (1, 0, 2)
    with pytest.raises(ValueError, match="제외"):
        TL.pair_scene_scores([row("wm", "제외", "w1")], [0.5])


def test_train_judge_val_groups_cli(tmp_path, capsys):
    p = tmp_path / "pairs.jsonl"
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in _lrows()), encoding="utf-8")
    g = tmp_path / "groups.txt"
    g.write_text("# 검증 묶음\nl1_wm:s3\n\nwm:w1\n", encoding="utf-8")
    TJ.main(["--train", str(p), "--val-groups", str(g), "--scene-unit", "pair", "--out", str(tmp_path / "m"),
             "--prepare-only"])
    rep = json.loads(capsys.readouterr().out)
    assert rep["val_groups"] == 2 and rep["val_groups_by_work"] == {"l1_wm": 1, "wm": 1}
    assert TJ.read_group_list(g) == ["l1_wm:s3", "wm:w1"]
    (tmp_path / "g.json").write_text('["a", "b"]', encoding="utf-8")
    assert TJ.read_group_list(tmp_path / "g.json") == ["a", "b"]
    assert TJ.build_parser().parse_args(["--train", "x", "--out", "y"]).scene_unit == "scene"


# ---------------------------------------------------------------- 장면 집계 후보 (실험 M-2)

def _agg_rows() -> list[dict]:
    def r(scene, pos, hyp, kind, label="무관"):
        return {"work": "q1", "scene_id": scene, "scene_positive": pos, "label": label, "event_id": "E", "entity": None,
                "field": None, "hypothesis": hyp, "premise_kind": kind}
    return [r("A", True, "h1", "ledger", "모순"), r("A", True, "h1", "past"), r("A", True, "h2", "past", "제외"),
            r("B", False, "h1", "past"), r("B", False, "h1", "past"), r("B", False, "h1", "ledger")]


def test_scene_scores_agg_three_candidates():
    rows, p = _agg_rows(), [0.9, 0.1, 0.8, 0.95, 0.9, 0.2]
    mx = TL.scene_scores_agg(rows, p, "max")
    assert mx == TL.scene_scores(rows, p, allow_excluded=True) and mx["q1|B"] == (0.95, False)
    hc = TL.scene_scores_agg(rows, p, "hyp_contrast")
    assert hc["q1|A"] == (pytest.approx(0.9 - 0.5), True)          # h1: 0.9−0.5 · h2(쌍 1개): 0 → 최댓값
    assert hc["q1|B"] == (pytest.approx(0.95 - 0.6833333), False)
    hp = TL.scene_scores_agg(rows, p, "hyp_contrast_past05")
    assert hp["q1|A"][0] == pytest.approx(0.9 - (0.9 + 0.05) / 2)
    assert hp["q1|B"][0] == pytest.approx(0.475 - (0.475 + 0.45 + 0.2) / 3)
    assert TL.hypothesis_id(rows[0]) == ("E", None, None, "h1")
    with pytest.raises(ValueError, match="aggregate"):
        TL.scene_scores_agg(rows, p, "mean")


def test_score_pairs_aggregate_option():
    spec = importlib.util.spec_from_file_location("score_pairs", ROOT / "scripts" / "eval" / "score_pairs.py")
    SP = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(SP)
    rows, p = _agg_rows(), [0.9, 0.1, 0.8, 0.95, 0.9, 0.2]
    probs = [[x, (1 - x) / 2, (1 - x) / 2] for x in p]
    assert SP.score(rows, p, probs, 0.0, None, bootstrap=0)["scene_auroc"]["auroc"] == 0.0
    out = SP.score(rows, p, probs, 0.0, None, bootstrap=0, aggregate="hyp_contrast", given_threshold=0.3)
    assert out["aggregate"] == "hyp_contrast" and out["scene_auroc"]["auroc"] == 1.0
    assert out["scene_at_given_threshold"]["tp"] == 1 and out["scene_at_given_threshold"]["fp"] == 0


# ---------------------------------------------------------------- pairs_version 3 연결 (실험 N)

def test_train_judge_data_pairs_version(tmp_path):
    assert TJ.data_pairs_version([{"label": "모순"}, {"pairs_version": "2"}]) == "2"
    assert TJ.data_pairs_version([{"pairs_version": "3"}]) == "3"
    with pytest.raises(SystemExit, match="섞여"):
        TJ.data_pairs_version([{"pairs_version": "3"}, {"label": "정상"}])


def test_score_pairs_checks_rows_against_meta(tmp_path):
    spec = importlib.util.spec_from_file_location("score_pairs", ROOT / "scripts" / "eval" / "score_pairs.py")
    SP = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(SP)
    (tmp_path / "judge_meta.json").write_text(json.dumps({"pairs_version": "3"}), encoding="utf-8")
    assert SP.check_rows_pairs_version([{"pairs_version": "3"}], str(tmp_path)) == "3"
    with pytest.raises(SystemExit, match="pairs_version"):
        SP.check_rows_pairs_version([{"label": "모순"}], str(tmp_path))   # 판 없음 = 2
    with pytest.raises(SystemExit, match="pairs_version"):
        SP.check_rows_pairs_version([{"pairs_version": "3"}, {"pairs_version": "2"}], str(tmp_path))
