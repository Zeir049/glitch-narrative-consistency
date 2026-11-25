"""PD-train 조립 v2 (설계 문서 §10 "데이터 규칙 수정", main 6dd9557) — pd_train.jsonl(A·B·C) → pd_train_v2.jsonl. 원본은 바꾸지 않는다.

단계(고정):
 (1) A− 기계 걸러내기 — 쌍째 버린다:
     (i)  바뀐 f 토막(편집 전·후 어절)과 주장의 문자 2-gram 겹침이 0
     (ii) 바꾸기 전 원래 값이 같은 맥락의 다른 곳(편집 자리 밖)에 다시 나옴
     (iii) 편집 영역이 2곳 이상(어절 단위 diff, 사이 같은 어절 MERGE_GAP 개 이하는 하나로 본다)
 (2) 판정 걸러내기(선택, --judgments): verdict == "충돌" 인 A−·B− 만 남기고 빠진 행의 짝도 뺀다.
 (3) C: C− 는 전부 버리고, C+ 는 새 규칙(g′)으로 다시 만들 때까지 보류(출력에서 뺀다).
 (4) 맥락 채우기: 쌍마다 주장 조각 앞의 원문 조각에서 같은 BM25+인명 검색(judge_v2.retrieve, D 와 같은 규칙)으로
     채워 조각 수를 K=4 로 맞춘다. 쌍의 두 행에 같은 채움·같은 순서. A− 쌍은 원래 f 값이 든 조각을 채움에서 뺀다.
     핵심 조각(원래 맥락)은 시드 고정 무작위 위치에 넣고, 채움 조각은 원문 위치 순으로 나머지 자리를 채운다.
 (5) 혼합 점검: D 와 섞은 전체에서 라벨(모순=1)에 대한 단일 특징 AUROC — 맥락 길이·조각 수·줄바꿈 유무·겹침.
     방향 무관 값 max(a, 1−a) 가 0.60 이하면 목표 달성, 0.70 을 넘으면 meta 에 학습 금지(gate fail)를 남긴다.
조각화는 PD-train 생성 코드(L-gen `scripts/eval/pd_train.py` 의 load_work·make_chunks, 브랜치 judge-l-gen)를 그대로 옮겼다.
행의 맥락·주장 조각이 이 조각화와 글자 그대로 맞는지 모두 대조한다(안 맞으면 멈춘다).

    python scripts/v2/build_v2.py --pd C:\\judge_models\\V2\\train\\pd_train.jsonl --d C:\\judge_models\\V2\\train\\gen\\D.jsonl
           --out C:\\judge_models\\V2\\train\\pd_train_v2.jsonl [--judgments filter_judgments.jsonl]
"""
from __future__ import annotations

import argparse
import difflib
import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path

import v2_common as C
from app.pipeline.judge_v2.render import clean
from app.pipeline.judge_v2.retrieve import K, Retriever, bigrams, guess_names, normalize
from app.pipeline.judge_v2.text import chunk_prefix

BUILD_VERSION = "1"  # 어절 단위 편집 영역·생성과 같은 조각화
FILL_SEED = "V2:fill:20261108"
MERGE_GAP = 1               # 어절 단위: 사이에 같은 어절이 1개 이하인 편집은 한 영역
MIN_ORIG_CHARS = 2          # (ii) 원래 값으로 볼 최소 글자 수(한글·영숫자)
GATE_TARGET, GATE_STOP = 0.60, 0.70
SOURCE = Path(r"C:\judge_models\V2\train\source")


# ---------------------------------------------------------------- 조각화(L-gen pd_train.py 그대로 — judge_v2.text.chunk_prefix)

def load_work(path: Path) -> str:
    t = path.read_text(encoding="utf-8")
    t = t.replace(chr(0x1F65D) + chr(0x1F65F), "").replace(chr(13), "")
    t = chr(10).join(ln for ln in t.split(chr(10)) if not re.match(r"^\s*==.*==\s*$", ln))
    return t.strip()


make_chunks = chunk_prefix   # judge_v2.text.chunk_prefix 가 생성 조각화와 같은 규칙(TEXT_VERSION 2)


def cidx(ref: str) -> int:
    m = re.fullmatch(r"c(\d{3})", str(ref).strip())
    if not m:
        raise ValueError(f"조각 id 형식: {ref!r}")
    return int(m.group(1))


