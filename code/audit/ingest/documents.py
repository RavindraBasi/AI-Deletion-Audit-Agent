"""Business documents: PDFs (pdftotext) and application screenshots (vision LLM)."""
import base64
import re
import subprocess
from datetime import datetime

from ..models import Observation
from .normalize import RESOURCE_RE, system_of, text_tokens, token

RETAIN_RE = re.compile(r"retain until\s+(\d{1,2} \w{3} \d{4})", re.I)


def pdf_text(path):
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True,
                          check=True).stdout


def parse_pdf(path, evidence_id, captured_at, aliases):
    """A PDF proves that every resource it names (directly or by business alias) still exists."""
    text = pdf_text(path)
    links = frozenset(text_tokens(text))
    named = {m.group(0) for m in RESOURCE_RE.finditer(text)}
    named |= {rid for alias, rid in aliases.items() if alias in text}
    retain = RETAIN_RE.search(text)
    attrs = {"ref": False, "document": True}
    if retain:
        attrs["retention_until"] = datetime.strptime(retain.group(1), "%d %b %Y").strftime("%Y-%m-%dT00:00:00Z")
    return [Observation(evidence_id, captured_at, captured_at, system_of(rid), rid, True, "primary",
                        links, True, dict(attrs, attachment=rid.startswith(("support_", "object_"))))
            for rid in sorted(named)], []


_ID = {"type": ["string", "null"]}
SCREENSHOT_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["records"],
    "properties": {"records": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["resource_id", "identifiers", "present", "enabled", "restore_allowed"],
        "properties": {
            "resource_id": {"type": "string"},
            "identifiers": {"type": "object", "additionalProperties": False,
                            "required": ["name", "email", "phone", "email_hash", "user_id"],
                            "properties": {k: _ID for k in ("name", "email", "phone", "email_hash", "user_id")}},
            "present": {"type": "boolean"},
            "enabled": {"type": ["boolean", "null"]},
            "restore_allowed": {"type": ["boolean", "null"]},
        }}}},
}


def image_extractor(llm):
    """Build the screenshot reader: one Sonnet vision call per image, cached by content hash."""
    from ..config import PROMPTS_DIR, VISION_MODEL
    system = (PROMPTS_DIR / "screenshot.md").read_text(encoding="utf-8")

    def extract(path):
        data = base64.standard_b64encode(path.read_bytes()).decode()
        content = [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}},
                   {"type": "text", "text": "Extract the records shown."}]
        answer = llm.json("screenshot extraction", VISION_MODEL, system, content, SCREENSHOT_SCHEMA,
                          max_tokens=4096)
        return (answer or {}).get("records", [])
    return extract


def parse_image(path, evidence_id, captured_at, extract):
    """Screenshots go through the vision extractor; each visible resource becomes an observation."""
    obs = []
    for rec in extract(path):
        rid = rec.get("resource_id", "")
        system = system_of(rid)
        if not system:
            continue
        ids = rec.get("identifiers") or {}
        links = {t for t in (token(ids.get(k), k) for k in ("email", "phone", "email_hash", "user_id")) if t}
        name = token(ids.get("name"), "name")
        pii = bool(links - {t for t in links if t.startswith("u:")}) or bool(name)
        if name:
            links.add(name)
        attrs = {"ref": bool(ids.get("user_id")), "screenshot": True}
        for key in ("enabled", "restore_allowed"):
            if rec.get(key) is not None:
                attrs[key] = rec[key]
        obs.append(Observation(evidence_id, captured_at, captured_at, system, rid, rec.get("present", True),
                               "primary", frozenset(links), pii, attrs))
    return obs, []
