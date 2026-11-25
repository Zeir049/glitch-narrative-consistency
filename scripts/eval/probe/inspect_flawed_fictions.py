# -*- coding: utf-8 -*-
"""외부 데이터: FlawedFictions parquet 분할별 행 수·필드·문장 주석 채움 (pyarrow 필요)

2026-11-06~07 판단 세션의 읽기 전용 탐침 — 진단용이지 채점기가 아니다(docs/related-work-extraction-verification.md §4·§5).
사용: python scripts/eval/probe/inspect_flawed_fictions.py <parquet 폴더>
"""
import sys,glob,os,json,collections,statistics
sys.stdout.reconfigure(encoding="utf-8")
try:
    import pyarrow.parquet as pq
except ImportError:
    print("pyarrow 없음"); sys.exit(0)
for p in sorted(glob.glob(os.path.join(sys.argv[1],"*.parquet"))):
    t=pq.read_table(p).to_pylist(); print(f"\n== {os.path.basename(p)} rows={len(t)} cols={list(t[0].keys())}")
    print(" cont_error:",collections.Counter(r["cont_error"] for r in t))
    L=[len(str(r["story"]).split()) for r in t]; print(" story words median/min/max:",statistics.median(L),min(L),max(L))
    def parse(x):
        if x is None: return []
        if isinstance(x,list): return x
        try: return json.loads(x)
        except Exception: return [x]
    el=[parse(r["cont_error_lines"]) for r in t]; cl=[parse(r["contradicted_lines"]) for r in t]
    print(" cont_error_lines filled:",sum(1 for x in el if x)," contradicted_lines filled:",sum(1 for x in cl if x))
    print(" lines per example (err/contr) median:",statistics.median([len(x) for x in el if x] or [0]),statistics.median([len(x) for x in cl if x] or [0]))
    r=t[0]; print(" ex id:",r["example_id"]); print("  expl:",str(r["cont_error_expl"])[:200]); print("  err_lines:",str(r["cont_error_lines"])[:250]); print("  contr_lines:",str(r["contradicted_lines"])[:250])
    ids=collections.Counter(str(r["example_id"]).split("_")[0] for r in t); print(" id prefixes:",dict(list(ids.items())[:6]))
