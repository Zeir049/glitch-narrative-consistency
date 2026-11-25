"""NLI 모델의 dev 정확도(3분류) — KLUE-NLI validation(로컬 캐시, 오프라인)과 KorNLI fiction 검증 10%(학습과 같은 분할).

    python scripts/eval/nli_dev_acc.py --model-dir C:\\judge_models\\E1L --out X.json [--device cuda]

모델 라벨은 학습 judge 라벨(모순/정상/무관)이다. KLUE-NLI 는 train_judge.KLUE_NLI_MAP 으로 같은 칸에 맞춘다(entailment→정상,
neutral→무관, contradiction→모순). KorNLI 검증은 train_judge 와 같은 TL.prepare(val_frac 0.1, data_seed 42) 분할이다.
입력 순서는 학습과 같다(premise, hypothesis). 토큰 상한은 모델 meta max_length(E1·E4·E1L 은 128).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import train_lib as TL  # noqa: E402

KORNLI = Path(r"C:\judge_models\ext\pairs_ext_kornli.jsonl")


def accuracy(model_dir: Path, rows: list[dict], device: str, max_length: int, batch: int = 64) -> dict:
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(str(model_dir))
    model = AutoModelForSequenceClassification.from_pretrained(str(model_dir)).to(device).eval()
    if device == "cuda":
        model = model.half()
    l2i = {v: int(k) for k, v in model.config.id2label.items()}
    correct, n, conf = 0, 0, {}
    with torch.no_grad():
        for i in range(0, len(rows), batch):
            b = rows[i:i + batch]
            enc = tok([r["premise"] for r in b], [r["hypothesis"] for r in b], truncation=True,
                      max_length=max_length, padding=True, return_tensors="pt").to(device)
            pred = model(**enc).logits.float().argmax(-1).tolist()
            for r, p in zip(b, pred):
                g = l2i[r["label"]]
                correct += int(p == g)
                n += 1
                conf[f"{r['label']}->{model.config.id2label[p]}"] = conf.get(f"{r['label']}->{model.config.id2label[p]}", 0) + 1
    return {"n": n, "acc": round(correct / n, 4), "confusion": dict(sorted(conf.items()))}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--model-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--device", default="cuda")
    a = ap.parse_args(argv)
    os.environ.setdefault("HF_DATASETS_OFFLINE", "1")
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    from datasets import load_dataset

    import train_judge as TJ

    meta = json.loads((a.model_dir / "judge_meta.json").read_text(encoding="utf-8"))
    ml = int(meta.get("max_length") or 128)
    klue = load_dataset("klue/klue", "nli", split="validation")
    klue_rows = [{"premise": x["premise"], "hypothesis": x["hypothesis"], "label": TL.LABELS[TJ.KLUE_NLI_MAP[x["label"]]]}
                 for x in klue if x["label"] in TJ.KLUE_NLI_MAP]
    split = TL.prepare(TL.read_jsonl(KORNLI), val_frac=0.1, seed=42)
    t0 = time.time()
    out = {"model_dir": str(a.model_dir), "model_sha256": TL.sha256_file(a.model_dir / "model.safetensors"),
           "max_length": ml, "klue_nli_validation": accuracy(a.model_dir, klue_rows, a.device, ml),
           "kornli_fiction_val10": accuracy(a.model_dir, split.val, a.device, ml)}
    out["sec"] = round(time.time() - t0, 1)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: (v if not isinstance(v, dict) else {kk: vv for kk, vv in v.items() if kk != "confusion"})
                      for k, v in out.items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
