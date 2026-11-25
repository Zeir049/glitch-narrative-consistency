# -*- coding: utf-8 -*-
"""Claude 가 쓴 학습 쌍 도구 (실험 L — L1 WM 반사실 · L2 외부 정리).

설계: docs/learned-judge-training-design.md §0-7(c). 생성은 Claude Code 세션의 서브에이전트가 직접 쓴다(유료 API 호출 없음).
이 파일은 **형식 도구**다 — 에이전트가 낸 구조 JSON 을 학습 JSONL 로 바꾸고(렌더링), 검증하고, 에이전트에 넘길 작업 파일을 만든다.

렌더링은 서비스와 같은 함수다. 형식을 손으로 흉내 내지 않는다:
  가설·전제 문자열은 전부 `app.pipeline.pairs.build_pairs`(PAIRS_VERSION 2)가 낸 `Pair.hypothesis_text/premise_text` 이다.
  합성 사건(가설)과 원장·코퍼스·레지스트리를 만들어 build_pairs 에 넣고, 그 출력에서 원하는 (가설, 전제) 한 쌍을 고른다.
  · L1  원장·코퍼스·세계관 규칙은 WM 의 1..k-1화 재생(datagen_common.replay_upto 와 같은 시점 규칙 — 미래 규칙·사건 제외).
        전제는 에이전트가 쓰지 않는다 — 카탈로그(build_pairs 출력)에서 `premise_ref` 로 고른 문장을 그대로 복사한다.
  · L2  쌍마다 작은 합성 이야기 상태(전제 사건 1개 → 과거 사건 또는 앎)를 만들어 같은 함수로 렌더링.

행 스키마(JSONL 한 줄 = 한 쌍; schema_version 1)
  id, source(l1_wm|l2_ffcd), label(모순|정상), group_id(장면·이야기 — 검증용 10% 를 이 단위로 뗀다), pair_of(짝 행 id), type(모순 유형),
  hyp_spec  {entity, entity_name, field|None, assumes, quote(근거 인용), summary(사건 요약), excerpt(원문 발췌), title, episode, participants}
  prem_spec L1 {premise_ref, kind, premise_id, ep}  /  L2 {kind, title, summary, quote, character, info, via, episode}
  premise_kind, premise_id, premise(=text_a), hypothesis(=text_b), text_a, text_b      ← 모델 입력은 tokenizer(premise, hypothesis)
  학습 호환 키: work, scene_id, case_id, contrast_group, source_kind='claude', split='train', keyed=True, pairs_version
  gen: {task, agent_note}  (생성 묶음 추적)

CLI (생성물은 저장소 밖 C:/judge_models/L/ 에 둔다):
  prep-l1   WM 장면 조각 + 전제 카탈로그 → 에이전트 작업 파일(work/l1/tasks/*.md)
  prep-l2   FlawedFictions·ContraDoc 원본(영어) → 항목 묶음 작업 파일(work/l2/tasks/*.md). 받은 파일은 데이터로만 읽는다
  build     work/<l1|l2>/out/*.json(에이전트 산출) → l1_wm.jsonl / l2_ffcd.jsonl (+ rejects)
  validate  스키마·짝·전제 id·중복·반복 n-gram·유형/종류 비율·재렌더링 일치
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import math
import random
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = Path(__file__).resolve().parent
for _p in (str(ROOT), str(EVAL_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app.pipeline.pairs import (  # noqa: E402 - 코어 경로(sys.path)도 이 import 가 연다
    PAIRS_VERSION, Pair, _entities_of, _relevance, build_pairs)

from ledger import ALLOWED_FIELDS, compile_ledger  # noqa: E402

SCHEMA_VERSION = 1
SOURCES = ("l1_wm", "l2_ffcd")
LABELS = ("모순", "정상")
# 유형 — 지시문의 여섯 가지 + 넘치는 쪽(소유·상태, 관계·행동). 에이전트는 '기타'를 쓰지 않는다.
TYPES = ("수치", "위치", "생사·신체", "앎", "규칙", "시간순서", "소유·상태", "관계·행동")
CORE_TYPES = TYPES[:6]
PREMISE_KINDS = ("ledger", "knowledge", "rule", "past")   # 지시문: 원장·앎·규칙·과거 사건
L2_KINDS = ("past", "knowledge")
FIELDS = tuple(sorted(ALLOWED_FIELDS))                      # alive location body_status possesses knows ability condition
WORK_DIR = Path("C:/judge_models/L")
EXT_DIR = Path("C:/judge_models/ext")
WM_TEXT = Path("%USERPROFILE%/Downloads/event_sourcing/WM_1-5.txt")   # 학습 작품 WM 의 원문(읽기 전용)
L1_EPISODES = (2, 3, 4, 5)         # 1화는 앞선 원장이 없다
L1_TIMELINE = "future_world"       # WM 장면 사건 다수의 원장 칸 (export_pairs.inject_timeline 과 같은 이유)
ALL_KINDS = {k: None for k in ("checker", "ledger", "knowledge", "rule", "past", "scene")}   # 할당량 없음 = 전부


TYPE_ALIAS = {"상태": "소유·상태", "소유": "소유·상태", "관계": "관계·행동", "행동": "관계·행동", "생사": "생사·신체",
              "신체": "생사·신체", "시간": "시간순서", "순서": "시간순서"}   # 에이전트가 줄여 쓴 유형 → 정식 이름


def canon_type(t):  # noqa: ANN001, ANN201
    if isinstance(t, str) and t not in TYPES:
        if t in TYPE_ALIAS:
            return TYPE_ALIAS[t]
        if t.split("·")[0] in TYPES:      # '수치·위치' 처럼 둘을 붙여 쓴 것 → 앞의 것
            return t.split("·")[0]
    return t


class ItemError(ValueError):
    """에이전트 산출 항목 하나를 버리는 사유(메시지 = 사유 코드: 설명)."""


# ================================================================ 문자열 보조

def norm(s: str | None) -> str:
    return re.sub(r"[\W_]+", "", s or "").lower()


def cut(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n]


_NEG = ("않", "못", "아니", "없", "안 ")


def _neg_count(t: str) -> int:
    return sum(t.count(n) for n in _NEG)


def negation_only(orig: str, new: str) -> bool:
    """orig → new 가 부정 표지를 붙이거나 뗀 것뿐인가(템플릿 지름길). 부정 표지 수가 다르고, 표지를 지운 나머지가 거의 같으면 True.
    (한국어 활용 때문에 형태소 분석 없이 문자열 유사도 0.72 로 가른다 — 있다↔없다 뒤집기도 여기 걸린다.)"""
    if _neg_count(orig) == _neg_count(new):
        return False
    strip = lambda t: re.sub(r"\s+", "", re.sub("|".join(map(re.escape, _NEG)), "", t))  # noqa: E731
    return difflib.SequenceMatcher(None, strip(orig), strip(new)).ratio() >= 0.72


def sha(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


# ================================================================ 렌더링 (build_pairs 호출)

def all_pairs(ledger, corpus: list[dict], registry: dict, ev: dict, render: str = "v2") -> list[Pair]:  # noqa: ANN001
    """합성 사건 하나를 build_pairs 에 넣는다. 전제 상한·할당량 없음(= 가설마다 쓸 수 있는 전제 전부)."""
    group = {"key": ev["event_id"], "events": [ev]}
    return build_pairs(group, ledger, corpus, registry, [], max_premises=10 ** 6, quotas=dict(ALL_KINDS), render=render)


def pick_pair(pairs: list[Pair], *, entity: str | None, field: str | None,
              kind: str, premise_id: str | None = None, premise_text: str | None = None,
              ) -> Pair:
    """가설 (entity, field) × 전제 (kind[, id, 본문]) 에 해당하는 쌍. 없으면 ItemError, 둘 이상이면 AssertionError."""
    hit = [p for p in pairs if p.entity == entity and p.field == field and p.premise_kind == kind
           and (premise_id is None or p.premise_id == premise_id)
           and (premise_text is None or p.premise_text == premise_text)]
    if not hit:
        raise ItemError(f"unreachable: 이 가설에서 그 전제(kind={kind})가 build_pairs 출력에 없다")
    if len({(p.premise_id, p.premise_text) for p in hit}) > 1:
        raise ItemError(f"ambiguous: 조건에 맞는 전제가 여럿이다({len(hit)})")
    return hit[0]


def hyp_event(spec: dict, *, event_id: str, episode: int, timeline: str) -> dict:
    """hyp_spec → build_pairs 가 읽는 합성 사건. field 가 None 이면 사건 가설(assumed_states 없음)."""
    ents = list(spec.get("participants") or [])
    ent = spec.get("entity")
    if ent and ent not in ents:
        ents.append(ent)
    ev = {"event_id": event_id, "episode": episode, "timeline": timeline, "title": spec["title"],
          "summary": spec["summary"], "source_text": spec["excerpt"], "participant_character_ids": ents,
          "assumed_states": []}
    if spec.get("field"):
        ev["assumed_states"] = [{"entity": ent, "field": spec["field"], "assumes": spec["assumes"],
                                 "source_text": spec["quote"]}]
    return ev


# ---------------------------------------------------------------- L1: WM 컨텍스트

class L1Context:
    """WM k화 시점(1..k-1화 원장·코퍼스, k 이전 규칙)의 재생. datagen_common.replay_upto 를 그대로 쓴다."""

    def __init__(self, k: int):
        import datagen_common as C   # judge_eval → app.pipeline.weave import 를 늦춘다(무거움)
        self.k = k
        ctx = C.replay_upto("wm", k)
        self.ledger, self.corpus, self.registry = ctx.ledger, ctx.corpus, ctx.registry
        self.names = {e["id"]: e["name"] for c in ("characters", "locations", "objects", "times")
                      for e in self.registry.get(c) or [] if e.get("id") and e.get("name")}
        self.chars = [c["id"] for c in self.registry.get("characters") or []]

    def event(self, spec: dict, event_id: str) -> dict:
        return hyp_event(spec, event_id=event_id, episode=self.k, timeline=L1_TIMELINE)

    def pairs(self, spec: dict, event_id: str, render: str = "v2") -> list[Pair]:
        return all_pairs(self.ledger, self.corpus, self.registry, self.event(spec, event_id), render=render)


def l1_catalog(ctx: L1Context) -> list[dict]:
    """k화 시점의 전제 카탈로그 — 전부 build_pairs 출력. {ref, kind, premise_id, text, entity, field, entities}.
    ledger: 그 (엔티티, 필드)에 대한 상태 가설의 출력 / knowledge: 인물 한 명만 참여시킨 사건의 출력 / rule·past: 모든 인물이 참여한 사건 가설.
    entity·field = 이 전제를 쓰려면 가설이 맞춰야 하는 값(ledger 는 둘 다, knowledge 는 인물, past 는 entities 중 하나), rule 은 없음."""
    led = ctx.ledger
    tl = L1_TIMELINE if L1_TIMELINE in led.state else next(iter(led.state))
    states = [(e, f) for e, fs in sorted(led.state.get(tl, {}).items()) for f in sorted(fs) if not f.startswith("_")]
    out: dict[tuple, dict] = {}

    def add(p: Pair, **extra) -> None:
        key = (p.premise_kind, p.premise_id, p.premise_text)
        if p.premise_kind in ("checker", "scene") or key in out:
            return
        out[key] = {"kind": p.premise_kind, "premise_id": p.premise_id, "text": p.premise_text,
                    "entity": None, "field": None, "entities": [], **extra}

    probe = {"title": "probe", "summary": "probe", "excerpt": "probe", "participants": list(ctx.chars),
             "entity": None, "field": None, "assumes": None, "quote": None}
    for e, f in states:    # ledger: 상태 가설마다
        spec = probe | {"entity": e, "field": f, "assumes": "x", "quote": "x"}
        for p in ctx.pairs(spec, "EVENT_PROBE"):
            if p.premise_kind == "ledger" and p.entity == e and p.field == f:
                add(p, entity=e, field=f)
    for c in ctx.chars:     # knowledge: 인물별
        if not led.knowledge.get(c):
            continue
        spec = probe | {"participants": [c], "entity": c, "field": "knows", "assumes": "x", "quote": "x"}
        for p in ctx.pairs(spec, "EVENT_PROBE"):
            if p.premise_kind == "knowledge" and p.entity == c and p.field == "knows":
                add(p, entity=c, field="knows")
    for p in ctx.pairs(probe, "EVENT_PROBE"):   # 사건 가설: rule·past (+ 위에서 못 잡은 것)
        if p.premise_kind in ("rule", "past"):
            ev = next((e for e in ctx.corpus if e.get("event_id") == p.premise_id), None)
            add(p, entities=sorted(_entities_of(ev)) if ev and p.premise_kind == "past" else [])
    order = {k: i for i, k in enumerate(PREMISE_KINDS)}
    rows = sorted(out.values(), key=lambda r: (order[r["kind"]], r["premise_id"], r["text"]))
    for i, r in enumerate(rows, 1):
        r["ref"] = f"P{i:02d}"
    return rows


def catalog_line(r: dict, names: dict) -> str:
    tag = r["kind"]
    if r["kind"] in ("ledger", "knowledge"):
        tag += f" · 가설은 {r['entity']}.{r['field']}"
    elif r["kind"] == "past":
        tag += " · 가설 엔티티는 " + ", ".join(r["entities"])
    else:
        tag += " · 사건 가설(field=null)"
    return f"{r['ref']} [{tag}] {r['text']}"


# ---------------------------------------------------------------- L1: 장면 조각

_HEAD = re.compile(r"^\s*-?\s*(\d+)화\s*[:：].*$", re.M)


def wm_scene_chunks(text: str, *, episodes=L1_EPISODES, max_chars: int = 1100, min_chars: int = 320) -> list[dict]:
    """WM 원문 → 장면 조각. 회차 머리줄로 나누고, 줄 `**`(장면 구분)로 나누고, 길면 빈 줄 단락 경계에서 자른다."""
    heads = [(int(m.group(1)), m.start(), m.end()) for m in _HEAD.finditer(text)]
    out = []
    for i, (ep, _s, e) in enumerate(heads):
        if ep not in episodes:
            continue
        body = text[e: heads[i + 1][1] if i + 1 < len(heads) else len(text)]
        segs = [s.strip() for s in re.split(r"^\s*\*\*\s*$", body, flags=re.M)]
        chunks: list[str] = []
        for seg in segs:
            if not seg:
                continue
            paras = [p.strip() for p in re.split(r"\n\s*\n", seg) if p.strip()]
            cur = ""
            pieces: list[str] = []
            for p in paras:
                if cur and len(cur) + len(p) + 2 > max_chars:
                    pieces.append(cur)
                    cur = p
                else:
                    cur = f"{cur}\n\n{p}" if cur else p
            if cur:
                pieces.append(cur)
            for pc in pieces:       # 너무 짧은 조각은 같은 장면 구분 안의 앞 조각에 붙인다
                if chunks and len(pc) < min_chars and len(chunks[-1]) + len(pc) + 2 <= max_chars * 1.4:
                    chunks[-1] += "\n\n" + pc
                else:
                    chunks.append(pc)
        for n, c in enumerate(chunks, 1):
            if len(c) >= 120:
                out.append({"scene_id": f"wm_e{ep}s{n:02d}", "episode": ep, "text": c})
    return out


def select_catalog(catalog: list[dict], text: str, *, n_know: int = 12, n_past: int = 10) -> list[dict]:
    """장면에 쓸 카탈로그를 줄인다: ledger·rule 전부, knowledge·past 는 장면 원문과 문자 2-gram Dice 상위."""
    def top(kind: str, n: int) -> list[dict]:
        rs = [r for r in catalog if r["kind"] == kind]
        rs.sort(key=lambda r: (-_relevance(text, r["text"]), r["ref"]))
        return sorted(rs[:n], key=lambda r: r["ref"])
    keep = [r for r in catalog if r["kind"] in ("ledger", "rule")] + top("knowledge", n_know) + top("past", n_past)
    return sorted(keep, key=lambda r: r["ref"])


# ---------------------------------------------------------------- L1: 항목 → 두 행

HYP_KEYS = ("title", "summary", "sentence")


def _need(d: Any, keys: tuple, where: str) -> None:
    if not isinstance(d, dict):
        raise ItemError(f"schema: {where} 가 객체가 아니다")
    for k in keys:
        if not isinstance(d.get(k), str) or not d[k].strip():
            raise ItemError(f"schema: {where}.{k} 가 비었다")


def _check_variant(v: Any, where: str, field: str | None) -> None:
    _need(v, HYP_KEYS + (("assumes",) if field else ()), where)
    if len(v["sentence"]) > 160:
        raise ItemError(f"schema: {where}.sentence 가 160자를 넘는다(서비스 발췌 상한)")
    if len(v["title"]) > 60 or len(v["summary"]) > 220:
        raise ItemError(f"schema: {where}.title/summary 가 너무 길다")


def l1_item_rows(item: dict, *, scene: dict, ctx: L1Context, catalog: list[dict], index: int, task: str) -> list[dict]:
    """에이전트 항목 하나 → [모순 행, 정상 행]. 규칙 위반이면 ItemError(사유)."""
    by_ref = {r["ref"]: r for r in catalog}
    ref = item.get("premise_ref")
    prem = by_ref.get(ref)
    if prem is None:
        raise ItemError(f"premise_ref: {ref!r} 이 카탈로그에 없다")
    ent, field = item.get("entity") or None, item.get("field") or None    # ""·null 모두 '없음'
    if field is not None and field not in FIELDS:
        raise ItemError(f"schema: field {field!r} 은 {FIELDS} 중 하나이거나 null")
    if ent is not None and ent not in ctx.names:
        raise ItemError(f"entity: {ent!r} 은 이 작품 레지스트리에 없다")
    if field is not None and ent is None:
        raise ItemError("schema: field 가 있으면 entity 도 있어야 한다")
    item = dict(item, type=canon_type(item.get("type")))
    if item.get("type") not in TYPES:
        raise ItemError(f"schema: type {item.get('type')!r} ∉ {TYPES}")
    parts = item.get("participants") or []
    if not isinstance(parts, list) or any(p not in ctx.names for p in parts):
        raise ItemError("participants: 알 수 없는 id")
    orig = item.get("orig")
    if not isinstance(orig, str) or norm(orig) not in norm(scene["text"]) or len(norm(orig)) < 8:
        raise ItemError("orig: 장면 원문에 그대로 있는 문장이 아니다")
    bad, ok = item.get("bad"), item.get("ok")
    _check_variant(bad, "bad", field)
    _check_variant(ok, "ok", field)
    if norm(bad["sentence"]) == norm(orig):
        raise ItemError("bad: 원문 그대로다(바뀐 문장이 아니다)")
    if norm(ok["sentence"]) == norm(orig):
        raise ItemError("ok: 원문 그대로다(일관판도 같은 자리를 바꾼 문장이어야 한다 — 문체 지름길 방지)")
    if norm(ok["sentence"]) == norm(bad["sentence"]):
        raise ItemError("ok: 모순판과 같은 문장이다")
    if negation_only(orig, bad["sentence"]):
        raise ItemError("negation_only: 부정어만 붙인 모순판(템플릿 지름길)")
    # 전제 종류별 가설 제약은 렌더링 결과로 확인한다(아래 pick_pair)
    gid = scene["scene_id"]
    rows = []
    for label, v, tag in (("모순", bad, "m"), ("정상", ok, "n")):
        spec = {"entity": ent, "entity_name": ctx.names.get(ent) if ent else None, "field": field,
                "assumes": v.get("assumes") if field else None, "quote": v["sentence"], "summary": v["summary"],
                "excerpt": v["sentence"], "title": v["title"], "episode": ctx.k, "participants": sorted(set(parts))}
        eid = f"EVENT_{ctx.k}_L{index:02d}{tag}"
        pairs = ctx.pairs(spec, eid)
        p = pick_pair(pairs, entity=ent if field else None, field=field, kind=prem["kind"],
                      premise_id=prem["premise_id"], premise_text=prem["text"])
        rows.append(make_row(f"{gid}:{index:02d}{tag}", "l1_wm", label, gid, item["type"], spec,
                             {"premise_ref": ref, "kind": prem["kind"], "premise_id": prem["premise_id"], "ep": ctx.k},
                             p, task=task, note=item.get("note")))
    rows[0]["pair_of"], rows[1]["pair_of"] = rows[1]["id"], rows[0]["id"]
    return rows


def make_row(rid: str, source: str, label: str, gid: str, typ: str, spec: dict, prem_spec: dict, pair: Pair,
             *, task: str, note: str | None) -> dict:
    return {"schema_version": SCHEMA_VERSION, "id": rid, "source": source, "label": label, "group_id": gid, "pair_of": None,
            "type": typ, "hyp_spec": spec, "prem_spec": prem_spec,
            "premise_kind": pair.premise_kind, "premise_id": pair.premise_id,
            "premise": pair.premise_text, "hypothesis": pair.hypothesis_text,
            "text_a": pair.premise_text, "text_b": pair.hypothesis_text,
            "work": source, "scene_id": gid, "case_id": rid, "contrast_group": f"{gid}:{rid.rsplit(':', 1)[-1][:-1]}",
            "source_kind": "claude", "split": "train", "keyed": True, "pairs_version": PAIRS_VERSION,
            "gen": {"task": task, "note": cut(note or "", 200)}}


# ---------------------------------------------------------------- L2: 합성 이야기 상태 렌더링

L2_VIA = ("direct", "told", "inference")


def l2_render(item: dict, hyp: dict, *, event_index: int, render: str = "v2") -> tuple[Pair, dict]:
    """L2 항목 + 가설 변형(bad|ok) → (Pair, hyp_spec). 전제는 item["premise"] 로 합성한 사건 1개(과거 사건 또는 앎)."""
    names = item["names"]
    pe, he = item["premise_ep"], item["hyp_ep"]
    pr, kind = item["premise"], item["premise_kind"]
    ent, field = item["entity"], item["field"]
    chars = sorted(k for k in names if k.startswith("CHAR_"))
    pev = {"event_id": f"EVENT_{pe}_{event_index}", "order": 1, "title": pr["title"], "summary": pr["summary"],
           "source_text": pr["quote"], "timeline": "main", "participant_character_ids": list(item["premise_entities"]),
           "state_changes": [], "assumed_states": []}
    if kind == "knowledge":
        pev["knowledge_gained"] = [{"character_id": pr["character"], "info": pr["info"], "via": pr.get("via") or "told",
                                    "source_text": pr["quote"], "retained": True}]
    epdoc = {"episode": pe, "events": [pev]}
    ledger = compile_ledger(episodes=[epdoc], timelines=["main"], knows_mode="accumulate")
    corpus = [dict(pev) | {"episode": pe, "global_idx": 0}]
    registry = {"characters": [{"id": c, "name": names[c]} for c in chars],
                "locations": [{"id": k, "name": v} for k, v in names.items() if k.startswith("LOC_")],
                "objects": [{"id": k, "name": v} for k, v in names.items() if k.startswith("OBJ_")],
                "worldview_rules": []}
    spec = {"entity": ent, "entity_name": names.get(ent) if ent else None, "field": field,
            "assumes": hyp.get("assumes") if field else None, "quote": hyp["sentence"], "summary": hyp["summary"],
            "excerpt": hyp["sentence"], "title": hyp["title"], "episode": he,
            "participants": sorted(set(item["hyp_participants"]))}
    ev = hyp_event(spec, event_id=f"EVENT_{he}_H", episode=he, timeline="main")
    pairs = all_pairs(ledger, corpus, registry, ev, render=render)
    want_kind = "knowledge" if kind == "knowledge" else "past"
    p = pick_pair(pairs, entity=ent if field else None, field=field, kind=want_kind)
    return p, spec


def l2_item_rows(item: dict, *, index: int, task: str) -> list[dict]:
    """에이전트 L2 항목 하나 → [모순 행, 정상 행]. 검사에 걸리면 ItemError."""
    names = item.get("names")
    if not isinstance(names, dict) or not names or not all(isinstance(k, str) and isinstance(v, str) and v.strip()
                                                           for k, v in names.items()):
        raise ItemError("schema: names 가 {id: 한국어 이름} 이 아니다")
    if not all(re.fullmatch(r"(CHAR|LOC|OBJ)_\d{3}", k) for k in names):
        raise ItemError("schema: names 의 id 는 CHAR_001 / LOC_001 / OBJ_001 형식")
    item = dict(item, type=canon_type(item.get("type")))
    if item.get("type") not in TYPES:
        raise ItemError(f"schema: type {item.get('type')!r} ∉ {TYPES}")
    kind = item.get("premise_kind")
    if kind not in L2_KINDS:
        raise ItemError(f"schema: premise_kind {kind!r} ∉ {L2_KINDS}")
    ent, field = item.get("entity") or None, item.get("field") or None
    if field is not None and field not in FIELDS:
        raise ItemError(f"schema: field {field!r} 은 {FIELDS} 중 하나이거나 null")
    if field is not None and ent not in names:
        raise ItemError("entity: names 에 없다")
    if ent is not None and ent not in names:
        raise ItemError("entity: names 에 없다")
    pr = item.get("premise")
    _need(pr, ("title", "summary", "quote"), "premise")
    if kind == "knowledge":
        _need(pr, ("character", "info"), "premise")
        if pr["character"] not in names or not pr["character"].startswith("CHAR_"):
            raise ItemError("premise.character: names 의 CHAR_ id 여야 한다")
        if pr.get("via") not in (None,) + L2_VIA:
            raise ItemError(f"premise.via: {L2_VIA} 중 하나")
    _check_variant(item.get("bad"), "bad", field)
    _check_variant(item.get("ok"), "ok", field)
    bad, ok = item["bad"], item["ok"]
    if norm(bad["sentence"]) == norm(ok["sentence"]):
        raise ItemError("ok: 모순판과 같은 문장이다")
    if norm(pr["quote"]) in (norm(bad["sentence"]), norm(ok["sentence"])):
        raise ItemError("premise.quote: 가설 문장과 같다(복사)")
    sid = item.get("item_id")
    if not isinstance(sid, str) or not sid:
        raise ItemError("schema: item_id")
    h = int(sha(sid)[:8], 16)
    pe = 1 + h % 3
    he = pe + 1 + (h >> 3) % 3
    base = dict(item) | {"premise_ep": pe, "hyp_ep": he, "entity": ent, "field": field}
    base["premise_entities"] = sorted(names)      # 전제 사건에 모든 개체가 나온 것으로 둔다(과거 사건 전제가 가설에서 보이게)
    parts = [e for e in ([ent] if ent and ent.startswith("CHAR_") else []) + list(item.get("participants") or []) if e in names]
    base["hyp_participants"] = sorted(set(parts)) or sorted(k for k in names if k.startswith("CHAR_"))
    if kind == "knowledge" and pr["character"] not in base["hyp_participants"]:
        base["hyp_participants"] = sorted(set(base["hyp_participants"]) | {pr["character"]})
    rows = []
    for label, v, tag in (("모순", bad, "m"), ("정상", ok, "n")):
        pair, spec = l2_render(base, v, event_index=index % 90 + 1)
        prem_spec = {"kind": kind, "title": pr["title"], "summary": pr["summary"], "quote": pr["quote"],
                     "character": pr.get("character"), "info": pr.get("info"), "via": pr.get("via"), "episode": pe,
                     "names": names, "entities": base["premise_entities"], "event_index": index % 90 + 1}
        rows.append(make_row(f"{sid}:{tag}", "l2_ffcd", label, item.get("story_id") or sid, item["type"],
                             spec, prem_spec, pair, task=task, note=item.get("note")))
    rows[0]["pair_of"], rows[1]["pair_of"] = rows[1]["id"], rows[0]["id"]
    for r in rows:
        r["contrast_group"] = f"{r['group_id']}:{sid}"
    return rows


# ================================================================ 재렌더링 일치 확인

def rerender_row(r: dict, l1ctx: dict[int, L1Context], render: str = "v2") -> tuple[str, str]:
    """저장된 구조(hyp_spec·prem_spec)에서 (premise, hypothesis) 를 다시 만든다. 파일을 손으로 고친 흔적을 잡는다.
    render=v3: 같은 쌍(저장된 v2 본문으로 찾은 것)의 v3 글을 돌려준다 — v2·v3 build_pairs 는 선택·순서가 같다."""
    spec, ps = r["hyp_spec"], r["prem_spec"]
    if r["source"] == "l1_wm":
        ctx = l1ctx.setdefault(ps["ep"], L1Context(ps["ep"]))
        pairs = ctx.pairs(spec, "EVENT_RR")
        out = ctx.pairs(spec, "EVENT_RR", render=render) if render != "v2" else pairs
        want = [i for i, p in enumerate(pairs) if p.premise_kind == ps["kind"] and p.premise_id == ps["premise_id"]
                and p.entity == (spec["entity"] if spec["field"] else None) and p.field == spec["field"]]
        # 같은 id 의 전제가 여럿(ledger 의 since 공유)이라 저장된 본문과 일치하는 것을 고른다
        hit = [i for i in want if pairs[i].premise_text == r["premise"]]
        if not hit:
            raise ItemError("rerender: 저장된 전제가 build_pairs 출력에 없다")
        return out[hit[0]].premise_text, out[hit[0]].hypothesis_text
    base = {"names": ps["names"], "premise_kind": ps["kind"], "premise_ep": ps["episode"],
            "hyp_ep": spec["episode"], "premise": {k: ps[k] for k in ("title", "summary", "quote", "character", "info", "via")},
            "entity": spec["entity"], "field": spec["field"], "premise_entities": ps["entities"],
            "hyp_participants": spec["participants"]}
    p, _ = l2_render(base, {"assumes": spec["assumes"], "sentence": spec["quote"], "summary": spec["summary"],
                            "title": spec["title"]}, event_index=ps["event_index"], render=render)
    return p.premise_text, p.hypothesis_text


def to_v3(rows: list[dict]) -> list[dict]:
    """L1·L2 행 → 같은 쌍의 v3 글(premise·hypothesis·text_a·text_b, pairs_version "3"). 먼저 v2 재렌더링이 저장본과 같은지 본다."""
    from app.pipeline.pairs import PAIRS_VERSION_V3
    ctx: dict[int, L1Context] = {}
    out = []
    for r in rows:
        p2, h2 = rerender_row(r, ctx)
        if (p2, h2) != (r["premise"], r["hypothesis"]):
            raise ItemError(f"to-v3: v2 재렌더링이 저장본과 다르다 {r.get('id')}")
        p3, h3 = rerender_row(r, ctx, render="v3")
        out.append(dict(r) | {"premise": p3, "hypothesis": h3, "text_a": p3, "text_b": h3, "pairs_version": PAIRS_VERSION_V3,
                              "premise_v2": r["premise"], "hypothesis_v2": r["hypothesis"]})
    return out


# ================================================================ 검증

def ngrams(rows: list[dict], n: int, key=lambda r: r["hyp_spec"]["quote"]) -> Counter:
    c: Counter = Counter()
    for r in rows:
        toks = key(r).split()
        for g in {" ".join(toks[i:i + n]) for i in range(len(toks) - n + 1)}:
            c[g] += 1
    return c


def assign_val_groups(group_ids, frac: float = 0.1, seed: int = 20261107) -> set[str]:
    """검증으로 뗄 묶음(결정적): 묶음 id 해시 순으로 ceil(frac·n) 개. 학습 세션이 이 단위로 10% 를 뗀다."""
    gs = sorted(set(group_ids))
    n = max(1, math.ceil(len(gs) * frac)) if gs else 0
    return set(sorted(gs, key=lambda g: sha(f"{seed}:{g}"))[:n])


def validate_rows(rows: list[dict], *, rerender: bool = False, top_n: int = 15, ngram_flag: float = 0.05) -> dict:
    """스키마·짝·전제·중복·반복·비율. 반환 dict 의 errors 가 비어야 통과."""
    errors: list[str] = []
    ids: dict[str, dict] = {}
    for r in rows:
        for k in ("id", "source", "label", "group_id", "pair_of", "type", "hyp_spec", "prem_spec", "premise_kind",
                  "premise_id", "premise", "hypothesis", "text_a", "text_b", "pairs_version"):
            if k not in r:
                errors.append(f"{r.get('id')}: 키 {k} 없음")
        if r.get("id") in ids:
            errors.append(f"{r['id']}: id 중복")
        ids[r.get("id")] = r
        if r.get("source") not in SOURCES:
            errors.append(f"{r.get('id')}: source {r.get('source')!r}")
        if r.get("label") not in LABELS:
            errors.append(f"{r.get('id')}: label {r.get('label')!r}")
        if r.get("type") not in TYPES:
            errors.append(f"{r.get('id')}: type {r.get('type')!r}")
        if r.get("pairs_version") != PAIRS_VERSION:
            errors.append(f"{r.get('id')}: pairs_version {r.get('pairs_version')!r} != {PAIRS_VERSION}")
        if r.get("text_a") != r.get("premise") or r.get("text_b") != r.get("hypothesis"):
            errors.append(f"{r.get('id')}: text_a/text_b 가 premise/hypothesis 와 다르다")
        if not (r.get("premise") or "").strip() or not (r.get("hypothesis") or "").strip():
            errors.append(f"{r.get('id')}: 빈 텍스트")
    for r in rows:
        o = ids.get(r.get("pair_of"))
        if o is None:
            errors.append(f"{r['id']}: 짝 {r.get('pair_of')!r} 없음")
            continue
        if o.get("pair_of") != r["id"]:
            errors.append(f"{r['id']}: 짝이 서로를 가리키지 않는다")
        if o["label"] == r["label"]:
            errors.append(f"{r['id']}: 짝과 라벨이 같다")
        if (o["premise"], o["group_id"]) != (r["premise"], r["group_id"]):
            errors.append(f"{r['id']}: 짝과 전제·묶음이 다르다")
        if o["hypothesis"] == r["hypothesis"]:
            errors.append(f"{r['id']}: 짝과 가설이 같다")
    seen: dict[tuple, str] = {}
    dup_pairs = dup_hyp = 0
    hyps: dict[str, str] = {}
    for r in rows:
        k = (norm(r["premise"]), norm(r["hypothesis"]))
        if k in seen:
            dup_pairs += 1
            errors.append(f"{r['id']}: (전제,가설) 중복 — {seen[k]}")
        seen[k] = r["id"]
        h = norm(r["hyp_spec"]["quote"])
        if h in hyps and hyps[h] != r["id"]:
            dup_hyp += 1
        hyps[h] = r["id"]
    if rerender:
        l1ctx: dict[int, L1Context] = {}
        for r in rows:
            try:
                a, b = rerender_row(r, l1ctx)
            except ItemError as e:
                errors.append(f"{r['id']}: {e}")
                continue
            if (a, b) != (r["premise"], r["hypothesis"]):
                errors.append(f"{r['id']}: 재렌더링 불일치")
    rep: dict = {"rows": len(rows), "errors": errors, "dup_pairs": dup_pairs, "dup_hypothesis_sentence": dup_hyp}
    for src in SOURCES:
        rs = [r for r in rows if r["source"] == src]
        if not rs:
            continue
        lab = Counter(r["label"] for r in rs)
        d: dict = {"rows": len(rs), "labels": dict(lab), "groups": len({r["group_id"] for r in rs}),
                   "types": dict(Counter(r["type"] for r in rs if r["label"] == "모순")),
                   "premise_kinds": dict(Counter(r["premise_kind"] for r in rs if r["label"] == "모순")),
                   "fields": dict(Counter(str(r["hyp_spec"]["field"]) for r in rs if r["label"] == "모순")),
                   "premise_len": _stats([len(r["premise"]) for r in rs]),
                   "hypothesis_len": _stats([len(r["hypothesis"]) for r in rs]),
                   "distinct_premises": len({r["premise"] for r in rs})}
        top = []
        for n in (2, 3):
            c = ngrams([r for r in rs if r["label"] == "모순"], n)
            denom = max(1, sum(1 for r in rs if r["label"] == "모순"))
            top += [{"n": n, "gram": g, "rows": k, "frac": round(k / denom, 3)} for g, k in c.most_common(top_n)]
        d["top_ngrams"] = top
        d["ngram_over_flag"] = [t for t in top if t["frac"] > ngram_flag]
        d["val_groups"] = sorted(assign_val_groups([r["group_id"] for r in rs]))
        rep[src] = d
    return rep


def _stats(xs: list[int]) -> dict:
    xs = sorted(xs)
    if not xs:
        return {}
    q = lambda p: xs[min(len(xs) - 1, int(p * len(xs)))]  # noqa: E731
    return {"min": xs[0], "p50": q(0.5), "p95": q(0.95), "max": xs[-1]}


# ================================================================ 에이전트 지시문

L1_PROMPT_VERSION = "L1-v2"   # v2: 앎·규칙 쏠림(파일럿 67쌍 중 58%) 때문에 전제 종류·유형 상한을 작업별 지침에 넣었다
L2_PROMPT_VERSION = "L2-v1"   # 재개 시 v2: type 은 정확히 하나의 문자열 (수치|위치|생사·신체|앎|규칙|시간순서|소유·상태|관계·행동)

L1_RULES = """\
# 과제 (L1 · WM 반사실) — 이 파일 하나만 읽고 일한다
너는 한국어 웹소설 한 장면의 문장 몇 개를 **최소한으로** 바꿔, 아래 '전제 목록'의 전제 **하나**와 충돌하는 문장(모순판)과,
같은 자리를 그 전제와 **맞게** 바꾼 문장(일관판)을 쌍으로 만든다. 학습 데이터(한국어 NLI식 판정기)용이다.
**다른 파일은 읽지 않는다.** 이 작업과 무관한 저장소 파일(특히 셜록·Q1·Q3·금호탕 관련 폴더)은 열지 않는다. 웹 접근·코드 실행도 하지 않는다.

