# -*- coding: utf-8 -*-
"""라벨 없는 작품(dracula)의 확정 원장 만들기 — 어댑터 추출 경로(app.pipeline.weave.extract_episode) 그대로 5화 추출.

갈래 A(label_pairs.py)는 확정 원장이 있어야 쌍을 만든다. 4코퍼스(WM·Q1·Q3·셜록)는 원장이 있고, dracula 는
원문(번역 5화)만 있으므로 먼저 이 스크립트로 추출한다. **기본은 --dry-run(호출 0회, 조각 수·예상 토큰만)**.
실제 추출은 --run 을 줄 때만 하며, 대량 호출이므로 비용 추정을 보고하고 승인을 받은 뒤 실행한다.

산출(--out 기본 build/dracula_ledger — datagen_common.DRACULA_LEDGER 와 같다)
  episodes/episode_01..05.json  registry.json  label_map.json  raw/  (WeaveStore 배치 그대로)
조각 크기는 settings.weave_chunk_chars(WEAVE_CHUNK_CHARS, 기본 2500 — nemotron 무료 열 확정 조건).
회차는 순서대로(앞 회차 레지스트리를 이어 받는다). 이미 있는 회차는 --force 없으면 건너뛴다.

실행
  PYTHONIOENCODING=utf-8 python scripts/eval/extract_work.py --work dracula            # 추정만
  WEAVE_LLM_RECORD=record WEAVE_LLM_RECORD_DIR=build/llm_record \\
    PYTHONIOENCODING=utf-8 python scripts/eval/extract_work.py --work dracula --run     # 승인 뒤
  PYTHONIOENCODING=utf-8 python scripts/eval/label_pairs.py --work dracula --dry-run
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
if str(EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(EVAL_DIR))

import datagen_common as C  # noqa: E402

logger = logging.getLogger("extract_work")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--work", default="dracula", choices=C.ALL_DATA_WORKS)
    ap.add_argument("--sources", type=Path)
    ap.add_argument("--out", type=Path, default=C.DRACULA_LEDGER)
    ap.add_argument("--episodes", default="1,2,3,4,5")
    ap.add_argument("--run", action="store_true", help="실제 추출(LLM 호출). 없으면 추정만")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--chars-per-token", type=float, default=1.6)
    ap.add_argument("--out-tokens-per-chunk", type=int, default=3500, help="추정용 조각당 출력 토큰")
    ap.add_argument("--registry-tokens", type=int, default=3000, help="추정용 조각당 레지스트리 입력 토큰(평균)")
    ap.add_argument("--price-in", type=float)
    ap.add_argument("--price-out", type=float)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from app.core.config import settings
    from app.pipeline import weave as W

    eps = [int(x) for x in a.episodes.split(",") if x.strip()]
    prompt_chars = len((W.CORE_DIR / W.EXTRACT_PROMPT).read_text(encoding="utf-8"))
    plan, n_chunks, in_tok, out_tok = [], 0, 0, 0
    for ep in eps:
        text = C.load_source(a.work, ep, a.sources)
        chunks = W.chunk_text(text, settings.weave_chunk_chars)
        n_chunks += len(chunks)
        t_in = sum(C.chars_to_tokens(prompt_chars + len(c), a.chars_per_token) + a.registry_tokens for c in chunks)
        in_tok += t_in
        out_tok += len(chunks) * a.out_tokens_per_chunk
        plan.append({"episode": ep, "chars": len(text), "chunks": len(chunks), "input_tokens": t_in})
    model = settings.weave_extract_model or settings.weave_llm_model
    p = C.price_of(model, (a.price_in, a.price_out))
    est = {"work": a.work, "model": model, "chunk_chars": settings.weave_chunk_chars, "episodes": plan,
           "calls": n_chunks, "input_tokens": in_tok, "output_tokens": out_tok,
           "usd": round(in_tok / 1e6 * p[0] + out_tok / 1e6 * p[1], 2) if p else None,
           "note": "JSON 파싱 실패 재요청·연결 재시도는 빼고 센 하한"}
    print(json.dumps(est, ensure_ascii=False, indent=2))
    if not a.run:
        return

    W._api_key_ready()
    store = W.WeaveStore(a.out.name, root=a.out.parent)
    cfg = W.weave_config()
    for ep in eps:
        if store.episode_path(ep).exists() and not a.force:
            logger.info("%s화: 이미 있음 — 건너뜀", ep)
            continue
        t0 = time.time()
        with store.lock():
            doc, reg, label_map, pending = W.extract_episode(C.load_source(a.work, ep, a.sources), ep, store, cfg)
            store.write_json(store.registry_path, reg)
            store.write_json(store.label_map_path, label_map)
            store.write_json(store.episode_path(ep), doc)
        logger.info("%s화: 사건 %s · 조각 %s · 보류 라벨 %s · %.0fs", ep, len(doc["events"]), doc["_chunks"],
                    len(pending), time.time() - t0)


if __name__ == "__main__":
    main()
