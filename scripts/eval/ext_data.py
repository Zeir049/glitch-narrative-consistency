"""외부 데이터 → 학습 judge 쌍 JSONL (실험 E: 외부 데이터 중간 단계, 지시문 K-E).

받은 파일은 저장소 밖(`C:/judge_models/ext/`)에 두고 데이터만 읽는다(코드 실행 없음 — `python -I` 로 돌린다).
행 모양은 export_pairs 와 같은 키(premise·hypothesis·label·work·group_id·source·split)를 쓴다. 외부 쌍은 문장 대 문장이라
본 학습에 섞지 않고 중간 단계로만 쓴다(train_judge `--train ext.jsonl --val-frac 0.1`, 묶음 단위 10% 로 조기 종료).

  kornli     KorNLI multi_nli 학습셋 + 원본 MNLI 학습셋(genre 열). 두 파일은 행 순서가 1:1 이다 — **라벨 열 전체가 같은지 먼저
             검사하고 하나라도 다르면 멈춘다.** genre == fiction 행만. entailment→정상, contradiction→모순, neutral→무관.
             work "ext_kornli", group_id = promptID(같은 전제의 가설 3개는 한 묶음).
  ffcd-en    FlawedFictions(단편·장편 양성) + ContraDoc(story 양성 중 ref 있는 것) → 영어 쌍.
             모순: (충돌 문장 = 전제, 오류 문장 = 가설). FF 는 `<li>` 목록의 모든 (오류, 충돌) 조합, ContraDoc 은 ref 를 문장으로
             나눈 (ref 문장, evidence). 무관: 모순 1개당 2개 — (같은 이야기의 다른 문장, 오류 문장)·(충돌 문장, 다른 문장).
             한쪽은 모순 쌍의 문장을 그대로 둬서 가설만 보고 맞히는 지름길을 막는다. 정상 쌍은 만들지 않는다(음성에 문장 주석 없음).
  translate  ffcd-en 의 고유 문장을 로컬 llama-server(127.0.0.1, OpenAI 호환)로 문장 단위 번역. 결과는 캐시 JSON 에 이어 쓴다.
  ffcd-build 영어 쌍 + 번역 캐시 → 한국어 쌍(work "ext_ffcd"). 번역이 빈 문장이 든 쌍은 뺀다.
  leak       외부 JSONL 에 Weave 4작품 원문 문장이 들어 있는지(정규화 20자 조각 대조) 점검.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
import time
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

MNLI_LABELS = {0: "정상", 1: "무관", 2: "모순"}   # HF multi_nli: 0 entailment, 1 neutral, 2 contradiction (KorNLI 같음)
MIN_CHARS, MAX_CHARS = 20, 500


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


def ext_row(premise: str, hypothesis: str, label: str, *, work: str, group: str, **meta) -> dict:
    return {"premise": premise, "hypothesis": hypothesis, "label": label, "work": work,
            "group_id": f"{work}:{group}", "scene_id": f"{work}:{group}", "case_id": f"{work}:{group}",
            "source": "ext", "split": "ext", "premise_kind": "ext", "keyed": True, **meta}


# ---------------------------------------------------------------- KorNLI fiction

def kornli_rows(kor: list[dict], mnli: list[dict], *, genre: str = "fiction", max_rows: int = 0,
                seed: int = 7) -> tuple[list[dict], dict]:
    """kor·mnli: 같은 순서의 행 목록(kor: premise·hypothesis·label, mnli: label·genre·promptID). 라벨 열 불일치면 SystemExit."""
    if len(kor) != len(mnli):
        raise SystemExit(f"행 수가 다르다: KorNLI {len(kor)} / MNLI {len(mnli)}")
    bad = [i for i, (k, m) in enumerate(zip(kor, mnli)) if k["label"] != m["label"]]
    if bad:
        raise SystemExit(f"라벨 열 불일치 {len(bad)}행(처음 {bad[:5]}) — 정렬을 믿을 수 없어 멈춘다")
    out = [ext_row(k["premise"], k["hypothesis"], MNLI_LABELS[k["label"]], work="ext_kornli",
                   group=str(m["promptID"]), ext_index=i)
           for i, (k, m) in enumerate(zip(kor, mnli)) if m["genre"] == genre and k["label"] in MNLI_LABELS]
    n_genre = len(out)
    if max_rows and len(out) > max_rows:
        groups = sorted({r["group_id"] for r in out})
        random.Random(f"kornli:{seed}").shuffle(groups)
        keep, n = set(), 0
        cnt = Counter(r["group_id"] for r in out)
        for g in groups:
            if n >= max_rows:
                break
            keep.add(g)
            n += cnt[g]
        out = [r for r in out if r["group_id"] in keep]
    report = {"rows_total": len(kor), "label_mismatch": 0, "genre": genre, "rows_genre": n_genre,
              "rows_out": len(out), "max_rows": max_rows, "labels": dict(Counter(r["label"] for r in out)),
              "groups": len({r["group_id"] for r in out})}
    return out, report


# ---------------------------------------------------------------- FlawedFictions + ContraDoc (영어 쌍)

_LI = re.compile(r"<li>(.*?)</li>", re.S)
_SENT = re.compile(r"(?<=[.!?])[\"”’']?\s+(?=[A-Z\"“‘'(])")


_TAG = re.compile(r"</?(?:p|br|i|b|em|strong|span)[^>]*>", re.I)


def strip_html(s: str | None) -> str:
    """이야기 본문의 단락 태그 → 줄바꿈, 나머지 서식 태그 제거."""
    s = re.sub(r"</p>\s*<p>|<br\s*/?>", "\n", s or "", flags=re.I)
    return _TAG.sub("", s)


def li_items(s: str | None) -> list[str]:
    items = [strip_html(x).replace("\n", " ").strip().strip('"“”').strip() for x in _LI.findall(s or "")]
    return [x for x in items if x]


def split_sentences(text: str) -> list[str]:
    parts = []
    for para in re.split(r"\n+", strip_html(text)):
        parts += [p.strip() for p in _SENT.split(para) if p.strip()]
    return parts


def _norm(s: str) -> str:
    return re.sub(r"\W+", "", (s or "").lower())


def _overlaps(s: str, keys: list[str]) -> bool:
    ns = _norm(s)
    return any(ns and k and (ns in k or k in ns) for k in keys)


def _cut(s: str) -> str:
    return s if len(s) <= MAX_CHARS else s[:MAX_CHARS].rsplit(" ", 1)[0] + "…"


def story_pairs(story_id: str, text: str, contra: list[tuple[str, str]], *, src: str, seed: int) -> list[dict]:
    """한 이야기의 (전제, 가설) 모순 쌍들 → 모순 + 모순 1개당 무관 2개(영어)."""
    keys = [_norm(x) for pair in contra for x in pair]
    pool = [s for s in split_sentences(text) if MIN_CHARS <= len(s) <= MAX_CHARS and not _overlaps(s, keys)]
    rows = []
    for j, (prem, hyp) in enumerate(contra):
        if _norm(prem) == _norm(hyp):   # ContraDoc ref 에 evidence 문장 자체가 든 경우 — 모순 쌍이 아니다
            continue
        prem, hyp = _cut(prem), _cut(hyp)
        rows.append({"premise_en": prem, "hypothesis_en": hyp, "label": "모순", "story": story_id, "src": src, "pair": j})
        if len(pool) < 2:
            continue
        rng = random.Random(f"ffcd:{seed}:{story_id}:{j}")
        a, b = rng.sample(pool, 2)
        rows.append({"premise_en": a, "hypothesis_en": hyp, "label": "무관", "story": story_id, "src": src, "pair": j,
                     "neg": "other_premise"})
        rows.append({"premise_en": prem, "hypothesis_en": b, "label": "무관", "story": story_id, "src": src, "pair": j,
                     "neg": "other_hypothesis"})
    return rows


def ff_contra(row: dict) -> list[tuple[str, str]]:
    errs, cons = li_items(row.get("cont_error_lines")), li_items(row.get("contradicted_lines"))
    return [(c, e) for e in errs for c in cons]


def ffcd_en(ff_tables: list[list[dict]], contradoc: dict, *, seed: int = 7) -> tuple[list[dict], dict]:
    rows, rep = [], Counter()
    for tab in ff_tables:
        for r in tab:
            if not r.get("cont_error"):
                continue
            pairs = ff_contra(r)
            rep["ff_stories"] += 1
            rep["ff_contra"] += len(pairs)
            rows += story_pairs(str(r["example_id"]), r["story"], pairs, src="flawed_fictions", seed=seed)
    for uid, v in sorted((contradoc.get("pos") or {}).items()):
        if v.get("doc_type") != "story" or not v.get("ref sentences"):
            continue
        raw = v["ref sentences"] if isinstance(v["ref sentences"], list) else [v["ref sentences"]]
        refs = [s for x in raw for s in (split_sentences(str(x)) or [str(x)]) if len(s) >= MIN_CHARS]
        pairs = [(ref, v["evidence"]) for ref in refs]
        rep["cd_stories"] += 1
        rep["cd_contra"] += len(pairs)
        rows += story_pairs(f"contradoc_{uid}", v["text"], pairs, src="contradoc", seed=seed)
    rep["rows"] = len(rows)
    rep["labels"] = dict(Counter(r["label"] for r in rows))
    return rows, dict(rep)


def unique_sentences(rows: list[dict]) -> list[str]:
    seen: dict[str, None] = {}
    for r in rows:
        seen.setdefault(r["premise_en"])
        seen.setdefault(r["hypothesis_en"])
    return list(seen)


# ---------------------------------------------------------------- 번역 (로컬 llama-server)

SYSTEM = ("너는 영한 번역가다. 사용자가 준 영어 소설 문장 하나를 자연스러운 한국어 문장으로 번역한다. "
          "고유명사는 소리 나는 대로 옮기고, 수·시간·부정·인물 관계를 빠뜨리지 않는다. 번역문만 출력한다(설명·따옴표 없이).")


def translate_one(text: str, *, url: str, model: str, timeout: float = 120.0) -> str:
    body = {"model": model, "temperature": 0, "max_tokens": 512,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": text}]}
    req = urllib.request.Request(url.rstrip("/") + "/v1/chat/completions", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        out = json.loads(resp.read().decode("utf-8"))
    msg = out["choices"][0]["message"]
    return clean_translation(msg.get("content") or "")


def clean_translation(s: str) -> str:
    s = re.sub(r"<think>.*?</think>", "", s, flags=re.S).strip()
    return s.strip().strip('"“”').strip()


def translate_all(sentences: list[str], cache: dict[str, str], *, url: str, model: str, workers: int,
                  log_every: int = 200) -> dict[str, str]:
    todo = [s for s in sentences if not cache.get(s)]
    t0 = time.time()

    def job(s: str) -> tuple[str, str]:
        try:
            return s, translate_one(s, url=url, model=model)
        except Exception as e:  # noqa: BLE001 - 한 문장 실패는 빈 번역으로 두고 계속
            print(f"실패: {e!r} :: {s[:60]}", file=sys.stderr)
            return s, ""
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, (s, ko) in enumerate(ex.map(job, todo), 1):
            cache[s] = ko
            if i % log_every == 0:
                print(f"{i}/{len(todo)} {time.time() - t0:.0f}s", flush=True)
    return cache


def ffcd_build(rows_en: list[dict], trans: dict[str, str]) -> tuple[list[dict], dict]:
    out, dropped = [], 0
    for r in rows_en:
        p, h = trans.get(r["premise_en"], ""), trans.get(r["hypothesis_en"], "")
        if not p or not h:
            dropped += 1
            continue
        out.append(ext_row(p, h, r["label"], work="ext_ffcd", group=r["story"], ext_src=r["src"],
                           premise_en=r["premise_en"], hypothesis_en=r["hypothesis_en"], neg=r.get("neg")))
    return out, {"rows": len(out), "dropped_untranslated": dropped, "labels": dict(Counter(r["label"] for r in out)),
                 "groups": len({r["group_id"] for r in out})}


# ---------------------------------------------------------------- 누수 점검

def leak_check(ext_rows: list[dict], weave_texts: list[str], k: int = 20) -> dict:
    """Weave 원문에서 정규화 k자 조각(겹치지 않게)을 뽑아 외부 문장에 들어 있는지 센다."""
    grams = set()
    for t in weave_texts:
        n = re.sub(r"\W+", "", t or "")
        grams.update(n[i:i + k] for i in range(0, max(0, len(n) - k + 1), k))
    hits = 0
    for r in ext_rows:
        for s in (r["premise"], r["hypothesis"]):
            n = re.sub(r"\W+", "", s)
            if any(n[i:i + k] in grams for i in range(0, max(0, len(n) - k + 1))):
                hits += 1
                break
    return {"ext_rows": len(ext_rows), "weave_grams": len(grams), "k": k, "rows_with_hit": hits}


# ---------------------------------------------------------------- CLI

def _read_parquet(path: Path, columns: list[str] | None = None) -> list[dict]:
    import pyarrow.parquet as pq
    return pq.read_table(path, columns=columns).to_pylist()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("kornli")
    k.add_argument("--kornli", type=Path, required=True)
    k.add_argument("--mnli", type=Path, required=True)
    k.add_argument("--out", type=Path, required=True)
    k.add_argument("--max-rows", type=int, default=0)
    k.add_argument("--seed", type=int, default=7)
    f = sub.add_parser("ffcd-en")
    f.add_argument("--ff", type=Path, nargs="+", required=True)
    f.add_argument("--contradoc", type=Path, required=True)
    f.add_argument("--out", type=Path, required=True)
    f.add_argument("--seed", type=int, default=7)
    t = sub.add_parser("translate")
    t.add_argument("--pairs", type=Path, required=True)
    t.add_argument("--cache", type=Path, required=True)
    t.add_argument("--url", default="http://127.0.0.1:8080")
    t.add_argument("--model", required=True)
    t.add_argument("--workers", type=int, default=3)
    t.add_argument("--limit", type=int, default=0, help="처음 N 문장만(품질 시험)")
    t.add_argument("--sample-seed", type=int, help="--limit 표본을 앞에서가 아니라 이 시드로 무작위로")
    b = sub.add_parser("ffcd-build")
    b.add_argument("--pairs", type=Path, required=True)
    b.add_argument("--cache", type=Path, required=True)
    b.add_argument("--out", type=Path, required=True)
    lk = sub.add_parser("leak")
    lk.add_argument("--ext", type=Path, nargs="+", required=True)
    a = ap.parse_args(argv)

    if a.cmd == "kornli":
        kor = _read_parquet(a.kornli, ["premise", "hypothesis", "label"])
        mnli = _read_parquet(a.mnli, ["promptID", "genre", "label"])
        rows, rep = kornli_rows(kor, mnli, max_rows=a.max_rows, seed=a.seed)
        write_jsonl(a.out, rows)
        rep |= {"sha256": hashlib.sha256(a.out.read_bytes()).hexdigest(),
                "sources": {str(a.kornli): hashlib.sha256(a.kornli.read_bytes()).hexdigest(),
                            str(a.mnli): hashlib.sha256(a.mnli.read_bytes()).hexdigest()}}
    elif a.cmd == "ffcd-en":
        tabs = [_read_parquet(p) for p in a.ff]
        cd = json.loads(a.contradoc.read_text(encoding="utf-8"))
        rows, rep = ffcd_en(tabs, cd, seed=a.seed)
        rep["unique_sentences"] = len(unique_sentences(rows))
        write_jsonl(a.out, rows)
    elif a.cmd == "translate":
        rows = read_jsonl(a.pairs)
        sents = unique_sentences(rows)
        if a.limit:
            if a.sample_seed is not None:
                sents = random.Random(a.sample_seed).sample(sents, min(a.limit, len(sents)))
            else:
                sents = sents[:a.limit]
        cache = json.loads(a.cache.read_text(encoding="utf-8")) if a.cache.exists() else {}
        cache = translate_all(sents, cache, url=a.url, model=a.model, workers=a.workers)
        a.cache.parent.mkdir(parents=True, exist_ok=True)
        a.cache.write_text(json.dumps(cache, ensure_ascii=False, indent=0), encoding="utf-8")
        done = [s for s in sents if cache.get(s)]
        rep = {"sentences": len(sents), "translated": len(done), "empty": len(sents) - len(done), "model": a.model}
        a.out = a.cache
    elif a.cmd == "ffcd-build":
        rows_en = read_jsonl(a.pairs)
        trans = json.loads(a.cache.read_text(encoding="utf-8"))
        rows, rep = ffcd_build(rows_en, trans)
        write_jsonl(a.out, rows)
        rep["sha256"] = hashlib.sha256(a.out.read_bytes()).hexdigest()
    else:  # leak
        root = Path(__file__).resolve().parents[2] / "tests" / "eval"
        texts = []
        for p in list(root.glob("weave_checker_corpus/**/*.json")) + list(root.glob("weave_judge_corpus/**/*.json")):
            try:
                texts.append(p.read_text(encoding="utf-8"))
            except UnicodeDecodeError:
                continue
        rows = [r for p in a.ext for r in read_jsonl(p)]
        rep = leak_check(rows, texts)
        a.out = None
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    if a.out is not None and a.cmd != "translate":
        a.out.with_suffix(".meta.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
