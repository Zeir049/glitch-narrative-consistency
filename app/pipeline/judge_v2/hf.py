"""학습 judge v2 HF 점수기(실제 모델). torch·transformers 는 이 모듈 안에서만 지연 import 한다.

p(모순) = sigmoid((l_모순 − logsumexp(나머지)) / T) = 온도 T 를 적용한 softmax[모순]. 입력은 render.render_ids(맥락 먼저, only_first).
온도·기준값은 설정값이 있으면 그것을, 없으면 모델 폴더의 v2_meta.json 을 쓴다.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Sequence

from app.pipeline.judge_v2.render import MAX_LENGTH, render_ids


def read_meta(model_dir: str | Path) -> dict:
    p = Path(model_dir) / "v2_meta.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


class HFScorer:
    def __init__(self, model_dir: str, *, device: str = "auto", temperature: float | None = None, batch_size: int = 16) -> None:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device, self.batch = device, batch_size
        self.tok = AutoTokenizer.from_pretrained(model_dir)
        self.model = AutoModelForSequenceClassification.from_pretrained(model_dir).to(device).eval()
        id2 = {int(k): v for k, v in self.model.config.id2label.items()}
        self.ci = next(i for i, v in id2.items() if v == "모순" or str(v).lower() == "contradiction")
        self.t = float(temperature if temperature is not None else read_meta(model_dir).get("temperature") or 1.0)

    def score(self, items: Sequence[tuple[Sequence[str], str]]) -> list[float]:
        import torch

        encs = [render_ids(self.tok, claim, ctx, MAX_LENGTH) for ctx, claim in items]
        order = sorted(range(len(encs)), key=lambda i: len(encs[i]["input_ids"]))
        out = [0.0] * len(encs)
        with torch.no_grad():
            for s in range(0, len(order), self.batch):
                idx = order[s:s + self.batch]
                n = max(len(encs[i]["input_ids"]) for i in idx)
                pad = self.tok.pad_token_id
                b = {k: torch.tensor([encs[i][k] + [pad if k == "input_ids" else 0] * (n - len(encs[i][k])) for i in idx],
                                     device=self.device) for k in ("input_ids", "attention_mask", "token_type_ids")}
                with torch.autocast(device_type="cuda", dtype=torch.float16, enabled=(self.device == "cuda")):
                    lg = self.model(**b).logits.float()
                others = torch.cat([lg[:, :self.ci], lg[:, self.ci + 1:]], dim=1)
                z = (lg[:, self.ci] - torch.logsumexp(others, dim=1)) / self.t
                for i, v in zip(idx, z.tolist()):
                    out[i] = 1 / (1 + math.exp(-v)) if v >= 0 else math.exp(v) / (1 + math.exp(v))
        return out
