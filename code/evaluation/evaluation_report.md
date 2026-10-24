# Evaluation Report — Ghost Data Still Exists

Reproduce: `python3 code/evaluation/main.py --write` (add `-v` to list every mismatching step).

The agent is scored against the 32 public samples (`dataset/sample_output.csv`) and against hand-built
scenario cases. The samples are audited inside the same run as the 518 evaluation requests,
because deletion-worker activity is assigned across all requests at once. `sample_output.csv` is
read only by this scorer, never on the prediction path.

The samples fall into two families, reported separately:

- `case_01`–`case_10`: handwritten, one evidence artifact per case (replica dump, PDF, log, export job). Several eval subjects behind the screenshots and PDFs follow this style.
- `case_11`–`case_32`: generated, multi-system cases citing shared evidence bundles, which is how most eval cases look.

## Metric definitions

| Metric | Definition |
|---|---|
| Status acc. | Share of cases whose `deletion_status` matches. |
| Step P / R / F1 | Multiset match of whole steps `(phase, system_id, resource_id, operation, state, evidence_id)`. |
| Field accuracy | Steps aligned by position (step *i* vs step *i*); share of positions where the field is equal. A missing or extra step counts as a miss for every field. |
| Exact sequence | Share of cases whose ordered step list is identical. |
| Mean LCS | Longest common subsequence of ordered step tuples ÷ the longer list, averaged per case. This scores ordering while tolerating a single missing step. |

## Results

These are scores from the submitted offline run: intent by the keyword classifier, screenshots not read, no model calls. See `usage_report.md`.

<!-- metrics:start -->
| Slice | Cases | Status acc. | Step P | Step R | Step F1 | Exact sequence | Mean LCS |
|---|---|---|---|---|---|---|---|
| All samples | 32 | 100.0% | 98.4% | 97.4% | 97.9% | 78.1% | 96.0% |
| case_01–10 (single-artifact) | 10 | 100.0% | 96.8% | 96.8% | 96.8% | 80.0% | 93.3% |
| case_11–32 (generated) | 22 | 100.0% | 98.8% | 97.5% | 98.1% | 77.3% | 97.2% |
| Custom scenarios (5) | 5 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |

| Field (position-aligned) | All samples | case_01–10 (single-artifact) | case_11–32 (generated) | Custom scenarios (5) |
|---|---|---|---|---|
| `step_no` | 96.9% | 93.8% | 97.5% | 100.0% |
| `phase` | 96.9% | 93.8% | 97.5% | 100.0% |
| `system_id` | 92.8% | 87.5% | 93.9% | 100.0% |
| `resource_id` | 92.3% | 87.5% | 93.3% | 100.0% |
| `operation` | 92.8% | 87.5% | 93.9% | 100.0% |
| `state` | 92.8% | 87.5% | 93.9% | 100.0% |
| `evidence_id` | 96.4% | 90.6% | 97.5% | 100.0% |
<!-- metrics:end -->

## Custom evaluation cases

`code/evaluation/custom_cases.py` builds each scenario as a miniature dataset folder: identities, audit log, activity, manifest and evidence files. It then runs the real `DeletionAuditor` on it. Expected outputs were written by hand from the rules. They also run as pytest cases.

| Case | Scenario | What it proves |
|---|---|---|
| `custom_01` | Korean request for an order-history copy that says "do not delete the account" | Intent handles non-English negation → `NOT_APPLICABLE`, `[]`. |
| `custom_02` | Account answers 404 before `audit_at`; a bundle captured *after* `audit_at` shows it recreated | Later evidence neither changes the answer nor gets cited → `COMPLETE`, citing the pre-audit file. |
| `custom_03` | Request uses the current email; the only surviving row is in a read replica under the *historical* email | Identity history + replica drift → `REPORT_REMAINING_RECORD` from the replica. |
| `custom_04` | Tombstone still shows name and email; a backup can still restore the profile; a security audit record must be retained | Tombstone redaction, restore risk, remediation order (`BLOCK_RESTORE` before `REDACT_RECORD`), retained-record verification. |
| `custom_05` | A search document for a near-identical decoy email (`j.moreau.jr@…`) belonging to someone else | Exact-identifier linking ignores look-alikes → no false finding. |

