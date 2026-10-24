"""Shared vocabulary: resource→system mapping and identifier tokens.

Identifier tokens are short typed strings so they can be compared as set members:
``e:<email>``, ``h:<sha256>``, ``p:<digits>``, ``u:<user_id>``, ``n:<name>``.
"""
import re

PREFIX_SYSTEM = {
    "account": "account_service", "search": "search_service", "analytics": "analytics_platform",
    "support": "support_platform", "export": "export_service", "data_flow": "integration_service",
    "backup": "backup_service", "audit_record": "security_service", "tombstone": "identity_service",
    "legal_hold": "legal_service", "cache": "cache_service", "marketing": "marketing_platform",
    "object": "object_storage", "email_delivery": "email_delivery_service",
}
RESOURCE_RE = re.compile(r"\b(" + "|".join(sorted(PREFIX_SYSTEM, key=len, reverse=True)) + r")_\d+\b")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE_RE = re.compile(r"\+\d[\d\s().-]{6,}\d")
USER_RE = re.compile(r"\buser_\d+\b")
HASH_RE = re.compile(r"\b[0-9a-f]{64}\b")

# Fields whose value is a direct identifier still stored in the record.
PII_FIELDS = {
    "email", "phone", "email_hash", "full_name", "name", "display_name", "requester_name",
    "requester_email", "requester_phone", "owner_email", "owner_phone", "customer_email", "uploaded_by",
}
# Fields that only point at a person (lookup keys, stable references).
LINK_FIELDS = {
    "lookup_key", "match_key", "correlation_key", "query", "lookup_value", "subject_reference",
    "customer_reference", "user_id", "subject_selector", "matched_value",
    "subject_ref", "subject_key", "owner_key", "scan_key",
}
REFERENCE_FIELDS = {"customer_reference", "subject_reference", "user_id", "subject_ref"}
NAME_FIELDS = {"full_name", "name", "display_name", "requester_name"}
NULLS = {None, "", "null", "none", "None", "NULL"}


def system_of(resource_id):
    m = RESOURCE_RE.fullmatch(resource_id or "")
    return PREFIX_SYSTEM[m.group(1)] if m else None


def token(value, field_name=""):
    """Classify one raw value into an identifier token (or None)."""
    if not isinstance(value, str) or value in NULLS:
        return None
    v = value.strip()
    if "@" in v and EMAIL_RE.fullmatch(v):
        return "e:" + v.lower()
    if HASH_RE.fullmatch(v):
        return "h:" + v
    if USER_RE.fullmatch(v):
        return "u:" + v
    digits = re.sub(r"\D", "", v)
    if PHONE_RE.fullmatch(v) and len(digits) >= 8:
        return "p:" + digits
    if field_name in NAME_FIELDS:
        return "n:" + " ".join(v.lower().split())
    return None


def text_tokens(text):
    """Identifiers embedded in free text (messages, PDFs, OCR)."""
    found = {"e:" + m.lower() for m in EMAIL_RE.findall(text)}
    found |= {"p:" + re.sub(r"\D", "", m) for m in PHONE_RE.findall(text) if len(re.sub(r"\D", "", m)) >= 8}
    found |= {"u:" + m for m in USER_RE.findall(text)}
    found |= {"h:" + m for m in HASH_RE.findall(text)}
    return found


def record_tokens(record):
    """Split a flat record into (link tokens, has_visible_pii, has_user_reference)."""
    links, pii, ref = set(), False, False
    for key, value in record.items():
        if isinstance(value, dict):
            sub_links, sub_pii, sub_ref = record_tokens(value)
            links |= sub_links
            pii, ref = pii or sub_pii, ref or sub_ref
            continue
        if key in ("message_text", "message_excerpt") and isinstance(value, str):
            embedded = text_tokens(value) - {t for t in text_tokens(value) if t.startswith("u:")}
            links |= embedded
            pii = pii or bool(embedded)
            continue
        tok = token(value, key)
        if tok is None:
            continue
        if key in PII_FIELDS:
            links.add(tok)
            pii = True
        elif key in LINK_FIELDS:
            links.add(tok)
            ref = ref or (tok.startswith("u:") and key in REFERENCE_FIELDS)
    return links, pii, ref


def flag(value):
    """Parse loose booleans used across evidence formats."""
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    return str(value).strip().lower() in ("1", "true", "yes", "enabled", "active")
