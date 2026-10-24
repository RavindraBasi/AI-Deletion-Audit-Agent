"""Evidence extraction entry point: build_observation_store(manifest, activities) -> ObservationStore."""
from __future__ import annotations

import logging
import re
from collections import defaultdict

from ..models import Activity, EvidenceFile
from . import activity, json_records, logs, multimodal, sql_snapshots, text_records
from .records import system_for, to_observation
from .store import Coverage, ObservationStore

log = logging.getLogger(__name__)

EXTRACTORS = {
    ".sql": sql_snapshots.extract,
    ".json": json_records.extract,
    ".txt": text_records.extract,
    ".log": logs.extract,
}


FILE_ALIAS_RE = re.compile(r"\b([a-z]+(?:_[a-z]+)*_\d+) \(([^()\s]+\.[A-Za-z0-9]{2,4})\)")


def file_aliases(activities: list[Activity]) -> dict[str, str]:
    """`object_19 (delivery_confirmation_19.pdf)` in a data-catalog link maps a file name to its resource id."""
    out = {}
    for a in activities:
        if a.event_type == "LINK_RESOURCE_IDENTITY":
            for rid, name in FILE_ALIAS_RE.findall(a.details):
                out[name] = rid
    return out


def build_observation_store(manifest: list[EvidenceFile], activities: list[Activity], llm=None) -> ObservationStore:
    store = ObservationStore()
    aliases = file_aliases(activities)
    for ef in sorted(manifest, key=lambda e: e.file_id):
        suffix = ef.path.suffix.lower()
        if not ef.path.exists():
            store.report[ef.file_id] = {"path": str(ef.path), "error": "missing"}
            continue
        if suffix in EXTRACTORS:
            records, cov = EXTRACTORS[suffix](ef)
        elif suffix in multimodal.VISUAL_SUFFIXES:
            records, cov = multimodal.extract(ef, llm)
        else:
            store.report[ef.file_id] = {"path": str(ef.path), "error": f"unsupported {suffix}"}
            continue
        n = 0
        present_by_system = defaultdict(set)
        for raw in records:
            if raw.resource_id.startswith("file:"):
                rid = aliases.get(raw.resource_id[5:])
                if rid is None:
                    continue
                raw.resource_id = rid
            obs = to_observation(raw)
            if obs is None:
                continue
            store.add(obs)
            n += 1
            present_by_system[(obs.system_id, obs.location)].add(obs.resource_id)
        # A snapshot that covers a system asserts completeness for it: a record of that system missing from
        # every table/collection of the file is absent at capture time.
        for system in sorted({s for s, _ in cov}):
            present = set()
            for (sys_id, _), rids in present_by_system.items():
                if sys_id == system:
                    present |= rids
            store.coverage.append(Coverage(ef.file_id, ef.captured_at, system, "*", frozenset(present)))
        store.report[ef.file_id] = {"path": str(ef.path.name), "records": len(records), "observations": n,
                                    "coverage": sorted(f"{s}@{l}" for s, l in cov)}
    for obs in activity.extract(activities):
        store.add(obs)
    store.report["system_activity"] = {"observations": len(activities)}
    return store


__all__ = ["build_observation_store", "ObservationStore", "Coverage", "system_for"]
