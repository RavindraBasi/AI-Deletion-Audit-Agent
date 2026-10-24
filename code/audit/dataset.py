"""CSV loaders for the participant-facing dataset."""
import csv

from .config import DATASET
from .models import Activity, Request


def _rows(name, root=DATASET):
    with open(root / name, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def load_requests(name="deletion_requests.csv", root=DATASET):
    return [Request(r["case_id"], r["email"].strip(), r["requested_at"], r["reported_status"],
                    r["reported_at"], r["audit_at"], r["request_text"]) for r in _rows(name, root)]


def load_activities(root=DATASET):
    return [Activity(r["activity_id"], r["event_time"], r["event_type"], r["source_system"],
                     r["target_system"], r["resource_id"], r["reported_result"], r["details"])
            for r in _rows("system_activity.csv", root)]


def load_identities(root=DATASET):
    return _rows("user_identities.csv", root)


def load_audit_log(root=DATASET):
    return _rows("user_audit_log.csv", root)


def load_manifest(root=DATASET):
    return _rows("evidence_files.csv", root)


def load_rules(root=DATASET):
    return _rows("deletion_rules.csv", root)


def case_number(case_id):
    return int(case_id.rsplit("_", 1)[1])
