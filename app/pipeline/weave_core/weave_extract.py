# -*- coding: utf-8 -*-
"""
weave_extract.py — 원문 → 사건 JSON 추출 계층 (제품 계층)

`추출_프롬프트_v2_2.md`를 시스템 프롬프트로, [기존 엔티티 목록]과 [신규 화 원문]을
사용자 메시지로 넣어 호출한다. 프롬프트 본문은 **한 글자도 가공하지 않는다** —
파일을 그대로 읽어 쓴다.

백엔드
------
호출은 `weave_llm`이 담당한다. project.json의 `llm.provider`가 `nim`/`anthropic`/
`manual` 중 하나를 고른다. 이 파일은 **프롬프트만 만들고 호출은 위임**한다 —
"바뀌는 것은 누가 추출하나뿐"을 코드 경계로 보장하기 위한 것이다.
provider가 `manual`이거나 키가 없으면 프롬프트를 파일로 떨어뜨리고 멈춘다.

엔티티 목록의 격리
------------------
추출기에 넘기는 엔티티 목록은 `id / name(expression) / entity_type`만 담는다.
`note`·`first_episode`·`new_label`·`worldview_rules`는 **넣지 않는다** —
Track 2에서 확인한 대로 원장 상태나 세계관 규칙이 섞이면 추출기가 정답을
조준하게 되고, 그렇게 만든 추출로는 시스템 성능을 알 수 없다
(`handoff/track2_report.md` §5, `track2_scoring.md` §2-1).
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import weave_llm

BASE = Path(__file__).resolve().parent
DEFAULT_PROMPT = "추출_프롬프트_v2_2.md"
DEFAULT_MODEL = "claude-sonnet-4-5"


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- 입력 구성

def entity_list_for_extractor(reg: dict) -> dict:
    """추출기에 줄 [기존 엔티티 목록] — 원장 정보 제거본."""
    # note는 "무엇을 제거했는지" 설명하지 않는다 — 그 자체가 메타 단서가 된다
    # (`handoff/track2_report.md` §13-8: 원장을 지우려고 만든 파일이 스스로
    #  그 사실을 광고했던 사례).
    out = {
        "note": "이 작품에서 지금까지 확정된 엔티티 목록이다.",
        "characters": [{"id": e["id"], "name": e.get("name", ""),
                        "entity_type": e.get("entity_type", "unknown")}
                       for e in reg.get("characters", [])],
        "locations": [{"id": e["id"], "name": e.get("name", "")}
                      for e in reg.get("locations", [])],
        "objects": [{"id": e["id"], "name": e.get("name", "")}
                    for e in reg.get("objects", [])],
        "times": [{"id": e["id"], "expression": e.get("expression", "")}
                  for e in reg.get("times", [])],
    }
    for coll in ("characters", "locations", "objects"):
        for src, dst in zip(reg.get(coll, []), out[coll]):
            if src.get("aliases"):
                dst["aliases"] = list(src["aliases"])
    return out


def build_prompt(text: str, reg: dict, episode: int, cfg: dict,
                 base: Path = BASE) -> tuple[str, str]:
    """(system, user) 두 문자열을 만든다. system은 프롬프트 파일 원문 그대로."""
    name = (cfg.get("extraction") or {}).get("prompt") or DEFAULT_PROMPT
    path = base / name
    if not path.exists():
        raise FileNotFoundError(f"추출 프롬프트가 없습니다: {path}")
    system = path.read_text(encoding="utf-8")

    timelines = [t for t in (cfg.get("timelines") or ["main"])]
    # 정체 판정 규칙 (2026-08-28 신설, 조건 태그 "추출 노트 v1") — 공개 원작
    # 코퍼스에서 추출기가 정전 암기로 무명 인물의 정체를 지어내는 오염이 실측됨.
    # cfg["extraction"]["user_note"]가 명시로 꺼지지 않는 한 항상 넣는다.
    note = (cfg.get("extraction") or {}).get("user_note", (
        "[정체 판정 규칙] 인물·장소·사물의 정체는 이 원문 텍스트가 확정하는 "
        "경우에만 기존 ID로 참조하라. 원작·정전·일반 상식 등 원문 밖 지식으로 "
        "정체를 추정하는 것은 금지한다. 원문이 아직 이름·정체를 밝히지 않은 "
        "대상은 기존 목록에 있을 것 같아도 NEW:서술형_라벨로 선언하라 "
        "(예: NEW:베일을_쓴_부인). 잘못된 병합은 잘못된 분리보다 해롭다."))
    user = [
        f"[시간대 목록]  이 작품의 timeline은 다음뿐이다: {timelines}",
        "  이 목록에 없는 값을 timeline에 쓰지 마라. 회상·꿈·환상은 timeline이 아니라",
        "  reality_status(memory/dream/vision)로 표현한다.",
        "",
        "[기존 엔티티 목록]",
        json.dumps(entity_list_for_extractor(reg), ensure_ascii=False, indent=2),
        "",
        *( [note, ""] if note else [] ),
        f"[신규 화 원문]  (episode = {episode})",
        text,
        "",
        f"위 원문을 규격대로 추출하라. episode 필드는 {episode}로 한다.",
        "JSON 외 텍스트를 출력하지 마라.",
    ]
    return system, "\n".join(user)


# ---------------------------------------------------------------- 호출

def _parse_json(text: str) -> dict:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n", "", t)
        t = re.sub(r"\n```$", "", t.rstrip())
    start, end = t.find("{"), t.rfind("}") + 1
    if start < 0 or end <= start:
        raise ValueError("JSON을 찾지 못했습니다")
    return json.loads(t[start:end])


def available(cfg: dict | None = None) -> tuple[bool, str]:
    """설정의 provider가 실제로 호출 가능한지. cfg가 없으면 anthropic 기준(구 동작)."""
    llm = weave_llm.llm_of(cfg or {})
    if llm["provider"] == "manual":
        return False, "provider=manual"
    return weave_llm.available(llm["provider"])


def extract(text: str, reg: dict, episode: int, cfg: dict, out_dir: Path,
            mode: str = "auto", base: Path = BASE) -> dict:
    """
    반환:
      {"status": "ok", "document": {...}, "meta": {...}}
      {"status": "manual", "prompt_path": "...", "message": "..."}
      {"status": "error", "message": "..."}
    """
    system, user = build_prompt(text, reg, episode, cfg, base)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    llm = weave_llm.llm_of(cfg)
    model = llm.get("extract_model") or llm.get("model")

    # mode 인자는 호출자가 강제로 manual을 요구할 때만 의미가 있다.
    ok, why = available(cfg)
    if mode == "manual" or llm["provider"] == "manual" or not ok:
        p = out_dir / f"extract_request_ep{episode:02d}.txt"
        p.write_text(system + "\n\n" + "=" * 70 + "\n[사용자 메시지]\n"
                     + "=" * 70 + "\n\n" + user, encoding="utf-8")
        target = out_dir / f"episode_{episode:02d}.json"
        return {"status": "manual", "prompt_path": str(p), "target": str(target),
                "reason": why if not ok else "manual 모드",
                "message": f"프롬프트를 저장했습니다: {p}\n"
                           f"이 프롬프트로 추출한 JSON을 {target} 에 넣고 "
                           f"`weave.py add`를 --skip-extract 로 다시 실행하세요."}

    res = weave_llm.complete_json(system, user, llm, role="extract", base=base)
    raw = res.get("raw") or res.get("text") or ""
    if raw:
        (out_dir / f"extract_raw_ep{episode:02d}.txt").write_text(raw, encoding="utf-8")

    if res["status"] != "ok":
        return {"status": "error",
                "message": res.get("message") or f"추출 실패 (status={res['status']})",
                "retried": res.get("retried", False),
                "parse_retried": res.get("parse_retried", False),
                "raw_path": str(out_dir / f"extract_raw_ep{episode:02d}.txt") if raw else None}

    doc = res["data"]
    doc.setdefault("schema_version", 2)
    doc["episode"] = episode
    doc["_extraction"] = {
        "produced_by": f"weave_extract / {llm['provider']} / {model}",
        "prompt": (cfg.get("extraction") or {}).get("prompt") or DEFAULT_PROMPT,
        "produced_at": now(),
        "timelines_given": list(cfg.get("timelines") or ["main"]),
        "temperature": llm.get("temperature"),
        "effort": llm.get("effort"),
        "retried": res.get("retried", False),
        "parse_retried": res.get("parse_retried", False),
        "attempts": res.get("attempts"),
        "elapsed_sec": res.get("elapsed_sec"),
    }
    return {"status": "ok", "document": doc,
            "meta": {"provider": llm["provider"], "model": model,
                     "usage": res.get("usage"), "retried": res.get("retried", False),
                     "parse_retried": res.get("parse_retried", False),
                     "elapsed_sec": res.get("elapsed_sec")}}


# ---------------------------------------------------------------- 원문 분할

def split_episodes(text: str, delimiter: str | None = None) -> list[str]:
    """원문 파일을 화 단위로 나눈다.

    delimiter가 없으면 통째로 1화로 본다. 정규식으로 해석하며 MULTILINE을 켠다
    (`^---$` 같은 줄 단위 구분자가 흔하므로). 잘못된 정규식이면 리터럴로 취급한다.
    """
    if not delimiter:
        return [text]
    try:
        parts = re.split(delimiter, text, flags=re.MULTILINE)
    except re.error:
        parts = text.split(delimiter)
    return [p.strip() for p in parts if p and p.strip()]
