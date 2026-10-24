#!/usr/bin/env python3
"""Contract checks for output.csv (header, row count, ordering, step schema, evidence rules)."""
import csv
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PHASE_OPS = {
    "FINDING": {"REPORT_REMAINING_RECORD": "PRESENT", "REPORT_EXPOSED_PII": "PRESENT",
                "REPORT_ACTIVE_DATA_FLOW": "ACTIVE", "REPORT_RESTORE_RISK": "ACTIVE"},
    "REMEDIATION": {"DISABLE_DATA_FLOW": "DISABLED", "BLOCK_RESTORE": "BLOCKED", "DELETE_RECORD": "ABSENT",
                    "REDACT_RECORD": "REDACTED", "ANONYMIZE_RECORD": "ANONYMIZED"},
    "VERIFICATION": {"VERIFY_RECORD_ABSENT": "ABSENT", "VERIFY_RECORD_PRESENT": "PRESENT",
                     "VERIFY_RECORD_REDACTED": "REDACTED", "VERIFY_RECORD_ANONYMIZED": "ANONYMIZED",
                     "VERIFY_DATA_FLOW_DISABLED": "DISABLED", "VERIFY_RESTORE_BLOCKED": "BLOCKED"},
}
KEYS = {"step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"}


def validate(out_path, req_path, manifest_path):
    errs = []
    reqs = {r["case_id"]: r for r in csv.DictReader(open(req_path, encoding="utf-8"))}
    caps = {r["file_id"]: r["captured_at"] for r in csv.DictReader(open(manifest_path, encoding="utf-8"))}
    with open(out_path, encoding="utf-8", newline="") as fh:
        rd = csv.reader(fh)
        header = next(rd)
        rows = list(rd)
    if header != ["case_id", "deletion_status", "steps_json"]:
        errs.append(f"bad header {header}")
    ids = [r[0] for r in rows]
    if len(rows) != len(reqs):
        errs.append(f"row count {len(rows)} != {len(reqs)}")
    if ids != sorted(ids, key=lambda c: (len(c), c)):
        errs.append("rows not sorted by case_id")
    if set(ids) != set(reqs):
        errs.append("case ids differ from requests")
    for cid, status, sj in rows:
        steps = json.loads(sj)
        if status not in ("COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"):
            errs.append(f"{cid}: bad status {status}")
        if status == "NOT_APPLICABLE" and steps:
            errs.append(f"{cid}: NOT_APPLICABLE must have []")
        if status != "NOT_APPLICABLE" and not steps:
            errs.append(f"{cid}: empty steps")
        order = {"FINDING": 0, "REMEDIATION": 1, "VERIFICATION": 2}
        last = -1
        for i, s in enumerate(steps, 1):
            if set(s) != KEYS:
                errs.append(f"{cid}: step keys {sorted(s)}")
                continue
            if s["step_no"] != i:
                errs.append(f"{cid}: step_no {s['step_no']} != {i}")
            ops = PHASE_OPS.get(s["phase"], {})
            if ops.get(s["operation"]) != s["state"]:
                errs.append(f"{cid}: bad phase/op/state {s['phase']}/{s['operation']}/{s['state']}")
            if order.get(s["phase"], 9) < last:
                errs.append(f"{cid}: phase order")
            last = order.get(s["phase"], 9)
            ev = s["evidence_id"]
            needs_ev = s["phase"] == "FINDING" or (status == "COMPLETE")
            if status == "COMPLETE" and s["phase"] != "VERIFICATION":
                errs.append(f"{cid}: COMPLETE with non-verification step")
            if needs_ev:
                if ev.startswith("evidence_"):
                    if ev not in caps:
                        errs.append(f"{cid}: unknown evidence {ev}")
                    elif caps[ev] > reqs[cid]["audit_at"]:
                        errs.append(f"{cid}: evidence {ev} captured after audit_at")
                elif not ev:
                    errs.append(f"{cid}: missing evidence on step {i}")
            elif ev:
                errs.append(f"{cid}: future step {i} must have empty evidence_id")
    return errs


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "output.csv")
    req = sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, "dataset", "deletion_requests.csv")
    errs = validate(out, req, os.path.join(ROOT, "dataset", "evidence_files.csv"))
    for e in errs[:50]:
        print(e)
    print(f"{len(errs)} problem(s)")
    sys.exit(1 if errs else 0)
