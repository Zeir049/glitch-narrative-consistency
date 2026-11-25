"""최종 1회 측정 보고서(지시문 V2-F 측정 1) — score_v2 산출(v2 최종 + 비교 열 (i) E1 영 학습) → v2_report.md.

채점은 score_v2.py 가 한다. 이 스크립트는 그 산출을 읽어 표로 옮기고, 유형별·작품별 값을 장면 행에서 센다(새 추정 없음).
- 관문(v2 만): 장면 AUROC ≥ 0.65 그리고 bootstrap 하한 > 0.5
- recall·오탐 @0.5 는 참고용(기준값 없음)
- 고친 흔적 기준선: M3-D 봉인 기록 최대 0.543(정규화 편집거리)

    python scripts/v2/final_report_v2.py --final C:\\judge_models\\V2\\eval\\final
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

import v2_common as C

GATE_AUROC = 0.65
EDIT_TRACE_BASELINE = 0.543
AT = 0.5


def gate(sa: dict) -> bool:
    return sa.get("auroc") is not None and sa["auroc"] >= GATE_AUROC and sa["lo"] is not None and sa["lo"] > 0.5


def group_metrics(rows: list[dict], key: str) -> dict[str, dict]:
    """key(type·work)별: 쌍 수, 장면 AUROC(점), 쌍둥이 d 평균·P>N, recall·오탐 @0.5, 근거 일치."""
    out: dict[str, dict] = {}
    for g in sorted({r.get(key) for r in rows if r.get(key)}):
        sub = [r for r in rows if r.get(key) == g]
        pos = [r["max"] for r in sub if r["y"]]
        neg = [r["max"] for r in sub if not r["y"]]
        by: dict[str, dict] = {}
        for r in sub:
            by.setdefault(r["pair_id"], {})[r["kind"]] = r["max"]
        d = [v["P"] - v["N"] for v in by.values() if "P" in v and "N" in v]
        gr = [r["grounded"] for r in sub if r["y"] and r["grounded"] is not None]
        out[g] = {"n_pairs": len(by), "auroc": round(C.auc(pos, neg), 4) if pos and neg else None,
                  "d_mean": round(statistics.mean(d), 4) if d else None, "P_gt_N": sum(x > 0 for x in d),
                  "recall": sum(p >= AT for p in pos), "n_pos": len(pos), "fp": sum(p >= AT for p in neg), "n_neg": len(neg),
                  "grounded": sum(gr), "n_grounded": len(gr)}
    return out


def _f(x: float | None) -> str:
    return "—" if x is None else f"{x:.3f}"


def summary_line(name: str, s: dict, with_gate: bool) -> str:
    sa, td, g, at = s["scene_auroc"], s["twin_d"], s["grounding"], s.get("at_threshold") or {}
    gate_s = f" · 관문 {'통과' if gate(sa) else '미달'}" if with_gate else ""
    return (f"| {name} | {_f(sa['auroc'])} [{_f(sa['lo'])}, {_f(sa['hi'])}]{gate_s} | {_f(td['mean'])} "
            f"[{_f((td.get('mean_ci') or [None, None])[0])}, {_f((td.get('mean_ci') or [None, None])[1])}] | "
            f"{td['P_gt_N']}/{td['n']} (같음 {td['eq']}) | {g['top_is_diff']}/{g['n_pos']} | "
            f"{at.get('tp', '—')}/{at.get('n_pos', '—')} | {at.get('fp', '—')}/{at.get('n_neg', '—')} |")


def group_table(title: str, gm: dict[str, dict]) -> list[str]:
    lines = [f"#### {title}", "", "| 묶음 | 쌍 | AUROC | d 평균 | P>N | recall@0.5 | 오탐@0.5 | 근거 일치 |", "|---|---|---|---|---|---|---|---|"]
    for g, m in gm.items():
        lines.append(f"| {g} | {m['n_pairs']} | {_f(m['auroc'])} | {_f(m['d_mean'])} | {m['P_gt_N']}/{m['n_pairs']} | "
                     f"{m['recall']}/{m['n_pos']} | {m['fp']}/{m['n_neg']} | {m['grounded']}/{m['n_grounded']} |")
    return lines + [""]


def render(v2: dict, v2_rows: list[dict], e1: dict, e1_rows: list[dict], seal: dict, adapter: dict) -> str:
    sa = v2["scene_auroc"]
    lines = ["# 최종 1회 측정 — 측정 1 (v2 최종 + 비교 열 (i) E1 영 학습)", "",
             "지시문 V2-F 측정 1. 한 번만 돌렸다. 결과를 보고 설정·모델·코드를 바꾸지 않았다.", "",
             f"- 봉인 확인: SEAL.json sha256 `{seal['seal_sha256']}` — {'일치' if seal['seal_ok'] else '불일치'}; "
             f"적힌 파일 {seal['n_files']}개 해시 불일치 {len(seal['mismatches'])}개",
             f"- 평가셋: {v2['n_pairs']}쌍, {v2['n_scenes']}장면, 문장 {v2['n_claims']}개(v2)·{e1['n_claims']}개(E1)",
             f"- 입력: 어댑터 산출 `{v2['s_file']}` sha256 `{v2['s_sha256']}` (이름만 바꿈: {adapter['rename']})",
             f"- 어댑터: {', '.join(f'`{k}` sha256 `{h}`' for k, h in adapter['adapter'].items())}",
             f"- pairs.json sha256 `{next(iter(adapter['input'].values()))}`",
             f"- v2 모델 `{v2['model_dir']}` sha256 `{v2['model_sha256']}` · 온도 {v2['temperature']}",
             f"- E1 모델 `{e1['model_dir']}` sha256 `{e1['model_sha256']}` · 온도 {e1['temperature']}(v2_meta 없음 → v2 형식 기본 1.0)",
             "- 형식: 장면 → 문장(split_sentences) → BM25 k=4 → 문장 p(모순) → max. 두 모델 같은 코드(score_v2.py)·같은 입력.",
             "- AUROC: 장면 단위, 층화 bootstrap 2,000회·시드 0. 쌍둥이 d 구간: 쌍 bootstrap 2,000회·시드 0.",
             "- 근거 일치: P 장면 1위 문장 ∈ (P 문장 중 정규화해 N 에 없는 문장). planted 안 씀.",
             f"- recall·오탐 @{AT}: 기준값이 없어 참고용.",
             f"- 관문(v2 만): AUROC ≥ {GATE_AUROC} 그리고 하한 > 0.5 → **{'통과' if gate(sa) else '미달'}**",
             "- 유료 API: 쓰지 않음(측정 1 은 로컬 GPU 만, 네트워크 호출 없음 — HF_HUB_OFFLINE=1).", "",
             "## 요약", "",
             "| 모델 | 장면 AUROC [95%] | 쌍둥이 d 평균 [95%] | P>N | 근거 일치 | recall@0.5 | 오탐@0.5 |",
             "|---|---|---|---|---|---|---|",
             summary_line("v2 최종 s2_noA_mil", v2, True), summary_line("(i) E1 영 학습", e1, False),
             f"| 고친 흔적 기준선(M3-D 봉인 기록, 단일 특징 최대) | {EDIT_TRACE_BASELINE:.3f} | | | | | |", "",
             f"- 근거 일치 분모: P 장면 수. P/N 사이 다른 문장이 없는 쌍 v2 {v2['grounding']['pairs_without_diff']}, "
             f"E1 {e1['grounding']['pairs_without_diff']}.", ""]
    for name, rows in (("v2 최종 s2_noA_mil", v2_rows), ("(i) E1 영 학습", e1_rows)):
        lines += [f"### {name}", ""]
        lines += group_table("유형별", group_metrics(rows, "type"))
        lines += group_table("작품별", group_metrics(rows, "work"))
    lines += ["묶음별 AUROC 는 점 추정(쌍 수가 작다, 구간 없음).", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--final", type=Path, required=True)
    a = ap.parse_args(argv)
    f = a.final
    out = f / "v2_report.md"
    if out.exists():
        raise SystemExit(f"덮어쓰지 않는다: {out}")
    rd = lambda p: json.loads((f / p).read_text(encoding="utf-8"))  # noqa: E731
    text = render(rd("v2_score.json"), C.read_jsonl(f / "v2_scores.jsonl"), rd("e1_zero_score.json"),
                  C.read_jsonl(f / "e1_zero_scores.jsonl"), rd("seal_check.json"), rd("pd_eval_adapted.meta.json"))
    out.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
