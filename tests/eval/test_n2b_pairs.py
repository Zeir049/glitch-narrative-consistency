# -*- coding: utf-8 -*-
"""n2b_pairs 단위 테스트 — 겹침 재기·전제값 추출·항목 판정(렌더링은 L1 데이터가 있을 때만)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "eval"))
import n2b_pairs as N  # noqa: E402


def test_premise_value_per_kind():
    assert N.premise_value("A.body_status = 로봇 신체 (미래) (since EVENT_1_6 · 1화) | 발췌: x", "ledger") == "로봇 신체 (미래)"
    assert N.premise_value("플레이어이(가) 앎: 해킹은 1년 전 시작 (via told, since E)", "knowledge") == "해킹은 1년 전 시작"
    assert N.premise_value("세계관 규칙 RULE_002: 대화는 음성인식", "rule") == "대화는 음성인식"
    assert N.premise_value("[1화] 제목: 요약 문장이다.", "past") == "요약 문장이다."


def test_jaccard_and_overlap_clause():
    assert N.jaccard("로봇 신체", "로봇 신체") == 1.0
    assert N.jaccard("가나다", "라마바") == 0.0
    pv = "클라인이 자신을 소개하고, 소마 체제의 존재를 설명한다"
    # 한 절을 그대로 베끼면 전체 대비 낮아도 절 단위로는 높다
    assert N.jaccard("소마 체제의 존재를 설명한다", pv) < N.overlap("소마 체제의 존재를 설명한다", pv)
    assert N.overlap("전혀 다른 말", pv) < 0.05


def test_wants_neg_share_is_near_30_percent():
    n = sum(N.wants_neg(f"s:{i:03d}") for i in range(1000))
    assert 240 <= n <= 360


def test_has_neg():
    assert N.has_neg("연락 수단이 없다") and not N.has_neg("빈손")


def test_task_md_has_rules_and_examples(tmp_path):
    v = [{"stem": "s:01", "field": "possesses", "entity": "A", "type": "소유·상태", "premise_kind": "ledger", "premise": "p",
          "premise_value": "pv", "orig_ok": {"assumes": "a", "summary": "s", "quote": "q"},
          "orig_bad": {"assumes": "b", "summary": "t", "quote": "r"}, "negated_expression": True}]
    md = N.task_md("s", v, [{"entity": "E", "field": "knows", "assumes": "x", "quote": "y"}], tmp_path / "o.json", {"s:01": "미달"})
    assert "부정표현: 예" in md and "지난번 결과가 목표 미달" in md and "E.knows → x" in md and str(tmp_path / "o.json") in md


@pytest.mark.skipif(not N.L1_PATH.exists(), reason="L1 데이터 없음")
def test_judge_item_on_real_pair():
    pairs = N.load_pairs()
    stem = sorted(pairs)[0]
    ok0, bad0 = pairs[stem]
    pv = N.premise_value(ok0["text_a"], ok0["premise_kind"])
    mk = lambda assumes, summary: {"assumes": assumes if ok0["hyp_spec"]["field"] else None, "summary": summary}  # noqa: E731
    # 겹침 큰 일관판 → 미달
    rows, why = N.judge_item({"stem": stem, "ok": mk(pv, pv), "bad": mk("zzz다른것", "zzz 다른 요약")}, pairs, {})
    assert rows is None and why.startswith("미달")
    rows, why = N.judge_item({"stem": stem, "skip": True, "why": "x"}, pairs, {})
    assert why.startswith("agent_skip")
    rows, why = N.judge_item({"stem": "nope"}, pairs, {})
    assert why.startswith("schema")
