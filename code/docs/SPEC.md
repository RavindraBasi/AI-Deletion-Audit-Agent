# Ghost Data Still Exists — Implementation Spec

Status: **v1.1, approved decisions D1–D8** · Owner: architect · Audience: humans + coding agents working in parallel worktrees
Contract sources (read-only, higher priority than this file): `AGENTS.md`, `problem_statement.md`, `README.md`.

---

## 0. TL;DR

Build a terminal-runnable Python pipeline (`python3 code/main.py`) that, for each of the 518 rows in
`dataset/deletion_requests.csv`, decides `COMPLETE | INCOMPLETE | NOT_APPLICABLE` and emits an ordered
`steps_json` plan. Most of the work is **deterministic code**: identity resolution, evidence parsing,
point-in-time state reconstruction, policy application, and step ordering. An **LLM** is used only for
(a) classifying the request's intent across languages and (b) a one-time, cached extraction of facts
from the 15 PNG/PDF evidence files. Every LLM output is cached on disk and committed, so a clean run is
reproducible with no API key.

## 1. Decisions (agreed with the human)

| # | Decision | Consequence |
|---|---|---|
| D1 | **Hybrid deterministic** architecture | The LLM never chooses steps. It produces typed facts (intent label, extracted records) that the rule engine consumes. |
| D2 | **Provider-agnostic LLM adapter** + **Claude subscription backend** for local trials/debugging | Backends: `anthropic` (API key), `openai` (API key), `claude_cli` (local `claude -p`, uses the logged-in subscription), `replay` (cache only), `offline` (heuristics). Selected via env `GDA_LLM_BACKEND`. Default for development + final run: `claude_cli` (D6). |
| D3 | **Content-hash cache + offline fallback** | Cache is committed under `code/cache/`. If the cache misses and no backend is available, a deterministic heuristic is used. `usage_report.md` is generated from a call ledger. |
| D4 | **One git worktree per work package (WP)** | WPs own disjoint files. Shared contracts are frozen in WP-0 before fan-out. Merges go to `main` behind a sample-regression gate. |
| D5 | **Deadline: 2026-10-25 13:30 CET = 12:30 UTC** | Five parallel WPs after WP-0. Hard milestones M0–M3 at absolute times (§9). Anything marked *stretch* is cut first. |
| D6 | **Claude subscription (`claude_cli`) is the backend for development and the final run** | API-key backends are implemented and tested with mocks. A live smoke test is optional (§12). |
| D7 | **Configurable LLM spending limit, default $3.00** | Enforced by `llm/budget.py` before every non-cached call (§6.1). |
| D8 | **Git workflow:** agents create `wp<N>-<slug>` branches and worktrees and commit to them. Humans open the MRs. | When a WP is ready, the agent notifies the human. It never pushes to or merges into `main` itself. |

## 2. Domain model (what the agents must understand)

### 2.1 Entities
- **Subject**: a `user_id` from `user_identities.csv`. Its identifier set = current {name, email, phone,
  email_hash} plus every `old_value`/`new_value` in `user_audit_log.csv` for that user.
  `email_hash = sha256(lower(email))` (verified: `minjun.kim90@gmail.com → 44a651…`).
- **Resource**: `(system_id, resource_id)`. Prefix → system: `account_`→account_service,
  `search_`→search_service, `support_`→support_platform, `analytics_`→analytics_platform,
  `export_`→export_service, `data_flow_`→integration_service, `backup_`→backup_service,
  `audit_record_`→security_service, `tombstone_`→identity_service. Replicas (failover/read/regional/
  reporting) are **locations** of the same resource. They are not separate systems: a profile left on a
  failover replica is reported as `account_service/account_005`.
- **Observation**: one fact from one evidence item about one resource at one time (schema in §4.2).
- **Claim**: an activity row whose `reported_result` asserts an outcome (`DELETE_RECORD SUCCESS`, …).
  Claims are **never** proof of state.

### 2.2 Data categories → required end-state (from `deletion_rules.csv`)

