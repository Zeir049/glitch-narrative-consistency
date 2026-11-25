# -*- coding: utf-8 -*-
"""탐침 ④: 엔티티 커버리지 신호 — 레지스트리 엔티티가 원문에 등장하는데 그 회차 사건이 참조하지 않는 수

2026-11-06~07 판단 세션의 읽기 전용 탐침 — 진단용이지 채점기가 아니다(docs/related-work-extraction-verification.md §4·§5).
사용: python scripts/eval/probe/entity_coverage.py <event_sourcing 저장소 경로>
"""
import sys
import json, re, sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
BASE=Path(sys.argv[1])
def refs(ev):
    out=set()
    for k in ("participant_character_ids","witness_character_ids","objects"):
        out.update(ev.get(k) or [])
    for k in ("main_actor_character_id","location_id","time_id"):
        if ev.get(k): out.add(ev[k])
    for coll in ("state_changes","assumed_states"):
        for it in ev.get(coll) or []:
            if isinstance(it,dict):
                if it.get("entity"): out.add(it["entity"])
                if it.get("to") and isinstance(it["to"],str): out.add(it["to"])
    for it in ev.get("knowledge_gained") or []:
        if isinstance(it,dict) and it.get("character_id"): out.add(it["character_id"])
    return out
for w,cdir,regf,watch in (("q1","corpus_q1_geumhotang","entity_registry_q1.json",["사진","뒷문","열쇠","요금","빗장","장부"]),
                          ("q3","corpus_q3_domec4","entity_registry_q3.json",["사진","일지","태블릿","UPS","뱅크","밸브","큐"])):
    reg=json.load(open(BASE/cdir/regf,encoding="utf-8"))
    ents=[]
    for coll in ("characters","locations","objects"):
        for e in reg.get(coll) or []:
            eid=e.get(f"{coll[:-1]}_id") or e.get("id") or e.get("character_id") or e.get("location_id") or e.get("object_id")
            names=[n for n in [e.get("name")]+list(e.get("aliases") or []) if n and len(n)>=2]
            if eid and names: ents.append((coll,eid,names))
    print(f"\n== {w} == 레지스트리 엔티티 {len(ents)} (키 예: {list((reg.get('objects') or [{}])[0].keys())[:6]})")
    tot_m=tot_u=0
    for ep in range(1,6):
        text="\n".join(p.read_text(encoding="utf-8") for p in sorted((BASE/cdir/"chunks").glob(f"episode_{ep:02d}_c*.txt")))
        doc=json.load(open(BASE/cdir/"ledger_nemotron_ultra"/f"episode_{ep:02d}.json",encoding="utf-8"))
        referenced=set()
        for ev in doc.get("events") or []: referenced|=refs(ev)
        mentioned=[(c,i,n) for c,i,n in ents if any(nm in text for nm in n)]
        unc=[(c,i,n) for c,i,n in mentioned if i not in referenced]
        tot_m+=len(mentioned); tot_u+=len(unc)
        flag=[f"{i}({n[0]})" for c,i,n in unc if any(k in " ".join(n) for k in watch)]
        print(f"  ep{ep} 언급 {len(mentioned):3d} / 미참조 {len(unc):3d} (객체 {sum(1 for c,_,_ in unc if c=='objects')}, 장소 {sum(1 for c,_,_ in unc if c=='locations')}, 인물 {sum(1 for c,_,_ in unc if c=='characters')}) | 관심 엔티티 미참조: {flag}")
    print(f"  합계 언급 {tot_m} / 미참조 {tot_u} ({tot_u/max(tot_m,1):.2f})")
