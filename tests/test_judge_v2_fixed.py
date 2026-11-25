"""학습 judge v2 고정 레시피(main 451bab2 §10) — v3 조립·고정 학습(마지막 체크포인트, 온도 1)·dev 기록·장면 채점 근거 일치.

모두 가짜 데이터다(평가셋 폴더는 열지 않는다).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "v2"))
import v2_common as C  # noqa: E402

from tests.test_judge_v2 import _tiny_model  # noqa: E402

# ---------------------------------------------------------------- 근거 일치(P/N 사이에 다른 문장)


def test_diff_sentences_and_grounding_ignore_planted():
    import score_v2 as S
    p = "김첨지는 비를 맞았다. 아내는 벌떡 일어나 춤을 추었다. 그는 집으로 갔다."
    n = "김첨지는 비를 맞았다. 아내는 누운 채 기침을 하였다. 그는 집으로 갔다."
    d = S.diff_sentences(p, n)
    assert d == ["아내는 벌떡 일어나 춤을 추었다."]
    assert S.grounded_diff("아내는 벌떡 일어나 춤을 추었다.", d)
    assert S.grounded_diff("아내는  벌떡 일어나 춤을 추었다", d)          # 정규화(공백·문장부호)
    assert not S.grounded_diff("김첨지는 비를 맞았다.", d)                  # P/N 공통 문장
    assert not S.grounded_diff(None, d) and not S.grounded_diff("x", [])
    assert S.diff_sentences(p, p) == []
    assert S.window_grounded("아내는 벌떡 일어나 춤을 추었다", d)
    assert not S.window_grounded("그는 집으로 갔다.", d) and not S.window_grounded(None, d)
    with pytest.raises(SystemExit):
        S.check_rows([{"id": "x", "prefix": "p", "target_P": "a"}])


def test_score_scene_path_uses_diff_not_planted(tmp_path):
    """장면 → 문장 → 검색 → max 경로와, planted 가 엉뚱해도 근거 일치가 다른 문장으로 잡히는지(가짜 모델·가짜 장면)."""
    init = _tiny_model(tmp_path)
    import score_v2 as S
    pre = "\n".join(["김첨지는 인력거를 끌고 비 오는 거리를 달렸다. 아내는 집에서 앓고 있었다."] * 5)
    rows = [{"id": f"e{i}", "work": "w", "prefix": pre, "target_P": "김첨지는 학생이었다. 아내는 집에서 앓았다.",
             "target_N": "김첨지는 정거장까지 달렸다. 아내는 집에서 앓았다.", "planted": "편집 설명: 직업을 바꿈", "type": "설정"}
            for i in range(3)]
    out, sc = S.score(init, rows, "cpu")
    assert out["n_scenes"] == 6 and out["grounding"]["n_pos"] == 3 and out["grounding"]["pairs_without_diff"] == 0
    assert "planted 안 씀" in out["grounding"]["rule"]
    for r in sc:
        assert r["max"] == pytest.approx(max(c["p"] for c in r["claims"]), abs=1e-6)   # 장면 점수 = 문장 max
        assert len(r["claims"]) == 2 and all(c["chunks"] for c in r["claims"])          # 문장마다 검색 맥락
        if r["y"]:
            assert r["grounded"] == (r["top_claim"] == "김첨지는 학생이었다.")
            assert r["n_diff_sentences"] == 1


# ---------------------------------------------------------------- v3 조립


def _pair(i: int, typ: str, split: str) -> list[dict]:
    base = {"type": typ, "work": "작가__가", "split": split, "context": ["가 나 다 라 마"]}
    return [base | {"id": f"{typ}{i}m", "pair_of": f"{typ}{i}n", "label": "모순", "claim": f"주장 {i} 바뀐 것" * (1 + i % 3)},
            base | {"id": f"{typ}{i}n", "pair_of": f"{typ}{i}m", "label": "무모순", "claim": f"주장 {i} 바뀐 것" * (1 + i % 3)}]


def test_select_v3_keeps_all_b_consensus_a_drops_c():
    import build_v3 as B
    rows = [r for i in range(5) for r in _pair(i, "A", "train")] + [r for i in range(3) for r in _pair(i, "B", "dev")] \
        + [r for i in range(2) for r in _pair(i, "C", "train")]
    for r in rows:
        if r["type"] == "A":
            r["claim"] = "같은 주장"      # A 쌍 성질
    keep = {"pairs": {"A": [{"id": "A1m", "pair_of": "A1n", "split": "train"}, {"id": "A3m", "pair_of": "A3n", "split": "train"}]}}
    out, stat = B.select(rows, keep)
    assert {r["id"] for r in out} == {"A1m", "A1n", "A3m", "A3n"} | {f"B{i}{k}" for i in range(3) for k in "mn"}
    assert stat["dropped"] == {"A": 6, "C": 4}
    assert B.pair_counts(out) == {"A": {"train": 2}, "B": {"dev": 3}}
    no_a, stat2 = B.select(rows, keep, ("A",))                # 세 번째 감사 분기: A 제외
    assert {r["type"] for r in no_a} == {"B"} and B.pair_counts(no_a) == {"B": {"dev": 3}}
    assert stat2["dropped"] == {"A": 10, "C": 4}
    bad = {"pairs": {"A": [{"id": "A1m", "pair_of": "A1n", "split": "dev"}]}}
    with pytest.raises(SystemExit):
        B.select(rows, bad)                                   # split 이 v2 와 다르면 멈춘다
    with pytest.raises(SystemExit):
        B.select(rows, {"pairs": {"A": [{"id": "A9m", "pair_of": "A9n", "split": "train"}]}})


def test_mix_v3_decides_on_plus_d_sets():
    import build_v3 as B
    rows = [r for i in range(6) for r in _pair(i, "B", "train")]
    d = [{"id": f"d{i}", "type": "D", "claim": "원문 문장" * (1 + i % 4), "context": ["가 나 다"] * 4, "label": "무모순"}
         for i in range(12)]
    rep = B.mix_v3(rows, d)
    assert rep["decision_sets"] == ["all+D", "A+D", "B+D"] and rep["worst"]["set"] in ("all+D", "B+D")
    assert rep["resample_D"] == (rep["worst"]["value"] > 0.60)
    assert rep["resample_D"]                                   # 조각 수가 라벨을 가른다(1 대 4) → 다시 뽑기


# ---------------------------------------------------------------- 고정 학습·dev 기록


def test_train_fixed_runs_all_epochs_last_checkpoint_t1(tmp_path):
    init = _tiny_model(tmp_path)
    import train_v2 as T
    pairs = tmp_path / "pairs.jsonl"
    rows = [{"id": f"r{i}", "type": "B", "work": "w1", "split": "dev" if (i // 2) % 4 == 0 else "train",
             "claim": "김첨지는 집에 있었다.", "context": ["아내는 집에서 앓고 있었다."], "label": "모순" if i % 2 else "무모순",
             "pair_of": f"r{i ^ 1}"} for i in range(16)]
    C.write_jsonl(pairs, rows)
    s = tmp_path / "S.jsonl"
    pre = "\n".join(["김첨지는 인력거를 끌고 비 오는 거리를 달렸다. 아내는 집에서 앓고 있었다."] * 5)
    C.write_jsonl(s, [{"id": f"s{i}", "work": "w", "split": "dev" if i < 2 else "train", "prefix": pre,
                       "target_P": "김첨지는 학생이었다. 비가 오지 않았다.", "target_N": "김첨지는 정거장까지 달렸다. 비가 왔다."}
                      for i in range(4)])
    o1 = tmp_path / "s1"
    T.main(["--stage", "pairs", "--init", str(init), "--train", str(pairs), "--out", str(o1), "--device", "cpu",
            "--epochs", "3", "--patience", "1", "--batch-size", "4", "--grad-accum", "1", "--lr", "5e-2", "--fixed",
            "--recipe", "test"])
    m1 = json.loads((o1 / "v2_meta.json").read_text(encoding="utf-8"))
    assert [h["epoch"] for h in m1["history"]] == [1, 2, 3] and not any(h.get("best") for h in m1["history"])
    assert m1["temperature"] == 1.0 and m1["threshold"] is None and m1["checkpoint"] == "last" and m1["fixed"]
    assert m1["nonfinite_steps"] == 0 and m1["recipe"] == "test"
    o2 = tmp_path / "s2"
    T.main(["--stage", "mil", "--init", str(o1), "--s", str(s), "--out", str(o2), "--device", "cpu", "--epochs", "2",
            "--patience", "1", "--lr", "5e-2", "--fixed"])
    m2 = json.loads((o2 / "v2_meta.json").read_text(encoding="utf-8"))
    assert [h["epoch"] for h in m2["history"]] == [1, 2] and m2["temperature"] == 1.0
    assert m2["dev"]["note"].startswith("기록만") and m2["scenes"] == {"train": 4, "dev": 4}

    import dev_record_v3 as R
    d = tmp_path / "D.jsonl"
    C.write_jsonl(d, [{"id": f"d{i}", "type": "D", "split": "dev", "claim": "비가 왔다.", "context": ["비가 왔다."],
                       "label": "무모순"} for i in range(3)])
    out = tmp_path / "dev.json"
    R.main(["--model", str(o2), "--pairs", str(pairs), "--d", str(d), "--s", str(s), "--out", str(out), "--device", "cpu"])
    rec = json.loads(out.read_text(encoding="utf-8"))
    assert rec["note"].startswith("기록만") and rec["temperature"] == 1.0
    assert rec["rows"]["B"]["n"] == 4 and rec["rows"]["D"]["n"] == 3 and "AB_pos_vs_D" in rec["rows"]
    assert rec["rows"]["pair_property"]["B"]["n_pairs"] == 2
    assert rec["S_dev"]["n_scenes"] == 4


def test_row_metrics_pair_property():
    import dev_record_v3 as R
    rows = [{"id": "a", "type": "A", "label": "모순", "pair_of": "b"}, {"id": "b", "type": "A", "label": "무모순", "pair_of": "a"},
            {"id": "c", "type": "A", "label": "모순", "pair_of": "d"}, {"id": "d", "type": "A", "label": "무모순", "pair_of": "c"},
            {"id": "e", "type": "D", "label": "무모순"}]
    m = R.row_metrics(rows, [0.9, 0.2, 0.3, 0.6, 0.1])
    assert m["pair_property"]["A"] == {"n_pairs": 2, "contra_gt_consistent": 0.5}
    assert m["A"]["acc"] == 0.5 and m["D"]["acc"] == 1.0 and m["AB_pos_vs_D"]["auroc"] == 1.0


# ---------------------------------------------------------------- 최종 측정(V2-F 측정 1): 이름만 바꾸는 어댑터 → 채점 → 보고서


def test_pd_eval_adapter_rename_only_and_report(tmp_path):
    init = _tiny_model(tmp_path)
    import final_report_v2 as R
    import pd_eval_adapter as A
    import score_v2 as S
    pre = "\n".join(["김첨지는 인력거를 끌고 비 오는 거리를 달렸다. 아내는 집에서 앓고 있었다."] * 5)
    pairs = [{"item": f"I{i}", "pair_id": f"PD-{i}", "work": f"w0{i % 2}", "type": "설정" if i % 2 else "시간",
              "prefix": pre, "p_window": "김첨지는 학생이었다. 아내는 집에서 앓았다.",
              "n_window": "김첨지는 정거장까지 달렸다. 아내는 집에서 앓았다.", "evidence": "x", "verdicts": {}}
             for i in range(4)]
    rows = A.adapt(pairs)
    assert rows[0] == {"id": "PD-0", "target_P": pairs[0]["p_window"], "target_N": pairs[0]["n_window"],
                       "prefix": pre, "work": "w00", "type": "시간"}
    with pytest.raises(SystemExit):
        A.adapt([{k: v for k, v in pairs[0].items() if k != "n_window"}])
    with pytest.raises(SystemExit):
        A.adapt(pairs[:1] * 2)
    src = tmp_path / "pairs.json"
    src.write_text(json.dumps(pairs, ensure_ascii=False), encoding="utf-8")
    fin = tmp_path / "final"
    A.main(["--pairs", str(src), "--out", str(fin / "pd_eval_adapted.jsonl")])
    with pytest.raises(SystemExit):
        A.main(["--pairs", str(src), "--out", str(fin / "pd_eval_adapted.jsonl")])   # 덮어쓰지 않는다
    for name in ("v2", "e1_zero"):
        S.main(["--model-dir", str(init), "--s", str(fin / "pd_eval_adapted.jsonl"), "--device", "cpu", "--threshold", "0.5",
                "--out", str(fin / f"{name}_score.json"), "--scores-out", str(fin / f"{name}_scores.jsonl")])
    (fin / "seal_check.json").write_text(json.dumps({"seal_sha256": "x", "seal_ok": True, "n_files": 1, "mismatches": []}),
                                         encoding="utf-8")
    R.main(["--final", str(fin)])
    md = (fin / "v2_report.md").read_text(encoding="utf-8")
    assert "관문" in md and "0.543" in md and "| w00 |" in md and "| 설정 |" in md
    gm = R.group_metrics(C.read_jsonl(fin / "v2_scores.jsonl"), "type")
    assert gm["설정"]["n_pairs"] == 2 and gm["설정"]["n_pos"] == 2 and gm["설정"]["n_grounded"] == 2
    assert R.gate({"auroc": 0.66, "lo": 0.51}) and not R.gate({"auroc": 0.66, "lo": 0.5}) and not R.gate({"auroc": 0.64, "lo": 0.6})


def test_explore_report_tags_and_compares(tmp_path):
    """V2-X 탐색 보고서: 꼬리표, 확증 값 나란히, 동점 수, 구간 겹침 사실."""
    init = _tiny_model(tmp_path)
    import explore_report as X
    import score_v2 as S
    pre = "\n".join(["김첨지는 인력거를 끌고 비 오는 거리를 달렸다. 아내는 집에서 앓고 있었다."] * 5)
    s = tmp_path / "s.jsonl"
    C.write_jsonl(s, [{"id": f"PD-{i}", "work": "w01", "type": "설정", "prefix": pre, "target_P": "김첨지는 학생이었다. 비가 왔다.",
                       "target_N": "김첨지는 정거장까지 달렸다. 비가 왔다."} for i in range(3)])
    ex, fin = tmp_path / "explore", tmp_path / "final"
    for d, names in ((ex, ("x1", "x2")), (fin, ("v2", "e1_zero"))):
        for n in names:
            S.main(["--model-dir", str(init), "--s", str(s), "--device", "cpu", "--threshold", "0.5", "--tag", X.TAG if d == ex else "",
                    "--out", str(d / f"{n}_score.json"), "--scores-out", str(d / f"{n}_scores.jsonl")])
    assert json.loads((ex / "x1_score.json").read_text(encoding="utf-8"))["tag"] == X.TAG
    assert all(r["tag"] == X.TAG for r in C.read_jsonl(ex / "x1_scores.jsonl"))
    (ex / "seal_check.json").write_text(json.dumps({"seal_sha256": "x", "seal_ok": True, "n_files": 1, "mismatches": []}),
                                        encoding="utf-8")
    X.main(["--explore", str(ex), "--final", str(fin)])
    md = (ex / "explore_report.md").read_text(encoding="utf-8")
    assert md.count(X.TAG) >= 3 and "확증: v2 최종" in md and "동점" in md and "겹친다" in md
    assert X.overlap({"lo": 0.4, "hi": 0.6}, {"lo": 0.59, "hi": 0.7}) and not X.overlap({"lo": 0.4, "hi": 0.5}, {"lo": 0.51, "hi": 0.7})
