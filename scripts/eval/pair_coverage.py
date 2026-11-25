"""쌍 생성기 측정 (읽기 전용) — 장면당 쌍 수·글자 수, kind 분포, 근거 있는 양성의 근거 포함 수.

설계안(docs/learned-judge-training-design.md) §2-0·§2-3 의 측정을 저장소로 옮긴 것. 파일을 쓰지 않는다.
재생은 judge_eval.load_context(work) — 서비스·평가와 같다(5화 확정 원장, knows accumulate, checker 항목 포함).
판정 대상 회차는 6화(scene_events 의 사건에는 episode 가 없어 build_pairs(current_episode=6)로 넘긴다).

조건(같은 표에 나란히)
  v1@32   개정 전: build_pairs(quotas=None)      — kind 순서 + 상한 32
  v2@24   개정 후: build_pairs()                 — kind 별 할당량 + 관련도 정렬, 합 24
  v1@∞    상한 없음: build_pairs(quotas=None, max_premises=10**9) — 참고(도달 가능한 최대)

근거(정답 전제 사건 ID)
  WM        labels.json 의 evidence_event_ids
  Q1·Q3·셜록 evidence_event_map.json 의 후보 — 두 해석을 모두 낸다
            matched: status=="matched" 인 인용의 후보만 / all: 모든 인용의 후보
  규칙 ID 는 지도에 없어 '설정' 유형의 규칙 전제는 세지 못한다(근사).
'근거 포함' = 장면의 쌍 중 premise_id 가 근거 사건 ID 인 것이 하나라도 있음(kind 무관).

토큰 길이(관문 G0 길이, 설계안 §3-3): --tokenizer <HF 이름|폴더> 면 v2@24 쌍을 그 토크나이저로 (전제, 가설) 쌍 인코딩
(truncation 없음, 특수 토큰 포함)해 p50/p95/p99·최대·384 초과 비율과 버킷(128/256/384/512) 분포를 낸다(transformers 필요).
기준 384 = GPU 경로 max_length(G0 길이 결정 2026-11-07, 발췌 160자 유지).

  python scripts/eval/pair_coverage.py [--works wm q1 q3 sherlock] [--json] [--tokenizer klue/roberta-base]
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for p in (str(ROOT), str(Path(__file__).resolve().parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

import judge_eval as JE  # noqa: E402
from app.pipeline.pairs import KIND_ORDER, MAX_PREMISES, PAIRS_VERSION, build_pairs  # noqa: E402

CURRENT_EPISODE = 6
CONDITIONS: dict[str, dict] = {
    "v1@32": {"quotas": None},
    "v2@24": {},
    "v1@inf": {"quotas": None, "max_premises": 10 ** 9},
}
MODES = ("matched", "all")


def evidence(work: str) -> dict[str, dict[str, set[str]]]:
    """{mode: {scene_id: {근거 사건 ID}}} — 양성 장면만, 빈 집합은 뺀다."""
    spec, gold = JE.WORKS[work], JE.load_gold(work)
    out: dict[str, dict[str, set[str]]] = {m: {} for m in MODES}
    if spec.evidence_map is None:
        data = json.loads(spec.labels.read_text(encoding="utf-8"))["labels"]
        labels = data if isinstance(data, list) else list(data.values())
        for lab in labels:
            ids = {i for i in lab.get("evidence_event_ids") or [] if i}
            if gold[lab["scene_id"]]["pos"] and ids:
                for m in MODES:
                    out[m][lab["scene_id"]] = set(ids)
        return out
    emap = json.loads(spec.evidence_map.read_text(encoding="utf-8")).get("scenes") or {}
    for sid, quotes in emap.items():
        if sid not in gold or not gold[sid]["pos"]:
            continue
        for m in MODES:
            ids = {c["event_id"] for q in quotes or [] if m == "all" or q.get("status") == "matched"
                   for c in q.get("candidates") or [] if c.get("event_id")}
            if ids:
                out[m][sid] = ids
    return out


def _q(xs: list[int], p: float) -> int:
    if not xs:
        return 0
    s = sorted(xs)
    return s[min(len(s) - 1, int(round(p * (len(s) - 1))))]


TOKEN_CONDITION = "v2@24"
TOKEN_LIMIT = 384
TOKEN_BUCKETS = (128, 256, 384, 512)  # learned_judge.BUCKETS 와 같다


def token_stats(lengths: list[int]) -> dict:
    n = len(lengths)
    buckets = {f"<={b}": sum(1 for x in lengths if x <= b and (i == 0 or x > TOKEN_BUCKETS[i - 1]))
               for i, b in enumerate(TOKEN_BUCKETS)}
    buckets[f">{TOKEN_BUCKETS[-1]}"] = sum(1 for x in lengths if x > TOKEN_BUCKETS[-1])
    return {"n": n, "p50": _q(lengths, 0.50), "p95": _q(lengths, 0.95), "p99": _q(lengths, 0.99),
            "max": max(lengths, default=0),
            "over_256": round(sum(x > 256 for x in lengths) / n, 4) if n else 0.0,
            f"over_{TOKEN_LIMIT}": round(sum(x > TOKEN_LIMIT for x in lengths) / n, 4) if n else 0.0,
            "buckets": {k: round(v / n, 4) if n else 0.0 for k, v in buckets.items()}}


def measure(work: str, tokenizer=None) -> dict:  # noqa: ANN001
    ctx = JE.load_context(work)
    ev = evidence(work)
    res: dict = {"work": work, "scenes": len(ctx.scenes),
                 "evidence_scenes": {m: len(ev[m]) for m in MODES}, "conditions": {}}
    for name, kw in CONDITIONS.items():
        per_scene, chars, kinds = [], [], Counter()
        covered = {m: 0 for m in MODES}
        for sid in sorted(ctx.scenes):
            group = {"key": sid, "unit": "scene", "events": ctx.scenes[sid]}
            pairs = build_pairs(group, ctx.ledger, ctx.corpus, ctx.registry, ctx.checker_items.get(sid, []),
                                current_episode=CURRENT_EPISODE, **kw)
            per_scene.append(len(pairs))
            if tokenizer is not None and name == TOKEN_CONDITION and pairs:
                enc = tokenizer([p.premise_text for p in pairs], [p.hypothesis_text for p in pairs], truncation=False)
                res.setdefault("_tok", []).extend(len(x) for x in enc["input_ids"])
            chars += [len(p.hypothesis_text) + len(p.premise_text) for p in pairs]
            kinds.update(p.premise_kind for p in pairs)
            ids = {p.premise_id for p in pairs}
            for m in MODES:
                if sid in ev[m] and ids & ev[m][sid]:
                    covered[m] += 1
        res["conditions"][name] = {
            "pairs_mean": round(statistics.mean(per_scene), 1) if per_scene else 0,
            "pairs_max": max(per_scene, default=0), "pairs_total": sum(per_scene),
            "chars_median": int(statistics.median(chars)) if chars else 0,
            "chars_p90": _q(chars, 0.90), "chars_p99": _q(chars, 0.99),
            "kinds": {k: kinds.get(k, 0) for k in KIND_ORDER},
            "covered": covered,
        }
    if tokenizer is not None:
        res["tokens"] = token_stats(res.pop("_tok", []))
    return res


def render(results: list[dict]) -> str:
    names = list(CONDITIONS)
    lines = [f"pairs_version={PAIRS_VERSION} · v2 상한 {MAX_PREMISES} · current_episode={CURRENT_EPISODE}", ""]
    lines.append("## 근거 있는 양성의 근거 포함 (장면 수)")
    lines.append("")
    lines.append("| 작품 | 해석 | 근거 있는 양성 | " + " | ".join(names) + " |")
    lines.append("|---|---|---|" + "---|" * len(names))
    for m in MODES:
        tot = {n: 0 for n in names}
        den = 0
        for r in results:
            d = r["evidence_scenes"][m]
            den += d
            cells = []
            for n in names:
                c = r["conditions"][n]["covered"][m]
                tot[n] += c
                cells.append(str(c))
            lines.append(f"| {r['work']} | {m} | {d} | " + " | ".join(cells) + " |")
        cells = [f"**{tot[n]}** ({tot[n] / den:.1%})" if den else "0" for n in names]
        lines.append(f"| 합 | {m} | {den} | " + " | ".join(cells) + " |")
    lines += ["", "## 장면당 쌍 수 · 쌍 글자 수(가설+전제)", "",
              "| 작품 | 조건 | 장면당 쌍 평균 / 최대 | 합 | 글자 중앙 / p90 / p99 |", "|---|---|---|---|---|"]
    for r in results:
        for n in names:
            c = r["conditions"][n]
            lines.append(f"| {r['work']} | {n} | {c['pairs_mean']} / {c['pairs_max']} | {c['pairs_total']} | "
                         f"{c['chars_median']} / {c['chars_p90']} / {c['chars_p99']} |")
    lines += ["", "## kind 분포 (쌍 수)", "", "| 작품 | 조건 | " + " | ".join(KIND_ORDER) + " |",
              "|---|---|" + "---|" * len(KIND_ORDER)]
    for r in results:
        for n in names:
            k = r["conditions"][n]["kinds"]
            lines.append(f"| {r['work']} | {n} | " + " | ".join(str(k[x]) for x in KIND_ORDER) + " |")
    if any("tokens" in r for r in results):
        lines += ["", f"## 토큰 길이 ({TOKEN_CONDITION}, 쌍 인코딩·특수 토큰 포함, truncation 없음)", "",
                  f"| 작품 | 쌍 | p50 | p95 | p99 | 최대 | >256 | >{TOKEN_LIMIT} | 버킷 128/256/384/512 |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for r in results:
            t = r.get("tokens")
            if t:
                lines.append(f"| {r['work']} | {t['n']} | {t['p50']} | {t['p95']} | {t['p99']} | {t['max']} | "
                             f"{t['over_256']:.1%} | {t[f'over_{TOKEN_LIMIT}']:.1%} | "
                             + " / ".join(f"{v:.1%}" for v in t["buckets"].values()) + " |")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> list[dict]:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--works", nargs="+", default=list(JE.ALL_WORKS), choices=list(JE.ALL_WORKS))
    ap.add_argument("--json", action="store_true", help="표 대신 JSON")
    ap.add_argument("--tokenizer", help="토큰 길이 측정용 토크나이저(HF 이름 또는 폴더)")
    a = ap.parse_args(argv)
    tok = None
    if a.tokenizer:
        from transformers import AutoTokenizer
        tok = AutoTokenizer.from_pretrained(a.tokenizer)
    results = [measure(w, tok) for w in a.works]
    print(json.dumps(results, ensure_ascii=False, indent=2) if a.json else render(results))
    return results


if __name__ == "__main__":
    main()
