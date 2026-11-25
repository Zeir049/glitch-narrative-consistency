"""[탐색: PD-eval 재사용, 확증 아님] 지시문 V2-X 보고서 — X1·X2 score_v2 산출 + 확증 결과(v2 0.496·E1 0.501) 나란히.

질문 하나: "걸러내기로 줄인 양이 문제였나?" 해석 규칙(main 42697ac §10, 미리 정함):
 - X1 이 0.496 보다 뚜렷이 높으면(구간이 겹치지 않을 정도) "양이 병목이었을 가능성"으로 기록(확증은 새 평가셋 필요).
 - 비슷하면 "양이 아니라 과제·집계 방식이 병목" 결론을 보강.
이 스크립트는 수를 옮겨 적고 위 규칙의 '구간 겹침' 사실만 적는다.

    python scripts/v2/explore_report.py --explore C:\\judge_models\\V2\\eval\\explore --final C:\\judge_models\\V2\\eval\\final
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import v2_common as C
from final_report_v2 import EDIT_TRACE_BASELINE, _f, group_table, group_metrics

TAG = "[탐색: PD-eval 재사용, 확증 아님]"


def line(name: str, s: dict) -> str:
    sa, td, g = s["scene_auroc"], s["twin_d"], s["grounding"]
    at = s.get("at_threshold") or {}
    ci = td.get("mean_ci") or [None, None]
    return (f"| {name} | {_f(sa['auroc'])} [{_f(sa['lo'])}, {_f(sa['hi'])}] | {_f(td['mean'])} [{_f(ci[0])}, {_f(ci[1])}] | "
            f"{td['P_gt_N']}/{td['n']} | {td['eq']} | {g['top_is_diff']}/{g['n_pos']} | "
            f"{at.get('tp', '—')}/{at.get('n_pos', '—')} | {at.get('fp', '—')}/{at.get('n_neg', '—')} |")


def overlap(a: dict, b: dict) -> bool:
    return not (a["lo"] > b["hi"] or b["lo"] > a["hi"])


def render(x1: dict, x1_rows: list[dict], x2: dict, x2_rows: list[dict], v2: dict, e1: dict, seal: dict, extra: dict) -> str:
    lines = [f"# {TAG} 탐색 실행 V2-X — X1 전체 데이터 · X2 정제+A43", "",
             f"**{TAG}** PD-eval 은 확증 측정(v2 0.496)에 이미 썼다. 이 수치는 관문 판정을 바꾸지 않는다. 각 1회, 재실행 없음.", "",
             f"- 봉인 확인: SEAL.json sha256 `{seal['seal_sha256']}` — {'일치' if seal['seal_ok'] else '불일치'}; "
             f"적힌 파일 {seal['n_files']}개 해시 불일치 {len(seal['mismatches'])}개",
             f"- 입력: `{x1['s_file']}` sha256 `{x1['s_sha256']}` (어댑터 이름만 바꿈, 확증 때와 같은 코드)",
             f"- X1 모델 `{x1['model_dir']}` sha256 `{x1['model_sha256']}`",
             f"- X2 모델 `{x2['model_dir']}` sha256 `{x2['model_sha256']}`",
             "- 형식·채점: score_v2.py(장면 → 문장 → BM25 k=4 → max), 온도 1.0, recall·오탐 @0.5 참고용. AUROC 층화 bootstrap 2,000·시드 0.",
             f"- X1 혼합 점검(기록만): 최대 {extra.get('x1_mix')}", "",
             "## 요약", "",
             "| 모델 | 장면 AUROC [95%] | 쌍둥이 d 평균 [95%] | P>N | 동점 | 근거 일치 | recall@0.5 | 오탐@0.5 |",
             "|---|---|---|---|---|---|---|---|",
             line("X1 전체 데이터(1+2단계)", x1), line("X2 정제+A43(MIL만)", x2),
             line("확증: v2 최종 s2_noA_mil", v2), line("확증: (i) E1 영 학습", e1),
             f"| 고친 흔적 기준선 | {EDIT_TRACE_BASELINE:.3f} | | | | | | |", "",
             f"- X1 구간과 확증 v2 구간이 {'겹친다' if overlap(x1['scene_auroc'], v2['scene_auroc']) else '겹치지 않는다'}.",
             f"- X2 구간과 확증 v2 구간이 {'겹친다' if overlap(x2['scene_auroc'], v2['scene_auroc']) else '겹치지 않는다'}.", ""]
    for name, rows in (("X1 전체 데이터", x1_rows), ("X2 정제+A43", x2_rows)):
        lines += [f"### {name}", ""] + group_table("유형별", group_metrics(rows, "type"))
    lines += [f"{TAG}", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--explore", type=Path, required=True)
    ap.add_argument("--final", type=Path, required=True, help="확증 결과(읽기만)")
    ap.add_argument("--x1-meta", type=Path, default=None, help="pd_train_x1.meta.json(혼합 점검 기록)")
    a = ap.parse_args(argv)
    out = a.explore / "explore_report.md"
    if out.exists():
        raise SystemExit(f"덮어쓰지 않는다: {out}")
    rd = lambda p: json.loads(p.read_text(encoding="utf-8"))  # noqa: E731
    extra = {}
    if a.x1_meta:
        m = rd(a.x1_meta)["mix_check"]
        extra["x1_mix"] = f"{m['worst']['value']} ({m['worst']['set']}, {m['worst']['feature']}) — 기록만"
    text = render(rd(a.explore / "x1_score.json"), C.read_jsonl(a.explore / "x1_scores.jsonl"),
                  rd(a.explore / "x2_score.json"), C.read_jsonl(a.explore / "x2_scores.jsonl"),
                  rd(a.final / "v2_score.json"), rd(a.final / "e1_zero_score.json"), rd(a.explore / "seal_check.json"), extra)
    out.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
