# -*- coding: utf-8 -*-
"""외부 데이터: FlawedFictions parquet 과 ContraDoc.json 에서 뽑을 수 있는 모순 문장 쌍 수 (pyarrow 필요)

2026-11-06~07 판단 세션의 읽기 전용 탐침 — 진단용이지 채점기가 아니다(docs/related-work-extraction-verification.md §4·§5).
사용: python scripts/eval/probe/external_pairs_count.py <flawed-fictions parquet 폴더> <ContraDoc.json>
"""
import sys
import re,json,glob,os,collections,statistics
sys.stdout.reconfigure(encoding="utf-8")
import pyarrow.parquet as pq
LI=re.compile(r"<li>(.*?)</li>",re.S)
def lines(x): 
    if not x or x=="NA": return []
    f=LI.findall(x); return [s.strip() for s in f] if f else [x.strip()]
tot=collections.Counter()
stories={}
for p in sorted(glob.glob(os.path.join(sys.argv[1],"*.parquet"))):
    name=os.path.basename(p)[:-8]; t=pq.read_table(p).to_pylist()
    pos=[r for r in t if r["cont_error"]==1.0]
    e=[len(lines(r["cont_error_lines"])) for r in pos]; c=[len(lines(r["contradicted_lines"])) for r in pos]
    pairs=sum(a*b for a,b in zip(e,c))
    print(f"{name}: 양성 {len(pos)} | 오류 문장/건 중앙 {statistics.median(e)} (합 {sum(e)}) | 충돌 문장/건 중앙 {statistics.median(c)} (합 {sum(c)}) | (오류,충돌) 쌍 {pairs}")
    stories[name]={r['story'][:200] for r in pos}
    tot["pairs"]+=pairs; tot["pos"]+=len(pos)
a=stories["flawed_fictions"]; print("negs 분할의 양성이 main 양성과 같은 이야기(앞 200자 기준):", len(a & stories["flawed_fictions_cf_negs"]), len(a & stories["flawed_fictions_error_resolved_negs"]))
print("합계(중복 포함):", dict(tot))
# ContraDoc 쌍 수
d=json.load(open(sys.argv[2],encoding="utf-8"))
for kind in ("story","news","wiki"):
    its=[it for it in d["pos"].values() if it.get("doc_type")==kind]
    refs=[it.get("ref sentences") or [] for it in its]
    refs=[r if isinstance(r,list) else [r] for r in refs]
    print(f"ContraDoc {kind}: 양성 {len(its)} | ref 있는 건 {sum(1 for r in refs if r)} | (evidence, ref) 쌍 {sum(len(r) for r in refs)}")