# ---------------------------------------------------------------- (1) A− 걸러내기

def _words(t: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in re.finditer(r"\S+", t)]


def edit_regions(orig: str, new: str) -> list[tuple[int, int, int, int]]:
    """어절(공백 단위) 편집 영역(orig i1:i2 → new j1:j2, 글자 위치). 사이에 같은 어절이 MERGE_GAP 개 이하면 합친다."""
    wo, wn = _words(orig), _words(new)
    to, tn = [orig[a:b] for a, b in wo], [new[a:b] for a, b in wn]
    ops = [o for o in difflib.SequenceMatcher(None, to, tn, autojunk=False).get_opcodes() if o[0] != "equal"]
    regs: list[list[int]] = []
    for _t, i1, i2, j1, j2 in ops:
        if regs and i1 - regs[-1][1] <= MERGE_GAP and j1 - regs[-1][3] <= MERGE_GAP:
            regs[-1][1], regs[-1][3] = i2, j2
        else:
            regs.append([i1, i2, j1, j2])
    out = []
    for i1, i2, j1, j2 in regs:
        a1 = wo[i1][0] if i1 < len(wo) else len(orig)
        a2 = wo[i2 - 1][1] if i2 > i1 else a1
        b1 = wn[j1][0] if j1 < len(wn) else len(new)
        b2 = wn[j2 - 1][1] if j2 > j1 else b1
        out.append((a1, a2, b1, b2))
    return out


def a_minus_reasons(claim: str, orig_ctx: list[str], new_ctx: list[str]) -> tuple[list[str], str | None]:
    """A− 걸러내기 사유 목록과 원래 f 값(편집 전 토막). 사유가 없으면 남긴다."""
    diff = [i for i, (x, y) in enumerate(zip(orig_ctx, new_ctx)) if x != y]
    if len(orig_ctx) != len(new_ctx) or len(diff) != 1:
        return ["shape"], None
    i = diff[0]
    o, n = orig_ctx[i], new_ctx[i]
    regs = edit_regions(o, n)
    reasons = []
    if len(regs) >= 2:
        reasons.append("iii_multi_edit")
    old_seg = "".join(o[a:b] for a, b, _c, _d in regs)
    new_seg = "".join(n[c:d] for _a, _b, c, d in regs)
    if not ((set(bigrams(old_seg)) | set(bigrams(new_seg))) & set(bigrams(claim))):
        reasons.append("i_no_overlap")
    orig_val = old_seg.strip()
    if len(normalize(orig_val)) >= MIN_ORIG_CHARS:
        rest = [n[:regs[0][2]] + "\u0000" + n[regs[-1][3]:]] + [c for j, c in enumerate(new_ctx) if j != i]
        if any(normalize(orig_val) in normalize(r) for r in rest):
            reasons.append("ii_orig_elsewhere")
    return reasons, (orig_val or None)


# ---------------------------------------------------------------- (4) 맥락 채우기

def fill(pair_id: str, query: str, key_ctx: list[str], prior: list[str], *, ban_value: str | None,
         names: list[str] | None = None) -> tuple[list[str], list[int], dict]:
    """같은 쌍에 쓸 채움: (최종 맥락의 핵심 자리 목록, 채움 조각 원문 위치, 정보). 핵심 조각 글은 행마다 다르므로 자리만 돌려준다."""
    need = max(0, K - len(key_ctx))
    keys = {clean(k) for k in key_ctx}
    ban = normalize(ban_value) if ban_value and len(normalize(ban_value)) >= MIN_ORIG_CHARS else None
    banned = [j for j, c in enumerate(prior) if clean(c) not in keys and ban and ban in normalize(c)]
    cand = [j for j, c in enumerate(prior) if clean(c) not in keys and j not in banned]
    sub = [prior[j] for j in cand]
    ret = Retriever(sub, guess_names("\n".join(prior)) if names is None else names)
    hits = ret.search(query, k=len(sub))
    picked = sorted(cand[h.index] for h in hits[:need])
    total = len(key_ctx) + len(picked)
    rng = random.Random(f"{FILL_SEED}:{pair_id}")
    key_slots = sorted(rng.sample(range(total), len(key_ctx)))
    return [str(s) for s in key_slots], picked, {"need": need, "got": len(picked), "banned": len(banned)}


