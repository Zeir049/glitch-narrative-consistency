"""Weave LLM 장애 시험 — 진짜 HTTP 경로(weave_llm → urllib)를 가짜 OpenRouter 서버에 붙인다.

API 키·외부 네트워크를 쓰지 않는다. 가짜 서버는 요청마다 '각본(script)'의 다음 동작을 수행한다:
  ok_extract / ok_judge / ok_judge_neg  정상 응답
  http:<code>                           HTTP 오류 (429, 500 …)
  body_error:<code>                     HTTP 200 + 본문 error (OpenRouter 업스트림 오류 형태)
  drop                                  헤더 일부만 보내고 연결을 끊음 (중간 절단)
  hang:<sec>                            응답 없이 sec 초 대기 (하드 데드라인 시험)
  truncated                             JSON 이 닫히지 않은 본문 (출력 토큰 절단)
각본이 바닥나면 마지막 동작을 반복한다.
"""
from __future__ import annotations

import asyncio
import json
import re
import threading
import time
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.core.config import settings
from app.pipeline import weave as W
from app.pipeline.interface import CanonDelta, CanonSnapshot
from tests.test_weave_pipeline import CH1, CH2, DOC1, DOC2, _snapshot

LONG = "\n\n".join(["가" * 700] * 4)
JUDGE_POS = {"verdict": "모순", "reason": "사망자 재등장", "evidence_event_ids": ["EVENT_1_1"], "confidence": 0.9}
JUDGE_NEG = {"verdict": "정상", "reason": "", "evidence_event_ids": [], "confidence": 0.9}


