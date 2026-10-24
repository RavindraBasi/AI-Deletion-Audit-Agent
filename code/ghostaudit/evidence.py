"""Evidence extraction: turn every supplied evidence file into normalised observations.

An Observation is one statement, made by one evidence file at one point in time,
about one resource: whether it exists, whether direct identifiers are still
visible, whether a data flow / restore path is active, and which identifiers
(emails, user ids, hashes, phones, names) link it to a person.

Supported formats: SQLite-compatible SQL snapshots (inventory tables, generic
record_json replicas, profile replicas, search primaries), JSON exports/bundles/
record lists, plain-text handovers, service logs (via logparse), PDFs (pdftotext)
and PNG screenshots (OCR via tesseract, optional LLM vision fallback).
"""
from __future__ import annotations

import csv
import json
import os
import re
import sqlite3
import subprocess
from dataclasses import dataclass, field

from . import logparse

PREFIX_SYSTEM = [
    ("audit_record_", "security_service"),
    ("data_flow_", "integration_service"),
    ("email_delivery_", "email_delivery_service"),
    ("legal_hold_", "legal_service"),
    ("tombstone_", "identity_service"),
    ("marketing_", "marketing_platform"),
    ("analytics_", "analytics_platform"),
    ("account_", "account_service"),
    ("search_", "search_service"),
    ("support_", "support_platform"),
    ("export_", "export_service"),
    ("backup_", "backup_service"),
    ("cache_", "cache_service"),
    ("object_", "object_storage"),
]

RID_RE = re.compile(
    r"\b(?:audit_record|data_flow|email_delivery|legal_hold|tombstone|marketing|analytics|account|search|support|export|backup|cache|object)_\d+\b"
)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
USER_RE = re.compile(r"\buser_\d+\b")
HASH_RE = re.compile(r"\b[0-9a-f]{64}\b")

NULLS = {None, "", "null", "None", "NULL", "[REDACTED]", "no direct identifier"}

# fields that carry a direct identifier of a person (presence => identifiable)
DIRECT_FIELDS = {
    "email", "phone", "full_name", "name", "display_name", "email_hash",
    "requester_email", "requester_name", "requester_phone", "owner_email", "owner_phone",
    "customer_email", "matched_value",
}
# fields that only carry the stable internal reference
REFERENCE_FIELDS = {"customer_reference", "user_id", "subject_reference", "subject_ref"}
# fields that carry a link key (lookup) but say nothing about record content
LOOKUP_FIELDS = {
    "lookup_key", "subject_key", "scan_key", "match_key", "owner_key", "correlation_key",
    "subject_selector", "query",
}


def system_for(resource_id: str) -> str:
    for p, s in PREFIX_SYSTEM:
        if resource_id.startswith(p):
            return s
    return "unknown"


def norm_key(v) -> str | None:
    if v is None:
        return None
    v = str(v).strip()
    if v in NULLS:
        return None
    if "@" in v:
        return v.lower()
    if v.startswith("+"):
        return re.sub(r"\D", "", v)  # phone digits
    return v.lower()


@dataclass
class Observation:
    file_id: str
    captured_at: str          # file capture time (manifest)
    ts: str                   # time the statement refers to
    resource_id: str
    system: str
    source: str               # e.g. sql:customer_data_read_replica, log, json, txt
    exists: bool | None = None
    identifiable: bool | None = None      # direct identifiers visible in the record
    active: bool | None = None            # data flow enabled / restore allowed
    keys: set = field(default_factory=set)          # normalised link keys
    direct: dict = field(default_factory=dict)      # visible direct identifiers
    hints: set = field(default_factory=set)         # category hints
    raw: dict = field(default_factory=dict)

    def add_key(self, v):
        k = norm_key(v)
        if k:
            self.keys.add(k)


def _absorb(obs: Observation, rec: dict, prefix: str = ""):
    """Absorb identifier fields from a (possibly nested) record dict."""
    for k, v in rec.items():
        if isinstance(v, dict):
            _absorb(obs, v, prefix + k + ".")
            continue
        if isinstance(v, list):
            continue
        if k in DIRECT_FIELDS:
            if norm_key(v):
                obs.direct[k] = v
                obs.add_key(v)
        elif k in REFERENCE_FIELDS or k in LOOKUP_FIELDS:
            obs.add_key(v)


