from typing import Dict, List, Optional, Set, Any
from dataclasses import dataclass, field
from pathlib import Path
import pandas as pd

from code import config


@dataclass
class UserIdentity:
    user_id: Optional[str] = None
    names: Set[str] = field(default_factory=set)
    emails: Set[str] = field(default_factory=set)
    phones: Set[str] = field(default_factory=set)
    email_hashes: Set[str] = field(default_factory=set)
    other_identifiers: Set[str] = field(default_factory=set)
    is_tombstone: bool = False

    def all_identifiers(self) -> Set[str]:
        """Return all non-empty string identifiers associated with the user."""
        all_ids = set()
        if self.user_id:
            all_ids.add(str(self.user_id).strip())
        for collection in (self.names, self.emails, self.phones, self.email_hashes, self.other_identifiers):
            for val in collection:
                if val and str(val).strip():
                    all_ids.add(str(val).strip())
        return all_ids

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "names": sorted(list(self.names)),
            "emails": sorted(list(self.emails)),
            "phones": sorted(list(self.phones)),
            "email_hashes": sorted(list(self.email_hashes)),
            "other_identifiers": sorted(list(self.other_identifiers)),
            "is_tombstone": self.is_tombstone,
            "all_identifiers": sorted(list(self.all_identifiers())),
        }


class IdentifierResolver:
    def __init__(
        self,
        identities_path: Optional[Path] = None,
        audit_log_path: Optional[Path] = None,
    ):
        self.identities_path = identities_path or config.USER_IDENTITIES_CSV
        self.audit_log_path = audit_log_path or config.USER_AUDIT_LOG_CSV
        self._load_data()

    def _load_data(self) -> None:
        try:
            self.identities_df = pd.read_csv(self.identities_path)
        except Exception as e:
            self.identities_df = pd.DataFrame()

        try:
            self.audit_log_df = pd.read_csv(self.audit_log_path)
        except Exception as e:
            self.audit_log_df = pd.DataFrame()

    def resolve(self, initial_identifier: str) -> UserIdentity:
        """
        Reconstruct complete UserIdentity footprint given an initial identifier (email, user_id, phone, etc.).
        """
        target = str(initial_identifier).strip().lower() if initial_identifier else ""
        identity = UserIdentity()
        if not target:
            return identity

        # Search in current user identities snapshot
        matched_user_ids: Set[str] = set()

        if not self.identities_df.empty:
            for _, row in self.identities_df.iterrows():
                uid = str(row.get("user_id", "")).strip()
                email = str(row.get("email", "")).strip()
                phone = str(row.get("phone", "")).strip()
                email_hash = str(row.get("email_hash", "")).strip()

                if (
                    target == uid.lower()
                    or target == email.lower()
                    or target == phone.lower()
                    or target == email_hash.lower()
                ):
                    if uid:
                        matched_user_ids.add(uid)

        # Search in audit log
        if not self.audit_log_df.empty:
            for _, row in self.audit_log_df.iterrows():
                uid = str(row.get("user_id", "")).strip()
                old_val = str(row.get("old_value", "")).strip()
                new_val = str(row.get("new_value", "")).strip()

                if target == uid.lower() or target == old_val.lower() or target == new_val.lower():
                    if uid:
                        matched_user_ids.add(uid)

        # If initial_identifier itself looks like email or user_id or phone, add to target set
        if "@" in initial_identifier:
            identity.emails.add(initial_identifier.strip())
        else:
            identity.other_identifiers.add(initial_identifier.strip())

        # For matched user_ids, collect current identity & historical changes
        for uid in matched_user_ids:
            identity.user_id = uid

            # Get current identity row
            if not self.identities_df.empty:
                user_rows = self.identities_df[self.identities_df["user_id"] == uid]
                for _, row in user_rows.iterrows():
                    name = str(row.get("name", "")).strip()
                    email = str(row.get("email", "")).strip()
                    phone = str(row.get("phone", "")).strip()
                    email_hash = str(row.get("email_hash", "")).strip()

                    # Check for tombstone: user_id exists, but name/email/phone are empty/null/nan or '[REDACTED]'
                    if (
                        not email or email.lower() in ("nan", "none", "", "[redacted]")
                    ) and (
                        not phone or phone.lower() in ("nan", "none", "", "[redacted]")
                    ):
                        identity.is_tombstone = True

                    if name and name.lower() not in ("nan", "none", "[redacted]"):
                        identity.names.add(name)
                    if email and email.lower() not in ("nan", "none", "[redacted]"):
                        identity.emails.add(email)
                    if phone and phone.lower() not in ("nan", "none", "[redacted]"):
                        identity.phones.add(phone)
                    if email_hash and email_hash.lower() not in ("nan", "none", "[redacted]"):
                        identity.email_hashes.add(email_hash)

            # Get audit log historical values
            if not self.audit_log_df.empty:
                audit_rows = self.audit_log_df[self.audit_log_df["user_id"] == uid]
                for _, row in audit_rows.iterrows():
                    field_name = str(row.get("field_name", "")).strip().lower()
                    old_val = str(row.get("old_value", "")).strip()
                    new_val = str(row.get("new_value", "")).strip()

                    for val in (old_val, new_val):
                        if not val or val.lower() in ("nan", "none", "", "[redacted]"):
                            continue
                        if field_name == "email":
                            identity.emails.add(val)
                        elif field_name == "phone":
                            identity.phones.add(val)
                        elif field_name == "email_hash":
                            identity.email_hashes.add(val)
                        else:
                            identity.other_identifiers.add(val)

        return identity
