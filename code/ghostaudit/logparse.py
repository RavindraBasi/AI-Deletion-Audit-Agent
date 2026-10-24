"""Format-agnostic parser for the heterogeneous service logs in dataset/evidence/logs.

Every supported line shape (logfmt, JSON, syslog, pipe-delimited, bracketed, cloud
runtime with an escaped message payload) is normalised to a dict with at least
`ts`, `service`, `trace`, `event` plus all key/value pairs on the line.
Lines that share a trace id are later merged into one "trace" observation.
"""
from __future__ import annotations

import json
import re

TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\s+(.*)$")
KV_RE = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)=("(?:[^"\\]|\\.)*"|\S+)')


def _unquote(v: str) -> str:
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        v = v[1:-1].replace('\\"', '"')
    return v


def parse_kv(text: str) -> dict:
    out = {}
    for k, v in KV_RE.findall(text):
        out[k] = _unquote(v)
    return out


def parse_line(line: str) -> dict | None:
    m = TS_RE.match(line.rstrip("\n"))
    if not m:
        return None
    ts, rest = m.groups()
    rec: dict = {"ts": ts}
    rest = rest.strip()
    if rest.startswith("{"):
        try:
            data = json.loads(rest)
        except json.JSONDecodeError:
            return None
        rec.update({k: str(v) for k, v in data.items()})
        rec.setdefault("service", data.get("logger", ""))
        rec["trace"] = data.get("trace_id") or data.get("instance", "")
    elif rest.startswith("<"):  # syslog: <15>1 node service trace - - kv...
        parts = rest.split(None, 6)
        if len(parts) >= 4:
            rec["service"] = parts[2]
            rec["trace"] = parts[3]
        rec.update(parse_kv(parts[6] if len(parts) > 6 else ""))
    elif rest.startswith("["):  # [LEVEL] [service] [trace] event: kv
        m2 = re.match(r"\[(\w+)\]\s+\[([^\]]+)\]\s+\[([^\]]+)\]\s+([A-Za-z_]+):?\s*(.*)$", rest)
        if not m2:
            return None
        lvl, svc, trace, event, kv = m2.groups()
        rec.update({"level": lvl, "service": svc, "trace": trace, "event": event})
        rec.update(parse_kv(kv))
    elif rest.startswith("|"):  # | LEVEL | service | trace | event | kv
        parts = [p.strip() for p in rest.lstrip("|").split("|", 4)]
        if len(parts) >= 4:
            rec.update({"level": parts[0], "service": parts[1], "trace": parts[2], "event": parts[3]})
            rec.update(parse_kv(parts[4] if len(parts) > 4 else ""))
    else:
        kv = parse_kv(rest)
        if "message" in kv and "function" in kv:  # cloud runtime
            inner = parse_kv(kv["message"].replace('\\"', '"'))
            rec["service"] = kv.get("function", "")
            rec["trace"] = kv.get("requestId", "")
            rec["level"] = kv.get("level", "")
            rec.update(inner)
        else:
            rec.update(kv)
            rec.setdefault("trace", kv.get("trace_id", ""))
    rec.setdefault("service", rec.get("service", ""))
    rec.setdefault("trace", rec.get("trace_id", ""))
    return rec


def parse_log(path: str) -> list[dict]:
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            r = parse_line(line)
            if r:
                out.append(r)
    return out