| Category | Typical source | Rule | Required final state | Finding if violated |
|---|---|---|---|---|
| USER_PROFILE | `account_*` | DELETE | ABSENT | REPORT_REMAINING_RECORD |
| SEARCH_INDEX_ENTRY | `search_*` | DELETE | ABSENT | REPORT_REMAINING_RECORD |
| CACHE_ENTRY | cache | DELETE | ABSENT | REPORT_REMAINING_RECORD |
| SUPPORT_ATTACHMENT (belongs only to subject) | `support_*` with file/attachment | DELETE | ABSENT | REPORT_REMAINING_RECORD |
| SUPPORT_MESSAGE (shared context) | `support_*` message | REDACT | REDACTED | REPORT_EXPOSED_PII |
| ANALYTICS_PROFILE / PRODUCT_TELEMETRY | `analytics_*` | ANONYMIZE | ANONYMIZED | REPORT_EXPOSED_PII |
| MARKETING_PROFILE | marketing / contact records (e.g. `email_opt_in`) | DELETE | ABSENT | REPORT_REMAINING_RECORD |
| USER_EXPORT | `export_*` | DELETE | ABSENT | REPORT_REMAINING_RECORD |
| EMAIL_DELIVERY_LOG | notification logs | REDACT | REDACTED | REPORT_EXPOSED_PII |
| SECURITY_AUDIT_LOG (in retention) | `audit_record_*` | RETAIN | PRESENT | — (verify only) |
| LEGAL_HOLD_RECORD (hold active) | legal register | RETAIN | PRESENT | — (verify only) |
| BACKUP_ARCHIVE | `backup_*` | RETAIN + block restore | BLOCKED | REPORT_RESTORE_RISK |
| ACCOUNT_TOMBSTONE | `tombstone_*` | REDACT (keep `subject_reference` only) | REDACTED | REPORT_EXPOSED_PII |
| (data flow, not a rule row) | `data_flow_*` | disable | DISABLED | REPORT_ACTIVE_DATA_FLOW |

Note from sample `case_12`: an `analytics_platform` record that is a **contact/marketing lookup**
(`email_opt_in`, keyed by an old email hash) was treated as REMAINING_RECORD → DELETE, not ANONYMIZE.
The category comes from the record's **content**, not only its system.

### 2.3 Facts observed in the dataset (all agents should know these)
- Evidence is released in **batches**. Roughly every 5 days there is one inventory SQL, one readback log, one
  service export JSON and one snapshot bundle, and ~10 cases share an `audit_at` equal to (or just after)
  that capture time. Samples cite the **latest evidence item captured ≤ `audit_at`** that observes the
  resource (hypothesis H1, to be validated in WP-4).
- Decoys are deliberate: emails differing only in case (`SIPHO.MOKOENA@GMAIL.COM`), near-miss emails
  (`g.silva76.jr@gmail.com` vs `g.silva76@gmail.com`), people with similar names, and evidence captured
  after `audit_at` (for example `security_audit_ledger_20261001.json`).
- Logs span 4 formats: logfmt, JSON-per-line, RFC5424 syslog, and the readback trace. The lookup key and
  the `resource_id` often sit on **different lines joined by `trace_id`**.
