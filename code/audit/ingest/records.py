"""JSON exports and plain-text handover records."""
import json

from ..models import Coverage, Observation
from .normalize import flag, record_tokens, system_of, token

# Ledger `result` vocabulary → (present, pii) where pii None means "not stated".
RESULT_STATE = {
    "FOUND": (True, None), "IDENTIFIABLE": (True, True), "ACTIVE": (True, None),
    "NOT_FOUND": (False, False), "ANONYMIZED": (True, False), "REDACTED": (True, False),
    "BLOCKED": (True, None), "DISABLED": (True, None),
}


def parse_json(path, evidence_id, captured_at):
    data = json.loads(path.read_text(encoding="utf-8"))
    if "snapshots" in data:
        return _bundle(data, evidence_id, captured_at)
    if "service_exports" in data:
        return _service_export(data, evidence_id, captured_at), []
    if "collection" in data:     # single-collection dump (analytics report, export jobs)
        system = {"customer_analytics_reports": "analytics_platform",
                  "customer_export_jobs": "export_service"}[data["collection"]]
        obs = [_record_obs(r, system, evidence_id, captured_at, data["captured_at"]) for r in data["records"]]
        return obs, [Coverage(evidence_id, captured_at, data["captured_at"], system, "primary",
                              frozenset(r["resource_id"] for r in data["records"]))]
    return [_ledger_obs(r, evidence_id, captured_at) for r in data["records"]], []


def parse_txt(path, evidence_id, captured_at):
    obs = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = [p.strip() for p in line.lstrip("- ").split("|")]
        if len(parts) == 6 and system_of(parts[2]):
            when, system, rid, record_type, result, subject = parts
            obs.append(_ledger_obs({"recorded_at": when, "system": system, "resource_id": rid,
                                    "record_type": record_type, "result": result,
                                    "subject_reference": subject}, evidence_id, captured_at))
    return obs, []


def _bundle(data, evidence_id, captured_at):
    obs, coverages = [], []
    for snap in data["snapshots"]:
        records = snap["records"]
        if snap["collection"] == "operational_record_history":
            obs += [_ledger_obs(r, evidence_id, captured_at) for r in records]
            continue
        system = snap["data_store"]
        obs += [_record_obs(r, system, evidence_id, captured_at, snap["captured_at"]) for r in records]
        coverages.append(Coverage(evidence_id, captured_at, snap["captured_at"], system, "primary",
                                  frozenset(r["resource_id"] for r in records)))
    return obs, coverages


def _service_export(data, evidence_id, captured_at):
    obs = []
    for system, groups in data["service_exports"].items():
        for group, records in groups.items():
            for r in records:
                when = r.get("observed_at", captured_at)
                if group == "profile_lookups":
                    profile = r.get("profile") or {}
                    o = _record_obs(profile, system, evidence_id, captured_at, when, r["resource_id"])
                    o.present = r.get("status_code") == 200
                elif group == "index_queries":
                    docs = r.get("hits", {}).get("documents", [])
                    o = _record_obs(docs[0] if docs else {}, system, evidence_id, captured_at, when,
                                    r["resource_id"])
                    o.present = bool(docs)
                    if token(r.get("query")):
                        o.links = o.links | {token(r["query"])}
                else:
                    o = _record_obs(r, system, evidence_id, captured_at, when)
                obs.append(o)
    return obs


def _record_obs(record, system, evidence_id, captured_at, observed_at, resource_id=None):
    flat = {k: v for k, v in record.items() if k not in ("resource_id", "metrics", "recent_activity")}
    links, pii, ref = record_tokens(flat)
    attrs = {"ref": ref}
    for key in ("enabled", "restore_allowed", "download_available"):
        if key in record:
            attrs[key] = flag(record[key])
    for key in ("account_status", "retention_until", "retained_until", "event_code", "event_name"):
        if record.get(key):
            attrs[key] = record[key]
    if "file_name" in record or "storage_class" in record:
        attrs["attachment"] = True
    return Observation(evidence_id, captured_at, observed_at or captured_at, system,
                       resource_id or record["resource_id"], True, "primary", frozenset(links), pii, attrs)


def _ledger_obs(r, evidence_id, captured_at):
    present, pii = RESULT_STATE.get(r["result"], (True, None))
    subject = r.get("subject_reference") or r.get("lookup_value")
    links = {t for t in [token(subject)] if t}
    attrs = {"record_type": r["record_type"], "result": r["result"], "ref": False}
    if r["result"] in ("ACTIVE", "DISABLED"):
        attrs["enabled"] = r["result"] == "ACTIVE"
    if r["result"] in ("ACTIVE", "BLOCKED") and r["record_type"] == "BACKUP_ARCHIVE":
        attrs["restore_allowed"] = r["result"] == "ACTIVE"
    if pii is None:   # FOUND on an identifier-bearing record: the person is still visible
        pii = present and r["result"] == "FOUND"
    return Observation(evidence_id, captured_at, r.get("recorded_at") or captured_at, r["system"],
                       r["resource_id"], present, "primary", frozenset(links), pii, attrs)
