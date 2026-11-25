# -*- coding: utf-8 -*-
"""탐침 ②: 못 찾은 인용의 성격 분류(다른 회차·설정집·앞 8자만 일치·어디에도 없음) + '커버됐는데 원장 부재'인 Q1 문장을 덮는 원장 항목

2026-11-06~07 판단 세션의 읽기 전용 탐침 — 진단용이지 채점기가 아니다(docs/related-work-extraction-verification.md §4·§5).
사용: python scripts/eval/probe/ungrounded_quotes.py <event_sourcing 저장소 경로>
"""
import sys
import json, re, sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
BASE=Path(sys.argv[1]); WS=re.compile(r"\s+"); ELL=re.compile(r"\.{3,}|…|⋯|\(중략\)|\[중략\]")
def norm(s): return WS.sub("", s or "")
def items(ev):
    if ev.get("source_text"): yield ("event", ev, ev["source_text"])
    for coll in ("state_changes","assumed_states","knowledge_gained"):
        for it in ev.get(coll) or []:
            if isinstance(it,dict) and it.get("source_text"): yield (coll, it, it["source_text"])
for w,cdir in (("q1","corpus_q1_geumhotang"),("q3","corpus_q3_domec4")):
    cdir=BASE/cdir
    texts={ep:"\n".join(p.read_text(encoding="utf-8") for p in sorted((cdir/"chunks").glob(f"episode_{ep:02d}_c*.txt"))) for ep in range(1,6)}
    ntexts={ep:norm(t) for ep,t in texts.items()}
    setting=norm((cdir/"setting_book.md").read_text(encoding="utf-8")) if (cdir/"setting_book.md").exists() else ""
    cls={"other_ep":0,"setting_book":0,"head8_found":0,"nowhere":0}; shown=0
    print(f"\n== {w} 못찾은 인용 분류 ==")
    for ep in range(1,6):
        doc=json.load(open(cdir/"ledger_nemotron_ultra"/f"episode_{ep:02d}.json",encoding="utf-8"))
        for ev in doc.get("events") or []:
            for coll,it,q in items(ev):
                pieces=[norm(p) for p in ELL.split(q) if len(norm(p))>=4]
                if not pieces or any(ntexts[ep].find(p)>=0 for p in pieces): continue
                p=pieces[0]
                if any(nt.find(p)>=0 for e,nt in ntexts.items() if e!=ep): k="other_ep"
                elif setting and setting.find(p)>=0: k="setting_book"
                elif ntexts[ep].find(p[:8])>=0: k="head8_found"
                else: k="nowhere"
                cls[k]+=1
                if shown<14 and k in ("nowhere","head8_found"):
                    shown+=1
                    # 가장 비슷한 원문 문장 (문자 2-gram Dice)
                    def grams(s): return {s[i:i+2] for i in range(len(s)-1)}
                    g=grams(p); best=(0,"")
                    for m in re.finditer(r"[^.!?\n]+[.!?]*", texts[ep]):
                        s=norm(m.group()); gs=grams(s)
                        if not gs: continue
                        d=2*len(g&gs)/(len(g)+len(gs))
                        if d>best[0]: best=(d,m.group().strip())
                    print(f"  ep{ep} {coll:15s} [{k}] {q[:70]}\n      ↳ 최근접 원문(Dice {best[0]:.2f}): {best[1][:70]}")
    print("  분류:",cls)
    if w=="q1":
        print("\n== q1 '커버됐는데 원장 부재' 문장을 덮는 항목 ==")
        for ep,needle in ((1,"육백원을서랍에"),(1,"첫째월요일과셋째월요일"),(3,"선희가아는절반이아니라"),(5,"탕밖으로나가지않는")):
            doc=json.load(open(cdir/"ledger_nemotron_ultra"/f"episode_{ep:02d}.json",encoding="utf-8"))
            for ev in doc.get("events") or []:
                for coll,it,q in items(ev):
                    if needle in norm(q) or any(needle[:6] in norm(p) and norm(p) in ntexts[ep] for p in ELL.split(q)):
                        desc={k:it.get(k) for k in ("entity","field","from","to","assumes","info","character_id") if it.get(k) is not None}
                        print(f"  ep{ep} {needle[:10]} ← {ev['event_id']} {coll}: {json.dumps(desc,ensure_ascii=False)[:160]} | 인용 {len(q)}자")
