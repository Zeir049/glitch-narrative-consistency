# -*- coding: utf-8 -*-
"""
Weave 체커 dev set 회귀 게이트 — 30장면 코퍼스를 checker 계층에만 통과시켜 집계한다.

Weave 저장소 `run_eval_checker.py`(event_sourcing 8d89349)의 `run()`을 옮긴 것.
LLM·네트워크 호출 없음. 원장은 `weave_checker_corpus/episode_0N.json` 5화를 재생해
만들고, 각 장면은 5화 종료 시점 원장을 기준으로 **독립** 판정한다(장면 간 전파 없음).

이 숫자는 Weave가 체커를 고칠 때 기준선으로 쓴 개발용 데이터다. 일반화 성능이 아니라
"코어 재동기화·어댑터 변경 뒤에도 같은 장면을 같은 등급으로 잡는가"를 보는 회귀 게이트.
n이 작아(checker 담당 라벨 4건) 정밀도·재현율·F1은 쓰지 않고 원시 카운트와 장면 ID 집합으로 적는다.

실행:
  PYTHONIOENCODING=utf-8 python -m tests.eval.checker_eval
기준선·갱신 절차: docs/EVAL.md
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import app.pipeline

# weave_core 는 평면 import(`from ledger import ...`)를 쓴다 — 원본 무수정을 위해
# app/pipeline/weave.py 와 같은 방식으로 경로만 연다. 코어 파일은 고치지 않는다.
CORE_DIR = Path(app.pipeline.__file__).resolve().parent / "weave_core"
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

import checker  # noqa: E402
from ledger import compile_ledger, load_registry  # noqa: E402

CORPUS_DIR = Path(__file__).resolve().parent / "weave_checker_corpus"
WEAVE_COMMIT = "8d89349"


def load_labels(corpus_dir: Path) -> dict[str, dict]:
    data = json.loads((corpus_dir / "labels.json").read_text(encoding="utf-8"))
    return {lab["scene_id"]: lab for lab in data["labels"]}


def scene_events(corpus_dir: Path):
    """scene_XX_event.json 을 (scene_id, event) 로 순서대로 내놓는다."""
    for path in sorted(corpus_dir.glob("scene_*_event.json")):
        scene_id = path.name.replace("_event.json", "")
        data = json.loads(path.read_text(encoding="utf-8"))
        for ev in data.get("events", []):
            yield scene_id, ev


def _brief(c: dict) -> dict:
    return {k: c.get(k) for k in ("type", "entity", "field", "premise", "ledger_value", "since", "detail")}


def run(corpus_dir: Path = CORPUS_DIR) -> dict:
    """코퍼스 전체를 checker 에 통과시켜 장면 ID 집합과 카운트를 돌려준다. 순수 함수."""
    ledger = compile_ledger(base=corpus_dir)
    _, mapping = load_registry(corpus_dir)
    labels = load_labels(corpus_dir)

    scenes: dict[str, dict] = {}
    for scene_id, ev in scene_events(corpus_dir):
        findings = checker.check_event(ev, ledger, mapping)
        conflicts = checker.conflicts_of(findings)
        lab = labels.get(scene_id, {})
        entry = scenes.setdefault(scene_id, {
            "scene_id": scene_id,
            "label_has_contradiction": lab.get("has_contradiction"),
            "label_type": lab.get("type"),
            "label_expected_layer": lab.get("expected_layer"),
            "hard": [], "soft": [],
        })
        entry["hard"] += [_brief(c) for c in conflicts if c["severity"] == checker.SEV_HARD]
        entry["soft"] += [_brief(c) for c in conflicts if c["severity"] == checker.SEV_SOFT]

    hard_scenes = {s for s, v in scenes.items() if v["hard"]}
    soft_scenes = {s for s, v in scenes.items() if v["soft"]}  # Weave 원본과 같이 hard 와 겹칠 수 있다
    flagged = hard_scenes | soft_scenes
    normal_scenes = {s for s, v in scenes.items() if v["label_has_contradiction"] is False}
    contradiction_scenes = {s for s, v in scenes.items() if v["label_has_contradiction"] is True}
    checker_labeled = {s for s, v in scenes.items() if v["label_expected_layer"] == "checker"}

    checker_caught = checker_labeled & flagged
    return {
        "weave_commit": WEAVE_COMMIT,
        "scene_count": len(scenes),
        "hard_scenes": sorted(hard_scenes),
        "soft_scenes": sorted(soft_scenes),
        "flagged_scenes": sorted(flagged),
        "normal_scenes": sorted(normal_scenes),
        "contradiction_scenes": sorted(contradiction_scenes),
        "false_positive_scenes": sorted(flagged & normal_scenes),
        "checker_labeled": sorted(checker_labeled),
        "checker_caught": sorted(checker_caught),
        "checker_caught_hard": sorted(checker_caught & hard_scenes),
        "checker_caught_soft": sorted(checker_caught & soft_scenes - hard_scenes),
        "checker_missed": sorted(checker_labeled - flagged),
        "contradiction_caught": sorted(contradiction_scenes & flagged),
        "counts": {
            "scenes": len(scenes),
            "normal": len(normal_scenes),
            "contradiction": len(contradiction_scenes),
            "checker_labeled": len(checker_labeled),
            "hard": len(hard_scenes),
            "soft": len(soft_scenes),
            "false_positive": len(flagged & normal_scenes),
            "checker_caught": len(checker_caught),
            "checker_missed": len(checker_labeled - flagged),
            "contradiction_caught": len(contradiction_scenes & flagged),
        },
        "scenes": scenes,
    }


def format_table(result: dict) -> str:
    c = result["counts"]
    join = lambda ids: ", ".join(ids) or "(없음)"  # noqa: E731
    return "\n".join([
        f"Weave 체커 dev set 회귀 게이트 (event_sourcing {result['weave_commit']}, "
        f"{c['scenes']}장면, LLM 호출 없음)",
        f"정상 {c['normal']}건 중 플래그           : {c['false_positive']}"
        + (f" ({join(result['false_positive_scenes'])})" if c["false_positive"] else ""),
        f"checker 담당 라벨 {c['checker_labeled']}건 중 잡음  : {c['checker_caught']} "
        f"(강 {len(result['checker_caught_hard'])}, 약 {len(result['checker_caught_soft'])})"
        f"  놓침: {join(result['checker_missed'])}",
        f"라벨 모순 {c['contradiction']}건 중 checker 플래그: {c['contradiction_caught']}",
        f"강: {join(result['hard_scenes'])} / 약: {join(result['soft_scenes'])}",
    ])


def main() -> None:
    print(format_table(run()))


if __name__ == "__main__":
    main()
