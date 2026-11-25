"""PD-train 조립 v2(scripts/v2/build_v2.py) — A− 걸러내기·판정 걸러내기·C 처리·맥락 채우기·혼합 점검."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "v2"))
import build_v2 as B  # noqa: E402
import v2_common as C  # noqa: E402

from app.pipeline.judge_v2.text import chunk_prefix  # noqa: E402


def test_edit_regions_word_level_and_merge():
    assert len(B.edit_regions("그는 삼십 전을 받았다.", "그는 오십 전을 받았다.")) == 1
    regs = B.edit_regions("가 나 다 라 마 바 사", "가 X 다 라 마 Y 사")
    assert len(regs) == 2                                         # 사이에 같은 어절 3개 → 두 영역
    assert len(B.edit_regions("가 나 다 라", "가 X 다 Y")) == 1   # 사이 1개 이하 → 한 영역


def test_a_minus_reasons_i_ii_iii():
    claim = "그는 학교 문을 돌아 나왔다."
    ok = (["그는 동광학교 앞에서 손님을 내렸다."], ["그는 종로 약방 앞에서 손님을 내렸다."])
    assert B.a_minus_reasons(claim, *ok)[0] == []                  # '학교' 겹침
    unrelated = (["비가 추적추적 내렸다. 그는 동광학교 앞에 있었다."], ["눈이 추적추적 내렸다. 그는 동광학교 앞에 있었다."])
    assert "i_no_overlap" in B.a_minus_reasons(claim, *unrelated)[0]
    again = (["손님은 동광학교 학생이다. 동광학교 문은 크다."], ["손님은 약방 학생이다. 동광학교 문은 크다."])
    rs, orig = B.a_minus_reasons(claim, *again)
    assert "ii_orig_elsewhere" in rs and orig == "동광학교"
    multi = (["가 학교 다 라 마 바 사"], ["가 약방 다 라 마 바 X"])
    assert "iii_multi_edit" in B.a_minus_reasons("학교에 갔다", *multi)[0]
    assert B.a_minus_reasons(claim, ["a", "b"], ["a2", "b2"])[0] == ["shape"]


def test_fill_same_for_pair_k4_ban_and_deterministic():
    prior = [f"학교 이야기 {i}. 그는 학교에 갔다." if i % 2 else f"시장 이야기 {i}. 동광학교 이름이 나온다." for i in range(12)]
    key = [prior[3]]
    slots, picked, info = B.fill("p1", "그는 학교 문을 나왔다.", key, prior, ban_value="동광학교")
    assert len(picked) == 3 and all(prior.index(prior[j]) == j for j in picked)
    assert all("동광학교" not in prior[j] for j in picked) and info["banned"] == 6
    assert 3 not in picked
    assert B.fill("p1", "그는 학교 문을 나왔다.", key, prior, ban_value="동광학교")[:2] == (slots, picked)
    ctx = B.assemble(key, [int(s) for s in slots], [prior[j] for j in picked])
    assert len(ctx) == 4 and ctx[int(slots[0])] == key[0]
    early = B.fill("p2", "학교", [prior[0]], prior[:2], ban_value=None)
    assert len(early[1]) <= 1                                     # 앞선 조각이 모자라면 있는 만큼만


def test_mix_check_gate():
    rows = [{"claim": "가나다", "context": ["가나" * 50], "label": "모순"} for _ in range(20)] + \
           [{"claim": "가나다", "context": ["가나" * 5], "label": "무모순"} for _ in range(20)]
    m = B.mix_check(rows)
    assert m["features"]["context_len"]["dir_free"] == 1.0 and m["gate"] == "fail" and not m["target_met"]
    even = [{"claim": "가나다", "context": ["가나" * 10], "label": lab} for lab in ("모순", "무모순") * 10]
    assert B.mix_check(even)["gate"] == "pass"


def _source(tmp_path: Path) -> tuple[Path, list[str]]:
    src = tmp_path / "source"
    src.mkdir()
    paras = [f"문단 {i}. 김첨지는 학교 앞에서 손님을 기다렸다. 비가 왔다. 동광학교 학생이 나왔다." * 3 for i in range(40)]
    (src / "작가__작품.txt").write_text("\n".join(paras), encoding="utf-8")
    return src, chunk_prefix(B.load_work(src / "작가__작품.txt"))


def test_build_end_to_end(tmp_path):
    src, ch = _source(tmp_path)
    assert len(ch) >= 6
    claim_chunk = 5
    claim = ch[claim_chunk][:30].strip()

    def row(i, typ, label, ctx_ids, ctx, pair, claim_text=claim):  # noqa: ANN001, ANN202
        return {"id": f"x:{typ}{i}{'m' if label == '모순' else 'n'}", "type": typ, "work": "작가__작품", "split": "train",
                "claim": claim_text, "context": ctx, "label": label, "pair_of": pair, "claim_chunk": f"c{claim_chunk:03d}",
                "ctx_ids": [f"c{j:03d}" for j in ctx_ids]}
    edited = ch[1].replace("동광학교", "종로약방", 1)
    rows = [row(1, "A", "무모순", [1], [ch[1]], "x:A1m"), row(1, "A", "모순", [1], [edited], "x:A1n"),
            row(2, "B", "무모순", [2], [ch[2]], "x:B2m"), row(2, "B", "모순", [2], [ch[2]], "x:B2n", claim + " 아니"),
            row(3, "B", "무모순", [3], [ch[3]], "x:B3m"), row(3, "B", "모순", [3], [ch[3]], "x:B3n", claim + " 다른"),
            row(4, "C", "무모순", [4], [ch[4]], "x:C4m"), row(4, "C", "모순", [4], [ch[4][:50]], "x:C4n")]
    out, meta = B.build(rows, src)
    assert meta["pairs"]["C_v1_dropped"] == 1 and not any(r["type"] == "C" for r in out)
    for pid in ("x:B2", "x:B3"):
        a, b = [r for r in out if r["id"].startswith(pid)]
        assert a["context"] == b["context"] and a["fill_ids"] == b["fill_ids"] and len(a["context"]) <= 4
    kept_a = [r for r in out if r["type"] == "A"]
    if kept_a:
        n, m = sorted(kept_a, key=lambda r: r["label"])
        assert n["fill_ids"] == m["fill_ids"] and n["key_slots"] == m["key_slots"]
    out2, meta2 = B.build(rows, src, judgments={"x:B2m": "충돌", "x:B3m": "근거 없음", "x:A1m": "충돌"})
    ids = {r["id"] for r in out2}
    assert "x:B2m" in ids and "x:B2n" in ids and "x:B3m" not in ids and "x:B3n" not in ids   # 짝도 함께 빠진다
    assert meta2["judgments_applied"] and meta2["pairs"]["B_dropped_judgment"] == 1
    import pytest
    bad = [dict(r) for r in rows]
    bad[2]["context"] = ["다른 글"]
    bad[3]["context"] = ["다른 글"]
    with pytest.raises(SystemExit):
        B.build(bad, src)                                          # 조각화가 생성 때와 다르면 멈춘다


def test_pair_property_mix_report_and_c_v2_slot(tmp_path):
    import pytest
    rows = [{"id": "a1m", "pair_of": "a1n", "type": "A", "label": "모순", "claim": "가나다", "context": ["x"],
             "fill_ids": ["c001"], "key_slots": [0]},
            {"id": "a1n", "pair_of": "a1m", "type": "A", "label": "무모순", "claim": "가나다", "context": ["y"],
             "fill_ids": ["c001"], "key_slots": [0]},
            {"id": "b1m", "pair_of": "b1n", "type": "B", "label": "모순", "claim": "다른", "context": ["z"],
             "fill_ids": [], "key_slots": [0]},
            {"id": "b1n", "pair_of": "b1m", "type": "B", "label": "무모순", "claim": "주장", "context": ["w"],
             "fill_ids": [], "key_slots": [0]}]
    p = B.pair_property(rows)
    assert p["violations"] == {"B_context_differs": 1} and p["pairs_checked"] == {"A": 1, "B": 1}
    rows[1]["claim"] = "가나다 "
    assert B.pair_property(rows)["violations"]["A_claim_differs"] == 1
    d = [{"claim": "짧", "context": ["q"], "label": "무모순", "type": "D"} for _ in range(5)]
    rep = B.mix_report(rows, d)
    assert set(rep["sets"]) == {"all+D", "A", "A+D", "B", "B+D"} and rep["worst"]["set"] is not None
    assert "claim_len" in rep["sets"]["A+D"]["features"]
    src, _ = _source(tmp_path)
    with pytest.raises(SystemExit):
        B.build([], src, c_v2=[{"id": "c"}])                       # C_v2 는 아직 받지 않는다(자리만)


def test_judgment_runs_before_machine_filter(tmp_path):
    src, ch = _source(tmp_path)
    claim = ch[5][:30].strip()
    unrelated = ch[1].replace("비가", "눈이", 1)                      # 주장과 무관한 편집 — (i) 대상
    base = {"type": "A", "work": "작가__작품", "split": "train", "claim": claim, "claim_chunk": "c005", "ctx_ids": ["c001"]}
    rows = [base | {"id": "x:A1n", "label": "무모순", "context": [ch[1]], "pair_of": "x:A1m"},
            base | {"id": "x:A1m", "label": "모순", "context": [unrelated], "pair_of": "x:A1n"}]
    _o, meta = B.build(rows, src, judgments={"x:A1m": "근거 없음"})
    assert meta["pairs"]["A_dropped_judgment"] == 1 and "A_dropped_machine" not in meta["pairs"]
    _o, meta = B.build(rows, src, judgments={"x:A1m": "충돌"})
    assert meta["pairs"]["A_after_judgment"] == 1 and meta["pairs"]["A_dropped_machine"] == 1


def test_mech_pass_lists_unjudged_a_minus(tmp_path):
    import json as _j
    import types
    rows = [{"id": "a1n", "pair_of": "a1m", "type": "A", "label": "무모순", "claim": "그는 학교 문을 나왔다.",
             "context": ["그는 동광학교 앞에 있었다."]},
            {"id": "a1m", "pair_of": "a1n", "type": "A", "label": "모순", "claim": "그는 학교 문을 나왔다.",
             "context": ["그는 종로 약방 앞에 있었다."]},
            {"id": "a2n", "pair_of": "a2m", "type": "A", "label": "무모순", "claim": "그는 학교 문을 나왔다.",
             "context": ["비가 내렸다. 그는 시장에 있었다."]},
            {"id": "a2m", "pair_of": "a2n", "type": "A", "label": "모순", "claim": "그는 학교 문을 나왔다.",
             "context": ["눈이 내렸다. 그는 시장에 있었다."]},
            {"id": "a3n", "pair_of": "a3m", "type": "A", "label": "무모순", "claim": "x 학교", "context": ["학교 y"]},
            {"id": "a3m", "pair_of": "a3n", "type": "A", "label": "모순", "claim": "x 학교", "context": ["약방 y"]}]
    pd = tmp_path / "pd.jsonl"
    C.write_jsonl(pd, rows)
    jd = tmp_path / "j.jsonl"
    C.write_jsonl(jd, [{"id": "a3m", "verdict": "충돌"}])
    out = tmp_path / "pass.json"
    B.mech_pass(types.SimpleNamespace(pd=pd, judgments=jd, out=out))
    r = _j.loads(out.read_text(encoding="utf-8"))
    assert r["targets"] == 2 and r["passed_ids"] == ["a1m"] and r["drop_reasons_overlapping"] == {"i_no_overlap": 1}
    assert len(r["input"]["pd_sha256"]) == 64


def test_c_v2_merge_ban_g_pair_property_and_leakage(tmp_path):
    import json as _j
    src, ch = _source(tmp_path)
    g = "동광학교 학생이 나왔다."
    gp = "아무도 나오지 않았다."
    assert g in ch[2]
    claim = ch[5][:30].strip()
    base = {"type": "C", "work": "작가__작품", "split": "train", "claim": claim, "claim_chunk": "c005", "ctx_ids": ["c002"],
            "g_text": g, "g_prime_text": gp, "c_relaxed": True}
    c = [base | {"id": "cv2:C1n", "label": "무모순", "context": [ch[2]], "pair_of": "cv2:C1m"},
         base | {"id": "cv2:C1m", "label": "모순", "context": [ch[2].replace(g, gp)], "pair_of": "cv2:C1n"},
         base | {"id": "cv2:C2n", "label": "무모순", "context": [ch[2]], "pair_of": "cv2:C2m"},
         base | {"id": "cv2:C2m", "label": "모순", "context": [ch[2].replace(g, gp)], "pair_of": "cv2:C2n"}]
    v1 = [{"id": "x:C9n", "type": "C", "label": "무모순", "pair_of": "x:C9m", "work": "작가__작품"},
          {"id": "x:C9m", "type": "C", "label": "모순", "pair_of": "x:C9n", "work": "작가__작품"}]
    out, meta = B.build(v1, src, judgments={}, c_v2=c, c_judgments={"cv2:C1m": "충돌", "cv2:C2m": "근거 없음"})
    assert meta["pairs"]["C_v1_dropped"] == 1 and meta["pairs"]["C_kept"] == 1 and meta["pairs"]["C_c_relaxed_kept"] == 1
    assert {r["id"] for r in out} == {"cv2:C1n", "cv2:C1m"}
    assert all(g not in ch[int(f[1:])] for f in out[0]["fill_ids"])           # 원래 g 가 든 조각은 채움에서 뺀다
    assert B.pair_property(out)["n_violations"] == 0
    broken = [dict(r) for r in out]
    broken[1]["context"] = [x + " 덧" for x in broken[1]["context"]]
    assert B.pair_property(broken)["violations"].get("C_context_not_only_g") == 1
    sp = tmp_path / "split.json"
    sp.write_text(_j.dumps({"works": [{"author": "작가", "title": "작품", "split": "train", "id": "w1"}]}, ensure_ascii=False),
                  encoding="utf-8")
    assert B.leakage(out, sp)["unknown_or_eval_author"] == 0
    assert B.leakage([{"id": "e", "work": "김유정__봄봄", "split": "train"}], sp)["unknown_or_eval_author"] == 1


def test_s_check_counts_and_features(tmp_path):
    src, ch = _source(tmp_path)
    s = [{"id": f"S{i}", "work": "작가__작품", "split": "dev" if i < 2 else "train", "prefix": "\n".join(ch[:4]),
          "target_P": ch[5].replace("학교", "약방", 1), "target_N": ch[5].replace("학교", "교실", 1), "region": "['c005']",
          "verified": True} for i in range(5)]
    r = B.s_check(s, src)
    assert r["pairs"] == 5 and r["split"] == {"dev": 2, "train": 3} and r["format"] == {}
    assert set(r["features"]) == {"edit_distance", "length_diff", "overlap"} and r["claims"] > 0
    s[0]["target_N"] = s[0]["target_P"]
    assert B.s_check(s, src)["format"]["P_equals_N"] == 1
