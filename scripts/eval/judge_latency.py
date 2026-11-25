"""학습 judge 회차 지연 측정 (관문 G1) + 결정성 확인 (관문 G2) — 설계안 §0-6·§3-1·§3-3.

합성 회차 하나(현재 6화 사건 30개를 장면 10개에 나눔 — 사건당 가설 약 4개, 1~5화 과거 사건 300개, 인물 10·장소 5·규칙 12)를
tests/test_weave_pipeline.py 의 사건 형식으로 만들고, 서비스와 같은 경로(build_pairs → HFBackend.predict)로
장면마다 판정 시간을 잰다. 가중치는 기초 모델이면 충분하다(시간은 가중치와 무관).

  --model     judge_meta.json 이 있는 모델 폴더, 또는 HF 이름/폴더(분류 머리 없는 사전학습 가중치).
              meta 가 없으면 --stub-dir 아래에 3클래스 머리를 붙인 사본 + 시간 측정용 meta 를 만든다(저장소 밖에).
  --precision meta 에 기록할 정밀도(stub 만). GPU 는 meta 를 따르고 CPU 는 항상 fp32(HFBackend 규칙).
  --device    cuda | cpu.  --threads  CPU 스레드.  --repeat  측정 반복(첫 1회는 예열로 버림).
결정성(G2): 같은 프로세스에서 반복마다 확률 배열을 float64 로 모아 tobytes() 를 비교하고 sha256 을 출력한다.
프로세스를 다시 띄워 같은 인자로 돌린 sha256 과 비교하면 재기동 결정성이다.

  CUBLAS_WORKSPACE_CONFIG=:4096:8 PYTHONIOENCODING=utf-8 python scripts/eval/judge_latency.py \\
      --model klue/roberta-base --stub-dir C:/judge_models/_latency --precision fp16 --device cuda --repeat 3
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.pipeline.pairs import PAIRS_VERSION, build_pairs  # noqa: E402

from ledger import Ledger  # noqa: E402 - 코어 경로는 pairs import 가 연다

CURRENT_EPISODE = 6
N_EVENTS, N_SCENES, N_PAST, N_CHARS, N_LOCS, N_RULES = 30, 10, 300, 10, 5, 12
FIELDS = ("body_status", "ability", "location_status", "mood")
STATES = ("오른 손목 골절", "왼쪽 다리 절뚝임", "고열로 쓰러짐", "목소리를 잃음", "시력이 흐려짐", "회복되어 멀쩡함",
          "깊은 잠에 빠짐", "칼에 베인 상처")
SURNAMES = "김이박최정강조윤장임"
GIVEN = ("서진", "도윤", "하은", "지후", "민서", "준우", "서아", "건우", "유나", "태오")


def synthetic_episode(seed: int = 0) -> dict:
    """결정적 합성 회차. 반환: registry, ledger, corpus, groups(장면 패킷), checker_items(장면별)."""
    rng = random.Random(f"judge_latency:{seed}")
    chars = [{"id": f"CHAR_{i + 1:03d}", "name": SURNAMES[i] + GIVEN[i], "entity_type": "person"} for i in range(N_CHARS)]
    locs = [{"id": f"LOC_{i + 1:03d}", "name": f"장소{i + 1}"} for i in range(N_LOCS)]
    rules = [{"id": f"RULE_{i + 1:03d}", "content": f"규칙 {i + 1}: 해가 진 뒤 {['성문', '서고', '탑', '항구'][i % 4]}에는 "
              f"허가 없는 자가 들어갈 수 없다", "certainty": "explicit"} for i in range(N_RULES)]
    registry = {"characters": chars, "locations": locs, "worldview_rules": rules}
    ids = [c["id"] for c in chars]
    led = Ledger(timelines=["main"], knows_mode="accumulate")
    corpus = []
    for k in range(N_PAST):  # 1~5화, 화당 60건
        ep, n = k // (N_PAST // 5) + 1, k % (N_PAST // 5) + 1
        eid = f"EVENT_{ep}_{n}"
        who = rng.sample(ids, 2)
        state = rng.choice(STATES)
        field = rng.choice(FIELDS)
        secret = f"비밀 {k % 40}: 지하 금고의 열쇠는 {rng.choice(['우물', '탑', '서고'])}에 있다"
        led.set("main", who[0], field, state, eid, "explicit")
        if k % 3 == 0:
            led.add_knowledge(who[1], secret, "direct", eid)
        corpus.append({"event_id": eid, "episode": ep, "global_idx": k + 1, "title": f"과거 사건 {k + 1}",
                       "summary": f"{chars[ids.index(who[0])]['name']}이(가) {state} 상태가 된다.",
                       "source_text": f"{chars[ids.index(who[0])]['name']}은(는) 그날 {state} 채로 "
                                      f"{rng.choice(locs)['name']}에 머물렀다. " * 2,
                       "participant_character_ids": who, "location_id": rng.choice(locs)["id"], "timeline": "main"})
    groups, checker = [], []
    per = N_EVENTS // N_SCENES
    for s in range(N_SCENES):
        evs, items = [], []
        for j in range(per):
            n = s * per + j + 1
            eid = f"EVENT_{CURRENT_EPISODE}_{n}"
            who = rng.sample(ids, 3)
            field = rng.choice(FIELDS)
            ev = {"event_id": eid, "order": n, "scene_id": f"S{s + 1}", "timeline": "main", "episode": CURRENT_EPISODE,
                  "title": f"현재 사건 {n}", "summary": f"{chars[ids.index(who[0])]['name']}이(가) 탑으로 달려간다.",
                  "source_text": f"{chars[ids.index(who[0])]['name']}은(는) 오른손으로 검을 쥐고 탑 계단을 뛰어올랐다. "
                                 f"해가 진 뒤였다. " * 2,
                  "participant_character_ids": who, "location_id": rng.choice(locs)["id"], "time_id": "unknown",
                  "reality_status": "actual",
                  "assumed_states": [{"entity": who[0], "field": field, "assumes": "멀쩡히 움직인다",
                                      "source_text": "오른손으로 검을 쥐고"},
                                     {"entity": who[1], "field": "body_status", "assumes": "걸을 수 있다",
                                      "source_text": "계단을 뛰어올랐다"}],
                  "state_changes": [{"entity": who[1], "field": rng.choice(FIELDS), "from": "unknown",
                                     "to": rng.choice(STATES), "source_text": "쓰러졌다"},
                                    {"entity": who[2], "field": "mood", "from": "unknown",
                                     "to": "분노", "source_text": "이를 악물었다"}],
                  "knowledge_gained": [{"character_id": who[2], "info": f"비밀 {n % 40}: 지하 금고의 열쇠는 탑에 있다",
                                        "via": "direct", "source_text": "열쇠를 보았다"}]}
            evs.append(ev)
            items.append({"event_id": eid, "entity": who[0], "field": field, "premise": "멀쩡히 움직인다",
                          "ledger_value": "오른 손목 골절", "since": "EVENT_1_1", "needs_judge": True})
        groups.append({"key": f"S{s + 1}", "events": evs})
        checker.append(items)
    return {"registry": registry, "ledger": led, "corpus": corpus, "groups": groups, "checker_items": checker}


def scene_pairs(ep: dict) -> list[list]:
    return [build_pairs(g, ep["ledger"], ep["corpus"], ep["registry"], items, current_episode=CURRENT_EPISODE)
            for g, items in zip(ep["groups"], ep["checker_items"])]


def ensure_model_dir(model: str, stub_dir: Path | None, precision: str, max_length: int, padding: str) -> Path:
    """judge_meta.json 이 있으면 그대로. 없으면 3클래스 머리를 붙인 사본 + 시간 측정용 meta(저장소 밖)."""
    from app.pipeline.learned_judge import LABELS, META_FILE
    p = Path(model)
    if (p / META_FILE).is_file():
        return p
    if stub_dir is None:
        raise SystemExit(f"{model} 에 {META_FILE} 가 없습니다 — --stub-dir 로 측정용 사본을 만들 폴더를 주세요")
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    out = stub_dir / f"{p.name}_{precision}_{max_length}"
    if not (out / META_FILE).is_file():
        out.mkdir(parents=True, exist_ok=True)
        m = AutoModelForSequenceClassification.from_pretrained(
            model, num_labels=3, id2label=dict(enumerate(LABELS)), label2id={x: i for i, x in enumerate(LABELS)})
        m.save_pretrained(str(out))
        AutoTokenizer.from_pretrained(model).save_pretrained(str(out))
        (out / META_FILE).write_text(json.dumps(
            {"labels": list(LABELS), "temperature": 1.0, "threshold": 0.5, "pairs_version": PAIRS_VERSION,
             "precision": precision, "attn_implementation": "eager", "max_length": max_length, "padding": padding,
             "base_model": model, "kind": "latency_stub (분류 머리 무작위 — 시간 측정 전용)"},
            ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="학습 judge 회차 지연(G1)·결정성(G2)")
    ap.add_argument("--model", required=True)
    ap.add_argument("--stub-dir", type=Path)
    ap.add_argument("--precision", choices=["fp16", "fp32"], default="fp16")
    ap.add_argument("--device", choices=["cuda", "cpu"], default="cuda")
    ap.add_argument("--threads", type=int)
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--max-length", type=int, default=384)
    ap.add_argument("--padding", choices=["bucket", "max_length"], default="bucket")
    ap.add_argument("--repeat", type=int, default=3, help="측정 반복(예열 1회 별도)")
    ap.add_argument("--seed", type=int, default=0, help="합성 회차 시드")
    ap.add_argument("--json-out", type=Path)
    a = ap.parse_args(argv)
    if a.device == "cuda" and os.environ.get("CUBLAS_WORKSPACE_CONFIG") is None:
        os.environ["CUBLAS_WORKSPACE_CONFIG"] = ":4096:8"  # CUDA 초기화 전

    import numpy as np

    from app.pipeline.learned_judge import HFBackend

    ep = synthetic_episode(a.seed)
    t0 = time.perf_counter()
    pairs = scene_pairs(ep)
    t_pairs = time.perf_counter() - t0
    model_dir = ensure_model_dir(a.model, a.stub_dir, a.precision, a.max_length, a.padding)
    t0 = time.perf_counter()
    be = HFBackend(str(model_dir), batch_size=a.batch_size, threads=a.threads, device=a.device)
    t_load = time.perf_counter() - t0

    def one_run() -> tuple[list[float], np.ndarray]:
        times, probs = [], []
        for ps in pairs:
            s = time.perf_counter()
            pr = be.predict(ps) if ps else []
            if be.device == "cuda":
                be._torch.cuda.synchronize()
            times.append(time.perf_counter() - s)
            probs += pr
        return times, np.asarray(probs, dtype=np.float64)

    one_run()  # 예열(커널 선택·캐시)
    runs = [one_run() for _ in range(max(1, a.repeat))]
    totals = [sum(t) for t, _ in runs]
    ref = runs[0][1].tobytes()
    identical = all(p.tobytes() == ref for _, p in runs[1:])
    scene_times = [t for t, _ in runs][len(runs) // 2]
    out = {"model": a.model, "model_dir": str(model_dir), "model_hash": be.model_hash, "device": be.device,
           "precision": be.precision, "attn_implementation": be.attn_implementation, "padding": be.padding,
           "max_length": be.max_length, "batch_size": be.batch_size, "pairs_version": PAIRS_VERSION,
           "synthetic": {"events": N_EVENTS, "scenes": N_SCENES, "past_events": N_PAST, "seed": a.seed},
           "pairs_total": sum(len(p) for p in pairs), "pairs_per_scene": [len(p) for p in pairs],
           "build_pairs_sec": round(t_pairs, 3), "load_sec": round(t_load, 2),
           "episode_sec": [round(x, 3) for x in totals], "episode_sec_median": round(statistics.median(totals), 3),
           "scene_sec_p50": round(statistics.median(scene_times), 3), "scene_sec_max": round(max(scene_times), 3),
           "repeat": len(runs), "in_process_identical": identical,
           "probs_sha256": hashlib.sha256(ref).hexdigest(), "env": be.env()}
    text = json.dumps(out, ensure_ascii=False, indent=1)
    print(text)
    if a.json_out:
        a.json_out.parent.mkdir(parents=True, exist_ok=True)
        a.json_out.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
