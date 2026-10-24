"""Service logs in several layouts, normalised to key/value events and joined by trace id.

Layouts seen: plain ``k=v``, RFC-5424 syslog, JSON-lines, ``[LEVEL] [service] [trace]``,
``ts | LEVEL | service | trace | event | k=v`` and a cloud export whose ``message`` holds escaped k=v.
"""
import json
import re

from ..models import Observation
from .normalize import flag, record_tokens, system_of, token

KV_RE = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|\S+)')
SYSLOG_RE = re.compile(r"^<\d+>\d+ \S+ (?P<service>\S+) (?P<trace>\S+) - - (?P<rest>.*)$")
BRACKET_RE = re.compile(r"^\[(?P<level>\w+)\] \[(?P<service>[\w-]+)\] \[(?P<trace>[\w-]+)\] (?P<event>\w+): ?(?P<rest>.*)$")
PIPE_RE = re.compile(r"^\| (?P<level>\w+) \| (?P<service>[\w-]+) \| (?P<trace>[\w-]+) \| (?P<event>\w+) \| ?(?P<rest>.*)$")
LOOKUP_KEYS = ("lookup_key", "match_key", "correlation_key", "subject_reference", "lookup_value",
               "subject_ref", "subject_key", "owner_key", "scan_key")
ABSENT_MARKERS = {("rows", "0"), ("http_status", "404"), ("result", "NOT_FOUND"), ("result", "miss"),
                  ("total_hits", "0"), ("archive_present", "false"), ("available", "false")}


def _kv(text):
    out = {}
    for key, raw in KV_RE.findall(text):
        value = raw[1:-1].replace('\\"', '"') if raw.startswith('"') else raw
        if key == "message" and "=" in value:
            out.update(_kv(value))
        else:
            out[key] = value
    return out


def parse_line(line):
    ts, _, rest = line.partition(" ")
    if not ts[:4].isdigit():
        return None
    rest = rest.strip()
    if rest.startswith("{"):
        fields = json.loads(rest)
    elif (m := SYSLOG_RE.match(rest)):
        fields = {"service": m["service"], "trace_id": m["trace"], **_kv(m["rest"])}
    elif (m := BRACKET_RE.match(rest)) or (m := PIPE_RE.match(rest)):
        fields = {"service": m["service"], "trace_id": m["trace"], "event": m["event"], **_kv(m["rest"])}
    else:
        fields = _kv(rest)
        if "requestId" in fields:
            fields.setdefault("trace_id", fields["requestId"])
    fields["_ts"] = ts
    return fields


def parse(path, evidence_id, captured_at):
    events = [e for e in map(parse_line, path.read_text(encoding="utf-8").splitlines()) if e]
    keys_by_trace = {}
    for e in events:
        key = next((e[k] for k in LOOKUP_KEYS if e.get(k) not in (None, "null")), None)
        if key and e.get("trace_id"):
            keys_by_trace.setdefault(e["trace_id"], set()).add(key)
    obs = []
    for e in events:
        rid = e.get("resource_id")
        system = e.get("system") or system_of(rid)
        if not rid or not system or e.get("event", "").endswith("_started"):
            continue
        obs.append(_observation(e, system, keys_by_trace.get(e.get("trace_id"), set()), evidence_id, captured_at))
    return obs, []


def _observation(e, system, trace_keys, evidence_id, captured_at):
    event = e.get("event", "")
    if event == "record_lookup":     # ledger-style line: reuse the ledger semantics
        from .records import _ledger_obs
        return _ledger_obs({"recorded_at": e["_ts"], **e}, evidence_id, captured_at)
    payload = {k: v for k, v in e.items() if k not in ("resource_id", "_ts", "trace_id", "event", "service",
                                                        "system", "level", "severity", "logger")}
    links, pii, ref = record_tokens(payload)
    links |= {t for t in map(token, trace_keys) if t}
    if event.endswith("_pii_scan") or event.endswith("identity_scan"):
        pii = pii or e.get("matched_value") not in (None, "null")
    if e.get("direct_identifiers") == "0":
        pii = False
    present = not any(str(e.get(k)) == v for k, v in ABSENT_MARKERS)
    if event == "deletion_marker_check":
        present = flag(e.get("marker_found")) is False
    attrs = {"ref": ref, "event": event}
    for key in ("enabled", "restore_allowed"):
        if key in e:
            attrs[key] = flag(e[key])
    if "eligible" in e:   # restore_candidate_evaluated: restorable unless a deletion block exists
        attrs["restore_allowed"] = flag(e["eligible"]) and not flag(e.get("deletion_block"))
    if event == "restore_authorization_completed":
        attrs["restore_allowed"] = e.get("decision", "").lower() in ("allow", "allowed", "approve", "approved")
    if "attachment" in event or "file" in e:
        attrs["attachment"] = True
    if e.get("retention_until"):
        attrs["retention_until"] = e["retention_until"]
    return Observation(evidence_id, captured_at, min(e["_ts"], captured_at), system, e["resource_id"],
                       present, "primary", frozenset(links), pii, attrs)
