"""Hand-built scenario cases, each a miniature dataset audited end-to-end by the real agent.

Every scenario writes its own dataset/ folder (identities, audit log, activity, manifest,
evidence) into a temporary directory, runs DeletionAuditor offline on it, and returns
(case_id, predicted, expected) for the scorer. Expected outputs were written by hand from
the problem statement's rules, not produced by the agent.
"""
import csv
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from audit.agent import DeletionAuditor  # noqa: E402
from audit.config import DATASET  # noqa: E402
from audit.dataset import load_requests  # noqa: E402
from audit.llm.client import LLM  # noqa: E402
from audit.planner import to_json_steps  # noqa: E402

REQUEST_HEADER = ["case_id", "email", "requested_at", "reported_status", "reported_at", "audit_at", "request_text"]
ACTIVITY_HEADER = ["activity_id", "event_time", "event_type", "source_system", "target_system", "resource_id",
                   "reported_result", "details"]


def sha(email):
    return hashlib.sha256(email.encode()).hexdigest()


def step(n, phase, system, rid, op, state, ev=""):
    return {"step_no": n, "phase": phase, "system_id": system, "resource_id": rid, "operation": op,
            "state": state, "evidence_id": ev}


def _write_csv(path, header, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def build(root, request, users, audit_log=(), activities=(), evidence=()):
    """evidence: [(file_id, relative_path, captured_at, text)]"""
    (root / "evidence").mkdir(parents=True)
    shutil.copy(DATASET / "deletion_rules.csv", root / "deletion_rules.csv")
    _write_csv(root / "deletion_requests.csv", REQUEST_HEADER, [request])
    _write_csv(root / "user_identities.csv", ["user_id", "name", "email", "phone", "email_hash", "updated_at"],
               [[u, n, e, p, sha(e), "2026-01-01T00:00:00Z"] for u, n, e, p in users])
    _write_csv(root / "user_audit_log.csv", ["audit_id", "user_id", "event_time", "field_name", "old_value",
                                             "new_value"], audit_log)
    _write_csv(root / "system_activity.csv", ACTIVITY_HEADER, activities)
    manifest = []
    for file_id, rel, captured, text in evidence:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        manifest.append([file_id, rel, "custom_scenario", captured])
    _write_csv(root / "evidence_files.csv", ["file_id", "file_path", "origin", "captured_at"], manifest)


def bundle(captured, collections):
    return json.dumps({"exported_at": captured, "source": "custom", "snapshots": [
        {"captured_at": captured, "data_store": store, "collection": name, "records": records}
        for store, name, records in collections]})


SCENARIOS = []


def scenario(fn):
    SCENARIOS.append(fn)
    return fn


@scenario
def korean_non_deletion(root):
    """A Korean request for an order-history copy that explicitly says not to delete → NOT_APPLICABLE."""
    build(root, ["custom_01", "min.seo@example.com", "2026-09-01T10:00:00Z", "COMPLETE", "2026-09-02T10:00:00Z",
                 "2026-09-10T10:00:00Z", "제 주문 내역 사본을 보내주세요. 계정은 삭제하지 마세요."],
          users=[("user_901", "Seo Min", "min.seo@example.com", "+82 10 1111 2222")])
    return "NOT_APPLICABLE", []


@scenario
def evidence_after_audit_is_ignored(root):
    """The account is gone at audit_at; a later capture shows it recreated, which must not change
    the answer or be cited."""
    email = "lena.vogt@example.com"
    build(root, ["custom_02", email, "2026-09-01T10:00:00Z", "COMPLETE", "2026-09-02T10:00:00Z",
                 "2026-09-05T10:00:00Z", "Please delete my account and all personal data."],
          users=[("user_902", "Lena Vogt", email, "+49 151 1234 5678")],
          activities=[["activity_1", "2026-09-02T09:00:00Z", "DELETE_RECORD", "deletion_worker", "account_service",
                       "account_902", "SUCCESS", "Deletion worker submitted primary profile account_902."]],
          evidence=[
              ("evidence_01", "evidence/logs/lookup_0904.log", "2026-09-04T08:00:00Z",
               "2026-09-04T08:00:00Z level=DEBUG service=account_service trace_id=rb-1 event=profile_lookup_started "
               f"resource_id=account_902 lookup_key={email}\n"
               "2026-09-04T08:00:00Z level=INFO service=account_service trace_id=rb-1 event=profile_lookup_completed "
               "resource_id=account_902 rows=0 http_status=404\n"),
              ("evidence_02", "evidence/records/bundle_0908.json", "2026-09-08T08:00:00Z",
               bundle("2026-09-08T08:00:00Z", [("account_service", "customer_profiles", [
                   {"resource_id": "account_902", "customer_reference": "user_902", "full_name": "Lena Vogt",
                    "email": email, "phone": "+49 151 1234 5678", "account_status": "ACTIVE"}])])),
          ])
    return "COMPLETE", [step(1, "VERIFICATION", "account_service", "account_902", "VERIFY_RECORD_ABSENT", "ABSENT",
                             "evidence_01")]


@scenario
def historical_email_in_replica_only(root):
    """The request uses the current email; the surviving row sits only in a read replica under the
    old email recorded in the audit log."""
    old, new = "r.okafor@example.org", "rita.okafor@example.org"
    replica = (
        "BEGIN TRANSACTION;\n"
        "CREATE TABLE customer_data_primary (resource_id TEXT, origin_system TEXT, collection_name TEXT, "
        "record_json TEXT, updated_at TEXT);\n"
        "CREATE TABLE customer_data_read_replica (resource_id TEXT, origin_system TEXT, collection_name TEXT, "
        "record_json TEXT, updated_at TEXT);\n"
        "INSERT INTO customer_data_read_replica VALUES('account_903','account_service','customer_profiles',"
        f"'{{\"resource_id\":\"account_903\",\"customer_reference\":\"user_903\",\"full_name\":\"Rita Okafor\","
        f"\"email\":\"{old}\",\"phone\":\"+234 803 000 1111\",\"account_status\":\"ACTIVE\"}}',"
        "'2026-09-06T00:00:00Z');\nCOMMIT;\n")
    build(root, ["custom_03", new, "2026-09-01T10:00:00Z", "COMPLETE", "2026-09-02T10:00:00Z",
                 "2026-09-07T10:00:00Z", "I changed my email last year. Delete my account under both addresses."],
          users=[("user_903", "Rita Okafor", new, "+234 803 000 1111")],
          audit_log=[["audit_1", "user_903", "2025-05-01T00:00:00Z", "email", old, new]],
          evidence=[("evidence_01", "evidence/databases/replica_0906.sql", "2026-09-06T00:00:00Z", replica)])
    return "INCOMPLETE", [
        step(1, "FINDING", "account_service", "account_903", "REPORT_REMAINING_RECORD", "PRESENT", "evidence_01"),
        step(2, "REMEDIATION", "account_service", "account_903", "DELETE_RECORD", "ABSENT"),
        step(3, "VERIFICATION", "account_service", "account_903", "VERIFY_RECORD_ABSENT", "ABSENT")]


@scenario
def tombstone_with_exposed_pii_and_restore_risk(root):
    """A deleted-account tombstone still shows the name and email, and a backup can still restore the
    profile: block the restore first, then redact; keep the retained audit record."""
    email = "tomas.lindqvist@example.se"
    build(root, ["custom_04", email, "2026-09-01T10:00:00Z", "PARTIALLY_COMPLETE", "2026-09-02T10:00:00Z",
                 "2026-09-06T10:00:00Z", "Ta bort mitt konto. Please delete my account permanently."],
          users=[("user_904", "Tomas Lindqvist", email, "+46 70 123 45 67")],
          evidence=[("evidence_01", "evidence/records/bundle_0905.json", "2026-09-05T08:00:00Z",
                     bundle("2026-09-05T08:00:00Z", [
                         ("account_service", "customer_profiles", []),
                         ("identity_service", "deleted_customer_references", [
                             {"resource_id": "tombstone_904", "subject_reference": "user_904",
                              "name": "Tomas Lindqvist", "email": email, "phone": None, "email_hash": None,
                              "account_status": "DELETED"}]),
                         ("backup_service", "backup_catalog", [
                             {"resource_id": "backup_904", "archive_path": "s3://b/seg-9.tar.zst",
                              "subject_reference": "user_904", "restore_allowed": True}]),
                         ("security_service", "security_audit_events", [
                             {"resource_id": "audit_record_904", "subject_reference": "user_904",
                              "event_name": "account_deletion_reviewed"}]),
                     ]))])
    return "INCOMPLETE", [
        step(1, "FINDING", "identity_service", "tombstone_904", "REPORT_EXPOSED_PII", "PRESENT", "evidence_01"),
        step(2, "FINDING", "backup_service", "backup_904", "REPORT_RESTORE_RISK", "ACTIVE", "evidence_01"),
        step(3, "REMEDIATION", "backup_service", "backup_904", "BLOCK_RESTORE", "BLOCKED"),
        step(4, "REMEDIATION", "identity_service", "tombstone_904", "REDACT_RECORD", "REDACTED"),
        step(5, "VERIFICATION", "security_service", "audit_record_904", "VERIFY_RECORD_PRESENT", "PRESENT"),
        step(6, "VERIFICATION", "identity_service", "tombstone_904", "VERIFY_RECORD_REDACTED", "REDACTED"),
        step(7, "VERIFICATION", "backup_service", "backup_904", "VERIFY_RESTORE_BLOCKED", "BLOCKED")]


@scenario
def decoy_lookalike_email(root):
    """A search document for a near-identical email belongs to someone else and must not be reported."""
    email = "j.moreau@example.fr"
    build(root, ["custom_05", email, "2026-09-01T10:00:00Z", "COMPLETE", "2026-09-02T10:00:00Z",
                 "2026-09-06T10:00:00Z", "Je demande la suppression définitive de mon compte."],
          users=[("user_905", "Julie Moreau", email, "+33 6 11 22 33 44"),
                 ("user_906", "Jules Moreau", "j.moreau.jr@example.fr", "+33 6 55 66 77 88")],
          evidence=[("evidence_01", "evidence/records/bundle_0905.json", "2026-09-05T08:00:00Z",
                     bundle("2026-09-05T08:00:00Z", [
                         ("account_service", "customer_profiles", []),
                         ("search_service", "customer_search_documents", [
                             {"resource_id": "search_906", "index_name": "customer_directory_v3",
                              "document": {"display_name": "Jules Moreau", "email": "j.moreau.jr@example.fr"}}]),
                         ("security_service", "security_audit_events", [
                             {"resource_id": "audit_record_905", "subject_reference": "user_905",
                              "event_name": "account_deletion_reviewed"}]),
                     ]))])
    return "COMPLETE", [step(1, "VERIFICATION", "security_service", "audit_record_905", "VERIFY_RECORD_PRESENT",
                             "PRESENT", "evidence_01")]


def run_custom():
    pairs = []
    for fn in SCENARIOS:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "dataset"
            expected = fn(root)
            requests = load_requests(root=root)
            status, steps = DeletionAuditor(LLM(offline=True), root=root).run(requests)[requests[0].case_id]
            pairs.append((f"{requests[0].case_id} ({fn.__name__})", (status, to_json_steps(steps)), expected))
    return pairs


if __name__ == "__main__":
    for name, got, want in run_custom():
        print("PASS" if got == want else "FAIL", name)
        if got != want:
            print("   got ", got)
            print("   want", want)