def _state_from_record(obs: Observation, rec: dict):
    """Derive exists/identifiable/active from the fields of a structured record."""
    flat = {}

    def walk(d, p=""):
        for k, v in d.items():
            if isinstance(v, dict):
                walk(v, p + k + ".")
            else:
                flat[k] = v
    walk(rec)
    if "status_code" in flat:
        obs.exists = str(flat["status_code"]) == "200"
    if "http_status" in flat:
        obs.exists = str(flat["http_status"]) == "200"
    if "rows" in flat and obs.exists is None:
        obs.exists = str(flat["rows"]) not in ("0",)
    if "profile" in rec and rec.get("profile") is None and "status_code" in rec:
        obs.exists = str(rec.get("status_code")) == "200"
    if "account_status" in flat:
        st = str(flat["account_status"]).upper()
        if obs.resource_id.startswith("tombstone_"):
            obs.exists = True
        else:
            obs.exists = st not in ("DELETED",)
            obs.raw["account_status"] = st
    for k in ("restore_allowed", "enabled", "download_available", "available"):
        if k in flat:
            val = str(flat[k]).lower() in ("true", "1")
            if k in ("restore_allowed", "enabled"):
                obs.active = val
                obs.exists = True
            else:
                obs.exists = val
    if "eligible" in flat:
        el = str(flat["eligible"]).lower() == "true"
        blk = str(flat.get("deletion_block", "false")).lower() == "true"
        obs.active = el and not blk
        obs.exists = True
    if "decision" in flat:
        obs.active = str(flat["decision"]).lower() == "allow"
        obs.exists = True
    if "result" in flat:
        r = str(flat["result"]).upper()
        if r == "HIT":
            obs.exists = True
        elif r == "MISS":
            obs.exists = False
    if "hits" in rec and isinstance(rec["hits"], dict):
        obs.exists = int(rec["hits"].get("total", 0) or 0) > 0
    if "direct_identifiers" in flat:
        obs.identifiable = str(flat["direct_identifiers"]) not in ("0",)
        obs.exists = True
    if "message_text" in flat and str(flat["message_text"]) == "[REDACTED]":
        obs.identifiable = False
        obs.exists = True
    if "access" in flat:
        obs.exists = True
    if "marker_found" in flat:
        obs.raw["marker_found"] = flat["marker_found"]
    if "email_opt_in" in flat:
        obs.hints.add("contact_lookup")


def _finalise(obs: Observation):
    if obs.direct:
        if obs.identifiable is None:
            obs.identifiable = True
        if obs.exists is None:
            obs.exists = True
    if obs.resource_id.startswith(("audit_record_", "legal_hold_")) and obs.exists is None:
        obs.exists = True
    return obs


# --------------------------------------------------------------------------- SQL
def parse_sql(path, file_id, captured_at):
    text = open(path, encoding="utf-8").read()
    con = sqlite3.connect(":memory:")
    try:
        con.executescript(text)
    except sqlite3.Error:
        # tolerate partial scripts
        pass
    cap = captured_at
    m = re.search(r"Captured at:\s*(\S+)", text)
    if m:
        cap = m.group(1)
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    obs = []
    scope = {}
    if "snapshot_scope" in tables:
        for sysname, t in con.execute("SELECT origin_system, captured_at FROM snapshot_scope"):
            scope[sysname] = t
    for t in tables:
        if t == "snapshot_scope":
            continue
        cur = con.execute(f'SELECT * FROM "{t}"')
        cols = [d[0] for d in cur.description]
        for row in cur.fetchall():
            rec = dict(zip(cols, row))
            if "record_json" in rec:
                try:
                    inner = json.loads(rec["record_json"])
                except json.JSONDecodeError:
                    inner = {}
                rid = rec["resource_id"]
                sysname = rec.get("origin_system") or system_for(rid)
                o = Observation(file_id, captured_at, rec.get("updated_at") or cap, rid, sysname,
                                f"sql:{t}")
                o.raw["collection"] = rec.get("collection_name")
                o.raw.update({k: v for k, v in inner.items() if not isinstance(v, (dict, list))})
                _absorb(o, inner)
                _state_from_record(o, inner)
                if o.exists is None:
                    o.exists = True
                if rid.startswith(("analytics_", "tombstone_", "support_")) and o.identifiable is None:
                    o.identifiable = bool(o.direct)
                if o.raw.get("collection") == "attachment_objects" or "attachment" in str(o.raw.get("collection", "")):
                    o.hints.add("attachment")
            else:
                rid = rec.get("resource_id")
                if not rid:
                    continue
                o = Observation(file_id, captured_at, rec.get("updated_at") or rec.get("indexed_at") or cap,
                                rid, system_for(rid), f"sql:{t}")
                o.raw.update(rec)
                _absorb(o, rec)
                _state_from_record(o, rec)
                if o.exists is None:
                    o.exists = True
                if rid.startswith(("analytics_", "tombstone_", "support_")) and o.identifiable is None:
                    o.identifiable = bool(o.direct)
                if rec.get("source_resource_id"):
                    o.raw["source_resource_id"] = rec["source_resource_id"]
            obs.append(_finalise(o))
    con.close()
    meta = {"scope": scope, "captured": cap, "tables": tables}
    return obs, meta


