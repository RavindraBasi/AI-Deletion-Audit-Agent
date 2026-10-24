# Evaluation Report — Ghost Data Still Exists

**Run date:** 2026-10-24 (UTC)

## 1. Full evaluation-dataset run

The final engine processed `dataset/deletion_requests.csv` and wrote the root-level `output.csv`.

| Check | Result |
|---|---:|
| Input requests | 518 |
| Output data rows | 518 |
| Required header/order | PASS — `case_id,deletion_status,steps_json` |
| Case IDs unique and sorted | PASS |
| JSON arrays parse | PASS |
| Step object schema | PASS — exactly 7 required fields |
| Step numbering | PASS — sequential from 1 per case |
| Phase/operation ordering | PASS |
| Evidence reference validity | PASS — references resolve to participant-facing evidence/activity/audit IDs |
| `NOT_APPLICABLE` rows have `[]` | PASS |
| `COMPLETE` rows contain verification steps only | PASS |
| `INCOMPLETE` findings have evidence; planned steps have blank evidence IDs | PASS |
| Overall output contract validator | **PASS — 0 errors** |

### Predicted status distribution

| Status | Rows |
|---|---:|
| `INCOMPLETE` | 368 |
| `COMPLETE` | 141 |
| `NOT_APPLICABLE` | 9 |
| **Total** | **518** |

These are model predictions, not accuracy measurements. The canonical labels for the 518 evaluation requests are not available to the participant, so hidden-set accuracy cannot be calculated honestly. Twelve deletion-authorized cases have no safely linkable resource-level evidence at or before `audit_at`; they are conservatively marked `INCOMPLETE` with an empty step list rather than inventing resource IDs or unsupported actions. This is a known score risk and should be investigated if additional participant-visible identity-link evidence is discovered.

## 2. Public 32-case sample benchmark

The engine was run against `sample_deletion_requests.csv` and compared with `sample_output.csv`. The benchmark is a development check only; it is separate from the 518-row evaluation output.

| Metric | Result |
|---|---:|
| Status accuracy | 29/32 = **90.62%** |
| Exact full-row accuracy (status + exact ordered `steps_json`) | 8/32 = **25.00%** |
| Ordered action-sequence accuracy | 9/32 = **28.12%** |
| Position-aligned field accuracy | **63.30%** |
| Gold steps | 193 |
| Predicted steps | 159 |
| Exact-step precision | **79.25%** |
| Exact-step recall | **65.28%** |
| Exact-step F1 | **71.59%** |

**Metric definitions:**

- *Status accuracy* compares only `deletion_status`.
- *Exact full-row accuracy* requires the status and the complete ordered step objects, including evidence IDs, to match exactly.
- *Ordered action-sequence accuracy* compares the ordered sequence of phase, operation, system, resource, and state.
- *Position-aligned field accuracy* compares all seven required fields at each step position; missing or extra positions count as mismatches.
- *Exact-step precision/recall/F1* compare step tuples (phase, system, resource, operation, state, evidence ID) as a multiset, independent of step position.

The public benchmark shows that status classification is stronger than exact step reconstruction. The remaining step mismatches primarily concern resource linkage, choosing among multiple valid evidence artifacts, and deciding which resource observations belong to a subject at the cutoff. The engine deliberately avoids treating timestamp proximity alone as proof that a resource belongs to a subject. The public sample should not be interpreted as an estimate of hidden-set accuracy.

## 3. Participant-created evaluation cases

The lightweight test suite in `code/evaluation/main.py` includes these additional cases:

1. **Explicit deletion:** “Please permanently delete my account and personal data.” Expected: deletion is authorized.
2. **Deletion synonym:** “Please erase the account linked to this address.” Expected: deletion is authorized.
3. **Marketing opt-out:** “Please stop the promotional emails. I am not asking for deletion.” Expected: `NOT_APPLICABLE` intent.
4. **Correction-only request:** “My surname is wrong. Please correct my personal data without removing records.” Expected: `NOT_APPLICABLE` intent.
5. **Access/copy request:** “Please send me a copy of the information held on my account; do not delete anything.” Expected: `NOT_APPLICABLE` intent.
6. **Verification of deletion:** “Please verify that my account has been removed.” Expected: deletion-related audit is in scope.
7. **Finding order:** Remaining-record finding sorts before active-data-flow finding.
8. **Verification phase:** Verification steps remain in the verification phase and follow the declared operation ordering.

**Latest result:** 8/8 custom tests passed. The output-contract validator also passed against all 518 rows.

## 4. Failure analysis and improvements made

- **Intent false positives:** Generic wording such as “apply the correct treatment” was initially confused with a data-correction request. The classifier was narrowed to explicit correction/access/marketing-opt-out wording and explicit negative deletion intent.
- **Over-linking from nearby log lines:** A first parser version treated adjacent log entries as if they belonged to the same subject. Matching was tightened to direct identifiers and explicit resource-ID relationships.
- **Over-linking by timestamp alone:** A broad activity-event time window linked unrelated resources. The broad cluster was removed; identity links are now accepted when participant-facing activity details explicitly name the subject.
- **SQL omission interpreted too broadly:** Absence inferred from SQL snapshots was restricted to account-service snapshots rather than assuming every missing export/search/support row was deleted.
- **Screenshot state spread across panels:** Image parsing was improved to use the whole OCR result for the already-identified resource, while avoiding attaching unrelated resources from the same screenshot.
- **Output validation:** Added checks for exact header, row count, sorted case IDs, allowed statuses, exact step fields, step numbering, phase/operation ordering, evidence-ID validity, and status-specific evidence rules.

## 5. Known limitations / next improvements

1. The solution is deterministic and does not use an LLM for ambiguous evidence extraction; unusual or low-quality OCR can still lose detail.
2. Resource linking is evidence-driven, but some ambiguous relationships require stronger cross-source provenance to achieve exact step-sequence parity.
3. The public sample benchmark's exact full-row score is only 21.88%; further work should prioritize case-by-case mismatch analysis and evidence-source selection rather than optimizing only status accuracy.
4. Hidden-set accuracy is unknown until the official evaluator returns scores.

No organizer-only answer keys, hidden labels, or generator internals were used.
