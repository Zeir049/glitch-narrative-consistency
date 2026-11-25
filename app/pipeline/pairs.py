"""학습 judge 용 (가설, 전제) 쌍 생성기 — 결정적, LLM 없음.

학습 데이터 내보내기(scripts/eval/export_pairs.py)와 서비스 추론(learned_judge.LearnedJudge)이
**이 함수 하나**(build_pairs)를 쓴다. 학습 때와 추론 때 입력 모양이 달라지지 않게 하기 위해서다.
출력 정의가 바뀌면 PAIRS_VERSION 을 올린다(모델 폴더 judge_meta.json 의 pairs_version 과 맞아야 로드된다).

가설(hypothesis) — 판정 패킷(장면)의 새 사건마다
  · assumed_states 항목 하나당 하나: 엔티티 이름·필드·값·근거 발췌 + 사건 요약 + 사건 원문(source_text)
  · 사건 자체 하나(field=None): 제목·요약·원문 — 전제 항목이 없는 서술(세계관 규칙 위반 등)용
  v2: 머리에 [M화](사건 episode, 없으면 current_episode), 원문·근거 발췌는 excerpt_chars 로 자른다.
전제(premise) kind
  checker   checker 의 needs_judge 항목(같은 사건, 엔티티·필드가 맞는 것) — 원장값·since 그대로
  ledger    같은 (엔티티, 필드)의 원장 이력: 현재값·누적 knows·승격 전제(_promoted) + since 사건 발췌
            (사건 가설은 그 사건에 나온 엔티티 전부의 필드)
            v2: since 회차 기준 '경과 M−N화', since 이후 같은 엔티티가 나온 가장 이른 corpus 사건 1줄
  rule      세계관 규칙(registry["worldview_rules"])
  knowledge 참여 인물의 knowledge(info·via·since) — v2: '경과 M−N화'
  scene     같은 장면의 다른 사건
  past      같은 엔티티가 나온 과거 사건

전제 선택 (v2, quotas=QUOTAS — 설계안 §2-3)
  kind 순서 checker → ledger → knowledge → rule → past → scene, 가설당 합 max_premises(24).
  kind 별 할당량 QUOTAS(checker 는 전부). 다 못 쓴 칸은 다음 kind 로 넘기고, 마지막 kind 뒤에도 칸이
  남으면 같은 순서로 남은 전제를 채운다. kind 안 정렬은 (-관련도, 기존 키) — 관련도는 가설 머리와 전제의
  문자 2-gram Dice(_relevance), 기존 키는 rule·scene·ledger = ID → 본문, knowledge·past = 최근 순 → ID.
  checker 는 기존 키만. 사건 단위 가설(field=None)의 ledger 는 관련도 상위 quotas["ledger"]개가 끝(넘김 없음).
전제 선택 (v1, quotas=None) — 개정 전 출력 그대로(비교·테스트용)
  kind 순서 checker → ledger → rule → knowledge → scene → past, 상한 max_premises(개정 전 기본 32).
  v1 은 [M화]·경과·발췌 자르기를 하지 않는다(excerpt_chars·current_episode 무시).

렌더링 v3 (render="v3", PAIRS_VERSION_V3 — 설계안 §0-9 실험 N): **전제 선택은 v2 와 같다**(관련도·중복 제거는 v2 문자열로 계산).
  글만 자연어로 바꾼다 — 사건 ID·since 태그·certainty·영문 필드명·'이후:' 줄·'발췌:' 접두를 빼고, 필드는 FIELD_KO 로 한국어.
  상태 가설 `“근거” → 이름의 필드: 값`, 사건 가설 `제목: 요약. 원문: “발췌”`(둘 다 [M화] 앞머리 유지),
  원장 `N화 기준 이름의 필드: 값. 원문: “since 발췌”`, 앎 `이름은(는) 정보을(를) 안다 (N화)`, 과거·장면 사건 `[N화] 제목: 요약. 원문: “발췌”`,
  규칙 `세계관 규칙: 내용`, checker `이름의 필드: 전제 X / 원장 Y`. render 기본은 v2(서비스 기본, 바이트 그대로).

원장은 weave_core/ledger.py 의 Ledger 공개 면(get · state · knowledge)만 읽고, 고치지 않는다.
"""
from __future__ import annotations

