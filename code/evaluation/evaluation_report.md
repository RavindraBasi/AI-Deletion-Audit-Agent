# Evaluation Report: Ghost Data Still Exists

All numbers come from `python3 code/evaluation/main.py` (32 public samples) and
`python3 code/evaluation/custom_cases.py` (5 team-authored cases). Both run the real agent end to end.

## 1. Public samples (32 cases, `dataset/sample_output.csv`)

| Metric | Value |
| --- | --- |
| Case-level deletion-status accuracy | **1.000** (32/32) |
| Exact case match (whole `steps_json` identical) | 0.875 (28/32) |
| Step precision / recall / F1 (all 6 fields incl. evidence) | 0.995 / 0.979 / **0.987** |
| Step F1 ignoring `evidence_id` | 0.987 |
| Sequence accuracy: normalised LCS of (phase, resource, operation) | **0.974** |
| Sequence accuracy: identical sequence | 0.875 |
| Resource recall (gold system/resource pairs discovered) | 0.964 |

Field-level accuracy, aligned by step position against the gold steps:

| phase | system_id | resource_id | operation | state | evidence_id |
| --- | --- | --- | --- | --- | --- |
| 0.984 | 0.959 | 0.953 | 0.959 | 0.959 | 0.979 |

Status confusion: COMPLETE→COMPLETE 10, INCOMPLETE→INCOMPLETE 22, no cross-errors.

### Remaining sample misses

- **case_18 and case_20.** The gold verification includes `audit_record_6645` and `audit_record_5569`. Neither id appears anywhere in the participant data (no evidence file, no activity row), so it cannot be recovered.
- **case_04.** The gold account `account_004` has no activity or evidence row in the participant data.
- **case_23.** Three requests in one workflow batch report the same status (NOT_STARTED), and there are three FAILED primary-profile deletes. Nothing in the data distinguishes which delete belongs to which request. The agent breaks the tie by case order and picks the wrong one for this case.

## 2. Team-authored evaluation cases (`code/evaluation/custom_cases.py`)

The script builds a miniature fictional dataset in a temp directory, runs `code/main.py` on it, and scores the result.

| Case | What it tests | Expected |
| --- | --- | --- |
| case_901 | Negated wording: "information request, not a request to erase it" | `NOT_APPLICABLE`, `[]` |
| case_902 | Request sent from a **historical** email. The search document is keyed by the old address in an SQL inventory. An analytics contact row is keyed only by the **sha256 of the old address**, so it is a secondary identifier and must be deleted, not anonymised | INCOMPLETE, 2 findings, 2 deletes, 4 checks |
| case_903 | A capture taken **after `audit_at`** shows a connector re-enabled. It must be ignored, and the answer must cite the earlier clean capture | COMPLETE, 3 verifications citing evidence_01 |
| case_904 | **Replication drift.** The primary is clean but the read replica still holds the profile. The tombstone still exposes name, email and phone | INCOMPLETE: account REMAINING plus tombstone REDACT |
| case_905 | A de-identified analytics row with no identifiers, attributable only through the deletion-workflow batch | COMPLETE with ANONYMIZED check |

Result: status accuracy 1.000, exact match 1.000 (5/5), step F1 1.000, sequence 1.000.

Writing case_905 exposed a gap. The first fixture placed the only evidence after `audit_at`, and the agent produced a COMPLETE case with empty evidence ids. `code/evaluation/validate.py` now flags this in any output.

## 3. Failures found and improvements made (sample metrics after each change)

| # | Change | Exact match | Step F1 |
| --- | --- | --- | --- |
| 0 | First end-to-end version: identifier linking, latest-state-before-audit, rule mapping, greedy batch attribution by case order | 0.531 | 0.933 |
| 1 | **Episode scoping.** One email can have several requests (for example case_17 in August and case_46 in March). Resources are attached to the request whose cut-off follows their first appearance. This removed a March account and tombstone from an August case | | |
| 2 | **Row-aware OCR.** Screenshots list several resources. Identifiers are now bound to the resource on the same visual row (tesseract TSV coordinates) instead of to the whole image, which removed a false data-flow finding in case_15 | | |
| 3 | **Account attribution by workflow outcome.** The deletion worker's SUCCESS, PARTIAL or FAILED result on the primary profile mirrors the reported status. Matching on it, inside a tight window before the status report, fixed case_21, case_22, case_23 and case_25 | 0.812 | 0.977 |
| 4 | **Prefer clean requests for de-identified records.** Anonymised analytics rows and 404 profiles go to requests whose identifier-linked records are already compliant, and must share the same capture. This stopped analytics rows being attached to INCOMPLETE requests (case_09, case_20, case_24) | | |
| 5 | **Two-pass account matching.** Strict status match first, then a relaxed pass only for requests still without an account. This fixed the hand-written samples (case_06, case_08) without letting decoy accounts steal slots | 0.875 | 0.987 |
| 6 | **Dark-theme OCR.** Grayscale, invert, then upscale 2x before tesseract. The security audit explorer went from 4 to all 6 audit ids recovered | (samples unchanged) | |
| 7 | **Catalog aliases.** Data-catalog events name a business alias (`delivery_confirmation_19.pdf`, `NST-2026-0718-1842`, `user_17`). Normalised alias matching, which tolerates OCR damage such as `.pdt`, links screenshot and PDF rows to `object_19`, `export_1842` and `tombstone_17` | (eval only) | |
| 8 | **Ignore post-cut-off episodes.** Resources first seen after every audit cut-off of that person are not part of any request ("evidence created later must not change the answer") | (eval only) | |

## 4. Known limitations

- **Batch ties.** When several requests in one workflow batch report the same status, de-identified records are assigned by case order. Nothing in the data breaks the tie.
- **Invisible retained records.** Some retained audit records that the gold expects are not present in any participant file, so they are never emitted.
- **Screenshots.** Restore and flow status are inferred from row text: "Continuous" means active, and an archive listed in a recovery console with no block marker counts as restorable. Setting `ANTHROPIC_API_KEY` adds an LLM transcription pass for these screens.
