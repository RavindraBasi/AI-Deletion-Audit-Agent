"""Request intent: does request_text authorise an account/personal-data deletion?

Deterministic multilingual rules decide confidently-classifiable texts. Texts that
match neither the deletion nor the non-deletion patterns (or match both) are sent
to an optional LLM classifier (see llm.py) when an API key is configured; without
one the rule-based fallback is used.
"""
from __future__ import annotations

import re

# Explicit non-deletion signals (access, correction, export, opt-out, support...)
NON_DELETION = [
    r"\bnot a request to (erase|delete|remove)",
    r"\bnot asking for (deletion|erasure|removal)",
    r"\b(don't|do not|dont)\s+(delete|erase|remove|close)",
    r"\bmarketing lists? only\b",
    r"\bstop (the )?(promotional|marketing) (emails?|messages?)",
    r"\bunsubscribe\b",
    r"\b(like to download|download my|portable format|copy of (the|my) (information|data)|send me a copy)\b",
    r"\bwhat personal information you hold\b",
    r"\b(misspelled|out of date|update my|correct my|change the email|change my email)\b",
    r"\b(reset(ting)? the login|can't access my profile|cannot access my profile)\b",
    r"\bcopy of last year's invoices\b",
]
# Deletion signals in the languages present in the dataset
DELETION = [
    r"\b(delete|deleted|deletion|deleting|erase|erased|erasure|remove|removed|"
    r"close (my|this|the) account|closed my account|account closure|close the account|"
    r"profile is gone|no longer (exists|be identified)|cannot identify me)\b",
    r"suppression", r"supprim", r"eliminaci", r"eliminar", r"exclus", r"excluir", r"apagar",
    r"löschung", r"löschen", r"cancellazione", r"verwijder",
    r"삭제", r"削除", r"消去", r"删除", r"注销",
]


def rule_intent(text: str):
    """Return (label, confident) where label in {'DELETE','OTHER'}."""
    t = (text or "").lower()
    neg = any(re.search(p, t) for p in NON_DELETION)
    pos = any(re.search(p, t) for p in DELETION)
    if neg and not pos:
        return "OTHER", True
    if pos and not neg:
        return "DELETE", True
    if neg and pos:
        # explicit refusal of deletion wins ("not a request to erase it", "don't delete")
        return "OTHER", False
    return "OTHER", False


def classify(text: str, llm=None) -> tuple[str, str]:
    """Return (label, method)."""
    label, confident = rule_intent(text)
    if confident or llm is None:
        return label, "rules"
    res = llm.classify_intent(text)
    if res in ("DELETE", "OTHER"):
        return res, "llm"
    return label, "rules"