# -------------------------------------------------------------------------- JSON
def parse_json(path, file_id, captured_at):
    data = json.load(open(path, encoding="utf-8"))
    obs = []
    file_ts = data.get("exported_at") or data.get("captured_at") or captured_at
    scope = {}

    def visit(node, ctx_ts, ctx_system, ctx_collection):
        if isinstance(node, dict):
            ts = node.get("captured_at") or ctx_ts
            system = node.get("data_store") or ctx_system
            coll = node.get("collection") or ctx_collection
            if "data_store" in node and "records" in node:
                scope.setdefault(node["data_store"], []).append(ts)
            if "resource_id" in node and isinstance(node["resource_id"], str) and RID_RE.fullmatch(node["resource_id"]):
                rid = node["resource_id"]
                o = Observation(file_id, captured_at,
                                node.get("observed_at") or node.get("recorded_at") or ts or file_ts,
                                rid, node.get("system") or system_for(rid), "json")
                o.raw["collection"] = coll
                o.raw.update({k: v for k, v in node.items() if not isinstance(v, (dict, list))})
                if "record_type" in node and "result" in node:
                    _record_type_result(o, node["record_type"], node["result"], node.get("subject_reference"))
                else:
                    _absorb(o, node)
                    _state_from_record(o, node)
                    if o.exists is None:
                        o.exists = True
                    if rid.startswith(("analytics_", "tombstone_", "support_")) and o.identifiable is None:
                        o.identifiable = bool(o.direct)
                if "file_name" in node:
                    o.raw["file_name"] = node["file_name"]
                obs.append(_finalise(o))
                return
            for k, v in node.items():
                visit(v, ts, system, coll)
        elif isinstance(node, list):
            for x in node:
                visit(x, ctx_ts, ctx_system, ctx_collection)

    exports = data.get("service_exports")
    if isinstance(exports, dict):
        for sysname, colls in exports.items():
            scope.setdefault(sysname, []).append(file_ts)
            visit(colls, file_ts, sysname, None)
    else:
        visit(data, file_ts, None, data.get("collection"))
    return obs, {"scope": scope, "captured": file_ts}


RESULT_MAP = {
    "FOUND": dict(exists=True),
    "NOT_FOUND": dict(exists=False),
    "IDENTIFIABLE": dict(exists=True, identifiable=True),
    "ANONYMIZED": dict(exists=True, identifiable=False),
    "REDACTED": dict(exists=True, identifiable=False),
    "ACTIVE": dict(exists=True, active=True),
    "DISABLED": dict(exists=True, active=False),
    "BLOCKED": dict(exists=True, active=False),
    "RETAINED": dict(exists=True),
    "PRESENT": dict(exists=True),
    "ABSENT": dict(exists=False),
    "DELETED": dict(exists=False),
}


def _record_type_result(o: Observation, rtype, result, subject):
    o.raw["record_type"] = rtype
    o.raw["result"] = result
    for k, v in RESULT_MAP.get(str(result).upper(), {}).items():
        setattr(o, k, v)
    if rtype:
        o.hints.add(str(rtype).upper())
    o.add_key(subject)
    if subject and norm_key(subject) and "@" in str(subject) and o.identifiable is None and str(result).upper() == "FOUND":
        pass


