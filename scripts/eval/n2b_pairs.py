# -*- coding: utf-8 -*-
"""실험 N-2b — 겹침과 라벨의 상관 끊기 (L1 쌍 다시 쓰기).

배경(K N-0): L1 일관판의 가설값↔전제값 겹침 중앙값이 모순판보다 높다(0.160 vs 0.093) — 실제 같은 슬롯 정상 쌍은 0.000.
그래서 L1 쌍마다 값 서술만 다시 쓴다:
  새 일관판  전제와 맞는 뜻, 전제 문구와 겹치지 않게(≤ LOW). 약 30% 는 같은 뜻의 부정 표현.
  새 모순판  전제와 충돌, 전제 문구를 많이 그대로 쓰고 핵심 값 하나만 바꾼다(≥ HIGH).
생성은 서브에이전트(유료 API 없음). 이 파일은 작업 파일 만들기 · 겹침 재기 · 행 만들기(claude_pairs 와 같은 렌더링).

겹침(overlap) 정의 — N-0 스크립트가 저장소에 없어 같은 취지로 새로 정의한다(절대값은 K 의 0.160/0.093 과 다를 수 있다):
  가설값  hv = hyp_spec.assumes (field 가 있을 때) 아니면 hyp_spec.summary
  전제값  ledger → ' = ' 뒤 값 / knowledge → '앎: ' 뒤 / rule → 'RULE_xxx: ' 뒤 / past → '[N화] 제목: ' 뒤 요약
  overlap = 공백 제거 문자 2-gram Jaccard(hv, 전제값 전체 또는 그 절(clause) 중 최대)   ← 긴 past 전제도 절 하나만 베끼면 닿도록 절 단위를 허용
  overlap_whole = 전제값 전체와만 잰 값(참고용)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
if str(EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(EVAL_DIR))
import claude_pairs as CP  # noqa: E402

N_DIR = Path("C:/judge_models/N")
L1_PATH = Path("C:/judge_models/L/l1_wm.jsonl")
LOW, HIGH = 0.05, 0.30
NEG_SHARE = 0.30
SOURCE = "n2b_wm"
PROMPT_VERSION = "N2b-v1"
_NEG = CP._NEG


# ---------------------------------------------------------------- 겹침

def premise_value(text_a: str, kind: str) -> str:
    if kind == "ledger":
        m = re.search(r" = (.*?)(?: \(since|$)", text_a.split(" | ")[0])
        return m.group(1).strip() if m else text_a.split(" | ")[0]
    head = text_a.split(" | ")[0]
    if kind == "knowledge":
        m = re.search(r"앎: (.*?)(?: \(via|$)", head)
        return m.group(1).strip() if m else head
    if kind == "rule":
        m = re.search(r"RULE_\w+: (.*)$", head)
        return m.group(1).strip() if m else head
    head = re.sub(r"^\[\d+화\]\s*", "", head)
    return head.split(": ", 1)[1].strip() if ": " in head else head


def hyp_value(spec: dict) -> str:
    return (spec.get("assumes") if spec.get("field") else None) or spec.get("summary") or ""


def _bg(s: str) -> set[str]:
    s = re.sub(r"\s+", "", s)
    return {s[i:i + 2] for i in range(len(s) - 1)}


def jaccard(a: str, b: str) -> float:
    A, B = _bg(a), _bg(b)
    return len(A & B) / len(A | B) if A | B else 0.0


def clauses(v: str) -> list[str]:
    return [c for c in re.split(r"[.;,:·/]\s*|\s[-—]\s|\s+\(|\)", v) if len(re.sub(r"\s", "", c)) >= 4]


def overlap(hv: str, pv: str) -> float:
    return max([jaccard(hv, pv)] + [jaccard(hv, c) for c in clauses(pv)])


def row_overlap(r: dict) -> float:
    return overlap(hyp_value(r["hyp_spec"]), premise_value(r["text_a"], r["premise_kind"]))


def has_neg(t: str) -> bool:
    return any(n in t for n in _NEG)


def wants_neg(stem: str) -> bool:
    return int(hashlib.sha1(stem.encode()).hexdigest(), 16) % 100 < int(NEG_SHARE * 100)


# ---------------------------------------------------------------- 문체 예

def style_examples(n: int = 20, seed: int = 20261108) -> list[dict]:
    import datagen_common as C
    eps, reg, _ = C.load_episodes("wm", None, None)
    names = {e["id"]: e["name"] for c in ("characters", "locations", "objects", "times")
             for e in reg.get(c) or [] if e.get("id") and e.get("name")}
    st = [{"entity": names.get(a["entity"], a["entity"]), "field": a["field"], "assumes": a["assumes"],
           "quote": a.get("source_text")} for e in eps for ev in e.get("events", []) for a in ev.get("assumed_states") or []]
    random.Random(seed).shuffle(st)
    return st[:n]


# ---------------------------------------------------------------- 작업 파일

RULES = """# 작업: L1 쌍의 '값 서술' 다시 쓰기 (N2b-v1)
너는 학습 데이터를 만드는 작성자다. 아래 항목마다 **같은 전제**를 두고 가설의 값 서술을 새로 쓴다. 파일은 읽기만 하고, 결과는 out_path 에 JSON 한 파일로 쓴다.
다른 파일(셜록·드라큘라·Q1·Q3 관련 자료)은 열지 않는다. 이 작업 파일 안의 내용만 쓴다.

