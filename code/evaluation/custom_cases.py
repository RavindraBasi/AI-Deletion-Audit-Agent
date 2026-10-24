#!/usr/bin/env python3
"""Team-authored evaluation cases.

Builds a miniature, fully fictional dataset in a temporary directory (same layout as
dataset/), runs the real agent end-to-end on it and scores the result against
hand-written expected answers with the same scorer used for the public samples.

    python3 code/evaluation/custom_cases.py

Each case targets a failure mode that the public samples exercise weakly:
  case_901  negated deletion wording ("not a request to erase it")          -> NOT_APPLICABLE
  case_902  request sent from a *historical* email; search entry keyed by the old
            address in an SQL inventory; analytics contact row keyed only by the
            sha256 hash of the old address (secondary identifier -> DELETE)
  case_903  evidence captured AFTER audit_at says a connector was re-enabled; the
            answer must ignore it and cite the earlier clean capture -> COMPLETE
  case_904  replication drift: primary is clean, read replica still holds the
            profile; tombstone still exposes name/email/phone -> REMAINING + REDACT
  case_905  de-identified analytics row with no identifiers, attributable only via
            the deletion workflow batch for this request -> COMPLETE with ANONYMIZED
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
from main import load, score  # noqa: E402


def sha(e):
    return hashlib.sha256(e.encode()).hexdigest()


def w_csv(path, header, rows):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


def build(d):
    os.makedirs(os.path.join(d, "evidence", "databases"))
    os.makedirs(os.path.join(d, "evidence", "logs"))
    os.makedirs(os.path.join(d, "evidence", "records"))
    shutil.copy(os.path.join(ROOT, "dataset", "deletion_rules.csv"), d)
    req_h = ["case_id", "email", "requested_at", "reported_status", "reported_at", "audit_at", "request_text"]
    w_csv(os.path.join(d, "sample_deletion_requests.csv"), req_h, [])
    w_csv(os.path.join(d, "deletion_requests.csv"), req_h, [
        ["case_901", "tova.lind@example.net", "2026-05-01T09:00:00Z", "NOT_STARTED", "2026-05-02T09:00:00Z",
         "2026-05-10T09:00:00Z",
         "Which personal details do you store about me? This is an information request, not a request to erase it."],
        ["case_902", "r.okafor.old@example.net", "2026-06-01T10:00:00Z", "COMPLETE", "2026-06-03T10:00:00Z",
         "2026-06-20T10:00:00Z", "Please delete my account and everything linked to my previous address."],
        ["case_903", "mei.tan@example.net", "2026-07-01T10:00:00Z", "COMPLETE", "2026-07-02T10:00:00Z",
         "2026-07-10T10:00:00Z", "Bitte löschen Sie mein Konto vollständig."],
        ["case_904", "j.alvarez@example.net", "2026-08-01T10:00:00Z", "PARTIALLY_COMPLETE",
         "2026-08-03T10:00:00Z", "2026-08-15T10:00:00Z", "Please permanently delete my account."],
        ["case_905", "s.berg@example.net", "2026-08-05T10:00:00Z", "COMPLETE", "2026-08-06T10:00:00Z",
         "2026-08-15T10:00:00Z", "Close my account and delete my personal data, thanks."],
    ])
    w_csv(os.path.join(d, "user_identities.csv"), ["user_id", "name", "email", "phone", "email_hash", "updated_at"], [
        ["user_901", "Tova Lind", "tova.lind@example.net", "+46 70 000 0901", sha("tova.lind@example.net"), "2026-01-01T00:00:00Z"],
        ["user_902", "Rita Okafor", "rita.okafor@example.net", "+234 800 000 0902", sha("rita.okafor@example.net"), "2026-03-01T00:00:00Z"],
        ["user_903", "Mei Tan", "mei.tan@example.net", "+65 8000 0903", sha("mei.tan@example.net"), "2026-01-01T00:00:00Z"],
        ["user_904", "Jon Alvarez", "j.alvarez@example.net", "+34 600 000 904", sha("j.alvarez@example.net"), "2026-01-01T00:00:00Z"],
        ["user_905", "Siv Berg", "s.berg@example.net", "+47 900 00 905", sha("s.berg@example.net"), "2026-01-01T00:00:00Z"],
    ])
    w_csv(os.path.join(d, "user_audit_log.csv"), ["audit_id", "user_id", "event_time", "field_name", "old_value", "new_value"], [
        ["audit_901", "user_902", "2026-03-01T00:00:00Z", "email", "r.okafor.old@example.net", "rita.okafor@example.net"],
    ])
    act_h = ["activity_id", "event_time", "event_type", "source_system", "target_system", "resource_id",
             "reported_result", "details"]
    w_csv(os.path.join(d, "system_activity.csv"), act_h, [
        ["activity_1", "2026-06-03T09:00:00Z", "DELETE_RECORD", "deletion_worker", "account_service", "account_90201",
         "SUCCESS", "Deletion worker submitted primary profile account_90201 to account_service."],
        ["activity_2", "2026-06-03T11:00:00Z", "DELETE_RECORD", "deletion_worker", "analytics_platform", "analytics_90201",
         "FAILED", "Deletion worker received the final result for analytics_90201 from analytics_platform."],
        ["activity_3", "2026-08-06T09:00:00Z", "DELETE_RECORD", "deletion_worker", "account_service", "account_90501",
         "SUCCESS", "Deletion worker submitted primary profile account_90501 to account_service."],
        ["activity_4", "2026-08-06T09:31:00Z", "ANONYMIZE_RECORD", "deletion_worker", "analytics_platform",
         "analytics_90501", "SUCCESS", "Policy action completed for analytics_90501."],
    ])
    # --- evidence files
    inv = """PRAGMA foreign_keys=ON;
