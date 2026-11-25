"""반복 추출 일치도(scripts/eval/extract_agreement.py) 고정 — 데이터가 고정이므로 수치도 고정. LLM 없음."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load():
    name = "extract_agreement"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "eval" / "extract_agreement.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


EA = _load()


def test_agreement_numbers_are_stable():
    res = EA.run()
    assert res["scenes"] == 30
    assert res["counts"]["o"]["as_slot"] == 28 and res["counts"]["r1"]["as_slot"] == 174
    u = res["units"]
    assert u["as_slot"]["reextract_vs_reextract"]["mean"] == 0.777
    assert u["as_slot"]["original_vs_reextract"]["mean"] == 0.147
    assert u["sc_slot"]["reextract_vs_reextract"]["mean"] == 0.567
    assert u["sc_transition"]["reextract_vs_reextract"]["mean"] == 0.229
    assert u["as_value"]["reextract_unanimity"]["in_all_3"] == 3


def test_jaccard_definition():
    data = {"s1": {"a": {"as_slot": {(1,), (2,)}}, "b": {"as_slot": {(2,), (3,)}}},
            "s2": {"a": {"as_slot": set()}, "b": {"as_slot": set()}}}
    j, inter, union = EA.pair_j(data, "a", "b", "as_slot")
    assert (inter, union) == (1, 3) and abs(j - 1 / 3) < 1e-9
    assert EA.dice("오른손 부상", "오른손 부상") == 1.0 and EA.dice("가나", "다라") == 0.0
