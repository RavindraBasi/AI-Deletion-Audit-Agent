"""Loading of the tabular inputs: requests, identities, identity history, activity, rules."""
from __future__ import annotations

import csv
import hashlib
import os
import re
from dataclasses import dataclass, field

from .evidence import norm_key, system_for


def read_csv(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


@dataclass
class Identity:
    user_id: str
    emails: list = field(default_factory=list)      # current first, then historical
    phones: list = field(default_factory=list)
    names: list = field(default_factory=list)
    hashes: list = field(default_factory=list)
    audit_ids: list = field(default_factory=list)   # audit_log ids that established history

    def keys(self) -> set:
        ks = {self.user_id.lower()}
        for e in self.emails:
            ks.add(e.lower())
        for p in self.phones:
            k = norm_key(p)
            if k:
                ks.add(k)
        for h in self.hashes:
            ks.add(h.lower())
        return ks


def sha(email: str) -> str:
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()


class World:
    """All participant-facing tabular data, indexed for lookups."""

    def __init__(self, dataset_dir: str):
        d = dataset_dir
        self.dataset_dir = d
        self.identities_rows = read_csv(os.path.join(d, "user_identities.csv"))
        self.audit_rows = read_csv(os.path.join(d, "user_audit_log.csv"))
        self.activity = read_csv(os.path.join(d, "system_activity.csv"))
        self.rules = read_csv(os.path.join(d, "deletion_rules.csv"))
        self.users: dict[str, Identity] = {}
        for r in self.identities_rows:
            ident = Identity(r["user_id"], [r["email"]], [r["phone"]], [r["name"]],
                             [r["email_hash"]] if r["email_hash"] else [])
            self.users[r["user_id"]] = ident
        for r in sorted(self.audit_rows, key=lambda x: x["event_time"], reverse=True):
            ident = self.users.get(r["user_id"])
            if not ident:
                ident = self.users[r["user_id"]] = Identity(r["user_id"])
            f, old = r["field_name"], r["old_value"]
            ident.audit_ids.append(r["audit_id"])
            if f == "email" and old and old not in ident.emails:
                ident.emails.append(old)
            elif f == "phone" and old and old not in ident.phones:
                ident.phones.append(old)
            elif f in ("name", "full_name") and old and old not in ident.names:
                ident.names.append(old)
            elif f == "email_hash" and old and old not in ident.hashes:
                ident.hashes.append(old)
        for ident in self.users.values():
            for e in ident.emails:
                h = sha(e)
                if h not in ident.hashes:
                    ident.hashes.append(h)
        # email -> user_ids (current or historical)
        self.by_email: dict[str, set] = {}
        for ident in self.users.values():
            for e in ident.emails:
                self.by_email.setdefault(e.lower(), set()).add(ident.user_id)
        # key -> user ids (for attributing evidence to people)
        self.key_owner: dict[str, set] = {}
        for ident in self.users.values():
            for k in ident.keys():
                self.key_owner.setdefault(k, set()).add(ident.user_id)
        # data-catalog identity links in the activity stream
        self.catalog_links: dict[str, set] = {}
        email_re = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
        for a in self.activity:
            if a["event_type"] == "LINK_RESOURCE_IDENTITY":
                keys = {e.lower() for e in email_re.findall(a["details"])}
                keys |= {u.lower() for u in re.findall(r"\buser_\d+\b", a["details"])}
                self.catalog_links.setdefault(a["resource_id"], set()).update(keys)
        self.activity_by_resource: dict[str, list] = {}
        for a in self.activity:
            self.activity_by_resource.setdefault(a["resource_id"], []).append(a)

    def resolve(self, email: str) -> list[Identity]:
        ids = self.by_email.get(email.strip().lower(), set())
        return [self.users[u] for u in sorted(ids)]