BEGIN TRANSACTION;
CREATE TABLE snapshot_scope (origin_system TEXT PRIMARY KEY, captured_at TEXT NOT NULL);
CREATE TABLE customer_profiles (resource_id TEXT PRIMARY KEY, customer_reference TEXT, full_name TEXT, email TEXT, phone TEXT, email_hash TEXT, account_status TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE customer_search_documents (resource_id TEXT PRIMARY KEY, customer_reference TEXT, display_name TEXT, email TEXT, phone TEXT, email_hash TEXT, index_name TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE customer_analytics_profiles (resource_id TEXT PRIMARY KEY, customer_reference TEXT, email TEXT, phone TEXT, email_hash TEXT, audience_cohort TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE connector_subscriptions (resource_id TEXT PRIMARY KEY, customer_reference TEXT, customer_email TEXT, connector_name TEXT NOT NULL, enabled INTEGER NOT NULL, last_delivery_status TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE security_audit_records (resource_id TEXT PRIMARY KEY, subject_reference TEXT NOT NULL, event_code TEXT NOT NULL, retention_until TEXT NOT NULL, updated_at TEXT NOT NULL);
INSERT INTO snapshot_scope VALUES ('account_service', '{T}');
INSERT INTO customer_search_documents VALUES ('search_90201', NULL, NULL, 'r.okafor.old@example.net', NULL, NULL, 'customer_directory_v3', '{T}');
INSERT INTO security_audit_records VALUES ('audit_record_90201', 'user_902', 'ACCOUNT_DELETION_RECORDED', '2031-12-31T23:59:59Z', '{T}');
INSERT INTO customer_analytics_profiles VALUES ('analytics_90301', 'user_903', NULL, NULL, NULL, 'general_shopper', '{T}');
INSERT INTO connector_subscriptions VALUES ('data_flow_90301', 'user_903', NULL, 'customer_events_connector', 0, 'stopped', '{T}');
INSERT INTO security_audit_records VALUES ('audit_record_90301', 'user_903', 'ACCOUNT_DELETION_RECORDED', '2031-12-31T23:59:59Z', '{T}');
COMMIT;
"""
    T = "2026-06-19T08:00:00Z"
    open(os.path.join(d, "evidence", "databases", "inventory_a.sql"), "w").write(inv.replace("{T}", T))
    log = (f'2026-06-19T09:00:00Z level=INFO service=analytics_platform instance=a-1 trace_id=tr-1 '
           f'resource_id=analytics_90201 event=contact_lookup_completed rows=1 email_opt_in=true '
           f'correlation_key="{sha("r.okafor.old@example.net")}"\n')
    open(os.path.join(d, "evidence", "logs", "contact_lookup.log"), "w").write(log)
    late = {"exported_at": "2026-07-20T10:00:00Z", "service_exports": {"integration_service": {"connectors": [
        {"resource_id": "data_flow_90301", "customer_reference": "user_903", "customer_email": None,
         "enabled": True, "last_delivery_status": "delivered", "observed_at": "2026-07-20T09:00:00Z"}]}}}
    json.dump(late, open(os.path.join(d, "evidence", "records", "late_export.json"), "w"))
    drift = """-- Captured at: 2026-08-14T10:00:00Z
BEGIN TRANSACTION;
CREATE TABLE customer_data_primary (resource_id TEXT PRIMARY KEY, origin_system TEXT NOT NULL, collection_name TEXT NOT NULL, record_json TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE customer_data_read_replica (resource_id TEXT PRIMARY KEY, origin_system TEXT NOT NULL, collection_name TEXT NOT NULL, record_json TEXT NOT NULL, updated_at TEXT NOT NULL);
INSERT INTO customer_data_primary VALUES('tombstone_90401','identity_service','deleted_customer_references','{"resource_id":"tombstone_90401","subject_reference":"user_904","name":"Jon Alvarez","email":"j.alvarez@example.net","phone":"+34 600 000 904","email_hash":null,"account_status":"DELETED"}','2026-08-14T10:00:00Z');
INSERT INTO customer_data_read_replica VALUES('account_90401','account_service','customer_profiles','{"resource_id":"account_90401","customer_reference":"user_904","full_name":"Jon Alvarez","email":"j.alvarez@example.net","phone":"+34 600 000 904","account_status":"ACTIVE"}','2026-08-14T10:00:00Z');
INSERT INTO customer_data_primary VALUES('audit_record_90401','security_service','security_audit_events','{"resource_id":"audit_record_90401","subject_reference":"user_904","event_name":"account_deletion_reviewed"}','2026-08-14T10:00:00Z');
INSERT INTO customer_data_primary VALUES('analytics_90501','analytics_platform','customer_analytics_reports','{"resource_id":"analytics_90501","user_id":null,"email":null,"phone":null,"email_hash":null}','2026-08-11T10:00:00Z');
INSERT INTO customer_data_primary VALUES('audit_record_90501','security_service','security_audit_events','{"resource_id":"audit_record_90501","subject_reference":"user_905","event_name":"account_deletion_reviewed"}','2026-08-11T10:00:00Z');
COMMIT;
"""
    open(os.path.join(d, "evidence", "databases", "drift.sql"), "w").write(drift)
    w_csv(os.path.join(d, "evidence_files.csv"), ["file_id", "file_path", "origin", "captured_at"], [
        ["evidence_01", "evidence/databases/inventory_a.sql", "customer_data_inventory_capture", T],
        ["evidence_02", "evidence/logs/contact_lookup.log", "central_observability_export", "2026-06-19T09:30:00Z"],
        ["evidence_03", "evidence/records/late_export.json", "service_resource_export", "2026-07-20T10:00:00Z"],
        ["evidence_04", "evidence/databases/drift.sql", "customer_data_read_replica_capture", "2026-08-14T10:00:00Z"],
    ])


def S(no, phase, sysid, rid, op, state, ev=""):
    return {"step_no": no, "phase": phase, "system_id": sysid, "resource_id": rid, "operation": op,
            "state": state, "evidence_id": ev}


EXPECTED = {
    "case_901": ("NOT_APPLICABLE", []),
    "case_902": ("INCOMPLETE", [
        S(1, "FINDING", "analytics_platform", "analytics_90201", "REPORT_REMAINING_RECORD", "PRESENT", "evidence_02"),
        S(2, "FINDING", "search_service", "search_90201", "REPORT_REMAINING_RECORD", "PRESENT", "evidence_01"),
        S(3, "REMEDIATION", "analytics_platform", "analytics_90201", "DELETE_RECORD", "ABSENT"),
        S(4, "REMEDIATION", "search_service", "search_90201", "DELETE_RECORD", "ABSENT"),
        S(5, "VERIFICATION", "account_service", "account_90201", "VERIFY_RECORD_ABSENT", "ABSENT"),
        S(6, "VERIFICATION", "analytics_platform", "analytics_90201", "VERIFY_RECORD_ABSENT", "ABSENT"),
        S(7, "VERIFICATION", "search_service", "search_90201", "VERIFY_RECORD_ABSENT", "ABSENT"),
        S(8, "VERIFICATION", "security_service", "audit_record_90201", "VERIFY_RECORD_PRESENT", "PRESENT"),
    ]),
    "case_903": ("COMPLETE", [
        S(1, "VERIFICATION", "security_service", "audit_record_90301", "VERIFY_RECORD_PRESENT", "PRESENT", "evidence_01"),
        S(2, "VERIFICATION", "analytics_platform", "analytics_90301", "VERIFY_RECORD_ANONYMIZED", "ANONYMIZED", "evidence_01"),
        S(3, "VERIFICATION", "integration_service", "data_flow_90301", "VERIFY_DATA_FLOW_DISABLED", "DISABLED", "evidence_01"),
    ]),
    "case_904": ("INCOMPLETE", [
        S(1, "FINDING", "account_service", "account_90401", "REPORT_REMAINING_RECORD", "PRESENT", "evidence_04"),
        S(2, "FINDING", "identity_service", "tombstone_90401", "REPORT_EXPOSED_PII", "PRESENT", "evidence_04"),
        S(3, "REMEDIATION", "account_service", "account_90401", "DELETE_RECORD", "ABSENT"),
        S(4, "REMEDIATION", "identity_service", "tombstone_90401", "REDACT_RECORD", "REDACTED"),
        S(5, "VERIFICATION", "account_service", "account_90401", "VERIFY_RECORD_ABSENT", "ABSENT"),
        S(6, "VERIFICATION", "security_service", "audit_record_90401", "VERIFY_RECORD_PRESENT", "PRESENT"),
        S(7, "VERIFICATION", "identity_service", "tombstone_90401", "VERIFY_RECORD_REDACTED", "REDACTED"),
    ]),
    "case_905": ("COMPLETE", [
        S(1, "VERIFICATION", "account_service", "account_90501", "VERIFY_RECORD_ABSENT", "ABSENT", "evidence_04"),
        S(2, "VERIFICATION", "security_service", "audit_record_90501", "VERIFY_RECORD_PRESENT", "PRESENT", "evidence_04"),
        S(3, "VERIFICATION", "analytics_platform", "analytics_90501", "VERIFY_RECORD_ANONYMIZED", "ANONYMIZED", "evidence_04"),
    ]),
}


def main():
    tmp = tempfile.mkdtemp(prefix="ghost_custom_")
    try:
        d = os.path.join(tmp, "dataset")
        build(d)
        out = os.path.join(tmp, "pred.csv")
        subprocess.run([sys.executable, os.path.join(ROOT, "code", "main.py"), "--dataset", d, "--output", out,
                        "--no-llm"], check=True, capture_output=True)
        gold = os.path.join(tmp, "gold.csv")
        with open(gold, "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["case_id", "deletion_status", "steps_json"])
            for cid, (st, steps) in sorted(EXPECTED.items()):
                w.writerow([cid, st, json.dumps(steps)])
        res = score(load(out), load(gold), details="-v" in sys.argv)
        for k, v in res.items():
            print(f"{k:28s} {v:.3f}" if isinstance(v, float) else f"{k:28s} {v}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
