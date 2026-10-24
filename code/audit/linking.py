"""Which resources belong to a case.

Tier 1 — exact identifier evidence (records, trace-joined lookup keys, data-catalog links).
Tier 2 — the deletion worker's activity trail for resources that carry no identifier at all
(deleted accounts answering 404, fully anonymised analytics rows), assigned globally so each
pipeline event belongs to exactly one case.
"""
import re
from collections import defaultdict
from datetime import datetime, timedelta

from .ingest.normalize import system_of, text_tokens

LINK_RE = re.compile(r"linked (\S+) \(([^)]+)\) to (\S+) and (user_\d+)")
# What the subject says they still see is evidence of which systems hold their data.
TEXT_HINTS = {
    "integration_service": re.compile(r"integration|sync|connector|send my data", re.I),
    "backup_service": re.compile(r"backup|restore", re.I),
    "search_service": re.compile(r"search", re.I),
    "analytics_platform": re.compile(r"analytics|statistic", re.I),
    "support_platform": re.compile(r"support|document I sent|conversation", re.I),
    "export_service": re.compile(r"export|download|archive", re.I),
}


def catalog_links(activities):
    """LINK_RESOURCE_IDENTITY rows → [(time, resource_id, tokens)] plus business aliases."""
    links, aliases = [], {}
    for a in activities:
        if a.event_type != "LINK_RESOURCE_IDENTITY":
            continue
        links.append((a.time, a.resource_id, frozenset(text_tokens(a.details))))
        m = LINK_RE.search(a.details)
        if m and m.group(2) != a.resource_id and not m.group(2).startswith("user_"):
            aliases[m.group(2)] = a.resource_id
    return links, aliases


def lifecycle_windows(cases):
    """Linking window per case: (previous audit_at of the same subject, own audit_at].

    The subject's latest case stays open-ended: records captured after audit_at may still name a
    resource that belongs to it (its state is never read from them — see state.py)."""
    by_user = defaultdict(list)
    for case in cases:
        by_user[case.subject.user_id].append(case)
    windows = {}
    for group in by_user.values():
        group.sort(key=lambda c: c.request.audit_at)
        lower = ""
        for i, case in enumerate(group):
            upper = case.request.audit_at if i + 1 < len(group) else "9999"
            windows[case.request.case_id] = (lower, upper)
            lower = case.request.audit_at
    return windows


def _minutes(a, b):
    return (datetime.fromisoformat(a.replace("Z", "+00:00"))
            - datetime.fromisoformat(b.replace("Z", "+00:00"))).total_seconds() / 60


def _expected_offset(activity):
    """Minutes after the account delete at which the worker typically emits this event."""
    if activity.event_type.startswith("CHECK_") or activity.result != "SUCCESS":
        return 125
    return 125 if activity.event_type == "DELETE_RECORD" else 35


def tier2(cases, linked, activities, files_of):
    """Assign the deletion worker's trail to cases.

    ``linked`` maps case_id → tier-1 resource set; it pins accounts and excludes resources that
    already belong to someone. ``files_of`` maps resource_id → evidence files observing it: a trail
    event prefers the case whose own resources appear in the same files. Returns case_id → set of
    (system, resource_id)."""
    worker = [a for a in activities if a.source == "deletion_worker"]
    owned = {rid for found in linked.values() for _, rid in found}
    account_events = [a for a in worker if a.target == "account_service" and a.event_type == "DELETE_RECORD"]
    anchors, taken = {}, set()
    for case in cases:   # pin accounts tier 1 already tied to the case
        for a in account_events:
            if (a.target, a.resource_id) in linked[case.request.case_id] and a.activity_id not in taken:
                anchors[case.request.case_id] = a
                taken.add(a.activity_id)
                break
    # The workflow's reported status and its account-delete result are the same claim.
    status_result = {"COMPLETE": "SUCCESS", "PARTIALLY_COMPLETE": "PARTIAL", "NOT_STARTED": "FAILED"}
    pending = {c.request.case_id: [a for a in account_events
                                   if -150 <= _minutes(a.time, c.request.reported_at) <= 30
                                   and a.resource_id not in owned]
               for c in cases if c.request.case_id not in anchors}
    by_id = {c.request.case_id: c for c in cases}
    trail = {cid: [a for a in worker if (system_of(a.resource_id), a.resource_id) in found and
                   a.target != "account_service"] for cid, found in linked.items()}

    def cost(cid, a):
        """Status claim must agree; the case's own downstream events must sit 15–175 min after."""
        mismatch = a.result != status_result.get(by_id[cid].request.reported_status)
        miss = sum(not 15 <= _minutes(e.time, a.time) <= 175 for e in trail.get(cid, ()))
        return mismatch + miss, abs(_minutes(a.time, by_id[cid].request.reported_at) + 60), a.activity_id

    def feasible(cid):
        n = sum(cost(cid, a)[0] == 0 for a in pending[cid])
        return (n or 99, len(pending[cid]) or 99, cid)

    while pending:   # constraint propagation, most-constrained case first
        for cid in pending:
            pending[cid] = [a for a in pending[cid] if a.activity_id not in taken]
        cid = min(pending, key=feasible)
        options = pending.pop(cid)
        if options:
            best = min(options, key=lambda a: cost(cid, a))
            anchors[cid] = best
            taken.add(best.activity_id)
    found = defaultdict(set, {cid: {("account_service", a.resource_id)} for cid, a in anchors.items()})
    # Without an account event the trail still starts about an hour before the reported status.
    start = {cid: a.time for cid, a in anchors.items()}
    for c in cases:
        if c.request.case_id not in start:
            shifted = datetime.fromisoformat(c.request.reported_at.replace("Z", "+00:00")) - timedelta(hours=1)
            start[c.request.case_id] = shifted.strftime("%Y-%m-%dT%H:%M:%SZ")
    case_files = {cid: set().union(*(files_of.get(r, set()) for _, r in found)) for cid, found in linked.items()}
    pairs = []
    for a in worker:
        if a.target == "account_service" or a.resource_id in owned:
            continue
        for cid, anchor_time in start.items():
            offset = _minutes(a.time, anchor_time)
            if 0 <= offset <= 180:
                apart = not files_of.get(a.resource_id, set()) & case_files.get(cid, set())
                hint = TEXT_HINTS.get(system_of(a.resource_id))
                unhinted = not (hint and hint.search(by_id[cid].request.text))
                # Cases sharing a slot are served in a stable order: earlier event ↔ lower case number.
                pairs.append((apart, unhinted, round(abs(offset - _expected_offset(a)) / 30), a.time,
                              int(cid.rsplit("_", 1)[1]), cid, a))
    used = set()
    systems = defaultdict(set, {cid: {s for s, _ in found} for cid, found in linked.items()})
    for *_, cid, a in sorted(pairs, key=lambda p: p[:5]):
        system = system_of(a.resource_id)
        if a.activity_id in used or system in systems[cid]:
            continue
        used.add(a.activity_id)
        systems[cid].add(system)
        found[cid].add((system, a.resource_id))
    return found


def tier1(case, window, observations, links):
    """Resources tied to the subject by an exact identifier inside the case's lifecycle window."""
    lo, hi = window
    found = set()
    for o in observations:
        if lo < o.captured_at <= hi and o.links & case.subject.tokens:
            found.add((o.system, o.resource_id))
    for time, rid, tokens in links:
        if lo < time <= hi and tokens & case.subject.tokens:
            found.add((system_of(rid), rid))
    return found
