# Ghost Data Still Exists — deletion-audit agent

HackerRank Orchestrate (October 2026). For each request in `dataset/deletion_requests.csv` the agent decides
whether deletion was authorized, reconstructs the subject's identifiers, finds every copy of their data at
`audit_at`, applies `deletion_rules.csv`, and writes `COMPLETE` / `INCOMPLETE` / `NOT_APPLICABLE` with an ordered
FINDING → REMEDIATION → VERIFICATION plan to `output.csv`.

Challenge contract: [`problem_statement.md`](./problem_statement.md) · Design spec: [`code/docs/SPEC.md`](./code/docs/SPEC.md) ·
Results: [`code/evaluation/evaluation_report.md`](./code/evaluation/evaluation_report.md),
[`code/evaluation/usage_report.md`](./code/evaluation/usage_report.md)

## Quick start (no API key needed)

Requirements: Python ≥ 3.10, standard library only (SQLite ships with Python).

```bash
python3 code/main.py                    # 518 requests -> ./output.csv   (~12 s)
python3 code/main.py --cases sample     # 32 public samples -> ./sample_predictions.csv
python3 code/evaluation/main.py         # metrics + regenerate both reports (~1.5 min)
python3 code/evaluation/check_all.py    # quality gate: tests, sample regression, invariants (~40 s)
python3 -m unittest discover -s code/tests -t code   # 22 unit + 15 end-to-end scenario tests
python3 code/package.py                 # build code.zip (README.md + code/, no dataset)
```

The default backend is `replay`: every LLM result the pipeline needs is committed in `code/cache/llm_cache.jsonl`,
so a clean clone reproduces `output.csv` byte-for-byte with no network access and no key.

## Running the LLM live

| `GDA_LLM_BACKEND` | What it does | Needs |
|---|---|---|
| `replay` (default) | committed cache only; on a miss, falls back to the deterministic heuristic | nothing |
| `offline` | ignores the cache; heuristic intent, no visual extraction | nothing |
| `claude_cli` | `claude -p --output-format json` with the logged-in Claude subscription (used for development and for producing the cache) | `claude` CLI logged in |
| `anthropic` | Anthropic Messages API through the official SDK | `pip install anthropic`, `ANTHROPIC_API_KEY` |
| `openai` | OpenAI Chat Completions through the official SDK | `pip install openai`, `OPENAI_API_KEY`, `GDA_PRICE_OVERRIDE_<ALIAS>="in,out"` |

Other settings: `GDA_MODEL_FAST` (default `claude-haiku-5-5`, intent), `GDA_MODEL_VISION` (default `claude-sonnet-5-5`,
PNG/PDF evidence), `GDA_BUDGET_USD` (default `3.00`, `0` = cache only), `GDA_BUDGET_SCOPE` (`total` | `run`),
`GDA_BUDGET_ON_EXCEED` (`fallback` | `abort`, exit code 3). Secrets are read from the environment only; keep them
in an uncommitted `.env` and `export` them. Example: `GDA_LLM_BACKEND=claude_cli python3 code/main.py`.

CLI options of `code/main.py`: `--dataset-dir DIR`, `--requests FILE`, `--out PATH`, `--trace-dir DIR` (one JSON
decision trace per case: identifiers, owned resources, how each was linked, the observations and the rule applied),
`--backend NAME`, `--workflow-model PATH`, `--relearn-workflow-model`, `-v`.

## How it works

```
dataset/ ─► loaders ─► IdentityResolver (current + historical email/phone/hash, user_id)
             │
             ├─► evidence extractors ─► ObservationStore  (facts, never judgements)
             │     SQL snapshots (typed tables + JSON-in-table replicas, via sqlite3) · JSON bundles / service
             │     exports / ledgers · handover TXT · 5 log layouts joined by trace id · activity stream ·
             │     PNG/PDF via a cached vision-LLM transcription
             │
             ├─► IntentClassifier (LLM, batched + cached; multilingual heuristic fallback)
             │
             └─► per case, as of audit_at:
                   1. ownership: identifier links + deletion-workflow links
                   2. state: latest observation ≤ audit_at (replicas: most exposing location wins;
                      sync/restore "repopulated" events re-create; inferred snapshot absence only as a fallback)
                   3. policy: data category → DELETE / REDACT / ANONYMIZE / RETAIN / block restore / disable flow
                   4. plan: findings + ordered remediation (flows/restores first) + verification, with citations
                 ─► serializer + contract validator ─► output.csv
```

- **The LLM never chooses steps.** It only (a) labels request intent and (b) transcribes the 15 screenshots/PDFs into
  typed records. Prompts: `code/ghostaudit/llm/prompts/*.md`; the visual transcriptions are auditable in the cache.
- **Claims are not proof.** Workflow results (`DELETE_RECORD SUCCESS`, `reported_status`) never decide a state;
  readbacks, snapshots, check events and recreation events do.
- **Workflow linking.** Profile deletions and successful policy actions in `system_activity.csv` usually carry no
  identifier (a 404 account, an anonymized analytics profile). The deletion worker runs each request's steps on a
  fixed sub-minute cadence after the profile deletion; that cadence is learned from workflows whose resources are
  identifier-linked (`code/cache/workflow_model.json`) and used to attach un-linked steps and to assign each
  profile deletion to exactly one request by elimination — only with information available at the case's `audit_at`.
- **No time travel.** Every observation, activity and cross-case elimination is cut at `audit_at`; the invariant
  suite re-runs cases with later evidence physically deleted and requires identical rows.

## Evaluation (details in the report)

| Measure | Result |
|---|---|
| Sample status accuracy (dev / holdout) | 100% / 100% |
| Sample step F1 · exact sequence | 96.0% · 68.8% (most misses are audit records absent from all pre-audit evidence) |
| Intent: own 62-text multilingual set + 32 samples (LLM) | 100% accuracy, NON_DELETE recall 100% |
| Custom end-to-end scenarios | 15 / 15 |
| Invariants on the 518-case run | contract, citations ≤ audit_at, policy sanity, determinism, identifier invariance, time travel: all pass |
| LLM cost to build the cache used by the final run | ≈ $2.41 (49 calls), $0 marginal for replay runs |

## Repository layout

```
code/main.py                 CLI entry point
code/ghostaudit/             pipeline: models, loaders, identity, intent, evidence/, llm/, engine/, output
code/cache/                  committed LLM cache + frozen workflow model
code/evaluation/             scorer, invariants, harness, check_all gate, intent set, reports
code/tests/                  unit tests + e2e scenario builder (miniature datasets in every evidence format)
code/docs/SPEC.md            design spec and decisions
```

Submission link: https://wwww.hackerrank.com/contests/hackerrank-orchestrate-october26/challenges/ghost-data-still-exists
