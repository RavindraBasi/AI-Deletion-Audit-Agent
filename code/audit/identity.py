"""Subject resolution: request email → user_id → every identifier the person ever held."""
import hashlib
from dataclasses import dataclass

from .ingest.normalize import token


@dataclass(frozen=True)
class Subject:
    user_id: str
    tokens: frozenset      # e:/h:/p:/u: tokens usable for linking (names never link on their own)
    names: frozenset


class IdentityIndex:
    def __init__(self, identities, audit_log):
        self._tokens, self._names, self._by_email = {}, {}, {}
        for row in identities:
            uid = row["user_id"]
            self._names[uid] = {token(row["name"], "name")}
            self._add(uid, token(row["email"]), token(row["phone"]), token(row["email_hash"]), "u:" + uid,
                      "h:" + hashlib.sha256(row["email"].strip().encode()).hexdigest())
            self._by_email[row["email"].strip().lower()] = uid
        for row in audit_log:
            uid = row["user_id"]
            for value in (row["old_value"], row["new_value"]):
                self._add(uid, token(value))
                if row["field_name"] == "email":
                    self._by_email.setdefault(value.strip().lower(), uid)
                    self._add(uid, "h:" + hashlib.sha256(value.strip().encode()).hexdigest())

    def _add(self, uid, *tokens):
        self._tokens.setdefault(uid, set()).update(t for t in tokens if t)

    def resolve(self, email):
        uid = self._by_email.get(email.strip().lower())
        if uid is None:
            return None
        return Subject(uid, frozenset(self._tokens[uid]), frozenset(self._names[uid]))
