# Evaluation report

## 1. Sample regression (public labelled cases)

Pipeline run on `sample_deletion_requests.csv` and scored against `sample_output.csv`. Dev = case_01–22 (used while building), holdout = case_23–32 (looked at only at milestones).

| Split | Cases | Status acc. | Step P | Step R | Step F1 | Sequence acc. | Citation acc. |
|---|---|---|---|---|---|---|---|
| all | 32 | 100.0% | 97.8% | 94.3% | 96.0% | 68.8% | 97.3% |
| dev | 22 | 100.0% | 96.6% | 94.2% | 95.4% | 72.7% | 94.7% |
| holdout | 10 | 100.0% | 100.0% | 94.4% | 97.1% | 60.0% | 100.0% |

Field-level accuracy (gold steps aligned on `(phase, system_id, resource_id)`):

| Field | phase | system_id | resource_id | operation | state | evidence_id |
|---|---|---|---|---|---|---|
| all | 94.3% | 94.3% | 94.3% | 94.3% | 94.3% | 94.3% |

Per-operation alignment (gold operation -> predicted operation at the same resource/phase):

| gold -> predicted | count |
|---|---|
| <none> -> VERIFY_RECORD_ABSENT | 4 |
| ANONYMIZE_RECORD -> ANONYMIZE_RECORD | 5 |
| BLOCK_RESTORE -> BLOCK_RESTORE | 3 |
| DELETE_RECORD -> DELETE_RECORD | 21 |
| DISABLE_DATA_FLOW -> DISABLE_DATA_FLOW | 6 |
| REDACT_RECORD -> REDACT_RECORD | 6 |
| REPORT_ACTIVE_DATA_FLOW -> REPORT_ACTIVE_DATA_FLOW | 6 |
| REPORT_EXPOSED_PII -> REPORT_EXPOSED_PII | 11 |
| REPORT_REMAINING_RECORD -> REPORT_REMAINING_RECORD | 21 |
| REPORT_RESTORE_RISK -> REPORT_RESTORE_RISK | 3 |
| VERIFY_DATA_FLOW_DISABLED -> VERIFY_DATA_FLOW_DISABLED | 9 |
| VERIFY_RECORD_ABSENT -> <missing> | 3 |
| VERIFY_RECORD_ABSENT -> VERIFY_RECORD_ABSENT | 47 |
| VERIFY_RECORD_ANONYMIZED -> <missing> | 1 |
| VERIFY_RECORD_ANONYMIZED -> VERIFY_RECORD_ANONYMIZED | 11 |
| VERIFY_RECORD_PRESENT -> <missing> | 7 |
| VERIFY_RECORD_PRESENT -> VERIFY_RECORD_PRESENT | 17 |
| VERIFY_RECORD_REDACTED -> VERIFY_RECORD_REDACTED | 10 |
| VERIFY_RESTORE_BLOCKED -> VERIFY_RESTORE_BLOCKED | 6 |

Cases whose step list is not an exact match (10):

- `case_04` status COMPLETE (gold COMPLETE); missing `account_004`, `analytics_901`; extra `account_3865`
- `case_08` status INCOMPLETE (gold INCOMPLETE); missing `account_008`; extra `account_5482`, `support_2351`
- `case_09` status INCOMPLETE (gold INCOMPLETE); missing `audit_record_4902`; extra –
- `case_10` status INCOMPLETE (gold INCOMPLETE); missing `account_010`; extra `account_007`
- `case_18` status INCOMPLETE (gold INCOMPLETE); missing `audit_record_6645`; extra –
- `case_20` status INCOMPLETE (gold INCOMPLETE); missing `audit_record_5569`; extra –
- `case_23` status INCOMPLETE (gold INCOMPLETE); missing `audit_record_4128`; extra –
- `case_24` status INCOMPLETE (gold INCOMPLETE); missing `audit_record_7095`; extra –
- `case_29` status INCOMPLETE (gold INCOMPLETE); missing `audit_record_3954`; extra –
- `case_30` status INCOMPLETE (gold INCOMPLETE); missing `audit_record_1707`; extra –

