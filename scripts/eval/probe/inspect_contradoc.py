# -*- coding: utf-8 -*-
"""외부 데이터: ContraDoc.json 구조·출처별 수·ref sentences 채움 비율·story 부분

2026-11-06~07 판단 세션의 읽기 전용 탐침 — 진단용이지 채점기가 아니다(docs/related-work-extraction-verification.md §4·§5).
사용: python scripts/eval/probe/inspect_contradoc.py <ContraDoc.json>
"""
import sys
import json,sys,collections,statistics
sys.stdout.reconfigure(encoding="utf-8")
d=json.load(open(sys.argv[1],encoding="utf-8"))
print("top keys:", list(d.keys()))
for top,grp in d.items():
    items=list(grp.values()); print(f"\n== {top}: {len(items)} docs")
    keys=collections.Counter()
    for it in items: keys.update(it.keys())
    print("fields:", dict(keys))
    src=collections.Counter(str(it.get("doc_type")) for it in items); print("source by id prefix:", dict(src))
    if "contra_type" in keys or "evidence" in keys:
        print("contra_type:", collections.Counter(str(it.get("contra_type")) for it in items))
        print("scope:", collections.Counter(str(it.get("scope")) for it in items))
        print("contra_plug:", collections.Counter(str(it.get("contra_plug")) for it in items))
        refk=[k for k in keys if "ref" in k.lower()]; print("ref keys:", refk)
        for rk in refk:
            filled=sum(1 for it in items if it.get(rk)); print(f"  {rk} filled: {filled}/{len(items)}")
        story=[(k,it) for k,it in grp.items() if "stor" in str(it.get("doc_type")).lower()]
        print("story docs:", len(story), "with ref:", sum(1 for k,it in story if any(it.get(rk) for rk in refk)))
        for k,it in story[:2]:
            print("  ---", k, {kk:(str(v)[:220]) for kk,v in it.items() if kk!="text"})
    L=[len(str(it.get("text","")).split()) for it in items]
    print("words median/min/max:", statistics.median(L), min(L), max(L))
    print("lang sample:", str(items[0].get("text",""))[:120].replace("\n"," "))
