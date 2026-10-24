"""Policy layer: maps resources to deletion_rules.csv categories and produces steps.

The category of a resource decides its required final state:
  DELETE    -> ABSENT       (USER_PROFILE, SECONDARY_IDENTIFIER, SEARCH_INDEX_ENTRY, CACHE_ENTRY,
                             SUPPORT_ATTACHMENT, USER_EXPORT, MARKETING_PROFILE)
  REDACT    -> REDACTED     (SUPPORT_MESSAGE, EMAIL_DELIVERY_LOG, ACCOUNT_TOMBSTONE)
  ANONYMIZE -> ANONYMIZED   (ANALYTICS_PROFILE, PRODUCT_TELEMETRY)
  RETAIN    -> PRESENT      (SECURITY_AUDIT_LOG, LEGAL_HOLD_RECORD)
  BACKUP_ARCHIVE is retained but its restore path must be BLOCKED; active data flows must be DISABLED.
"""
from __future__ import annotations

ACTION_TARGET = {"DELETE": "ABSENT", "REDACT": "REDACTED", "ANONYMIZE": "ANONYMIZED", "RETAIN": "PRESENT"}

FINDING_ORDER = ["REPORT_REMAINING_RECORD", "REPORT_EXPOSED_PII", "REPORT_ACTIVE_DATA_FLOW", "REPORT_RESTORE_RISK"]
REMEDIATION_ORDER = ["DISABLE_DATA_FLOW", "BLOCK_RESTORE", "DELETE_RECORD", "REDACT_RECORD", "ANONYMIZE_RECORD"]
VERIFICATION_ORDER = ["VERIFY_RECORD_ABSENT", "VERIFY_RECORD_PRESENT", "VERIFY_RECORD_REDACTED",
                      "VERIFY_RECORD_ANONYMIZED", "VERIFY_DATA_FLOW_DISABLED", "VERIFY_RESTORE_BLOCKED"]

# target state -> (finding op, remediation op, verification op)
TARGET_OPS = {
    "ABSENT": ("REPORT_REMAINING_RECORD", "DELETE_RECORD", "VERIFY_RECORD_ABSENT"),
    "REDACTED": ("REPORT_EXPOSED_PII", "REDACT_RECORD", "VERIFY_RECORD_REDACTED"),
    "ANONYMIZED": ("REPORT_EXPOSED_PII", "ANONYMIZE_RECORD", "VERIFY_RECORD_ANONYMIZED"),
    "DISABLED": ("REPORT_ACTIVE_DATA_FLOW", "DISABLE_DATA_FLOW", "VERIFY_DATA_FLOW_DISABLED"),
    "BLOCKED": ("REPORT_RESTORE_RISK", "BLOCK_RESTORE", "VERIFY_RESTORE_BLOCKED"),
    "PRESENT": (None, None, "VERIFY_RECORD_PRESENT"),
}
OP_STATE = {
    "REPORT_REMAINING_RECORD": "PRESENT", "REPORT_EXPOSED_PII": "PRESENT",
    "REPORT_ACTIVE_DATA_FLOW": "ACTIVE", "REPORT_RESTORE_RISK": "ACTIVE",
    "DISABLE_DATA_FLOW": "DISABLED", "BLOCK_RESTORE": "BLOCKED", "DELETE_RECORD": "ABSENT",
    "REDACT_RECORD": "REDACTED", "ANONYMIZE_RECORD": "ANONYMIZED",
    "VERIFY_RECORD_ABSENT": "ABSENT", "VERIFY_RECORD_PRESENT": "PRESENT", "VERIFY_RECORD_REDACTED": "REDACTED",
    "VERIFY_RECORD_ANONYMIZED": "ANONYMIZED", "VERIFY_DATA_FLOW_DISABLED": "DISABLED",
    "VERIFY_RESTORE_BLOCKED": "BLOCKED",
}


class Rules:
    """deletion_rules.csv indexed by data category."""

    def __init__(self, rows):
        self.by_category = {r["data_category"]: r for r in rows}

    def action(self, category: str) -> str:
        r = self.by_category.get(category)
        return r["required_action"] if r else "DELETE"

    def target(self, category: str) -> str:
        if category == "DATA_FLOW":
            return "DISABLED"
        if category == "BACKUP_ARCHIVE":
            return "BLOCKED"
        return ACTION_TARGET.get(self.action(category), "ABSENT")