## 2. Intent classification

Labelled set: `code/evaluation/intent_cases.md` (62 own multilingual texts) + 32 public samples.

| Classifier | n | Accuracy | NON_DELETE precision | NON_DELETE recall |
|---|---|---|---|---|
| llm | 94 | 100.0% | 100.0% | 100.0% |
| heuristic | 94 | 93.6% | 86.5% | 97.0% |

Confusion (expected->predicted): **llm** DELETE->DELETE=61, NON_DELETE->NON_DELETE=33; **heuristic** DELETE->DELETE=56, DELETE->NON_DELETE=5, NON_DELETE->DELETE=1, NON_DELETE->NON_DELETE=32

Misclassified texts:

| id | lang | trap | expected | llm | heuristic |
|---|---|---|---|---|---|
| i03 | en | no verb, outcome | DELETE | DELETE | NON_DELETE |
| i07 | en | polite, indirect | DELETE | DELETE | NON_DELETE |
| i23 | es | plain | DELETE | DELETE | NON_DELETE |
| i24 | es | borrar | DELETE | DELETE | NON_DELETE |
| i53 | es | close | DELETE | DELETE | NON_DELETE |
| i58 | en | review deletion only | NON_DELETE | NON_DELETE | DELETE |

Evaluation texts: 375 unique, 375 labelled by the LLM; LLM/heuristic agreement **100.0%**, 0 disagreement(s). NOT_APPLICABLE cases: case_533, case_534, case_535, case_536, case_537, case_538, case_539, case_540, case_541, case_542.

## 3. Custom end-to-end evaluation cases

15/15 hand-labelled scenarios pass. Each writes a miniature dataset in the real file formats, runs `main.py` as a subprocess and compares every output step (code: `code/tests/e2e/`).

| # | Scenario | Expected | Result |
|---|---|---|---|
| 1 | `historical_email_only` | case_01: INCOMPLETE | pass |
| 2 | `near_miss_decoys` | case_02: COMPLETE | pass |
| 3 | `evidence_after_audit_is_ignored` | case_03: COMPLETE | pass |
| 4 | `korean_non_deletion_request` | case_04: NOT_APPLICABLE | pass |
| 5 | `success_claim_is_not_proof` | case_05: INCOMPLETE | pass |
| 6 | `failover_replica_drift` | case_06: INCOMPLETE | pass |
| 7 | `connector_recreates_deleted_entry` | case_07: INCOMPLETE | pass |
| 8 | `restorable_backup` | case_08: INCOMPLETE | pass |
| 9 | `tombstones_exposed_and_clean` | case_09: INCOMPLETE, case_10: COMPLETE | pass |
| 10 | `support_message_vs_attachment` | case_11: INCOMPLETE | pass |
| 11 | `retained_audit_and_legal_hold` | case_12: COMPLETE | pass |
| 12 | `analytics_profile_vs_marketing_contact` | case_13: INCOMPLETE | pass |
| 13 | `complete_cites_latest_evidence` | case_14: COMPLETE | pass |
| 14 | `identifiers_joined_across_trace_lines` | case_15: INCOMPLETE | pass |
| 15 | `unknown_identity_uses_bare_email` | case_16: INCOMPLETE | pass |

## 4. Invariants on the 518-case run (no labels needed)

| Check | Result |
|---|---|
| contract | pass |
| citations | pass |
| policy_sanity | pass |
| determinism | pass |
| identifier_invariance | pass |
| time_travel | pass |
| time-travel cases re-run with later evidence physically removed | 51 |

## 5. Failures found and improvements made

Each row is a failure a test layer caught during development, in the order it happened, and the change that fixed it.
Sample numbers are on all 32 public cases (status accuracy / exact sequence accuracy).