class FakeOpenRouter:
    def __init__(self):
        self.script: list[str] = ["ok_extract"]
        self.requests: list[dict] = []
        self.lock = threading.Lock()
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):  # noqa: ANN002
                pass

            def do_POST(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                with fake.lock:
                    act = fake.script.pop(0) if len(fake.script) > 1 else fake.script[0]
                    fake.requests.append({"act": act, "auth": self.headers.get("Authorization"),
                                          "t": time.monotonic(), "body": body})
                user = body["messages"][-1]["content"]
                fake.handle(self, act, user)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/api/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def handle(self, h, act: str, user: str):
        def send(code: int, obj):
            data = json.dumps(obj, ensure_ascii=False).encode()
            h.send_response(code)
            h.send_header("Content-Type", "application/json")
            h.send_header("Content-Length", str(len(data)))
            h.end_headers()
            h.wfile.write(data)

        def content(text: str):
            send(200, {"choices": [{"message": {"content": text}, "finish_reason": "stop"}],
                       "usage": {"prompt_tokens": 1, "completion_tokens": 1}})

        if act == "ok_extract":
            ep = int(re.search(r"episode = (\d+)", user).group(1))
            docs = getattr(self, "docs", None) or {1: DOC1, 2: DOC2}
            content(json.dumps(docs.get(ep, DOC1), ensure_ascii=False))
        elif act == "ok_judge":
            content(json.dumps(JUDGE_POS if "EVENT_2_" in user else JUDGE_NEG, ensure_ascii=False))
        elif act == "ok_judge_neg":
            content(json.dumps(JUDGE_NEG, ensure_ascii=False))
        elif act.startswith("http:"):
            send(int(act[5:]), {"error": {"message": "fake error", "code": int(act[5:])}})
        elif act.startswith("body_error:"):
            send(200, {"error": {"message": "upstream busy", "code": int(act[11:])}})
        elif act == "drop":
            h.send_response(200)
            h.send_header("Content-Type", "application/json")
            h.send_header("Content-Length", "5000")
            h.end_headers()
            h.wfile.write(b'{"choices": [{"message": {"content": "{\\"events')
            h.wfile.flush()
            h.connection.shutdown(2)
        elif act.startswith("hang:"):
            time.sleep(float(act[5:]))
            content("{}")
        elif act == "truncated":
            content('{"schema_version": 2, "events": [{"event_id": "EVENT_1_1", "title": "잘린')
        else:  # pragma: no cover
            send(500, {"error": {"message": f"unknown act {act}"}})

    def close(self):
        self.server.shutdown()


@pytest.fixture
def fake_or(tmp_path, monkeypatch):
    srv = FakeOpenRouter()
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-SECRET-should-never-leak")
    monkeypatch.setattr(settings, "weave_llm_provider", "openrouter")
    monkeypatch.setattr(settings, "weave_judge_k", 3)
    monkeypatch.setattr(settings, "weave_judge_concurrency", 3)
    monkeypatch.setattr(W.w_llm, "OPENROUTER_BASE_URL", srv.url)
    monkeypatch.setattr(W.w_llm, "MIN_INTERVAL_SEC", 0.0)
    monkeypatch.setattr(W.w_llm, "HARD_DEADLINE", 1.5)
    monkeypatch.setattr(W.w_llm, "HTTP_TIMEOUT", 5.0)
    # 재시도 백오프(5/10/20초)만 0.01초로 — 그 외 time 은 진짜
    shim = types.SimpleNamespace(time=time.time, sleep=lambda s: time.sleep(min(s, 0.01)),
                                 monotonic=time.monotonic)
    monkeypatch.setattr(W.w_llm, "time", shim)
    yield srv, W.WeavePipeline(data_dir=str(tmp_path)), tmp_path
    srv.close()


def _no_secret(*texts):
    for t in texts:
        assert "SECRET" not in (t or ""), "API 키가 노출됐다"


# ── 추출 단계 ─────────────────────────────────────────────────
async def test_extract_ok_through_real_http(fake_or):
    srv, pipe, root = fake_or
    out = await pipe.extract_chapter(CH1, CanonSnapshot(project_id="p"), chapter_no=1)
    assert out.canon_delta.events and srv.requests[0]["auth"].startswith("Bearer ")
    assert not (root / "p" / ".lock").exists()


@pytest.mark.parametrize("act, expect", [
    ("drop", "연결 실패"),
    ("http:429", "HTTP 429"),
    ("http:500", "HTTP 500"),
    ("body_error:503", "HTTP 503"),
    ("hang:3", "데드라인"),
    ("truncated", "JSON"),
])
async def test_extract_failure_modes(fake_or, act, expect):
    srv, pipe, root = fake_or
    srv.script = [act]
    with pytest.raises(W.WeaveError) as ei:
        await pipe.extract_chapter(CH1, CanonSnapshot(project_id="p"), chapter_no=1)
    msg = str(ei.value)
    assert expect in msg, msg
    _no_secret(msg)
    # 실패하면 작품 상태(레지스트리·사건 로그)를 남기지 않고, 잠금도 풀린다
    assert not (root / "p" / "registry.json").exists()
    assert not (root / "p" / "episodes" / "episode_01.json").exists()
    assert not (root / "p" / ".lock").exists()


async def test_extract_recovers_after_transient_errors(fake_or):
    srv, pipe, _ = fake_or
    srv.script = ["http:500", "drop", "ok_extract"]
    out = await pipe.extract_chapter(CH1, CanonSnapshot(project_id="p"), chapter_no=1)
    assert out.canon_delta.events and len(srv.requests) == 3


async def test_extract_second_chunk_failure_keeps_previous_state(fake_or, monkeypatch):
    """1화 재추출 중 2번째 조각이 끊기면, 직전에 성공한 1화 상태가 그대로 남는다."""
    srv, pipe, root = fake_or
    await pipe.extract_chapter(CH1, CanonSnapshot(project_id="p"), chapter_no=1)
    before_ep = (root / "p" / "episodes" / "episode_01.json").read_text(encoding="utf-8")
    before_reg = (root / "p" / "registry.json").read_text(encoding="utf-8")
    monkeypatch.setattr(settings, "weave_chunk_chars", 1200)
    srv.script = ["ok_extract", "http:400"]
    with pytest.raises(W.WeaveError, match="2/"):
        await pipe.extract_chapter("\n\n".join(["가" * 700] * 4), CanonSnapshot(project_id="p"), chapter_no=1)
    assert (root / "p" / "episodes" / "episode_01.json").read_text(encoding="utf-8") == before_ep
    assert (root / "p" / "registry.json").read_text(encoding="utf-8") == before_reg


# ── 검수 단계 ─────────────────────────────────────────────────
async def _two_chapters(srv, pipe):
    srv.script = ["ok_extract"]
    out1 = await pipe.extract_chapter(CH1, CanonSnapshot(project_id="p"), chapter_no=1)
    out2 = await pipe.extract_chapter(CH2, _snapshot("p", 1, out1.canon_delta), chapter_no=2)
    return out1, out2


async def test_review_all_judge_calls_fail(fake_or):
    srv, pipe, _ = fake_or
    out1, out2 = await _two_chapters(srv, pipe)
    srv.script = ["drop"]
    with pytest.raises(W.WeaveError) as ei:
        await pipe.review_chapter(CH2, _snapshot("p", 1, out1.canon_delta), out2.canon_delta, chapter_no=2)
    assert "판정" in str(ei.value)
    _no_secret(str(ei.value))


async def test_review_partial_judge_failure_uses_remaining_runs(fake_or, monkeypatch):
    srv, pipe, root = fake_or
    out1, out2 = await _two_chapters(srv, pipe)
    monkeypatch.setattr(settings, "weave_judge_concurrency", 1)  # 각본 순서를 고정
    srv.script = ["http:500", "http:500", "http:500", "http:500", "ok_judge"]  # 1회는 재시도 소진 → 실패
    res = await pipe.review_chapter(CH2, _snapshot("p", 1, out1.canon_delta), out2.canon_delta, chapter_no=2)
    j = [c for c in res.contradictions if c.explanation.startswith("[LLM 판정")]
    assert j and "3회 중 2회 검출, 1회는 호출 실패" in j[0].explanation, [c.explanation for c in res.contradictions]
    saved = json.loads((root / "p" / "reviews" / "review_02.json").read_text(encoding="utf-8"))
    assert saved["judge_failed_runs"] == 1
    _no_secret(json.dumps(saved, ensure_ascii=False))


async def test_review_packet_with_no_answer_is_not_silently_clean(fake_or, monkeypatch):
    """장면 하나의 판정 k회가 전부 실패하면 '모순 없음'으로 넘기지 않고 실패로 알린다."""
    srv, pipe, _ = fake_or
    doc2 = json.loads(json.dumps(DOC2))
    ev2 = json.loads(json.dumps(doc2["events"][0]))
    ev2.update({"event_id": "EVENT_2_2", "order": 2, "scene_id": "S2", "assumed_states": []})
    doc2["events"].append(ev2)
    monkeypatch.setitem(DOC2, "events", doc2["events"])
    out1, out2 = await _two_chapters(srv, pipe)
    # 6회 호출 중 S2 장면의 3회만 실패하도록: 프롬프트로 구분
    orig = srv.handle

    def handle(h, act, user):
        if "EVENT_2_2" in user and "EVENT_2_1" not in user:
            return orig(h, "http:400", user)
        return orig(h, "ok_judge", user)

    srv.handle = handle
    with pytest.raises(W.WeaveError, match="판정"):
        await pipe.review_chapter(CH2, _snapshot("p", 1, out1.canon_delta), out2.canon_delta, chapter_no=2)


# ── 취소(arq job_timeout) ─────────────────────────────────────
async def test_cancel_during_extraction_leaves_no_late_writes(fake_or, monkeypatch):
    """작업이 취소되면, 뒤늦게 응답이 와도 사건 로그·레지스트리를 쓰지 않고 잠금을 푼다."""
    srv, pipe, root = fake_or
    monkeypatch.setattr(W.w_llm, "HARD_DEADLINE", 5.0)
    srv.script = ["hang:1", "ok_extract"]
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            pipe.extract_chapter(CH1, CanonSnapshot(project_id="p"), chapter_no=1), timeout=0.3)
    await asyncio.sleep(2.0)  # 버려진 스레드가 응답을 받고 끝날 시간
    assert not (root / "p" / "episodes" / "episode_01.json").exists()
    assert not (root / "p" / "registry.json").exists()
    assert not (root / "p" / ".lock").exists()


