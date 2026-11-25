"""학습 judge v2(app/pipeline/judge_v2, scripts/v2) — 문장 분리·조각·검색·렌더러·장면·D 생성·지표·학습 스모크."""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

from app.pipeline.judge_v2 import (Retriever, build_scene, chunk_prefix, guess_names, render_ids, render_text,
                                   scene_score, split_sentences)
from app.pipeline.judge_v2 import render as R
from app.pipeline.judge_v2.retrieve import normalize

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "v2"))
import v2_common as C  # noqa: E402

# ---------------------------------------------------------------- 문장 분리


def test_split_sentences_dialogue_and_narration():
    t = ("비가 왔다. 그는 우산을 폈다!\n"
         "“어디 가?” 하고 아내가 물었다.\n"
         "“나가요.” “언제 와요?”\n"
         "“일 원만 줍시요.” 이 말이 불쑥 떨어졌다. 그러나…… 그는 웃었다.")
    assert split_sentences(t) == ["비가 왔다.", "그는 우산을 폈다!", "“어디 가?” 하고 아내가 물었다.",
                                  "“나가요.”", "“언제 와요?”", "“일 원만 줍시요.” 이 말이 불쑥 떨어졌다.",
                                  "그러나…… 그는 웃었다."]


def test_split_sentences_ellipsis_dialogue_end():
    assert split_sentences("“글쎄……” “가자.”") == ["“글쎄……”", "“가자.”"]


def test_split_sentences_quote_inner_terminal_not_split_and_short_merged():
    t = "그녀는 “좋아. 가자.”라고 말하고 웃었다. 아."
    assert split_sentences(t) == ["그녀는 “좋아. 가자.”라고 말하고 웃었다. 아."]