## 왜
지금 데이터는 '맞는(일관) 가설은 전제 문구를 따라 쓰고, 모순 가설은 전혀 다른 단어를 쓴다'는 겹침 지름길이 있다. 실제는 반대다 —
맞는 가설도 전제와 다른 말로 쓰이고, 모순 가설도 전제 문구를 거의 그대로 쓰며 핵심 값 하나만 다르다. 이 지름길을 끊는 쌍을 만든다.

## 항목마다 쓸 것
- `ok`  = 전제와 **맞는(모순 아님)** 가설. 같은 뜻이되 전제값의 단어·어구를 **쓰지 않는다** — 동의어·풀어쓰기·다른 어순·상위/하위 표현.
  · 전제값에 나온 명사·동사·수치 표기를 그대로 다시 쓰지 말 것(같은 글자 2개 연속이 겹치지 않게 의식해서 고른다). 고유명사(이름)는 가설 쪽에 쓰지 않아도 되도록 대명사·역할어로 바꿔도 된다.
  · 입력에 `부정표현: 예` 로 표시된 항목은 **같은 뜻을 부정 표현으로** 쓴다(예: 전제 "가지고 있다" ↔ 가설 "빈손이 아니다"가 아니라 뜻이 같은 부정 표현을 쓰되, 전제와 단어는 겹치지 않게. 예: 전제 "휴대폰을 갖고 있지 않다" ↔ 가설 "연락 수단이 없다"). 그렇게 쓸 수 없으면 `negated: false` 로 두고 그냥 쓴다.
- `bad` = 전제와 **충돌하는(모순)** 가설. 전제값의 문구를 **최대한 그대로** 쓰고, **핵심 값 하나만**(수·방향·상태·대상·유무 등) 충돌하게 바꾼다. 부정어만 붙이는 변형("~지 않았다")은 쓰지 않는다 — 값을 바꾼다.
- 두 가설 모두: 입력의 `field` 가 null 이 아니면 `assumes`(상태 값 서술, 짧은 명사구·구)와 `summary`(사건 한 줄 요약, 220자 이하) 둘 다 쓴다. field 가 null 이면 `assumes` 는 null, `summary` 만 쓴다(겹침은 summary 로 잰다).
- 근거 인용(`sentence`)은 원래 문장을 쓴다(입력에 있음) — **너는 안 써도 된다**. 다만 원래 문장이 새 값과 어긋나면(일관판 문장이 새 서술과 뜻이 달라지는 경우 등) 최소한만 고친 문장을 `sentence` 로 낸다(160자 이하). 고쳤다면 `quote_fixed: true`.
- 쓰는 말투는 아래 '실제 추출 문체 예'를 따른다: 짧고 서술적인 값, 군더더기 없음, 따옴표·설명 괄호 남발 금지.
- 전제를 바꾸지 않는다. 모순은 반드시 그 전제와 충돌해야 하고, 일관은 반드시 그 전제와 모순되지 않아야 한다. 판단이 서지 않는 항목은 `skip: true` 로 낸다.