## 쌍 하나가 만들어지는 법
- 판정기 입력은 (전제 문장, 가설 문장) 이다. **전제 문장은 네가 쓰지 않는다.** 전제 목록의 `premise_ref`(P01…)만 고르면 도구가 원문 그대로 복사한다.
- 가설은 장면의 문장 하나(`orig`)를 바꾼 것이다. 도구가 가설을 `[N화] 이름의 field: assumes (근거: 문장) | 사건: summary | 원문: 문장` 모양으로 만든다
  (`field` 가 null 이면 `사건 title | 사건: summary | 원문: 문장`).
- **모순판**(bad): `orig` 를 바꾼 문장. 고른 전제 **하나와 그 쌍만 읽어도** 충돌해야 한다(다른 전제·장면 맥락 없이).
- **일관판**(ok): 같은 자리를 같은 전제와 **맞게** 바꾼 문장. 모순판과 같은 개체·필드를 다루되 값만 전제와 맞는 쪽이다. 전제 문구를 그대로 베끼지 말고 다르게 표현한다.
  일관판도 **반드시 `orig` 와 다른 문장**이다(원문을 그대로 쓰지 않는다 — 문체 단서가 되기 때문).

## 지킬 것
- `orig` 는 장면 원문에 **그대로 있는** 한 문장(또는 짧은 구절)이다(복사). 장면 전체를 다시 쓰지 않는다. 바뀐 문장은 `orig` 와 비슷한 길이(0.5~2배), 160자 이하.
- 원문에 없는 새 인물·새 사건을 만들지 않는다. 이름은 원문 표기 그대로.
- **"~가 아니다/않았다" 식 부정어만 붙이거나 떼는 변형은 만들지 않는다**(템플릿 지름길) — 값이나 사실 자체를 바꾼다(숫자, 장소, 시간, 상태, 누가 무엇을 아는지 …). 전체의 1/4 을 넘기지 않는다.
- 같은 문형·같은 단어를 반복하지 않는다. 같은 `orig`·같은 전제를 두 번 쓰지 않는다. 문장마다 어휘와 구조를 달리한다.
- 억지로 맞추지 않는다: 이 장면·전제로 자연스럽게 안 되는 유형은 다른 유형으로 바꿔라. 충돌이 분명하지 않은 쌍은 만들지 않는다(적게 내도 된다).