def assemble(key_ctx: list[str], key_slots: list[int], fill_texts: list[str]) -> list[str]:
    total = len(key_ctx) + len(fill_texts)
    out: list[str | None] = [None] * total
    for s, k in zip(key_slots, key_ctx):
        out[s] = clean(k)
    it = iter(fill_texts)
    return [x if x is not None else clean(next(it)) for x in out]


# ---------------------------------------------------------------- (5) 혼합 점검

def features(row: dict) -> dict[str, float]:
    ctx = C.contexts_of(row)
    return {"context_len": float(sum(len(c) for c in ctx)), "n_contexts": float(len(ctx)),
            "claim_len": float(len(row["claim"])),
            "has_newline": float(any("\n" in c for c in ctx)),
            "overlap": C.claim_overlap(row["claim"], ctx)}


def mix_report(rows: list[dict], d_rows: list[dict]) -> dict:
    """전체(행 + D) 와 유형별(그 유형만, 그 유형 + D) 단일 특징 AUROC. 최악 값과 그 위치를 낸다."""
    rep = {"all+D": mix_check(rows + d_rows)}
    for t in sorted({r["type"] for r in rows}):
        sub = [r for r in rows if r["type"] == t]
        rep[f"{t}"] = mix_check(sub)
        rep[f"{t}+D"] = mix_check(sub + d_rows)
    worst = max(((v["worst"], k, max(v["features"], key=lambda f: v["features"][f]["dir_free"] or 0))
                 for k, v in rep.items() if v.get("features")), default=(0, None, None))
    return {"sets": rep, "worst": {"value": worst[0], "set": worst[1], "feature": worst[2]},
            "gate": "fail" if worst[0] > GATE_STOP else "pass", "target_met": worst[0] <= GATE_TARGET}


def pair_property(rows: list[dict]) -> dict:
    """A: 두 행 주장이 바이트까지 같은가 / B: 두 행 맥락(채움 뒤)이 같은가 / 모든 쌍: 채움·자리가 같은가."""
    by = {r["id"]: r for r in rows}
    bad: Counter = Counter()
    n: Counter = Counter()
    for r in rows:
        if C.y_of(r) != 1:
            continue
        q = by.get(r.get("pair_of"))
        n[r["type"]] += 1
        if q is None:
            bad["missing_pair"] += 1
            continue
        if r["type"] == "A" and r["claim"].encode() != q["claim"].encode():
            bad["A_claim_differs"] += 1
        if r["type"] == "B" and r["context"] != q["context"]:
            bad["B_context_differs"] += 1
        if r["type"] == "C":
            g, gp = q.get("g_text") or r.get("g_text"), r.get("g_prime_text") or q.get("g_prime_text")
            if r["claim"].encode() != q["claim"].encode():
                bad["C_claim_differs"] += 1
            if not g or not gp or [c.replace(clean(gp), clean(g)) for c in r["context"]] != q["context"]:
                bad["C_context_not_only_g"] += 1
        if r.get("fill_ids") != q.get("fill_ids") or r.get("key_slots") != q.get("key_slots"):
            bad["fill_differs"] += 1
    return {"pairs_checked": dict(n), "violations": dict(bad), "n_violations": sum(bad.values())}


EVAL_AUTHORS = ("김유정", "이효석")


def leakage(rows: list[dict], split_path: Path) -> dict:
    """작가 분할 누수: 행의 작품이 split.json 학습 작가 작품인가, 평가 작가 이름이 작품 필드에 있는가."""
    works = json.loads(split_path.read_text(encoding="utf-8"))["works"]
    known = {f"{w['author']}__{w['title']}": w["split"] for w in works}
    bad = [r["id"] for r in rows if r.get("work") not in known or any(a in str(r.get("work")) for a in EVAL_AUTHORS)]
    split_mismatch = [r["id"] for r in rows if r.get("work") in known and known[r["work"]] != r.get("split")]
    return {"unknown_or_eval_author": len(bad), "split_mismatch": len(split_mismatch), "examples": (bad + split_mismatch)[:5]}