- Useful activity templates: `CHECK_DATA_FLOW` ("remains enabled") and `CHECK_RESTORE_ACCESS` ("can still
  be restored") are **state observations**. `SYNC_RECORD` with "Active connector repopulated downstream
  resource" and `RESTORE_BACKUP` "repopulated" are **recreation events**. `LINK_RESOURCE_IDENTITY` links
  resource ↔ email/user. `CLASSIFY_REQUEST` "non-deletion" is a claim that corroborates intent.
  `DELETE/REDACT/ANONYMIZE/BLOCK/DISABLE …` results are **claims**.
- About 418 of the 518 request emails match a current identity and about 100 match a historical email
  only. About 10 request texts are clearly non-deletion (marketing opt-out, export, correction, access,
  invoices, login help).

## 3. Architecture

```
dataset/ ──► Loaders ──► IdentityResolver ─┐
                 │                          │
                 ├─► Evidence extractors ───┼─► ObservationStore (indexed by identifier & resource)
                 │   (sql, json, txt, log,  │
                 │    activity, multimodal*)│
                 │                          ▼
                 └─► IntentClassifier* ──► CaseAuditor (per case, as-of audit_at)
                                            ├─ link resources to subject (ownership graph)
                                            ├─ reconstruct latest state ≤ audit_at
                                            ├─ categorize + apply policy
                                            ├─ build findings / remediation / verification
                                            └─ cite evidence
                                                       ▼
                                       StepSerializer + Validator ──► output.csv
(* = may call LLM through LLMAdapter → cache → ledger)
```

Pipeline properties: single process, no network calls when the cache is warm, deterministic ordering,
and runtime under 60 s for all 518 cases.

### 3.1 Repository layout and file ownership

```
code/
  main.py                         WP-0  CLI entry: python3 code/main.py [--cases sample|eval] [--out PATH]
  requirements.txt                WP-0  stdlib-first; optional: anthropic, openai
  ghostaudit/
    __init__.py                   WP-0
    models.py                     WP-0  FROZEN contracts (§4). Changes need an architect ACK.
    config.py                     WP-0  paths (resolved from repo root), env vars
    loaders.py                    WP-0  CSV loaders for requests, identities, audit log, rules, manifest
    output.py                     WP-0  Step ordering, serialization, output validator
    identity.py                   WP-1
    evidence/
      __init__.py                 WP-2  build_observation_store(manifest, as_of=None) -> ObservationStore
      store.py                    WP-2
      sql_snapshots.py            WP-2
      json_records.py             WP-2
      text_records.py             WP-2
      logs.py                     WP-2
      activity.py                 WP-2
      multimodal.py               WP-3  PNG/PDF → Observations (LLM + cache)
    llm/
      adapter.py                  WP-3  LLMAdapter interface + backend registry
      backends/{anthropic_api,openai_api,claude_cli,replay,offline}.py   WP-3
      cache.py, ledger.py         WP-3
      prompts/*.md                WP-3  all prompts as Markdown (visible to the judges)
    intent.py                     WP-3
    engine/
      auditor.py                  WP-4  audit_case(case, ctx) -> CaseResult
      linking.py, state.py, policy.py, planner.py, citation.py   WP-4
  cache/                          WP-3  committed LLM cache (llm_cache.jsonl) + extracted_evidence.md
  evaluation/
    main.py                       WP-5  scorer + report generator
    scorer.py, custom_cases/      WP-5
    evaluation_report.md          WP-5  (numbers filled in at M3)
    usage_report.md               WP-5  (generated from the ledger of the final run)
  tests/                          each WP: tests/test_<module>.py (pytest, or unittest from stdlib)
  docs/SPEC.md                    architect
README.md (root)                  WP-5  setup/run, backends, packaging
```

## 4. Frozen contracts (WP-0 delivers; everyone codes against these)

### 4.1 Inputs
```python
@dataclass(frozen=True)
class Case:  case_id: str; email: str; requested_at: datetime; reported_status: str
             reported_at: datetime; audit_at: datetime; request_text: str

@dataclass(frozen=True)
class Rule:  rule_id: str; data_category: str; condition: str; required_action: str

@dataclass(frozen=True)
class EvidenceFile: file_id: str; path: Path; origin: str; captured_at: datetime
```
All datetimes are timezone-aware UTC. Emails are compared by `normalize_email()` (strip + lower).
Phones are compared by `normalize_phone()` (digits only, with leading `+` kept).

### 4.2 Observation (the central currency between WP-2/WP-3 and WP-4)
```python
class Kind(str, Enum):        # what the observation says about the resource
    PRESENT = "PRESENT"       # record exists (direct identifiers may or may not be present; see `identifiers`)
    ABSENT = "ABSENT"         # lookup returned 0 rows / 404 / NOT_FOUND / missing from a full snapshot table
    REDACTED = "REDACTED"     # exists, direct identifiers removed (tombstone/support with nulls or a REDACTED status)
    ANONYMIZED = "ANONYMIZED" # exists, unlinked from any user
    FLOW_ACTIVE = "FLOW_ACTIVE";  FLOW_DISABLED = "FLOW_DISABLED"
    RESTORE_ALLOWED = "RESTORE_ALLOWED";  RESTORE_BLOCKED = "RESTORE_BLOCKED"
    LINK = "LINK"             # links the resource to identifiers without stating its state
    RECREATED = "RECREATED"   # sync/restore repopulated the resource (from the activity stream)
    CLAIM = "CLAIM"           # workflow outcome claim; never decides state

@dataclass(frozen=True)
class Observation:
    evidence_id: str          # file_id | activity_id | audit_id  (the id that may be cited)
    observed_at: datetime     # row/line timestamp if present, else the file's captured_at
    captured_at: datetime     # manifest captured_at (or event_time for activity) — used for the ≤ audit_at filter
    system_id: str            # canonical system (replicas collapse to their logical system)
    resource_id: str
    kind: Kind
    category: str | None      # DATA_CATEGORY if derivable (record_type column, table, content), else None
    identifiers: frozenset[tuple[str, str]]  # {("email","x@y"),("user_id","user_12"),("email_hash",..),("phone",..),("name",..)} normalized
    location: str = ""        # e.g. "failover_replica", "read_replica", "primary", "log:trace_id"
    attrs: Mapping[str, str] = {}  # raw extras: enabled, restore_allowed, event_code, retention_until, attachment file, message_excerpt…
```
Extractor rule: **emit facts, not judgements**. Extractors never decide relevance to a case.

### 4.3 Output step
```python
@dataclass(frozen=True)
class Step: phase: str; system_id: str; resource_id: str; operation: str; state: str; evidence_id: str
@dataclass(frozen=True)
class CaseResult: case_id: str; deletion_status: str; steps: list[Step]
```
`output.py` assigns `step_no`, sorts within each phase, serializes with
`json.dumps(..., ensure_ascii=False, separators=(",", ":"))` using the key order from the problem statement,
and validates each row (allowed phase/op/state pairs, consecutive numbering, empty `evidence_id` on
INCOMPLETE remediation/verification steps, `[]` for NOT_APPLICABLE, 518 rows, sorted by `case_id`).

**Ordering (normative):** phase order FINDING → REMEDIATION → VERIFICATION. Within a phase: operation
index in the problem-statement table, then `system_id`, then `resource_id` (ascending; string compare —
WP-4 must confirm natural vs. lexicographic order on samples and record the result here).

### 4.4 LLM adapter
```python
class LLMAdapter(Protocol):
    def complete(self, *, task: str, prompt: str, images: list[Path] = [], model: str | None = None,
                 json_schema: dict | None = None) -> LLMResult  # text, parsed_json, usage{in,out}, model, provider, cached: bool
```
Cache key = `sha256(task | backend-agnostic model alias | prompt | sha256(image bytes)…)`. Every call
(including cache hits, flagged) is appended to `code/cache/ledger/<run_id>.jsonl`.

## 5. Decision algorithm (WP-4, normative)

For each case:
1. **Intent.** `intent.classify(text) -> DELETE | NON_DELETE` (+ confidence). NON_DELETE → `NOT_APPLICABLE, []`.
2. **Identity.** `identity.resolve(email, as_of=audit_at) -> Subject | None`. The email may be current or
   historical. Return the full identifier set (current + all historical values). If no subject is found, use
   the bare email (plus its sha256) as the identifier set.
3. **Ownership graph.** Using only observations with `captured_at ≤ audit_at`, collect every resource
   whose `identifiers` intersect the subject's set. Then expand transitively through `LINK` observations
   and resource-to-resource references (for example a search doc that references `account_006`, or a record
   with `customer_reference=user_x`). Exact normalized match only — no fuzzy matching (decoys).
4. **State at audit_at.** For each owned resource, take the latest non-CLAIM observation ≤ `audit_at`
   (`observed_at`, tie-break by `captured_at`, then by stronger kind). If *any* location (replica) in
   the latest snapshot still shows the subject's identifiers, the resource is PRESENT. A `RECREATED` event
   after the last ABSENT observation flips the resource back to PRESENT unless a later observation shows
   ABSENT.
5. **Categorize + policy.** Map each resource to a category (§2.2) and its required final state. Check
   rule conditions: retention period still valid at `audit_at` → RETAIN; legal hold active; support
   attachment owned only by the subject → DELETE vs. shared message → REDACT.
6. **Compliance.** A resource is compliant if its state equals the required state (PRESENT for retained
   records, BLOCKED for backups, DISABLED for flows, ABSENT/REDACTED/ANONYMIZED otherwise).
   For a non-compliant resource, the FINDING uses the §2.2 finding op, plus REMEDIATION and VERIFICATION
   of the required state.
7. **Status.** COMPLETE iff every owned resource is compliant. Otherwise INCOMPLETE.
8. **Verification set** (both statuses): the account record (VERIFY_RECORD_ABSENT) is always included,
   plus every owned resource whose required treatment is not "ignore". Retained security/legal records →
   VERIFY_RECORD_PRESENT. Tombstones → VERIFY_RECORD_REDACTED. Analytics → ANONYMIZED. Flows → DISABLED.
   Backups → BLOCKED.
9. **Citation.** FINDING steps, and every step of a COMPLETE case, cite the evidence_id of the observation
   that established the decisive state (H1: latest capture ≤ audit_at). INCOMPLETE remediation and
   verification steps use `""`.
10. **Ordering** via `output.py`.

Hypotheses WP-4 must confirm or refute against `sample_output.csv`, recording each result in §10:
H1 (citation = latest relevant capture ≤ audit_at), H2 (account verification always present), H3 (resource_id
sort order), H4 (marketing/contact analytics → DELETE), H5 (an anonymized analytics record is still "owned"
when it is linked by earlier evidence).

## 6. LLM usage policy

| Task | When | Default model alias | Volume | Fallback (offline) |
|---|---|---|---|---|
| `intent` | Unique request texts. Batch 25 per call with JSON output `[{id, label, confidence, reason}]` | `fast` (e.g. claude-haiku-5-5 / small OpenAI model) | ≈ 375 unique texts → ~15 calls | Multilingual keyword rules (EN/ES/PT/FR/JA/KO deletion verbs) with negative patterns (marketing-only, export/copy/download, correction, access, invoices, login) |
| `extract_visual` | Once per PNG/PDF (15 files). JSON output = a list of records `{system_id, resource_id, category?, state, identifiers…, observed_at?}` | `vision` (e.g. claude-sonnet-5-5) | 15 calls | Skip and log a warning (a warm cache is expected to be committed) |

- Prompts live in `code/ghostaudit/llm/prompts/*.md`. Temperature is 0. Output is JSON validated against a schema, with one retry on a parse error.
- `claude_cli` backend: `claude -p --output-format json --model <alias>` (prompt passed on stdin). Usage and cost are read from the JSON result. For images or PDFs, pass the file path in the prompt and allow only the `Read` tool. It is intended for local trials. CI and the final run may use any backend.
- After the multimodal pass, the extracted records are also rendered to `code/cache/extracted_evidence.md`
  so humans and judges can audit what the model saw.

### 6.1 Spending limit (`llm/budget.py`, owned by WP-3)
- **Config:**
  - `GDA_BUDGET_USD` (float, default `3.00`; `0` means cache/offline only).
  - `GDA_BUDGET_SCOPE` = `total` (default: sums every ledger in `code/cache/ledger/`, so repeated
    runs can't exceed the cap together) or `run` (this run only).
  - `GDA_BUDGET_ON_EXCEED` = `fallback` (default: use the offline heuristic or skip the extraction, then
    continue) or `abort` (raise `BudgetExceeded`, exit code 3).
- **Cost source:**
  - `claude_cli` uses `total_cost_usd` from `claude -p --output-format json`. On a subscription this is
    the API-equivalent cost, which is what the usage report needs anyway.
  - API backends use `tokens × price` from the table `llm/pricing.py`, keyed by model id with USD per
    million input/output tokens. Prices are filled in from official provider pricing during WP-3, never
    from memory. An unknown model is an error unless `GDA_PRICE_OVERRIDE_<ALIAS>` is set.
  - Cache hits cost $0. They are still written to the ledger with `cached=true`.
- **Enforcement (before every non-cached call):** estimate `cost = in_tokens_est × in_price +
  max_output_tokens × out_price`, where `in_tokens_est = len(prompt)/3.5` plus the provider-documented
  cost per image or page. If `spent + estimate > budget`, apply `ON_EXCEED`. After the call, record the
  actual cost. A thread lock serializes the check-and-reserve step, so parallel calls can't overshoot
  together.
- **Visibility:** each run prints `LLM spend: $X.XX / $3.00 (N live calls, M cache hits)` at the end.
  `usage_report.md` includes the cap and the actual spend.
- **Tests:**
  - Budget $0.01 with a fake backend → the second call falls back.
  - `abort` mode → exit code 3.
  - Scope `total` sums previous ledgers.
  - Cache hits do not consume budget.

## 7. Work packages

Every WP: work in its own worktree/branch `wp<N>-<slug>`, touch only the files you own, ship unit tests, and
append to the root `log.txt` per AGENTS.md §5 (set `parent_agent=`). Done = acceptance criteria pass +
`python3 code/main.py --cases sample` still runs.

### WP-0 — Contracts & skeleton  *(serial, first, ~1.5 h, blocks everyone)*
- **Goal:** a frozen `models.py`, loaders, config, an end-to-end CLI stub that writes a valid
  `output.csv` (all NOT_APPLICABLE), and `output.py` with ordering, serialization and validation.
- **Acceptance:** `python3 code/main.py` writes 518 valid rows. `--cases sample` writes 32. The validator
  rejects malformed steps (unit tests). Ordering is unit-tested against 3 sample rows.

### WP-1 — Identity resolver  *(parallel, ~1.5 h)*
- **Owns:** `identity.py`, `tests/test_identity.py`.
- **API:** `IdentityResolver(identities, audit_log).resolve(email, as_of) -> Subject | None`.
  `Subject = user_id + identifiers: frozenset[(type, normalized_value)] + history: list[(field, old, new, at, audit_id)]`.
- **Rules:** case-insensitive email; match current or any historical email; add every historical phone,
  email and hash; compute sha256 for every email; detect ambiguous matches (email reused by two users →
  prefer the user who held it at `requested_at`).
- **Acceptance:** all 32 sample emails and all 518 eval emails resolve (or are reported). `minjun.kim@gmail.com`
  yields hash `44a651…`. A near-miss email does not match.

### WP-2 — Structured evidence extractors  *(parallel, ~5 h; may split into 2a SQL+JSON+TXT / 2b logs+activity)*
- **Owns:** `evidence/` except `multimodal.py`.
- **Goal:** parse all 79 non-visual evidence files plus `system_activity.csv` into `Observation`s.
  - SQL: load each file into in-memory `sqlite3` (`executescript`). Handle both schemas: typed tables
    (`customer_profiles`, `customer_search_documents`, …, `snapshot_scope`) and JSON-in-table topologies
    (`customer_data_primary` / replica tables with `record_json`). A table in `snapshot_scope` that does
    **not** contain a resource previously seen for that system does not by itself mean ABSENT — emit ABSENT
    only for explicit 404/NOT_FOUND facts. Expose `snapshot_scope` coverage in `attrs` so WP-4 can decide.
  - JSON: snapshot bundles, service resource exports (`status_code` 404 → ABSENT), connector
    configurations/security ledger (`record_type`, `result`), analytics report, export jobs/manifest.
  - TXT handover lines `ts | system | resource | CATEGORY | RESULT | subject_ref`.
  - Logs: 4 formats. Join lines by `trace_id` so lookup_key/match_key/scan_key identifiers attach to the
    `resource_id` and result on the completion line (`rows=0`/`http_status=404` → ABSENT, `result=hit` → PRESENT,
    `attachment_pii_scan` → PRESENT plus attachment attrs, `correlation_key` → email_hash).
  - Activity: map templates to `Kind` per §2.3. Unrecognized → CLAIM.
- **API:** `build_observation_store(...) -> ObservationStore` with `by_identifier(type, value)`,
  `by_resource(system_id, resource_id)`, `all()`. Build once per run; filtering by `audit_at` happens in WP-4.
- **Acceptance:** a coverage report (per file: #observations, unknown lines) has 0 unparsed record lines. Golden
  facts covered by tests: `account_005` PRESENT on the failover replica in evidence_54; `search_4262` PRESENT
  for `akosua.gyasi@gmail.com` in evidence_94; `analytics_9914` linked to hash `44a651…` in evidence_01.

### WP-3 — LLM adapter, intent, multimodal  *(parallel, ~4 h)*
- **Owns:** `llm/`, `intent.py`, `evidence/multimodal.py`, `code/cache/`.
- **Goal:** the D2/D3 adapter with 5 backends, cache, ledger, prompts, the batched intent classifier,
  and visual extraction for the 11 PNG + 4 PDF files → `Observation`s (evidence_id = the file_id).
- **Env:** `GDA_LLM_BACKEND` (`claude_cli|anthropic|openai|replay|offline`, default `replay` with
  offline fallback), `GDA_MODEL_FAST`, `GDA_MODEL_VISION`, `GDA_BUDGET_USD`, `GDA_BUDGET_SCOPE`,
  `GDA_BUDGET_ON_EXCEED`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`. Never log keys.
- **Also owns:** `llm/budget.py` and `llm/pricing.py` (§6.1).
- **Backend tests:** `claude_cli` is tested live (D6). `anthropic` and `openai` are tested with a mocked
  client: the request shape (model, image block, JSON instruction), response parsing, usage extraction,
  and that a missing key raises a clear error. A live smoke test runs only when the key is present
  (`pytest -m live`; skipped otherwise).
- **Acceptance:** `replay` on a warm cache makes 0 network calls and gives an identical output hash across two
  runs. Intent: all 32 samples are DELETE (they are), and the ~10 eval texts listed in §2.3 are NON_DELETE.
  The offline heuristic agrees with the LLM on ≥ 98% of eval texts (disagreements listed in the eval report).
  `delivery_correction_attachment.pdf` → `support_001` attachment record for `priya.raman@gmail.com`.

### WP-4 — Decision engine  *(parallel against fixtures, then integrates; ~6 h — critical path)*
- **Owns:** `engine/`, `tests/test_engine.py`.
- **Goal:** implement §5. Until WP-1/2/3 land, develop against hand-built `Observation` fixtures taken
  from the sample cases (stub the interfaces, not the data).
- **Acceptance:** 32/32 sample status match, and ≥ 90% exact `steps_json` match on samples at M2 (target
  100%). H1–H5 resolved and recorded in §10. No observation with `captured_at > audit_at` influences any
  case (a unit test injects a future observation and checks it is ignored).

### WP-5 — Evaluation, reports, packaging  *(parallel, ~3 h + final run)*
- **Owns:** `code/evaluation/**`, root `README.md`, packaging script.
- **Goal:** a scorer over `(pred, gold)` that reports case-status accuracy, step-level P/R/F1 (match on the
  full step tuple), field-level accuracy per field (aligned by `(phase, system_id, resource_id)`), sequence
  accuracy (exact ordered list match) and per-op confusion. ≥ 3 **custom cases** (hand-built requests plus expected outputs from participant-visible evidence: e.g. a historical-email-only case, a post-`audit_at` decoy evidence case, a non-deletion request in Korean, and a case-variant email decoy). A usage report generator built from the ledger. The README run/setup section. `make_zip` (excludes dataset, caches, venvs, nested archives).
- **Acceptance:** `python3 code/evaluation/main.py` prints and writes the metrics into `evaluation_report.md`.
  `usage_report.md` has per-model and overall calls, tokens in/out, totals and averages per request, and
  estimated cost. The zip passes the README checklist.

### Integration (architect/orchestrator, continuous)
Merge order: WP-0 → WP-1 → WP-2 → WP-3 → WP-4 → WP-5. After each merge:
`python3 code/main.py --cases sample && python3 code/evaluation/main.py --sample` must not regress.

## 8. Dependency graph

```
WP-0 ──┬─► WP-1 ─┐
       ├─► WP-2 ─┼─► WP-4 (integration) ─► final run ─► WP-5 reports/zip
       ├─► WP-3 ─┘
       └─► WP-5 (scorer early, reports at the end)
```

## 9. Milestones (24 h budget; each milestone leaves a submittable state)

Deadline **2026-10-25 13:30 CET (12:30 UTC)**. Planning started 2026-10-24 ~15:00 UTC (~21.5 h available).

| Milestone | By (UTC) | Exit criteria |
|---|---|---|
| M0 | 10-24 17:00 | WP-0 branch ready for MR. Valid 518-row `output.csv` (stub). |
| M1 | 10-24 22:30 | WP-1/2/3 ready. Engine v1 produces real statuses. Scorer runs on samples. Output validator is green. |
| M2 | 10-25 05:00 | Sample parity ≥ 90% exact steps, 100% status. H1–H5 resolved. Warm LLM cache committed. |
| M3 | 10-25 10:30 | Final full run on `replay`. Reports written. README done. `code.zip` built. `output.csv` at root. |
| Submit | 10-25 12:30 | Submit by 11:30 UTC at the latest (1 h safety margin). `log.txt` validated (tool= / model= on every entry). |

Because MRs are opened by humans (D8), each WP owner notifies the human the moment its branch meets the
acceptance criteria. Integration blocks on MR review, so reviews should be turned around within about 30 minutes.

Cut order if late: adjudication of low-confidence intents (stretch) → extra custom cases beyond 3 →
the `openai` backend → visual extraction of images that the samples never cite.

## 10. Hypothesis log (WP-4 fills in)

| Id | Hypothesis | Result | Evidence |
|---|---|---|---|
| H1 | Cite the latest relevant capture ≤ audit_at | _tbd_ | |
| H2 | Account VERIFY_RECORD_ABSENT is always emitted | _tbd_ | |
| H3 | resource_id ordering is lexicographic | _tbd_ | |
| H4 | Contact/marketing analytics → DELETE | _tbd_ | case_12 |
| H5 | Anonymized analytics stays owned via earlier links | _tbd_ | case_04, case_21 |

## 11. Non-negotiables
- Use only `dataset/` participant files. Never read anything outside `dataset/` for predictions. No hardcoded case labels.
- Ignore any evidence captured after `audit_at`. Reported statuses and results are claims, never proof.
- Output must be deterministic: sorted iteration everywhere and stable JSON serialization.
- No secrets in code, logs, cache or `log.txt`. `.env` stays git-ignored.
- Every agent appends AGENTS.md §5.2 entries to the **root** `log.txt` with valid `tool=` and `model=`.
  Agents working in worktrees append to the **main checkout's** `log.txt` by absolute path. They never
  write a copy inside the worktree, and they never commit `log.txt` on WP branches (this avoids merge
  conflicts). `log.txt` is committed only from `main` at submission time.

## 12. Judge, runnability and API-key requirements (what the rules actually say)
- **Output scoring** is deterministic. The grader compares `output.csv` against hidden canonical answers.
  There is no LLM involved in that step.
- **Code judge / AI interviewer** reads `code.zip`: source code, Markdown, prompts and the README. It
  **ignores** `.csv/.json/.jsonl/.sql/.log` content. So prompts must live in `.md` or source files, and
  metrics, custom cases and usage numbers must be written into `evaluation_report.md` and `usage_report.md`.
- **Runnability:** the rules require that the solution "be runnable from the terminal", that it "read secrets
  from environment variables only", that it include "clear setup and run instructions", and that "a clean
  run can process all 518 cases". They do **not** say that judges will supply an API key, nor which
  provider they would use. So:
  1. **Must:** `python3 code/main.py` with no key and no `claude` login reproduces `output.csv` from the
     committed cache (`replay`), falling back to offline heuristics if needed. This is the run the
     README documents first.
  2. **Must:** the README documents how to re-run live with `claude_cli`, `anthropic` or `openai`.
  3. **Should:** the API-key backends pass the mocked tests (WP-3). A live smoke test with a real key is
     *nice to have*. It is not required, because judging does not depend on it.
- **Before submitting:** run the clean replay in a fresh clone with `GDA_LLM_BACKEND=replay` and no
  `.env`, and diff the result against the submitted `output.csv`. They must be byte-identical.