## 전제 종류별 가설 제약 (도구가 렌더링하며 어기면 그 항목이 버려진다)
- `ledger`: 가설은 그 전제가 가리키는 (entity, field) 와 **같아야** 한다(목록의 '가설은 CHAR_00x.field'). 값이 `LOC_00x`·`OBJ_00x` 같은 id 이면 `assumes` 도 id 로 쓴다(아래 id 표).
  위치(location) 전제는 id 비교라 학습 신호가 약하니 전체의 15% 이하로.
- `knowledge`: 가설은 그 인물(entity)에 대한 것이고 field 는 `knows`(알거나 모르는 것) 등. participants 에 그 인물이 들어간다.
- `rule`: 사건 가설(field null). 그 세계관 규칙을 어기는 사건을 쓴다. participants 에 등장 인물 1~2명(CHAR id).
- `past`: 가설 entity 는 그 사건에 나오는 엔티티 중 하나(목록의 '가설 엔티티는 …'). field 는 자유(location, body_status, alive, possesses, knows, ability, condition) 또는 null.
- field 선택지: alive, location, body_status, possesses, knows, ability, condition, 또는 null. (condition 은 장소·사물, 나머지는 인물)

## 유형 (type) — 전부 고르게. 아래 여섯 가지에서 각각 대략 같은 수
수치(날짜·횟수·크기·시각·몇째 …) · 위치(어디에 있었는가) · 생사·신체(살아있음/죽음/부상/손상/상태) · 앎(누가 무엇을 아는가/모르는가) · 규칙(세계관 규칙 위반) · 시간순서(앞뒤·며칠 뒤·먼저/나중)
어쩔 수 없을 때만 `소유·상태`, `관계·행동`을 쓴다. 전제 종류(ledger / knowledge / rule / past)도 고르게 쓴다.

