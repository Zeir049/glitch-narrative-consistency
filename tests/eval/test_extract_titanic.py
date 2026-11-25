"""extract_titanic.py — 합성 텍스트·가짜 LLM 만 쓴다(타이타닉 원문 없음, API 호출 없음)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "eval"))

import extract_titanic as X  # noqa: E402
from app.pipeline import weave as W  # noqa: E402
from app.pipeline.llm_record import LLMRecorder  # noqa: E402


def fake_doc(label: str, n_events: int = 1) -> dict:
    return {
        "characters": [{"character_id": f"NEW:{label}", "name": label, "entity_type": "person"}],
        "events": [{"event_id": f"E{i}", "scene_id": "scene_1", "main_actor_character_id": f"NEW:{label}",
                    "participant_character_ids": [f"NEW:{label}"], "summary": f"{label} {i}"}
                   for i in range(1, n_events + 1)],
    }


class Inner:
    """호출 수를 세는 가짜 안쪽 함수. 프롬프트의 '이름:' 줄에서 라벨을 뽑는다."""

    def __init__(self, replies=None) -> None:  # noqa: ANN001
        self.calls = 0
        self.replies = list(replies or [])

    def __call__(self, system, user, llm, *, role="judge", base=None):  # noqa: ANN001
        self.calls += 1
        if self.replies:
            r = self.replies.pop(0)
            if isinstance(r, dict) and "status" in r:
                return r
            return {"status": "ok", "text": r, "attempts": 1, "usage": {"input": 10, "output": 5},
                    "finish_reason": "stop"}
        return {"status": "ok", "text": json.dumps(fake_doc("철수", 2), ensure_ascii=False), "attempts": 1,
                "usage": {"input": 10, "output": 5}, "finish_reason": "stop"}


def make(tmp_path, replies=None):  # noqa: ANN001
    inner = Inner(replies)
    rec = LLMRecorder(inner, mode="record", root=tmp_path / "rec")
    return inner, X.CachedLLM(rec, tmp_path / "calls.jsonl")


LLM = {"model": "x/y:free"}


def test_refuses_non_free_model(tmp_path):
    inner, c = make(tmp_path)
    with pytest.raises(RuntimeError, match="유료"):
        c("s", "u", {"model": "nvidia/nemotron-3-ultra-550b-a55b"}, role="extract")
    assert inner.calls == 0


def test_second_run_uses_recording_not_api(tmp_path):
    inner, c = make(tmp_path)
    r1 = c("s", "u", LLM, role="extract")
    assert inner.calls == 1 and c.api_calls == 1 and c.hits == 0
    inner2, c2 = make(tmp_path)           # 새 프로세스를 흉내: 같은 녹화 폴더, 커서 0
    r2 = c2("s", "u", LLM, role="extract")
    assert inner2.calls == 0 and c2.hits == 1 and r2 == r1
    assert c2.api_calls == 0


def test_repeated_same_prompt_calls_api_for_second(tmp_path):
    """JSON 파싱 실패 재요청: 같은 프롬프트의 두 번째는 녹화에 두 번째 항목이 없으면 실제 호출."""
    inner, c = make(tmp_path)
    c("s", "u", LLM, role="extract")
    c("s", "u", LLM, role="extract")
    assert inner.calls == 2
    inner2, c2 = make(tmp_path)
    c2("s", "u", LLM, role="extract")
    c2("s", "u", LLM, role="extract")
    assert inner2.calls == 0 and c2.hits == 2


def test_failure_counted_and_not_recorded(tmp_path):
    inner, c = make(tmp_path, [{"status": "error", "text": "", "message": "HTTP 500: boom", "attempts": 4}])
    r = c("s", "u", LLM, role="extract")
    assert r["status"] == "error" and c.failures == 1 and c.api_calls == 1
    inner2, c2 = make(tmp_path)
    c2("s", "u", LLM, role="extract")      # 실패는 녹화되지 않으므로 다시 부른다
    assert inner2.calls == 1


def test_429_stops(tmp_path):
    inner, c = make(tmp_path, [{"status": "error", "text": "", "message": "HTTP 429: rate limited", "attempts": 4}])
    with pytest.raises(X.StopExtraction):
        c("s", "u", LLM, role="extract")
    assert c.stopped and c.failures == 1


def test_scene_extract_text_blank_lines_and_chunking():
    raw = "가\n나다\n\n라\n"
    assert X.scene_extract_text(raw) == "가\n\n나다\n\n라\n"
    paras = [("문장 " * 120).strip() + str(i) for i in range(10)]     # 문단당 ~360자
    one = "\n".join(paras)
    # 줄바꿈뿐인 원문은 그대로는 한 조각, 빈 줄을 넣으면 여러 조각
    assert len(W.chunk_text(one, 2500)) == 1
    assert len(W.chunk_text(X.scene_extract_text(one), 2500)) >= 2


def test_plan_shares_identical_chunks():
    big = [("문장 " * 120).strip() + str(i) for i in range(14)]
    a = {"scene_01": "\n".join(big), "scene_02": "\n".join(big[:-1] + ["끝 수정"])}
    per_scene, chunks = X.plan_scene_chunks(a, W.chunk_text)
    assert len(per_scene["scene_01"]) >= 2
    shared = set(per_scene["scene_01"]) & set(per_scene["scene_02"])
    assert shared and len(chunks) < sum(len(v) for v in per_scene.values())


def test_revert_new_ids_only_for_new_entities():
    reg_after = {"characters": [{"id": "CHAR_001", "name": "철수", "new_label": "NEW:철수"},
                                {"id": "CHAR_002", "name": "영희", "new_label": "NEW:영희"}]}
    doc = {"events": [{"main_actor_character_id": "CHAR_001", "participant_character_ids": ["CHAR_001", "CHAR_002"]}]}
    out = X.revert_new_ids(doc, reg_after, {"CHAR_001"}, W._rewrite)
    assert out["events"][0]["participant_character_ids"] == ["CHAR_001", "NEW:영희"]


def test_assemble_scene_renumbers_and_prefixes():
    d1 = {"events": [{"event_id": "EVENT_5_1", "scene_id": "scene_5_1", "related": ["EVENT_5_2"]},
                     {"event_id": "EVENT_5_2", "scene_id": "scene_5_1"}], "characters": [{"character_id": "CHAR_001"}]}
    d2 = {"events": [{"event_id": "EVENT_5_1", "scene_id": "scene_5_1"}], "characters": [{"character_id": "CHAR_001"},
                                                                                         {"character_id": "CHAR_002"}]}
    out = X.assemble_scene([d1, d2], W._rewrite)
    ids = [e["event_id"] for e in out["events"]]
    assert ids == ["EVENT_5_1", "EVENT_5_2", "EVENT_5_3"]
    assert out["events"][0]["related"] == ["EVENT_5_2"]
    assert [e["scene_id"] for e in out["events"]] == ["c1:scene_5_1", "c1:scene_5_1", "c2:scene_5_1"]
    assert [e["order"] for e in out["events"]] == [1, 2, 3]
    assert [c["character_id"] for c in out["characters"]] == ["CHAR_001", "CHAR_002"]
    single = X.assemble_scene([d2], W._rewrite)
    assert single["events"][0]["scene_id"] == "scene_5_1"          # 조각이 하나면 접두사 없음


def test_extract_chunk_end_to_end_with_fake_llm(tmp_path, monkeypatch):
    """서비스 extract_episode 를 가짜 LLM 으로: 원장 레지스트리는 바뀌지 않고, 새 엔티티는 NEW:라벨로 남는다."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy")
    inner, c = make(tmp_path)
    monkeypatch.setattr(W.w_llm, "complete", c)
    cfg = W.weave_config()
    store = W.WeaveStore("t_e2e", tmp_path / "data")
    text = "철수가 말한다.\n\n영희가 대답한다.\n"
    with store.lock():
        doc, reg, lm, _ = W.extract_episode(text, 1, store, cfg)
        store.write_json(store.registry_path, reg)
        store.write_json(store.label_map_path, lm)
    assert inner.calls == 1 and doc["events"]
    frozen = X.registry_ids(store.load_registry())
    assert frozen                                            # 원장에 철수가 등재됨
    before = (store.dir / "registry.json").read_text(encoding="utf-8")
    inner.replies = [json.dumps(fake_doc("민수", 1), ensure_ascii=False)]
    out = X.extract_chunk(W, "민수가 들어온다.\n", store, tmp_path / "scratch", cfg)
    assert (store.dir / "registry.json").read_text(encoding="utf-8") == before   # 읽기 전용
    assert out["events"][0]["main_actor_character_id"] == "NEW:민수"