## 출력(JSON 한 파일, 입력 항목 전부에 한 줄씩)
```json
{"items": [
 {"stem": "<입력의 stem>",
  "ok":  {"assumes": "...", "summary": "...", "negated": false},
  "bad": {"assumes": "...", "summary": "..."}},
 {"stem": "<id>", "skip": true, "why": "..."}
]}
```
(`sentence`·`quote_fixed` 는 필요할 때만 ok/bad 안에 넣는다.) 마치면 쓴 항목 수와 skip 수만 한 줄로 답한다.
"""


def item_view(stem: str, ok: dict, bad: dict) -> dict:
    return {"stem": stem, "field": ok["hyp_spec"]["field"], "entity": ok["hyp_spec"].get("entity_name"),
            "type": ok["type"], "premise_kind": ok["premise_kind"], "premise": ok["text_a"].split(" | ")[0],
            "premise_value": premise_value(ok["text_a"], ok["premise_kind"]),
            "orig_ok": {k: ok["hyp_spec"].get(k) for k in ("assumes", "summary", "quote")},
            "orig_bad": {k: bad["hyp_spec"].get(k) for k in ("assumes", "summary", "quote")},
            "negated_expression": wants_neg(stem)}


def item_md(v: dict, feedback: str | None = None) -> str:
    lines = [f"### {v['stem']}   (type {v['type']} · 전제 종류 {v['premise_kind']} · field {v['field']} · 대상 {v['entity']})",
             f"- 전제 전문: {v['premise']}", f"- **전제값**: {v['premise_value']}",
             f"- 원래 일관 가설: assumes={v['orig_ok']['assumes']!r} / summary={v['orig_ok']['summary']!r} / 인용={v['orig_ok']['quote']!r}",
             f"- 원래 모순 가설: assumes={v['orig_bad']['assumes']!r} / summary={v['orig_bad']['summary']!r} / 인용={v['orig_bad']['quote']!r}",
             f"- 부정표현: {'예' if v['negated_expression'] else '아니오'}"]
    if feedback:
        lines.append(f"- **지난번 결과가 목표 미달**: {feedback} — 이번에는 더 확실히 맞춰 다시 쓴다.")
    return "\n".join(lines)


def task_md(scene: str, views: list[dict], examples: list[dict], out_path: Path, feedback: dict[str, str] | None = None) -> str:
    ex = "\n".join(f"- {e['entity']}.{e['field']} → {e['assumes']}   (인용: {e['quote']})" for e in examples)
    body = "\n\n".join(item_md(v, (feedback or {}).get(v["stem"])) for v in views)
    return (f"{RULES}\n---\n# 이 작업\n- scene_id: {scene}\n- out_path: {out_path}\n- 일관판 목표: 전제값과 문자 2-gram 겹침 ≤ {LOW} (스크립트가 잰다)\n"
            f"- 모순판 목표: 전제값(또는 그 한 절)과 겹침 ≥ {HIGH}\n\n## 실제 추출 문체 예 (assumed_states)\n{ex}\n\n## 항목 ({len(views)}개)\n\n{body}\n")


# ---------------------------------------------------------------- 행 만들기

def load_pairs() -> dict[str, tuple[dict, dict]]:
    """stem → (정상 행, 모순 행). stem = '<scene>:<nn>'."""
    rows = CP.read_jsonl(L1_PATH)
    out: dict[str, list] = {}
    for r in rows:
        out.setdefault(r["id"][:-1], [None, None])[0 if r["label"] == "정상" else 1] = r
    return {k: (v[0], v[1]) for k, v in out.items() if v[0] and v[1]}


def new_row(orig: dict, twin_id: str, v: dict, l1ctx: dict) -> dict:
    """원래 행 + 새 값 서술 → 새 행(렌더링은 claude_pairs.rerender_row = build_pairs)."""
    spec = dict(orig["hyp_spec"])
    field = spec["field"]
    spec["summary"] = str(v.get("summary") or "").strip()
    if not spec["summary"] or len(spec["summary"]) > 220:
        raise CP.ItemError("schema: summary 가 비었거나 220자 초과")
    if field:
        a = str(v.get("assumes") or "").strip()
        if not a or len(a) > 120:
            raise CP.ItemError("schema: assumes 가 비었거나 120자 초과")
        spec["assumes"] = a
    else:
        spec["assumes"] = None
    fixed = False
    if v.get("sentence"):
        s = str(v["sentence"]).strip()
        if len(s) > 160:
            raise CP.ItemError("schema: sentence 160자 초과")
        spec["quote"] = spec["excerpt"] = s
        fixed = s != orig["hyp_spec"]["quote"]
    r = dict(orig)
    r.update(hyp_spec=spec, id=f"n2b:{orig['id']}", source=SOURCE, work=SOURCE, case_id=f"n2b:{orig['id']}", pair_of=orig["id"],
             contrast_group=f"n2b:{orig['contrast_group']}", pairs_version=CP.PAIRS_VERSION)
    r["source"] = "l1_wm"          # rerender_row 는 source 로 L1/L2 를 가른다 — 렌더링 동안만
    r["premise"], r["hypothesis"] = CP.rerender_row(r, l1ctx)
    r["source"] = SOURCE
    r["text_a"], r["text_b"] = r["premise"], r["hypothesis"]
    r["gen"] = {"task": orig["gen"].get("task"), "prompt": PROMPT_VERSION, "quote_fixed": fixed,
                "negated_requested": wants_neg(orig["id"][:-1]) if orig["label"] == "정상" else None,
                "negated_actual": has_neg(hyp_value(spec)) if orig["label"] == "정상" else None,
                "twin": twin_id, "orig_overlap": round(row_overlap(orig), 3)}
    r["overlap"] = round(row_overlap(r), 3)
    r["overlap_whole"] = round(jaccard(hyp_value(spec), premise_value(r["text_a"], r["premise_kind"])), 3)
    return r


def judge_item(item: dict, pairs: dict, l1ctx: dict) -> tuple[list[dict] | None, str | None]:
    """에이전트 항목 → ([정상 행, 모순 행], None) 또는 (None, 사유). 목표 미달은 '미달: …' 로 구분."""
    stem = item.get("stem")
    if stem not in pairs:
        return None, f"schema: 알 수 없는 stem {stem!r}"
    if item.get("skip"):
        return None, f"agent_skip: {str(item.get('why'))[:60]}"
    ok0, bad0 = pairs[stem]
    try:
        okr = new_row(ok0, f"n2b:{bad0['id']}", item.get("ok") or {}, l1ctx)
        badr = new_row(bad0, f"n2b:{ok0['id']}", item.get("bad") or {}, l1ctx)
    except CP.ItemError as e:
        return None, str(e)
    except Exception as e:  # noqa: BLE001 - 에이전트 JSON 모양 오류
        return None, f"schema: {type(e).__name__} {e}"
    why = []
    if okr["overlap"] > LOW:
        why.append(f"일관판 겹침 {okr['overlap']:.2f} > {LOW}")
    if badr["overlap"] < HIGH:
        why.append(f"모순판 겹침 {badr['overlap']:.2f} < {HIGH}")
    if CP.norm(hyp_value(okr["hyp_spec"])) == CP.norm(hyp_value(badr["hyp_spec"])):
        why.append("일관·모순 가설값이 같다")
    if CP.negation_only(hyp_value(bad0["hyp_spec"]), hyp_value(badr["hyp_spec"])) and hyp_value(bad0["hyp_spec"]):
        why.append("모순판이 부정어만 바뀐 변형")
    if why:
        return None, "미달: " + "; ".join(why)
    okr["gen"]["twin"], badr["gen"]["twin"] = badr["id"], okr["id"]
    return [okr, badr], None


# ---------------------------------------------------------------- CLI

def _prep(a: argparse.Namespace) -> None:
    pairs = load_pairs()
    ex = style_examples()
    by_scene: dict[str, list] = {}
    for stem, (ok, bad) in sorted(pairs.items()):
        by_scene.setdefault(ok["group_id"], []).append(item_view(stem, ok, bad))
    (a.work / "tasks").mkdir(parents=True, exist_ok=True)
    (a.work / "out").mkdir(parents=True, exist_ok=True)
    for sc, views in by_scene.items():
        (a.work / "tasks" / f"{sc}.md").write_text(task_md(sc, views, ex, a.work / "out" / f"{sc}.json"), encoding="utf-8")
    (a.work / "style_examples.json").write_text(json.dumps(ex, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"scenes": len(by_scene), "pairs": len(pairs), "neg_requested": sum(v["negated_expression"] for vs in by_scene.values() for v in vs)}, ensure_ascii=False))


def _build(a: argparse.Namespace) -> None:
    """out/*.json (+ out/retry/*.json 이 있으면 미달분을 그것으로 덮는다) → n2b_paraphrase.jsonl. 아직 미달인 항목의 재작업 파일을 retry/ 에 만든다."""
    pairs = load_pairs()
    l1ctx: dict = {}
    got: dict[str, tuple] = {}
    items_by_stem: dict[str, dict] = {}
    for sub in ("out", "out/retry"):
        d = a.work / sub
        for p in sorted(d.glob("*.json")) if d.exists() else []:
            for it in json.loads(p.read_text(encoding="utf-8")).get("items", []):
                items_by_stem[it.get("stem")] = it | {"_round": 2 if sub.endswith("retry") else 1}
    rows, rej, retry_fb = [], [], {}
    for stem in sorted(pairs):
        it = items_by_stem.get(stem)
        if it is None:
            rej.append({"stem": stem, "why": "no_output"})
            continue
        pr, why = judge_item(it, pairs, l1ctx)
        if pr:
            rows.extend(pr)
        else:
            rej.append({"stem": stem, "why": why, "round": it["_round"]})
            if it["_round"] == 1 and why.startswith("미달"):
                retry_fb[stem] = why
    CP.write_jsonl(a.out, rows)
    CP.write_jsonl(a.work / "n2b_rejects.jsonl", rej)
    if retry_fb and a.make_retry:
        ex = style_examples()
        by_scene: dict[str, list] = {}
        for stem in retry_fb:
            ok, bad = pairs[stem]
            by_scene.setdefault(ok["group_id"], []).append(item_view(stem, ok, bad))
        (a.work / "retry").mkdir(exist_ok=True)
        (a.work / "out" / "retry").mkdir(parents=True, exist_ok=True)
        for sc, views in by_scene.items():
            (a.work / "retry" / f"{sc}.md").write_text(
                task_md(sc, views, ex, a.work / "out" / "retry" / f"{sc}.json", retry_fb), encoding="utf-8")
        print("retry scenes:", len(by_scene), "items:", len(retry_fb))
    print(json.dumps(report(rows, rej), ensure_ascii=False))


def _q(xs: list[float]) -> dict:
    if not xs:
        return {"n": 0}
    s = sorted(xs)
    return {"n": len(s), "p25": round(s[len(s) // 4], 3), "p50": round(statistics.median(s), 3), "p75": round(s[(3 * len(s)) // 4], 3),
            "max": round(s[-1], 3)}


def report(rows: list[dict], rej: list[dict]) -> dict:
    ok = [r for r in rows if r["label"] == "정상"]
    bad = [r for r in rows if r["label"] == "모순"]
    return {"consistent": len(ok), "contradiction": len(bad), "rejected": len(rej),
            "reject_reasons": dict(Counter(r["why"].split(":")[0] for r in rej)),
            "overlap_ok": _q([r["overlap"] for r in ok]), "overlap_bad": _q([r["overlap"] for r in bad]),
            "overlap_whole_ok": _q([r["overlap_whole"] for r in ok]), "overlap_whole_bad": _q([r["overlap_whole"] for r in bad]),
            "orig_overlap_ok": _q([r["gen"]["orig_overlap"] for r in ok]), "orig_overlap_bad": _q([r["gen"]["orig_overlap"] for r in bad]),
            "neg_requested": round(sum(bool(r["gen"]["negated_requested"]) for r in ok) / max(1, len(ok)), 3),
            "neg_actual_ok": round(sum(bool(r["gen"]["negated_actual"]) for r in ok) / max(1, len(ok)), 3),
            "neg_in_bad": round(sum(has_neg(hyp_value(r["hyp_spec"])) for r in bad) / max(1, len(bad)), 3),
            "quote_fixed": sum(bool(r["gen"]["quote_fixed"]) for r in rows),
            "by_premise_kind": dict(Counter(r["premise_kind"] for r in ok))}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prep")
    p.add_argument("--work", type=Path, default=N_DIR / "n2b")
    p.set_defaults(fn=_prep)
    p = sub.add_parser("build")
    p.add_argument("--work", type=Path, default=N_DIR / "n2b")
    p.add_argument("--out", type=Path, default=N_DIR / "n2b_paraphrase.jsonl")
    p.add_argument("--make-retry", action="store_true")
    p.set_defaults(fn=_build)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