async def test_cancel_during_review_stops_remaining_calls(fake_or, monkeypatch):
    srv, pipe, root = fake_or
    out1, out2 = await _two_chapters(srv, pipe)
    monkeypatch.setattr(W.w_llm, "HARD_DEADLINE", 5.0)
    monkeypatch.setattr(settings, "weave_judge_concurrency", 1)
    n0 = len(srv.requests)
    srv.script = ["hang:1"]
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(pipe.review_chapter(
            CH2, _snapshot("p", 1, out1.canon_delta), out2.canon_delta, chapter_no=2), timeout=0.3)
    await asyncio.sleep(3.0)
    assert len(srv.requests) - n0 == 1, "취소 뒤에도 남은 판정 호출을 계속 보냈다"
    assert not (root / "p" / "reviews" / "review_02.json").exists()


# ── 잠금 ──────────────────────────────────────────────────────
async def test_stale_lock_from_dead_process_is_cleared(fake_or):
    srv, pipe, root = fake_or
    d = root / "p"
    d.mkdir(parents=True)
    (d / ".lock").write_text("999999", encoding="utf-8")  # 죽은 프로세스가 남긴 잠금
    t0 = time.monotonic()
    out = await pipe.extract_chapter(CH1, CanonSnapshot(project_id="p"), chapter_no=1)
    assert out.canon_delta.events and time.monotonic() - t0 < 5