| # | Caught by | Failure | Fix | Effect |
|---|---|---|---|---|
| 1 | Sample regression | First end-to-end run: 94% status, 41% sequence. Absence inferred from *any* later snapshot that lacked a record overrode an explicit PRESENT row (case_05's profile left on a failover replica was reported deleted because a later, unrelated inventory had no rows). | Absence inferred from a full snapshot is now only a fallback when nothing explicit ever observed the record. | case_05, case_24 fixed |
| 2 | Sample regression | Workflow steps were attached to the wrong person: any deletion-worker step whose 30-second phase matched was accepted. | Learned *per step class* cadences ("Policy action completed" steps at phase 9–11 s, "final result"/check steps at 20–22 s) from workflows whose account and steps are both identifier-linked. | precision 0.81 → 0.90 |
| 3 | Sample regression | Profile deletions were assigned to cases arbitrarily inside overlapping request windows (case_21/25/27/29 swapped accounts). | Account inherits the owner of its chain's identifiable steps; learned "deletion precedes reported_at by 46–74 min" prior; minute-phase of the first identifiable step; global one-to-one assignment, most decisive first. | sequence 44% → 62% |
| 4 | Sample regression | Failed workflow steps (search entries of *other* people) linked by timing alone (case_18, case_32). | Only successful policy actions may be linked by timing alone: a failed step leaves identifiers behind and must link by identifier. | status 97% → 100% |
| 5 | Trace review (L6) | 10 retained audit records reported ABSENT: a `cache_lookup result="miss"` line in the same log trace overwrote the readback's `rows=1`. | Lines without a resource id contribute only lookup keys to the trace merge. | regression test `split_trace_log` scenario |
| 6 | Invariants (contract) | case_61 produced COMPLETE with no steps (invalid row): the subject's only footprint is an attachment shown in an object-storage screenshot by file name. | Second visual pass that transcribes file listings; data-catalog LINK activities map `object_19 (delivery_confirmation_19.pdf)`; defined fallback for subjects with no observable footprint. | 0 contract errors |
| 7 | Invariants (time travel) | 6/51 rows changed when later evidence was physically removed: the workflow cadence was re-learned from the truncated data, and workflow elimination used other cases' later evidence. | Workflow assignment is computed per audit cutoff with only evidence available then; the learned workflow cadence is a frozen artifact (`code/cache/workflow_model.json`) describing the deletion worker, not any person. | 0 time-travel failures |
| 8 | Sample regression | An attachment last seen 199 days before case_07's request (it belongs to the same person's earlier February request) was attached to the September request. | Records last seen more than 90 days before the request are treated as an earlier lifecycle. | precision 0.963 → 0.978 |
| 9 | Intent eval (L2) | The LLM labelled sample case_27 ("please audit the deletion … confirm the account was fully removed") NON_DELETE when batched with the own intent set. | Prompt states that requests to audit/confirm a removal reaffirm an erasure request. | LLM accuracy 98.9% → 100% on 94 texts |
| 10 | Unit test | The budget allowed one more zero-estimate call after the cap was reached exactly. | `reserve` refuses when committed spend already equals the cap. | budget tests green |

## 6. Known limitations

- Seven sample verification steps cite security audit records that appear in no participant evidence captured before
  `audit_at` (some only in a snapshot captured two days after the audit). The engine never uses post-audit evidence, so
  these steps are unreachable by design; they account for most of the remaining sample sequence gap.
- Four hand-written sample cases (case_02/04/07/08/10) have accounts whose deletion activity sits outside the reported
  window or has no activity at all; the generic workflow linker sometimes picks a neighbouring account for them.
- When several requests share one reporting window and no identifier links any of their accounts, the assignment of
  deletion workflows to requests is resolved by elimination and can swap two accounts. Status is unaffected; only the
  account `resource_id` of a verification step changes.

## 7. How to reproduce

```bash
python3 code/main.py                       # 518 rows -> output.csv (replay cache, no API key)
python3 code/evaluation/main.py            # regenerates this report and usage_report.md
python3 code/evaluation/check_all.py       # gate: unit + e2e tests, sample regression, invariants
```
