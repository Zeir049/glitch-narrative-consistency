"""judge 앞 단계 개선 P — 타이타닉 E0 순차 맥락 쌍 내보내기·LLM 패킷 수 세기(호출 0). 개발 값(타이타닉 재사용, 사후 설계).

E0(설계 문서 §0-9): 사건 e 의 P·N 장면은 둘 다 같은 원장으로 판정한다.
  원장 = 1–4화 원장 + 사건 048…e−1 의 N 장면 추출(run2, 봉인본)을 순서대로 재생한 것(5화로 이어 붙임).
  048…e−1 은 과거 사건 목록(corpus)에도 들어간다. 현재 장면은 6화(앞 사건이 없으면 5화)로 넣는다.
  사건 ID 충돌(장면 파일마다 EVENT_5_n)은 앞 장면을 EVENT_5_<누적 n> 으로, 현재 장면을 EVENT_<현재 화>_n 으로 바꿔 푼다.
F1·F2 는 app/pipeline/prejudge.py 를 그대로 쓴다(F3 는 LLM 프롬프트 전용 — 쌍 글에는 없다).

    python scripts/eval/prejudge_eval.py export --corpus-dir C:\\judge_models\\M3\\titanic --condition B|P --pairs-version 2|3 --out X.jsonl
    python scripts/eval/prejudge_eval.py plan   --corpus-dir ...                         (LLM 판정 패킷·호출 수)
봉인 폴더는 읽기만 한다.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import export_pairs as X  # noqa: E402
import judge_eval as JE  # noqa: E402

FLAGS = ("f1", "f2", "f2_5", "f3", "f4", "f5", "f6", "f7", "f8", "f9")
CONDITIONS = {"B": {f: False for f in FLAGS},
              "P": {f: f in ("f1", "f2", "f3") for f in FLAGS},
              "P2": {f: True for f in FLAGS}}
_EV5 = re.compile(r"\bEVENT_5_(\d+)\b")


def event_scenes(corpus_dir: Path) -> dict[str, dict[str, str]]:
    """chunk_map.json(봉인) → {사건: {"P": scene, "N": scene}}."""
    cmap = json.loads((corpus_dir.parent / "work" / "chunk_map.json").read_text(encoding="utf-8"))["scenes"]
    out: dict[str, dict[str, str]] = {}
    for sid, v in cmap.items():
        out.setdefault(v["event"], {})[v["kind"]] = sid
    return dict(sorted(out.items()))


def renumber(doc: dict, fn) -> dict:  # noqa: ANN001
    """문서 안의 EVENT_5_n 을 fn(n) 으로 바꾼다(JSON 글 단위 — 교차 참조까지)."""
    return json.loads(_EV5.sub(lambda m: fn(int(m.group(1))), json.dumps(doc, ensure_ascii=False)))


def scene_doc(spec: JE.WorkSpec, sid: str) -> dict:
    return json.loads((spec.scene_events / f"{sid}_event.json").read_text(encoding="utf-8"))


class SeqContext:
    """사건 e 의 E0 맥락(원장·corpus·레지스트리) + 현재 장면 사건(정규화)."""

    def __init__(self, work: str, event: str, prior: list[str], ev_sc: dict, *, f2: bool,
                 f2_counts: Counter, knows_mode: str = "accumulate", flags: dict | None = None) -> None:
        w_checker, w_pipe, compile_ledger, core_normalize = JE._core()
        from app.pipeline import prejudge as PJ

        flags = dict(flags or {}) | {"f2": f2 or bool((flags or {}).get("f2"))}
        self.flags = flags
        # F4: 경계 인식 정규화 1회, 그 뒤 코어(check_event 등)에는 mapping={}
        normalize = PJ.normalize_bounded if flags.get("f4") else core_normalize
        self.rules = PJ.f2_rules(base=bool(flags.get("f2")), assumed_location=bool(flags.get("f2_5")))
        self.normalize, self.PJ = normalize, PJ
        spec = JE.WORKS[work]
        reg = json.loads(spec.registry.read_text(encoding="utf-8"))
        mapping = {e["new_label"]: e["id"] for c in ("characters", "locations", "objects", "times", "worldview_rules")
                   for e in reg.get(c, []) or [] if e.get("new_label")}
        n_eps = JE.episode_count(spec)
        episodes = [normalize(json.loads((spec.ledger_dir / f"episode_{i:02d}.json").read_text(encoding="utf-8")), mapping)
                    for i in range(1, n_eps + 1)]
        rules = JE.collect_episode_rules(list(enumerate(episodes, 1)))   # 1–4화만(조건과 무관하게 같게)
        registry = dict(reg)
        if not registry.get("worldview_rules"):
            registry["worldview_rules"] = rules
        self.prior_scenes = [ev_sc[p]["N"] for p in prior]
        if prior:
            evs, off = [], 0
            for psid in self.prior_scenes:
                d = scene_doc(spec, psid)
                k = len(d.get("events") or [])
                d = renumber(d, lambda n, off=off: f"EVENT_{n_eps + 1}_{off + n}")
                for e in sorted(d.get("events") or [], key=lambda x: x.get("order", 0)):
                    e = dict(e)
                    if e.get("scene_id"):
                        e["scene_id"] = f"{psid}:{e['scene_id']}"
                    e["order"] = len(evs) + 1
                    evs.append(e)
                off += k
            episodes.append(normalize({"episode": n_eps + 1, "events": evs}, mapping))
        if self.rules:
            episodes = [PJ.f2_doc(ep, registry, f2_counts, rules=self.rules)[0] for ep in episodes]
        self.cur_ep = len(episodes) + 1
        replay = [PJ.f6_doc(ep) for ep in episodes] if flags.get("f6") else episodes   # F6: 재생에서만 뺀다
        self.ledger = compile_ledger(episodes=replay, timelines=JE.timelines_of(work), knows_mode=knows_mode)
        self.corpus = []
        for ep in episodes:
            for ev in sorted(ep["events"], key=lambda e: e.get("order", 0)):
                self.corpus.append(dict(ev) | {"episode": ep["episode"], "global_idx": len(self.corpus)})
        self.mapping, self.registry, self.reg_raw = mapping, registry, reg
        self.core_mapping = {} if flags.get("f4") else mapping
        self.w_checker, self.w_pipe, self.f2, self.f2_counts = w_checker, w_pipe, f2, f2_counts
        self.event, self.prior = event, list(prior)

    def scene(self, spec: JE.WorkSpec, sid: str) -> tuple[list[dict], list[dict], dict[str, str]]:
        """현재 장면 사건(정규화·F2)·checker 항목·새 ID→원래 ID."""
        d = scene_doc(spec, sid)
        back: dict[str, str] = {}

        def fn(n: int) -> str:
            new = f"EVENT_{self.cur_ep}_{n}"
            back[new] = f"EVENT_5_{n}"
            return new
        d = renumber(d, fn) if self.cur_ep != 5 else d
        if self.cur_ep == 5:
            back = {e.get("event_id"): e.get("event_id") for e in d.get("events") or []}
        evs = [self.normalize(e, self.mapping) for e in d.get("events", [])]
        if self.rules:
            evs = self.PJ.f2_doc({"events": evs}, self.registry, self.f2_counts, rules=self.rules)[0]["events"]
        items = []
        for e in evs:
            for f in self.w_checker.check_event(e, self.ledger, self.core_mapping):
                if f.get("severity") == self.w_checker.SEV_INFO and not f.get("needs_judge"):
                    continue
                items.append({"kind": "checker", "event_id": e.get("event_id"), "scene_id": e.get("scene_id"),
                              "severity": f.get("severity"), "type": f.get("type"), "entity": f.get("entity"),
                              "field": f.get("field"), "premise": f.get("premise"),
                              "ledger_value": f.get("ledger_value"), "since": f.get("since"),
                              "detail": f.get("detail"), "needs_judge": bool(f.get("needs_judge")),
                              "evidence_trace": self.w_pipe.trace_evidence(self.ledger, self.corpus, f)})
        return evs, items, back


def contexts(work: str, corpus_dir: Path, *, f2: bool, f2_counts: Counter, flags: dict | None = None):  # noqa: ANN201
    ev_sc = event_scenes(corpus_dir)
    evs = list(ev_sc)
    for i, e in enumerate(evs):
        yield e, ev_sc[e], SeqContext(work, e, evs[:i], ev_sc, f2=f2, f2_counts=f2_counts, flags=flags)


def export(work: str, corpus_dir: Path, condition: str, pairs_version: str) -> tuple[list[dict], dict]:
    from app.pipeline.pairs import build_pairs
    from app.pipeline import prejudge as PJ

    flags = CONDITIONS[condition]
    spec = JE.WORKS[work]
    labels = X.load_scene_labels(work)
    ev_map = X.evidence_ids(work, labels)
    scratch: Counter = Counter()   # 맥락마다 같은 문서를 다시 처리하므로 적용 수는 f2_census 로 한 번만 센다
    rows, ctx_log = [], []
    for e, sc, ctx in contexts(work, corpus_dir, f2=flags["f2"], f2_counts=scratch, flags=flags):
        ctx_log.append({"event": e, "prior_events": ctx.prior, "prior_scenes": ctx.prior_scenes,
                        "context_episode_events": len([c for c in ctx.corpus if c["episode"] > 4]),
                        "current_episode": ctx.cur_ep, "scenes": sc})
        for kind in ("P", "N"):
            sid = sc[kind]
            lab = labels[sid]
            events, items, back = ctx.scene(spec, sid)
            positive = bool(lab.get("has_contradiction"))
            evidence = ev_map.get(sid, set())
            meta = {"work": work, "scene_id": sid, "group_id": f"{work}:{sid}", "scene_positive": positive,
                    "case_id": sid, "operator": None, "type": lab.get("type"), "contrast_group": None,
                    "source": "real", "split": "real", "label_version": 2}
            starts = None
            if flags.get("f5"):   # F5: 서비스 학습 judge 처럼 묶음마다 패킷(그룹) — 장면 점수는 그 OR(max)
                bundled, starts = PJ.f5_regroup(events)
                groups = []
                for x in bundled:
                    if not groups or groups[-1]["key"] != x["scene_id"]:
                        groups.append({"key": x["scene_id"], "unit": "scene", "events": []})
                    groups[-1]["events"].append(x)
            else:
                groups = [{"key": sid, "unit": "scene", "events": events}]
            snaps = (PJ.Snapshots(ctx.ledger, events, starts=starts, view=PJ.f6_view if flags.get("f6") else None)
                     if flags["f1"] else None)

            def bp(render: str) -> list:
                out = []
                for group in groups:
                    if snaps is not None:
                        out += PJ.build_pairs_f1(group, snaps, ctx.corpus, ctx.registry, list(items), render=render)
                    else:
                        out += build_pairs(group, ctx.ledger, ctx.corpus, ctx.registry, list(items), render=render)
                return out
            pairs = bp("v2")
            out_pairs = pairs
            if str(pairs_version) == "3":
                out_pairs = bp("v3")
                assert [(p.premise_kind, p.premise_id, p.event_id, p.entity, p.field) for p in pairs] == \
                    [(p.premise_kind, p.premise_id, p.event_id, p.entity, p.field) for p in out_pairs], sid
            labs = X.label_scene_v2(pairs, lab, evidence, tau_h=X.TAU_H, tau_p=X.TAU_P)
            v1 = [X.real_label(p, positive=positive, scene_type=lab.get("type"), evidence=evidence,
                               evidence_keys={(p.entity, p.field) for p in pairs if p.premise_id in evidence})
                  for p in pairs]
            for p, (label, keyed), (label1, _k) in zip(out_pairs, labs, v1):
                r = X._row(p, label, keyed=keyed, label_v1=label1, pairs_version=str(pairs_version), **meta)
                r["event_id_orig"] = back.get(p.event_id, p.event_id)
                r["prejudge"] = condition
                rows.append(r)
    return rows, {"condition": condition, "flags": flags,
                  "f2_counts": f2_census(work, corpus_dir, flags) if (flags["f2"] or flags.get("f2_5")) else None,
                  "contexts": ctx_log}


def f2_census(work: str, corpus_dir: Path, flags: dict | None = None) -> dict:
    """F2 규칙별 적용 수 — 문서마다 한 번(1–4화 원장 + 장면 48, 정규화 뒤). 원장 몫과 장면 몫을 나눠 센다.
    F6 재생 제외 수와 F4 경계 정규화가 막은 치환(코어 normalize 와 결과가 다른 문자열 칸 수)도 함께 센다."""
    from app.pipeline import prejudge as PJ

    flags = flags or {"f2": True}
    _wc, _wp, _cl, core_normalize = JE._core()
    normalize = PJ.normalize_bounded if flags.get("f4") else core_normalize
    rules = PJ.f2_rules(base=bool(flags.get("f2")), assumed_location=bool(flags.get("f2_5")))
    spec = JE.WORKS[work]
    reg = json.loads(spec.registry.read_text(encoding="utf-8"))
    mapping = {e["new_label"]: e["id"] for c in ("characters", "locations", "objects", "times", "worldview_rules")
               for e in reg.get(c, []) or [] if e.get("new_label")}
    led, scn = Counter(), Counter()

    def broken(raw: Any) -> int:   # 코어 normalize 와 경계 정규화 결과가 다른 문자열 칸 수(H2 가 깨뜨리는 곳)
        a, b = core_normalize(raw, mapping), PJ.normalize_bounded(raw, mapping)
        n = 0
        stack = [(a, b)]
        while stack:
            x, y = stack.pop()
            if isinstance(x, dict):
                stack += [(x[k], y[k]) for k in x]
            elif isinstance(x, list):
                stack += list(zip(x, y))
            elif x != y:
                n += 1
        return n

    for i in range(1, JE.episode_count(spec) + 1):
        raw = json.loads((spec.ledger_dir / f"episode_{i:02d}.json").read_text(encoding="utf-8"))
        led["f4_fields_differ"] += broken(raw)
        d = PJ.f2_doc(normalize(raw, mapping), reg, led, rules=rules)[0]
        PJ.f6_doc(d, led)
    for sc in event_scenes(corpus_dir).values():
        for kind in ("P", "N"):
            raw = scene_doc(spec, sc[kind])
            scn["f4_fields_differ"] += broken(raw)
            d = PJ.f2_doc({"events": [normalize(e, mapping) for e in raw.get("events", [])]}, reg, scn, rules=rules)[0]
            PJ.f6_doc(d, scn)
    keys = list(PJ.F2_RULES) + [PJ.F2_ASSUMED_LOCATION, "f4_fields_differ", "f6_events", "f6_state_changes"]
    return {"ledger_ep1_4": {k: led.get(k, 0) for k in keys}, "scenes_48": {k: scn.get(k, 0) for k in keys}}


def plan(work: str, corpus_dir: Path) -> dict:
    """LLM judge 패킷 수(서비스 unit=scene: 장면 사건의 scene_id 묶음) × k. 조건(B/P)과 무관하다(F1–F3 은 묶음을 바꾸지 않는다)."""
    from app.core.config import settings

    spec = JE.WORKS[work]
    per = {}
    for e, sc in event_scenes(corpus_dir).items():
        for kind in ("P", "N"):
            d = scene_doc(spec, sc[kind])
            per[sc[kind]] = len({x.get("scene_id") or x.get("event_id") for x in d.get("events") or []})
    k = settings.weave_judge_k
    return {"packets": sum(per.values()), "k": k, "calls_per_condition": sum(per.values()) * k, "per_scene": per}


def service(work: str, corpus_dir: Path, condition: str, out_dir: Path) -> dict:
    """서비스 경로(run_checks_parallel) 호출 0 점검: 장면별 판정 패킷 수·프롬프트 글자 수·checker 강한/약한 모순 수.
    판정 호출 자리(_judge_run)는 프롬프트만 받아 두는 대역으로 바꾼다. 설정 플래그는 조건대로 켜고 끝나면 되돌린다."""
    import tempfile

    from app.core.config import settings
    from app.pipeline import weave as W

    flags = CONDITIONS[condition]
    old = {f: getattr(settings, f"weave_prejudge_{f}") for f in FLAGS}
    for f in FLAGS:
        setattr(settings, f"weave_prejudge_{f}", bool(flags[f]))
    cfg = W.weave_config()
    cfg["judge"]["unit"] = "scene"
    spec = JE.WORKS[work]
    labels = X.load_scene_labels(work)
    orig = W._judge_run
    prompts: dict[str, str] = {}

    def fake(g, p, i, c):  # noqa: ANN001, ANN202
        if i == 1:
            prompts[g["key"]] = p
        return {"run_index": i, "status": "failed", "verdict": None, "message": "dry-run"}
    W._judge_run = fake
    per: dict[str, dict] = {}
    pj_total: Counter = Counter()
    try:
        for e, sc, ctx in contexts(work, corpus_dir, f2=flags["f2"], f2_counts=Counter(), flags=flags):
            for kind in ("P", "N"):
                sid = sc[kind]
                evs, _items, _b = ctx.scene(spec, sid)
                prompts.clear()
                with tempfile.TemporaryDirectory() as tmp:
                    ch = W.run_checks_parallel(evs, ctx.ledger, ctx.mapping, ctx.corpus, cfg, Path(tmp),
                                               registry=ctx.registry, concurrency=1)
                sev = Counter(it.get("severity") for it in ch["checker"])
                per[sid] = {"positive": bool(labels[sid].get("has_contradiction")), "event": e,
                            "packets": len(ch["judge"]), "prompt_chars": sum(len(p) for p in prompts.values()),
                            "hard": sev.get(W.w_checker.SEV_HARD, 0), "soft": sev.get(W.w_checker.SEV_SOFT, 0),
                            "hard_types": dict(Counter(it.get("type") for it in ch["checker"]
                                                       if it.get("severity") == W.w_checker.SEV_HARD))}
                pj_total.update((ch["config"].get("prejudge") or {}))
                if sid in ("scene_41",):
                    (out_dir / f"prompts_{condition}_{sid}.json").write_text(
                        json.dumps(prompts, ensure_ascii=False, indent=1), encoding="utf-8")
    finally:
        W._judge_run = orig
        for f, v in old.items():
            setattr(settings, f"weave_prejudge_{f}", v)
    k = int(cfg["judge"].get("k", 3))
    pos = [v for v in per.values() if v["positive"]]
    neg = [v for v in per.values() if not v["positive"]]
    summ = {"condition": condition, "flags": flags, "k": k,
            "packets": sum(v["packets"] for v in per.values()),
            "calls": k * sum(v["packets"] for v in per.values()),
            "prompt_chars_per_run": sum(v["prompt_chars"] for v in per.values()),
            "hard": {"P": sum(v["hard"] for v in pos), "N": sum(v["hard"] for v in neg)},
            "soft": {"P": sum(v["soft"] for v in pos), "N": sum(v["soft"] for v in neg)},
            "scenes_with_hard": {"P": sum(v["hard"] > 0 for v in pos), "N": sum(v["hard"] > 0 for v in neg)},
            "hard_types": dict(sum((Counter(v["hard_types"]) for v in per.values()), Counter())),
            "packets_mean": {"P": round(sum(v["packets"] for v in pos) / len(pos), 2),
                             "N": round(sum(v["packets"] for v in neg) / len(neg), 2)},
            "prejudge_counts": dict(pj_total), "per_scene": per}
    (out_dir / f"service_{condition}.json").write_text(json.dumps(summ, ensure_ascii=False, indent=1), encoding="utf-8")
    return summ


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=("export", "plan", "service"))
    ap.add_argument("--work", default="titanic")
    ap.add_argument("--corpus-dir", type=Path, required=True)
    ap.add_argument("--condition", choices=sorted(CONDITIONS), default="B")
    ap.add_argument("--pairs-version", choices=("2", "3"), default="2")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    JE.register_corpus(a.work, a.corpus_dir)
    if a.cmd == "plan":
        print(json.dumps(plan(a.work, a.corpus_dir) | {"per_scene": "…"}, ensure_ascii=False))
        return
    if a.cmd == "service":
        a.out.mkdir(parents=True, exist_ok=True)
        r = service(a.work, a.corpus_dir, a.condition, a.out)
        print(json.dumps({k: v for k, v in r.items() if k != "per_scene"}, ensure_ascii=False))
        return
    rows, meta = export(a.work, a.corpus_dir, a.condition, a.pairs_version)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    with a.out.open("w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    a.out.with_suffix(".meta.json").write_text(json.dumps(meta | {"n_rows": len(rows), "labels": dict(
        Counter(r["label"] for r in rows))}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(len(rows), "pairs ->", a.out, meta["f2_counts"], dict(Counter(r["label"] for r in rows)))


if __name__ == "__main__":
    main()