## Failures found and improvements made

Each change was measured on the samples with the scorer. Linking figures count (system, resource) pairs against the expected resource sets.

| # | Failure observed | Change | Effect |
|---|---|---|---|
| 1 | Baseline linking by exact identifiers only | Tier 1 (records, trace-joined lookup keys, data-catalog links) | 72 TP / 0 FP / 39 FN: perfect precision, but recall too low. |
| 2 | Retained audit records referenced only in bundles captured *after* `audit_at` were missing | Linking may use later files within the subject's lifecycle; **state and citations still only use files ≤ `audit_at`** | +7 TP, 0 FP. |
| 3 | The same person files several requests (`user_07`, `user_17`, `user_18`), so earlier lifecycles leaked in | Lifecycle windows: each request owns evidence after the previous request's `audit_at` | `object_19` and `tombstone_17` stay with February's and March's cases (regression tests). |
| 4 | Deleted accounts (404, no key) and anonymised analytics carry no identifier | Tier 2: assign the deletion worker's trail; naive nearest-time | +10 TP but +13 FP (swaps between cases sharing a `reported_at` slot). |
| 5 | Slot-mates swapped accounts | The account `DELETE_RECORD` result mirrors the case's `reported_status` claim (SUCCESS↔COMPLETE, PARTIAL↔PARTIALLY_COMPLETE, FAILED↔NOT_STARTED). Most-constrained-first assignment uses the case's own downstream events (15–175 min after the account) as feasibility. | FP 13 → 7. |
| 6 | Trail events went to eval slot-mates from unrelated files | Prefer the case whose own resources appear in the same evidence files (co-location) | Status accuracy 93.8% → 96.9%. |
| 7 | Remaining same-slot ties | Request-text hints ("integration still active", "restore my profile") and a stable slot order (earlier event ↔ lower case number) | Status 100%, step F1 96.3% → 96.8%. |
| 8 | Handwritten case with no account activity lost its analytics record | Virtual trail anchor at `reported_at` − 1 h | Step F1 97.1%. |
| 9 | Backup log lines say `eligible=true deletion_block=false`, not `restore_allowed` | Parse eligibility; an unblocked backup that holds the subject's data counts as a restore risk | `case_17` correctly INCOMPLETE. |
| 10 | Offline intent fallback missed French "suppression" and flagged "remove any remaining copy of my profile" | Tightened multilingual patterns; Claude remains the primary classifier | Fallback flags exactly the 10 eval non-deletion requests. |
| 11 | COMPLETE case whose only resource is evidenced by a screenshot ended with no steps when offline | Fall back to citing the latest participant-visible activity; the live run reads the screenshot | Output always satisfies the contract. |
| 12 | Treating unknown restore flags as risk wrongly flipped `case_265`, whose log says `restore_authorization_completed decision="deny"` | Parse authorization decisions; audit that no backup observation is left with an unknown flag | `case_265` back to COMPLETE; 0 unknown backup states remain. |
| 13 | A scan of every log key carrying an identifier found four unrecognized link fields: `subject_ref` (92), `subject_key` (60), `owner_key` (6), `scan_key` (5) | Treat them as link keys (`subject_ref` also as a stable reference) | Step recall 95.9% → 97.4%, F1 97.1% → 97.9%; 64 eval rows changed. The same scan over JSON/SQL finds nothing unrecognized. |

### How much the slot tie-break flatters the samples

Ties inside a shared `reported_at` slot go to the lower case number first, and every sample number (01–32) sorts before every eval number (33+). Re-scoring with samples forced to sort *after* eval cases gives step F1 **97.4%** instead of 97.9%, with status still 100%. The rule is kept, but eval slot-tie accuracy is closer to the lower figure.

### Known limits (residual sample misses)

- Four retained audit records (`case_15`, `case_18`, `case_19`, `case_20`) appear in no evidence with any identifier, and have no activity rows. They cannot be linked without guessing.
- Ties within a shared `reported_at` slot for identifier-less resources, e.g. `account_410` vs `account_7015` (both FAILED) and three analytics candidates in one slot. The data holds no distinguishing signal, so a deterministic rule decides.
- `case_04` uses `account_004`, which has no activity row and no evidence.
