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
