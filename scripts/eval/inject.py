"""학습 전용 구조 주입 생성기 — 학습 judge 의 (가설, 전제) 쌍 학습 데이터 원료를 만든다.

**평가에는 쓰지 않는다.** 평가는 Weave 4작품 실제 코퍼스로 한다(docs 계획서 "사전 확정").
구조 주입 평가셋은 "정답 조준" 함정(Phase 4)을 재현하고 생성기 템플릿이 누수되기 때문이다.

기반 작품(학습 작품)의 실제 원장 위에, 사례(case)마다
  setup_events  6화로 재생할 사건(주입한 상태·지식·장면) — 원장에 들어간다
  rules         이 사례에서만 레지스트리에 더하는 세계관 규칙
  test_events   7화 한 장면(판정 대상)
  labels        (event_id, premise_id) → 모순 | 정상 | 무관   ← 정답은 주입 과정에서 확정
를 만든다. 대조 묶음(contrast_group): 같은 전제(setup)로 모순·정상·무관 변형을 함께 낸다.

연산자(OPERATORS)는 함수 하나씩 — 하나를 통째로 빼서(--exclude) 홀드아웃할 수 있다.
  유형     연산자                 내용
  state    state_contrast         자유 서술 상태(body_status·ability): 모순/정상/무관
  state    state_recovery         [어려운 음성] 부상 뒤 회복 사건을 끼움 → 정상
  state    state_gradual          [어려운 음성] 점진적 변화(서서히 악화) → 정상
  state    state_scene_change     [어려운 음성] 장면 전환으로 상태가 풀림(원문에 근거) → 정상
  rule     rule_contrast          세계관 규칙: 위반/준수/무관
  rule     rule_exception         [어려운 음성] 규칙에 명시된 예외 상황 → 정상
  knowledge knowledge_contrast    '못 들었다'는 지식이 있는 인물이 비밀을 앎 → 모순 / 아는 인물 → 정상 / 무관
  knowledge knowledge_heard       [어려운 음성] 나중에 전해 들은 장면을 끼움 → 정상

실행:
  PYTHONIOENCODING=utf-8 python scripts/eval/inject.py --work sherlock \\
      --out build/judge_inject/manifest_sherlock.json --per-operator 40 --seed 7 [--exclude knowledge_heard]
레지스트리(인물·장소 이름의 출처): --registry <파일> > --work <작품>(judge_eval 의 레지스트리: wm entity_registry.json,
q1·q3·셜록 registry_live.json) > --base <폴더>(entity_registry.json → registry_live.json 순으로 찾는다).
**누수 규칙(설계안 §0-6·§2-6)**: 접기의 주입은 그 접기의 학습 작품 레지스트리로만 만든다 — 평가 작품의 인물 이름이
들어간 주입은 누수다. manifest 에 registry 경로·sha256 을 남긴다.
같은 인자 → 같은 파일(난수는 --seed 로 고정).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path
from typing import Callable

LABEL_CONTRA, LABEL_OK, LABEL_UNREL = "모순", "정상", "무관"
SETUP_EP, TEST_EP = 6, 7
TIMELINE = "future_world"

# ---------------------------------------------------------------- 소재(템플릿)
# (상태, 상태와 어긋나는 행동, 상태와 맞는 행동, 어긋나는 전제, 맞는 전제)
STATES = [
    ("body_status", "오른 손목 골절로 깁스를 함", "오른손으로 펜을 쥐고 또박또박 글씨를 썼다",
     "왼손으로 삐뚤빼뚤 글씨를 썼다", "오른손을 자유롭게 씀", "오른손을 쓰지 못해 왼손을 씀"),
    ("body_status", "왼쪽 다리 부상으로 걷지 못함", "계단을 두 칸씩 뛰어 올라갔다",
     "목발을 짚고 한 걸음씩 천천히 걸었다", "다리가 멀쩡해 뛸 수 있음", "다리를 절며 목발에 의지함"),
    ("body_status", "두 눈에 붕대를 감아 앞을 보지 못함", "멀리 걸린 간판 글씨를 소리 내어 읽었다",
     "소리에 귀를 기울여 방향을 가늠했다", "앞을 똑똑히 봄", "앞을 보지 못해 소리에 의지함"),
    ("body_status", "목을 다쳐 목소리가 나오지 않음", "큰 소리로 노래를 불렀다",
     "종이에 글을 써서 뜻을 전했다", "목소리를 크게 냄", "말 대신 글로 소통함"),
    ("body_status", "고열로 정신이 흐릿함", "복잡한 암산을 막힘없이 해냈다",
     "말을 더듬으며 겨우 대답했다", "정신이 또렷함", "정신이 흐려 말이 어눌함"),
    ("ability", "마력을 모두 잃어 마법을 쓸 수 없음", "손끝에서 불꽃 마법을 피워 올렸다",
     "마법 대신 맨손으로 문을 밀어 열었다", "마법을 자유롭게 씀", "마법 없이 맨손으로 해결함"),
    ("ability", "헤엄을 전혀 치지 못함", "깊은 강을 헤엄쳐 건넜다",
     "강가에서 배가 오기를 기다렸다", "능숙하게 헤엄침", "헤엄치지 못해 배를 기다림"),
    ("ability", "글을 읽을 줄 모름", "두꺼운 고문서를 술술 읽어 내려갔다",
     "다른 사람에게 편지를 대신 읽어 달라고 부탁했다", "글을 막힘없이 읽음", "글을 몰라 대신 읽어 달라 함"),
]
UNRELATED_ACTS = [
    ("창가에 앉아 내리는 비를 한참 바라보았다", "location", "창가"),
    ("동료와 저녁 메뉴를 두고 잠시 이야기를 나누었다", "location", "식당 앞"),
    ("오래된 시계의 태엽을 감았다", "location", "거실"),
    ("하늘의 구름 모양을 세어 보았다", "location", "옥상"),
]
RECOVERY = "치료를 마치고 완전히 회복함"
GRADUAL = [("body_status", "약간 피곤함", "꽤 지쳐 발걸음이 무거움", "몹시 지쳐 금방이라도 쓰러질 듯함",
            "며칠 동안 쉬지 못해 점점 더 지쳐 결국 벽에 기대 주저앉았다"),
           ("body_status", "가벼운 기침을 함", "기침이 잦아지고 열이 오름", "고열로 앓아누움",
            "감기가 점점 심해져 끝내 자리에 누워 일어나지 못했다")]
SCENE_CHANGE = [("body_status", "폭우를 맞아 온몸이 흠뻑 젖음", "옷이 보송하게 말라 있음",
                 "실내로 들어가 마른 옷으로 갈아입은 뒤 보송한 차림으로 차를 마셨다"),
                ("body_status", "진흙탕에 넘어져 얼굴과 옷이 진흙투성이", "깨끗한 차림",
                 "숙소에서 씻고 새 옷으로 갈아입은 뒤 깨끗한 차림으로 회의에 나타났다")]
RULES = [
    ("이 세계에는 화폐가 없고 물자는 대가 없이 나눈다", "상점 점원에게 동전을 건네고 거스름돈을 받았다",
     "창고에서 필요한 물자를 대가 없이 가져갔다", "단, 외부 교역소에서는 은화를 쓴다",
     "외부 교역소에서 은화를 건네고 소금을 샀다"),
    ("해가 진 뒤에는 마법을 쓸 수 없다", "한밤중에 마법으로 방 안을 환하게 밝혔다",
     "한밤중이라 마법 대신 횃불을 켰다", "단, 보름달이 뜬 밤에는 예외다",
     "보름달이 뜬 밤, 마법으로 방 안을 환하게 밝혔다"),
    ("성 안에서는 누구도 무기를 지닐 수 없다", "성 안 연회장에서 허리에 찬 칼을 뽑아 들었다",
     "성문 앞에서 칼을 맡기고 맨몸으로 들어갔다", "단, 왕의 친위대는 예외다",
     "왕의 친위대원으로서 성 안에서 칼을 차고 순찰했다"),
    ("도시의 모든 문은 자정에 닫히고 아침까지 열리지 않는다", "자정이 지난 새벽 두 시에 성문을 열고 밖으로 나갔다",
     "자정이 지나 성문이 닫혀 있어 아침까지 여관에 머물렀다", "단, 화재가 나면 문을 연다",
     "새벽에 큰불이 나자 성문이 열려 그 틈에 밖으로 나갔다"),
]
SECRETS = ["금고의 비밀번호가 0417이라는 것", "의뢰인이 사실은 왕의 동생이라는 것",
           "지하 통로가 서쪽 우물과 이어져 있다는 것", "배신자가 경비대장이라는 것"]


# ---------------------------------------------------------------- 공통

class Ctx:
    def __init__(self, registry: dict, rng: random.Random):
        self.rng = rng
        self.chars = [(c["id"], str(c.get("name", c["id"])).split(" (")[0])
                      for c in registry.get("characters") or [] if c.get("entity_type", "person") == "person"]
        self.locs = [(l["id"], str(l.get("name", l["id"])).split(" (")[0]) for l in registry.get("locations") or []]
        if len(self.chars) < 3 or not self.locs:
            raise SystemExit("기반 레지스트리에 인물 3명·장소 1곳 이상이 필요합니다.")

    def pick_chars(self, n: int) -> list[tuple[str, str]]:
        return self.rng.sample(self.chars, n)

    def loc(self) -> tuple[str, str]:
        return self.rng.choice(self.locs)


def _event(eid: str, order: int, title: str, summary: str, source: str, *, loc: str, chars: list[str],
           state_changes=None, assumed=None, knowledge=None, scene: str | None = None) -> dict:  # noqa: ANN001
    return {
        "event_id": eid, "order": order, "scene_id": scene, "title": title, "summary": summary,
        "location_id": loc, "time_id": "unknown", "timeline": TIMELINE, "reality_status": "actual",
        "participant_character_ids": chars, "witness_character_ids": [],
        "main_actor_character_id": chars[0] if chars else None, "objects": [],
        "state_changes": state_changes or [], "assumed_states": assumed or [],
        "knowledge_gained": knowledge or [], "cause": "unknown", "result": "unknown",
        "source_text": source, "certainty": "explicit",
    }


def _sc(entity: str, field: str, to: str, source: str) -> dict:
    return {"entity": entity, "field": field, "from": "unknown", "to": to, "source_text": source}


def _as(entity: str, field: str, assumes: str, source: str) -> dict:
    return {"entity": entity, "field": field, "assumes": assumes, "source_text": source}


def _case(cid: str, op: str, typ: str, group: str, setup: list, test: list, labels: list,
          rules: list | None = None) -> dict:
    return {"case_id": cid, "operator": op, "type": typ, "contrast_group": group,
            "setup_events": setup, "rules": rules or [], "test_events": test,
            "labels": [{"event_id": e, "premise_id": p, "label": lab} for e, p, lab in labels]}


T1 = f"EVENT_{TEST_EP}_1"


# ---------------------------------------------------------------- 연산자: state

def state_contrast(ctx: Ctx, i: int) -> list[dict]:
    field, state, bad, good, bad_as, good_as = STATES[i % len(STATES)]
    (a, an), (b, bn) = ctx.pick_chars(2)
    lid, ln = ctx.loc()
    s1 = f"EVENT_{SETUP_EP}_1"
    setup = [_event(s1, 1, f"{an}의 상태", f"{an}은(는) {state}.", f"{an}은(는) {state}.",
                    loc=lid, chars=[a], state_changes=[_sc(a, field, state, f"{an}은(는) {state}.")])]
    g = f"state_contrast-{i:04d}"
    unrel, ufield, uval = UNRELATED_ACTS[i % len(UNRELATED_ACTS)]
    out = []
    for tag, act, assumes, lab in (("c", bad, bad_as, LABEL_CONTRA), ("n", good, good_as, LABEL_OK)):
        src = f"{an}은(는) {act}."
        ev = _event(T1, 1, f"{an}의 행동", f"{ln}에서 {an}이(가) {act}.", src, loc=lid, chars=[a],
                    assumed=[_as(a, field, assumes, src)], scene="S_T")
        out.append(_case(f"{g}{tag}", "state_contrast", "state", g, setup, [ev], [(T1, s1, lab)]))
    src = f"{bn}은(는) {unrel}."
    ev = _event(T1, 1, f"{bn}의 한때", f"{bn}이(가) {unrel}.", src, loc=lid, chars=[b, a],
                assumed=[_as(b, ufield, uval, src)], scene="S_T")
    out.append(_case(f"{g}u", "state_contrast", "state", g, setup, [ev], [(T1, s1, LABEL_UNREL)]))
    return out


def state_recovery(ctx: Ctx, i: int) -> list[dict]:
    field, state, bad, _good, bad_as, _ = STATES[i % len(STATES)]
    (a, an), = ctx.pick_chars(1)
    lid, _ = ctx.loc()
    s1, s2 = f"EVENT_{SETUP_EP}_1", f"EVENT_{SETUP_EP}_2"
    setup = [_event(s1, 1, f"{an}의 부상", f"{an}은(는) {state}.", f"{an}은(는) {state}.", loc=lid, chars=[a],
                    state_changes=[_sc(a, field, state, f"{an}은(는) {state}.")]),
             _event(s2, 2, f"{an}의 회복", f"몇 달 뒤 {an}은(는) {RECOVERY}.", f"몇 달 뒤 {an}은(는) {RECOVERY}.",
                    loc=lid, chars=[a], state_changes=[_sc(a, field, RECOVERY, f"{an}은(는) {RECOVERY}.")])]
    src = f"{an}은(는) {bad}."
    ev = _event(T1, 1, f"{an}의 행동", src, src, loc=lid, chars=[a], assumed=[_as(a, field, bad_as, src)],
                scene="S_T")
    g = f"state_recovery-{i:04d}"
    return [_case(g, "state_recovery", "state", g, setup, [ev], [(T1, s2, LABEL_OK), (T1, s1, LABEL_OK)])]


def state_gradual(ctx: Ctx, i: int) -> list[dict]:
    field, v1, v2, v3, act = GRADUAL[i % len(GRADUAL)]
    (a, an), = ctx.pick_chars(1)
    lid, _ = ctx.loc()
    s1, s2 = f"EVENT_{SETUP_EP}_1", f"EVENT_{SETUP_EP}_2"
    setup = [_event(s1, 1, f"{an}의 상태", f"{an}은(는) {v1}.", f"{an}은(는) {v1}.", loc=lid, chars=[a],
                    state_changes=[_sc(a, field, v1, f"{an}은(는) {v1}.")]),
             _event(s2, 2, f"{an}의 상태 변화", f"{an}은(는) {v2}.", f"{an}은(는) {v2}.", loc=lid, chars=[a],
                    state_changes=[_sc(a, field, v2, f"{an}은(는) {v2}.")])]
    src = f"{an}은(는) {act}."
    ev = _event(T1, 1, f"{an}의 악화", src, src, loc=lid, chars=[a], assumed=[_as(a, field, v3, src)], scene="S_T")
    g = f"state_gradual-{i:04d}"
    return [_case(g, "state_gradual", "state", g, setup, [ev], [(T1, s2, LABEL_OK), (T1, s1, LABEL_OK)])]


def state_scene_change(ctx: Ctx, i: int) -> list[dict]:
    field, state, after, act = SCENE_CHANGE[i % len(SCENE_CHANGE)]
    (a, an), = ctx.pick_chars(1)
    (l1, _), (l2, _) = ctx.loc(), ctx.loc()
    s1 = f"EVENT_{SETUP_EP}_1"
    setup = [_event(s1, 1, f"{an}의 상태", f"{an}은(는) {state}.", f"{an}은(는) {state}.", loc=l1, chars=[a],
                    state_changes=[_sc(a, field, state, f"{an}은(는) {state}.")])]
    src = f"{an}은(는) {act}."
    ev = _event(T1, 1, f"{an}의 다음 장면", src, src, loc=l2, chars=[a], assumed=[_as(a, field, after, src)],
                scene="S_T")
    g = f"state_scene_change-{i:04d}"
    return [_case(g, "state_scene_change", "state", g, setup, [ev], [(T1, s1, LABEL_OK)])]


# ---------------------------------------------------------------- 연산자: rule

def _rule_id(i: int) -> str:
    return f"RULE_I{i:04d}"


def rule_contrast(ctx: Ctx, i: int) -> list[dict]:
    rule, bad, good, _exc, _exc_act = RULES[i % len(RULES)]
    (a, an), = ctx.pick_chars(1)
    lid, _ = ctx.loc()
    rid = _rule_id(i)
    rules = [{"id": rid, "content": rule, "certainty": "explicit", "first_episode": SETUP_EP}]
    g = f"rule_contrast-{i:04d}"
    unrel = UNRELATED_ACTS[i % len(UNRELATED_ACTS)][0]
    out = []
    for tag, act, lab in (("c", bad, LABEL_CONTRA), ("n", good, LABEL_OK), ("u", unrel, LABEL_UNREL)):
        src = f"{an}은(는) {act}."
        ev = _event(T1, 1, f"{an}의 행동", src, src, loc=lid, chars=[a], scene="S_T")
        out.append(_case(f"{g}{tag}", "rule_contrast", "rule", g, [], [ev], [(T1, rid, lab)], rules))
    return out


def rule_exception(ctx: Ctx, i: int) -> list[dict]:
    rule, bad, _good, exc, exc_act = RULES[i % len(RULES)]
    (a, an), = ctx.pick_chars(1)
    lid, _ = ctx.loc()
    rid = _rule_id(i)
    rules = [{"id": rid, "content": f"{rule}. {exc}", "certainty": "explicit", "first_episode": SETUP_EP}]
    g = f"rule_exception-{i:04d}"
    out = []
    for tag, act, lab in (("x", exc_act, LABEL_OK), ("c", bad, LABEL_CONTRA)):
        src = f"{an}은(는) {act}."
        ev = _event(T1, 1, f"{an}의 행동", src, src, loc=lid, chars=[a], scene="S_T")
        out.append(_case(f"{g}{tag}", "rule_exception", "rule", g, [], [ev], [(T1, rid, lab)], rules))
    return out


# ---------------------------------------------------------------- 연산자: knowledge

def _reveal(ctx: Ctx, i: int):  # noqa: ANN202
    """s0: c 는 멀리 있어 소식을 못 들음(c 의 knowledge 로 남는다), s1: a·b 만 있는 자리에서 비밀이 밝혀짐."""
    secret = SECRETS[i % len(SECRETS)]
    (a, an), (b, bn), (c, cn) = ctx.pick_chars(3)
    (lid, _), (far, _) = ctx.loc(), ctx.loc()
    s0, s1 = f"EVENT_{SETUP_EP}_1", f"EVENT_{SETUP_EP}_2"
    src0 = f"그 시각 {cn}은(는) 멀리 떨어진 곳에 있어 {secret}에 대해 아무것도 듣지 못했다."
    away = _event(s0, 1, f"{cn}의 부재", src0, src0, loc=far, chars=[c],
                  knowledge=[{"character_id": c, "info": f"{secret}에 대해 아무것도 듣지 못함",
                              "via": "direct", "source_text": src0}])
    src = f"{an}와(과) {bn}만 남은 방에서 {secret}이(가) 밝혀졌다."
    ev = _event(s1, 2, "비밀이 밝혀짐", src, src, loc=lid, chars=[a, b],
                knowledge=[{"character_id": a, "info": secret, "via": "direct", "source_text": src},
                           {"character_id": b, "info": secret, "via": "direct", "source_text": src}])
    return secret, (a, an), (c, cn), lid, s0, s1, [away, ev]


def knowledge_contrast(ctx: Ctx, i: int) -> list[dict]:
    secret, (a, an), (c, cn), lid, s0, s1, setup = _reveal(ctx, i)
    g = f"knowledge_contrast-{i:04d}"
    out = []
    for tag, who, wn, key, lab in (("c", c, cn, s0, LABEL_CONTRA), ("n", a, an, s1, LABEL_OK)):
        src = f"{wn}은(는) {secret}을(를) 이미 알고 있다는 듯 곧장 행동에 옮겼다."
        ev = _event(T1, 1, f"{wn}의 행동", src, src, loc=lid, chars=[who],
                    assumed=[_as(who, "knows", secret, src)], scene="S_T")
        out.append(_case(f"{g}{tag}", "knowledge_contrast", "knowledge", g, setup, [ev], [(T1, key, lab)]))
    unrel = UNRELATED_ACTS[i % len(UNRELATED_ACTS)][0]
    src = f"{cn}은(는) {unrel}."
    ev = _event(T1, 1, f"{cn}의 한때", src, src, loc=lid, chars=[c], scene="S_T")
    out.append(_case(f"{g}u", "knowledge_contrast", "knowledge", g, setup, [ev], [(T1, s0, LABEL_UNREL)]))
    return out


def knowledge_heard(ctx: Ctx, i: int) -> list[dict]:
    """[어려운 음성] 못 들었던 인물이 나중에 전해 들음 — 판정 장면 원문에도 '전해 들은 대로'가 있다."""
    secret, (a, an), (c, cn), lid, s0, _s1, setup = _reveal(ctx, i)
    s2 = f"EVENT_{SETUP_EP}_3"
    src2 = f"그날 밤 {an}은(는) {cn}에게 {secret}을(를) 몰래 귀띔했다."
    heard = _event(s2, 3, "비밀을 전해 들음", src2, src2, loc=lid, chars=[a, c],
                   knowledge=[{"character_id": c, "info": secret, "via": "hearsay", "source_text": src2}])
    src = f"{cn}은(는) {an}에게서 전해 들은 대로 {secret}을(를) 떠올리고 곧장 행동에 옮겼다."
    ev = _event(T1, 1, f"{cn}의 행동", src, src, loc=lid, chars=[c], assumed=[_as(c, "knows", secret, src)],
                scene="S_T")
    g = f"knowledge_heard-{i:04d}"
    return [_case(g, "knowledge_heard", "knowledge", g, setup + [heard], [ev],
                  [(T1, s2, LABEL_OK), (T1, s0, LABEL_OK)])]


OPERATORS: dict[str, Callable[[Ctx, int], list[dict]]] = {
    "state_contrast": state_contrast,
    "state_recovery": state_recovery,
    "state_gradual": state_gradual,
    "state_scene_change": state_scene_change,
    "rule_contrast": rule_contrast,
    "rule_exception": rule_exception,
    "knowledge_contrast": knowledge_contrast,
    "knowledge_heard": knowledge_heard,
}


def generate(registry: dict, *, per_operator: int, seed: int, operators: list[str]) -> dict:
    cases = []
    for name in operators:  # 연산자마다 독립 난수열 — 하나를 빼도 나머지 출력이 바뀌지 않는다
        ctx = Ctx(registry, random.Random(f"{seed}:{name}"))
        for i in range(per_operator):
            cases += OPERATORS[name](ctx, i)
    return {"generator": "scripts/eval/inject.py", "seed": seed, "per_operator": per_operator,
            "operators": operators, "setup_episode": SETUP_EP, "test_episode": TEST_EP, "cases": cases}


REGISTRY_NAMES = ("entity_registry.json", "registry_live.json")


def find_registry(*, registry: Path | None = None, work: str | None = None, base: Path | None = None) -> Path:
    """레지스트리 파일 하나를 고른다. 우선순위 registry > work > base(폴더 안 REGISTRY_NAMES 순)."""
    if registry is not None:
        if not registry.is_file():
            raise SystemExit(f"레지스트리 파일이 없습니다: {registry}")
        return registry
    if work is not None:
        import judge_eval  # noqa: PLC0415 - --work 일 때만(코어 경로를 연다)
        if work not in judge_eval.WORKS:
            raise SystemExit(f"알 수 없는 작품: {work} (가능: {list(judge_eval.WORKS)})")
        return judge_eval.WORKS[work].registry
    if base is None:
        raise SystemExit("--registry · --work · --base 중 하나가 필요합니다")
    for name in REGISTRY_NAMES:
        if (base / name).is_file():
            return base / name
    raise SystemExit(f"{base} 에 {' / '.join(REGISTRY_NAMES)} 가 없습니다 (--registry 로 지정)")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base", type=Path, help="기반 작품 폴더(entity_registry.json 또는 registry_live.json)")
    ap.add_argument("--work", help="기반 작품 이름(judge_eval.WORKS 의 레지스트리) — wm·q1·q3·sherlock")
    ap.add_argument("--registry", type=Path, help="레지스트리 파일 직접 지정")
    ap.add_argument("--out", type=Path, required=True, help="manifest JSON 경로")
    ap.add_argument("--per-operator", type=int, default=40)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--operators", nargs="*", default=list(OPERATORS))
    ap.add_argument("--exclude", nargs="*", default=[], help="홀드아웃할 연산자")
    a = ap.parse_args(argv)
    unknown = set(a.operators + a.exclude) - set(OPERATORS)
    if unknown:
        raise SystemExit(f"알 수 없는 연산자: {sorted(unknown)} (가능: {list(OPERATORS)})")
    ops = [o for o in a.operators if o not in set(a.exclude)]
    reg_path = find_registry(registry=a.registry, work=a.work, base=a.base)
    raw = reg_path.read_bytes()
    registry = json.loads(raw.decode("utf-8"))
    manifest = generate(registry, per_operator=a.per_operator, seed=a.seed, operators=ops)
    manifest["base"] = a.base.as_posix() if a.base else None
    manifest["work"] = a.work
    manifest["registry"] = reg_path.as_posix()
    manifest["registry_sha256"] = hashlib.sha256(raw).hexdigest()
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(manifest['cases'])} cases ({', '.join(ops)}) -> {a.out}")


if __name__ == "__main__":
    main()
