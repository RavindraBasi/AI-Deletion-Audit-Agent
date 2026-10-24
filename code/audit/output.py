"""Write output.csv and enforce the submission contract."""
import csv
import json

from .config import OPERATION_ORDER, PHASES
from .dataset import case_number
from .planner import to_json_steps

STATES = {"PRESENT", "ACTIVE", "DISABLED", "BLOCKED", "ABSENT", "REDACTED", "ANONYMIZED"}


def serialize(steps):
    return json.dumps(to_json_steps(steps), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def write(results, path, case_ids):
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["case_id", "deletion_status", "steps_json"])
        for cid in sorted(case_ids, key=case_number):
            status, steps = results[cid]
            writer.writerow([cid, status, serialize(steps)])


def validate(path, requests, evidence_times):
    """Raise AssertionError on any contract violation; return the row count."""
    audit_at = {r.case_id: r.audit_at for r in requests}
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert [r["case_id"] for r in rows] == sorted(audit_at, key=case_number), "rows must cover every case, sorted"
    for row in rows:
        steps = json.loads(row["steps_json"])
        status = row["deletion_status"]
        assert status in ("COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"), row["case_id"]
        assert (status == "NOT_APPLICABLE") == (steps == []), row["case_id"]
        assert [s["step_no"] for s in steps] == list(range(1, len(steps) + 1)), row["case_id"]
        phases = [PHASES.index(s["phase"]) for s in steps]
        assert phases == sorted(phases), row["case_id"]
        for s in steps:
            assert s["operation"] in OPERATION_ORDER and s["state"] in STATES, row["case_id"]
            cited = s["evidence_id"]
            if status == "INCOMPLETE" and s["phase"] != "FINDING":
                assert cited == "", row["case_id"]
            if status == "COMPLETE" or s["phase"] == "FINDING":
                assert cited in evidence_times, (row["case_id"], cited)
                assert evidence_times[cited] <= audit_at[row["case_id"]], (row["case_id"], cited)
    return len(rows)
