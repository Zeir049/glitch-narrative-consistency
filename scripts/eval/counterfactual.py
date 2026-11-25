# -*- coding: utf-8 -*-
"""갈래 B — 한국어 반사실 회차 (FlawedFictionsMaker식). 앞 회차 사실 하나를 부정하는 다음 회차 장면을 LLM으로 쓴다.

근거: docs/learned-judge-prior-work.md §2-1 표 갈래 B(FlawedFictions 2504.11900 — 1막 명제 하나를 부정해 뒷부분을
다시 쓰고 1막은 원본 유지, 자기일관성 필터 → 사람 검증).

절차 (사례 하나 = 사실 하나 → 모순 장면 1 + 대조 정상 장면 1)
  1) 사실 후보  1..k화 확정 원장(datagen_common.load_episodes, timelines main·knows accumulate)에서
                상태(alive·body_status·condition·possesses) · 지식(knowledge, retained) · 규칙(worldview_rules).
                점수 = 그 사실이 생긴 뒤(1..k화 안) 같은 엔티티가 다시 나온 사건 수(규칙은 규칙 내용어 2개 이상을
                공유하는 뒤 사건 수). 결정적. location·일시적(transient) 상태·점수 < --min-refs 는 뺀다.
                '중간 중요도' = 점수 내림차순 순위의 --band(기본 0.3~0.7) 구간. 그 안에서 --pick 번째(없으면 --seed).
  2) 생성      LLM 한 번: (k+1)화 장면 1~3개(원문 문체 — k화 끝부분 발췌를 보여 준다)에서 그 사실 하나만 어기고,
                나머지 보존 사실(상위 점수 사실 목록)은 지킨다. 같은 호출에서 **대조 정상판**(위반 문장만 사실에 맞게
                고친 같은 장면)을 함께 받는다 → NoCha식 쌍 정확도 평가. 앞 회차 원문은 바꾸지 않는다.
  3) 자기일관성 같은 LLM 이 '번호 붙은 사실 목록(대상 + 보존 사실, 시드 섞기) + 장면'을 보고 어긴 사실 번호를 답한다.
                모순 장면 --sc-runs(5)회 중 대상 번호를 맞힌 횟수 ≥ --sc-min(4) 이고,
                대조 장면에서 대상 번호를 모순으로 짚은 횟수 ≤ --control-max(1) 이면 통과.
  4) 저장      <out>/<work>/scenes/scene_NN.txt · labels.json(WM labels 형식: seeded_text·violated_fact·
                evidence_event_ids·type + contrast_group) · manifest.json(사례별 사실·자기일관성·human_verified: null)
                · raw/<case_id>.json(생성·검출 원응답). 이미 있으면 장면 번호를 이어서 붙인다.
                통과 못 한 사례도 저장한다(manifest passed=false) — 사람 검증 큐는 passed=true 만 보면 된다.

녹화: WEAVE_LLM_RECORD=record|replay. role 은 생성 'cf_generate', 검출 'cf_detect'. 같은 검출 프롬프트 5회는
녹화 순서대로 쌓이고 재생된다. 검출 다양성을 위해 검출 온도는 --sc-temperature(기본 0.7).

실행
  PYTHONIOENCODING=utf-8 python scripts/eval/counterfactual.py --work q1 --k 4 --dry-run
  WEAVE_LLM_RECORD=record WEAVE_LLM_RECORD_DIR=build/llm_record \\
    PYTHONIOENCODING=utf-8 python scripts/eval/counterfactual.py --work q1 --k 4 --out build/counterfactual
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import random
import re
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
if str(EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(EVAL_DIR))

import datagen_common as C  # noqa: E402

from app.pipeline.pairs import _entities_of, _names  # noqa: E402 - 읽기 전용 보조 함수

logger = logging.getLogger("counterfactual")

STATE_FIELDS = ("alive", "body_status", "condition", "possesses")
TYPE_OF = {"state": "상태", "knowledge": "지식", "rule": "설정"}
GEN_ROLE, DET_ROLE = "cf_generate", "cf_detect"
INTENDED_USE = "train_only"  # 누수 규칙: 접기의 학습 작품에서만 만들고 학습에만 쓴다
STYLE_CHARS = 2500
N_PRESERVED = 14
DET_MAX_TOKENS = 700
STOP = set("그리고 그러나 하지만 있다 없다 한다 된다 것이 것은 수가 때문 그것 이것 저것 모든 어떤 사람 경우 때는 위해 대한".split())


@dataclass(frozen=True)
class Fact:
    fact_id: str
    kind: str              # state | knowledge | rule
    entity: str | None
    field: str | None
    text: str              # 사람이 읽는 한 줄
    since: str | None      # 근거 사건 ID
    since_episode: int | None
    quote: str
    score: int


# ---------------------------------------------------------------- 1) 사실 후보

def _content_words(text: str) -> set[str]:
    """내용어 근사: 2자 이상 낱말의 앞 2자(조사·어미를 떼는 대신). 결정적."""
    return {w[:2] for w in re.findall(r"[가-힣A-Za-z0-9]{2,}", text or "") if w not in STOP and w[:2] not in STOP}


def fact_candidates(episodes: list[dict], registry: dict, k: int, *, knows_mode: str = "accumulate") -> list[Fact]:
    """1..k화 원장의 사실 후보(점수 포함). 같은 입력 → 같은 목록(점수 내림차순, fact_id)."""
    _w, _p, compile_ledger, _n = C.JE._core()
    eps = [e for e in episodes if int(e.get("episode") or 0) <= k]
    ledger = compile_ledger(episodes=eps, timelines=["main"], knows_mode=knows_mode)
    corpus = C.build_corpus(eps)
    by_id = {e.get("event_id"): e for e in corpus}
    names = _names(registry)
    ents_of = [set(_entities_of(e)) for e in corpus]
    words_of = [_content_words(f"{e.get('title', '')} {e.get('summary', '')} {e.get('source_text', '')}")
                for e in corpus]
    text_of = [f"{e.get('summary', '')} {e.get('source_text', '')}" for e in corpus]

    def refs_after(ent: str | None, since: str | None, words: set[str] | None = None, min_overlap: int = 2,
                   name: str | None = None) -> int:
        """그 사실이 생긴 뒤 다시 '참조'된 사건 수. 참조 = (엔티티가 나옴 또는 이름이 본문에 있음)
        그리고 (words 를 주면) 사실 내용어를 min_overlap 개 이상 공유."""
        start = by_id[since]["global_idx"] if since in by_id else -1
        n = 0
        for i in range(start + 1, len(corpus)):
            if ent is not None and ent not in ents_of[i] and not (name and name in text_of[i]):
                continue
            if words is not None and len(words & words_of[i]) < min_overlap:
                continue
            n += 1
        return n

    def ep_of(since: str | None) -> int | None:
        return by_id[since]["episode"] if since in by_id else None

    def quote(since: str | None) -> str:
        return str((by_id.get(since) or {}).get("source_text") or "").strip()

    out: list[Fact] = []
    for ent, fields in sorted((ledger.state.get("main") or {}).items()):
        name = names.get(ent, ent)
        for f in STATE_FIELDS:
            prov = fields.get(f)
            if not isinstance(prov, dict):
                continue
            if f == "possesses" and "value" not in prov:
                for oid, p in sorted(prov.items()):
                    if p.get("status") == "removed":
                        continue
                    s = p.get("since")
                    out.append(Fact(f"state:{ent}.possesses.{oid}", "state", ent, f,
                                    f"{name}은(는) {names.get(oid, oid)}을(를) 지니고 있다", s, ep_of(s), quote(s),
                                    refs_after(oid, s, name=names.get(oid))))
                continue
            if prov.get("volatility") == "transient" or str(prov.get("value") or "").lower() in ("", "unknown", "none"):
                continue
            s = prov.get("since")
            val = prov.get("value")
            text = (f"{name}은(는) {'살아 있다' if str(val).lower() == 'true' else '죽었다'}" if f == "alive"
                    else f"{name}의 {f} = {val}")
            score = (refs_after(ent, s, name=name) if f == "alive"
                     else refs_after(ent, s, _content_words(str(val)), 1 if len(_content_words(str(val))) < 3 else 2))
            out.append(Fact(f"state:{ent}.{f}", "state", ent, f, text, s, ep_of(s), quote(s), score))
    for ch, items in sorted((ledger.knowledge or {}).items()):
        for i, kn in enumerate(items):
            if not kn.get("retained", True) or not str(kn.get("info") or "").strip():
                continue
            s = kn.get("since")
            out.append(Fact(f"knowledge:{ch}:{i:03d}", "knowledge", ch, "knows",
                            f"{names.get(ch, ch)}은(는) 다음을 안다: {kn['info']} (경로 {kn.get('via')})",
                            s, ep_of(s), quote(s), refs_after(ch, s, _content_words(kn["info"]), 2)))
    rules = C._rules_before(registry, episodes, k + 1)
    for r in rules:
        words = _content_words(r.get("content", ""))
        first = int(r.get("first_episode") or 1)
        n = sum(1 for i, e in enumerate(corpus) if int(e.get("episode") or 0) > first and len(words & words_of[i]) >= 2)
        rid = r.get("id") or r.get("rule_id")
        out.append(Fact(f"rule:{rid}", "rule", None, None, f"세계관 규칙: {r.get('content', '')}", None, first,
                        str(r.get("content") or ""), n))
    return sorted(out, key=lambda f: (-f.score, f.fact_id))


def middle_band(facts: list[Fact], band: tuple[float, float], min_refs: int) -> list[Fact]:
    ranked = [f for f in facts if f.score >= min_refs]
    lo, hi = int(len(ranked) * band[0]), max(int(len(ranked) * band[1]), int(len(ranked) * band[0]) + 1)
    return ranked[lo:hi]


def pick_fact(facts: list[Fact], *, band: tuple[float, float] = (0.3, 0.7), min_refs: int = 2,
              pick: int | None = None, seed: int = 20261106, kinds: tuple[str, ...] = ("state", "knowledge", "rule"),
              exclude: set[str] | None = None) -> Fact | None:
    """pick 을 주면 (kind 섞은) 중간 구간의 pick 번째. 없으면 kind 를 seed 로 돌려 고르고(지식이 대부분이라
    한 kind 로 쏠리지 않게), 그 kind 의 중간 구간에서 seed 로 하나."""
    ex = exclude or set()
    if pick is not None:
        pool = [f for f in middle_band([f for f in facts if f.kind in kinds], band, min_refs) if f.fact_id not in ex]
        return pool[pick % len(pool)] if pool else None
    bands = {kd: [f for f in middle_band([f for f in facts if f.kind == kd], band, min_refs) if f.fact_id not in ex]
             for kd in kinds}
    present = [kd for kd in kinds if bands[kd]]
    if not present:
        return None
    return random.Random(seed).choice(bands[present[seed % len(present)]])


def preserved_facts(facts: list[Fact], target: Fact, n: int = N_PRESERVED) -> list[Fact]:
    """보존해야 할 사실 — 대상 외 상위 점수, 대상과 같은 (엔티티, 필드)는 뺀다(혼동 방지)."""
    out = [f for f in facts if f.fact_id != target.fact_id
           and not (f.entity == target.entity and f.field == target.field and f.entity is not None)]
    return out[:n]


# ---------------------------------------------------------------- 2) 생성

GEN_SYSTEM = """당신은 한국어 장편 소설의 문체를 그대로 이어 쓰는 작가입니다. 연구용 '설정 모순 검출' 데이터를 만듭니다.
요청받은 회차의 장면을 쓰되, 지정된 '대상 사실' 하나만 자연스럽게 어기고 나머지 설정은 모두 지킵니다.