## 출력
`out_path` 에 **JSON 파일 하나**를 쓴다(Write 도구). 다른 곳엔 쓰지 않는다. 형식:
```
{"scene_id": "<이 장면 id>",
 "items": [
  {"premise_ref": "P07", "type": "앎", "entity": "CHAR_002", "field": "knows", "participants": ["CHAR_002"],
   "orig": "<장면 원문에 그대로 있는 문장>",
   "bad": {"sentence": "<모순판 문장>", "assumes": "<가설의 값 — field 가 null 이면 생략>", "title": "<사건 제목 짧게>", "summary": "<이 문장이 서술하는 사건 한 줄 요약>"},
   "ok":  {"sentence": "<일관판 문장>", "assumes": "...", "title": "...", "summary": "..."},
   "note": "<왜 충돌인지 한 줄>"}
 ],
 "skipped": [{"why": "<만들지 못한 이유>"}]}
```
- `assumes`: 그 문장이 서술하는 상태의 값. 짧은 구(예: `로봇 신체 손상`, `LOC_009`, `헤일트라움 제1창고`). 모순판은 전제와 충돌하는 값, 일관판은 맞는 값.
- `summary`/`title`: 바뀐 문장이 서술하는 사건을 추출기처럼 건조하게 한 줄(문장과 같은 사실을 담는다 — 문장과 어긋나면 안 된다).
- 마치면 만든 항목 수와 건너뛴 사유를 한두 줄로만 답한다.
"""

L2_RULES = """\
# 과제 (L2 · 외부 정리) — 이 파일 하나만 읽고 일한다
영어 소설에서 사람이 확인한 연속성 오류(FlawedFictions) 또는 문서 내 모순(ContraDoc) 항목들이 있다. 항목마다
**한국어로 다시 써서, 그 쌍만 읽어도 모순이 성립하는 (전제, 가설) 쌍**과 **같은 자리를 전제와 맞게 고친 일관판**을 만든다.
학습 데이터(한국어 판정기용)다. **다른 파일은 읽지 않는다.** 웹 접근·코드 실행도 하지 않는다. 번역 파일이 있어도 쓰지 않는다 — 영어 원문 맥락에서 새로 쓴다.