def test_build_chunk_map():
    texts = {"scene_01": "가가가\n나나나\n다다다", "scene_02": "가가가\n나나나\n라라라"}
    chunks = {"h1": "가가가\n\n나나나", "h2": "다다다", "h3": "라라라"}
    per_scene = {"scene_01": ["h1", "h2"], "scene_02": ["h1", "h3"]}
    val = [{"scene_id": "scene_01", "event": "E", "kind": "P", "replacement": "다 다다"},
           {"scene_id": "scene_02", "event": "E", "kind": "N", "replacement": "라라라"}]
    m = X.build_chunk_map(per_scene, chunks, val, {"h1": 2, "h2": 1, "h3": 3}, {"h1": ["extract:aa"]}, texts)
    c1, c2 = m["scenes"]["scene_01"]["chunks"], m["scenes"]["scene_02"]["chunks"]
    assert c1[0]["shared"] and not c1[1]["shared"] and c1[1]["contains_edit"] and not c1[0]["contains_edit"]
    assert c1[0]["char_range"] == [0, 8] and c1[1]["char_range"] == [10, 13]
    assert c1[1]["event_ids"] == ["EVENT_5_3"] and c2[1]["event_ids"] == ["EVENT_5_3", "EVENT_5_4", "EVENT_5_5"]
    assert c1[0]["record_keys"] == ["extract:aa"] and m["scenes"]["scene_01"]["mates"] == ["scene_02"]


