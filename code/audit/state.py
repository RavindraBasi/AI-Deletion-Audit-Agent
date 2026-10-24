"""Reconstruct a resource's state at audit_at from the evidence (never from workflow claims).

Only files captured at or before audit_at are read. Observations made before the deletion
request describe the pre-deletion world: they can link a resource to a person but cannot prove
that it survived the deletion. The latest post-request observation of each copy decides; any
violating copy makes the resource non-compliant.
"""
from .policy import ANONYMIZE, BLOCK, DELETE, DISABLE, REDACT, RETAIN


def violates(o, treatment):
    if treatment is DELETE:
        return o.present
    if treatment is REDACT:
        return o.present and o.pii
    if treatment is ANONYMIZE:
        return o.present and (o.pii or o.attrs.get("ref", False))
    if treatment is BLOCK:
        return o.present and o.attrs.get("restore_allowed") is not False   # unblocked = restorable
    if treatment is DISABLE:
        return o.present and o.attrs.get("enabled") is True
    return False


def proves_compliance(o, treatment):
    if treatment in (DELETE,):
        return not o.present
    if treatment is RETAIN:
        return o.present
    if treatment is BLOCK:
        return o.present and o.attrs.get("restore_allowed") is False
    if treatment is DISABLE:
        return o.present and o.attrs.get("enabled") is False
    return o.present and not violates(o, treatment)


def _key(o):
    return o.observed_at, o.captured_at, o.evidence_id


def resolve(request, treatment, observations, coverages):
    """Return (violated, evidence_id). evidence_id is the proof of the reported state ('' if none)."""
    usable = [o for o in observations if o.captured_at <= request.audit_at]
    current = [o for o in usable if o.observed_at > request.requested_at]
    latest = {}
    for o in sorted(current, key=_key):
        latest[o.copy] = o
    bad = [o for o in latest.values() if violates(o, treatment)]
    if bad:
        return True, max(bad, key=_key).evidence_id
    proof = [o for o in usable if proves_compliance(o, treatment)]
    if proof:
        return False, max(proof, key=_key).evidence_id
    if treatment is DELETE and coverages:
        usable_cov = [c for c in coverages if c.captured_at <= request.audit_at]
        if usable_cov:
            return False, max(usable_cov, key=lambda c: (c.observed_at, c.captured_at)).evidence_id
    return False, ""