## 쌍 하나가 만들어지는 법
- 판정기 입력은 (전제 문장, 가설 문장). 두 문장 모양은 도구가 만든다. 네가 주는 것은 구조 필드다.
- **전제**(= 충돌 당한 쪽, 영어 항목의 `premise_en`): 과거 사건 한 건이거나(`premise_kind: "past"`), 어떤 인물이 안다는 사실이다(`"knowledge"`).
  · past: `title`(짧은 제목), `summary`(사건 한 줄 요약), `quote`(원문 발췌 — 한국어로 옮긴 그 문장)
  · knowledge: `character`(CHAR_ id), `info`(그 인물이 아는 내용), `via`(direct|told|inference), 그리고 `title`·`summary`·`quote` 도(그 앎이 생긴 사건)
- **가설**(= 오류 쪽, `hypothesis_en`): 개체·필드·값으로 쓴다. `entity`, `field`(alive|location|body_status|possesses|knows|ability|condition 또는 null — null 이면 사건 가설), `assumes`(값),
  `sentence`(근거 인용 = 원문 발췌 — 오류 문장을 한국어로), `summary`(그 문장이 서술하는 사건 한 줄), `title`.
- **모순판**(bad) = 원래 오류 문장의 한국어판. **일관판**(ok) = 같은 자리를 전제와 맞게 고친 문장(원작의 오류를 고친 것). 같은 개체·필드, 값만 맞는 쪽.
  일관판은 모순판과 문형이 거의 같되 사실만 전제와 맞게 바뀐 것이어야 한다(문체 단서가 안 되게).

