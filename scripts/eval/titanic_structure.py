#!/usr/bin/env python
"""타이타닉 평가 코퍼스 — 근거 지도 · '고친 흔적' 지름길 · 쌍 구조(R·오라클) 점검 (M-3 D 뒤·E 단계). 모델 호출 없음.

  evidence-map  라벨의 evidence_quotes 를 원장 사건(source_text·전제·지식) 글과 대조해 evidence_event_map.json 을 만든다
                (Weave map_evidence_events.py 와 같은 방식: 부분 포함, 25자 머리·꼬리). matched/ambiguous/unmatched 를 그대로 둔다.
                ambiguous·unmatched 는 work/evidence_review.md 목록으로만 남긴다 — **고르지 않는다**(사용자 확정 몫).
  trace         P/N 수정 문장의 길이 차·편집 거리 단일 특징 AUROC('고친 흔적' 지름길). 어느 하나라도 0.70 을 넘으면 봉인 전에 알린다.
  structure     export_pairs 가 만든 쌍 JSONL → 장면별 쌍 수·라벨 v2 분포, 짚을 수 있는 장면 수 R, 오라클 상한(진단 S 방식).

원문 글은 저장소 밖(--root)에만 있다. 이 스크립트는 글을 출력하지 않는다(review 목록은 저장소 밖 work/ 에 쓴다).
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import train_lib as TL  # noqa: E402

DEFAULT_ROOT = Path(r"C:\judge_models\M3")
LEDGER = "ledger_nemotron_ultra"
SHORTCUT_LIMIT = 0.70


# ---------------------------------------------------------------- 근거 지도

def norm(s: str | None) -> str:
    """공백을 모두 없앤다 — 원문 PDF 줄 잇기 때문에 띄어쓰기 자리가 흔들리므로 Weave 의 공백 접기보다 한 단계 느슨하다."""
    return re.sub(r"\s+", "", str(s or ""))


def event_texts(ep: dict):  # noqa: ANN201
    for ev in ep.get("events", []) or []:
        eid = ev.get("event_id")
        if ev.get("source_text"):
            yield eid, "source_text", norm(ev["source_text"])
        for coll in ("assumed_states", "state_changes", "knowledge_gained"):
            for a in ev.get(coll) or []:
                if isinstance(a, dict) and a.get("source_text"):
                    yield eid, coll, norm(a["source_text"])


def match_quote(quote: str, frags: list[tuple[str, str, str]]) -> dict:
    q = norm(quote)
    hits: dict[str, set[str]] = {}
    for eid, where, text in frags:
        if q and text and (q in text or text in q or (len(q) >= 25 and (q[:25] in text or q[-25:] in text))):
            hits.setdefault(eid, set()).add(where)
    eids = sorted(hits)
    return {"status": "matched" if len(eids) == 1 else "ambiguous" if eids else "unmatched",
            "candidates": [{"event_id": e, "via": sorted(hits[e])} for e in eids]}


def build_evidence_map(labels: dict[str, dict], episodes: list[dict]) -> dict:
    frags = [f for ep in episodes for f in event_texts(ep)]
    out = {"note": "문자열 대조 후보 — 확정 전 사람 확인 필수. Weave map_evidence_events.py 방식, 공백 없이 대조(타이타닉 원문의 PDF 줄 잇기)",
           "scenes": {}}
    stats = Counter()
    for sid, lab in sorted(labels.items()):
        if not lab.get("has_contradiction"):
            continue
        rows = []
        for q in lab.get("evidence_quotes") or []:
            quote = q["quote"] if isinstance(q, dict) else q
            m = match_quote(quote, frags)
            stats[m["status"]] += 1
            rows.append({"quote": norm(quote)[:60], "role": q.get("role") if isinstance(q, dict) else None, **m})
        out["scenes"][sid] = rows
    out["stats"] = dict(stats)
    return out


def review_markdown(emap: dict, labels: dict[str, dict], episodes: list[dict]) -> str:
    """ambiguous·unmatched 후보 목록 — 사용자가 확정한다. 이 세션은 고르지 않는다."""
    ev_text: dict[str, str] = {}
    for ep in episodes:
        for ev in ep.get("events", []) or []:
            ev_text[ev.get("event_id")] = " ".join(str(ev.get("source_text") or "").split())[:160]
    lines = ["# 근거 지도 확정 대기 목록 (ambiguous · unmatched)", "",
             "사용자가 확정한다. 아래는 문자열 대조 결과일 뿐이며 이 세션은 후보를 고르지 않았다.",
             f"matched 는 그대로 근거로 쓰였다. 집계: {emap.get('stats')}", ""]
    n = 0
    for sid, rows in emap["scenes"].items():
        for i, r in enumerate(rows, 1):
            if r["status"] == "matched":
                continue
            n += 1
            lab = labels[sid]
            quote = lab["evidence_quotes"][i - 1]
            lines += [f"## {sid} · 근거 {i}/{len(rows)} · {r['status']}",
                      f"- 유형 {lab.get('type')} / 에피소드 {quote.get('episode') if isinstance(quote, dict) else '?'}",
                      f"- 인용: {quote['quote'] if isinstance(quote, dict) else quote}"]
            if r["candidates"]:
                for c in r["candidates"]:
                    lines.append(f"- 후보 {c['event_id']} (via {', '.join(c['via'])}): {ev_text.get(c['event_id'], '')}")
            else:
                lines.append("- 후보 없음 — 원장이 이 사실을 담지 못했을 수 있다.")
            lines.append("")
    if n == 0:
        lines.append("없음 — 모든 근거가 matched.")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- '고친 흔적' 지름길

def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def trace_features(original: str, replacement: str) -> dict[str, float]:
    ld = len(replacement) - len(original)
    ed = levenshtein(original, replacement)
    return {"len_diff_abs": float(abs(ld)), "len_diff_signed": float(ld),
            "edit_distance": float(ed), "edit_distance_norm": ed / max(len(original), len(replacement), 1)}


def trace_report(validated: list[dict]) -> dict:
    """수정 문장 특징 → 장면 점수 → P/N AUROC. 방향은 둘 다 적는다(높을수록 P 인 쪽, 낮을수록 P 인 쪽)."""
    feats = {s["scene_id"]: (trace_features(s["original"], s["replacement"]), s["kind"] == "P") for s in validated}
    names = next(iter(feats.values()))[0].keys()
    out = {}
    for name in names:
        scores = {sid: (f[name], is_p) for sid, (f, is_p) in feats.items()}
        up = TL.bootstrap_auroc(scores)
        flipped = {sid: (-v, g) for sid, (v, g) in scores.items()}
        down = TL.bootstrap_auroc(flipped)
        best = max(up["auroc"], down["auroc"])
        out[name] = {"auroc_high_is_P": up["auroc"], "ci_high_is_P": [up["lo"], up["hi"]],
                     "auroc_low_is_P": down["auroc"], "ci_low_is_P": [down["lo"], down["hi"]],
                     "strongest": round(best, 3), "over_limit": best > SHORTCUT_LIMIT,
                     "mean_P": round(statistics.mean(v for v, g in scores.values() if g), 3),
                     "mean_N": round(statistics.mean(v for v, g in scores.values() if not g), 3)}
    out["n_P"] = sum(1 for _f, p in feats.values() if p)
    out["n_N"] = sum(1 for _f, p in feats.values() if not p)
    out["any_over_limit"] = any(v["over_limit"] for k, v in out.items() if isinstance(v, dict))
    return out


# ---------------------------------------------------------------- 쌍 구조 · R · 오라클

def structure_report(rows: list[dict]) -> dict:
    by: dict[str, list[dict]] = defaultdict(list)
    pos: dict[str, bool] = {}
    for r in rows:
        by[r["scene_id"]].append(r)
        pos[r["scene_id"]] = bool(r["scene_positive"])
    per_scene = {}
    for sid, rs in sorted(by.items()):
        c = Counter(r["label"] for r in rs)
        per_scene[sid] = {"positive": pos[sid], "pairs": len(rs), **{k: c.get(k, 0) for k in ("모순", "정상", "무관", "제외")}}
    able = sorted(s for s, v in per_scene.items() if v["positive"] and v["모순"] > 0)
    sc_pos = sorted(s for s, v in per_scene.items() if v["positive"])
    sc_neg = sorted(s for s, v in per_scene.items() if not v["positive"])

    def oracle(able_set: set[str]) -> dict:
        # 짚을 수 있는 양성 장면은 모든 정상 장면보다 높고, 나머지 양성은 정상과 동률(0.5)
        scores = {s: (2.0 if s in able_set else 1.0, True) for s in sc_pos}
        scores.update({s: (1.0, False) for s in sc_neg})
        return TL.bootstrap_auroc(scores)
    ex_able = sorted(s for s, v in per_scene.items() if v["positive"] and (v["모순"] > 0 or v["제외"] > 0))
    npairs = [v["pairs"] for v in per_scene.values()]
    return {
        "scenes": len(per_scene), "scenes_pos": len(sc_pos), "scenes_neg": len(sc_neg),
        "pairs_total": len(rows),
        "pairs_per_scene": {"min": min(npairs), "median": statistics.median(npairs), "max": max(npairs),
                            "pos_mean": round(statistics.mean(per_scene[s]["pairs"] for s in sc_pos), 1),
                            "neg_mean": round(statistics.mean(per_scene[s]["pairs"] for s in sc_neg), 1)},
        "label_v2": dict(Counter(r["label"] for r in rows)),
        "label_v2_pos_scenes": dict(Counter(r["label"] for r in rows if r["scene_positive"])),
        "label_v2_neg_scenes": dict(Counter(r["label"] for r in rows if not r["scene_positive"])),
        "R": len(able), "able_scenes": able, "R_total_pos": len(sc_pos),
        "oracle": oracle(set(able)),
        "optimistic_R_with_excluded": len(ex_able), "optimistic_oracle": oracle(set(ex_able)),
        "per_scene": per_scene}


# ---------------------------------------------------------------- CLI

def load_labels(root: Path) -> dict[str, dict]:
    d = json.loads((root / "titanic" / "labels.json").read_text(encoding="utf-8"))["labels"]
    return {l["scene_id"]: l for l in d} if isinstance(d, list) else dict(d)


def load_episodes(root: Path) -> list[dict]:
    led = root / "titanic" / LEDGER
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(led.glob("episode_[0-9][0-9].json"))]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["evidence-map", "trace", "structure"])
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    ap.add_argument("--pairs", type=Path, help="structure: export_pairs 가 만든 JSONL")
    ap.add_argument("--out", type=Path, help="structure·trace: 결과 JSON 경로")
    a = ap.parse_args(argv)
    root: Path = a.root
    if a.cmd == "evidence-map":
        labels, eps = load_labels(root), load_episodes(root)
        emap = build_evidence_map(labels, eps)
        (root / "titanic" / "evidence_event_map.json").write_text(json.dumps(emap, ensure_ascii=False, indent=1), encoding="utf-8")
        (root / "work" / "evidence_review.md").write_text(review_markdown(emap, labels, eps), encoding="utf-8")
        print("evidence_event_map:", emap["stats"])
        return 0
    if a.cmd == "trace":
        val = json.loads((root / "work" / "seed_sheet.validated.json").read_text(encoding="utf-8"))["scenes"]
        rep = trace_report(val)
        if a.out:
            a.out.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
        for k, v in rep.items():
            if isinstance(v, dict):
                print(f"{k:20s} 높을수록P {v['auroc_high_is_P']:.3f} {v['ci_high_is_P']} | 낮을수록P {v['auroc_low_is_P']:.3f} {v['ci_low_is_P']} "
                      f"| 평균 P {v['mean_P']} N {v['mean_N']} | 최강 {v['strongest']}{' ★0.70 초과' if v['over_limit'] else ''}")
        print("0.70 초과:", rep["any_over_limit"])
        return 2 if rep["any_over_limit"] else 0
    rows = TL.read_jsonl(a.pairs)
    rep = structure_report(rows)
    if a.out:
        a.out.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    brief = {k: v for k, v in rep.items() if k != "per_scene"}
    print(json.dumps(brief, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
