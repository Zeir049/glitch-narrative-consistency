"""학습 judge v2 학습(설계 문서 §10) — 1단계 이진 분류, 2단계 MIL.

1단계 `--stage pairs`: A·B·C·D 쌍 행 {id,type,work,split,claim,context,label,...}. 3분류 NLI 헤드에서 시작해
  이진 로짓(l_모순 − logsumexp(나머지))에 BCE. split=="dev" 행(작품 단위 dev)으로 조기 종료(dev BCE).
2단계 `--stage mil`: S 행 {id,work,split,prefix,target_P,target_N,...} → 장면(P=1, N=0). 장면마다 문장 로짓을 no_grad 로
  모두 내 argmax 문장을 고른 뒤, 그 문장만 grad 로 다시 계산해 BCE(max 로짓, 장면 라벨). max 의 기울기는 argmax 문장에만
  흐르므로 전체를 grad 로 계산한 것과 같다(메모리만 다르다). dev 장면 BCE 로 조기 종료.
공통: 끝나면 dev S 장면(있으면)으로 온도(T)·기준값('정상 오탐 ≤ 10% 에서 recall 최대')·장면 AUROC 를 meta 에 남긴다.

    python scripts/v2/train_v2.py --stage pairs --init C:\\judge_models\\E1 --train A.jsonl B.jsonl ... --dev-s S.jsonl --out DIR
    python scripts/v2/train_v2.py --stage mil   --init DIR1 --s S.jsonl --out DIR2
기존 모델 폴더는 덮어쓰지 않는다(--out 이 있으면 멈춘다).
"""
from __future__ import annotations

import argparse
import json
import logging
import random
import time
from pathlib import Path

import v2_common as C

log = logging.getLogger("train_v2")
TRACE: dict = {"loss_curve": [], "nonfinite_steps": 0, "steps": 0}
LOG_EVERY = 25   # 손실 곡선: 이 배치 수마다 평균 하나


def _track(v: float) -> None:
    """배치 손실 기록 — NaN/inf 수와 LOG_EVERY 배치 평균 곡선."""
    import math
    TRACE["steps"] += 1
    if not math.isfinite(v):
        TRACE["nonfinite_steps"] += 1
        return
    buf = TRACE.setdefault("_buf", [])
    buf.append(v)
    if len(buf) >= LOG_EVERY:
        TRACE["loss_curve"].append(round(sum(buf) / len(buf), 5))
        buf.clear()