# --------------------------------------------------------------------------- TXT
TXT_ROW = re.compile(r"^-\s*(\S+)\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|\s*(.+?)\s*$")


def parse_txt(path, file_id, captured_at):
    obs = []
    for line in open(path, encoding="utf-8"):
        m = TXT_ROW.match(line.strip())
        if not m:
            continue
        ts, system, rid, rtype, result, subject = m.groups()
        o = Observation(file_id, captured_at, ts, rid, system, "txt")
        _record_type_result(o, rtype, result, subject)
        obs.append(_finalise(o))
    return obs, {"scope": {}, "captured": captured_at}


# --------------------------------------------------------------------------- LOG
LINK_FIELDS = REFERENCE_FIELDS | LOOKUP_FIELDS | {"matched_value", "email", "customer_email", "owner_email", "requester_email"}


def parse_logfile(path, file_id, captured_at):
    recs = logparse.parse_log(path)
    traces: dict = {}
    order = []
    for i, r in enumerate(recs):
        t = r.get("trace") or ""
        if not (t.startswith("tr-") or t.startswith("rb-")):
            t = f"line-{i}"
        if t not in traces:
            traces[t] = []
            order.append(t)
        traces[t].append(r)
    obs = []
    for t in order:
        rs = traces[t]
        rids = []
        for r in rs:
            rid = r.get("resource_id")
            if rid and RID_RE.fullmatch(rid) and rid not in rids:
                rids.append(rid)
        if not rids:
            continue
        merged = {}
        for r in rs:
            merged.update({k: v for k, v in r.items()})
        events = [r.get("event", "") for r in rs]
        for rid in rids:
            last = [r for r in rs if r.get("resource_id") == rid][-1]
            o = Observation(file_id, captured_at, last["ts"], rid,
                            merged.get("system") if merged.get("system", "").endswith(("_service", "_platform")) else system_for(rid),
                            "log")
            o.raw.update({k: v for k, v in merged.items() if k not in ("ts",)})
            o.raw["events"] = events
            o.raw["trace"] = t
            if "record_type" in merged and "result" in merged:
                _record_type_result(o, merged["record_type"], merged["result"], merged.get("subject_reference"))
                obs.append(_finalise(o))
                continue
            for k, v in merged.items():
                if k in LINK_FIELDS:
                    o.add_key(v)
            for k, v in merged.items():
                if k in DIRECT_FIELDS and k != "matched_value" and norm_key(v):
                    o.direct[k] = v
            if "matched_value" in merged and norm_key(merged["matched_value"]):
                o.direct["matched_value"] = merged["matched_value"]
            _state_from_record(o, merged)
            ev = " ".join(events)
            if "attachment_pii_scan" in ev:
                o.hints.add("attachment")
                o.exists = True
                o.identifiable = True
            if "message_pii_scan" in ev or "analytics_identity_scan" in ev:
                o.exists = True
                o.identifiable = True
            if "contact_query" in ev or merged.get("source") in ("legacy_contact_index", "case_archive"):
                o.hints.add("replica_copy")
                if o.exists is None:
                    o.exists = str(merged.get("rows", "1")) != "0"
            if "contact_lookup_completed" in ev:
                o.hints.add("contact_lookup")
            if "connector_heartbeat" in ev or "connector_config" in ev:
                if "scheduler_registered" in merged and "enabled" in merged:
                    o.active = merged["enabled"].lower() == "true" and merged["scheduler_registered"].lower() == "true"
            if rid.startswith(("analytics_", "support_", "tombstone_")) and o.identifiable is None and o.exists:
                o.identifiable = bool(o.direct)
            if "export_download_check" in ev or "export_metadata" in ev:
                o.exists = str(merged.get("available", "true")).lower() == "true"
            obs.append(_finalise(o))
    return obs, {"scope": {}, "captured": captured_at}


# ----------------------------------------------------------------- PDF / IMAGE
def _pdf_text(path):
    try:
        return subprocess.run(["pdftotext", "-layout", path, "-"], capture_output=True, text=True, timeout=60).stdout
    except Exception:
        try:
            import pdfplumber
            with pdfplumber.open(path) as pdf:
                return "\n".join(p.extract_text() or "" for p in pdf.pages)
        except Exception:
            return ""