def s_check(s_rows: list[dict], source: Path) -> dict:
    """S(MIL 장면 쌍둥이) 형식·수·검색·P/N 단일 특징 AUROC(편집 거리·길이 차·겹침, 원래 구간 대비)."""
    from app.pipeline.judge_v2.scene import build_scene
    from app.pipeline.judge_v2.text import split_sentences

    fmt: Counter = Counter()
    feats: dict[str, list[tuple[float, int]]] = defaultdict(list)
    sents, no_ctx, n_claims = [], 0, 0
    chunks_of: dict[str, list[str]] = {}
    for r in s_rows:
        if not all(r.get(k) for k in ("prefix", "target_P", "target_N")):
            fmt["missing_field"] += 1
            continue
        if r["target_P"] == r["target_N"]:
            fmt["P_equals_N"] += 1
        if str(r.get("verified")) not in ("True", "true"):
            fmt["not_verified"] += 1
        w = r["work"]
        if w not in chunks_of:
            chunks_of[w] = make_chunks(load_work(source / f"{w}.txt"))
        orig = " ".join(chunks_of[w][cidx(x)] for x in re.findall(r"c\d{3}", str(r.get("region") or "")))
        for kind, y in (("P", 1), ("N", 0)):
            t = r[f"target_{kind}"]
            ss = split_sentences(t)
            sents.append(len(ss))
            items = build_scene(r["prefix"], t)
            n_claims += len(items)
            no_ctx += sum(1 for it in items if not it.contexts)
            sm = difflib.SequenceMatcher(None, orig, t, autojunk=False) if orig else None
            feats["edit_distance"].append((float(sum(max(i2 - i1, j2 - j1) for op, i1, i2, j1, j2 in sm.get_opcodes() if op != "equal")) if sm else 0.0, y))
            feats["length_diff"].append((float(len(t) - len(orig)) if orig else 0.0, y))
            feats["overlap"].append((C.claim_overlap(t, [r["prefix"]]), y))
    out = {}
    for k, v in feats.items():
        a = C.auc([x for x, y in v if y], [x for x, y in v if not y])
        out[k] = {"auroc": round(a, 4), "dir_free": round(max(a, 1 - a), 4)}
    worst = max(v["dir_free"] for v in out.values()) if out else 0.0
    sents.sort()
    return {"pairs": len(s_rows), "split": dict(Counter(r.get("split") for r in s_rows)), "format": dict(fmt),
            "sentences_per_target": {"min": sents[0], "median": sents[len(sents) // 2], "max": sents[-1]} if sents else None,
            "claims": n_claims, "claims_without_context": no_ctx, "features": out, "worst": worst,
            "gate": "fail" if worst > GATE_STOP else "pass", "target_met": worst <= GATE_TARGET}


def mix_check(rows: list[dict]) -> dict:
    ys = [C.y_of(r) for r in rows]
    if not rows or len(set(ys)) < 2:
        return {"n": len(rows), "pos": sum(ys), "features": None, "worst": 0.0, "note": "한 라벨뿐"}
    fs = [features(r) for r in rows]
    out = {}
    for k in fs[0]:
        pos = [f[k] for f, y in zip(fs, ys) if y]
        neg = [f[k] for f, y in zip(fs, ys) if not y]
        a = C.auc(pos, neg) if pos and neg else None
        out[k] = {"auroc": round(a, 4) if a is not None else None, "dir_free": round(max(a, 1 - a), 4) if a is not None else None}
    worst = max((v["dir_free"] or 0) for v in out.values())
    return {"n": len(rows), "pos": sum(ys), "features": out, "worst": worst,
            "target_met": worst <= GATE_TARGET, "gate": "fail" if worst > GATE_STOP else "pass"}


# ---------------------------------------------------------------- 조립

def build(pd_rows: list[dict], source: Path, judgments: dict[str, str] | None = None,
          c_v2: list[dict] | None = None, c_judgments: dict[str, str] | None = None,
          mech_filter: bool = True) -> tuple[list[dict], dict]:
    """pd_rows 의 C(v1)는 버린다. c_v2: C_v2 행(C+ = g 를 담은 맥락, C− = g 를 g′ 로 바꾼 맥락)을 c_judgments(충돌)로
    거른 뒤 A·B 와 같은 방식으로 채운다(C− 쌍은 원래 g 가 든 조각을 채움에서 뺀다)."""
    pd_rows = [r for r in pd_rows] + [dict(r, _cv2=True) for r in (c_v2 or [])]
    by_id = {r["id"]: r for r in pd_rows}
    pairs, seen = [], set()
    for r in pd_rows:
        if r["id"] in seen:
            continue
        q = by_id.get(r.get("pair_of"))
        if q is None:
            raise SystemExit(f"짝이 없다: {r['id']}")
        pos, neg = (r, q) if C.y_of(r) == 0 else (q, r)
        pairs.append((pos, neg))
        seen |= {r["id"], q["id"]}
    chunks_of: dict[str, list[str]] = {}
    stat: Counter = Counter()
    reasons: Counter = Counter()
    out = []
    fill_dist: Counter = Counter()
    for pos, neg in pairs:
        t = pos["type"]
        cv2 = bool(pos.get("_cv2"))
        if t == "C" and not cv2:
            stat["C_v1_in"] += 1
            stat["C_v1_dropped"] += 1
            continue
        stat[f"{t}_in"] += 1
        w = pos["work"]
        if w not in chunks_of:
            chunks_of[w] = make_chunks(load_work(source / f"{w}.txt"))
        ch = chunks_of[w]
        ci = cidx(pos["claim_chunk"])
        pos_ctx, neg_ctx = C.contexts_of(pos), C.contexts_of(neg)
        ids = [cidx(x) for x in pos.get("ctx_ids") or []]
        if not cv2 and [ch[i] for i in ids] != pos_ctx:
            raise SystemExit(f"조각화가 생성 때와 다르다: {pos['id']}")
        if cv2 and [ch[i] for i in ids] != pos_ctx:
            stat["C_ctx_not_verbatim_chunks"] += 1     # C+ 맥락이 원문 조각과 글자 그대로가 아님(g 덧붙임 등) — 세기만
        if t == "B" and (pos_ctx != neg_ctx):
            raise SystemExit(f"B 쌍 맥락이 다르다: {pos['id']}")
        jset = c_judgments if cv2 else judgments
        if jset is not None:   # (2) 판정 걸러내기 먼저(지시문 순서), 빠진 행의 짝도 함께
            v = jset.get(neg["id"])
            if v != "충돌":
                reasons[f"{t}_judgment_{v or '판정없음'}"] += 1
                stat[f"{t}_dropped_judgment"] += 1
                continue
            stat[f"{t}_after_judgment"] += 1
        ban = None
        if t == "A":
            rs, ban = a_minus_reasons(pos["claim"], pos_ctx, neg_ctx)
            if rs and not mech_filter:      # 탐색 X1(V2-X): 기계 걸러내기를 끈다 — 사유는 세기만, 채움 규칙(ban)은 같다
                for x in rs:
                    reasons[f"A_{x}_kept_unfiltered"] += 1
            elif rs:
                for x in rs:
                    reasons[f"A_{x}"] += 1
                stat["A_dropped_machine"] += 1
                continue
        if cv2:
            ban = pos.get("g_text")
            if pos.get("c_relaxed") in (True, "True", "true"):
                stat["C_c_relaxed_kept"] += 1
        prior = ch[:ci]
        key_slots, picked, info = fill(f"{pos['id']}|{neg['id']}", pos["claim"], pos_ctx, prior, ban_value=ban)
        stat["ban_excluded_chunks"] += info["banned"]
        fill_dist[len(pos_ctx) + len(picked)] += 1
        slots = [int(s) for s in key_slots]
        for r, ctx in ((pos, pos_ctx), (neg, neg_ctx)):
            row = dict(r) | {"context": assemble(ctx, slots, [ch[j] for j in picked]), "claim": clean(r["claim"]),
                             "fill_ids": [f"c{j:03d}" for j in picked], "key_slots": slots, "build_version": BUILD_VERSION}
            out.append(row)
        stat[f"{t}_kept"] += 1
    meta = {"version": BUILD_VERSION, "seed": FILL_SEED, "K": K, "merge_gap": MERGE_GAP, "mech_filter": mech_filter,
            "judgments_applied": judgments is not None, "pairs": dict(stat), "drop_reasons": dict(reasons),
            "contexts_after_fill": dict(sorted(fill_dist.items())), "rows": len(out)}
    return out, meta


def read_judgments(path: Path) -> dict[str, str]:
    """filter_judgments.jsonl → {행 id: verdict}. 행 키는 id 또는 row_id."""
    out = {}
    for r in C.read_jsonl(path):
        rid = r.get("id") or r.get("row_id")
        out[rid] = str(r.get("verdict"))
    return out


def mech_pass(a: argparse.Namespace) -> None:
    """2차 판정 대상 고르기: A− 기계 걸러내기 (i)~(iii)만(판정과 무관). 쌍의 무모순 행 맥락과 비교한다."""
    if a.out.exists():
        raise SystemExit(f"덮어쓰지 않는다: {a.out}")
    rows = C.read_jsonl(a.pd)
    by = {r["id"]: r for r in rows}
    judged = set(read_judgments(a.judgments)) if a.judgments else set()
    target = [r for r in rows if r["type"] == "A" and C.y_of(r) == 1 and r["id"] not in judged]
    passed, reasons, shape = [], Counter(), 0
    for r in target:
        q = by.get(r.get("pair_of"))
        if q is None:
            reasons["missing_pair"] += 1
            continue
        rs, _orig = a_minus_reasons(q["claim"], C.contexts_of(q), C.contexts_of(r))
        if rs:
            for x in rs:
                reasons[x] += 1
            continue
        passed.append(r["id"])
    out = {"version": BUILD_VERSION, "rule": "A− 기계 걸러내기 (i)~(iii) — b346133 과 같은 a_minus_reasons(어절 diff, MERGE_GAP 1)",
           "input": {"pd": str(a.pd), "pd_sha256": C.sha256_file(a.pd),
                     "judgments": str(a.judgments) if a.judgments else None,
                     "judgments_sha256": C.sha256_file(a.judgments) if a.judgments else None},
           "targets": len(target), "passed": len(passed), "dropped": len(target) - len(passed),
           "drop_reasons_overlapping": dict(reasons), "passed_ids": passed}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "passed_ids"}, ensure_ascii=False))