def test_chunk_prefix_keeps_order_and_text():
    paras = [f"문단{i} " + "가나다라마바사. " * (5 + i % 7) for i in range(30)]
    long_para = "긴문장입니다. " * 120
    text = "\n".join(paras[:10] + [long_para] + paras[10:])
    from app.pipeline.judge_v2.text import CHUNK_HI, CHUNK_LO
    chunks = chunk_prefix(text)
    assert normalize("".join(chunks)) == normalize(text)
    assert max(len(c) for c in chunks) <= CHUNK_HI + CHUNK_LO // 2 + 1      # 마지막 짧은 조각은 앞에 붙는다
    assert all(len(c) >= CHUNK_LO // 2 for c in chunks)
    assert len(chunks) > 5


# ---------------------------------------------------------------- 검색


def test_retriever_bm25_names_and_ties():
    chunks = ["김첨지는 인력거를 끌었다. 비가 왔다.", "아내는 설렁탕을 먹고 싶다고 했다.", "학생이 정거장에 가자고 했다.",
              "비가 오는 날 김첨지는 술을 마셨다.", "개똥이는 울었다."]
    r = Retriever(chunks, ["김첨지", "아내"])
    hits = r.search("김첨지는 설렁탕을 사 들고 들어갔다.", k=4)
    assert len(hits) <= 4 and all(h.score > 0 for h in hits)
    assert hits[0].index in (0, 1, 3)
    assert [h.index for h in r.search("김첨지는 설렁탕을 사 들고 들어갔다.")] == [h.index for h in hits]  # 결정적
    with_name = {h.index: h for h in hits}
    assert with_name[0].names == ("김첨지",) and with_name[0].score > with_name[0].bm25
    assert Retriever([], []).search("아무것") == []


def test_guess_names_counts_and_stopwords():
    t = "김첨지가 왔다. 김첨지는 갔다. 김첨지의 아내가 아팠다. 아내는 울었다. 아내를 보았다. 그것이 문제다. 그것은 그것을."
    assert guess_names(t) == ["김첨지", "아내"]


# ---------------------------------------------------------------- 렌더러


class CharTok:
    """글자 하나 = 토큰 하나, BERT 쌍 형식([CLS] A [SEP] B [SEP])과 truncation="only_first"(A 의 끝을 자름)를 흉내 낸다."""
    cls_token_id, sep_token_id, pad_token_id = 0, 1, 2

    def _ids(self, text):  # noqa: ANN001, ANN202
        return [10 + ord(c) for c in text]

    def decode(self, ids):  # noqa: ANN001, ANN202
        return "".join(chr(i - 10) for i in ids if i >= 10)

    def __call__(self, a, b=None, truncation=None, max_length=None, add_special_tokens=True):  # noqa: ANN001, ANN204
        x = self._ids(a)
        y = self._ids(b) if b is not None else None
        if not add_special_tokens:
            return {"input_ids": x}
        if y is None:
            ids = [0] + x + [1]
        else:
            if truncation == "only_first" and max_length is not None and len(x) + len(y) + 3 > max_length:
                keep = max_length - 3 - len(y)
                assert keep >= 0
                x = x[:keep]
            ids = [0] + x + [1] + y + [1]
        return {"input_ids": ids, "attention_mask": [1] * len(ids), "token_type_ids": [0] * len(ids)}


def test_render_context_first_only_first_truncation():
    tok = CharTok()
    claim = "가나다"
    r = render_ids(tok, claim, ["A" * 300, "B" * 300], max_length=512)
    ids = r["input_ids"]
    assert len(ids) == 512 and r["truncated"]
    claim_ids = tok._ids(R.CLAIM_TAG + claim)
    assert ids[-1 - len(claim_ids):-1] == claim_ids          # 주장(둘째 칸)은 그대로 끝에
    assert ids[1:1 + len(tok._ids(R.CONTEXT_TAG))] == tok._ids(R.CONTEXT_TAG)   # 맥락이 첫째 칸
    assert ids.count(1) == 2
    short = render_ids(tok, claim, ["A" * 10], max_length=512)
    assert not short["truncated"] and tok.decode(short["input_ids"][1:-1]).startswith(R.CONTEXT_TAG)


def test_render_claim_cap_no_context_and_clean():
    tok = CharTok()
    r = render_ids(tok, "가" * 1000, ["B" * 1000], max_length=512)
    assert len(r["input_ids"]) == 512 and r["truncated"]
    assert r["input_ids"][-2 - R.CLAIM_MAX + 1:-1].count(tok._ids("가")[0]) >= R.CLAIM_MAX - len(R.CLAIM_TAG)
    r2 = render_ids(tok, "가" * 10, [], max_length=512)
    assert R.NO_CONTEXT in tok.decode(r2["input_ids"]) and not r2["truncated"]
    assert render_ids(tok, "가 나", ["다\n라  마"]) == render_ids(tok, "가\n나", ["다 라 마"])   # 공백 접기
    t = render_text("c", ["x", "y"])
    assert t.splitlines()[0].startswith("[맥락]") and t.splitlines()[1] == "[주장] c"
    assert R.CONTEXT_FIRST and R.RENDER_VERSION == "2"


def test_render_real_bert_tokenizer_only_first(tmp_path):
    tr = pytest.importorskip("transformers")
    vocab = ["[PAD]", "[UNK]", "[CLS]", "[SEP]", "[MASK]"] + sorted(set("[주장맥락] …가나다라ABX()앞선원문에서찾은구간없음"))
    (tmp_path / "vocab.txt").write_text("\n".join(vocab), encoding="utf-8")
    tok = tr.BertTokenizerFast(vocab_file=str(tmp_path / "vocab.txt"), do_lower_case=False)
    r = render_ids(tok, "가나다", ["A " * 300], max_length=128)   # 띄어 써야 토큰이 300개(붙이면 [UNK] 하나)
    assert len(r["input_ids"]) == 128 and r["truncated"]
    claim_enc = tok(R.claim_text(tok, "가나다"), add_special_tokens=False)["input_ids"]
    ids = r["input_ids"]
    assert ids[-1] == tok.sep_token_id and ids[-1 - len(claim_enc):-1] == claim_enc   # 주장은 둘째 칸에 통째로
    assert ids[0] == tok.cls_token_id and ids.count(tok.sep_token_id) == 2


# ---------------------------------------------------------------- 장면


def test_build_scene_contexts_in_position_order_and_score():
    prefix = "\n".join(["김첨지는 인력거꾼이다. 아내가 아프다.", "학생이 정거장에 가자고 했다.", "김첨지는 설렁탕을 생각했다.",
                        "비가 추적추적 내렸다.", "김첨지는 술집에 들렀다. 김첨지는 취했다."] * 3)
    items = build_scene(prefix, "김첨지는 설렁탕을 사 들고 집에 갔다. “여보, 설렁탕 사 왔소.”", names=["김첨지"])
    assert [it.claim for it in items] == ["김첨지는 설렁탕을 사 들고 집에 갔다.", "“여보, 설렁탕 사 왔소.”"]
    for it in items:
        assert it.chunk_ids == sorted(it.chunk_ids) and len(it.contexts) <= 4
        assert sorted(it.priority) == list(range(len(it.contexts)))   # 기록용 검색 순위
    assert scene_score([0.1, 0.7, 0.3]) == 0.7 and scene_score([]) == 0.0


# ---------------------------------------------------------------- 지표


def test_pick_threshold_and_temperature():
    sc = [(0.9, 1), (0.8, 1), (0.7, 0), (0.6, 1), (0.5, 0), (0.1, 0)] + [(0.05, 0)] * 7
    t = C.pick_threshold(sc, 0.10)
    assert t["fp"] == 1 and t["n_neg"] == 10 and t["tp"] == 3 and t["threshold"] == 0.6   # 1/10 = 10% 는 허용
    xs = [3.0, 2.0, -2.0, -3.0, 1.0, -1.0]
    ys = [1, 1, 0, 0, 0, 1]
    assert 0.25 <= C.fit_temperature(xs, ys) <= 100
    assert C.fit_temperature([], []) == 1.0
    assert abs(C.sigmoid(0.0) - 0.5) < 1e-12 and C.sigmoid(-800) >= 0.0
    assert C.y_of({"label": "모순"}) == 1 and C.y_of({"label": "무모순"}) == 0
    assert C.contexts_of({"context": "x"}) == ["x"] and C.contexts_of({"contexts": ["a", "b"]}) == ["a", "b"]


def test_grounded_rule():
    import score_v2 as S
    assert S.grounded("“일 원만 줍시요.” 이 말이 떨어졌다.", "“일 원만 줍시요.”")
    assert S.grounded("그는 집으로 갔다.", ["다른 문장", "그는 집으로 갔다"])
    assert not S.grounded("비가 왔다.", "그는 웃었다.")
    assert not S.grounded("비가 왔다.", None)


# ---------------------------------------------------------------- D 생성


def test_make_d_rules(tmp_path):
    import make_d as D
    src = tmp_path / "train" / "source"
    src.mkdir(parents=True)
    para = "김첨지는 인력거를 끌고 비 오는 거리를 달렸다. 아내는 집에서 앓고 있었다. 학생이 정거장까지 가자고 했다."
    for w in ("작가__가", "작가__나"):
        (src / f"{w}.txt").write_text("\n".join([para] * 120), encoding="utf-8")
    split = tmp_path / "split.json"
    split.write_text(json.dumps({"seed": "x", "works": [{"id": "w01", "author": "작가", "title": "가", "split": "train"},
                                                         {"id": "w02", "author": "작가", "title": "나", "split": "dev"}]},
                                ensure_ascii=False), encoding="utf-8")
    rows, meta = D.make(src, D.load_split(split), target=40)
    cap = math.ceil(40 * D.WORK_CAP)                          # 작품당 상한 6 → 두 작품이면 12행
    assert len(rows) == 2 * cap and meta["split"] == {"train": cap, "dev": cap}
    assert all(r["label"] == "무모순" and r["type"] == "D" and r["context"] for r in rows)
    assert max(meta["per_work"].values()) <= math.ceil(40 * D.WORK_CAP)
    assert all(r["window_start"] >= D.MIN_PREFIX for r in rows)
    assert all("\n" not in c and "  " not in c for r in rows for c in r["context"] + [r["claim"]])
    assert meta["retrieval"]["K"] == 4 and len(meta["source_sha256"]) == 2
    rows2, _ = D.make(src, D.load_split(split), target=40)
    assert rows2 == rows                                      # 결정적
    assert D.load_split(split) == {"작가__가": ("w01", "train"), "작가__나": ("w02", "dev")}
    assert {r["work"] for r in rows} == {"w01", "w02"}
    old = tmp_path / "old.json"
    old.write_text(json.dumps({"train": ["작가__가"]}, ensure_ascii=False), encoding="utf-8")
    assert D.load_split(old) == {"작가__가": ("작가__가", "train")}


# ---------------------------------------------------------------- 학습 스모크(torch 있을 때만)


def _tiny_model(tmp_path: Path) -> Path:
    torch = pytest.importorskip("torch")
    tr = pytest.importorskip("transformers")
    chars = sorted(set("[주장맥락] …가나다라마바사아자차카타파하김첨지는인력거를끌고비오거리달렸아내집에서앓있었학생정장까지자했.“”?!"))
    vocab = ["[PAD]", "[UNK]", "[CLS]", "[SEP]", "[MASK]"] + chars
    d = tmp_path / "tiny"
    d.mkdir()
    (d / "vocab.txt").write_text("\n".join(vocab), encoding="utf-8")
    tok = tr.BertTokenizerFast(vocab_file=str(d / "vocab.txt"), do_lower_case=False, tokenize_chinese_chars=True)
    cfg = tr.BertConfig(vocab_size=len(vocab), hidden_size=16, num_hidden_layers=1, num_attention_heads=2,
                        intermediate_size=32, max_position_embeddings=520, num_labels=3,
                        id2label={0: "모순", 1: "정상", 2: "무관"}, label2id={"모순": 0, "정상": 1, "무관": 2})
    torch.manual_seed(0)
    tr.BertForSequenceClassification(cfg).save_pretrained(str(d))
    tok.save_pretrained(str(d))
    return d


def test_train_pairs_then_mil_and_score_smoke(tmp_path):
    init = _tiny_model(tmp_path)
    import score_v2 as S
    import train_v2 as T
    pairs = tmp_path / "pairs.jsonl"
    rows = [{"id": f"r{i}", "type": "A", "work": "w1" if i % 4 else "w2", "split": "dev" if i % 4 == 0 else "train",
             "claim": "김첨지는 집에 있었다.", "context": ["아내는 집에서 앓고 있었다."], "label": "모순" if i % 2 else "무모순"}
            for i in range(16)]
    C.write_jsonl(pairs, rows)
    s = tmp_path / "S.jsonl"
    pre = "\n".join(["김첨지는 인력거를 끌고 비 오는 거리를 달렸다. 아내는 집에서 앓고 있었다."] * 5)
    C.write_jsonl(s, [{"id": f"s{i}", "work": "w", "split": "dev" if i < 2 else "train", "prefix": pre,
                       "target_P": "김첨지는 학생이었다. 비가 오지 않았다.", "target_N": "김첨지는 정거장까지 달렸다. 비가 왔다.",
                       "planted": "김첨지는 학생이었다.", "type": "설정"} for i in range(4)])
    o1 = tmp_path / "m1"
    T.main(["--stage", "pairs", "--init", str(init), "--train", str(pairs), "--dev-s", str(s), "--out", str(o1),
            "--device", "cpu", "--epochs", "1", "--batch-size", "4", "--grad-accum", "1", "--lr", "1e-3"])
    meta = json.loads((o1 / "v2_meta.json").read_text(encoding="utf-8"))
    assert meta["stage"] == "pairs" and meta["dev"]["n_scenes"] == 4 and meta["temperature"] > 0
    o2 = tmp_path / "m2"
    T.main(["--stage", "mil", "--init", str(o1), "--s", str(s), "--out", str(o2), "--device", "cpu", "--epochs", "1",
            "--grad-accum", "1", "--lr", "1e-3"])
    with pytest.raises(SystemExit):
        T.main(["--stage", "mil", "--init", str(o1), "--s", str(s), "--out", str(o2), "--device", "cpu"])
    out, rows_ = S.score(o2, C.read_jsonl(s), "cpu")
    assert out["n_scenes"] == 8 and out["twin_d"]["n"] == 4 and out["grounding"]["n_pos"] == 4
    assert all(0.0 <= r["max"] <= 1.0 for r in rows_)


def test_make_d_stratified_matches_quota(tmp_path):
    import make_d as D
    src = tmp_path / "train" / "source"
    src.mkdir(parents=True)
    paras = [f"김첨지는 인력거를 끌고 비 오는 거리를 {i}번 달렸다. 아내는 집에서 앓고 있었다. 학생이 정거장까지 가자고 했다." * (1 + i % 3)
             for i in range(150)]
    (src / "작가__가.txt").write_text("\n".join(paras), encoding="utf-8")
    split = tmp_path / "split.json"
    split.write_text(json.dumps({"작가__가": "train"}, ensure_ascii=False), encoding="utf-8")
    match = [{"claim": "김첨지는 정거장까지 달렸다." * k, "context": ["김첨지는 인력거를 끌었다."], "type": "A"} for k in (1, 1, 2, 3, 4)]
    edges = {"claim_len": D.quantile_edges([float(len(r["claim"])) for r in match]),
             "overlap": D.quantile_edges([C.claim_overlap(r["claim"], r["context"]) for r in match])}
    q = D.quotas(match, edges, 10)
    assert sum(q.values()) == 10 and len(edges["claim_len"]) == 4
    old = D.WORK_CAP
    D.WORK_CAP = 1.0
    try:
        rows, meta = D.make(src, D.load_split(split), target=10, match_rows=match)
    finally:
        D.WORK_CAP = old
    assert meta["sampling"].startswith("stratified") and meta["strata"]["bins"] == 5
    got = {k: v for k, v in meta["strata"]["got"].items()}
    for k, v in got.items():
        assert v <= meta["strata"]["quota"][k]
    assert len(rows) + meta["strata"]["shortfall_total"] == 10
    assert C.claim_overlap("가나다", ["가나"]) == 0.5