def _ocr_text(path, cache_dir=None):
    cache = None
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        cache = os.path.join(cache_dir, os.path.basename(path) + ".txt")
        if os.path.exists(cache):
            return open(cache, encoding="utf-8").read()
    txt = ""
    try:
        txt = subprocess.run(["tesseract", path, "-", "--psm", "3"], capture_output=True, text=True, timeout=120).stdout
    except Exception:
        txt = ""
    if cache:
        with open(cache, "w", encoding="utf-8") as fh:
            fh.write(txt)
    return txt


def fix_ocr_ids(text: str) -> str:
    """Repair common OCR damage in identifiers (e.g. 'data_flow.1121', 'search 1874')."""
    t = re.sub(r"\b(data_flow|audit_record|email_delivery|legal_hold)[\. ](\d+)", r"\1_\2", text)
    t = re.sub(r"\b(account|search|analytics|support|export|backup|tombstone|marketing|cache)[\. ](\d{2,6})\b", r"\1_\2", t)
    t = t.replace("@outiook.com", "@outlook.com").replace("@gmall.com", "@gmail.com")
    return t


def _preprocess_image(path, cache_dir=None):
    """Grayscale, invert dark-theme screens and upscale 2x - markedly better OCR on UI text."""
    try:
        import tempfile
        from PIL import Image, ImageOps
        im = Image.open(path).convert("L")
        small = im.resize((40, 40))
        mean = sum(small.tobytes()) / 1600
        if mean < 110:
            im = ImageOps.invert(im)
        im = im.resize((im.width * 2, im.height * 2))
        out_dir = cache_dir or tempfile.gettempdir()
        os.makedirs(out_dir, exist_ok=True)
        out = os.path.join(out_dir, os.path.basename(path) + ".prep.png")
        im.save(out)
        return out
    except Exception:
        return path


def _ocr_rows(path, cache_dir=None):
    """OCR an image into visual rows (words grouped by vertical position)."""
    cache = None
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        cache = os.path.join(cache_dir, os.path.basename(path) + ".tsv")
    if cache and os.path.exists(cache):
        tsv = open(cache, encoding="utf-8").read()
    else:
        src = _preprocess_image(path, cache_dir)
        try:
            tsv = subprocess.run(["tesseract", src, "-", "tsv"], capture_output=True, text=True, timeout=180).stdout
        except Exception:
            tsv = ""
        if cache:
            with open(cache, "w", encoding="utf-8") as fh:
                fh.write(tsv)
    words = []
    for r in csv.DictReader(tsv.splitlines(), delimiter="\t", quoting=csv.QUOTE_NONE):
        t = (r.get("text") or "").strip()
        if not t:
            continue
        try:
            words.append((int(r["top"]), int(r["left"]), t))
        except (TypeError, ValueError):
            continue
    words.sort()
    rows, cur, cur_top = [], [], None
    for top, left, t in words:
        if cur_top is None or abs(top - cur_top) <= 12:
            cur.append((left, t))
            cur_top = top if cur_top is None else cur_top
        else:
            rows.append(" ".join(w for _, w in sorted(cur)))
            cur, cur_top = [(left, t)], top
    if cur:
        rows.append(" ".join(w for _, w in sorted(cur)))
    return rows


def _alias_norm(t: str) -> str:
    t = t.lower().replace(".pdt", ".pdf").replace(".ipg", ".jpg")
    return re.sub(r"[^a-z0-9]", "", t)


ROW_ACTIVE = re.compile(r"\b(continuous|enabled|active|restore allowed|eligible|ready)\b", re.I)
ROW_INACTIVE = re.compile(r"\b(disabled|paused|stopped|blocked|expired|deleted)\b", re.I)


