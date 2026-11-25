# -*- coding: utf-8 -*-
"""
weave_pipeline.py — 검사 실행 + 검토 큐 생성 (제품 계층)

    확정 원장 재생 → 신규 화 사건별 checker → ERS → judge k회 → review_queue.json

**이 모듈은 원장을 갱신하지 않는다.** 사용자가 UI/CLI에서 확정(commit)해야
원장에 들어간다. 검사 단계에서 원장을 건드리면 오탐 하나가 이후 모든 화의
판정을 오염시키기 때문이다.

코어 호출만 한다
----------------
  validator.Validator      replay 검증 (확정분 재생 시)
  checker.check_event      전제 대조 — 판정 규칙 무수정
  ers.rank                 관련 과거 사건 top-k
  judge.build_judge_prompt 판정 프롬프트 구성 — 무수정
  judge.judge              manual 모드의 프롬프트 저장에만 사용
  weave_llm.complete_json  실제 판정 호출 (provider: nim/anthropic/manual)

**judge.py는 수정하지 않는다.** 프롬프트 구성은 judge.py가, "누가 판정하나"는
weave_llm과 project.json의 llm 설정이 담당한다 — 경계를 코드로 고정한 것이다.

판정 단위 (project.json의 judge.unit)
-------------------------------------
  "event"  사건 하나 = 패킷 하나. judge.build_judge_prompt를 그대로 쓴다.
  "scene"  같은 scene_id의 사건들을 묶어 패킷 하나 (G3에서 쓴 방식).
           scene_id가 없으면 사건 단위로 자동 폴백한다.
  "both"   둘 다 돌리고 OR 집계. 호출 수가 배로 든다.

  G3 결과: 장면 묶음이 scene_09(사건 관계 모순)를 잡았으나 scene_13·22를
  희석시켰다(B-013). 어느 쪽이 나은지는 아직 통제 실험 전이므로
  **설정값으로 남겨 두고 기본은 scene**으로 한다.

k회 반복
--------
Track 1에서 표준으로 확정된 프로토콜. 같은 패킷을 k회 판정하고
검출률(n/k)과 다수결을 함께 보고한다. 1회 판정은 정상 장면에서
평균 10% 오탐을 만든다(`handoff/track1_report.md` §4-2).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from checker import SEV_HARD, SEV_INFO, SEV_SOFT, check_event, conflicts_of
from ers import load_corpus, rank
from judge import build_judge_prompt, judge
from ledger import Ledger, apply_event, compile_ledger, normalize
import weave_llm
from validator import Validator

BASE = Path(__file__).resolve().parent

POSITIVE_VERDICTS = {"모순", "설정누수", "지식범위위반"}
SEVERITY_ORDER = {SEV_HARD: 0, SEV_SOFT: 1, SEV_INFO: 2}


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- 원장 재생

def replay_confirmed(episodes: list[dict], timelines=None, snapshot_dir=None,
                     knows_mode: str = "legacy"):
    """확정된 화들만 재생해 현재 원장을 만든다. (B-039) knows_mode 지원."""
    v = Validator()
    led = compile_ledger(episodes=episodes, validator=v, snapshot_dir=snapshot_dir,
                         timelines=timelines, knows_mode=knows_mode)
    return led, v


# ---------------------------------------------------------------- 패킷 구성

def scene_groups(events: list[dict], unit: str) -> list[dict]:
    """판정 패킷 목록. 각 패킷은 {"key", "unit", "events": [...]}."""
    if unit == "event":
        return [{"key": e.get("event_id"), "unit": "event", "events": [e]}
                for e in events]
    groups, order = {}, []
    for e in events:
        sid = e.get("scene_id")
        key = sid if sid else e.get("event_id")
        u = "scene" if sid else "event"
        if key not in groups:
            groups[key] = {"key": key, "unit": u, "events": []}
            order.append(key)
        groups[key]["events"].append(e)
    return [groups[k] for k in order]


def build_packet_prompt(group: dict, ledger: Ledger, mapping: dict, corpus: list,
                        findings_by_event: dict, retrieval: str = "ers",
                        top_k: int = 5, registry=None) -> tuple[str, list]:
    """패킷 프롬프트와 그 패킷이 참조한 ERS 사건 목록.

    사건 1개짜리 패킷은 코어의 build_judge_prompt를 **그대로** 쓴다.
    사건 2개 이상(장면 묶음)은 사건별 프롬프트를 만들고 머리말로 묶는다 —
    코어 프롬프트 구성 함수를 고치지 않기 위한 선택이다.
    """
    evs = group["events"]
    related_all, seen = [], set()
    for e in evs:
        for r in rank(e, corpus, top_k=top_k, mapping=mapping):
            if r["event_id"] not in seen:
                seen.add(r["event_id"])
                related_all.append(r)
    related_all.sort(key=lambda r: -r["ers"])

    if len(evs) == 1:
        e = evs[0]
        return build_judge_prompt(e, ledger, related_all, mapping,
                                  findings=findings_by_event.get(e.get("event_id")),
                                  retrieval_mode=retrieval, corpus=corpus,
                                  registry=registry), related_all

    head = [
        "당신은 연재 소설의 서사 일관성 검수자다.",
        f"아래는 **한 장면({group['key']})을 이루는 사건 {len(evs)}건**이다.",
        "사건 하나만 떼어 보면 정상이어도 **사건들의 관계**에서 모순이 생길 수 있으므로,",
        "반드시 전체를 함께 보고 **장면 전체에 대해 한 번** 판정하라.",
        "",
    ]
    cross = []
    for e in evs:
        for c in e.get("cross_event_assumptions", []) or []:
            cross.append(f"    · {e.get('event_id')} ↔ {c.get('with_event_id','')}: "
                         f"{c.get('assumes','')}")
    if cross:
        head += ["## 사건 간 관계 전제 (cross_event_assumptions)"] + cross + [""]

    body = []
    for i, e in enumerate(evs, 1):
        body.append(f"{'=' * 66}\n### 이 장면의 사건 {i}/{len(evs)} — {e.get('event_id')}\n{'=' * 66}")
        body.append(build_judge_prompt(
            e, ledger, related_all, mapping,
            findings=findings_by_event.get(e.get("event_id")),
            retrieval_mode=retrieval, corpus=corpus, registry=registry))
    tail = [
        "",
        "=" * 66,
        "## 최종 지시 (장면 전체)",
        "위 사건들을 **하나의 장면으로** 보고 판정하라. 사건 하나에서라도 문제가 있으면,",
        "또는 사건들의 관계에서 문제가 있으면 장면 전체를 그 유형으로 판정한다.",
        "출력은 JSON 하나만:",
        '   {"verdict": "...", "reason": "...", "evidence_event_ids": ["..."], "confidence": 0.0}',
    ]
    return "\n".join(head + body + tail), related_all


# ---------------------------------------------------------------- 판정 k회

def judge_packet(group: dict, prompt: str, k: int, mode: str,
                 request_dir: Path, cfg: dict | None = None,
                 run_offset: int = 0) -> list[dict]:
    """패킷 하나를 k회 판정한다.

    호출은 weave_llm이 한다 — **judge.py의 judge()를 타지 않는다.**
    judge.py는 프롬프트 구성(build_judge_prompt)만 담당하고, "누가 판정하나"는
    이 함수와 project.json의 llm 설정이 정한다.

    manual 모드는 프롬프트가 회차마다 동일하므로 **파일을 한 번만 저장**하고
    k개 회차 자리만 비워 둔다 (같은 파일을 k번 쓰는 것은 낭비다).
    """
    llm = weave_llm.llm_of(cfg or {})
    model = llm.get("judge_model") or llm.get("model")
    runs = []
    manual_res = None
    for i in range(1, k + 1):
        if mode == "manual" or llm["provider"] == "manual":
            if manual_res is None:
                manual_res = judge(group["key"], prompt, mode="manual",
                                   request_dir=request_dir)
            res = manual_res
            runs.append({
                "run_index": i, "status": res.get("status"),
                "verdict": None, "reason": None, "evidence_event_ids": [],
                "confidence": None,
                "judged_by": f"manual ({model})",
                "judged_at": now(), "batch_id": f"{group['key']}-r{i}",
                "prompt_path": res.get("path"), "message": res.get("message"),
            })
            continue

        r = weave_llm.complete_json(None, prompt, llm, role="judge")
        d = r.get("data") or {}
        runs.append({
            "run_index": i,
            "status": "ok" if r["status"] == "ok" else "failed",
            "verdict": d.get("verdict"),
            "reason": d.get("reason"),
            "evidence_event_ids": d.get("evidence_event_ids", []) or [],
            "confidence": d.get("confidence"),
            "judged_by": f"{llm['provider']} / {model}",
            "judged_at": now(),
            "batch_id": f"{group['key']}-r{i}",
            "retried": r.get("retried", False),
            "parse_retried": r.get("parse_retried", False),
            "attempts": r.get("attempts"),
            "elapsed_sec": r.get("elapsed_sec"),
            "message": r.get("message"),
            "raw": None if r["status"] == "ok" else (r.get("raw") or "")[:2000],
        })
    return runs


def aggregate_runs(runs: list[dict], majority: int) -> dict:
    done = [r for r in runs if r.get("verdict")]
    if not done:
        return {"k": len(runs), "answered": 0, "detection_rate": None,
                "majority_positive": None, "verdict": None, "stable": None,
                "confidence_max": None, "evidence_union": []}
    pos = [r["verdict"] in POSITIVE_VERDICTS for r in done]
    rate = sum(pos) / len(done)
    positives = [r for r in done if r["verdict"] in POSITIVE_VERDICTS]
    rep = max(positives, key=lambda r: r.get("confidence") or 0) if positives else done[0]
    ev = sorted({e for r in done for e in (r.get("evidence_event_ids") or [])})
    return {
        "k": len(runs), "answered": len(done),
        "detection_rate": round(rate, 3),
        "majority_positive": sum(pos) >= majority,
        "verdict": rep["verdict"],
        "stable": rate in (0.0, 1.0),
        "confidence_max": max((r.get("confidence") or 0) for r in done),
        "evidence_union": ev,
        "verdicts": [r["verdict"] for r in done],
    }


# ---------------------------------------------------------------- 근거 역추적

def trace_evidence(ledger: Ledger, corpus: list, finding: dict) -> dict:
    """since 사건 ID → 그 사건의 요약·원문 발췌. UI가 근거 원문을 보여주기 위한 것."""
    by_id = {e.get("event_id"): e for e in corpus}
    out = {}
    for eid in filter(None, [finding.get("since")] + list(finding.get("evidence", []) or [])):
        ev = by_id.get(eid)
        if ev:
            out[eid] = {"title": ev.get("title"), "summary": ev.get("summary"),
                        "source_text": ev.get("source_text"),
                        "episode": ev.get("episode")}
    return out


# ---------------------------------------------------------------- 메인

def run_checks(new_events: list[dict], ledger: Ledger, mapping: dict, corpus: list,
               cfg: dict, request_dir: Path, judge_mode: str = "manual",
               registry: dict | None = None) -> dict:
    """신규 화 사건들을 검사해 검토 큐 항목을 만든다. 원장은 건드리지 않는다."""
    jc = cfg.get("judge") or {}
    k = int(jc.get("k", 3))
    unit = jc.get("unit", "scene")
    majority = int(jc.get("majority", 2))
    retrieval = jc.get("retrieval", "ers")

    new_events = [normalize(e, mapping) for e in new_events]

    # 1) checker — 사건별 구조·전제 검사
    findings_by_event, checker_items = {}, []
    for e in new_events:
        fs = check_event(e, ledger, mapping)
        findings_by_event[e.get("event_id")] = fs
        for f in fs:
            if f.get("severity") == SEV_INFO and not f.get("needs_judge"):
                continue                     # 순수 참고는 큐에 올리지 않는다
            checker_items.append({
                "kind": "checker",
                "event_id": e.get("event_id"),
                "scene_id": e.get("scene_id"),
                "severity": f.get("severity"),
                "type": f.get("type"),
                "entity": f.get("entity"),
                "field": f.get("field"),
                "premise": f.get("premise"),
                "ledger_value": f.get("ledger_value"),
                "since": f.get("since"),
                "detail": f.get("detail"),
                "needs_judge": bool(f.get("needs_judge")),
                "evidence_trace": trace_evidence(ledger, corpus, f),
            })

    # 2) judge — 패킷 단위 k회
    units = ["event", "scene"] if unit == "both" else [unit]
    judge_items = []
    for u in units:
        for g in scene_groups(new_events, u):
            if unit == "both" and u == "scene" and len(g["events"]) == 1:
                continue                     # event 단위와 중복
            prompt, related = build_packet_prompt(
                g, ledger, mapping, corpus, findings_by_event, retrieval,
                registry=registry)
            runs = judge_packet(g, prompt, k, judge_mode, request_dir, cfg=cfg)
            agg = aggregate_runs(runs, majority)
            judge_items.append({
                "kind": "judge",
                "packet_key": g["key"],
                "unit": g["unit"],
                "event_ids": [e.get("event_id") for e in g["events"]],
                "scene_id": g["events"][0].get("scene_id"),
                "retrieval": retrieval,
                "related_event_ids": [r["event_id"] for r in related],
                "runs": runs,
                "aggregate": agg,
                "evidence_trace": trace_evidence(
                    ledger, corpus, {"evidence": agg.get("evidence_union", [])}),
            })

    return {"checker": checker_items, "judge": judge_items,
            "config": {"k": k, "unit": unit, "majority": majority,
                       "retrieval": retrieval, "judge_mode": judge_mode}}


# ---------------------------------------------------------------- 심각도 통합

def item_severity(item: dict) -> str:
    """검토 큐 표시용 통합 심각도 — UI 필터가 쓴다."""
    if item["kind"] == "checker":
        return item.get("severity") or SEV_INFO
    agg = item.get("aggregate") or {}
    if agg.get("detection_rate") is None:
        return SEV_INFO
    if agg.get("majority_positive"):
        return SEV_HARD if agg["detection_rate"] == 1.0 else SEV_SOFT
    return SEV_SOFT if agg["detection_rate"] > 0 else SEV_INFO


def build_review_queue(project: str, episode: int, doc: dict, checks: dict,
                       registry_pending: list, replay_warnings: list) -> dict:
    items = []
    for it in checks["checker"] + checks["judge"]:
        items.append(it | {"severity_label": item_severity(it),
                           "status": "pending", "decision": None})
    items.sort(key=lambda x: (SEVERITY_ORDER.get(x["severity_label"], 9),
                              str(x.get("event_id") or x.get("packet_key"))))
    return {
        "project": project,
        "episode": episode,
        "created_at": now(),
        "config": checks["config"],
        "counts": {
            "total": len(items),
            SEV_HARD: sum(1 for i in items if i["severity_label"] == SEV_HARD),
            SEV_SOFT: sum(1 for i in items if i["severity_label"] == SEV_SOFT),
            SEV_INFO: sum(1 for i in items if i["severity_label"] == SEV_INFO),
            "registry_pending": len(registry_pending),
            "replay_warnings": len(replay_warnings),
        },
        "registry_pending": registry_pending,
        "replay_warnings": replay_warnings,
        "events": doc.get("events", []),
        "items": items,
    }
