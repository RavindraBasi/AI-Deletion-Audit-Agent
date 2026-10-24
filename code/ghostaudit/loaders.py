"""CSV loaders and normalization helpers for the participant dataset."""
from __future__ import annotations

import csv
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from .models import Activity, AuditEntry, Case, EvidenceFile, Identity, Rule

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
HASH_RE = re.compile(r"\b[0-9a-f]{64}\b")
USER_RE = re.compile(r"\buser_\d+\b")


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    v = value.strip()
    if not v or v.lower() in {"null", "none"}:
        return None
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def normalize_email(value: str) -> str:
    return (value or "").strip().lower()


def normalize_phone(value: str) -> str:
    v = (value or "").strip()
    digits = re.sub(r"\D", "", v)
    return ("+" + digits) if v.startswith("+") else digits


def email_hash(email: str) -> str:
    return hashlib.sha256(normalize_email(email).encode("utf-8")).hexdigest()


def _rows(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def load_cases(path: Path) -> list[Case]:
    return [
        Case(
            case_id=r["case_id"].strip(),
            email=r["email"],
            requested_at=parse_ts(r["requested_at"]),
            reported_status=r["reported_status"].strip(),
            reported_at=parse_ts(r["reported_at"]),
            audit_at=parse_ts(r["audit_at"]),
            request_text=r["request_text"],
        )
        for r in _rows(path)
    ]


def load_rules(path: Path) -> list[Rule]:
    return [Rule(r["rule_id"], r["data_category"], r["condition"], r["required_action"]) for r in _rows(path)]


def load_manifest(path: Path, dataset_root: Path) -> list[EvidenceFile]:
    return [
        EvidenceFile(r["file_id"], dataset_root / r["file_path"], r["origin"], parse_ts(r["captured_at"]))
        for r in _rows(path)
    ]


def load_identities(path: Path) -> list[Identity]:
    return [
        Identity(r["user_id"], r["name"], r["email"], r["phone"], r["email_hash"], parse_ts(r["updated_at"]))
        for r in _rows(path)
    ]


def load_audit_log(path: Path) -> list[AuditEntry]:
    return [
        AuditEntry(r["audit_id"], r["user_id"], parse_ts(r["event_time"]), r["field_name"], r["old_value"], r["new_value"])
        for r in _rows(path)
    ]


def load_activity(path: Path) -> list[Activity]:
    return [
        Activity(
            r["activity_id"], parse_ts(r["event_time"]), r["event_type"], r["source_system"],
            r["target_system"], r["resource_id"], r["reported_result"], r["details"],
        )
        for r in _rows(path)
    ]


def case_sort_key(case_id: str):
    m = re.search(r"(\d+)$", case_id)
    return (case_id.rstrip("0123456789"), int(m.group(1)) if m else -1, case_id)
