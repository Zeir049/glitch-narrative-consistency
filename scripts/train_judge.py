"""학습 judge 학습 — klue/roberta 쌍 분류(모순/정상/무관) cross-encoder. 설계안 §3-1 `train_judge.py` 항목 판.

단계
  0) 데이터 준비(scripts/eval/train_lib.py — torch 없이 테스트됨)
       --val-work W [--train-works A B] [--eval-work E]  JSONL 의 `work` 로 접기 분할. 같은 작품이 학습·검증 양쪽이면 에러,
                                                         E 의 행은 학습·검증 모두에서 뺀다(누수 규칙 §0-4c)
       라벨 '제외'는 손실·보정·기준값 어디에도 넣지 않는다. split=holdout 행은 학습하지 않고 지표만 보고한다.
       --hyp-only(전제 = 빈 문자열, 지름길 기준선 G4) · --train-frac 0.25|0.5|1.0(학습 곡선, 묶음 단위·포개짐)
       --inject-frac F(학습의 주입 행만 contrast_group 단위로 F, 실제 행은 그대로 — 복제 오버샘플링은 하지 않는다)
  1) (선택) KLUE-NLI 기초 학습     --nli-epochs N (entailment→정상, neutral→무관, contradiction→모순), 128토큰.
                                   시드들이 같은 기초 모델에서 출발한다. 산출물은 <out>/_nli 에 judge_meta.json(threshold 0.5,
                                   temperature 1, pairs_version·precision·attn·labels)과 함께 **보존** — 하네스 --mode learned 로
                                   영 학습 기준선(G3)을 내고, 다른 접기는 --nli-from <out>/_nli 로 재사용한다.
                                   --nli-only 면 기초 학습만 하고 끝(--train 불필요)
  2) 본 학습(시드마다)             에폭마다 무관 쌍을 (모순+정상)×--unrelated-ratio 로 줄여 데이터를 다시 짠다(묶음 라운드로빈,
                                   시드·에폭 고정). 손실 --loss weighted(빈도^−½, 평균 1) | ce, --label-smoothing.
                                   --epochs 상한, 검증 쌍 NLL 이 --early-stop-patience 에폭 나아지지 않으면 멈추고 최선 에폭으로 돌린다
  3) 온도 보정                     검증 쌍 logits 에 단일 T(NLL 최소, LBFGS)
  4) 장면 기준값                   검증 장면 p_scene = max p(모순), 정상 장면 오탐 ≤ --target-fpr 에서 recall 최대, 동률 높은 값
                                   p_scene 은 검증 장면의 **모든** 실제 쌍('제외' 포함 — 서비스는 장면의 쌍을 다 본다)의 max.
                                   '제외' 행은 손실·온도·NLL 에는 넣지 않는다. 옛 방식('제외' 뺀 max)은 meta seeds[].scene_drop_excluded
  5) 시드 선택                     검증 장면 recall 최대, 동률은 검증 쌍 NLL. 시드별 지표는 전부 meta 에
  정밀도(설계안 §0-6): 학습 순전파는 AMP(fp16 autocast + GradScaler) 허용. 검증 logits(조기 종료·온도·기준값의 입력)는
  서비스와 같은 조건 — GPU·--precision fp16 이면 model.half() 복제본, 아니면 fp32 — 으로 계산한다. 어텐션은
  --attn-implementation(기본 eager)으로 학습·검증·서비스 모두 같게. 둘 다 meta 에 기록되고 HFBackend 가 읽는다.
  6) 저장                          모델·토크나이저 + judge_meta.json(라벨·온도·기준값·pairs_version·precision·max_length·padding·
                                   분할·시드별 지표·쌍 ECE·판·토큰 길이 분위수·데이터 해시·인자). <out>.sha256 = model_dir_hash

로컬 RTX 3060 (설계안 §3-2):
  pip install -e .[train]   (또는 torch·transformers·datasets·accelerate)
  PYTHONIOENCODING=utf-8 python scripts/train_judge.py --train build/pairs_wm.jsonl build/pairs_q1.jsonl build/pairs_q3.jsonl \\
      build/pairs_sherlock.jsonl --val-work sherlock --train-works q1 q3 --eval-work wm --out D:/judge/judge_base_wm \\
      --nli-epochs 2 --seeds 1 2 3
서비스: WEAVE_JUDGE_MODE=learned WEAVE_JUDGE_BACKEND=hf WEAVE_JUDGE_MODEL_DIR=<out>
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import shutil
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = ROOT / "scripts" / "eval"
for _p in (str(ROOT), str(EVAL_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import train_lib as TL  # noqa: E402

LABELS, L2I = TL.LABELS, TL.L2I
read_jsonl = TL.read_jsonl          # 옛 이름(테스트·도구가 쓴다)
split_val = TL.split_val_groups
pick_scene_threshold = TL.pick_scene_threshold
KLUE_NLI_MAP = {0: L2I["정상"], 1: L2I["무관"], 2: L2I["모순"]}  # klue/nli: entailment, neutral, contradiction
logger = logging.getLogger("train_judge")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="학습 judge 학습 (klue/roberta cross-encoder)")
    ap.add_argument("--train", type=Path, nargs="+", help="export_pairs.py / label_pairs.py JSONL(여러 개 가능, --nli-only 가 아니면 필수)")
    ap.add_argument("--val-work", help="검증 작품(JSONL work). 접기 분할의 기본 방식")
    ap.add_argument("--train-works", nargs="+", help="학습 작품. 없으면 val·eval 이 아닌 작품 전부")
    ap.add_argument("--eval-work", help="평가 작품 — 학습·검증 모두에서 뺀다")
    ap.add_argument("--val-sources", choices=["real", "all"], default="real",
                    help="검증 작품에서 쓸 행: real = 실제 장면만(기본, 기준값·시드 선택의 단위), all = 주입·LLM 라벨 포함")
    ap.add_argument("--val-jsonl", type=Path, help="(작품 분할을 안 쓸 때) 검증 쌍 파일")
    ap.add_argument("--val-frac", type=float, default=0.15, help="(둘 다 없을 때) 묶음 단위로 떼어 낼 비율")
    ap.add_argument("--val-groups", type=Path,
                    help="검증으로 뗄 group_id 목록 파일(JSON 배열 또는 한 줄에 하나). --val-frac 대신 미리 정한 묶음으로 뗀다")
    ap.add_argument("--scene-unit", choices=["scene", "pair"], default="scene",
                    help="기준값·검증 AUROC 단위: scene = 장면 max(기본), pair = '쌍 = 장면 하나' 근사('제외' 행 안 씀)")
    ap.add_argument("--out", type=Path, required=True, help="모델 폴더(저장소 밖)")
    ap.add_argument("--base-model", default="klue/roberta-base")
    ap.add_argument("--init-model", type=Path, help="본 학습 시작 가중치(이미 만든 KLUE-NLI 기초 모델 폴더)")
    ap.add_argument("--nli-epochs", type=float, default=0.0, help="KLUE-NLI 기초 학습 에폭(0 이면 건너뜀)")
    ap.add_argument("--nli-from", type=Path, help="이미 만든 KLUE-NLI 기초 모델 폴더(<out>/_nli) — 접기 간 재사용")
    ap.add_argument("--nli-only", action="store_true", help="KLUE-NLI 기초 학습만 하고 <out>/_nli 에 저장(G3 영 학습 기준선)")
    ap.add_argument("--nli-dataset", default="klue/klue", help="HF 데이터셋 이름(config nli). 옛 이름 'klue'")
    ap.add_argument("--nli-max", type=int, default=0, help="KLUE-NLI 표본 상한(0 = 전부)")
    ap.add_argument("--nli-max-length", type=int, default=128)
    ap.add_argument("--epochs", type=int, default=6)
    ap.add_argument("--early-stop-patience", type=int, default=2, help="검증 쌍 NLL 기준(0 = 끄기)")
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--grad-accum", type=int, default=1)
    ap.add_argument("--gradient-checkpointing", action="store_true", help="large·12GB 용")
    ap.add_argument("--warmup-ratio", type=float, default=0.06)
    ap.add_argument("--weight-decay", type=float, default=0.01)
    ap.add_argument("--max-length", type=int, default=384, help="학습·서비스 공통 토큰 상한(GPU 기본 384 — G0 길이 결정)")
    ap.add_argument("--padding", choices=["bucket", "max_length"], default="bucket",
                    help="서비스 추론 패딩(meta 기록용). 학습 배치는 배치 안 최장 길이로 동적 패딩")
    ap.add_argument("--loss", choices=["ce", "weighted"], default="weighted")
    ap.add_argument("--label-smoothing", type=float, default=0.0, choices=[0.0, 0.1])
    ap.add_argument("--unrelated-ratio", type=float, default=2.0, help="에폭당 무관 ≤ (모순+정상)×비율. 0 이면 감축 없음")
    ap.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3])
    ap.add_argument("--target-fpr", type=float, default=0.05, help="검증 정상 장면 오탐 상한(장면 기준값)")
    ap.add_argument("--hyp-only", action="store_true", help="전제를 빈 문자열로 — 지름길 기준선")
    ap.add_argument("--train-frac", type=float, default=1.0, choices=[0.25, 0.5, 1.0])
    ap.add_argument("--inject-frac", type=float, default=1.0,
                    help="학습의 source=inject 행을 contrast_group 단위로 이 비율만 남긴다(--data-seed, 실제 행은 그대로)")
    ap.add_argument("--keyed-only", action="store_true", help="정답이 명시된 쌍만")
    ap.add_argument("--no-real", action="store_true", help="real split(실제 장면) 쌍을 학습에서 뺌")
    ap.add_argument("--precision", choices=["fp16", "fp32"], default="fp16",
                    help="학습 AMP 사용 여부이자 검증·서비스 추론 정밀도(model.half() 고정, meta 기록 — HFBackend 가 읽는다). GPU 전제")
    ap.add_argument("--attn-implementation", default="eager", choices=["eager", "sdpa"],
                    help="어텐션 구현(학습·검증·서비스 공통, meta 기록). eager = SDPA 백엔드 선택 비결정성 회피")
    ap.add_argument("--data-seed", type=int, default=42, help="--val-frac·--train-frac 표본 시드")
    ap.add_argument("--prepare-only", action="store_true", help="데이터 준비 보고만 출력하고 끝(torch 불필요)")
    return ap


def load_rows(paths: list[Path]) -> list[dict]:
    rows: list[dict] = []
    for p in paths:
        rows += TL.read_jsonl(p)
    return rows


def nli_meta_for(a: argparse.Namespace, n_rows: int, pairs_version: str, labels: list[str],
                 versions: dict, device: str) -> dict:
    """KLUE-NLI 기초 모델 폴더의 judge_meta.json — 하네스(HFBackend)가 그대로 읽을 수 있게(torch 불필요, 테스트 대상)."""
    return {"labels": list(labels), "temperature": 1.0, "threshold": 0.5, "scene_threshold": None,
            "threshold_unit": "fixed_0.5", "pairs_version": pairs_version, "precision": a.precision,
            "attn_implementation": a.attn_implementation, "max_length": a.max_length, "padding": a.padding,
            "base_model": a.base_model, "kind": "nli_base",
            "nli": {"dataset": a.nli_dataset, "config": "nli", "epochs": a.nli_epochs, "n": n_rows,
                    "max_length": a.nli_max_length, "max": a.nli_max, "seed": a.seeds[0], "lr": a.lr,
                    "batch_size": a.batch_size},
            "versions": versions, "device": device}


def check_args(a: argparse.Namespace) -> None:
    if a.nli_from and (a.nli_epochs > 0 or a.init_model):
        raise SystemExit("--nli-from 는 --nli-epochs·--init-model 과 함께 쓰지 않는다")
    if a.nli_epochs > 0 and a.init_model:
        raise SystemExit("--nli-epochs 와 --init-model 은 함께 쓰지 않는다")
    if a.nli_only and a.nli_epochs <= 0:
        raise SystemExit("--nli-only 에는 --nli-epochs 가 필요하다")
    if not a.nli_only and not a.train:
        raise SystemExit("--train 이 필요하다(--nli-only 가 아니면)")
    if a.nli_from and not (a.nli_from / "config.json").exists():
        raise SystemExit(f"--nli-from 폴더에 모델이 없다: {a.nli_from}")


def data_pairs_version(rows: list[dict]) -> str:
    """학습·검증 행의 pairs_version(없으면 "2"). 섞여 있으면 거부한다 — meta 가 서비스 렌더링을 정한다."""
    got = {str(r.get("pairs_version") or "2") for r in rows}
    if len(got) != 1:
        raise SystemExit(f"pairs_version 이 섞여 있다: {sorted(got)}")
    return got.pop()


def read_group_list(path: Path) -> list[str]:
    """--val-groups 파일: JSON 배열이거나 한 줄에 하나(빈 줄·# 주석 무시)."""
    text = path.read_text(encoding="utf-8").strip()
    if text.startswith("["):
        return [str(g) for g in json.loads(text)]
    return [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]


def prepare_from_args(a: argparse.Namespace) -> TL.Split:
    rows = load_rows(a.train)
    val_rows = TL.read_jsonl(a.val_jsonl) if a.val_jsonl else None
    val_groups = read_group_list(a.val_groups) if a.val_groups else None
    return TL.prepare(rows, val_work=a.val_work, train_works=a.train_works, eval_work=a.eval_work,
                      val_rows=val_rows, val_groups=val_groups, val_frac=a.val_frac, seed=a.data_seed, keyed_only=a.keyed_only,
                      no_real=a.no_real, hyp_only_mode=a.hyp_only, train_frac=a.train_frac, inject_frac=a.inject_frac,
                      val_sources=a.val_sources)


def main(argv: list[str] | None = None) -> None:  # noqa: C901 - 학습 스크립트는 한 흐름으로 둔다
    a = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    check_args(a)
    split = None
    if not a.nli_only:
        split = prepare_from_args(a)
        logger.info("분할 %s", json.dumps(split.report, ensure_ascii=False))
        if a.prepare_only:
            print(json.dumps(split.report, ensure_ascii=False, indent=1))
            return
        if not split.val:
            raise SystemExit("검증 행이 없습니다(--val-work / --val-jsonl / --val-frac)")

    import copy

    import numpy as np
    import torch
    from torch.utils.data import DataLoader
    from transformers import (AutoModelForSequenceClassification, AutoTokenizer, DataCollatorWithPadding,
                              get_linear_schedule_with_warmup, set_seed)

    from app.pipeline.learned_judge import META_FILE, model_dir_hash
    from app.pipeline.pairs import PAIRS_VERSION

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    use_amp = a.precision == "fp16" and device.type == "cuda"
    eval_half = use_amp  # 검증 logits = 서비스 조건(GPU fp16 → model.half(), 그 밖 fp32)
    if device.type == "cuda":
        torch.backends.cudnn.benchmark = False
    if a.precision == "fp16" and device.type != "cuda":
        logger.warning("fp16 은 GPU 전제 — CPU 에서는 fp32 로 학습한다(meta 의 precision 은 인자 그대로)")
    tok = AutoTokenizer.from_pretrained(a.base_model)
    collator = DataCollatorWithPadding(tok)
    a.out.mkdir(parents=True, exist_ok=True)

    def encode(rows: list[dict], max_length: int) -> list[dict]:
        enc = tok([r["premise"] for r in rows], [r["hypothesis"] for r in rows], truncation=True,
                  max_length=max_length)
        return [{k: enc[k][i] for k in enc} | {"labels": L2I[r["label"]]} for i, r in enumerate(rows)]

    def token_lengths(rows: list[dict]) -> list[int]:
        enc = tok([r["premise"] for r in rows], [r["hypothesis"] for r in rows], truncation=False)
        return [len(x) for x in enc["input_ids"]]

    def loader(feats: list[dict], shuffle: bool, seed: int) -> DataLoader:
        g = torch.Generator()
        g.manual_seed(seed)
        return DataLoader(feats, batch_size=a.batch_size, shuffle=shuffle, collate_fn=collator, generator=g)

    def new_model(src: str | Path):
        m = AutoModelForSequenceClassification.from_pretrained(
            str(src), num_labels=len(LABELS), id2label=dict(enumerate(LABELS)), label2id=L2I,
            attn_implementation=a.attn_implementation)
        if a.gradient_checkpointing:
            m.gradient_checkpointing_enable()
        return m.to(device)

    @torch.no_grad()
    def logits_of(model, feats: list[dict]) -> np.ndarray:  # noqa: ANN001
        """서비스와 같은 수치 조건: GPU fp16 이면 half 복제본(autocast 아님), 아니면 fp32. 학습 가중치(fp32)는 그대로."""
        was_training = model.training
        model.eval()
        m = copy.deepcopy(model).half() if eval_half else model
        if eval_half and getattr(m, "is_gradient_checkpointing", False):
            m.gradient_checkpointing_disable()
        out = []
        for batch in loader(feats, False, 0):
            batch = {k: v.to(device) for k, v in batch.items() if k != "labels"}
            out.append(m(**batch, return_dict=True).logits.float().cpu())
        if m is not model:
            del m
            if device.type == "cuda":
                torch.cuda.empty_cache()
        model.train(was_training)
        return torch.cat(out).numpy() if out else np.zeros((0, len(LABELS)), dtype=np.float32)

    def run_epochs(model, make_rows, n_epochs: float, seed: int, weight: list[float] | None,  # noqa: ANN001
                   smoothing: float, max_length: int, val_feats: list[dict] | None,
                   patience: int) -> tuple[dict, TL.EarlyStopper]:
        """에폭마다 make_rows(epoch) 로 학습 행을 다시 짠다. val_feats 가 있으면 NLL 조기 종료, 최선 가중치를 돌려준다."""
        full = max(1, math.ceil(n_epochs))
        frac_last = n_epochs - math.floor(n_epochs)  # 0 이면 마지막 에폭도 전부
        epoch_sizes = [len(make_rows(e)) for e in range(full)]
        steps = sum(-(-n // a.batch_size) for n in epoch_sizes) // max(1, a.grad_accum)
        opt = torch.optim.AdamW(model.parameters(), lr=a.lr, weight_decay=a.weight_decay)
        sched = get_linear_schedule_with_warmup(opt, int(steps * a.warmup_ratio), max(1, steps))
        scaler = (torch.amp.GradScaler("cuda", enabled=use_amp) if hasattr(torch, "amp") and
                  hasattr(torch.amp, "GradScaler") else torch.cuda.amp.GradScaler(enabled=use_amp))
        w = torch.tensor(weight, dtype=torch.float32, device=device) if weight is not None else None
        stopper = TL.EarlyStopper(patience)
        best_state = None
        for epoch in range(full):
            model.train()
            feats = encode(make_rows(epoch), max_length)
            batches = list(loader(feats, True, seed * 1000 + epoch))
            if epoch == full - 1 and 0 < frac_last < 1:
                batches = batches[:max(1, int(len(batches) * frac_last))]
            t0, total = time.time(), 0.0
            for step, batch in enumerate(batches, 1):
                batch = {k: v.to(device) for k, v in batch.items()}
                labels = batch.pop("labels")
                with torch.autocast(device_type=device.type, dtype=torch.float16, enabled=use_amp):
                    logits = model(**batch, return_dict=True).logits
                loss = torch.nn.functional.cross_entropy(logits.float(), labels, weight=w,
                                                         label_smoothing=smoothing) / max(1, a.grad_accum)
                scaler.scale(loss).backward()
                if step % max(1, a.grad_accum) == 0 or step == len(batches):
                    scaler.unscale_(opt)
                    torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                    scaler.step(opt)
                    scaler.update()
                    opt.zero_grad(set_to_none=True)
                    sched.step()
                total += float(loss) * max(1, a.grad_accum)
            msg = f"epoch {epoch + 1}/{full} rows {len(feats)} loss {total / max(1, len(batches)):.4f} ({time.time() - t0:.0f}s)"
            if val_feats is not None:
                lg = logits_of(model, val_feats)
                v_nll = TL.nll([TL.softmax(r) for r in lg.tolist()], [f["labels"] for f in val_feats])
                stop = stopper.step(epoch, v_nll)
                if stopper.improved:
                    best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
                logger.info("%s val_nll %.4f%s", msg, v_nll, " *" if stopper.improved else "")
                if stop:
                    logger.info("조기 종료: %d 에폭 연속 개선 없음", patience)
                    break
            else:
                logger.info(msg)
        if best_state is not None:
            model.load_state_dict(best_state)
        return {"epochs_run": len(stopper.history) if val_feats is not None else full,
                "best_epoch": stopper.best_epoch + 1 if val_feats is not None else full,
                "val_nll_history": [round(x, 6) for x in stopper.history]}, stopper

    def fit_temperature(lg: np.ndarray, gold: list[int]) -> float:
        if not len(gold):
            return 1.0
        x = torch.tensor(lg, dtype=torch.float32)
        y = torch.tensor(gold, dtype=torch.long)
        log_t = torch.zeros(1, requires_grad=True)
        opt = torch.optim.LBFGS([log_t], lr=0.05, max_iter=200)

        def closure():  # noqa: ANN202
            opt.zero_grad()
            loss = torch.nn.functional.cross_entropy(x / log_t.exp(), y)
            loss.backward()
            return loss
        opt.step(closure)
        return float(log_t.exp().item())

    # 1) KLUE-NLI 기초 — 시드들이 공유
    init_src: str | Path = a.nli_from or a.init_model or a.base_model
    nli_meta = None
    if a.nli_from:
        src_meta = (json.loads((a.nli_from / META_FILE).read_text(encoding="utf-8"))
                    if (a.nli_from / META_FILE).exists() else {})
        nli_meta = {**(src_meta.get("nli") or {}), "from": str(a.nli_from), "model_hash": model_dir_hash(a.nli_from)}
    if a.nli_epochs > 0:
        from datasets import load_dataset
        set_seed(a.seeds[0])
        nli = load_dataset(a.nli_dataset, "nli", split="train")
        if a.nli_max:
            nli = nli.shuffle(seed=a.seeds[0]).select(range(min(a.nli_max, len(nli))))
        nli_rows = [{"premise": x["premise"], "hypothesis": x["hypothesis"], "label": LABELS[KLUE_NLI_MAP[x["label"]]]}
                    for x in nli if x["label"] in KLUE_NLI_MAP]
        model = new_model(a.base_model)
        run_epochs(model, lambda e: nli_rows, a.nli_epochs, a.seeds[0], None, 0.0, a.nli_max_length, None, 0)
        nli_dir = a.out / "_nli"
        model.save_pretrained(str(nli_dir))
        tok.save_pretrained(str(nli_dir))
        dev_name = torch.cuda.get_device_name(0) if device.type == "cuda" else "cpu"
        (nli_dir / META_FILE).write_text(json.dumps(
            nli_meta_for(a, len(nli_rows), PAIRS_VERSION, list(LABELS), TL.package_versions(), dev_name),
            ensure_ascii=False, indent=1), encoding="utf-8")
        nli_hash = model_dir_hash(nli_dir)
        Path(str(nli_dir) + ".sha256").write_text(nli_hash + "\n", encoding="utf-8")
        init_src = nli_dir
        nli_meta = {"from": str(nli_dir), "model_hash": nli_hash, "dataset": a.nli_dataset, "epochs": a.nli_epochs,
                    "n": len(nli_rows), "max_length": a.nli_max_length}
        del model
        if device.type == "cuda":
            torch.cuda.empty_cache()
        if a.nli_only:
            print(json.dumps({"out": str(nli_dir), "model_hash": nli_hash, "n": len(nli_rows)}, ensure_ascii=False))
            return

    # 2~4) 시드마다
    tr, va, ho = split.train, split.val, split.holdout
    counts = TL.sampled_counts(tr, a.unrelated_ratio)
    weight = TL.class_weights(counts, a.loss)
    va_feats = encode(va, a.max_length)
    va_gold = [f["labels"] for f in va_feats]
    # 검증 '제외' 행: 손실·보정·NLL 에는 안 쓰고 장면 점수(max)에만 — 서비스는 장면의 모든 쌍을 본다
    vx = split.val_excluded
    vx_feats = encode([dict(r) | {"label": TL.UNREL} for r in vx], a.max_length) if vx else []
    ho_feats = encode(ho, a.max_length) if ho else []
    seed_results = []
    for seed in a.seeds:
        set_seed(seed)
        model = new_model(init_src)
        hist, _st = run_epochs(model, lambda e, s=seed: TL.sample_epoch(tr, a.unrelated_ratio, s, e), a.epochs,
                               seed, weight, a.label_smoothing, a.max_length, va_feats, a.early_stop_patience)
        lg = logits_of(model, va_feats)
        temp = fit_temperature(lg, va_gold)
        probs = [TL.softmax(r, temp) for r in lg.tolist()]
        pc = [p[L2I["모순"]] for p in probs]
        pcx = [TL.softmax(r, temp)[L2I["모순"]] for r in logits_of(model, vx_feats).tolist()] if vx_feats else []
        if a.scene_unit == "pair":   # 떼어 둔 쌍이 장면이 아닐 때: 쌍 하나를 장면 하나로(쌍 라벨), '제외' 행은 쓰지 않는다
            pair_scores = TL.pair_scene_scores(va, pc)
            thr, scene_m = TL.pick_threshold_from_scores(pair_scores, a.target_fpr)
            scene_rows, n_vx_scored, v_auroc = "pair_as_scene_excl_excluded", 0, TL.scene_auroc(pair_scores)
        else:
            thr, scene_m = TL.pick_scene_threshold(va + vx, pc + pcx, a.target_fpr, allow_excluded=True)
            scene_rows, n_vx_scored = "all_real_rows_incl_excluded", len(vx)
            v_auroc = TL.scene_auroc(TL.scene_scores(va + vx, pc + pcx, allow_excluded=True))
        thr_d, scene_d = TL.pick_scene_threshold(va, pc, a.target_fpr)
        res = {"seed": seed, **hist, "temperature": round(temp, 6), "scene_threshold": thr,
               "scene_recall": scene_m.get("recall"), "scene": scene_m,
               "scene_rows": scene_rows, "n_val_excluded_scored": n_vx_scored,
               "scene_auroc": v_auroc,
               "scene_drop_excluded": scene_d | {"threshold": thr_d},
               "val_nll": TL.nll(probs, va_gold), "val_nll_uncalibrated": TL.nll([TL.softmax(r) for r in lg.tolist()], va_gold),
               "pair_calibration": TL.pair_calibration(probs, va_gold),
               "pair_pr_auc": TL.pr_auc(pc, [g == L2I["모순"] for g in va_gold])}
        if ho_feats:
            hp = [TL.softmax(r, temp)[L2I["모순"]] for r in logits_of(model, ho_feats).tolist()]
            res["holdout"] = {"n": len(ho), "scene": TL.scene_metrics_at(TL.scene_scores(ho, hp), thr),
                              "pair_pr_auc": TL.pr_auc(hp, [r["label"] == "모순" for r in ho]),
                              "operators": sorted({r.get("operator") for r in ho if r.get("operator")})}
        seed_dir = a.out / f"_seed_{seed}"
        model.save_pretrained(str(seed_dir))
        seed_results.append(res)
        logger.info("seed %d: scene recall %s fpr %s thr %.4f T %.3f nll %.4f", seed, scene_m.get("recall"),
                    scene_m.get("fpr"), thr, temp, res["val_nll"])
        del model
        if device.type == "cuda":
            torch.cuda.empty_cache()

    # 5) 시드 선택 → 6) 저장
    chosen = TL.select_seed(seed_results)
    chosen_dir = a.out / f"_seed_{chosen['seed']}"
    for p in chosen_dir.iterdir():
        shutil.copy2(p, a.out / p.name)
    tok.save_pretrained(str(a.out))
    for p in a.out.glob("_seed_*"):  # _nli 는 보존(G3·접기 간 재사용)
        shutil.rmtree(p, ignore_errors=True)
    lengths = {name: TL.quantiles(token_lengths(rs)) for name, rs in (("train", tr), ("val", va), ("holdout", ho)) if rs}
    for rs, name in ((tr, "train"), (va, "val")):
        for w in sorted({r.get("work") for r in rs if r.get("work")}):
            lengths[f"{name}:{w}"] = TL.quantiles(token_lengths([r for r in rs if r.get("work") == w]))
    meta = {"labels": LABELS, "temperature": chosen["temperature"], "threshold": chosen["scene_threshold"],
            "scene_threshold": chosen["scene_threshold"], "threshold_unit": "scene_max",
            "threshold_fit_unit": "pair_as_scene" if a.scene_unit == "pair" else "scene",
            "target_fpr": a.target_fpr, "pairs_version": data_pairs_version(tr + va), "precision": a.precision,
            "attn_implementation": a.attn_implementation,
            "val_logits_precision": "fp16_half" if eval_half else "fp32",
            "train_precision": "amp_fp16" if use_amp else "fp32",
            "max_length": a.max_length, "padding": a.padding, "train_padding": "longest_in_batch",
            "base_model": a.base_model, "init_model": str(a.init_model) if a.init_model else None, "nli": nli_meta,
            "val_work": split.report.get("val_work"), "train_works": split.report.get("train_works"),
            "eval_work": split.report.get("eval_work"), "hyp_only": a.hyp_only, "train_frac": a.train_frac,
            "inject_frac": a.inject_frac,
            "loss": a.loss, "class_weights": dict(zip(LABELS, [round(x, 6) for x in weight])),
            "class_counts_per_epoch": counts, "label_smoothing": a.label_smoothing,
            "unrelated_ratio": a.unrelated_ratio, "chosen_seed": chosen["seed"], "seeds": seed_results,
            "pair_ece": chosen["pair_calibration"], "token_length_quantiles": lengths,
            "versions": TL.package_versions(), "device": torch.cuda.get_device_name(0) if device.type == "cuda" else "cpu",
            "split": split.report,
            "data_sha256": {str(p): TL.sha256_file(p) for p in a.train},
            "val_sha256": TL.sha256_file(a.val_jsonl) if a.val_jsonl else None,
            "args": {k: (str(v) if isinstance(v, Path) else [str(x) for x in v] if isinstance(v, list) and v and
                         isinstance(v[0], Path) else v) for k, v in vars(a).items()},
            "counts": {"train": len(tr), "val": len(va), "holdout": len(ho),
                       "train_labels": dict(Counter(r["label"] for r in tr))}}
    (a.out / META_FILE).write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    digest = model_dir_hash(a.out)
    Path(str(a.out) + ".sha256").write_text(digest + "\n", encoding="utf-8")
    print(json.dumps({"out": str(a.out), "model_hash": digest, "chosen_seed": chosen["seed"],
                      "temperature": meta["temperature"], "scene_threshold": meta["scene_threshold"],
                      "seeds": [{k: s.get(k) for k in ("seed", "scene_recall", "val_nll", "best_epoch")}
                                for s in seed_results]}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