def categorize(rid: str, hints: set, activity_types: set) -> str:
    """Decide the data category of one resource from its id, evidence hints and the
    policy operation the deletion workflow attempted on it."""
    h = {x.upper() for x in hints}
    if rid.startswith("account_"):
        return "USER_PROFILE"
    if rid.startswith("search_"):
        return "SEARCH_INDEX_ENTRY"
    if rid.startswith("cache_"):
        return "CACHE_ENTRY"
    if rid.startswith("export_"):
        return "USER_EXPORT"
    if rid.startswith("marketing_"):
        return "MARKETING_PROFILE"
    if rid.startswith("tombstone_"):
        return "ACCOUNT_TOMBSTONE"
    if rid.startswith("email_delivery_"):
        return "EMAIL_DELIVERY_LOG"
    if rid.startswith("audit_record_"):
        return "SECURITY_AUDIT_LOG"
    if rid.startswith("legal_hold_"):
        return "LEGAL_HOLD_RECORD"
    if rid.startswith("data_flow_"):
        return "DATA_FLOW"
    if rid.startswith("backup_"):
        return "BACKUP_ARCHIVE"
    if rid.startswith("support_"):
        if "SUPPORT_ATTACHMENT" in h or "ATTACHMENT" in h or "REPLICA_COPY" in h:
            return "SUPPORT_ATTACHMENT"
        if "SUPPORT_MESSAGE" in h:
            return "SUPPORT_MESSAGE"
        if "DELETE_RECORD" in activity_types and "REDACT_RECORD" not in activity_types:
            return "SUPPORT_ATTACHMENT"
        return "SUPPORT_MESSAGE"
    if rid.startswith("analytics_"):
        if "SECONDARY_IDENTIFIER" in h or "CONTACT_LOOKUP" in h:
            return "SECONDARY_IDENTIFIER"
        if "PRODUCT_TELEMETRY" in h:
            return "PRODUCT_TELEMETRY"
        if "DELETE_RECORD" in activity_types and "ANONYMIZE_RECORD" not in activity_types:
            return "SECONDARY_IDENTIFIER"
        return "ANALYTICS_PROFILE"
    return "USER_PROFILE"


def is_violation(target: str, exists, identifiable, active) -> bool:
    if target == "ABSENT":
        return exists is True
    if target in ("REDACTED", "ANONYMIZED"):
        return exists is not False and identifiable is True
    if target in ("DISABLED", "BLOCKED"):
        return active is True
    return False


def build_steps(items, complete: bool):
    """items: list of dicts {system_id, resource_id, target, violation, evidence_id}."""
    findings, remediation, verification = [], [], []
    for it in items:
        f_op, r_op, v_op = TARGET_OPS[it["target"]]
        if it["violation"] and f_op:
            findings.append((it, f_op))
            remediation.append((it, r_op))
        verification.append((it, v_op))

    def key(order):
        return lambda x: (order.index(x[1]), x[0]["system_id"], x[0]["resource_id"])

    steps = []
    if not complete:
        for it, op in sorted(findings, key=key(FINDING_ORDER)):
            steps.append(("FINDING", it, op, it.get("evidence_id", "")))
        for it, op in sorted(remediation, key=key(REMEDIATION_ORDER)):
            steps.append(("REMEDIATION", it, op, ""))
        for it, op in sorted(verification, key=key(VERIFICATION_ORDER)):
            steps.append(("VERIFICATION", it, op, ""))
    else:
        for it, op in sorted(verification, key=key(VERIFICATION_ORDER)):
            steps.append(("VERIFICATION", it, op, it.get("evidence_id", "")))
    out = []
    for i, (phase, it, op, ev) in enumerate(steps, 1):
        out.append({
            "step_no": i, "phase": phase, "system_id": it["system_id"], "resource_id": it["resource_id"],
            "operation": op, "state": OP_STATE[op], "evidence_id": ev or "",
        })
    return out