import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent / "weave_core"
if str(CORE_DIR) not in sys.path:  # 코어는 평면 import 를 쓴다(weave.py 와 같은 방식)
    sys.path.insert(0, str(CORE_DIR))

from ledger import FALLBACK_TIMELINE  # noqa: E402

PAIRS_VERSION = "2"
PAIRS_VERSION_V3 = "3"
RENDERS = {"v2": PAIRS_VERSION, "v3": PAIRS_VERSION_V3}
# v3 필드 한국어 대응표(고정). 없는 필드는 원문 그대로
FIELD_KO: dict[str, str] = {
    "location": "위치", "body_status": "몸 상태", "possesses": "소지품", "knows": "앎", "condition": "상태",
    "alive": "생사", "ability": "능력", "relationship": "관계", "affiliation": "소속", "occupation": "직업",
    "appearance": "외모", "emotion": "감정", "belief": "믿음", "health": "건강", "role": "역할", "status": "처지",
    "age": "나이", "name": "이름", "identity": "정체", "goal": "목표", "mental_state": "마음 상태",
}
KIND_ORDER = ("checker", "ledger", "rule", "knowledge", "scene", "past")  # v1(quotas=None) 순서
KIND_RANK = {k: i for i, k in enumerate(KIND_ORDER)}
LEGACY_MAX_PREMISES = 32
MAX_PREMISES = 24
# v2 할당량(사전 고정 — 설계안 §2-3. 평가 결과를 보고 바꾸지 않는다). dict 순서가 곧 kind 순서. None = 전부
QUOTAS: dict[str, int | None] = {"checker": None, "ledger": 6, "knowledge": 6, "rule": 4, "past": 6, "scene": 2}
EXCERPT_CHARS = 160
NEXT_EVENT_CHARS = 80

Premise = tuple[str, str, str]  # (kind, id, text)


@dataclass(frozen=True)
class Pair:
    hypothesis_text: str
    premise_text: str
    premise_id: str
    premise_kind: str
    entity: str | None
    field: str | None
    event_id: str

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------- 보조

def _s(v) -> str:  # noqa: ANN001
    return "" if v is None else str(v).strip()


def _cut(text: str, n: int | None) -> str:
    """앞 n자. 잘렸으면 끝에 '…'. n=None 이면 그대로."""
    return text if n is None or len(text) <= n else text[:n] + "…"