## 지킬 것
- **혼자 읽혀도** 뜻이 통해야 한다. 대명사('그는', '그녀는')에 의존하지 말고 이름을 쓴다. 원문 조각이라 맥락이 더 필요하거나 모순이 불분명하면 **버린다**
  (`keep: false`, `why` 에 사유 한 줄: 맥락 필요 / 충돌 없음 / 의견·해석 차이 / 오류 아님 / 번역으로 뜻 상실 …). 버려도 된다 — 품질이 개수보다 중요하다.
- 이름은 한국어 음차로 **항목 안에서 일관되게**(같은 사람은 항상 같은 표기). `names` 에 id → 한국어 이름 (CHAR_001, CHAR_002 …, 장소 LOC_001, 사물 OBJ_001). 직업·호칭(왕, 어부)은 그대로 한국어.
- 전제와 가설의 문장은 서로 베끼지 않는다. 같은 문형을 반복하지 않는다(동사·문형·어휘를 항목마다 다르게).
- 모순은 **한 쌍으로** 성립해야 한다: 전제 문장과 모순판 문장을 나란히 두면 사실이 양립 불가능하다. 인물의 감정·의도·해석, 문체 차이, 서로 다른 시점의 변화는 모순이 아니다.
- 유형(type): 정확히 다음 문자열 중 **하나만**(둘을 붙여 쓰지 않는다): 수치 | 위치 | 생사·신체 | 앎 | 규칙 | 시간순서 | 소유·상태 | 관계·행동. 고르게 쓰되 억지 금지.
- 문장 길이: sentence/quote 는 각각 160자 이하, 한 문장(길어도 두 문장).

