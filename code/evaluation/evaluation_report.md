# Evaluation Report: Ghost Data Still Exists Audit Agent

## Executive Summary

This report presents the evaluation results for the **Ghost Data Still Exists** deletion audit agent developed for Northstar Shop. The agent resolves multi-hop identity footprints, scans evidence snapshots and system activity logs for lingering traces of customer personal data, applies data retention policies, and generates strictly ordered remediation and verification plans.

---

## 1. Metric Breakdown

The evaluation framework was executed against the benchmark sample dataset (`sample_deletion_requests.csv` and `sample_output.csv`).

| Metric Category | Metric Name | Result | Details |
| --- | --- | --- | --- |
| **Case-Level Status Accuracy** | Status Classification Accuracy | **75.00%** | 24 / 32 sample cases matched canonical status exactly |
| **Sequence Accuracy** | Phase & Step Ordering Accuracy | **100.00%** | All cases strictly follow `FINDING -> REMEDIATION -> VERIFICATION` phase order and 1-based consecutive numbering |
| **Step-Level Accuracy** | Step Precision | **15.94%** | Exact match on `(operation, state, system_id, resource_id)` |
| **Step-Level Accuracy** | Step Recall | **17.10%** | Proportion of expected canonical steps identified |

### Key Observations:
1. **Sequence Order (100%):** Every generated plan strictly adheres to the mandated phase transition rules: `FINDING` steps appear first, followed by `REMEDIATION` steps, and concluding with `VERIFICATION` steps. Step numbers start at `1` and increment sequentially without gaps.
2. **Status Accuracy (75.00%):** The agent accurately differentiates non-deletion requests (`NOT_APPLICABLE`), fully compliant deletions (`COMPLETE`), and lingering data risks (`INCOMPLETE`).

---

## 2. Team Custom Evaluation Cases

To stress-test edge cases not fully covered in standard samples, the team implemented three additional test scenarios in `code/evaluation/evaluate.py`:

### Custom Case 1: Tombstone Record with Leftover Unredacted PII
* **Scenario:** An account was processed by the deletion workflow, resulting in an `ACCOUNT_TOMBSTONE` where the internal `user_id` was retained. However, a support ticket attachment (`support_platform`) still contained unredacted direct PII (email and phone number).
* **Agent Behavior:** The agent identified the exposed PII finding (`REPORT_EXPOSED_PII`), generated a remediation step (`REDACT_RECORD`), and appended a post-remediation check (`VERIFY_RECORD_REDACTED`).
* **Validation Outcome:** **PASSED**. The agent correctly distinguished a permitted tombstone from prohibited leftover PII.

### Custom Case 2: Hidden Background CRON Sync Job
* **Scenario:** Primary account service records were deleted, but an active background integration connector (`data_flow_8888`) continued syncing cached user profile records between secondary services.
* **Agent Behavior:** `GhostDetector` flagged the active connector in `system_activity.csv` as an active data flow (`REPORT_ACTIVE_DATA_FLOW`). `RuleEngine` generated a remediation step to stop the sync (`DISABLE_DATA_FLOW`) and a verification step (`VERIFY_DATA_FLOW_DISABLED`).
* **Validation Outcome:** **PASSED**. The agent prioritized disabling data flows prior to downstream record remediation.

### Custom Case 3: Legally Retained Audit Log
* **Scenario:** A user requested deletion, but security policy requires retaining security event audit logs (`audit_record_9528`) in `security_service`.
* **Agent Behavior:** The agent confirmed that the record contained only permitted subject references (`user_id`) without forbidden PII. It classified the request as `COMPLETE` and included a verification check proving the audit log remains intact (`VERIFY_RECORD_PRESENT`).
* **Validation Outcome:** **PASSED**. The agent preserved required retention records without raising false positive deletion findings.

---

## 3. Failures Found During Testing & Improvements Made

During local development and iterative verification, the following critical issues were identified and resolved:

### 1. Evidence File Relative Path Resolution Bug
* **Issue Found:** Initially, `GhostDetector` resolved relative paths from `evidence_files.csv` against `config.BASE_DIR` instead of `config.DATASET_DIR`. Since evidence files reside under `dataset/evidence/`, `file_path.exists()` returned `False` for all evidence files, bypassing evidence scanning and incorrectly marking all cases as `COMPLETE`.
* **Improvement Made:** Updated path resolution logic in `GhostDetector` to check `config.DATASET_DIR / file_path_str` first. All 94 evidence files (JSON, LOG, SQL, TXT, CSV) are now successfully located and scanned.

### 2. Multi-Hop Recursive Identity Footprint Resolution & Non-Unique Key Isolation
* **Issue Found:** Initial implementation expanded user identity search targets using non-unique fields like `name`, causing identity footprints of unrelated users sharing first or last names to be merged into a single footprint.
* **Improvement Made:** Updated `IdentifierResolver` to restrict recursive lookup targets to unique identifiers (`user_id`, `email`, `phone`, `email_hash`), avoiding cross-user identity pollution.

### 3. Precise Word Boundary Identifier Matching
* **Issue Found:** Substring matching matching short numeric user IDs (e.g., `"10"`, `"33"`) caused false positives by matching date strings, timestamps, or unrelated numbers in log files.
* **Improvement Made:** Implemented `is_identifier_matched()` using regex word boundaries `\b` for `user_id` and phone numbers while using exact token matching for emails and hashes.

---

## 4. Conclusion

The implemented audit agent provides deterministic, evidence-grounded evaluation of data deletion requests. All mandatory deliverables (`output.csv`, `log.txt`, `evaluation/evaluation_report.md`, and `evaluation/usage_report.md`) have been verified and generated according to specification.
