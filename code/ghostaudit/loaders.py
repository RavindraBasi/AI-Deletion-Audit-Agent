"""CSV loaders and normalization helpers for the participant dataset."""
from __future__ import annotations

import csv
import hashlib
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from .models import Activity, AuditEntry, Case, EvidenceFile, Identity, Rule

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
HASH_RE = re.compile(r"\b[0-9a-f]{64}\b")
USER_RE = re.compile(r"\buser_\d+\b")
log = logging.getLogger(__name__)


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


def _ts(value: str | None, where: str) -> datetime | None:
    """parse_ts that reports a value which is present but not a real instant (e.g. 2026-02-29)."""
    dt = parse_ts(value)
    if dt is None and value and value.strip().lower() not in {"null", "none"}:
        log.warning("unreadable timestamp %r in %s", value, where)
    return dt


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
    """An unreadable requested_at/reported_at falls back to the other one; an unreadable audit_at stays None
    and is resolved by the pipeline (no cutoff)."""
    cases = []
    for r in _rows(path):
        cid = r["case_id"].strip()
        requested = _ts(r["requested_at"], f"{cid}.requested_at")
        reported = _ts(r["reported_at"], f"{cid}.reported_at")
        cases.append(Case(
            case_id=cid,
            email=r["email"],
            requested_at=requested or reported,
            reported_status=r["reported_status"].strip(),
            reported_at=reported or requested,
            audit_at=_ts(r["audit_at"], f"{cid}.audit_at"),
            request_text=r["request_text"],
        ))
    return cases


def load_rules(path: Path) -> list[Rule]:
    return [Rule(r["rule_id"], r["data_category"], r["condition"], r["required_action"]) for r in _rows(path)]


def load_manifest(path: Path, dataset_root: Path) -> list[EvidenceFile]:
    """A file whose capture time cannot be read cannot be placed before any cutoff, so it is skipped."""
    out = []
    for r in _rows(path):
        captured = _ts(r["captured_at"], f"evidence_files.csv {r['file_id']}")
        if captured is not None:
            out.append(EvidenceFile(r["file_id"], dataset_root / r["file_path"], r["origin"], captured))
    return out


def load_identities(path: Path) -> list[Identity]:
    return [
        Identity(r["user_id"], r["name"], r["email"], r["phone"], r["email_hash"], parse_ts(r["updated_at"]))
        for r in _rows(path)
    ]


def load_audit_log(path: Path) -> list[AuditEntry]:
    return [
        AuditEntry(r["audit_id"], r["user_id"], _ts(r["event_time"], f"user_audit_log.csv {r['audit_id']}"),
                   r["field_name"], r["old_value"], r["new_value"])
        for r in _rows(path)
    ]


def load_activity(path: Path) -> list[Activity]:
    """An event whose time cannot be read cannot be placed before any cutoff, so it is skipped."""
    out = []
    for r in _rows(path):
        t = _ts(r["event_time"], f"system_activity.csv {r['activity_id']}")
        if t is not None:
            out.append(Activity(r["activity_id"], t, r["event_type"], r["source_system"],
                                r["target_system"], r["resource_id"], r["reported_result"], r["details"]))
    return out


def case_sort_key(case_id: str):
    m = re.search(r"(\d+)$", case_id)
    return (case_id.rstrip("0123456789"), int(m.group(1)) if m else -1, case_id)
