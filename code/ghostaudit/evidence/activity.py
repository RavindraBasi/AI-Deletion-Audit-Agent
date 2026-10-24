"""system_activity.csv -> Observations. Workflow results are claims; only state checks and recreation
events are treated as observations (SPEC §2.3)."""
from __future__ import annotations

import re

from ..identity import norm_identifier
from ..loaders import EMAIL_RE
from ..models import Activity, Kind, Observation
from .records import RESOURCE_RE, USER_RE, system_for

REPOPULATED_RE = re.compile(r"repopulated downstream resource (\S+?)\.?$")


def _obs(a: Activity, rid: str, kind: Kind, ids=frozenset(), attrs=None) -> Observation:
    base = {
        "event_type": a.event_type, "reported_result": a.reported_result, "source_system": a.source_system,
        "target_system": a.target_system, "details": a.details,
    }
    if attrs:
        base.update(attrs)
    return Observation(
        evidence_id=a.activity_id,
        observed_at=a.event_time,
        captured_at=a.event_time,
        system_id=system_for(rid, a.target_system),
        resource_id=rid,
        kind=kind,
        category=None,
        identifiers=frozenset(ids),
        location="activity",
        attrs=base,
    )


def extract(activities: list[Activity]) -> list[Observation]:
    out: list[Observation] = []
    for a in activities:
        d = a.details
        rid = a.resource_id
        if a.event_type == "CHECK_DATA_FLOW" and "remains enabled" in d:
            out.append(_obs(a, rid, Kind.FLOW_ACTIVE))
        elif a.event_type == "CHECK_RESTORE_ACCESS" and "can still be restored" in d:
            out.append(_obs(a, rid, Kind.RESTORE_ALLOWED))
        elif a.event_type in {"SYNC_RECORD", "RESTORE_BACKUP"} and (m := REPOPULATED_RE.search(d)):
            downstream = m.group(1)
            src_kind = Kind.FLOW_ACTIVE if a.event_type == "SYNC_RECORD" else Kind.RESTORE_ALLOWED
            out.append(_obs(a, rid, src_kind, attrs={"_refs": downstream, "_recreates": downstream}))
            out.append(_obs(a, downstream, Kind.RECREATED, attrs={"_refs": rid, "_recreated_by": rid}))
        elif a.event_type == "LINK_RESOURCE_IDENTITY":
            ids = set()
            for e in EMAIL_RE.findall(d):
                ids.add(norm_identifier("email", e))
            for u in USER_RE.findall(d):
                ids.add(norm_identifier("user_id", u))
            refs = {r for r in RESOURCE_RE.findall(d) if r != rid}
            attrs = {"_refs": " ".join(sorted(refs))} if refs else {}
            if "belongs only to" in d:
                attrs["exclusive_owner"] = "true"
            out.append(_obs(a, rid, Kind.LINK, ids, attrs))
        elif a.event_type == "CLASSIFY_REQUEST":
            out.append(_obs(a, rid, Kind.CLAIM, attrs={"non_deletion": str("non-deletion" in d).lower()}))
        else:
            out.append(_obs(a, rid, Kind.CLAIM))
    return out