def test_unparsable_recorded_response_is_not_replayed(tmp_path):
    inner, c = make(tmp_path, ["이건 JSON 이 아니다"])
    c("s", "u", LLM, role="extract")
    inner2, c2 = make(tmp_path)
    r = c2("s", "u", LLM, role="extract")      # 깨진 녹화는 건너뛰고 새로 부른다
    assert inner2.calls == 1 and json.loads(r["text"])


def test_split_in_two_at_paragraph_boundary():
    chunk = "\n\n".join(["가" * 100, "나" * 100, "다" * 100, "라" * 100])
    a, b = X.split_in_two(chunk)
    assert a + "\n\n" + b == chunk and abs(len(a) - len(b)) <= 102
    with pytest.raises(RuntimeError):
        X.split_in_two("문단 하나")


def test_seed_registry_merges_and_drops_generic():
    draft = {"characters": [{"name": n} for n in
                            ["로즈", "늙은 로즈", "로벳", "브록", "플리트", "플릿", "스미스 선장", "스미스", "남자", "카메라맨", "머독"]],
             "locations": [{"name": "갑판"}]}
    reg, log = X.build_seed_registry(draft)
    names = {c["name"]: c for c in reg["characters"]}
    assert set(names) == {"로즈", "브록 로벳", "플리트", "스미스 선장", "머독"}
    assert names["로즈"]["aliases"] == ["늙은 로즈", "젊은 로즈"] and names["브록 로벳"]["aliases"] == ["로벳", "브록"]
    assert reg["locations"] == [] and [c["id"] for c in reg["characters"]][0] == "CHAR_001"
    assert "남자" in log["dropped_generic"] and log["renamed"][0]["name"] == "브록 로벳"


def test_guard_demotes_id_name_conflicts():
    reg = {"characters": [{"id": "CHAR_001", "name": "로즈", "aliases": ["젊은 로즈"]},
                          {"id": "CHAR_002", "name": "칼 호클리"}]}
    doc = {"characters": [{"character_id": "CHAR_001", "name": "로즈 드윗 부카터 (젊은 시절)"},   # 이름 변형 → 통과
                          {"character_id": "CHAR_002", "name": "루스 드윗 부카터"},               # 충돌 → 강등
                          {"character_id": "CHAR_099", "name": "짐꾼"}],                          # 레지스트리에 없는 번호 → 강등
           "events": [{"main_actor_character_id": "CHAR_002", "participant_character_ids": ["CHAR_001", "CHAR_099"]}]}
    log = X.demote_conflicts(doc, reg, W._rewrite)
    assert [d["id"] for d in log] == ["CHAR_002", "CHAR_099"]
    ev = doc["events"][0]
    assert ev["main_actor_character_id"] == "NEW:루스_드윗_부카터" and ev["participant_character_ids"] == ["CHAR_001", "NEW:짐꾼"]
    assert doc["characters"][0]["character_id"] == "CHAR_001"