def parse(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--stage", choices=("pairs", "mil"), required=True)
    ap.add_argument("--init", type=Path, required=True, help="시작 모델 폴더(E1·E1L 또는 1단계 결과)")
    ap.add_argument("--train", type=Path, nargs="*", default=[], help="(pairs) 쌍 행 jsonl 들")
    ap.add_argument("--s", type=Path, help="(mil) S 행 jsonl — split train 으로 학습, dev 로 조기 종료")
    ap.add_argument("--dev-s", type=Path, help="(공통) dev S 행 jsonl — 온도·기준값·장면 AUROC(없으면 --s 의 dev)")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--epochs", type=int, default=2)
    ap.add_argument("--patience", type=int, default=1)
    ap.add_argument("--lr", type=float, default=1e-5)
    ap.add_argument("--batch-size", type=int, default=8)
    ap.add_argument("--grad-accum", type=int, default=2)
    ap.add_argument("--warmup-ratio", type=float, default=0.06)
    ap.add_argument("--weight-decay", type=float, default=0.01)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--max-fpr", type=float, default=0.10)
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--gradient-checkpointing", action="store_true")
    ap.add_argument("--fixed", action="store_true",
                    help="고정 레시피(main 451bab2 §10): --epochs 를 다 돌고 마지막 체크포인트를 쓴다(조기 종료·최적 복원 없음), "
                         "온도 1.0·기준값 없음. dev 수치는 기록만(선택에 쓰지 않음)")
    ap.add_argument("--recipe", default=None, help="(--fixed) meta 에 남길 레시피 이름")
    return ap.parse_args(argv)


def load(init: Path, device: str, gc: bool):  # noqa: ANN201
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(str(init))
    model = AutoModelForSequenceClassification.from_pretrained(str(init)).to(device)
    if gc:
        model.gradient_checkpointing_enable()
    return tok, model


def optim(model, n_steps: int, a: argparse.Namespace):  # noqa: ANN001, ANN201
    import torch
    from transformers import get_linear_schedule_with_warmup

    nd = ("bias", "LayerNorm.weight")
    groups = [{"params": [p for n, p in model.named_parameters() if not any(x in n for x in nd)], "weight_decay": a.weight_decay},
              {"params": [p for n, p in model.named_parameters() if any(x in n for x in nd)], "weight_decay": 0.0}]
    opt = torch.optim.AdamW(groups, lr=a.lr)
    sch = get_linear_schedule_with_warmup(opt, int(n_steps * a.warmup_ratio), max(1, n_steps))
    return opt, sch


def bce_mean(logits: list[float], ys: list[int]) -> float:
    import math
    s = 0.0
    for x, y in zip(logits, ys):
        p = min(max(C.sigmoid(x), 1e-7), 1 - 1e-7)
        s -= math.log(p) if y else math.log(1 - p)
    return s / max(1, len(ys))


def scene_logits(model, tok, scenes: list[dict], device: str) -> list[list[float]]:  # noqa: ANN001
    encs, owner = [], []
    for si, sc in enumerate(scenes):
        for it in sc["items"]:
            encs.append(C.encode_item(tok, it.claim, it.contexts))
            owner.append(si)
    flat = C.predict_logits(model, tok, encs, device)
    out: list[list[float]] = [[] for _ in scenes]
    for si, v in zip(owner, flat):
        out[si].append(v)
    return out


def dev_report(model, tok, dev_scenes: list[dict], device: str, max_fpr: float) -> dict:  # noqa: ANN001
    if not dev_scenes:
        return {}
    per = scene_logits(model, tok, dev_scenes, device)
    mx = [max(v, default=-30.0) for v in per]
    ys = [sc["y"] for sc in dev_scenes]
    t = C.fit_temperature(mx, ys)
    probs = [C.sigmoid(x, t) for x in mx]
    sc = {s["scene_id"]: (p, y) for s, p, y in zip(dev_scenes, probs, ys)}
    return {"n_scenes": len(dev_scenes), "temperature": t, "scene_auroc": C.bootstrap_auroc(sc, 2000, 0),
            "threshold": C.pick_threshold(list(sc.values()), max_fpr), "scene_bce_T1": round(bce_mean(mx, ys), 5)}


def train_pairs(a: argparse.Namespace, tok, model, dev_scenes: list[dict]) -> dict:  # noqa: ANN001
    import torch

    rows = [r for f in a.train for r in C.read_jsonl(f)]
    tr = [r for r in rows if r.get("split") != "dev"]
    dv = [r for r in rows if r.get("split") == "dev"]
    log.info("쌍 학습 %d · dev %d (유형 %s)", len(tr), len(dv), sorted({r.get("type") for r in rows}))
    enc_tr = [C.encode_item(tok, r["claim"], C.contexts_of(r)) | {"y": C.y_of(r)} for r in tr]
    enc_dv = [C.encode_item(tok, r["claim"], C.contexts_of(r)) for r in dv]
    y_dv = [C.y_of(r) for r in dv]
    ci = C.contra_index(model)
    steps = a.epochs * ((len(enc_tr) + a.batch_size - 1) // a.batch_size) // a.grad_accum
    opt, sch = optim(model, steps, a)
    scaler = torch.amp.GradScaler("cuda", enabled=(a.device == "cuda"))
    lossf = torch.nn.BCEWithLogitsLoss()
    hist, best, best_state, bad = [], None, None, 0
    for ep in range(a.epochs):
        model.train()
        rng = random.Random(f"V2:pairs:{a.seed}:{ep}")
        order = list(range(len(enc_tr)))
        rng.shuffle(order)
        t0, tot, n = time.time(), 0.0, 0
        for s in range(0, len(order), a.batch_size):
            idx = order[s:s + a.batch_size]
            b = {k: v.to(a.device) for k, v in C.collate([enc_tr[i] for i in idx], tok.pad_token_id).items()}
            y = torch.tensor([float(enc_tr[i]["y"]) for i in idx], device=a.device)
            with torch.autocast(device_type="cuda", dtype=torch.float16, enabled=(a.device == "cuda")):
                lg = model(**b).logits.float()
            loss = lossf(C.binary_logit(lg, ci), y) / a.grad_accum
            scaler.scale(loss).backward()
            n += 1
            tot += float(loss.detach()) * a.grad_accum
            _track(float(loss.detach()) * a.grad_accum)
            if n % a.grad_accum == 0:
                scaler.unscale_(opt)
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                scaler.step(opt)
                scaler.update()
                opt.zero_grad(set_to_none=True)
                sch.step()
        dv_logits = C.predict_logits(model, tok, enc_dv, a.device) if enc_dv else []
        dloss = bce_mean(dv_logits, y_dv) if enc_dv else None
        rep = {"epoch": ep + 1, "train_bce": round(tot / max(1, n), 5), "dev_pair_bce": dloss, "sec": round(time.time() - t0, 1)}
        if dev_scenes:
            rep["dev_scene_auroc"] = dev_report(model, tok, dev_scenes, a.device, a.max_fpr)["scene_auroc"]["auroc"]
        hist.append(rep)
        log.info("에폭 %s", rep)
        key = dloss if dloss is not None else -ep
        if a.fixed:
            continue          # 고정 레시피: 조기 종료·최적 복원 없음(마지막 체크포인트)
        if best is None or key < best:
            best, bad = key, 0
            best_state = {k: v.detach().to("cpu").clone() for k, v in model.state_dict().items()}
            rep["best"] = True
        else:
            bad += 1
            if bad >= a.patience:
                break
    if best_state is not None:
        model.load_state_dict(best_state)
    return {"rows": {"train": len(tr), "dev": len(dv)}, "history": hist,
            "data": {str(f): C.sha256_file(f) for f in a.train}}


def train_mil(a: argparse.Namespace, tok, model, dev_scenes: list[dict]) -> dict:  # noqa: ANN001
    import torch

    s_rows = C.read_jsonl(a.s)
    tr_sc = C.scenes_of([r for r in s_rows if r.get("split") != "dev"])
    log.info("MIL 장면 학습 %d · dev %d", len(tr_sc), len(dev_scenes))
    ci = C.contra_index(model)
    steps = a.epochs * len(tr_sc) // a.grad_accum
    opt, sch = optim(model, steps, a)
    scaler = torch.amp.GradScaler("cuda", enabled=(a.device == "cuda"))
    lossf = torch.nn.BCEWithLogitsLoss()
    enc = [[C.encode_item(tok, it.claim, it.contexts) for it in sc["items"]] for sc in tr_sc]
    hist, best, best_state, bad = [], None, None, 0
    for ep in range(a.epochs):
        rng = random.Random(f"V2:mil:{a.seed}:{ep}")
        order = [i for i in range(len(tr_sc)) if enc[i]]
        rng.shuffle(order)
        t0, tot, n = time.time(), 0.0, 0
        for i in order:
            lg_all = C.predict_logits(model, tok, enc[i], a.device)          # no_grad, eval 모드
            j = max(range(len(lg_all)), key=lambda k: (lg_all[k], -k))
            model.train()
            b = {k: v.to(a.device) for k, v in C.collate([enc[i][j]], tok.pad_token_id).items()}
            with torch.autocast(device_type="cuda", dtype=torch.float16, enabled=(a.device == "cuda")):
                lg = model(**b).logits.float()
            y = torch.tensor([float(tr_sc[i]["y"])], device=a.device)
            loss = lossf(C.binary_logit(lg, ci), y) / a.grad_accum
            scaler.scale(loss).backward()
            n += 1
            tot += float(loss.detach()) * a.grad_accum
            _track(float(loss.detach()) * a.grad_accum)
            if n % a.grad_accum == 0:
                scaler.unscale_(opt)
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                scaler.step(opt)
                scaler.update()
                opt.zero_grad(set_to_none=True)
                sch.step()
        dv = dev_report(model, tok, dev_scenes, a.device, a.max_fpr)
        rep = {"epoch": ep + 1, "train_bce": round(tot / max(1, n), 5), "dev_scene_bce": dv.get("scene_bce_T1"),
               "dev_scene_auroc": (dv.get("scene_auroc") or {}).get("auroc"), "sec": round(time.time() - t0, 1)}
        hist.append(rep)
        log.info("에폭 %s", rep)
        key = rep["dev_scene_bce"] if rep["dev_scene_bce"] is not None else -ep
        if a.fixed:
            continue          # 고정 레시피: 조기 종료·최적 복원 없음(마지막 체크포인트)
        if best is None or key < best:
            best, bad = key, 0
            best_state = {k: v.detach().to("cpu").clone() for k, v in model.state_dict().items()}
            rep["best"] = True
        else:
            bad += 1
            if bad >= a.patience:
                break
    if best_state is not None:
        model.load_state_dict(best_state)
    return {"scenes": {"train": len(tr_sc), "dev": len(dev_scenes)}, "history": hist, "data": {str(a.s): C.sha256_file(a.s)}}


def roundtrip_and_delta(a: argparse.Namespace, tok, model, init_state: dict) -> dict:  # noqa: ANN001
    """저장 → 다시 읽기 왕복(같은 입력의 이진 로짓 최대 차)과 학습으로 바뀐 매개변수 크기(L2)."""
    import torch
    from transformers import AutoModelForSequenceClassification

    probe = [C.encode_item(tok, "그는 집으로 돌아갔다.", ["그는 시장에 있었다.", "비가 왔다."]),
             C.encode_item(tok, "아내는 앓고 있었다.", ["아내가 아팠다."])]
    before = C.predict_logits(model, tok, probe, a.device)
    re = AutoModelForSequenceClassification.from_pretrained(str(a.out)).to(a.device)
    after = C.predict_logits(re, tok, probe, a.device)
    del re
    sq, cls_sq = 0.0, 0.0
    for k, v in model.state_dict().items():
        if k in init_state and v.dtype.is_floating_point:
            d = float(((v.detach().float().cpu() - init_state[k].float()) ** 2).sum())
            sq += d
            if k.startswith("classifier"):
                cls_sq += d
    return {"roundtrip_max_abs_diff": max(abs(x - y) for x, y in zip(before, after)),
            "param_delta_l2": round(sq ** 0.5, 4), "classifier_delta_l2": round(cls_sq ** 0.5, 6)}


def main(argv: list[str] | None = None) -> None:
    a = parse(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if a.out.exists():
        raise SystemExit(f"출력 폴더가 이미 있다(덮어쓰지 않는다): {a.out}")
    if a.stage == "pairs" and not a.train:
        raise SystemExit("--stage pairs 는 --train 이 필요하다")
    if a.stage == "mil" and not a.s:
        raise SystemExit("--stage mil 은 --s 가 필요하다")
    import torch

    torch.manual_seed(a.seed)
    random.seed(a.seed)
    tok, model = load(a.init, a.device, a.gradient_checkpointing)
    init_state = {k: v.detach().to("cpu", copy=True) for k, v in model.state_dict().items()}
    if a.device == "cuda":
        torch.cuda.reset_peak_memory_stats()
    dev_src = a.dev_s or a.s
    dev_scenes = C.scenes_of([r for r in C.read_jsonl(dev_src) if r.get("split") == "dev"]) if dev_src else []
    t0 = time.time()
    info = train_pairs(a, tok, model, dev_scenes) if a.stage == "pairs" else train_mil(a, tok, model, dev_scenes)
    dev = dev_report(model, tok, dev_scenes, a.device, a.max_fpr)
    a.out.mkdir(parents=True)
    model.save_pretrained(str(a.out))
    tok.save_pretrained(str(a.out))
    check = roundtrip_and_delta(a, tok, model, init_state)
    init_meta = {}
    for name in ("v2_meta.json", "judge_meta.json"):
        if (a.init / name).exists():
            init_meta = {"file": name, "sha256": C.sha256_file(a.init / name)}
            break
    meta = {"kind": "judge_v2", "stage": a.stage, "init": str(a.init), "init_meta": init_meta,
            "init_model_sha256": C.sha256_file(a.init / "model.safetensors") if (a.init / "model.safetensors").exists() else None,
            "versions": C.VERSIONS, "readout": "sigmoid(l_모순 − logsumexp(나머지)) = softmax[모순]",
            "temperature": 1.0 if a.fixed else dev.get("temperature", 1.0),
            "threshold": None if a.fixed else (dev.get("threshold") or {}).get("threshold"),
            "threshold_rule": "없음(고정 레시피: 관문이 AUROC)" if a.fixed else f"dev 정상 오탐 ≤ {a.max_fpr} 에서 recall 최대",
            "fixed": bool(a.fixed), "recipe": a.recipe, "checkpoint": "last" if a.fixed else "best_dev",
            "dev": ({"note": "기록만 — 선택에 쓰지 않음"} | dev) if a.fixed else dev, "dev_s": str(dev_src) if dev_src else None, "args": {k: str(v) for k, v in vars(a).items()},
            "sec": round(time.time() - t0, 1),
            "gpu_peak_mib": round(torch.cuda.max_memory_allocated() / 2 ** 20) if a.device == "cuda" else None,
            "loss_curve": TRACE["loss_curve"], "nonfinite_steps": TRACE["nonfinite_steps"], "batches": TRACE["steps"],
            "log_every": LOG_EVERY} | check | info
    (a.out / "v2_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    log.info("저장 %s · dev %s", a.out, {k: dev.get(k) for k in ("temperature", "scene_auroc", "threshold")})


if __name__ == "__main__":
    main()
