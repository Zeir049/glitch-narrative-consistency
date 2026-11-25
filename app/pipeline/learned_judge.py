"""학습 judge — 판정 패킷을 LLM 없이 (가설, 전제) 쌍 분류로 판정한다 (WEAVE_JUDGE_MODE=learned).

  쌍 생성   app.pipeline.pairs.build_pairs (학습 데이터 내보내기와 같은 함수)
  쌍 분류   백엔드가 쌍마다 [모순, 정상·관련, 무관] 확률을 낸다
            FakeBackend  결정적 휴리스틱(반의어·부정어 소사전). 모델 없이 돈다 — 테스트·연결 확인용
            HFBackend    transformers cross-encoder(klue/roberta-base 미세조정본). 선택 의존성 [judge]
  패킷 판정 모순 확률이 가장 높은 쌍 하나를 대표로. 그 확률 ≥ threshold 면 양성
            (전제가 knowledge 면 '지식범위위반', 그 밖은 '모순'), 아니면 '정상'.

결정성(설계안 §2-5): 쌍 순서는 build_pairs 가 고정한다. HFBackend 는 쌍마다 토큰 길이로 버킷
(128/256/384/512, max_length 이하)을 정하고, 버킷 안에서 build_pairs 순서를 유지한 채 고정 크기 배치로
묶어 그 버킷 길이로 패딩·추론한 뒤 원래 순서로 되돌린다 — 배치 구성이 입력에서 결정적으로 정해진다.
정밀도·어텐션·패딩·max_length 는 모델 폴더 judge_meta.json 이 정한다(학습 검증 logits 와 같은 조건, 설계안 §0-6·§2-5):
GPU 는 meta precision(기본 fp16) → model.half() 고정(autocast 아님), CPU 대체 경로는 항상 fp32,
attn_implementation 은 meta 값(기본 eager). eval·no_grad, torch.use_deterministic_algorithms(True),
cudnn.benchmark=False, CPU 는 스레드 수 고정(interop 1), GPU 는 CUBLAS_WORKSPACE_CONFIG=:4096:8.
같은 기계·같은 설정·같은 판에서 비트 동일을 목표로 한다.
동률은 앞선 쌍. 모델 폴더 judge_meta.json 의 pairs_version 이 PAIRS_VERSION 과 다르면 로드하지 않는다.
반환 dict 는 weave._judge_run 과 같은 키 — run_checks_parallel 이 그 자리에 그대로 넣는다.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import re
import threading
import time
from pathlib import Path
from typing import Callable, Protocol, Sequence

from app.pipeline.pairs import PAIRS_VERSION, PAIRS_VERSION_V3, QUOTAS, Pair, build_pairs

SUPPORTED_PAIRS_VERSIONS = {PAIRS_VERSION: "v2", PAIRS_VERSION_V3: "v3"}  # meta pairs_version → build_pairs render
from weave_pipeline import now  # noqa: E402 - 코어 경로는 pairs import 가 연다

logger = logging.getLogger(__name__)

LABELS = ("모순", "정상", "무관")  # 백엔드 출력 순서: [p_contradiction, p_related, p_unrelated]
META_FILE = "judge_meta.json"      # scripts/train_judge.py 가 모델 폴더에 남기는 보정·기준값 기록
DEFAULT_THRESHOLD = 0.5
BUCKETS = (128, 256, 384, 512)     # 버킷 패딩 길이(max_length 보다 큰 버킷은 max_length 로 대신)
PADDINGS = ("bucket", "max_length")
DEVICES = ("auto", "cpu", "cuda")
PRECISIONS = ("fp16", "fp32")
DEFAULT_MAX_LENGTH = 384        # GPU 경로 기본(G0 길이 결정 2026-11-07: 발췌 160자 유지, 256 → 384)
DEFAULT_ATTN = "eager"             # SDPA 백엔드 선택의 비결정성을 피한다(설계안 §0-6)
CUBLAS_WORKSPACE = ":4096:8"


class JudgeBackend(Protocol):
    model_hash: str
    batch_size: int

    def predict(self, pairs: Sequence[Pair]) -> list[list[float]]:
        """쌍마다 [p_모순, p_정상, p_무관]. 입력 순서 그대로."""
        ...

    def env(self) -> dict:
        """실행 환경 기록(회차당 한 번 checks["config"]["judge_env"] 에 남는다)."""
        ...


# ---------------------------------------------------------------- 가짜 백엔드

_ANTONYMS: tuple[tuple[str, str], ...] = (
    ("alive", "false"), ("alive", "dead"), ("true", "false"), ("unconscious", "true"),
    ("살아", "죽"), ("살아", "사망"), ("생존", "사망"), ("멀쩡", "부상"), ("멀쩡", "골절"),
    ("멀쩡", "파손"), ("가능", "불가"), ("성공", "실패"), ("열려", "잠겨"), ("있", "없"),
)
_NEGATIONS = ("않", "못", "없", "불가", "아니", "모른", "모르")
_TOKEN = re.compile(r"[0-9A-Za-z가-힣_]+")
_STOP = {"사건", "원문", "근거", "발췌", "since", "certainty", "explicit", "inferred", "via", "direct"}


def _toks(text: str) -> set[str]:
    return {t for t in _TOKEN.findall(text.lower()) if len(t) > 1 and t not in _STOP}


def _has(text: str, stem: str) -> bool:
    return stem in text


class FakeBackend:
    """결정적 휴리스틱 판정기. 모델 파일 없이 learned 경로를 끝까지 돌리기 위한 것.

    fixed: {(event_id, premise_id): p_모순} — 테스트에서 특정 쌍의 확률을 고정한다.
    """

    VERSION = "fake-v1"

    def __init__(self, fixed: dict[tuple[str, str], float] | None = None, batch_size: int = 16):
        self.fixed = dict(fixed or {})
        self.batch_size = batch_size
        payload = json.dumps({"v": self.VERSION, "ant": _ANTONYMS, "neg": _NEGATIONS,
                              "fixed": sorted([list(k) + [v] for k, v in self.fixed.items()])},
                             ensure_ascii=False, sort_keys=True)
        self.model_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _one(self, p: Pair) -> list[float]:
        key = (p.event_id, p.premise_id)
        if key in self.fixed:
            c = float(self.fixed[key])
            return [c, (1 - c) / 2, (1 - c) / 2]
        h, q = p.hypothesis_text.lower(), p.premise_text.lower()
        # 가설 머리(엔티티·필드·값)만 본다 — 사건 요약·원문까지 보면 우연한 낱말에 휩쓸린다
        head = h.split(" | ")[0]
        clash = any((_has(head, a) and _has(q, b) and not _has(head, b))
                    or (_has(head, b) and _has(q, a) and not _has(head, a)) for a, b in _ANTONYMS)
        overlap = len(_toks(head) & _toks(q))
        neg_h = any(n in head for n in _NEGATIONS)
        neg_q = any(n in q for n in _NEGATIONS)
        if clash and (p.field is None or p.premise_kind in ("ledger", "checker")) and overlap >= 1:
            return [0.9, 0.05, 0.05]
        if neg_h != neg_q and overlap >= 2:
            return [0.6, 0.2, 0.2]
        if overlap >= 1:
            return [0.1, 0.7, 0.2]
        return [0.05, 0.15, 0.8]

    def predict(self, pairs: Sequence[Pair]) -> list[list[float]]:
        return [self._one(p) for p in pairs]

    def env(self) -> dict:
        return {"backend": "fake", "version": self.VERSION, "batch_size": self.batch_size,
                "pairs_version": PAIRS_VERSION}


# ---------------------------------------------------------------- HF 백엔드

def model_dir_hash(model_dir: str | Path) -> str:
    """모델 폴더의 파일 전부(상대 경로 + 내용)의 sha256. judge_meta.json 은 빼지 않는다 —
    온도·기준값이 바뀌면 판정이 바뀌므로 해시도 바뀌어야 한다."""
    root = Path(model_dir)
    h = hashlib.sha256()
    files = sorted(p for p in root.rglob("*") if p.is_file())
    if not files:
        raise FileNotFoundError(f"학습 judge 모델 파일이 없습니다: {root}")
    for p in files:
        h.update(p.relative_to(root).as_posix().encode("utf-8") + b"\0")
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        h.update(b"\0")
    return h.hexdigest()


def load_meta(model_dir: str | Path | None) -> dict:
    if not model_dir:
        return {}
    path = Path(model_dir) / META_FILE
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def buckets_for(max_length: int) -> tuple[int, ...]:
    """max_length 보다 작은 버킷들 + max_length(가장 긴 버킷)."""
    return tuple(b for b in BUCKETS if b < max_length) + (int(max_length),)


def bucket_plan(lengths: Sequence[int], batch_size: int, max_length: int) -> list[tuple[int, list[int]]]:
    """[(패딩 길이, 쌍 인덱스들)] — 버킷 오름차순, 버킷 안에서는 입력(build_pairs) 순서, batch_size 씩.
    길이는 truncation 뒤 토큰 수(≤ max_length). 같은 입력 → 같은 계획."""
    bks = buckets_for(max_length)
    by: dict[int, list[int]] = {b: [] for b in bks}
    for i, n in enumerate(lengths):
        by[next((b for b in bks if n <= b), bks[-1])].append(i)
    bs = max(1, int(batch_size))
    return [(b, by[b][s:s + bs]) for b in bks for s in range(0, len(by[b]), bs)]


def run_plan(n: int, plan: list[tuple[int, list[int]]],
             infer: Callable[[list[int], int], list[list[float]]]) -> list[list[float]]:
    """계획대로 infer(인덱스들, 패딩 길이)를 부르고 결과를 원래 순서로 되돌린다."""
    out: list[list[float] | None] = [None] * n
    for pad_len, idx in plan:
        rows = infer(idx, pad_len)
        if len(rows) != len(idx):
            raise RuntimeError(f"배치 출력 수가 다릅니다: {len(rows)} != {len(idx)}")
        for i, row in zip(idx, rows):
            out[i] = row
    missing = [i for i, r in enumerate(out) if r is None]
    if missing:
        raise RuntimeError(f"추론되지 않은 쌍이 있습니다: {missing[:5]}")
    return out  # type: ignore[return-value]


def _cpu_name() -> str:
    try:  # Linux 는 platform.processor() 가 비거나 'x86_64' 라서 /proc/cpuinfo 를 본다
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or platform.machine()


def configure_torch(torch, device: str, threads: int | None) -> None:  # noqa: ANN001
    """결정성 설정(설계안 §2-5). CPU: 스레드 고정·interop 1. GPU: CUBLAS_WORKSPACE_CONFIG. 공통: 결정적 알고리즘."""
    if device == "cpu":
        if threads:
            torch.set_num_threads(int(threads))
        try:
            torch.set_num_interop_threads(1)
        except RuntimeError as e:  # 이미 병렬 작업이 시작된 프로세스에서는 바꿀 수 없다
            logger.warning("learned judge: set_num_interop_threads(1) 실패 — %s", e)
    else:
        if os.environ.get("CUBLAS_WORKSPACE_CONFIG") is None:
            logger.warning("learned judge: CUBLAS_WORKSPACE_CONFIG 가 비어 있어 %s 로 설정합니다. "
                           "CUDA 초기화 뒤에는 효과가 없을 수 있으니 워커 환경 변수로 미리 넣으세요.",
                           CUBLAS_WORKSPACE)
            os.environ["CUBLAS_WORKSPACE_CONFIG"] = CUBLAS_WORKSPACE
    torch.backends.cudnn.benchmark = False
    torch.use_deterministic_algorithms(True)


def resolve_load_options(meta: dict, device: str, max_length: int | None = None,
                         padding: str | None = None) -> dict:
    """모델 로드 조건(torch 불필요 — 테스트 대상). 인자가 None 이면 meta, meta 에도 없으면 기본값.

    precision: cuda 면 meta precision(기본 fp16), cpu 면 항상 fp32(CPU 대체 경로 — notes 에 남긴다).
    attn_implementation: meta 값(기본 eager). padding: bucket | max_length. max_length: 학습 토큰 상한과 같아야 한다.
    """
    meta_prec = meta.get("precision") or "fp16"
    if meta_prec not in PRECISIONS:
        raise ValueError(f"judge_meta.json precision 이 이상합니다: {meta_prec!r} ({'|'.join(PRECISIONS)})")
    precision = meta_prec if device == "cuda" else "fp32"
    attn = meta.get("attn_implementation") or DEFAULT_ATTN
    pad = padding if padding is not None else (meta.get("padding") or "bucket")
    if pad not in PADDINGS:
        raise ValueError(f"알 수 없는 WEAVE_JUDGE_PADDING: {pad} ({'|'.join(PADDINGS)})")
    meta_len = meta.get("max_length")
    length = int(max_length) if max_length is not None else int(meta_len or DEFAULT_MAX_LENGTH)
    notes = []
    if meta_len is not None and int(meta_len) != length:
        notes.append(f"max_length {length} ≠ 학습 {meta_len}")
    if device != "cuda" and meta_prec != "fp32":
        notes.append(f"CPU 대체 경로: 학습 검증 정밀도 {meta_prec} 대신 fp32")
    return {"precision": precision, "attn_implementation": attn, "padding": pad, "max_length": length,
            "meta_precision": meta_prec, "notes": notes}


def check_pairs_version(meta: dict, model_dir: str | Path) -> str:
    """meta 의 pairs_version 이 쌍 생성기가 낼 수 있는 판(2 | 3)인지 확인하고 그 판을 돌려준다."""
    got = meta.get("pairs_version")
    if str(got) not in SUPPORTED_PAIRS_VERSIONS:
        raise RuntimeError(f"학습 judge 모델의 pairs_version({got!r})이 쌍 생성기 판({sorted(SUPPORTED_PAIRS_VERSIONS)})과 "
                           f"다릅니다: {model_dir} — 같은 build_pairs 판으로 다시 학습해야 합니다.")
    return str(got)


class HFBackend:
    """transformers 쌍 분류 cross-encoder. transformers/torch 는 생성할 때 import 한다(선택 의존성).

    padding: bucket(버킷 패딩) | max_length(모든 쌍을 max_length 로 패딩 — 개정 전 동작). None 이면 meta.
    max_length: None 이면 meta(학습 상한), 없으면 384.
    device: auto(cuda 가 있으면 cuda) | cpu | cuda. threads: CPU 스레드 수(None 이면 torch 기본).
    정밀도·어텐션은 meta(resolve_load_options): GPU 는 meta precision(기본 fp16)으로 model.half() 고정 — autocast 금지,
    CPU 는 fp32. attn_implementation 기본 eager(§0-6·§2-5).
    """

    def __init__(self, model_dir: str, batch_size: int = 16, max_length: int | None = None,
                 threads: int | None = None, padding: str | None = None, device: str = "auto"):
        if not model_dir:
            raise ValueError("WEAVE_JUDGE_MODEL_DIR 가 비어 있습니다 (WEAVE_JUDGE_BACKEND=hf).")
        if padding is not None and padding not in PADDINGS:
            raise ValueError(f"알 수 없는 WEAVE_JUDGE_PADDING: {padding} ({'|'.join(PADDINGS)})")
        if device not in DEVICES:
            raise ValueError(f"알 수 없는 WEAVE_JUDGE_DEVICE: {device} ({'|'.join(DEVICES)})")
        self.model_dir = str(model_dir)
        self.meta = load_meta(model_dir)
        self.pairs_version = check_pairs_version(self.meta, model_dir)  # torch 를 부르기 전에 확인
        try:
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
        except ImportError as e:  # pragma: no cover - 선택 의존성
            raise RuntimeError("학습 judge(hf)에는 transformers·torch 가 필요합니다: "
                               "pip install -e .[judge]") from e
        self._torch = torch
        self.batch_size = int(batch_size)
        self.threads = threads
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        elif device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("WEAVE_JUDGE_DEVICE=cuda 인데 CUDA 를 쓸 수 없습니다.")
        self.device = device
        opts = resolve_load_options(self.meta, device, max_length, padding)
        for note in opts["notes"]:
            logger.warning("learned judge: %s — %s", note, model_dir)
        self.max_length = opts["max_length"]
        self.padding = opts["padding"]
        self.precision = opts["precision"]
        self.attn_implementation = opts["attn_implementation"]
        configure_torch(torch, device, threads)
        self.temperature = float(self.meta.get("temperature") or 1.0)
        self.model_hash = model_dir_hash(model_dir)
        self.tokenizer = AutoTokenizer.from_pretrained(self.model_dir)
        model = AutoModelForSequenceClassification.from_pretrained(
            self.model_dir, torch_dtype=torch.float32, attn_implementation=self.attn_implementation)
        if self.precision == "fp16":
            model = model.half()  # 학습 검증 logits 와 같은 조건(autocast 아님)
        self.model = model.to(device).eval()
        # 모델 라벨 → [모순, 정상, 무관] 순서
        id2label = {int(k): v for k, v in (self.model.config.id2label or {}).items()}
        order = self.meta.get("labels") or [id2label.get(i, LABELS[i]) for i in range(len(LABELS))]
        self.index = [list(order).index(lab) if lab in order else i for i, lab in enumerate(LABELS)]

    def env(self) -> dict:  # pragma: no cover - torch 필요
        torch = self._torch
        try:
            import transformers
            tf_ver = transformers.__version__
        except ImportError:
            tf_ver = None
        env = {"backend": "hf", "torch": torch.__version__, "transformers": tf_ver, "device": self.device,
               "threads": torch.get_num_threads(), "interop_threads": torch.get_num_interop_threads(),
               "cpu": _cpu_name(), "padding": self.padding, "max_length": self.max_length,
               "batch_size": self.batch_size, "pairs_version": self.pairs_version,
               "precision": self.precision, "attn_implementation": self.attn_implementation,
               "cudnn_benchmark": bool(torch.backends.cudnn.benchmark),
               "MKL_CBWR": os.environ.get("MKL_CBWR"),
               "deterministic": torch.are_deterministic_algorithms_enabled()}
        if self.device == "cuda":
            env |= {"gpu": torch.cuda.get_device_name(0), "cuda": torch.version.cuda,
                    "cudnn": torch.backends.cudnn.version(),
                    "CUBLAS_WORKSPACE_CONFIG": os.environ.get("CUBLAS_WORKSPACE_CONFIG")}
        return env

    def predict(self, pairs: Sequence[Pair]) -> list[list[float]]:  # pragma: no cover - 모델 필요
        torch = self._torch
        enc = self.tokenizer([p.premise_text for p in pairs], [p.hypothesis_text for p in pairs],
                             truncation=True, max_length=self.max_length, padding=False)
        feats = [{k: enc[k][i] for k in enc.keys()} for i in range(len(pairs))]
        if self.padding == "bucket":
            plan = bucket_plan([len(f["input_ids"]) for f in feats], self.batch_size, self.max_length)
        else:
            plan = [(self.max_length, list(range(s, min(s + self.batch_size, len(pairs)))))
                    for s in range(0, len(pairs), self.batch_size)]

        def infer(idx: list[int], pad_len: int) -> list[list[float]]:
            batch = self.tokenizer.pad([feats[i] for i in idx], padding="max_length", max_length=pad_len,
                                       return_tensors="pt")
            batch = {k: v.to(self.device) for k, v in batch.items()}
            logits = self.model(**batch, return_dict=True).logits.float() / self.temperature
            probs = torch.softmax(logits, dim=-1).cpu().tolist()
            return [[row[i] for i in self.index] for row in probs]

        with torch.no_grad():
            return run_plan(len(pairs), plan, infer)


# ---------------------------------------------------------------- 판정기

class LearnedJudge:
    def __init__(self, backend: JudgeBackend, threshold: float = DEFAULT_THRESHOLD,
                 max_premises: int | None = None, quotas: dict | None = QUOTAS):
        self.backend = backend
        self.threshold = float(threshold)
        self.max_premises = max_premises  # None = build_pairs 기본(v2 24, v1 32)
        self.quotas = quotas

    @property
    def judged_by(self) -> str:
        return f"learned / {self.backend.model_hash[:16]}"

    def judge_group(self, group: dict, ledger, corpus: list[dict], registry: dict | None,  # noqa: ANN001
                    checker_items: list[dict] | None, *, current_episode: int | None = None,
                    snapshots=None) -> dict:  # noqa: ANN001
        """snapshots: prejudge.Snapshots(F1) — 주면 사건마다 그 시점 원장으로 쌍을 만든다(쌍 규칙·순서 그대로)."""
        t0 = time.perf_counter()
        pv = str(getattr(self.backend, "pairs_version", PAIRS_VERSION))
        kw = {"max_premises": self.max_premises, "quotas": self.quotas, "current_episode": current_episode,
              "render": SUPPORTED_PAIRS_VERSIONS.get(pv, "v2")}
        if snapshots is not None:
            from app.pipeline.prejudge import build_pairs_f1
            pairs = build_pairs_f1(group, snapshots, corpus, registry, checker_items, **kw)
        else:
            pairs = build_pairs(group, ledger, corpus, registry, checker_items, **kw)
        probs = self.backend.predict(pairs) if pairs else []
        # 동률은 앞선 쌍(순서는 build_pairs 가 고정). 확률은 반올림하지 않은 값으로 비교
        ranked = sorted(range(len(probs)), key=lambda i: (-probs[i][0], i))
        best_i = ranked[0] if ranked else None
        top2 = None
        if len(ranked) > 1:
            q = pairs[ranked[1]]
            top2 = {"kind": q.premise_kind, "id": q.premise_id, "event_id": q.event_id,
                    "p": round(float(probs[ranked[1]][0]), 4)}

        base = {
            "run_index": 1, "status": "ok",
            "judged_by": self.judged_by, "model_hash": self.backend.model_hash,
            "threshold": self.threshold, "pairs": len(pairs), "pairs_version": pv, "top2": top2,
            "judged_at": now(), "batch_id": f"{group['key']}-r1",
            "retried": False, "parse_retried": False, "attempts": 1, "message": None, "raw": None,
        }
        if best_i is None:
            return base | {"verdict": "정상", "reason": "판정할 (가설, 전제) 쌍이 없습니다.",
                           "evidence_event_ids": [], "confidence": 0.0, "evidence_premise": None,
                           "elapsed_sec": round(time.perf_counter() - t0, 3)}
        best, p = pairs[best_i], float(probs[best_i][0])
        positive = p >= self.threshold
        verdict = ("지식범위위반" if best.premise_kind == "knowledge" else "모순") if positive else "정상"
        premise = {"kind": best.premise_kind, "id": best.premise_id, "text": best.premise_text,
                   "hypothesis": best.hypothesis_text, "event_id": best.event_id,
                   "entity": best.entity, "field": best.field}
        reason = (f"근거 전제({best.premise_kind} {best.premise_id}): {best.premise_text} "
                  f"↔ 가설({best.event_id}): {best.hypothesis_text} — 모순 확률 {p:.3f}")
        return base | {"verdict": verdict, "reason": reason, "evidence_event_ids": [best.premise_id],
                       "confidence": round(p, 4), "evidence_premise": premise,
                       "elapsed_sec": round(time.perf_counter() - t0, 3)}


# ---------------------------------------------------------------- 설정 → 판정기 (프로세스당 하나)

_CACHE: dict[tuple, LearnedJudge] = {}
_CACHE_LOCK = threading.Lock()


def judge_from_settings() -> LearnedJudge:
    from app.core.config import settings

    backend_name = settings.weave_judge_backend
    model_dir = settings.weave_judge_model_dir or ""
    key = (backend_name, model_dir, settings.weave_judge_threshold, settings.weave_judge_batch_size,
           settings.weave_judge_max_length, settings.weave_judge_threads, settings.weave_judge_padding,
           settings.weave_judge_device)
    with _CACHE_LOCK:
        if key not in _CACHE:
            if backend_name == "fake":
                backend: JudgeBackend = FakeBackend(batch_size=settings.weave_judge_batch_size)
            elif backend_name == "hf":
                backend = HFBackend(model_dir, batch_size=settings.weave_judge_batch_size,
                                    max_length=settings.weave_judge_max_length,
                                    threads=settings.weave_judge_threads,
                                    padding=settings.weave_judge_padding, device=settings.weave_judge_device)
            else:
                raise ValueError(f"알 수 없는 WEAVE_JUDGE_BACKEND: {backend_name}")
            threshold = settings.weave_judge_threshold
            if threshold is None:
                threshold = float(load_meta(model_dir).get("threshold") or DEFAULT_THRESHOLD) \
                    if backend_name == "hf" else DEFAULT_THRESHOLD
            _CACHE[key] = LearnedJudge(backend, threshold)
            logger.info("learned judge: backend=%s hash=%s threshold=%s",
                        backend_name, _CACHE[key].backend.model_hash[:16], threshold)
        return _CACHE[key]
