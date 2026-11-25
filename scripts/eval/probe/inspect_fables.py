# -*- coding: utf-8 -*-
"""외부 데이터: FABLES.json(압축 해제 폴더) 주장 수·라벨 분포·근거 인용 포함 여부

2026-11-06~07 판단 세션의 읽기 전용 탐침 — 진단용이지 채점기가 아니다(docs/related-work-extraction-verification.md §4·§5).
사용: python scripts/eval/probe/inspect_fables.py <FABLES.json 이 든 폴더>
"""
import sys
import json,sys,collections,os,glob
sys.stdout.reconfigure(encoding="utf-8")
p=glob.glob(os.path.join(sys.argv[1],"**","*.json"),recursive=True); print(p)
d=json.load(open(p[0],encoding="utf-8"))
print(type(d).__name__, len(d), list(d.keys())[:5] if isinstance(d,dict) else "")
claims=[]; 
def collect(o,ctx=()):
    if isinstance(o,dict):
        if "claim" in o and "label" in o: claims.append(o|{"_ctx":ctx})
        else:
            for k,v in o.items(): collect(v,ctx+(k,))
    elif isinstance(o,list):
        for v in o: collect(v,ctx)
collect(d)
print("claims:",len(claims))
print("labels:",collections.Counter(str(c.get("label")) for c in claims))
def has_ev(c):
    ev=c.get("evidence"); 
    return bool(ev) and not (isinstance(ev,list) and all(str(e).strip().lower() in ("n/a","na","") for e in ev))
print("with real evidence:",sum(1 for c in claims if has_ev(c)))
print("with reason:",sum(1 for c in claims if c.get("reason") and str(c.get("reason")).strip() not in ("[]","")))
no=[c for c in claims if str(c.get("label")).lower()=="no" and has_ev(c)]
part=[c for c in claims if "partial" in str(c.get("label")).lower() and has_ev(c)]
print("No with evidence:",len(no),"Partial with evidence:",len(part))
import statistics
evlen=[len(" ".join(c["evidence"]).split()) for c in claims if has_ev(c)]
print("evidence words median/p90/max:",statistics.median(evlen),sorted(evlen)[int(len(evlen)*.9)],max(evlen))
for c in no[:3]: print("  NO ex:",{k:str(v)[:220] for k,v in c.items() if k!="_ctx"}, c["_ctx"][:2])
books=collections.Counter(c["_ctx"][0] for c in claims if c["_ctx"]); print("top-level groups:",len(books), list(books.items())[:3])
