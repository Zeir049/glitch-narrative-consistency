# glitch-narrative-consistency

**Glitch: a service that tracks the "canon" of a long Korean story chapter by chapter and flags passages that contradict earlier chapters.**
Capstone project, 2026. This repository has the backend, the frontend, the integration of the detection engine, the evaluation harness, and a **learned judge** that I trained to try to replace the engine's LLM judge.

> **Short version of the result:** the deployed detector is an **LLM-based pipeline**. The learned judge (a KLUE-RoBERTa cross-encoder) was trained and then measured once on a sealed, pre-registered test set. It scored **scene AUROC 0.496 [0.366, 0.623]**, below the pre-registered gate (≥ 0.65 and lower bound > 0.5), so it was **not** adopted. Details and causes are [below](#learned-judge).

Most project documents in `docs/` are in Korean. All numbers in this README are copied from those files, and each one cites its source file.

---

## What Glitch does

A writer uploads chapters (txt / md / docx / pdf). For each chapter the system

1. **extracts** a *canon delta*: worldview rules, characters, relations, timeline, places and events,
2. **reviews** the chapter against the canon of chapters `1 … N-1` and lists suspected contradictions with quotes,
3. after the writer edits and accepts the result, **merges** the delta into a versioned canon. Canon rows are append-only: an old row is marked `is_current=false`, and a new row is inserted, so you can view the canon as it stood at any chapter.

The frontend shows the canon in six tabs (worldview, characters, relations, timeline, spaces, events) and the review results per chapter.

## Architecture

```
frontend/ (static ES modules, served at /)
        │  REST
FastAPI  api/ → service/ → repository/ → PostgreSQL (SQLAlchemy 2.0, Alembic 0001–0006)
        │                      chapters.status: uploaded→extracting→extracted→reviewing→reviewed→saved
        │ enqueue
arq worker (Redis) ── AnalysisPipeline protocol (app/pipeline/interface.py)
                          ├─ fake    deterministic sample output (default; used by tests)
                          ├─ weave   Weave engine, in-process (the real detector, see below)
                          ├─ remote  external AI server over HTTP (/extract, /review, /health)
                          ├─ local   local LLM
                          └─ replay  pre-computed responses for demos
```

The service only talks to the detector through the `AnalysisPipeline` protocol (`extract_chapter`, `review_chapter`, `health`), and the implementation is chosen with `PIPELINE_MODE`. Rules for the codebase are in `CLAUDE.md`, and the request flow and state machine are in `docs/ARCHITECTURE.md`.

### What the detector actually runs on (`PIPELINE_MODE=weave`)

The engine lives in `app/pipeline/weave_core/`. It is vendored unchanged from my separate Weave repository (`VENDORED.md`). `app/pipeline/weave.py` is the adapter that connects it to the service.

| Stage | How | LLM? |
|---|---|---|
| Event extraction | Chapter split into 2,500-char chunks, each turned into structured events by an LLM prompt (`weave_extract.py`) | **yes** |
| Entity registry | `NEW:label` → canonical IDs (`weave_registry.py`) | no |
| State ledger | Event-sourced replay of all confirmed events (alive / location / possessions / knowledge) (`ledger.py`) | no |
| Rule checker | Each new event's premises vs. the ledger (`checker.py`) | no |
| Retrieval | Related past events for the judge (`ers.py`) | no |
| Judge | LLM judges each scene **k = 3** times, flagged if **≥ 2** agree (`judge.py`, `weave_pipeline.py`) | **yes** |

Defaults in `app/core/config.py`: provider `openrouter`, model `nvidia/nemotron-3-ultra-550b-a55b:free`, `weave_judge_mode="llm"`. The optional parts are all **off by default**:

- learned judge v1: `WEAVE_JUDGE_MODE=learned`
- learned judge v2: `JUDGE_V2_ENABLED=false`
- deterministic extraction verifier: `WEAVE_VERIFIER=off`
- extraction gate: `WEAVE_GATE_MODE=off`
- pre-judge fixes F1–F9: all `false`

The code default and `.env.example` both use `PIPELINE_MODE=fake`. The capstone presentation demo ran on recorded/replayed LLM output rather than live calls (`docs/learned-judge-training-design.md` §0-7, §0-8).

**Measured quality of the LLM judge.** All numbers are from `docs/EVAL.md` and the training-design doc.
- **Recorded "frozen" run** (nemotron, k=3, 2/3 majority) on three benchmark sets whose test scenes are mostly LLM-written:
  - recall **18/60 = 0.300** [0.199–0.425], false positives **3/60 = 0.050**
  - by story: WM 15/20, Q1 0/20, Q3 3/20
- **Titanic set**: a human-written screenplay with contradictions planted by an LLM.
  - The same LLM judge scored scene AUROC **0.521 [0.375, 0.667]** with **14/24** false positives.
  - In 150 of its 153 positive verdicts, the judge relied on stale ledger state, such as a character still recorded as handcuffed after he was released (§0-9).
  - A later review found that this evaluation design had its own flaw: events that come before each test scene were missing from the ledger. So this number is not clean either.
- The rule checker (no LLM) runs as a CI regression gate on a 30-scene dev set: 0 flags on 10 normal scenes and 3 of the 4 checker-type contradictions caught (`docs/EVAL.md`). This is a regression gate, not a generalisation estimate.

I do **not** claim that the deployed pipeline works well on human-written fiction. The measurements above say otherwise.

---

## What I built

The commits in this project are mine. I wrote them with heavy use of an AI coding assistant (Claude Code), which also appears as a co-author on many commits. I made the design and evaluation decisions and directed the implementation.

- **Backend**:
  - FastAPI service with auth (password + Firebase ID tokens)
  - projects/chapters, uploads and parsing
  - arq job queue and status state machine
  - append-only canon with point-in-time queries, with a Hypothesis property test (`tests/test_canon_properties.py`). It found two restore bugs, which are fixed, and one late-re-save ordering bug, which is still open and kept as a strict `xfail`.
  - rate limiting, security headers, a production start-up guard (`APP_ENV`), Docker/compose deploy files
- **Weave engine and integration**: the event-sourced ledger / rule checker / LLM judge design (vendored from my Weave repo) and the adapter `app/pipeline/weave.py`
  - adapter features: per-work state, locking, parallel judge calls, relation building from co-occurring events, LLM call recording and replay
- **Evaluation**:
  - checker regression gate in CI, and judge-layer harness `scripts/eval/judge_eval.py` that reproduces the frozen numbers above
  - pair export and labelling tools
  - a deterministic extraction verifier: `app/pipeline/extract_verifier/`, no LLM calls
- **Learned judge**: everything in the next section

## Learned judge

**Goal:** replace the LLM judge with a small, local model. The model is a Korean RoBERTa cross-encoder, which is deterministic, free to run, and fits on an RTX 3060 12 GB. The rules were fixed in writing before any results were seen:

- **Pre-registration:** gates, data rules and the final test conditions were committed before scoring (`docs/learned-judge-plan.md`, `docs/learned-judge-training-design.md` §0). Any change made after a result is tagged as such.
- **Leakage rules:** splits are by story (v1) or by author (v2). The evaluation story or author is never used for training, data generation or threshold selection.
- **Sealed test sets:** each final test set was hashed (`SEAL.json`, sha256) and scored **once**.
- **Gate:** scene-level AUROC ≥ 0.65 **and** stratified-bootstrap lower bound > 0.5 (2,000 resamples, seed 0).
- **Shortcut baselines:** every result is reported next to "edit-trace" single-feature baselines, such as the length difference between the two edited versions, and next to a zero-shot NLI model.

### v1: structured pairs from the Weave ledger (`scripts/train_judge.py`, `app/pipeline/learned_judge.py`, `app/pipeline/pairs.py`)

- **Input:** a pair of (ledger premise, hypothesis from an extracted event), 3-class (contradiction / consistent / unrelated)
- **Model:** `klue/roberta-base` / `-large`
- **Training options:**
  - KLUE-NLI pre-training
  - frequency-weighted cross-entropy
  - per-epoch down-sampling of unrelated pairs
  - multiple seeds with selection on validation
  - temperature calibration
  - a scene threshold chosen as maximum recall at ≤ 1 false positive
- **Precision:** the same as in the service (fp16, eager attention)
- **Serving:** `WEAVE_JUDGE_MODE=learned`

| Run | Test set | Scene AUROC [95 %] | Gate | Source |
|---|---|---|---|---|
| First fold (base, 3 seeds) | Q1 (dev) | recall 0.05 / 0.05 / 0.00 vs. zero-shot 0.10 | – | `docs/learned-judge-handoff.md` §3-1 |
| Exp. N (natural-language pairs) | Q3, once | 0.590 [0.420, 0.748] | fail | design doc §0-9 |
| Exp. N | Titanic, sealed, once | 0.636 [0.471, 0.785] | fail | `docs/EVAL.md` "실험 N — 타이타닉 최종 1회" |

On Titanic the "edit-trace" baseline alone reached 0.624. The paired P−N score difference was 0.031 [−0.020, +0.097], with sign-test p = 0.84. In none of the 6 scenes that had the key pair did that pair rank first. I read this as: the model does not see the contradiction, and its signal comes from edits disturbing the extraction.

### v2: claim sentence ↔ retrieved context (`scripts/v2/`, `app/pipeline/judge_v2/`)

**Design**, after MiniCheck / AlignScore / SummaC; design doc §0-10:
- **Claim:** each sentence of the new passage.
- **Context:** up to 4 earlier-text chunks retrieved with character-bigram BM25, in a 512-token window, context first.
- **Model:** `klue/roberta-large` fine-tuned on KLUE-NLI → KorNLI.
- **Two-stage recipe**, fixed in advance with no model selection because the dev split was too small:
  - **Stage 1:** binary pairs. B = 222 claim-side minimal pairs; D = 1,999 real retrieved negatives. Settings: 3 epochs, lr 1e-5, seed 2.
  - **Stage 2:** multiple-instance learning on 43 scene twins, where the scene score is the max over sentences, trained with scene BCE. Settings: 2 epochs, lr 5e-6.
  - Training took 482 s for stage 1 on the RTX 3060, peaking at 9,970 MiB.

**Training data:**
- Source: Korean public-domain fiction by 현진건, 나도향, 채만식 and 이상.
- Minimal pairs: an LLM wrote them, and other LLM passes filtered them by consensus.
- Audits: three audits by separate sessions, each of 30 pairs per type. One data type (A, context-side edits) failed the third audit (23/30) and was dropped by a rule fixed beforehand.

**Test set (PD-eval):**
- 38 P/N twin pairs from 11 works by **different authors** (김유정, 이효석). P has a contradiction planted, and N has a same-sized consistent edit.
- A pair was kept only if two independent LLM judges agreed; 50 % of 76 candidates were rejected.
- The edit-trace shortcut AUROC was at most 0.543. The set was sealed, then scored once.

| Model | Scene AUROC [95 %] | P > N (of 38) | Source |
|---|---|---|---|
| **v2 final** | **0.496 [0.366, 0.623]**, gate failed | 1 (36 ties) | design doc §0-10 "측정 1 결과" |
| NLI-only baseline (no task training) | 0.501 [0.372, 0.629] | 3 (34 ties) | same |

**Why it failed (measured):** in almost every pair, the scene maximum came from a sentence that the P and N versions **share**, so P and N got the same score. The edited sentence never became the scene maximum. The model has no sentence-level discrimination, and max-aggregation exposes that directly.

**Was data volume the bottleneck?** This was an exploratory follow-up that reuses the already-used test set, so it is **not confirmatory**:
- **X1:** trained on the full unfiltered data (A 818, B 679, C 96, S 179 pairs). Result: **0.511 [0.381, 0.637]**.
- **X2:** filtered data plus A 43. Result: 0.497 [0.366, 0.626].

Both intervals overlap the confirmatory result, so by the rule written in advance this supports *"the task formulation and aggregation are the bottleneck, not the amount of data"*. Source: design doc §0-10 "탐색 실행".

### What I learned

1. **The judge was not the only weak link.** On human-written text, both the learned judge and the LLM judge failed. In the Titanic run, the extractor missed 10 of 24 planted sentences, and the ledger did not expire temporary states such as location or arrest. Better judges cannot fix missing or stale premises.
2. **Benchmarks written by an LLM hid this.** In the benchmark sets where results looked fine, most test scenes were written by an LLM (40 of 45 per set in Q1/Q3, per `authored_by` in `labels.json`), and the contradictions were planted by an LLM. Human-written text exposed the extraction and ledger problems.
3. **Pre-registration and sealing did their job.** Without them, the 0.658 dev result in experiment N would have looked like success. That gain did not transfer to the next test set (0.590 on Q3).
4. **Always report shortcut baselines.** Several "signals" were at the level of edit-length or same-slot shortcuts.

### Limitations

- All labels, audits and planted contradictions came from LLM sessions (Claude Sonnet / Opus, nemotron). **No human annotation**, and the test sets are small (24–38 twin pairs).
- The Titanic evaluation had a design flaw (missing preceding events in the ledger), which affects both judges' numbers there.
- Training data, model weights and the sealed test sets are kept outside this repository and are not published.

---

## Team

Glitch was a team capstone. Parts that are not mine:

- **Cho Euna:** UI design and the original HTML/CSS/JS frontend prototypes. I ported them into `frontend/` and connected them to the backend API.
- **Yujin Jo:** the team's original AI analysis pipeline (`Project_scope_AI`, including the embedding-based ERS event-relation scoring). The earlier `remote` mode called it. Its output was used to build the presentation's replay demo data. That pipeline is **not** included in this repository.

## Benchmark data in this repo

The corpora under `tests/eval/` come from the Weave development repository. They hold labels, entity registries, extracted event ledgers and, for Q1/Q3/Sherlock, the test scenes.

| Set | Text origin |
|---|---|
| **WM** (near-future SF web novel, episodes 1–5 + 30 test scenes) | Written by an LLM that was prompted with methods from literary-studies papers. It is the project's own development corpus, not a third-party work. Only extracted events are stored here. Their `source_text` fields quote that LLM-written text. |
| **Q1**, **Q3** | Original stories for this project. 40 of 45 test scenes per set are LLM-written (`authored_by` in `labels.json`). |
| **Sherlock** | Korean translation of the public-domain *The Adventures of Sherlock Holmes*, with planted contradictions in the test scenes. |

## Not included

- Demo replay data (`app/pipeline/replay_data/<story>/`): built from film screenplays, which are third-party text. `tests/test_replay_pipeline.py` skips when that data is missing.
- `scripts/build_replay_interstellar.py`, the builder for the second demo story: it embedded screenplay lines.
- `.env`, Firebase project config (placeholders in `frontend/firebase-config.js`), runtime data (`weave_data/`, `uploads/`, `logs/`, `backups/`), model weights and training data.

## Run

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
copy .env.example .env                      # set JWT_SECRET, REDIS_PASSWORD
docker compose up -d db redis               # PostgreSQL 127.0.0.1:5433, Redis 127.0.0.1:6379
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m arq app.workers.worker.WorkerSettings
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

- `PIPELINE_MODE=fake` works without any API key.
- `PIPELINE_MODE=weave` needs an OpenRouter, NVIDIA NIM or Anthropic key in `WEAVE_API_KEY`.
- The learned judge needs `pip install -e .[judge]` (inference) or `.[train]` (training).
- Tests use a real PostgreSQL test database (`tests/conftest.py`).
- Full setup notes (Korean): `docs/README.ko.md`.