def _names(registry: dict | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for coll, key in (("characters", "id"), ("locations", "id"), ("objects", "id"), ("times", "id")):
        for item in (registry or {}).get(coll) or []:
            iid = item.get(key)
            name = item.get("name") or item.get("expression")
            if iid and name:
                out[iid] = str(name)
    return out


def _entities_of(ev: dict) -> list[str]:
    """사건에 나온 엔티티 ID(등장 순, 중복 제거). 판정 프롬프트(judge._entities_of)보다 조금 넓다."""
    ids = list(ev.get("participant_character_ids") or [])
    ids += ev.get("witness_character_ids") or []
    if ev.get("main_actor_character_id"):
        ids.append(ev["main_actor_character_id"])
    ids += ev.get("objects") or []
    ids += [a.get("entity") for a in ev.get("assumed_states") or []]
    ids += [c.get("entity") for c in ev.get("state_changes") or []]
    if ev.get("location_id"):
        ids.append(ev["location_id"])
    seen, out = set(), []
    for i in ids:
        if isinstance(i, str) and i and i != "unknown" and i not in seen:
            seen.add(i)
            out.append(i)
    return out


def _participants(ev: dict) -> list[str]:
    ids = list(ev.get("participant_character_ids") or [])
    if ev.get("main_actor_character_id"):
        ids.append(ev["main_actor_character_id"])
    ids += ev.get("witness_character_ids") or []
    return sorted({i for i in ids if isinstance(i, str) and i and i != "unknown"})


def _timeline(ledger, ev: dict) -> str:  # noqa: ANN001
    tl = ev.get("timeline") or FALLBACK_TIMELINE
    return tl if tl in ledger.state else FALLBACK_TIMELINE


def _episode(v) -> int | None:  # noqa: ANN001
    try:
        return int(v) if v is not None and str(v).strip() != "" else None
    except (TypeError, ValueError):
        return None


def _bigrams(text: str) -> frozenset[str]:
    t = "".join(text.lower().split())
    return frozenset(t[i:i + 2] for i in range(len(t) - 1))


def _relevance(hyp_head: str, premise: str) -> float:
    """가설 머리와 전제 본문의 문자 2-gram Dice(공백 제거·소문자, 집합). 0~1, 결정적."""
    a, b = _bigrams(hyp_head), _bigrams(premise)
    if not a or not b:
        return 0.0
    return 2 * len(a & b) / (len(a) + len(b))


def _fko(field: str | None) -> str:
    return FIELD_KO.get(_s(field), _s(field))


def _sentence(text: str) -> str:
    """끝에 마침표 하나(이미 문장부호·닫는 따옴표로 끝나면 그대로)."""
    t = text.rstrip()
    return t if not t or t[-1] in ".!?…”\"'" else t + "."


def _quoted(text: str, n: int | None) -> str:
    return f"“{_cut(text, n)}”" if text else ""


def _v3_event(ev: dict, n: int | None) -> str:
    head = f"[{ev['episode']}화] " if ev.get("episode") is not None else ""
    body = _sentence(f"{_s(ev.get('title'))}: {_s(ev.get('summary'))}".strip())
    src = _s(ev.get("source_text"))
    return f"{head}{body}" + (f" 원문: {_quoted(src, n)}" if src else "")


_ID = re.compile(r"\b(?:CHAR|LOC|OBJ|TIME|EVENT)_[0-9A-Za-z_]*\d")


def _v3_ids(text: str, names: dict) -> str:
    """자유 서술 안의 레지스트리 ID 를 이름으로(모르는 ID 는 그대로)."""
    return _ID.sub(lambda m: names.get(m.group(0), m.group(0)), text)


def _v3_value(field: str, value, names: dict) -> str:  # noqa: ANN001
    v = _s(value)
    if field == "alive" and v.lower() in ("true", "false"):
        return "살아 있다" if v.lower() == "true" else "죽었다"
    return _v3_ids(names.get(v, v), names) or "기록 없음"


class _Ctx:
    """build_pairs 한 번 동안 공유하는 조회표."""

    def __init__(self, corpus: list[dict] | None, names: dict, excerpt_chars: int | None, legacy: bool):
        self.names = names
        self.legacy = legacy
        self.excerpt = None if legacy else excerpt_chars
        self.corpus = list(corpus or [])
        self.by_id = {e.get("event_id"): e for e in self.corpus}
        self._ents: dict[int, list[str]] = {}
        self._by_entity: dict[str, list[tuple[int, str, dict]]] | None = None
        self.v3: dict[tuple[str, str], str] | None = None  # render=v3 일 때 (id, v2 본문) → v3 본문

    def ents(self, ev: dict) -> list[str]:
        k = id(ev)
        if k not in self._ents:
            self._ents[k] = _entities_of(ev)
        return self._ents[k]

    def quote(self, eid) -> str:  # noqa: ANN001
        ev = self.by_id.get(eid) if eid else None
        return _cut(_s(ev.get("source_text")), self.excerpt) if ev else ""

    def episode_of(self, eid) -> int | None:  # noqa: ANN001
        ev = self.by_id.get(eid) if eid else None
        return _episode(ev.get("episode")) if ev else None

    def gidx(self, eid) -> int | None:  # noqa: ANN001
        ev = self.by_id.get(eid) if eid else None
        return int(ev.get("global_idx") or 0) if ev else None

    def next_event(self, ent: str, since: str, exclude: str) -> dict | None:
        """since 사건 뒤(global_idx 가 큰) 같은 엔티티가 나온 corpus 사건 중 가장 이른 것."""
        if self._by_entity is None:
            idx: dict[str, list[tuple[int, str, dict]]] = {}
            for e in self.corpus:
                g = int(e.get("global_idx") or 0)
                for x in self.ents(e):
                    idx.setdefault(x, []).append((g, _s(e.get("event_id")), e))
            for v in idx.values():
                v.sort(key=lambda t: (t[0], t[1]))
            self._by_entity = idx
        g0 = self.gidx(since)
        if g0 is None:
            return None
        for g, eid, e in self._by_entity.get(ent, []):
            if g > g0 and eid != since and eid != exclude:
                return e
        return None

    def event_line(self, ev: dict) -> str:
        head = f"[{ev['episode']}화] " if ev.get("episode") is not None else ""
        parts = [f"{head}{_s(ev.get('title'))}: {_s(ev.get('summary'))}".strip()]
        if _s(ev.get("source_text")):
            parts.append(f"원문: {_cut(_s(ev.get('source_text')), self.excerpt)}")
        text = " | ".join(parts)
        if self.v3 is not None:
            self.v3[(_s(ev.get("event_id")), text)] = _v3_event(ev, self.excerpt)
        return text

    def since_tail(self, since: str, cur_ep: int | None) -> str:
        """v2: ' · N화, 경과 M−N화'(현재 회차를 모르면 ' · N화'). since 회차를 모르면 빈 문자열."""
        if self.legacy:
            return ""
        n = self.episode_of(since)
        if n is None:
            return ""
        return f" · {n}화" + (f", 경과 {cur_ep - n}화" if cur_ep is not None else "")


# ---------------------------------------------------------------- 가설

def _hypotheses(ev: dict, cx: _Ctx, cur_ep: int | None) -> list[tuple[str | None, str | None, str, str]]:
    """(entity, field, text, head). assumed_states 항목들 + 사건 자체. head 는 관련도 계산용 가설 머리
    (상태 가설은 엔티티·필드·값·근거, 사건 가설은 제목·요약)."""
    names, n = cx.names, cx.excerpt
    mark = "" if cx.legacy or cur_ep is None else f"[{cur_ep}화] "
    tail = []
    if _s(ev.get("summary")):
        tail.append(f"사건: {_s(ev.get('summary'))}")
    if _s(ev.get("source_text")):
        tail.append(f"원문: {_cut(_s(ev.get('source_text')), n)}")
    out: list[tuple[str | None, str | None, str, str]] = []
    for a in ev.get("assumed_states") or []:
        ent, field = a.get("entity"), a.get("field")
        if not ent or not field:
            continue
        head = f"{names.get(ent, ent)}의 {field}: {_s(a.get('assumes'))}"
        if _s(a.get("source_text")):
            head += f" (근거: {_cut(_s(a.get('source_text')), n)})"
        out.append((ent, field, mark + " | ".join([head] + tail), head))
        if cx.v3 is not None:
            src = _s(a.get("source_text"))
            cx.v3[("hyp", out[-1][2])] = (mark + (f"{_quoted(src, n)} → " if src else "")
                                          + f"{names.get(ent, ent)}의 {_fko(field)}: {_v3_ids(_s(a.get('assumes')), names)}")
    ev_head = f"사건 {_s(ev.get('title'))}".strip()
    out.append((None, None, mark + " | ".join([ev_head] + tail),
                f"{_s(ev.get('title'))} {_s(ev.get('summary'))}".strip()))
    if cx.v3 is not None:
        cx.v3[("hyp", out[-1][2])] = mark + _v3_event({k: v for k, v in ev.items() if k != "episode"}, n)
    return out


# ---------------------------------------------------------------- 전제

def _ledger_premises(ledger, tl: str, ent: str, field: str | None, cx: _Ctx,  # noqa: ANN001
                     cur_ep: int | None, ev_id: str) -> list[tuple[str, str]]:
    """(premise_id, text). field=None 이면 그 엔티티의 모든 필드."""
    fields = ledger.state.get(tl, {}).get(ent, {}) or {}
    wanted = [field] if field else sorted(k for k in fields if k != "_promoted")
    names = cx.names
    name = names.get(ent, ent)
    out: list[tuple[str, str]] = []

    def line(f: str, prov: dict, mark: str = "") -> tuple[str, str]:
        since = _s(prov.get("since"))
        text = (f"{name}.{f}{mark} = {_s(prov.get('value'))} "
                f"(since {since}{cx.since_tail(since, cur_ep)}, certainty {_s(prov.get('certainty'))})")
        if prov.get("volatility") == "transient":
            text += f" [일시적 — 해소 시 {_s(prov.get('decays_to'))}]"
        q = cx.quote(prov.get("since"))
        if q:
            text += f" | 발췌: {q}"
        if not cx.legacy and since:
            nxt = cx.next_event(ent, since, ev_id)
            if nxt is not None:
                ep = f"[{nxt['episode']}화] " if nxt.get("episode") is not None else ""
                text += " | 이후: " + _cut(f"{ep}{_s(nxt.get('title'))}: {_s(nxt.get('summary'))}",
                                         NEXT_EVENT_CHARS)
        pid = since or f"ledger:{ent}.{f}"
        if cx.v3 is not None:
            ep = cx.episode_of(since)
            val = prov.get("v3_value") or _v3_value(f, prov.get("value"), names)
            t3 = (f"{ep}화 기준 " if ep is not None else "") + f"{name}의 {_fko(f)}: {val}"
            if mark:
                t3 += " (앞서 추정된 전제)"
            if prov.get("volatility") == "transient":
                t3 += f" (일시적이며, 해소되면 {_v3_value(f, prov.get('decays_to'), names)})"
            t3 = _sentence(t3)
            q = cx.quote(prov.get("since"))
            if q:
                t3 += f" 원문: “{q}”"
            cx.v3[(pid, text)] = t3
        return pid, text

    for f in wanted:
        prov = fields.get(f) if f else None
        if not isinstance(prov, dict):
            continue
        if f == "possesses" and "value" not in prov:
            for oid in sorted(prov):
                p = prov[oid]
                st = " [상실됨]" if p.get("status") == "removed" else ""
                lost = " (지금은 잃었다)" if p.get("status") == "removed" else ""
                out.append(line(f, {**p, "value": f"{names.get(oid, oid)}{st}",
                                    "v3_value": f"{names.get(oid, oid)}{lost}"}))
        elif f == "knows" and "accumulated" in prov:
            for h in prov.get("accumulated") or []:
                out.append(line(f, h))
        else:
            out.append(line(f, prov))
    promoted = fields.get("_promoted") or {}
    for f in ([field] if field else sorted(promoted)):
        p = promoted.get(f) if f else None
        if isinstance(p, dict):
            out.append(line(f, p, " [승격 전제]"))
    return out


def _checker_premises(items: list[dict], ev_id: str, ent: str | None, field: str | None,
                      names: dict, v3: dict | None = None) -> list[tuple[str, str]]:
    out = []
    for it in items:
        if not it.get("needs_judge") or it.get("event_id") != ev_id:
            continue
        if ent is not None and (it.get("entity") != ent or it.get("field") != field):
            continue
        e = it.get("entity")
        text = (f"[검수 요망] {names.get(e, e)}.{_s(it.get('field'))} — 전제 {_s(it.get('premise'))} / "
                f"원장 {_s(it.get('ledger_value'))} (since {_s(it.get('since'))})")
        if _s(it.get("detail")):
            text += f" {_s(it.get('detail'))}"
        pid = _s(it.get("since")) or f"checker:{e}.{_s(it.get('field'))}"
        if v3 is not None:
            f = _s(it.get("field"))
            v3[(pid, text)] = (f"{names.get(e, e)}의 {_fko(f)}: 전제 {_v3_ids(_s(it.get('premise')), names)} / "
                               f"원장 {_v3_value(f, it.get('ledger_value'), names)}")
        out.append((pid, text))
    return out


def _rule_premises(registry: dict | None, v3: dict | None = None) -> list[tuple[str, str]]:
    out = []
    for i, r in enumerate((registry or {}).get("worldview_rules") or [], 1):
        content = _s(r.get("content"))
        if content:
            rid = _s(r.get("id") or r.get("rule_id")) or f"RULE_{i:03d}"
            out.append((rid, f"세계관 규칙 {rid}: {content}"))
            if v3 is not None:
                v3[out[-1]] = f"세계관 규칙: {content}"
    return out


def _knowledge_premises(ledger, chars: list[str], cx: _Ctx,  # noqa: ANN001
                        cur_ep: int | None) -> list[tuple[str, str]]:
    out = []
    for ch in chars:
        for k in ledger.knowledge.get(ch) or []:
            since = _s(k.get("since"))
            held = "" if k.get("retained", True) else " [보유하지 않음]"
            text = (f"{cx.names.get(ch, ch)}이(가) 앎{held}: {_s(k.get('info'))} "
                    f"(via {_s(k.get('via'))}, since {since}{cx.since_tail(since, cur_ep)})")
            q = cx.quote(k.get("since"))
            if q:
                text += f" | 발췌: {q}"
            pid = since or f"knowledge:{ch}"
            if cx.v3 is not None:
                ep = cx.episode_of(since)
                verb = "안다" if k.get("retained", True) else "알았으나 지금은 모른다"
                cx.v3[(pid, text)] = (f"{cx.names.get(ch, ch)}은(는) {_s(k.get('info'))}을(를) {verb}"
                                      + (f" ({ep}화)" if ep is not None else ""))
            out.append((pid, text))
    return out


def _sorted(kind: str, items: list[tuple[str, str]]) -> list[Premise]:
    """(kind, id, text) 로, ID → 본문 정렬, 완전 중복 제거."""
    return [(kind, i, t) for i, t in sorted(set(items))]


def _recent(kind: str, items: list[tuple[str, str]], cx: _Ctx) -> list[Premise]:
    """최근 순(근거 사건의 global_idx 내림차순, 동률은 ID → 본문). 지식이 많은 인물에서 상한에 걸려도
    최신 지식이 남게 한다."""
    def idx(pid: str) -> int:
        g = cx.gidx(pid)
        return -1 if g is None else g
    return [(kind, i, t) for i, t in sorted(set(items), key=lambda x: (-idx(x[0]), x[0], x[1]))]


def _by_relevance(items: list[Premise], head: str) -> list[Premise]:
    """(-관련도, 기존 순서). 기존 순서가 이미 완전한 키(ID·최근 순 → 본문)라 동률은 거기서 깨진다."""
    keyed = sorted((-_relevance(head, x[2]), i) for i, x in enumerate(items))
    return [items[i] for _, i in keyed]


def _select(by_kind: dict[str, list[Premise]], quotas: dict[str, int | None], cap: int,
            hard: dict[str, int]) -> list[Premise]:
    """할당량 선택. 칸이 남으면 다음 kind 로 넘기고, 끝까지 남으면 같은 kind 순서로 남은 전제를 채운다.
    hard: kind → 넘김·채움 없이 이 수가 끝(사건 가설의 ledger). 같은 (id, 본문)은 앞선 kind 에서 한 번만."""
    kinds = list(quotas)
    seen: set[tuple[str, str]] = set()
    pools: dict[str, list[Premise]] = {}
    for k in kinds:
        pool = []
        for x in by_kind.get(k, []):
            if (x[1], x[2]) not in seen:
                seen.add((x[1], x[2]))
                pool.append(x)
        pools[k] = pool[:hard[k]] if k in hard else pool
    taken = {k: 0 for k in kinds}
    total = carry = 0
    for k in kinds:
        q = quotas[k]
        want = len(pools[k]) if q is None else min(len(pools[k]), q + carry)
        n = min(want, cap - total)
        taken[k] = n
        total += n
        if q is not None:
            carry = q + carry - n
    for k in kinds:  # 마지막 kind 뒤에도 남은 칸
        if total >= cap:
            break
        extra = min(len(pools[k]) - taken[k], cap - total)
        taken[k] += extra
        total += extra
    return [x for k in kinds for x in pools[k][:taken[k]]]


# ---------------------------------------------------------------- 본체

def build_pairs(group: dict, ledger, corpus: list[dict], registry: dict | None,  # noqa: ANN001
                checker_items: list[dict] | None, *, max_premises: int | None = None,
                quotas: dict[str, int | None] | None = QUOTAS, excerpt_chars: int = EXCERPT_CHARS,
                current_episode: int | None = None, render: str = "v2") -> list[Pair]:
    """판정 패킷(group = {"key", "events": [...]}) 하나의 쌍 목록. 같은 입력 → 같은 목록(순서까지).
    render: v2(기본, PAIRS_VERSION 2 바이트 그대로) | v3(같은 선택, 자연어 글 — PAIRS_VERSION_V3).

    group 의 사건과 corpus 는 정규화(ID 치환)가 끝난 것이어야 한다(run_checks 가 넘기는 그대로).
    max_premises: 가설당 전제 수. None 이면 v2 24 / v1(quotas=None) 32.
    quotas=None: 개정 전(v1) 출력 그대로. current_episode: 사건에 episode 가 없을 때 쓰는 현재 회차.
    """
    legacy = quotas is None
    cap = max_premises if max_premises is not None else (LEGACY_MAX_PREMISES if legacy else MAX_PREMISES)
    names = _names(registry)
    if render not in RENDERS:
        raise ValueError(f"render 는 {sorted(RENDERS)}: {render!r}")
    if render == "v3" and legacy:
        raise ValueError("render=v3 는 v2 선택(quotas)과만 쓴다")
    cx = _Ctx(corpus, names, excerpt_chars, legacy)
    if render == "v3":
        cx.v3 = {}
    items = list(checker_items or [])
    rules = _sorted("rule", _rule_premises(registry, cx.v3))
    evs = list(group.get("events") or [])
    pairs: list[Pair] = []

    for ev in evs:
        ev_id = _s(ev.get("event_id"))
        cur_ep = _episode(ev.get("episode"))
        if cur_ep is None:
            cur_ep = _episode(current_episode)
        tl = _timeline(ledger, ev)
        ev_ents = cx.ents(ev)
        knowledge = _recent("knowledge", _knowledge_premises(ledger, _participants(ev), cx, cur_ep), cx)
        scene = _sorted("scene", [(_s(o.get("event_id")), cx.event_line(o)) for o in evs
                                  if o.get("event_id") != ev.get("event_id")])

        for ent, field, hyp, head in _hypotheses(ev, cx, cur_ep):
            targets = [ent] if ent else ev_ents
            led: list[tuple[str, str]] = []
            for t in targets:
                led += _ledger_premises(ledger, tl, t, field, cx, cur_ep, ev_id)
            tset = set(targets)
            past_ev = [p for p in cx.corpus if p.get("event_id") != ev.get("event_id")
                       and tset & set(cx.ents(p))]
            past_ev.sort(key=lambda p: (-(p.get("global_idx") or 0), _s(p.get("event_id"))))
            checker = _sorted("checker", _checker_premises(items, ev_id, ent, field, names, cx.v3))
            ledger_p = _sorted("ledger", led)
            past: list[Premise] = [("past", _s(p.get("event_id")), cx.event_line(p)) for p in past_ev]

            if legacy:
                chosen: list[Premise] = []
                seen: set[tuple[str, str]] = set()
                for x in checker + ledger_p + rules + knowledge + scene + past:
                    if (x[1], x[2]) in seen:
                        continue
                    seen.add((x[1], x[2]))
                    chosen.append(x)
                    if len(chosen) >= cap:
                        break
            else:
                by_kind = {"checker": checker,
                           "ledger": _by_relevance(ledger_p, head),
                           "knowledge": _by_relevance(knowledge, head),
                           "rule": _by_relevance(rules, head),
                           "past": _by_relevance(past, head),
                           "scene": _by_relevance(scene, head)}
                q = dict(quotas)
                hard = {"ledger": int(q["ledger"])} if field is None and q.get("ledger") is not None else {}
                chosen = _select(by_kind, q, cap, hard)

            if cx.v3 is not None:
                hyp_out = cx.v3[("hyp", hyp)]
                chosen = [(kind, pid, cx.v3[(pid, text)]) for kind, pid, text in chosen]
            else:
                hyp_out = hyp
            for kind, pid, text in chosen:
                pairs.append(Pair(hypothesis_text=hyp_out, premise_text=text, premise_id=pid,
                                  premise_kind=kind, entity=ent, field=field, event_id=ev_id))
    return pairs
