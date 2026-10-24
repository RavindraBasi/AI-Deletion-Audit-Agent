"""deletion_rules.csv → what each relevant resource must look like after deletion.

A resource's data category comes from (1) the action the deletion workflow itself attempted
(the event type is policy intent even when its result is only a claim), (2) the category a
ledger states, (3) the owning system and record shape.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Treatment:
    finding: tuple | None        # (operation, state) reported when violated
    remediation: tuple | None    # (operation, state)
    verification: tuple          # (operation, state)


DELETE = Treatment(("REPORT_REMAINING_RECORD", "PRESENT"), ("DELETE_RECORD", "ABSENT"),
                   ("VERIFY_RECORD_ABSENT", "ABSENT"))
REDACT = Treatment(("REPORT_EXPOSED_PII", "PRESENT"), ("REDACT_RECORD", "REDACTED"),
                   ("VERIFY_RECORD_REDACTED", "REDACTED"))
ANONYMIZE = Treatment(("REPORT_EXPOSED_PII", "PRESENT"), ("ANONYMIZE_RECORD", "ANONYMIZED"),
                      ("VERIFY_RECORD_ANONYMIZED", "ANONYMIZED"))
RETAIN = Treatment(None, None, ("VERIFY_RECORD_PRESENT", "PRESENT"))
BLOCK = Treatment(("REPORT_RESTORE_RISK", "ACTIVE"), ("BLOCK_RESTORE", "BLOCKED"),
                  ("VERIFY_RESTORE_BLOCKED", "BLOCKED"))
DISABLE = Treatment(("REPORT_ACTIVE_DATA_FLOW", "ACTIVE"), ("DISABLE_DATA_FLOW", "DISABLED"),
                    ("VERIFY_DATA_FLOW_DISABLED", "DISABLED"))

ACTION_TREATMENT = {"DELETE": DELETE, "REDACT": REDACT, "ANONYMIZE": ANONYMIZE, "RETAIN": RETAIN}
SYSTEM_CATEGORY = {
    "account_service": "USER_PROFILE", "search_service": "SEARCH_INDEX_ENTRY", "cache_service": "CACHE_ENTRY",
    "export_service": "USER_EXPORT", "marketing_platform": "MARKETING_PROFILE",
    "object_storage": "SUPPORT_ATTACHMENT", "analytics_platform": "ANALYTICS_PROFILE",
    "support_platform": "SUPPORT_MESSAGE", "email_delivery_service": "EMAIL_DELIVERY_LOG",
    "security_service": "SECURITY_AUDIT_LOG", "legal_service": "LEGAL_HOLD_RECORD",
    "backup_service": "BACKUP_ARCHIVE", "identity_service": "ACCOUNT_TOMBSTONE",
}


class Policy:
    def __init__(self, rules):
        self.action = {r["data_category"]: r["required_action"] for r in rules}

    def category(self, system, observations, worker_ops):
        stated = [o.attrs["record_type"] for o in observations if o.attrs.get("record_type")]
        if system == "analytics_platform" and "DELETE_RECORD" in worker_ops:
            return "SECONDARY_IDENTIFIER"     # linked only through an identifier: delete, don't anonymise
        if system == "support_platform":
            if "DELETE_RECORD" in worker_ops or any(o.attrs.get("attachment") for o in observations):
                return "SUPPORT_ATTACHMENT"
            if "REDACT_RECORD" in worker_ops:
                return "SUPPORT_MESSAGE"
        if stated and stated[-1] in self.action:
            return stated[-1]
        return SYSTEM_CATEGORY.get(system, "SECONDARY_IDENTIFIER")

    def treatment(self, system, category):
        if system == "integration_service":
            return DISABLE
        if category == "BACKUP_ARCHIVE" or system == "backup_service":
            return BLOCK
        return ACTION_TREATMENT[self.action.get(category, "DELETE")]
