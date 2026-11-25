"""Claude 생성 쌍 도구(scripts/eval/claude_pairs.py, 실험 L) — 렌더링은 build_pairs 와 같은 출력, 검증기는 틀린 입력을 잡는다.

L1 은 저장소의 WM 확정 원장(tests/eval/weave_checker_corpus)을 쓰므로 받은 외부 파일이 필요 없다. L2 는 합성 항목.
셜록·Q1·Q3 는 어디서도 열지 않는다.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EVAL = ROOT / "scripts" / "eval"
for _p in (str(ROOT), str(EVAL)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _load(name: str, path: Path):  # noqa: ANN202
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


CP = _load("claude_pairs", EVAL / "claude_pairs.py")
TL = _load("train_lib", EVAL / "train_lib.py")
from app.pipeline.pairs import PAIRS_VERSION, build_pairs  # noqa: E402


@pytest.fixture(scope="module")
def ctx3():  # noqa: ANN201
    return CP.L1Context(3)


@pytest.fixture(scope="module")
def cat3(ctx3):  # noqa: ANN001, ANN201
    return CP.l1_catalog(ctx3)


SCENE = {"scene_id": "wm_e3s02", "episode": 3,
         "text": "알라인은 이 말을 마지막으로 앞장서서 건물 안으로 들어갔다.\n\n오늘은 폭발 사고로부터 거의 일주일이 지난 토요일."}


def _ref(cat: list[dict], kind: str, **kw) -> dict:
    return next(r for r in cat if r["kind"] == kind and all(r.get(k) == v for k, v in kw.items()))


def _variant(sentence: str, assumes: str | None = "값", title: str = "제목", summary: str = "요약") -> dict:
    v = {"sentence": sentence, "title": title, "summary": summary}
    if assumes is not None:
        v["assumes"] = assumes
    return v


def _l1_item(cat: list[dict]) -> dict:
    p = _ref(cat, "ledger", entity="CHAR_002", field="body_status")
    return {"premise_ref": p["ref"], "type": "생사·신체", "entity": "CHAR_002", "field": "body_status",
            "participants": ["CHAR_002"], "orig": "알라인은 이 말을 마지막으로 앞장서서 건물 안으로 들어갔다.",
            "bad": _variant("나는 멀쩡한 로봇 몸으로 알라인보다 먼저 건물 안으로 뛰어 들어갔다.", "로봇 신체 정상"),
            "ok": _variant("나는 화면에 노이즈가 낀 로봇 몸으로 알라인을 따라 건물 안으로 들어갔다.", "로봇 신체 손상(화면 노이즈)"),
            "note": "테스트"}


# ---------------------------------------------------------------- 문자열 보조

def test_negation_only_detects_pure_negation_variants():
    assert CP.negation_only("소년은 집에 갔다.", "소년은 집에 가지 않았다.") is True
    assert CP.negation_only("그는 문을 열었다.", "그는 문을 열지 않았다.") is True
    assert CP.negation_only("그는 서울에 있었다.", "그는 부산에 있었다.") is False
    assert CP.negation_only("사흘 뒤에 도착했다.", "열흘 뒤에 도착했다.") is False


def test_assign_val_groups_deterministic_and_at_least_ten_percent():
    ids = [f"g{i}" for i in range(24)]
    a = CP.assign_val_groups(ids, 0.1, seed=1)
    assert a == CP.assign_val_groups(list(reversed(ids)), 0.1, seed=1)
    assert len(a) == 3 and a <= set(ids)
    assert len(CP.assign_val_groups(["only"], 0.1)) == 1
    assert CP.assign_val_groups([], 0.1) == set()


def test_wm_scene_chunks_split_on_heads_markers_and_length():
    para = "가나다라마바사 " * 20   # 약 140자
    txt = ("- 1화 : 첫째 -\n" + para + "\n"
           "2화 : 둘째 -\n" + "\n\n".join([para] * 4) + "\n**\n" + "\n\n".join([para] * 3) + "\n"
           "- 3화 : 셋째 -\n" + "\n\n".join([para] * 2) + "\n")
    ch = CP.wm_scene_chunks(txt, episodes=(2, 3), max_chars=420, min_chars=200)
    assert {c["episode"] for c in ch} == {2, 3}                      # 1화는 앞선 원장이 없어 제외
    assert all(len(c["text"]) <= 420 * 1.4 for c in ch)
    assert [c["scene_id"] for c in ch if c["episode"] == 2][0] == "wm_e2s01"
    assert len({c["scene_id"] for c in ch}) == len(ch)


# ---------------------------------------------------------------- L1: 카탈로그

def test_l1_catalog_is_build_pairs_output_with_past_only_rules(ctx3, cat3):
    assert {r["kind"] for r in cat3} <= set(CP.PREMISE_KINDS)
    assert {"ledger", "knowledge", "rule", "past"} <= {r["kind"] for r in cat3}
    assert len({r["ref"] for r in cat3}) == len(cat3)
    # 3화 시점: 규칙은 first_episode < 3 만, 과거 사건은 1..2화만 (미래 누출 없음)
    rules = [r for r in cat3 if r["kind"] == "rule"]
    assert len(rules) == len([x for x in ctx3.registry["worldview_rules"]]) and all("RULE_" in r["text"] for r in rules)
    assert max(int(e["episode"]) for e in ctx3.corpus) == 2
    assert all(r["text"].startswith(("[1화]", "[2화]")) for r in cat3 if r["kind"] == "past")
    # 모든 카탈로그 전제는 실제 build_pairs 출력에 그대로 나온다
    spec = {"title": "t", "summary": "s", "excerpt": "e", "participants": list(ctx3.chars), "entity": None, "field": None,
            "assumes": None, "quote": None}
    emitted = {(p.premise_kind, p.premise_id, p.premise_text) for p in ctx3.pairs(spec, "EVENT_X")}
    for r in cat3:
        if r["kind"] in ("rule", "past"):
            assert (r["kind"], r["premise_id"], r["text"]) in emitted


# ---------------------------------------------------------------- L1: 항목 → 행

def test_l1_item_rows_render_with_build_pairs_and_copy_premise(ctx3, cat3):
    item = _l1_item(cat3)
    rows = CP.l1_item_rows(item, scene=SCENE, ctx=ctx3, catalog=cat3, index=1, task="t")
    m, n = rows
    assert (m["label"], n["label"]) == ("모순", "정상") and m["pair_of"] == n["id"] and n["pair_of"] == m["id"]
    prem = _ref(cat3, "ledger", entity="CHAR_002", field="body_status")
    assert m["premise"] == n["premise"] == m["text_a"] == prem["text"]        # 전제는 카탈로그(원장)에서 그대로 복사
    assert m["hypothesis"] != n["hypothesis"] and m["group_id"] == n["group_id"] == "wm_e3s02"
    # 서비스 함수로 독립적으로 만든 같은 사건의 가설 문자열과 정확히 같다
    ev = {"event_id": "EVENT_3_X", "episode": 3, "timeline": CP.L1_TIMELINE, "title": "제목", "summary": "요약",
          "source_text": item["bad"]["sentence"], "participant_character_ids": ["CHAR_002"],
          "assumed_states": [{"entity": "CHAR_002", "field": "body_status", "assumes": "로봇 신체 정상",
                              "source_text": item["bad"]["sentence"]}]}
    ps = build_pairs({"key": "k", "events": [ev]}, ctx3.ledger, ctx3.corpus, ctx3.registry, [], max_premises=10 ** 6,
                     quotas=dict(CP.ALL_KINDS))
    assert m["hypothesis"] in {p.hypothesis_text for p in ps}
    assert m["hypothesis"].startswith("[3화] 플레이어 ('나', 알파)의 body_status: 로봇 신체 정상 (근거: ")
    assert m["pairs_version"] == PAIRS_VERSION == "2" and m["source_kind"] == "claude"
    TL.check_labels(rows)                                                       # 학습 라벨 집합과 호환
    assert CP.validate_rows(rows, rerender=True)["errors"] == []


def test_l1_event_hypothesis_for_rule_and_knowledge_premises(ctx3, cat3):
    rule = _ref(cat3, "rule")
    it = {"premise_ref": rule["ref"], "type": "규칙", "entity": None, "field": None, "participants": ["CHAR_002"],
          "orig": SCENE["text"].split("\n")[0],
          "bad": _variant("알라인이 건물 안에서 큰 소리로 노래했다.", None, "소란", "알라인이 소란을 피운다."),
          "ok": _variant("알라인이 건물 안에서 조용히 걸었다.", None, "이동", "알라인이 걷는다.")}
    rows = CP.l1_item_rows(it, scene=SCENE, ctx=ctx3, catalog=cat3, index=2, task="t")
    assert rows[0]["hypothesis"].startswith("[3화] 사건 소란 | 사건: ") and rows[0]["premise_kind"] == "rule"
    know = _ref(cat3, "knowledge", entity="CHAR_001")
    it2 = dict(it, premise_ref=know["ref"], type="앎", entity="CHAR_001", field="knows", participants=["CHAR_001"],
               bad=_variant("클라인은 폭발을 전혀 몰랐다.", "폭발을 모름"), ok=_variant("클라인은 폭발 소식을 들어 알았다.", "폭발을 앎"))
    rows2 = CP.l1_item_rows(it2, scene=SCENE, ctx=ctx3, catalog=cat3, index=3, task="t")
    assert "이(가) 앎:" in rows2[0]["premise"] and rows2[0]["premise_kind"] == "knowledge"


@pytest.mark.parametrize("patch,needle", [
    ({"premise_ref": "P99"}, "premise_ref"),
    ({"orig": "장면에 이런 문장은 정말로 없습니다"}, "orig"),
    ({"type": "기타"}, "type"),
    ({"entity": "CHAR_999"}, "entity"),
    ({"field": "mood"}, "field"),
    ({"field": "location"}, "unreachable"),                       # ledger 전제는 자기 (엔티티, 필드)의 가설에서만 보인다
    ({"participants": ["CHAR_404"]}, "participants"),
    ({"bad": {"sentence": "", "title": "t", "summary": "s", "assumes": "a"}}, "schema"),
])
def test_l1_item_rejections(ctx3, cat3, patch, needle):
    item = deepcopy(_l1_item(cat3)) | patch
    with pytest.raises(CP.ItemError, match=needle):
        CP.l1_item_rows(item, scene=SCENE, ctx=ctx3, catalog=cat3, index=1, task="t")


def test_l1_rejects_copy_and_negation_shortcuts(ctx3, cat3):
    base = _l1_item(cat3)
    for key, msg in (("bad", "원문 그대로"), ("ok", "원문 그대로")):
        it = deepcopy(base)
        it[key]["sentence"] = base["orig"]
        with pytest.raises(CP.ItemError, match=msg):
            CP.l1_item_rows(it, scene=SCENE, ctx=ctx3, catalog=cat3, index=1, task="t")
    it = deepcopy(base)
    it["ok"]["sentence"] = it["bad"]["sentence"]
    with pytest.raises(CP.ItemError, match="모순판과 같은"):
        CP.l1_item_rows(it, scene=SCENE, ctx=ctx3, catalog=cat3, index=1, task="t")
    it = deepcopy(base)
    it["bad"]["sentence"] = "알라인은 이 말을 마지막으로 앞장서서 건물 안으로 들어가지 않았다."
    with pytest.raises(CP.ItemError, match="negation_only"):
        CP.l1_item_rows(it, scene=SCENE, ctx=ctx3, catalog=cat3, index=1, task="t")


# ---------------------------------------------------------------- L2

def _l2_item(kind: str = "past", sid: str = "flawed_fictions_5#1") -> dict:
    it = {"item_id": sid, "story_id": "flawed_fictions_5", "keep": True, "type": "생사·신체", "premise_kind": kind,
          "names": {"CHAR_001": "마르코", "CHAR_002": "엘레나"},
          "premise": {"title": "마르코의 부상", "summary": "마르코가 사고로 오른팔을 잃는다.",
                      "quote": "마르코는 사고로 오른팔을 잃었다."},
          "entity": "CHAR_001", "field": "body_status", "participants": ["CHAR_001"],
          "bad": _variant("마르코가 양손으로 사다리를 붙들고 올라갔다.", "양팔 멀쩡"),
          "ok": _variant("마르코가 왼손으로 사다리를 붙들고 올라갔다.", "오른팔 없음"), "note": "t"}
    if kind == "knowledge":
        it["premise"] |= {"character": "CHAR_002", "info": "마르코가 오른팔을 잃었다", "via": "told"}
    return it


def test_l2_past_premise_and_hypothesis_use_service_format():
    rows = CP.l2_item_rows(_l2_item("past"), index=1, task="t01")
    m, n = rows
    assert m["premise"].startswith("[") and "화] 마르코의 부상: 마르코가 사고로 오른팔을 잃는다. | 원문: 마르코는 사고로 오른팔을 잃었다." in m["premise"]
    assert m["premise_kind"] == "past" and m["premise"] == n["premise"]
    assert m["hypothesis"].startswith("[") and "마르코의 body_status: 양팔 멀쩡 (근거: 마르코가 양손으로" in m["hypothesis"]
    pe, he = m["prem_spec"]["episode"], m["hyp_spec"]["episode"]
    assert he > pe >= 1 and m["premise"].startswith(f"[{pe}화]") and m["hypothesis"].startswith(f"[{he}화]")
    assert (m["pair_of"], n["pair_of"]) == (n["id"], m["id"]) and m["contrast_group"] == n["contrast_group"]
    assert CP.validate_rows(rows, rerender=True)["errors"] == []


def test_l2_knowledge_premise_and_event_hypothesis():
    it = _l2_item("knowledge", "contradoc_9#1") | {"field": None, "entity": "CHAR_001"}
    rows = CP.l2_item_rows(it, index=2, task="t01")
    assert "엘레나이(가) 앎: 마르코가 오른팔을 잃었다 (via told, since EVENT_" in rows[0]["premise"]
    assert rows[0]["premise_kind"] == "knowledge" and rows[0]["hypothesis"].startswith(("[2화] 사건 ", "[3화] 사건 ", "[4화] 사건 ", "[5화] 사건 ", "[6화] 사건 "))
    assert CP.validate_rows(rows, rerender=True)["errors"] == []


@pytest.mark.parametrize("patch,needle", [
    ({"type": "x"}, "type"),
    ({"premise_kind": "ledger"}, "premise_kind"),
    ({"names": {"A": "x"}}, "names"),
    ({"field": "mood"}, "field"),
    ({"entity": "CHAR_009"}, "entity"),
    ({"item_id": ""}, "item_id"),
])
def test_l2_item_rejections(patch, needle):
    with pytest.raises(CP.ItemError, match=needle):
        CP.l2_item_rows(_l2_item() | patch, index=1, task="t")


def test_l2_rejects_same_sentence_and_copied_quote():
    it = _l2_item()
    it["ok"]["sentence"] = it["bad"]["sentence"]
    with pytest.raises(CP.ItemError, match="모순판과 같은"):
        CP.l2_item_rows(it, index=1, task="t")
    it = _l2_item()
    it["premise"]["quote"] = it["bad"]["sentence"]
    with pytest.raises(CP.ItemError, match="복사"):
        CP.l2_item_rows(it, index=1, task="t")


def test_l2_candidates_locate_windows_and_screen():
    story = ("<p>Marco lost his right arm in the accident. He never forgot it.</p>"
             "<p>Years later the village had a feast. Everyone came. Then Marco climbed the ladder with both hands.</p>")
    sherlock = "<p>Holmes lost his right arm in the accident. Years later Holmes climbed the ladder with both hands.</p>"
    tab = [{"story": story, "cont_error": 1.0, "example_id": "ff_1",
            "cont_error_lines": '<li>"Then Marco climbed the ladder with both hands."</li>',
            "contradicted_lines": '<li>"Marco lost his right arm in the accident."</li>', "cont_error_expl": "arm"},
           {"story": sherlock, "cont_error": 1.0, "example_id": "ff_2",
            "cont_error_lines": '<li>"Years later Holmes climbed the ladder with both hands."</li>',
            "contradicted_lines": '<li>"Holmes lost his right arm in the accident."</li>', "cont_error_expl": ""}]
    c = CP.l2_candidates([tab], {"pos": {}})
    assert [x["story_id"] for x in c] == ["ff_1"]                       # 셜록·드라큘라 인물이 든 이야기는 거른다
    assert "right arm" in c[0]["premise_en"] and "both hands" in c[0]["hypothesis_en"]
    assert c[0]["ctx_hyp"] and c[0]["ctx_premise"]
    md = CP.l2_task_md(c, "t01", Path("C:/x/out.json"))
    assert "item_id: ff_1#1" in md and "out_path: " in md


# ---------------------------------------------------------------- 검증기

def _rows(ctx3, cat3) -> list[dict]:
    return CP.l1_item_rows(_l1_item(cat3), scene=SCENE, ctx=ctx3, catalog=cat3, index=1, task="t")


def test_validate_flags_missing_pair_same_label_duplicates_and_tampering(ctx3, cat3):
    rows = _rows(ctx3, cat3)
    ok = CP.validate_rows(rows, rerender=True)
    assert ok["errors"] == [] and ok["l1_wm"]["labels"] == {"모순": 1, "정상": 1}
    assert ok["l1_wm"]["types"] == {"생사·신체": 1} and ok["l1_wm"]["premise_kinds"] == {"ledger": 1}

    assert any("짝" in e for e in CP.validate_rows(rows[:1])["errors"])                     # 짝 없음
    same = deepcopy(rows)
    same[1]["label"] = "모순"
    assert any("라벨이 같다" in e for e in CP.validate_rows(same)["errors"])
    dup = deepcopy(rows) + [deepcopy(r) | {"id": r["id"] + "x", "pair_of": r["pair_of"] + "x"} for r in rows]
    assert any("중복" in e for e in CP.validate_rows(dup)["errors"])
    tam = deepcopy(rows)
    tam[0]["hypothesis"] = tam[0]["text_b"] = tam[0]["hypothesis"] + " 몰래 고침"
    assert any("재렌더링 불일치" in e for e in CP.validate_rows(tam, rerender=True)["errors"])
    txt = deepcopy(rows)
    txt[0]["text_a"] = "다른 글"
    assert any("text_a/text_b" in e for e in CP.validate_rows(txt)["errors"])
    ver = deepcopy(rows)
    ver[0]["pairs_version"] = "1"
    assert any("pairs_version" in e for e in CP.validate_rows(ver)["errors"])


def test_validate_reports_repeated_ngrams(ctx3, cat3):
    rows = []
    for i in range(6):
        it = deepcopy(_l1_item(cat3))
        it["bad"]["sentence"] = f"나는 갑자기 이상하게 멀쩡한 몸으로 {i}번째 문을 열었다."
        it["ok"]["sentence"] = f"나는 천천히 노이즈 낀 몸으로 {i}번째 창을 닫았다."
        rows += CP.l1_item_rows(it, scene=SCENE, ctx=ctx3, catalog=cat3, index=i + 1, task="t")
    rep = CP.validate_rows(rows)["l1_wm"]
    assert rep["ngram_over_flag"] and rep["ngram_over_flag"][0]["frac"] == 1.0           # 모든 모순판이 같은 3-gram
    assert rep["val_groups"] == ["wm_e3s02"]


def test_build_cli_reads_agent_output_and_writes_jsonl(tmp_path, ctx3, cat3):
    work = tmp_path
    (work / "l1" / "out").mkdir(parents=True)
    (work / "l1" / "scenes.json").write_text(json.dumps({"chunks": [SCENE]}, ensure_ascii=False), encoding="utf-8")
    good = _l1_item(cat3)
    badp = deepcopy(good) | {"premise_ref": "P99"}
    (work / "l1" / "out" / "wm_e3s02.json").write_text(json.dumps(
        {"scene_id": "wm_e3s02", "items": [good, badp], "skipped": [{"why": "맥락 부족"}]}, ensure_ascii=False), encoding="utf-8")
    CP.main(["--work", str(work), "build", "l1"])
    rows = CP.read_jsonl(work / "l1_wm.jsonl")
    rej = CP.read_jsonl(work / "l1_rejects.jsonl")
    assert len(rows) == 2 and {r["label"] for r in rows} == {"모순", "정상"}
    assert [r["reason"].split(":")[0] for r in rej] == ["premise_ref", "agent_skipped"]
    CP.main(["--work", str(work), "validate", "--rerender"])