# ── API 종단: 실패 → failed 상태·사유 → 복구 후 재시도 ───────────
async def test_api_failure_then_retry(client, auth_headers, project_id, fake_or, monkeypatch):
    from tests.util import poll_result

    srv, pipe, _ = fake_or
    monkeypatch.setattr("app.workers.tasks.get_pipeline", lambda: pipe)
    h, pid = auth_headers, project_id
    r = await client.post(f"/projects/{pid}/chapters", json={"chapter_no": 1, "text": CH1}, headers=h)
    assert r.status_code == 201
    srv.script = ["drop"]
    await client.post(f"/projects/{pid}/chapters/1/extract", headers=h)
    res = await poll_result(client, pid, 1, h, stop=("extracted", "failed"))
    assert res["status"] == "failed" and res.get("failed_stage") == "extract", res
    assert "연결 실패" in (res.get("detail") or ""), res  # 화면에 보이는 실패 사유
    _no_secret(json.dumps(res, ensure_ascii=False))

    srv.script = ["ok_extract"]
    await client.post(f"/projects/{pid}/chapters/1/extract", headers=h)
    res = await poll_result(client, pid, 1, h, stop=("extracted", "failed"))
    assert res["status"] == "extracted", res


# ── 검토에서 나온 데이터 흐름 결함 회귀 시험 ───────────────────────
def _doc(base, **ev_over):
    d = json.loads(json.dumps(base))
    d["events"][0].update(ev_over)
    return d


async def test_alias_matched_label_resolves_across_chapters(fake_or):
    """2화가 다른 라벨(NEW:서진)로 같은 인물을 부르면 별칭 매칭 ID 로 묶여 원장 대조가 된다."""
    srv, pipe, _ = fake_or
    d1 = json.loads(json.dumps(DOC1))
    d1["characters"][0]["aliases"] = ["서진"]
    d2 = json.loads(json.dumps(DOC2).replace("NEW:한서진", "NEW:서진"))
    d2["characters"][0]["name"] = "서진"
    srv.docs = {1: d1, 2: d2}
    out1 = await pipe.extract_chapter(CH1, CanonSnapshot(project_id="p"), chapter_no=1)
    out2 = await pipe.extract_chapter(CH2, _snapshot("p", 1, out1.canon_delta), chapter_no=2)
    assert out2.canon_delta.characters[0].entity_key == out1.canon_delta.characters[0].entity_key
    srv.script = ["ok_judge"]
    res = await pipe.review_chapter(CH2, _snapshot("p", 1, out1.canon_delta), out2.canon_delta, chapter_no=2)
    assert any(c.explanation.startswith("[규칙 검사") for c in res.contradictions)


async def test_scene_ids_do_not_collide_across_chunks(fake_or, monkeypatch):
    srv, pipe, root = fake_or
    monkeypatch.setattr(settings, "weave_chunk_chars", 1200)
    out = await pipe.extract_chapter(LONG, CanonSnapshot(project_id="p"), chapter_no=1)
    scenes = [e.attrs["scene_id"] for e in out.canon_delta.events]
    assert len(scenes) == 2 and len(set(scenes)) == 2, scenes


