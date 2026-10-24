"""SQLite-compatible snapshot exports (primary + replica tables)."""
import json
import re
import sqlite3

from ..models import Coverage, Observation
from .normalize import flag, record_tokens, system_of

INVENTORY_TABLES = {
    "customer_profiles": "account_service", "customer_search_documents": "search_service",
    "customer_analytics_profiles": "analytics_platform", "support_records": "support_platform",
    "customer_exports": "export_service", "connector_subscriptions": "integration_service",
    "backup_restore_entries": "backup_service", "security_audit_records": "security_service",
}
COPY_RE = re.compile(r"_(primary|failover_replica|read_replica|reporting_replica|replica_[a-z_]+)$")


def parse(path, evidence_id, captured_at):
    db = sqlite3.connect(":memory:")
    db.executescript(path.read_text(encoding="utf-8"))
    db.row_factory = sqlite3.Row
    tables = [r[0] for r in db.execute("select name from sqlite_master where type='table'")]
    scope = {}
    if "snapshot_scope" in tables:
        scope = {r["origin_system"]: r["captured_at"] for r in db.execute("select * from snapshot_scope")}
    obs, rows_by_copy, systems = [], {}, set()
    for table in tables:
        if table == "snapshot_scope":
            continue
        m = COPY_RE.search(table)
        copy = m.group(1) if m else "primary"
        for row in db.execute(f'select * from "{table}"'):
            record = dict(row)
            if "record_json" in record:                       # generic replica layout
                record = {**json.loads(record.pop("record_json")), "updated_at": record["updated_at"],
                          "_system": record["origin_system"]}
            system = (record.pop("_system", None) or INVENTORY_TABLES.get(table)
                      or system_of(record["resource_id"]))
            obs.append(_observation(record, system, copy, evidence_id, captured_at))
            rows_by_copy.setdefault((system, copy), set()).add(record["resource_id"])
            systems.add(system)
    if scope:   # inventory snapshot: coverage only for the systems it declares
        cover = [(s, "primary", t) for s, t in scope.items()]
    else:       # replica dump: every copy table covers every system seen in the file
        copies = {COPY_RE.search(t).group(1) if COPY_RE.search(t) else "primary" for t in tables}
        cover = [(s, c, captured_at) for s in systems for c in copies]
    coverages = [Coverage(evidence_id, captured_at, t, s, c, frozenset(rows_by_copy.get((s, c), ())))
                 for s, c, t in cover]
    return obs, coverages


def _observation(record, system, copy, evidence_id, captured_at):
    links, pii, ref = record_tokens({k: v for k, v in record.items() if k not in ("resource_id",)})
    attrs = {"ref": ref}
    for key in ("enabled", "restore_allowed", "download_available"):
        if key in record:
            attrs[key] = flag(record[key])
    if "account_status" in record:
        attrs["status"] = record["account_status"]
    if "retention_until" in record or "retained_until" in record:
        attrs["retention_until"] = record.get("retention_until") or record.get("retained_until")
    if "file_name" in record or "storage_class" in record:
        attrs["attachment"] = True
    observed = record.get("updated_at") or record.get("indexed_at") or captured_at
    return Observation(evidence_id, captured_at, min(observed, captured_at), system, record["resource_id"],
                       True, copy, frozenset(links), pii, attrs)
