# -*- coding: utf-8 -*-
"""
weave_registry.py — NEW: 라벨 → 정식 ID 자동 발급 + 레지스트리 병합 (제품 계층)

코어(ledger.load_registry / normalize)는 "NEW:라벨 → 정식 ID" 매핑이 **이미 있다**고
전제한다. 신규 화를 추출하면 그 매핑이 아직 없으므로, 이 모듈이 그 간극을 메운다.

핵심 원칙
---------
1. **애매하면 발급하지 않는다.** 자동 병합은 확신이 높을 때만 하고,
   그 외에는 검토 큐로 넘겨 사람이 정한다. 잘못된 병합은 원장을 오염시키고
   되돌리기 어렵다 (같은 인물이 두 ID로 갈리거나, 다른 인물이 한 ID로 합쳐진다).
2. **삭제하지 않는다.** 기존 항목의 id·name을 바꾸지 않는다. 별칭(aliases)은 추가만 한다.
3. **결정론적이다.** LLM을 호출하지 않는다. 같은 입력 → 같은 결과.

매칭 단계 (위에서부터, 먼저 걸리는 것 채택)
-------------------------------------------
  exact_label   기존 new_label과 정확히 같다                    → 자동
  exact_name    이름/표현이 정규화 후 정확히 같다                → 자동
  alias         기존 aliases 중 하나와 정확히 같다               → 자동
  contains      한쪽이 다른 쪽을 포함하고 길이차가 크지 않다      → 검토 큐 (후보 1순위)
  token         토큰 자카드 >= AMBIGUOUS_MIN                    → 검토 큐 (후보 목록)
  none          아무것도 안 걸림                                 → 신규 ID 자동 발급

(B-025) 세계관 규칙 개정 이력
-----------------------------
규칙 항목에 선택 필드 `valid_from_event` · `superseded_by`(규칙 ID) · `revision_of`(규칙 ID)를 둔다.
추출 결과의 `rule_revisions`(추출 프롬프트 v2.4)는 **새 규칙 추가 + 구 규칙에 superseded_by 표기**로만
반영한다 — 구 규칙의 id·content를 바꾸거나 지우지 않는다. 개정 대상(revises)이 확실히 해소되지
않거나 이미 다른 규칙으로 개정된 경우는 발급하지 않고 검토 큐로 보낸다 (애매하면 발급 안 함).
`rule_revisions`가 없는 추출 결과(v2.3 이하)는 기존 동작과 같다.

"검토 큐로" 간 항목은 **임시 ID(PENDING_ 접두)**를 받고, 사용자가 확정하기 전까지
원장에 들어가지 않는다 (weave_pipeline이 확정 전 원장 갱신을 하지 않으므로 안전).
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

# 컬렉션 이름 → (ID 접두, 표시 이름 필드)
COLLECTIONS = {
    "characters": ("CHAR", "name"),
    "locations": ("LOC", "name"),
    "objects": ("OBJ", "name"),
    "times": ("TIME", "expression"),
    "worldview_rules": ("RULE", "content"),
}

PREFIX_TO_COLLECTION = {p: c for c, (p, _) in COLLECTIONS.items()}

# (B-025) 규칙 개정 이력 필드 — 전부 선택. 없으면 개정 이력 없는 규칙
RULE_REVISION_FIELDS = ("valid_from_event", "superseded_by", "revision_of")

# 자동 병합 없이 사람에게 물어보는 유사도 하한
AMBIGUOUS_MIN = 0.34
# contains 판정에서 허용하는 길이 비율 (짧은 쪽 / 긴 쪽)
CONTAINS_MIN_RATIO = 0.4

_TOKEN = re.compile(r"[가-힣A-Za-z0-9]+")
_NEW_LABEL = re.compile(r"NEW:[^\s,\"'\]\}]+")


def norm_text(s: str) -> str:
    """비교용 정규화: NFKC, 소문자, 밑줄/공백 제거, 괄호 주석 제거."""
    s = unicodedata.normalize("NFKC", str(s or "")).strip()
    s = re.sub(r"\([^)]*\)", " ", s)          # "슐라우 (Schlau)" → "슐라우"
    s = s.replace("_", " ")
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def tokens(s: str) -> set:
    return set(_TOKEN.findall(norm_text(s)))


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def label_body(label: str) -> str:
    """'NEW:노이즈_옷의_범인' → '노이즈 옷의 범인'"""
    s = str(label or "")
    if s.startswith("NEW:"):
        s = s[4:]
    return norm_text(s)


# ---------------------------------------------------------------- 레지스트리

def empty_registry(description: str = "") -> dict:
    reg = {
        "description": description or "Weave 엔티티 레지스트리",
        "schema_version": 2,
        "last_episode": 0,
    }
    for coll in COLLECTIONS:
        reg[coll] = []
    return reg


def load_registry_file(path: Path) -> dict:
    if Path(path).exists():
        return json.loads(Path(path).read_text(encoding="utf-8"))
    return empty_registry()


def next_id(reg: dict, coll: str) -> str:
    prefix = COLLECTIONS[coll][0]
    nums = []
    for item in reg.get(coll, []):
        m = re.match(rf"{prefix}_(\d+)$", str(item.get("id", "")))
        if m:
            nums.append(int(m.group(1)))
    return f"{prefix}_{(max(nums) + 1) if nums else 1:03d}"


def _display(item: dict, coll: str) -> str:
    return str(item.get(COLLECTIONS[coll][1]) or item.get("name") or "")


def collection_of_label(label: str, hint: str | None = None) -> str | None:
    """NEW: 라벨이 어느 컬렉션인지. 추출 JSON의 위치(hint)가 가장 신뢰도 높다."""
    if hint in COLLECTIONS:
        return hint
    return None


# ---------------------------------------------------------------- 매칭

def match_candidates(name: str, reg: dict, coll: str) -> list[dict]:
    """기존 항목 중 유사한 것들을 점수 내림차순으로."""
    target_n = norm_text(name)
    target_t = tokens(name)
    out = []
    for item in reg.get(coll, []):
        names = [_display(item, coll)] + list(item.get("aliases") or [])
        if item.get("new_label"):
            names.append(label_body(item["new_label"]))
        best, how = 0.0, "token"
        for cand in names:
            cn = norm_text(cand)
            if not cn:
                continue
            if cn == target_n:
                best, how = 1.0, "exact_name"
                break
            if target_n and cn and (target_n in cn or cn in target_n):
                ratio = min(len(target_n), len(cn)) / max(len(target_n), len(cn))
                if ratio >= CONTAINS_MIN_RATIO and best < 0.9:
                    best, how = 0.9, "contains"
            j = jaccard(target_t, tokens(cand))
            if j > best:
                best, how = j, "token"
        if best > 0:
            out.append({"id": item["id"], "name": _display(item, coll),
                        "score": round(best, 3), "how": how})
    out.sort(key=lambda x: (-x["score"], x["id"]))
    return out


def resolve_one(label: str, reg: dict, coll: str) -> dict:
    """NEW: 라벨 하나를 해소한다.

    반환: {"label", "collection", "decision", "id", "candidates", "reason"}
      decision = "matched"    기존 ID로 병합 (자동)
               | "new"        신규 ID 발급 (자동)
               | "ambiguous"  후보가 있으나 확신 부족 → 검토 큐 (임시 ID)
    """
    body = label_body(label)

    # 1) new_label 정확 일치 — 같은 작품 안에서 이미 발급된 라벨
    for item in reg.get(coll, []):
        if item.get("new_label") == label:
            return {"label": label, "collection": coll, "decision": "matched",
                    "id": item["id"], "candidates": [], "reason": "exact_label"}

    cands = match_candidates(body, reg, coll)
    top = cands[0] if cands else None

    # 2) 이름/별칭 정확 일치
    if top and top["how"] == "exact_name" and top["score"] >= 1.0:
        return {"label": label, "collection": coll, "decision": "matched",
                "id": top["id"], "candidates": cands[:3], "reason": "exact_name"}

    # 3) 애매 — 사람에게
    if top and top["score"] >= AMBIGUOUS_MIN:
        return {"label": label, "collection": coll, "decision": "ambiguous",
                "id": None, "candidates": cands[:5],
                "reason": f"{top['how']} 유사({top['score']}) — 자동 병합하지 않음"}

    # 4) 신규
    return {"label": label, "collection": coll, "decision": "new",
            "id": None, "candidates": cands[:3], "reason": "유사 항목 없음"}


# ---------------------------------------------------------------- 수집·병합

def collect_labels(doc: dict) -> dict:
    """추출 결과 JSON에서 NEW: 라벨을 컬렉션별로 모은다.

    라벨이 어느 컬렉션인지는 **선언된 위치**로 정한다 (characters 배열의
    character_id면 characters). 사건 본문 문자열에만 등장하는 라벨은
    선언이 없다는 뜻이므로 'unknown'으로 모아 검토 큐에 올린다.
    """
    found = {c: [] for c in COLLECTIONS}
    found["unknown"] = []
    seen = set()

    def add(coll, label):
        if label and label.startswith("NEW:") and (coll, label) not in seen:
            seen.add((coll, label))
            found[coll].append(label)

    for item in doc.get("characters", []) or []:
        add("characters", item.get("character_id"))
    for item in doc.get("locations", []) or []:
        add("locations", item.get("location_id"))
    for item in doc.get("objects", []) or []:
        add("objects", item.get("object_id"))
    for item in doc.get("times", []) or []:
        add("times", item.get("time_id"))
    for item in doc.get("worldview_rules", []) or []:
        add("worldview_rules", item.get("rule_id"))

    declared = {lb for c in COLLECTIONS for lb in found[c]}
    # (B-025) rule_revisions의 new_rule_id·revises는 apply_rule_revisions가 처리한다
    declared |= {r.get(k) for r in doc.get("rule_revisions", []) or []
                 for k in ("new_rule_id", "revises") if r.get(k)}
    blob = json.dumps(doc, ensure_ascii=False)
    for lb in sorted(set(_NEW_LABEL.findall(blob))):
        if lb in declared:
            continue
        # 선언된 라벨의 조각이면 건너뛴다.
        #  - 라벨에 공백이 있으면 정규식이 앞부분만 잘라 오고("NEW:옥상 정원" → "NEW:옥상")
        #  - 본문에 조사가 붙으면 뒤가 늘어난다("NEW:그림자" → "NEW:그림자가")
        if any(d.startswith(lb) or lb.startswith(d) for d in declared):
            continue
        add("unknown", lb)
    return found


def _entry_for(doc: dict, coll: str, label: str) -> dict:
    """추출 결과에서 그 라벨의 선언부를 찾아 레지스트리 항목 재료를 만든다."""
    key = {"characters": "character_id", "locations": "location_id",
           "objects": "object_id", "times": "time_id",
           "worldview_rules": "rule_id"}[coll]
    for item in doc.get(coll, []) or []:
        if item.get(key) == label:
            return item
    return {}


def merge(doc: dict, reg: dict, episode: int) -> dict:
    """추출 결과의 NEW: 라벨을 해소해 (매핑, 신규 등재분, 검토 대상)을 만든다.

    **레지스트리를 파일로 저장하지 않는다.** 호출자가 결과를 보고 결정한다.
    반환 dict의 "registry"는 자동 확정분만 반영한 사본이다.
    """
    import copy
    reg = copy.deepcopy(reg)
    found = collect_labels(doc)
    mapping, added, pending = {}, [], []

    for coll in COLLECTIONS:
        for label in found[coll]:
            r = resolve_one(label, reg, coll)
            if r["decision"] == "matched":
                mapping[label] = r["id"]
                # 별칭 보강 (삭제 없이 추가만)
                for item in reg[coll]:
                    if item["id"] == r["id"]:
                        al = item.setdefault("aliases", [])
                        body = label_body(label)
                        if body and body not in [norm_text(a) for a in al] \
                                and norm_text(_display(item, coll)) != body:
                            al.append(label_body(label))
                r["applied"] = True
            elif r["decision"] == "new":
                src = _entry_for(doc, coll, label)
                new_id = next_id(reg, coll)
                entry = {"id": new_id, "new_label": label,
                         "first_episode": episode}
                if coll == "times":
                    entry["expression"] = src.get("expression") or label_body(label)
                    entry["timeline"] = src.get("timeline") or "unknown"
                elif coll == "worldview_rules":
                    entry["content"] = src.get("content") or label_body(label)
                    entry["certainty"] = src.get("certainty") or "inferred"
                    if src.get("valid_from_event"):
                        entry["valid_from_event"] = src["valid_from_event"]
                else:
                    entry["name"] = src.get("name") or label_body(label)
                    if coll == "characters":
                        entry["entity_type"] = src.get("entity_type") or "unknown"
                    if src.get("aliases"):
                        entry["aliases"] = list(src["aliases"])
                if src.get("description"):
                    entry["note"] = src["description"]
                reg[coll].append(entry)
                mapping[label] = new_id
                added.append(entry | {"_collection": coll})
                r["id"] = new_id
                r["applied"] = True
            else:
                r["applied"] = False
                pending.append(r)

    for label in found["unknown"]:
        # 어느 컬렉션인지 모르므로 전 컬렉션에서 후보를 찾아 보여준다.
        cands = []
        for coll in COLLECTIONS:
            for c in match_candidates(label_body(label), reg, coll)[:2]:
                cands.append(c | {"collection": coll})
        cands.sort(key=lambda x: -x["score"])
        pending.append({"label": label, "collection": "unknown", "decision": "ambiguous",
                        "id": None, "candidates": cands[:5], "applied": False,
                        "reason": "선언부 없이 본문에만 등장 — 종류를 알 수 없음 "
                                  "(조사가 붙었거나 추출기가 선언을 빠뜨렸을 수 있다)"})

    if doc.get("rule_revisions"):
        apply_rule_revisions(reg, doc["rule_revisions"], episode, mapping, added, pending)

    reg["last_episode"] = max(int(reg.get("last_episode") or 0), int(episode))
    return {"registry": reg, "mapping": mapping, "added": added, "pending": pending,
            "labels_found": {k: v for k, v in found.items() if v}}


def apply_rule_revisions(reg: dict, revisions: list[dict], episode: int,
                         mapping: dict | None = None, added: list | None = None,
                         pending: list | None = None) -> dict:
    """(B-025) 규칙 개정 쌍을 레지스트리에 반영한다 (reg를 제자리 수정).

    revision = {"revises": RULE_xxx | NEW:라벨, "new_rule_id": NEW:라벨, "content",
                "valid_from_event", "certainty", ...}
    - revises가 기존 규칙 ID 또는 매핑된 NEW: 라벨로 **정확히** 해소되고, 그 규칙이 아직
      개정되지 않았을 때만 새 규칙을 발급한다. 구 규칙에는 superseded_by만 추가한다.
    - 그 외(해소 불가·이미 개정됨)는 검토 큐로 보낸다 — 발급하지 않는다.
    """
    mapping = mapping if mapping is not None else {}
    added = added if added is not None else []
    pending = pending if pending is not None else []
    rules = reg.setdefault("worldview_rules", [])
    by_id = {r["id"]: r for r in rules}
    by_label = {r["new_label"]: r for r in rules if r.get("new_label")}
    for rv in revisions or []:
        target = rv.get("revises")
        old = by_id.get(target) or by_label.get(target) or by_id.get(mapping.get(target, ""))
        label = rv.get("new_rule_id")
        if old is None or old.get("superseded_by"):
            pending.append({"label": label, "collection": "worldview_rules",
                            "decision": "ambiguous", "id": None, "candidates": [],
                            "applied": False, "revises": target,
                            "reason": ("개정 대상 규칙을 찾을 수 없음" if old is None else
                                       f"이미 {old.get('superseded_by')}로 개정된 규칙")})
            continue
        new_id = next_id(reg, "worldview_rules")
        entry = {"id": new_id, "first_episode": episode,
                 "content": rv.get("content") or label_body(label or new_id),
                 "certainty": rv.get("certainty") or "inferred",
                 "revision_of": old["id"]}
        if label:
            entry["new_label"] = label
            mapping[label] = new_id
        if rv.get("valid_from_event"):
            entry["valid_from_event"] = rv["valid_from_event"]
        if rv.get("source_text"):
            entry["note"] = rv["source_text"]
        old["superseded_by"] = new_id          # 구 규칙은 지우지 않고 표기만
        rules.append(entry)
        by_id[new_id] = entry
        added.append(entry | {"_collection": "worldview_rules"})
    return reg


def apply_decision(reg: dict, label: str, coll: str, decision: dict,
                   episode: int, doc: dict | None = None) -> tuple[dict, str]:
    """검토 큐에서 사람이 내린 결정을 레지스트리에 반영한다.

    decision = {"action": "merge", "id": "CHAR_003"}  기존 항목에 병합
             | {"action": "new", "collection": "characters", "name": "..."} 신규 발급
    """
    import copy
    reg = copy.deepcopy(reg)
    action = decision.get("action")
    if action == "merge":
        target = decision["id"]
        coll = coll if coll in COLLECTIONS else PREFIX_TO_COLLECTION.get(target.split("_")[0])
        for item in reg.get(coll, []):
            if item["id"] == target:
                al = item.setdefault("aliases", [])
                body = label_body(label)
                if body and body not in [norm_text(a) for a in al]:
                    al.append(body)
        return reg, target
    coll = decision.get("collection") or coll
    if coll not in COLLECTIONS:
        raise ValueError(f"컬렉션을 알 수 없습니다: {coll}")
    new_id = next_id(reg, coll)
    entry = {"id": new_id, "new_label": label, "first_episode": episode}
    name = decision.get("name") or label_body(label)
    if coll == "times":
        entry["expression"] = name
        entry["timeline"] = decision.get("timeline") or "unknown"
    elif coll == "worldview_rules":
        entry["content"] = name
        entry["certainty"] = decision.get("certainty") or "inferred"
    else:
        entry["name"] = name
        if coll == "characters":
            entry["entity_type"] = decision.get("entity_type") or "unknown"
    reg[coll].append(entry)
    return reg, new_id


def mapping_of(reg: dict) -> dict:
    """ledger.normalize()에 넘길 NEW:라벨 → 정식 ID 매핑."""
    m = {}
    for coll in COLLECTIONS:
        for item in reg.get(coll, []):
            if item.get("new_label"):
                m[item["new_label"]] = item["id"]
    return m