def parse_document(path, file_id, captured_at, kind, cache_dir=None, vision=None, aliases=None):
    """PDFs and screenshots: business artefacts. Identifiers are linked to the
    resource ids that appear on the same visual row (screens) or anywhere in the
    document when it is about a single resource (PDF, detail views)."""
    if kind == "pdf":
        text = _pdf_text(path)
        rows = text.splitlines()
    else:
        rows = _ocr_rows(path, cache_dir)
        if vision is not None:
            vt = vision(path)
            if vt:
                rows = rows + vt.splitlines()
        text = "\n".join(rows)
    rows = [fix_ocr_ids(r) for r in rows]
    text = fix_ocr_ids(text)
    all_rids = list(dict.fromkeys(RID_RE.findall(text)))
    # business artefacts often show a resource only through its business alias
    # (file name, statement number, customer key) known from the data catalog
    alias_rows = {}
    for rid, alias in (aliases or {}).items():
        if rid in all_rids or not alias or alias == rid:
            continue
        na = _alias_norm(alias)
        if len(na) < 6:
            continue
        hit = [r for r in rows if na in _alias_norm(r)]
        if hit:
            all_rids.append(rid)
            alias_rows[rid] = hit
    single = len(all_rids) == 1 or kind == "pdf"
    obs = []
    for rid in all_rids:
        o = Observation(file_id, captured_at, captured_at, rid, system_for(rid), kind)
        o.exists = True
        if rid in alias_rows:
            scope_rows = alias_rows[rid]
            o.hints.add("catalog_alias")
        else:
            scope_rows = [text] if single else [r for r in rows if rid in r]
        for r in scope_rows:
            for e in EMAIL_RE.findall(r):
                o.keys.add(e.lower())
                o.direct["email"] = e
            for u in USER_RE.findall(r):
                o.keys.add(u.lower())
            for p in re.findall(r"\+\d[\d \-()]{7,}\d", r):
                o.add_key(p)
                o.direct["phone"] = p
        row_text = " ".join(scope_rows)
        if rid.startswith(("data_flow_", "backup_")):
            if ROW_INACTIVE.search(row_text):
                o.active = False
            elif ROW_ACTIVE.search(row_text) or rid.startswith("backup_"):
                # an archive listed in a recovery console with no block marker is restorable
                o.active = True
        o.identifiable = bool(o.direct)
        if rid.startswith("object_") or (kind == "pdf" and rid.startswith("support_")):
            o.hints.add("attachment")
        obs.append(o)
    return obs, {"scope": {}, "captured": captured_at, "text": text}


# ----------------------------------------------------------------------- driver
def load_manifest(dataset_dir):
    rows = list(csv.DictReader(open(os.path.join(dataset_dir, "evidence_files.csv"), encoding="utf-8")))
    return {r["file_id"]: r for r in rows}


def catalog_aliases(dataset_dir):
    """resource_id -> business alias, from data-catalog link events in system_activity.csv."""
    out = {}
    path = os.path.join(dataset_dir, "system_activity.csv")
    with open(path, encoding="utf-8", newline="") as fh:
        for a in csv.DictReader(fh):
            if a["event_type"] == "LINK_RESOURCE_IDENTITY":
                m = re.search(re.escape(a["resource_id"]) + r" \(([^)]+)\)", a["details"])
                if m:
                    out[a["resource_id"]] = m.group(1)
    return out


def extract_all(dataset_dir, cache_dir=None, vision=None):
    manifest = load_manifest(dataset_dir)
    aliases = catalog_aliases(dataset_dir)
    all_obs = []
    metas = {}
    for fid, row in manifest.items():
        path = os.path.join(dataset_dir, row["file_path"])
        cap = row["captured_at"]
        ext = os.path.splitext(path)[1].lower()
        if not os.path.exists(path):
            continue
        try:
            if ext == ".sql":
                obs, meta = parse_sql(path, fid, cap)
            elif ext == ".json":
                obs, meta = parse_json(path, fid, cap)
            elif ext == ".txt":
                obs, meta = parse_txt(path, fid, cap)
            elif ext == ".log":
                obs, meta = parse_logfile(path, fid, cap)
            elif ext == ".pdf":
                obs, meta = parse_document(path, fid, cap, "pdf", cache_dir, aliases=aliases)
            elif ext in (".png", ".jpg", ".jpeg"):
                obs, meta = parse_document(path, fid, cap, "image", cache_dir, vision, aliases=aliases)
            else:
                continue
        except Exception as exc:  # never let one bad file kill the run
            obs, meta = [], {"error": repr(exc)}
        meta["path"] = row["file_path"]
        meta["captured_at"] = cap
        meta["origin"] = row["origin"]
        metas[fid] = meta
        all_obs.extend(obs)
    return all_obs, metas