규칙
- 앞 회차 원문은 바꾸지 않습니다. 당신은 그다음 회차의 장면 1~3개(합쳐 1,200~2,500자)만 씁니다.
- 원문 발췌의 문체·시점·호칭·말투를 따릅니다.
- 대상 사실을 어기는 대목은 한두 문장으로, 서술이나 인물이 그것을 사실처럼 다룹니다. 인물이 놀라거나
  "이상하다"고 짚거나, 회복·변심·착각·거짓말 같은 설명을 붙이지 않습니다(작가가 설정을 잊은 것처럼).
- 그 밖의 '보존 사실'은 하나도 어기지 않습니다. 새 설정을 크게 늘리지 않습니다.
- 대조판: 같은 장면을 그대로 두고, 위반 대목만 대상 사실에 맞게 고친 판도 함께 냅니다. 나머지 글자는 같아야 합니다.

출력: 아래 JSON 객체 하나만(코드펜스 없이).
{"contradiction": {"text": "모순판 장면 전체", "seeded_text": "모순판에서 대상 사실을 어긴 문장(원문 그대로 발췌)", "violation": "무엇을 어떻게 어겼는지 한 문장"},
 "control": {"text": "대조판 장면 전체", "kept_text": "대조판에서 그 자리에 들어간 문장(원문 그대로 발췌)"}}"""


def gen_prompt(work: str, k: int, target: Fact, preserved: list[Fact], cast: list[str], style: str) -> str:
    lines = [f"작품: {work}. 앞 회차: 1~{k}화(확정). 쓸 회차: {k + 1}화.",
             "", "[등장인물]", *[f"- {c}" for c in cast],
             "", "[보존 사실 — 어기지 말 것]", *[f"- {f.text}" for f in preserved],
             "", "[대상 사실 — 이것 하나만 어길 것]", f"- {target.text}"]
    if target.quote:
        lines += [f"  (근거 원문{f', {target.since_episode}화' if target.since_episode else ''}: {target.quote[:400]})"]
    lines += ["", f"[{k}화 끝부분 원문 — 문체 참고]", style, "",
              f"{k + 1}화 장면을 규칙대로 쓰고 JSON 하나로 답하세요."]
    return "\n".join(lines)


def validate_generation(data: dict | None) -> tuple[bool, str]:
    if not isinstance(data, dict):
        return False, "JSON 없음"
    c, n = data.get("contradiction") or {}, data.get("control") or {}
    ct, nt = str(c.get("text") or ""), str(n.get("text") or "")
    seeded, kept = str(c.get("seeded_text") or "").strip(), str(n.get("kept_text") or "").strip()
    if not ct or not nt:
        return False, "장면 본문 없음"
    if not seeded or seeded not in ct:
        return False, "seeded_text 가 모순판 본문에 없음"
    if seeded in nt:
        return False, "대조판에 위반 문장이 그대로 있음"
    if kept and kept not in nt:
        return False, "kept_text 가 대조판 본문에 없음"
    return True, ""


# ---------------------------------------------------------------- 3) 자기일관성

DET_SYSTEM = """당신은 장편 서사의 설정 모순 검수자입니다. 앞 회차까지 확정된 사실 목록과 새 회차 장면을 받습니다.
장면이 목록의 사실 가운데 하나라도 어기는지 판정합니다. 회복·시간 경과·장면 전환·거짓말·모른 척처럼
이야기 안에서 설명되는 차이는 모순이 아닙니다. 사실이 참인 채로는 장면 서술이 성립할 수 없을 때만 모순입니다.
출력: JSON 하나만. {"has_contradiction": true|false, "fact_no": 어긴 사실 번호(없으면 null), "quote": "장면에서 어긴 대목 발췌", "reason": "한 문장"}"""


def det_facts(target: Fact, preserved: list[Fact], seed: int) -> tuple[list[Fact], int]:
    """(번호 매길 사실 목록, 대상의 1-기반 번호). 시드로 섞어 대상 위치를 고정하지 않는다."""
    items = [target] + list(preserved)
    random.Random(seed).shuffle(items)
    return items, items.index(target) + 1


def det_prompt(facts: list[Fact], scene: str) -> str:
    return "\n".join(["[확정 사실]", *[f"{i}. {f.text}" for i, f in enumerate(facts, 1)],
                      "", "[새 회차 장면]", scene, "", "JSON 하나로 답하세요."])


def _as_bool(v) -> bool | None:  # noqa: ANN001
    if isinstance(v, bool):
        return v
    if isinstance(v, str) and v.strip().lower() in ("true", "false"):
        return v.strip().lower() == "true"
    return None


def _as_int(v) -> int | None:  # noqa: ANN001
    try:
        return int(v) if v is not None and str(v).strip() not in ("", "null") else None
    except (TypeError, ValueError):
        return None


def judge_runs(results: list[dict], target_no: int) -> dict:
    """검출 결과들 → {"runs", "ok", "flagged", "target_hits"}. flagged = 모순이라 답한 횟수(번호 무관)."""
    ok = flagged = hits = 0
    for r in results:
        d = r.get("data") if r.get("status") == "ok" else None
        if not isinstance(d, dict):
            continue
        has = _as_bool(d.get("has_contradiction"))
        if has is None:
            continue
        ok += 1
        if has:
            flagged += 1
            if _as_int(d.get("fact_no")) == target_no:
                hits += 1
    return {"runs": len(results), "ok": ok, "flagged": flagged, "target_hits": hits}


def self_consistency(contra: dict, control: dict, *, sc_min: int = 4, control_max: int = 1) -> dict:
    """통과 규칙: 모순판 target_hits ≥ sc_min, 대조판 target_hits ≤ control_max(형식 오류·실패 회차는 hits 0)."""
    c_pass = contra["target_hits"] >= sc_min
    n_pass = control["ok"] >= sc_min and control["target_hits"] <= control_max
    return {"contradiction": contra | {"pass": c_pass}, "control": control | {"pass": n_pass},
            "rule": f"contradiction target_hits>={sc_min}, control ok>={sc_min} and target_hits<={control_max}",
            "passed": c_pass and n_pass}


# ---------------------------------------------------------------- 4) 저장

def _next_scene_no(labels: list[dict]) -> int:
    nums = [int(m.group(1)) for lab in labels if (m := re.match(r"scene_(\d+)$", lab.get("scene_id", "")))]
    return max(nums, default=0) + 1


def save_case(out_dir: Path, case: dict, gen: dict, raw: dict) -> dict:
    """장면 2개 + labels.json + manifest.json 갱신. 반환: manifest 항목."""
    (out_dir / "scenes").mkdir(parents=True, exist_ok=True)
    (out_dir / "raw").mkdir(parents=True, exist_ok=True)
    lpath, mpath = out_dir / "labels.json", out_dir / "manifest.json"
    labels_doc = json.loads(lpath.read_text(encoding="utf-8")) if lpath.exists() else {
        "description": ("반사실 회차(갈래 B) — LLM 생성, 사람 검증 전(manifest human_verified). WM labels 형식. "
                        "학습 전용(intended_use=train_only): 이 작품이 평가 작품인 접기에서는 학습에서도 뺀다."),
        "labels": []}
    manifest = json.loads(mpath.read_text(encoding="utf-8")) if mpath.exists() else {"cases": []}
    n = _next_scene_no(labels_doc["labels"])
    sid_c, sid_n = f"scene_{n:02d}", f"scene_{n + 1:02d}"
    fact = case["fact"]
    c, ctl = gen["contradiction"], gen["control"]
    (out_dir / "scenes" / f"{sid_c}.txt").write_text(c["text"], encoding="utf-8")
    (out_dir / "scenes" / f"{sid_n}.txt").write_text(ctl["text"], encoding="utf-8")
    author = f"llm:{case['model']} counterfactual"
    labels_doc["labels"] += [
        {"scene_id": sid_c, "has_contradiction": True, "type": TYPE_OF[fact["kind"]], "difficulty": "medium",
         "seeded_text": c.get("seeded_text", ""),
         "violated_fact": f"{fact['text']}" + (f" (근거 {fact['since_episode']}화 {fact['since']})" if fact["since"] else ""),
         "evidence_event_ids": [fact["since"]] if fact["since"] else [],
         "evidence_rule_ids": [fact["fact_id"].split(":", 1)[1]] if fact["kind"] == "rule" else [],
         "expected_layer": "judge", "authored_by": author, "episode": case["target_episode"],
         "contrast_group": case["case_id"], "intended_use": INTENDED_USE, "work": case["work"],
         "notes": c.get("violation", "")},
        {"scene_id": sid_n, "has_contradiction": False, "type": "없음", "difficulty": "medium",
         "seeded_text": "", "violated_fact": "", "evidence_event_ids": [], "expected_layer": "none",
         "authored_by": author, "episode": case["target_episode"], "contrast_group": case["case_id"],
         "intended_use": INTENDED_USE, "work": case["work"], "rewritten": True,
         "notes": f"대조판 — 대상 사실을 지키는 문장: {ctl.get('kept_text', '')}"}]
    entry = case | {"intended_use": INTENDED_USE, "scenes": {"contradiction": sid_c, "control": sid_n}, "human_verified": None, "human_notes": ""}
    manifest["cases"].append(entry)
    lpath.write_text(json.dumps(labels_doc, ensure_ascii=False, indent=2), encoding="utf-8")
    mpath.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "raw" / f"{case['case_id']}.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2),
                                                            encoding="utf-8")
    return entry


# ---------------------------------------------------------------- 실행

def cast_lines(registry: dict, episodes: list[dict], k: int, n: int = 12) -> list[str]:
    from collections import Counter
    cnt: Counter = Counter()
    for ep in episodes:
        if int(ep.get("episode") or 0) <= k:
            for e in ep.get("events") or []:
                cnt.update(i for i in _entities_of(e) if str(i).startswith("CHAR_"))
    by_id = {c.get("id"): c for c in registry.get("characters") or []}
    out = []
    for cid, _ in sorted(cnt.items(), key=lambda x: (-x[1], x[0]))[:n]:
        c = by_id.get(cid) or {}
        alias = ", ".join(c.get("aliases") or [])
        note = str(c.get("note") or "").strip()
        out.append(f"{c.get('name', cid)}" + (f" ({alias})" if alias else "") + (f" — {note[:80]}" if note else ""))
    return out


def run_case(work: str, k: int, *, sources: Path | None, model: str, out_dir: Path | None, seed: int,
             pick: int | None, band: tuple[float, float], min_refs: int, kinds: tuple[str, ...],
             sc_runs: int, sc_min: int, control_max: int, sc_temperature: float, dry_run: bool,
             caller=None, usage: C.Usage | None = None) -> dict:  # noqa: ANN001
    eps, reg, _mapping = C.load_episodes(work)
    facts = fact_candidates(eps, reg, k)
    done = set()
    if out_dir is not None and (out_dir / "manifest.json").exists():
        done = {c["fact"]["fact_id"] for c in json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))["cases"]}
    target = pick_fact(facts, band=band, min_refs=min_refs, pick=pick, seed=seed, kinds=kinds, exclude=done)
    if target is None:
        raise SystemExit("중간 중요도 구간에 남은 사실이 없습니다(--band/--min-refs/--kinds 확인)")
    preserved = preserved_facts(facts, target)
    style = C.load_source(work, k, sources)[-STYLE_CHARS:]
    prompt = gen_prompt(work, k, target, preserved, cast_lines(reg, eps, k), style)
    dfacts, target_no = det_facts(target, preserved, seed)
    case_id = f"cf_{work}_ep{k + 1}_" + hashlib.sha1(target.fact_id.encode("utf-8")).hexdigest()[:8]
    info = {"case_id": case_id, "work": work, "base_episode": k, "target_episode": k + 1, "model": model,
            "fact": asdict(target), "preserved_fact_ids": [f.fact_id for f in preserved],
            "detector_target_no": target_no, "candidates": len(facts),
            "band": list(band), "min_refs": min_refs, "seed": seed}
    if dry_run:
        n_in = len(GEN_SYSTEM) + len(prompt)
        det_in = (len(DET_SYSTEM) + len(det_prompt(dfacts, "x" * 2000))) * sc_runs * 2
        return info | {"gen_prompt_chars": n_in, "det_prompt_chars_total": det_in,
                       "band_facts": [asdict(f) | {"quote": f.quote[:60]}
                                      for f in middle_band([f for f in facts if f.kind in kinds], band, min_refs)][:20]}

    call = caller or (lambda s, u, llm, role, occ=0: C.call_json(s, u, llm, role=role, usage=usage, occurrence=occ))
    gen_llm = C.llm_for(model)
    det_llm = C.llm_for(model, max_tokens=DET_MAX_TOKENS, temperature=sc_temperature)
    t0 = time.time()
    g = call(GEN_SYSTEM, prompt, gen_llm, GEN_ROLE)
    ok, why = validate_generation(g.get("data") if g.get("status") == "ok" else None)
    raw = {"generation": {"status": g.get("status"), "raw": g.get("raw"), "message": g.get("message")},
           "detect": {}}
    if not ok:
        logger.warning("생성 실패/형식 오류: %s", why or g.get("message"))
        if out_dir is not None:  # 실패 원응답도 남긴다(프롬프트 조정용)
            (out_dir / "raw").mkdir(parents=True, exist_ok=True)
            (out_dir / "raw" / f"{case_id}.invalid.json").write_text(
                json.dumps(info | {"reason": why, "raw": raw}, ensure_ascii=False, indent=2), encoding="utf-8")
        return info | {"generation_status": "invalid", "reason": why or g.get("message")}
    data = g["data"]
    res = {}
    for name, scene in (("contradiction", data["contradiction"]["text"]), ("control", data["control"]["text"])):
        runs = [call(DET_SYSTEM, det_prompt(dfacts, scene), det_llm, DET_ROLE, i) for i in range(sc_runs)]
        raw["detect"][name] = [{"status": r.get("status"), "data": r.get("data")} for r in runs]
        res[name] = judge_runs(runs, target_no)
    sc = self_consistency(res["contradiction"], res["control"], sc_min=sc_min, control_max=control_max)
    case = info | {"generation_status": "ok", "self_consistency": sc, "passed": sc["passed"],
                   "elapsed_sec": round(time.time() - t0, 1)}
    if out_dir is not None:
        return save_case(out_dir, case, data, raw)
    return case


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--work", required=True, choices=("wm", "q1", "q3", "sherlock"))
    ap.add_argument("--k", type=int, default=4, help="앞 회차 마지막(사실 출처 1..k). 생성 회차는 k+1")
    ap.add_argument("--sources", type=Path, help="회차 원문 폴더(episode_NN.txt) 또는 WM 한 파일")
    ap.add_argument("--model", default="", help="비우면 설정 judge 모델")
    ap.add_argument("--out", type=Path, help="출력 루트(그 아래 <work>/). dry-run 이 아니면 필수")
    ap.add_argument("--n", type=int, default=1, help="만들 사례 수(사실마다 1건)")
    ap.add_argument("--pick", type=int, help="중간 구간 안 사실 순번(없으면 시드)")
    ap.add_argument("--seed", type=int, default=20261106)
    ap.add_argument("--band", default="0.3,0.7")
    ap.add_argument("--min-refs", type=int, default=2)
    ap.add_argument("--kinds", default="state,knowledge,rule")
    ap.add_argument("--sc-runs", type=int, default=5)
    ap.add_argument("--sc-min", type=int, default=4)
    ap.add_argument("--control-max", type=int, default=1)
    ap.add_argument("--sc-temperature", type=float, default=0.7)
    ap.add_argument("--price-in", type=float)
    ap.add_argument("--price-out", type=float)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    model = a.model or C.default_model()
    band = tuple(float(x) for x in a.band.split(","))
    kinds = tuple(x.strip() for x in a.kinds.split(",") if x.strip())
    out_dir = (a.out / a.work) if a.out else None
    if not a.dry_run and out_dir is None:
        raise SystemExit("--out 이 필요합니다(또는 --dry-run)")
    usage = C.Usage()
    results = []
    for i in range(a.n):
        r = run_case(a.work, a.k, sources=a.sources, model=model, out_dir=out_dir, seed=a.seed + i,
                     pick=(a.pick + i) if a.pick is not None else None, band=band, min_refs=a.min_refs,
                     kinds=kinds, sc_runs=a.sc_runs, sc_min=a.sc_min, control_max=a.control_max,
                     sc_temperature=a.sc_temperature, dry_run=a.dry_run, usage=usage)
        results.append(r)
    u = usage.to_dict((a.price_in, a.price_out))
    summary = {"cases": [{k: r.get(k) for k in ("case_id", "fact", "generation_status", "passed", "scenes",
                                                "self_consistency", "reason")} for r in results],
               "usage": u}
    if a.dry_run:
        summary = {"dry_run": results}
    elif out_dir is not None:
        (out_dir / "last_run_usage.json").write_text(json.dumps(u, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