def recheck(a: argparse.Namespace) -> None:
    meta_p = a.out.with_suffix(".meta.json")
    meta = json.loads(meta_p.read_text(encoding="utf-8"))
    rows = C.read_jsonl(a.out)
    d_rows = [r for r in C.read_jsonl(a.d) if r.get("split") != "dev"]
    hist = meta.setdefault("mix_check_history", [])
    hist.append({"d": meta["inputs"].get("d"), "d_sha256": meta["inputs"].get("d_sha256"), "mix_check": meta.get("mix_check")})
    d_meta_p = a.d.with_suffix(".meta.json")
    d_meta = json.loads(d_meta_p.read_text(encoding="utf-8")) if d_meta_p.exists() else {}
    meta["inputs"] |= {"d": str(a.d), "d_sha256": C.sha256_file(a.d), "d_version": d_meta.get("version"),
                       "d_sampling": d_meta.get("sampling"), "d_note": a.note}
    meta["mix_check"] = mix_report(rows, d_rows)
    meta["pair_property"] = pair_property(rows)
    meta["d_train_rows"] = len(d_rows)
    meta_p.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"worst": meta["mix_check"]["worst"], "gate": meta["mix_check"]["gate"],
                      "target_met": meta["mix_check"]["target_met"], "d_train_rows": len(d_rows),
                      "pair_property": meta["pair_property"]["n_violations"]}, ensure_ascii=False))
    if meta["mix_check"]["gate"] == "fail":
        raise SystemExit(f"혼합 점검 실패(> {GATE_STOP}): {meta['mix_check']['worst']} — 학습하지 않는다")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--pd", type=Path, required=True)
    ap.add_argument("--d", type=Path, required=True)
    ap.add_argument("--source", type=Path, default=SOURCE)
    ap.add_argument("--judgments", type=Path, nargs="*", default=None, help="A·B 판정 파일들(1·2차 합침)")
    ap.add_argument("--c-judgments", type=Path, help="C_v2 C− 판정 파일")
    ap.add_argument("--s", type=Path, help="S(MIL) 파일 — 형식·수·특징 점검만, 그대로 둔다")
    ap.add_argument("--split", type=Path, default=Path(r"C:\judge_models\V2\train\split.json"))
    ap.add_argument("--expect-kept", default=None, help="최종 남는 쌍 수, 예: A=156,B=222 (다르면 멈춘다)")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--expect", default=None, help="판정 걸러내기 뒤 기대 쌍 수, 예: A=251,B=181 (다르면 멈춘다)")
    ap.add_argument("--c-v2", type=Path, help="C_v2 행(gen/C_v2.jsonl) — --c-judgments 와 함께")
    ap.add_argument("--recheck", action="store_true",
                    help="이미 만든 --out 행은 그대로 두고 D 만 바꿔 혼합·쌍 성질 점검을 다시 재 meta 를 갱신(이전 값은 history)")
    ap.add_argument("--note", default=None, help="(--recheck) D 를 바꾼 이유 — meta 에 남긴다")
    ap.add_argument("--mech-pass", action="store_true",
                    help="판정이 없는(--judgments 에 id 가 없는) A 모순 행에 A− 기계 걸러내기만 적용해 통과 id 목록을 --out(json)에 쓴다")
    a = ap.parse_args(argv)
    if a.mech_pass:
        mech_pass(a)
        return
    if a.recheck:
        recheck(a)
        return
    if a.out.exists() or a.out.resolve() == a.pd.resolve():
        raise SystemExit(f"덮어쓰지 않는다: {a.out}")
    pd_rows = C.read_jsonl(a.pd)
    judg = None
    if a.judgments:
        judg = {}
        for f in a.judgments:
            for k, v in read_judgments(f).items():
                if k in judg and judg[k] != v:
                    raise SystemExit(f"판정 파일끼리 같은 행 판정이 다르다: {k}")
                judg[k] = v
    if a.c_v2 and not a.c_judgments:
        raise SystemExit("--c-v2 는 --c-judgments 와 함께")
    rows, meta = build(pd_rows, a.source, judg, C.read_jsonl(a.c_v2) if a.c_v2 else None,
                       read_judgments(a.c_judgments) if a.c_judgments else None)
    if a.expect_kept:
        want = {k: int(v) for k, v in (x.split("=") for x in a.expect_kept.split(","))}
        got = {t: meta["pairs"].get(f"{t}_kept", 0) for t in want}
        if got != want:
            raise SystemExit(f"남은 쌍 수가 기대와 다르다: {got} ≠ {want}")
    if a.expect:
        want = {k: int(v) for k, v in (x.split("=") for x in a.expect.split(","))}
        got = {t: meta["pairs"].get(f"{t}_after_judgment", 0) for t in want}
        if got != want:
            raise SystemExit(f"판정 걸러내기 뒤 쌍 수가 기대와 다르다: {got} ≠ {want}")
    d_rows = [r for r in C.read_jsonl(a.d) if r.get("split") != "dev"]      # 혼합 점검은 D train 만
    meta["mix_check"] = mix_report(rows, d_rows)
    meta["pair_property"] = pair_property(rows)
    meta["inputs"] = {"pd": str(a.pd), "pd_sha256": C.sha256_file(a.pd), "d": str(a.d), "d_sha256": C.sha256_file(a.d),
                      "judgments": {str(f): C.sha256_file(f) for f in (a.judgments or [])},
                      "c_v2": {str(a.c_v2): C.sha256_file(a.c_v2)} if a.c_v2 else None,
                      "c_judgments": {str(a.c_judgments): C.sha256_file(a.c_judgments)} if a.c_judgments else None}
    meta["leakage"] = leakage(rows, a.split)
    if a.s:
        meta["S"] = {"path": str(a.s), "sha256": C.sha256_file(a.s)} | s_check(C.read_jsonl(a.s), a.source)
    meta["labels"] = dict(Counter(f"{r['type']}:{r['label']}" for r in rows))
    meta["pairs_by"] = {"type_split": dict(Counter(f"{r['type']}:{r['split']}" for r in rows if C.y_of(r) == 1)),
                        "author": dict(Counter(f"{r['type']}:{r['work'].split('__')[0]}" for r in rows if C.y_of(r) == 1))}
    C.write_jsonl(a.out, rows)
    a.out.with_suffix(".meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: meta[k] for k in ("pairs", "drop_reasons", "contexts_after_fill", "rows")}, ensure_ascii=False))
    print(json.dumps({"worst": meta["mix_check"]["worst"], "gate": meta["mix_check"]["gate"],
                      "pair_property": meta["pair_property"], "pairs_by": meta["pairs_by"], "leakage": meta["leakage"],
                      "S": {k: v for k, v in (meta.get("S") or {}).items() if k not in ("path", "sha256")}},
                     ensure_ascii=False))
    if meta["mix_check"]["gate"] == "fail":
        raise SystemExit(f"혼합 점검 실패(> {GATE_STOP}): {meta['mix_check']['worst']} — 학습하지 않는다")


if __name__ == "__main__":
    main()