async def test_unsaved_reextraction_and_deleted_events_stay_out_of_ledger(fake_or):
    srv, pipe, _ = fake_or
    out1 = await pipe.extract_chapter(CH1, CanonSnapshot(project_id="p"), chapter_no=1)
    saved = _snapshot("p", 1, out1.canon_delta)
    # 저장 뒤 1화를 '죽음 없음'으로 재추출(저장 안 함) → 검수는 저장된 판본으로 해야 한다
    srv.docs = {1: _doc(DOC1, state_changes=[]), 2: DOC2}
    await pipe.extract_chapter(CH1, CanonSnapshot(project_id="p"), chapter_no=1)
    srv.docs = {1: DOC1, 2: DOC2}
    out2 = await pipe.extract_chapter(CH2, saved, chapter_no=2)
    srv.script = ["ok_judge_neg"]
    res = await pipe.review_chapter(CH2, saved, out2.canon_delta, chapter_no=2)
    assert any(c.explanation.startswith("[규칙 검사") for c in res.contradictions), "저장된 판본이 아닌 재추출본을 썼다"
    # 1차 검토에서 사망 사건을 지우고 저장했다면 원장에 사망이 없다
    no_death = _snapshot("p", 1, out1.canon_delta)
    no_death.events = [e for e in no_death.events if e["entity_key"] != "EVENT_1_1"]
    res2 = await pipe.review_chapter(CH2, no_death, out2.canon_delta, chapter_no=2)
    assert not any(c.explanation.startswith("[규칙 검사") for c in res2.contradictions)


async def test_judge_rules_only_from_saved_canon(fake_or):
    srv, pipe, _ = fake_or
    d3 = json.loads(json.dumps(DOC2).replace("EVENT_2_", "EVENT_3_"))
    d3["worldview_rules"] = [{"rule_id": "NEW:비밀규칙", "content": "저장되지 않은 비밀 규칙", "certainty": "explicit"}]
    srv.docs = {1: DOC1, 2: DOC2, 3: d3}
    out1 = await pipe.extract_chapter(CH1, CanonSnapshot(project_id="p"), chapter_no=1)
    await pipe.extract_chapter(CH2, CanonSnapshot(project_id="p"), chapter_no=3)  # 3화: 추출만, 저장 안 함
    out2 = await pipe.extract_chapter(CH2, _snapshot("p", 1, out1.canon_delta), chapter_no=2)
    n0 = len(srv.requests)
    srv.script = ["ok_judge"]
    await pipe.review_chapter(CH2, _snapshot("p", 1, out1.canon_delta), out2.canon_delta, chapter_no=2)
    prompts = [r["body"]["messages"][-1]["content"] for r in srv.requests[n0:]]
    assert prompts and all("저장되지 않은 비밀 규칙" not in p for p in prompts)
    assert all("죽은 자는 되살아나지 않는다" in p for p in prompts)


async def test_effort_matches_measured_condition(fake_or):
    """측정 조건: 추출은 reasoning_effort=high(llm_of 기본), 판정은 미전송. 둘 다 reasoning off."""
    srv, pipe, _ = fake_or
    out1, out2 = await _two_chapters(srv, pipe)
    n0 = len(srv.requests)
    srv.script = ["ok_judge"]
    await pipe.review_chapter(CH2, _snapshot("p", 1, out1.canon_delta), out2.canon_delta, chapter_no=2)
    ext = srv.requests[0]["body"]
    assert ext.get("reasoning_effort") == "high" and ext.get("reasoning") == {"enabled": False}
    for r in srv.requests[n0:]:
        assert "reasoning_effort" not in r["body"] and r["body"].get("reasoning") == {"enabled": False}


