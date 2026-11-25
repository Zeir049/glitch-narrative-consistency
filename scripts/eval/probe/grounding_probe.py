# -*- coding: utf-8 -*-
"""탐침 ①②③: 원장 source_text 가 원문에서 찾아지는가 / 수치 문장 탐지기 반응률 / 원장 부재 13건 근거 문장의 탐지·커버 여부

2026-11-06~07 판단 세션의 읽기 전용 탐침 — 진단용이지 채점기가 아니다(docs/related-work-extraction-verification.md §4·§5).
사용: python scripts/eval/probe/grounding_probe.py <event_sourcing 저장소 경로>
"""
import sys
import json, re, sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
BASE = Path(sys.argv[1])
WORKS = {"q1": ("corpus_q1_geumhotang", ["03","09","13","21","22","35"]),
         "q3": ("corpus_q3_domec4", ["04","05","13","28","36","40","42"])}
WS = re.compile(r"\s+")
ELL = re.compile(r"\.{3,}|…|⋯|\(중략\)|\[중략\]")
NUM = re.compile(r"[0-9０-９]|[일이삼사오육칠팔구십백천만]{1,4}\s*(원|시|분|초|년|월|일|개|벌|명|번|회|째|배)"
                 r"|(한|두|세|네|다섯|여섯|일곱|여덟|아홉|열)\s*(개|벌|명|번|시간|시|달|해|주|배|살|장|병|잔|그릇)"
                 r"|첫째|둘째|셋째|넷째|월요일|화요일|수요일|목요일|금요일|토요일|일요일")
def norm(s): return WS.sub("", s or "")
def sentences(text):
    out=[]; pos=0
    for m in re.finditer(r"[^.!?\n]+[.!?]*", text):
        s=m.group(); 
        if s.strip(): out.append((m.start(), m.end(), s))
    return out
def quotes_of(ev):
    q=[]
    for k in ("source_text",):
        if ev.get(k): q.append(("event", ev[k]))
    for coll in ("state_changes","assumed_states","knowledge_gained"):
        for it in ev.get(coll) or []:
            if isinstance(it, dict) and it.get("source_text"): q.append((coll, it["source_text"]))
    return q
for w,(cdir,miss) in WORKS.items():
    cdir=BASE/cdir
    labels=json.load(open(cdir/"labels.json",encoding="utf-8"))["labels"]
    stats={}; tot_sent=0; flagged=0; flagged_uncov=0; all_uncov=0
    per_ep=[]
    ev_keys=None
    for ep in range(1,6):
        chunks=sorted((cdir/"chunks").glob(f"episode_{ep:02d}_c*.txt"))
        text="\n".join(p.read_text(encoding="utf-8") for p in chunks)
        ntext=norm(text)
        # 원문 정규화 좌표 매핑: 정규화 인덱스 -> 원문 인덱스
        map_idx=[i for i,ch in enumerate(text) if not ch.isspace()]
        doc=json.load(open(cdir/"ledger_nemotron_ultra"/f"episode_{ep:02d}.json",encoding="utf-8"))
        spans=[]  # 원문 좌표의 (start,end) — 찾아진 조각
        for ev in doc.get("events") or []:
            if ev_keys is None: ev_keys=sorted(ev.keys())
            for coll,q in quotes_of(ev):
                pieces=[norm(p) for p in ELL.split(q) if len(norm(p))>=4]
                st=stats.setdefault(coll,{"quotes":0,"full":0,"partial":0,"none":0,"exact_whole":0,"pieces":0,"pieces_found":0,"multi":0})
                st["quotes"]+=1; st["pieces"]+=len(pieces)
                if len(pieces)>1: st["multi"]+=1
                if text.find((q or "").strip().strip('"“”'))>=0: st["exact_whole"]+=1
                found=0
                for p in pieces:
                    i=ntext.find(p)
                    if i>=0:
                        found+=1; spans.append((map_idx[i], map_idx[min(i+len(p),len(map_idx))-1]+1))
                st["pieces_found"]+=found
                if pieces and found==len(pieces): st["full"]+=1
                elif found>0: st["partial"]+=1
                else: st["none"]+=1
        def covered(a,b): return any(s<b and a<e for s,e in spans)
        n=f=fu=au=0
        for a,b,s in sentences(text):
            n+=1; c=covered(a,b)
            if not c: au+=1
            if NUM.search(s):
                f+=1
                if not c: fu+=1
        per_ep.append((ep,n,f,fu,au)); tot_sent+=n; flagged+=f; flagged_uncov+=fu; all_uncov+=au
        # 원장 부재 장면의 근거 인용 (이 회차 것만)
        for sid in miss:
            lab=labels[[k for k in labels if k.endswith(sid)][0]]
            for eq in lab.get("evidence_quotes") or []:
                if int(eq.get("episode") or 0)!=ep: continue
                nq=norm(eq.get("quote"))[:40]
                i=ntext.find(nq)
                if i<0: print(f"  [{w} s{sid} ep{ep}] 인용을 원문에서 못 찾음: {eq.get('quote')[:40]}"); continue
                a=map_idx[i]
                sent=[(x,y,s) for x,y,s in sentences(text) if x<=a<y]
                x,y,s=sent[0] if sent else (a,a+len(nq),eq["quote"])
                print(f"  [{w} s{sid} ep{ep}] 탐지기={'O' if NUM.search(s) else '-'} 커버={'O' if covered(x,y) else '-'} | {s.strip()[:60]}")
    print(f"\n== {w} == 사건 키: {ev_keys}")
    for coll,st in stats.items():
        print(f"  {coll:17s} 인용 {st['quotes']:4d} | 전부찾음 {st['full']:4d} 일부 {st['partial']:3d} 못찾음 {st['none']:3d} | 통째 exact {st['exact_whole']:4d} | 조각 {st['pieces_found']}/{st['pieces']} (…포함 인용 {st['multi']})")
    print(f"  문장 {tot_sent} | 수치 문장 {flagged} ({flagged/tot_sent:.2f}) | 수치·미커버 {flagged_uncov} ({flagged_uncov/tot_sent:.2f}) | 전체 미커버 {all_uncov} ({all_uncov/tot_sent:.2f})")
    print("  회차별 (문장, 수치, 수치·미커버, 전체미커버):", per_ep)
