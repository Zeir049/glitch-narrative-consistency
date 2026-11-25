# -*- coding: utf-8 -*-
"""탐침 ③: 항목 단위 인용만으로 커버리지 + 수치 보존 검사(커버됐으나 구조값에 그 수치가 없음) + 문장부호 정규화 후 진짜 못 찾은 인용 수

2026-11-06~07 판단 세션의 읽기 전용 탐침 — 진단용이지 채점기가 아니다(docs/related-work-extraction-verification.md §4·§5).
사용: python scripts/eval/probe/number_preservation.py <event_sourcing 저장소 경로>
"""
import sys
import json, re, sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
BASE=Path(sys.argv[1]); WS=re.compile(r"[\s\"'“”‘’「」『』\-—–·,。]+"); ELL=re.compile(r"\.{3,}|…|⋯|\(중략\)|\[중략\]|\s/\s")
WORKS={"q1":("corpus_q1_geumhotang",["03","09","13","21","22","35"]),"q3":("corpus_q3_domec4",["04","05","13","28","36","40","42"])}
NUM=re.compile(r"[0-9０-９]+(?:[.,][0-9]+)?|[일이삼사오육칠팔구십백천만]{1,6}(?=\s*(원|시|분|초|년|월|일|개|벌|명|번|회|째|배|리터|도|층|호|조|킬로|미터|장|명))|(한|두|세|네|다섯|여섯|일곱|여덟|아홉|열)(?=\s*(개|벌|명|번|시간|시|달|해|주|배|살|장|병|잔|그릇|사람|곳|칸))|첫째|둘째|셋째|넷째|월요일|화요일|수요일|목요일|금요일|토요일|일요일")
D={"일":1,"이":2,"삼":3,"사":4,"오":5,"육":6,"칠":7,"팔":8,"구":9}; U={"십":10,"백":100,"천":1000,"만":10000}
SMALL={"한":1,"두":2,"세":3,"네":4,"다섯":5,"여섯":6,"일곱":7,"여덟":8,"아홉":9,"열":10,"첫째":1,"둘째":2,"셋째":3,"넷째":4}
DAY={"월요일":"mon","화요일":"tue","수요일":"wed","목요일":"thu","금요일":"fri","토요일":"sat","일요일":"sun"}
def kor2int(s):
    if s in SMALL: return str(SMALL[s])
    if s in DAY: return DAY[s]
    if re.fullmatch(r"[0-9０-９]+(?:[.,][0-9]+)?", s): return s.replace(",","").lstrip("0") or "0"
    total=0; cur=0
    for ch in s:
        if ch in D: cur=D[ch]
        elif ch in U:
            total+= (cur or 1)*U[ch]; cur=0
    return str(total+cur)
def nums(s): return {kor2int(m.group()) for m in NUM.finditer(s)}
def norm(s): return WS.sub("", s or "")
def sentences(text): return [(m.start(),m.end(),m.group()) for m in re.finditer(r"[^.!?\n]+[.!?]*", text) if m.group().strip()]
def items(ev):
    for coll in ("state_changes","assumed_states","knowledge_gained"):
        for it in ev.get(coll) or []:
            if isinstance(it,dict) and it.get("source_text"):
                val=" ".join(str(it.get(k) or "") for k in ("from","to","assumes","info","decays_to"))
                yield coll,it,it["source_text"],val
    for r in ev.get("worldview_rules") or []: pass
for w,(cdir,miss) in WORKS.items():
    cdir=BASE/cdir; labels=json.load(open(cdir/"labels.json",encoding="utf-8"))["labels"]
    T=F=FU=FL=0; nowhere=0; total_q=0; hits=[]
    for ep in range(1,6):
        text="\n".join(p.read_text(encoding="utf-8") for p in sorted((cdir/"chunks").glob(f"episode_{ep:02d}_c*.txt")))
        ntext=norm(text); mp=[i for i,ch in enumerate(text) if not WS.fullmatch(ch)]
        doc=json.load(open(cdir/"ledger_nemotron_ultra"/f"episode_{ep:02d}.json",encoding="utf-8"))
        spans=[]  # (start,end,valnums)
        rules=doc.get("worldview_rules") or []
        for r in rules:
            if isinstance(r,dict) and r.get("source_text"):
                for p in ELL.split(r["source_text"]):
                    p=norm(p); i=ntext.find(p) if len(p)>=4 else -1
                    if i>=0: spans.append((mp[i],mp[min(i+len(p),len(mp))-1]+1,nums(str(r.get("rule") or r.get("description") or ""))|nums(r["source_text"])))
        for ev in doc.get("events") or []:
            for coll,it,q,val in items(ev):
                total_q+=1; pieces=[norm(p) for p in ELL.split(q) if len(norm(p))>=4]
                found=False
                for p in pieces:
                    i=ntext.find(p)
                    if i>=0: found=True; spans.append((mp[i],mp[min(i+len(p),len(mp))-1]+1,nums(val)))
                if pieces and not found:
                    # 문장 단위로 다시 쪼개서 시도
                    sub=[norm(s) for s in re.split(r"[.!?]\s*", q) if len(norm(s))>=6]
                    if any(ntext.find(s)>=0 for s in sub):
                        for s in sub:
                            i=ntext.find(s)
                            if i>=0: spans.append((mp[i],mp[min(i+len(s),len(mp))-1]+1,nums(val)))
                    else: nowhere+=1
        def cover(a,b): return [sp for sp in spans if sp[0]<b and a<sp[1]]
        for a,b,s in sentences(text):
            T+=1; ns=nums(s)
            if not ns: continue
            F+=1; c=cover(a,b)
            if not c: FU+=1
            elif not any(ns & sp[2] for sp in c): FL+=1
        for sid in miss:
            lab=labels[[k for k in labels if k.endswith(sid)][0]]
            for eq in lab.get("evidence_quotes") or []:
                if int(eq.get("episode") or 0)!=ep: continue
                i=ntext.find(norm(eq["quote"])[:30])
                if i<0: continue
                a=mp[i]; sent=[(x,y,s) for x,y,s in sentences(text) if x<=a<y]
                if not sent: continue
                x,y,s=sent[0]; ns=nums(s); c=cover(x,y)
                flag = "수치없음" if not ns else ("미커버" if not c else ("수치손실" if not any(ns&sp[2] for sp in c) else "보존"))
                hits.append((sid,ep,flag,s.strip()[:50]))
    print(f"\n== {w} == 항목 인용 {total_q}, 문장 쪼개도 못찾음 {nowhere} ({nowhere/total_q:.3f})")
    print(f"  문장 {T} | 수치 문장 {F} ({F/T:.2f}) | 수치·미커버 {FU} ({FU/T:.3f}, 회차 평균 {FU/5:.0f}) | 커버됐으나 수치 손실 {FL} ({FL/T:.3f}, 회차 평균 {FL/5:.0f})")
    by={}
    for sid,ep,flag,s in hits:
        by.setdefault(sid,[]).append(flag); print(f"  s{sid} ep{ep} {flag:5s} | {s}")
    fired=[s for s,fl in by.items() if any(f in ("미커버","수치손실") for f in fl)]
    print(f"  원장 부재 {len(miss)}건 중 신호(미커버 또는 수치손실) 발화 장면: {len(fired)} {fired}")
