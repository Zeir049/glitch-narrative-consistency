"""학습 judge v2 공통 — 데이터 읽기·라벨·모델 읽기·문장 확률·장면 구성(S)·지표. 학습·채점·D 생성이 같이 쓴다."""
from __future__ import annotations

import hashlib
import json
import math
import random
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "eval"))

from app.pipeline.judge_v2 import build_scene, render_ids  # noqa: E402
from app.pipeline.judge_v2.render import MAX_LENGTH, RENDER_VERSION  # noqa: E402
from app.pipeline.judge_v2.retrieve import RETRIEVE_VERSION  # noqa: E402
from app.pipeline.judge_v2.text import TEXT_VERSION  # noqa: E402

CONTRA = "모순"
POS_LABELS = {CONTRA, "contradiction", 1, True, "1"}
VERSIONS = {"text": TEXT_VERSION, "retrieve": RETRIEVE_VERSION, "render": RENDER_VERSION}


def read_jsonl(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_jsonl(path: Path, rows: Iterable[dict]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            n += 1
    return n


def sha256_file(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def y_of(row: dict) -> int:
    """쌍 행 라벨 → 1(모순)/0(무모순·그 밖)."""
    return 1 if row.get("label") in POS_LABELS else 0


def contexts_of(row: dict) -> list[str]:
    c = row.get("context")
    if c is None:
        c = row.get("contexts")
    if isinstance(c, str):
        return [c]
    return [str(x) for x in (c or [])]


# ---------------------------------------------------------------- S(장면 묶음) → 장면

def scenes_of(s_rows: Sequence[dict], names_by_work: dict[str, list[str]] | None = None) -> list[dict]:
    """S 행 {id, work, split, prefix, target_P, target_N, planted, evidence} → 장면 2개(P 라벨 1, N 라벨 0).
    장면마다 items(주장·맥락·배정 순서)를 붙인다. P/N 은 같은 앞부분이라 검색 색인을 공유한다."""
    from app.pipeline.judge_v2.retrieve import Retriever, guess_names
    from app.pipeline.judge_v2.text import chunk_prefix

    out = []
    for r in s_rows:
        names = (names_by_work or {}).get(r.get("work")) if names_by_work else None
        ret = Retriever(chunk_prefix(r["prefix"]), guess_names(r["prefix"]) if names is None else names)
        for kind, y in (("P", 1), ("N", 0)):
            items = build_scene(r["prefix"], r[f"target_{kind}"], retriever=ret)
            out.append({"scene_id": f"{r['id']}:{kind}", "pair_id": r["id"], "kind": kind, "y": y,
                        "work": r.get("work"), "split": r.get("split"), "planted": r.get("planted"),
                        "type": r.get("type"), "items": items})
    return out


# ---------------------------------------------------------------- 모델

def contra_index(model: Any) -> int:
    id2 = {int(k): v for k, v in model.config.id2label.items()}
    for i, v in id2.items():
        if v == CONTRA or str(v).lower() == "contradiction":
            return i
    raise SystemExit(f"모델 라벨에 '{CONTRA}' 이 없다: {id2}")


def binary_logit(logits: Any, ci: int) -> Any:
    """3분류 로짓 → 이진 로짓 = l_모순 − logsumexp(나머지). sigmoid(이 값) = softmax[모순]."""
    import torch

    others = torch.cat([logits[:, :ci], logits[:, ci + 1:]], dim=1)
    return logits[:, ci] - torch.logsumexp(others, dim=1)


def collate(batch: list[dict], pad_id: int) -> dict:
    import torch

    n = max(len(b["input_ids"]) for b in batch)
    ids = [b["input_ids"] + [pad_id] * (n - len(b["input_ids"])) for b in batch]
    am = [b["attention_mask"] + [0] * (n - len(b["attention_mask"])) for b in batch]
    tt = [b["token_type_ids"] + [0] * (n - len(b["token_type_ids"])) for b in batch]
    return {"input_ids": torch.tensor(ids), "attention_mask": torch.tensor(am), "token_type_ids": torch.tensor(tt)}


def encode_item(tok: Any, claim: str, contexts: Sequence[str]) -> dict:
    r = render_ids(tok, claim, contexts, MAX_LENGTH)
    return {k: r[k] for k in ("input_ids", "attention_mask", "token_type_ids")}


def predict_logits(model: Any, tok: Any, encs: list[dict], device: str, batch: int = 16) -> list[float]:
    """이진 로짓 목록(no_grad, 길이 순으로 묶어 원래 순서로 돌려준다)."""
    import torch

    ci = contra_index(model)
    order = sorted(range(len(encs)), key=lambda i: len(encs[i]["input_ids"]))
    out = [0.0] * len(encs)
    model.eval()
    with torch.no_grad():
        for s in range(0, len(order), batch):
            idx = order[s:s + batch]
            b = {k: v.to(device) for k, v in collate([encs[i] for i in idx], tok.pad_token_id).items()}
            if "token_type_ids" not in getattr(model, "forward").__code__.co_varnames:
                b.pop("token_type_ids", None)
            with torch.autocast(device_type="cuda", dtype=torch.float16, enabled=(device == "cuda")):
                lg = model(**b).logits.float()
            for i, v in zip(idx, binary_logit(lg, ci).tolist()):
                out[i] = float(v)
    return out


def sigmoid(x: float, t: float = 1.0) -> float:
    z = x / t
    return 1 / (1 + math.exp(-z)) if z >= 0 else math.exp(z) / (1 + math.exp(z))


# ---------------------------------------------------------------- 지표

def auc(pos: Sequence[float], neg: Sequence[float]) -> float:
    w = 0.0
    for a in pos:
        for b in neg:
            w += 1.0 if a > b else 0.5 if a == b else 0.0
    return w / (len(pos) * len(neg))


def bootstrap_auroc(scores: dict[str, tuple[float, int]], n: int = 2000, seed: int = 0) -> dict:
    """층화 bootstrap(양성·음성 장면 각각 복원 추출) — train_lib.bootstrap_auroc 와 같은 규칙·시드 문자열."""
    import train_lib as TL

    return TL.bootstrap_auroc({k: (s, bool(y)) for k, (s, y) in scores.items()}, n, seed)


def pick_threshold(scores: Sequence[tuple[float, int]], max_fpr: float = 0.10) -> dict:
    """'정상 오탐 ≤ max_fpr 에서 recall 최대', 동률이면 높은 기준값."""
    pos = sorted(s for s, y in scores if y)
    neg = sorted(s for s, y in scores if not y)
    best = None
    for t in sorted({s for s, _ in scores} | {math.inf}):
        fp = sum(s >= t for s in neg)
        if neg and fp / len(neg) > max_fpr:
            continue
        tp = sum(s >= t for s in pos)
        cand = (tp, t)
        if best is None or cand[0] > best[0] or (cand[0] == best[0] and cand[1] > best[1]):
            best = cand
    tp, t = best
    fp = sum(s >= t for s in neg)
    return {"threshold": t, "tp": tp, "n_pos": len(pos), "fp": fp, "n_neg": len(neg), "max_fpr": max_fpr}


def fit_temperature(logits: Sequence[float], ys: Sequence[int]) -> float:
    """이진 로짓 온도(격자 → 황금분할, NLL 최소). 표본이 없으면 1."""
    if not logits:
        return 1.0

    def nll(t: float) -> float:
        s = 0.0
        for x, y in zip(logits, ys):
            p = min(max(sigmoid(x, t), 1e-7), 1 - 1e-7)
            s -= math.log(p) if y else math.log(1 - p)
        return s / len(logits)
    grid = [0.25 * 1.15 ** i for i in range(40)]
    t0 = min(grid, key=nll)
    lo, hi = t0 / 1.15, t0 * 1.15
    g = (math.sqrt(5) - 1) / 2
    for _ in range(40):
        a, b = hi - g * (hi - lo), lo + g * (hi - lo)
        if nll(a) < nll(b):
            hi = b
        else:
            lo = a
    return round((lo + hi) / 2, 6)


def claim_overlap(claim: str, contexts: Sequence[str]) -> float:
    """주장 ↔ 이어 붙인 맥락의 문자 2-gram Jaccard(혼합 점검·D 층화 표본의 '겹침' 정의, 하나로 둔다)."""
    from app.pipeline.judge_v2.retrieve import bigrams

    x, y = set(bigrams(claim)), set(bigrams(" ".join(contexts)))
    return len(x & y) / len(x | y) if x | y else 0.0


def seeded(name: str) -> random.Random:
    return random.Random(name)