async def test_live_lock_waits_and_foreign_lock_not_deleted(fake_or, tmp_path):
    import os
    import socket

    srv, pipe, root = fake_or
    d = root / "p"
    d.mkdir(parents=True)
    lock = d / ".lock"
    # 살아 있는 '다른' 프로세스 = 부모(pytest 를 띄운 셸). 내 pid 는 쓰지 않는다 — 내 pid 인데
    # 내가 쥔 토큰이 아니면 재시작 전 프로세스가 남긴 잠금으로 본다(아래 pid 재사용 시험).
    lock.write_text(json.dumps({"pid": os.getppid(), "host": socket.gethostname(), "token": "other"}),
                    encoding="utf-8")
    st = W.WeaveStore("p", root)
    with pytest.raises(W.WeaveError, match="다른 분석"):
        with st.lock(timeout=0.6):
            pass
    assert json.loads(lock.read_text(encoding="utf-8"))["token"] == "other"  # 남의 잠금은 그대로
    lock.write_text(json.dumps({"pid": 999999, "host": socket.gethostname(), "token": "dead"}),
                    encoding="utf-8")
    with st.lock(timeout=2):
        assert json.loads(lock.read_text(encoding="utf-8"))["token"] != "dead"
    assert not lock.exists()


def test_lock_left_by_previous_process_with_same_pid_is_cleared(tmp_path):
    """컨테이너 재시작: worker 가 다시 pid 1 로 뜨면 이전 생의 잠금도 pid 1·같은 host 다.
    pid 만 보면 '살아 있음'이라 영원히 기다린다 — 이 프로세스가 쥔 토큰이 아니면 회수해야 한다."""
    import os
    import socket

    d = tmp_path / "p"
    d.mkdir()
    lock = d / ".lock"
    lock.write_text(json.dumps({"pid": os.getpid(), "host": socket.gethostname(), "token": "previous-life"}),
                    encoding="utf-8")
    t0 = time.monotonic()
    with W.WeaveStore("p", tmp_path).lock(timeout=2):
        assert json.loads(lock.read_text(encoding="utf-8"))["token"] != "previous-life"
    assert time.monotonic() - t0 < 1.5 and not lock.exists()


def test_same_process_holder_is_not_mistaken_for_previous_life(tmp_path):
    """같은 프로세스의 다른 작업(WORKER_MAX_JOBS>1)이 쥔 잠금은 pid 가 같아도 기다린다."""
    st1, st2 = W.WeaveStore("p", tmp_path), W.WeaveStore("p", tmp_path)
    with st1.lock(timeout=2):
        with pytest.raises(W.WeaveError, match="다른 분석"):
            with st2.lock(timeout=0.6):
                pass
        assert (tmp_path / "p" / ".lock").exists()  # 쥔 쪽 잠금이 지워지지 않았다
    assert not (tmp_path / "p" / ".lock").exists()


def test_lock_without_heartbeat_is_cleared_even_from_other_host(tmp_path):
    """SIGKILL 등으로 heartbeat 가 멈춘 잠금은 host·pid 와 무관하게 LOCK_STALE_SECONDS 뒤 회수."""
    import os

    d = tmp_path / "p"
    d.mkdir()
    lock = d / ".lock"
    lock.write_text(json.dumps({"pid": 1, "host": "some-other-host", "token": "gone"}), encoding="utf-8")
    st = W.WeaveStore("p", tmp_path)
    with pytest.raises(W.WeaveError, match="다른 분석"):  # 방금 갱신된 다른 기계의 잠금은 기다린다
        with st.lock(timeout=0.6):
            pass
    old = time.time() - W.WeaveStore.LOCK_STALE_SECONDS - 5
    os.utime(lock, (old, old))
    with st.lock(timeout=2):
        assert json.loads(lock.read_text(encoding="utf-8"))["token"] != "gone"
    assert not lock.exists()


def test_lock_heartbeat_keeps_mtime_fresh_while_held(tmp_path, monkeypatch):
    """LLM 호출 1건이 길어도(조각 사이 heartbeat 없음) 쥔 동안은 mtime 이 계속 갱신된다."""
    monkeypatch.setattr(W.WeaveStore, "LOCK_HEARTBEAT_SECONDS", 0.1)
    st = W.WeaveStore("p", tmp_path)
    lock = tmp_path / "p" / ".lock"
    with st.lock(timeout=2):
        import os

        old = time.time() - 1000
        os.utime(lock, (old, old))
        time.sleep(0.5)
        assert time.time() - lock.stat().st_mtime < 5
    assert not lock.exists()
    assert not any(t.name.startswith("weave-lock-") for t in threading.enumerate())  # 스레드 정리됨