## 출력
`out_path` 에 **JSON 파일 하나**를 쓴다(Write 도구). 형식:
```
{"task": "<이 작업 id>",
 "items": [
  {"item_id": "<입력의 id 그대로>", "keep": true, "type": "생사·신체", "premise_kind": "past",
   "names": {"CHAR_001": "...", "CHAR_002": "..."},
   "premise": {"title": "...", "summary": "...", "quote": "...", "character": "CHAR_001", "info": "...", "via": "told"},
   "entity": "CHAR_001", "field": "alive", "participants": ["CHAR_001"],
   "bad": {"sentence": "...", "assumes": "...", "title": "...", "summary": "..."},
   "ok":  {"sentence": "...", "assumes": "...", "title": "...", "summary": "..."},
   "note": "<왜 충돌인지 한 줄>"},
  {"item_id": "<id>", "keep": false, "why": "맥락 필요"}
 ]}
```
(knowledge 가 아니면 premise 의 character·info·via 는 생략.) 입력 항목 **전부**에 대해 줄을 하나씩 낸다(keep true/false). 마치면 keep 수와 버린 수만 한 줄로 답한다.
"""


def l1_task_md(scene: dict, cat: list[dict], names: dict, out_path: Path, target: int, plan: str) -> str:
    ids = "\n".join(f"  {k} = {v}" for k, v in sorted(names.items()))
    prem = "\n".join(catalog_line(r, names) for r in cat)
    return (f"{L1_RULES}\n---\n# 이 작업\n- scene_id: {scene['scene_id']}  (WM {scene['episode']}화)\n- out_path: {out_path}\n"
            f"- 목표: 모순/일관 쌍 {target}개 (유형 배분 지침: {plan})\n\n## id 표 (entity·participants 에 쓰는 id)\n{ids}\n\n"
            f"## 전제 목록 ({scene['episode']}화 시점, premise_ref 로 고른다)\n{prem}\n\n"
            f"## 장면 원문 ({scene['episode']}화)\n```\n{scene['text']}\n```\n")


# ================================================================ L2: 외부 항목 추출

SCREEN = re.compile(r"\b(Holmes|Watson|Baker Street|Lestrade|Moriarty|Mycroft|Irene Adler|Mrs\.? Hudson|Dracula|"
                    r"Van Helsing|Harker|Westenra|Renfield|Quincey|Whitby)\b", re.I)   # 학습 작품 밖 데이터 작품(셜록·드라큘라)
_SENT = re.compile(r"(?<=[.!?])[\"”’']?\s+(?=[A-Z\"“‘'(])")


def _sentences(text: str) -> list[str]:
    import ext_data as X
    return X.split_sentences(text)


def _locate(sents: list[str], line: str) -> int | None:
    nl = norm(line)
    if len(nl) < 12:
        return None
    for i, s in enumerate(sents):
        ns = norm(s)
        if ns and (nl in ns or ns in nl):
            return i
    return None


def _window(sents: list[str], i: int, before: int = 2, after: int = 2, cap: int = 900) -> str:
    seg = " ".join(sents[max(0, i - before): i + after + 1])
    return seg if len(seg) <= cap else seg[:cap].rsplit(" ", 1)[0] + " …"


def l2_candidates(ff_tables: list[list[dict]], contradoc: dict, *, per_story: int = 2) -> list[dict]:
    """영어 항목 후보: {item_id, story_id, src, premise_en(전제 쪽), hypothesis_en(오류 쪽), ctx_premise, ctx_hyp, expl}."""
    import ext_data as X
    out = []
    for tab in ff_tables:
        for r in tab:
            if not r.get("cont_error"):
                continue
            story = X.strip_html(r["story"])
            if SCREEN.search(story):
                continue
            sents = _sentences(story)
            errs, cons = X.li_items(r.get("cont_error_lines")), X.li_items(r.get("contradicted_lines"))
            seen_err: set[str] = set()
            n = 0
            for e in errs:
                if n >= per_story:
                    break
                ei = _locate(sents, e)
                if ei is None or norm(e) in seen_err:
                    continue
                best, bi = None, None
                for c in cons:
                    ci = _locate(sents, c)
                    if ci is None or ci == ei or norm(c) == norm(e):
                        continue
                    if best is None or ci < bi:   # 가장 앞선(원래) 서술
                        best, bi = c, ci
                if best is None:
                    continue
                seen_err.add(norm(e))
                n += 1
                out.append({"item_id": f"{r['example_id']}#{n}", "story_id": str(r["example_id"]), "src": "flawed_fictions",
                            "premise_en": X._cut(best), "hypothesis_en": X._cut(e),
                            "ctx_premise": _window(sents, bi), "ctx_hyp": _window(sents, ei),
                            "expl": cut(re.sub(r"\s+", " ", str(r.get("cont_error_expl") or "")), 500)})
    for uid, v in sorted((contradoc.get("pos") or {}).items()):
        if v.get("doc_type") != "story" or not v.get("ref sentences"):
            continue
        text = X.strip_html(v["text"])
        if SCREEN.search(text):
            continue
        sents = _sentences(text)
        ev = v["evidence"]
        refs = v["ref sentences"] if isinstance(v["ref sentences"], list) else [v["ref sentences"]]
        ei = _locate(sents, ev)
        ri = next((i for i in (_locate(sents, str(x)) for x in refs) if i is not None), None)
        if ei is None or ri is None or ri == ei:
            continue
        out.append({"item_id": f"contradoc_{uid}#1", "story_id": f"contradoc_{uid}", "src": "contradoc",
                    "premise_en": X._cut(sents[ri]), "hypothesis_en": X._cut(ev),
                    "ctx_premise": _window(sents, ri), "ctx_hyp": _window(sents, ei),
                    "expl": f"contra_type: {v.get('contra_type')}"})
    return out


def l2_task_md(items: list[dict], task: str, out_path: Path) -> str:
    blocks = []
    for it in items:
        blocks.append(f"### item_id: {it['item_id']}   (출처 {it['src']})\n"
                      f"- 전제 쪽 문장(충돌 당한 쪽): {it['premise_en']}\n- 그 앞뒤 맥락: {it['ctx_premise']}\n"
                      f"- 오류 쪽 문장(가설이 될 쪽): {it['hypothesis_en']}\n- 그 앞뒤 맥락: {it['ctx_hyp']}\n"
                      f"- 라벨러 설명(참고만, 틀릴 수 있다): {it['expl']}\n")
    return f"{L2_RULES}\n---\n# 이 작업\n- task: {task}\n- out_path: {out_path}\n- 항목 수: {len(items)}\n\n" + "\n".join(blocks)


# ================================================================ CLI

def _prep_l1(a: argparse.Namespace) -> None:
    text = WM_TEXT.read_text(encoding="utf-8")
    chunks = wm_scene_chunks(text)
    out = a.work / "l1"
    ctxs: dict[int, L1Context] = {}
    plan = ("앎(knowledge 전제)은 최대 2개, 규칙(rule 전제)은 최대 2개. 나머지는 과거 사건(past) 전제 4개 이상과 원장(ledger) 전제 2개 이상으로 채운다 — "
            "수치·시간순서·생사·신체·위치는 주로 past/ledger 전제로 만든다(past 전제의 요약·원문에 나온 날짜·횟수·순서·상태·장소를 바꾼다). "
            "자연스럽지 않으면 건너뛰되, 앎·규칙으로 메우지 않는다")
    index = []
    for sc in chunks:
        ctx = ctxs.setdefault(sc["episode"], L1Context(sc["episode"]))
        cat = l1_catalog(ctx)
        sel = select_catalog(cat, sc["text"])
        out_path = out / "out" / f"{sc['scene_id']}.json"
        md = l1_task_md(sc, sel, ctx.names, out_path, a.target, plan)
        (out / "tasks").mkdir(parents=True, exist_ok=True)
        (out / "tasks" / f"{sc['scene_id']}.md").write_text(md, encoding="utf-8")
        index.append({"scene_id": sc["scene_id"], "episode": sc["episode"], "chars": len(sc["text"]),
                      "premises": len(sel), "task_md_chars": len(md)})
    (out / "out").mkdir(parents=True, exist_ok=True)
    (out / "scenes.json").write_text(json.dumps({"prompt_version": L1_PROMPT_VERSION, "chunks": chunks}, ensure_ascii=False,
                                                indent=1), encoding="utf-8")
    print(json.dumps({"scenes": len(index), "by_episode": dict(Counter(i["episode"] for i in index)),
                      "task_chars_p50": sorted(i["task_md_chars"] for i in index)[len(index) // 2]}, ensure_ascii=False))


def _prep_l2(a: argparse.Namespace) -> None:
    import ext_data as X
    tabs = [X._read_parquet(p) for p in a.ff]
    cd = json.loads(a.contradoc.read_text(encoding="utf-8"))
    cands = l2_candidates(tabs, cd, per_story=a.per_story)
    rng = random.Random(a.seed)
    stories = sorted({c["story_id"] for c in cands})
    rng.shuffle(stories)
    rank = {s: i for i, s in enumerate(stories)}
    cands.sort(key=lambda c: (rank[c["story_id"]], c["item_id"]))
    if a.max_items:
        cands = cands[: a.max_items]
    out = a.work / "l2"
    (out / "tasks").mkdir(parents=True, exist_ok=True)
    (out / "out").mkdir(parents=True, exist_ok=True)
    n_tasks = math.ceil(len(cands) / a.per_task)
    for t in range(n_tasks):
        chunk = cands[t * a.per_task: (t + 1) * a.per_task]
        name = f"t{t + 1:02d}"
        (out / "tasks" / f"{name}.md").write_text(l2_task_md(chunk, name, out / "out" / f"{name}.json"), encoding="utf-8")
    (out / "candidates.json").write_text(json.dumps({"prompt_version": L2_PROMPT_VERSION, "items": cands}, ensure_ascii=False,
                                                    indent=1), encoding="utf-8")
    print(json.dumps({"candidates": len(cands), "tasks": n_tasks, "by_src": dict(Counter(c["src"] for c in cands)),
                      "stories": len({c["story_id"] for c in cands})}, ensure_ascii=False))


def _build(a: argparse.Namespace) -> None:
    rows, rejects = [], []
    out_files = sorted((a.work / a.part / "out").glob("*.json"))
    if a.part == "l1":
        scenes = {c["scene_id"]: c for c in json.loads((a.work / "l1" / "scenes.json").read_text(encoding="utf-8"))["chunks"]}
        ctxs: dict[int, L1Context] = {}
        cats: dict[int, list[dict]] = {}
        for f in out_files:
            data = json.loads(f.read_text(encoding="utf-8"))
            sc = scenes[data["scene_id"]]
            ctx = ctxs.setdefault(sc["episode"], L1Context(sc["episode"]))
            cat = cats.setdefault(sc["episode"], l1_catalog(ctx))
            seen_orig: set[str] = set()
            for i, item in enumerate(data.get("items") or [], 1):
                try:
                    if norm(item.get("orig", "")) + "|" + str(item.get("premise_ref")) in seen_orig:
                        raise ItemError("dup: 같은 orig·같은 전제")
                    seen_orig.add(norm(item.get("orig", "")) + "|" + str(item.get("premise_ref")))
                    rows += l1_item_rows(item, scene=sc, ctx=ctx, catalog=cat, index=i, task=f.stem)
                except ItemError as e:
                    rejects.append({"task": f.stem, "index": i, "reason": str(e), "item": item})
            for s in data.get("skipped") or []:
                rejects.append({"task": f.stem, "index": None, "reason": f"agent_skipped: {s.get('why')}"})
        dest = a.work / "l1_wm.jsonl"
    else:
        for f in out_files:
            data = json.loads(f.read_text(encoding="utf-8"))
            for i, item in enumerate(data.get("items") or [], 1):
                if item.get("keep") is False:
                    rejects.append({"task": f.stem, "index": i, "reason": f"agent_drop: {item.get('why')}",
                                    "item_id": item.get("item_id")})
                    continue
                try:
                    rows += l2_item_rows(item, index=i, task=f.stem)
                except ItemError as e:
                    rejects.append({"task": f.stem, "index": i, "reason": str(e), "item": item})
        dest = a.work / "l2_ffcd.jsonl"
    write_jsonl(dest, rows)
    write_jsonl(a.work / f"{a.part}_rejects.jsonl", rejects)
    lab = Counter(r["label"] for r in rows)
    why = Counter(r["reason"].split(":")[0] for r in rejects)
    print(json.dumps({"rows": len(rows), "labels": dict(lab), "rejects": len(rejects), "reject_reasons": dict(why)},
                     ensure_ascii=False))


def _validate(a: argparse.Namespace) -> None:
    rows = []
    for name in ("l1_wm.jsonl", "l2_ffcd.jsonl"):
        p = a.work / name
        if p.exists():
            rows += read_jsonl(p)
    rep = validate_rows(rows, rerender=a.rerender)
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    if rep["errors"]:
        raise SystemExit(f"검증 실패: 오류 {len(rep['errors'])}건")


def _to_v3(a: argparse.Namespace) -> None:
    rows = to_v3(read_jsonl(a.inp))
    a.out.parent.mkdir(parents=True, exist_ok=True)
    with a.out.open("w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(json.dumps({"rows": len(rows), "out": str(a.out)}, ensure_ascii=False))


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--work", type=Path, default=WORK_DIR)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("prep-l1")
    p1.add_argument("--target", type=int, default=12, help="장면당 쌍 수")
    p2 = sub.add_parser("prep-l2")
    p2.add_argument("--ff", type=Path, nargs="+", default=[EXT_DIR / "ff" / "flawed_fictions.parquet",
                                                           EXT_DIR / "ff" / "flawed_fictions_long.parquet"])
    p2.add_argument("--contradoc", type=Path, default=EXT_DIR / "contradoc" / "ContraDoc.json")
    p2.add_argument("--per-task", type=int, default=30)
    p2.add_argument("--per-story", type=int, default=2)
    p2.add_argument("--max-items", type=int, default=0)
    p2.add_argument("--seed", type=int, default=20261107)
    b = sub.add_parser("build")
    b.add_argument("part", choices=["l1", "l2"])
    v = sub.add_parser("validate")
    v.add_argument("--rerender", action="store_true")
    t = sub.add_parser("to-v3", help="L1·L2 행을 같은 쌍의 v3(자연어) 글로")
    t.add_argument("--in", dest="inp", type=Path, required=True)
    t.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    {"prep-l1": _prep_l1, "prep-l2": _prep_l2, "build": _build, "validate": _validate, "to-v3": _to_v3}[a.cmd](a)


if __name__ == "__main__":
    main()
