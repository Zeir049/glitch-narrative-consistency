"""judge 층 기준선 고정 — 동결 열(nemotron-3-ultra:free, k=3, 다수결 2/3) 원시 판정 결과를 재생해
Weave `handoff/longitudinal_report.md` §2~§6 · `final_report.md` §1 의 수치를 그대로 재현하는지 본다.

LLM·네트워크 호출 없음, 수 초. 숫자가 바뀌면 채점 이식(scripts/eval/judge_eval.py)이나
baseline/ 데이터가 바뀐 것이다 — 의도한 변화가 아니면 고치지 말고 원인을 찾는다.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load():
    name = "judge_eval"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "eval" / "judge_eval.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


JE = _load()


@pytest.fixture(scope="module")
def rep() -> dict:
    return JE.score(JE.RecordedJudge(), rerun=JE.RecordedJudge(rerun=True))


def _counts(m: dict) -> tuple[int, int, int, int]:
    return m["TP"], m["n_pos"], m["FP"], m["n_neg"]


def test_total_matches_final_report(rep):
    t = rep["total"]
    assert rep["works"] == ["wm", "q1", "q3"]
    assert _counts(t) == (18, 60, 3, 60)
    assert round(t["recall"], 3) == 0.300 and round(t["fpr"], 3) == 0.050
    assert tuple(round(x, 3) for x in t["recall_ci"]) == (0.199, 0.425)
    assert tuple(round(x, 3) for x in t["fpr_ci"]) == (0.017, 0.137)
    assert t["conditional_precision"] == {"0.01": 0.057, "0.02": 0.109, "0.05": 0.24, "0.44": 0.825}


def test_per_work_and_lowo(rep):
    pw = {w: _counts(r["metrics"]) for w, r in rep["per_work"].items()}
    assert pw == {"wm": (15, 20, 1, 10), "q1": (0, 20, 1, 25), "q3": (3, 20, 1, 25)}
    lowo = {k: _counts(v) for k, v in rep["leave_one_work_out"].items()}
    assert lowo == {"without_wm": (3, 40, 2, 50), "without_q1": (18, 40, 2, 35),
                    "without_q3": (15, 40, 2, 35)}


def test_by_type(rep):
    tot = {t: (c["TP"], c["n"]) for t, c in rep["total"]["by_type"].items()}
    assert tot == {"상태": (8, 24), "설정": (4, 18), "지식": (6, 18)}
    per = {w: {t: (c["TP"], c["n"]) for t, c in r["by_type"].items()} for w, r in rep["per_work"].items()}
    assert per["wm"] == {"상태": (7, 8), "설정": (3, 6), "지식": (5, 6)}
    assert per["q1"] == {"상태": (0, 8), "설정": (0, 6), "지식": (0, 6)}
    assert per["q3"] == {"상태": (1, 8), "설정": (1, 6), "지식": (1, 6)}


def test_miss_attribution(rep):
    q1 = rep["per_work"]["q1"]["attribution_2_7"]
    assert q1["ledger_absent"] == [f"scene_{n:02d}" for n in (3, 9, 13, 21, 22, 35)]
    assert q1["judge"] == [f"scene_{n:02d}" for n in (1, 2, 6, 8, 14, 15, 17, 24, 28, 29, 32, 37, 38, 45)]
    q3 = rep["per_work"]["q3"]["attribution_2_7"]
    assert q3["ledger_absent"] == [f"scene_{n:02d}" for n in (4, 5, 13, 28, 36, 40, 42)]
    assert q3["judge"] == [f"scene_{n:02d}" for n in (8, 11, 12, 16, 21, 24, 29, 33, 35, 41)]
    wm = rep["per_work"]["wm"]["attribution_2_7"]
    assert wm["misses"] == [f"scene_{n:02d}" for n in (13, 17, 22, 25, 29)] and not wm["has_map"]


def test_authors_and_rerun(rep):
    hc = rep["total"]["human_control"]
    assert (hc["n_pos"], hc["TP"], hc["n"] - hc["n_pos"], hc["FP"]) == (6, 2, 4, 0)
    llm = [rep["per_work"][w]["by_author"]["llm:claude-opus-5"] for w in ("q1", "q3")]
    assert (sum(a["TP"] for a in llm), sum(a["n_pos"] for a in llm),
            sum(a["FP"] for a in llm), sum(a["n_neg"] for a in llm)) == (1, 34, 2, 46)
    rr = {w: (r["rerun"]["same_majority"], r["rerun"]["n"]) for w, r in rep["per_work"].items()}
    assert rr == {"wm": (6, 6), "q1": (9, 9), "q3": (9, 9)}
    assert rep["total"]["rerun"]["reproducibility"] == 1.0


def test_paired_bootstrap_properties():
    pos = [f"w:s{i}" for i in range(20)]
    a = {k: i < 6 for i, k in enumerate(pos)}
    same = JE.paired_bootstrap_recall_diff(a, a, pos, n_boot=500)
    assert same["diff"] == 0 and same["ci"] == (0.0, 0.0)
    b = {k: i < 10 for i, k in enumerate(pos)}
    r1 = JE.paired_bootstrap_recall_diff(a, b, pos, n_boot=2000, seed=7)
    r2 = JE.paired_bootstrap_recall_diff(a, b, pos, n_boot=2000, seed=7)
    assert r1 == r2  # 시드 고정 → 같은 구간
    assert r1["diff"] == pytest.approx(0.2) and r1["ci"][0] > 0 and r1["ci"][0] <= 0.2 <= r1["ci"][1]
    with pytest.raises(ValueError):
        JE.paired_bootstrap_recall_diff(a, {}, pos)


def test_learned_stub_runs_without_llm(rep, monkeypatch):
    """learned 경로(원장 재생 + checker 항목 + 판정기 교체)가 LLM 없이 4작품을 돈다 — stub 은 전부 '정상'."""
    from app.pipeline import weave as W

    def bomb(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("learned 모드에서 LLM 호출")
    monkeypatch.setattr(W.w_llm, "complete", bomb)
    seen = []

    class Spy(JE._AlwaysNormal):
        def judge_group(self, group, ledger, corpus, registry, checker_items):  # noqa: ANN001
            seen.append((group["key"], len(group["events"]), bool(corpus), bool(registry.get("worldview_rules"))))
            return super().judge_group(group, ledger, corpus, registry, checker_items)

    lr = JE.score(JE.LearnedJudge(Spy()))
    assert lr["works"] == ["wm", "q1", "q3", "sherlock"]
    assert _counts(lr["total"]) == (0, 80, 0, 85)
    assert len(seen) == 30 + 45 * 3 and all(n > 0 and c and r for _, n, c, r in seen)
    cmp_ = JE.compare(rep, lr, n_boot=2000)
    assert cmp_["works"] == ["wm", "q1", "q3"] and cmp_["n_pos"] == 60
    assert cmp_["diff"] == pytest.approx(-0.3) and cmp_["ci"][1] < 0 and cmp_["a_only"] == 18
