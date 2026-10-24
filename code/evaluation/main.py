#!/usr/bin/env python3
"""Lightweight validation and participant-created tests for the deletion audit agent.

Run from the repository root with: python3 code/evaluation/main.py
Add --sample to compare the engine against the 32 public sample answers.
"""
from __future__ import annotations
import argparse
import collections
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAIN_PATH = ROOT / "code" / "main.py"
spec = importlib.util.spec_from_file_location("ghost_audit", MAIN_PATH)
engine = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = engine
spec.loader.exec_module(engine)


def custom_tests():
    tests = [
        ("explicit deletion", engine.request_is_deletion("Please permanently delete my account and personal data."), True),
        ("erase synonym", engine.request_is_deletion("Please erase the account linked to this address."), True),
        ("marketing opt-out is not deletion", engine.request_is_deletion("Please stop the promotional emails. I am not asking for deletion."), False),
        ("correction without deletion", engine.request_is_deletion("My surname is wrong. Please correct my personal data without removing records."), False),
        ("access request is not deletion", engine.request_is_deletion("Please send me a copy of the information held on my account; do not delete anything."), False),
        ("deletion verification request", engine.request_is_deletion("Please verify that my account has been removed."), True),
    ]
    failures = [(name, got, want) for name, got, want in tests if got != want]

    sample_steps = [
        engine.step("VERIFY_RECORD_PRESENT", "VERIFICATION", "security_service", "audit_record_2", "PRESENT", "evidence_1"),
        engine.step("REPORT_REMAINING_RECORD", "FINDING", "search_service", "search_2", "PRESENT", "evidence_2"),
        engine.step("REPORT_ACTIVE_DATA_FLOW", "FINDING", "integration_service", "data_flow_1", "ACTIVE", "evidence_3"),
    ]
    ordered = engine.sort_steps(sample_steps[1:], "FINDING")
    if [s["operation"] for s in ordered] != ["REPORT_REMAINING_RECORD", "REPORT_ACTIVE_DATA_FLOW"]:
        failures.append(("finding operation order", [s["operation"] for s in ordered], ["REPORT_REMAINING_RECORD", "REPORT_ACTIVE_DATA_FLOW"]))
    ordered_verification = engine.sort_steps(sample_steps[:1], "VERIFICATION")
    if ordered_verification[0]["phase"] != "VERIFICATION":
        failures.append(("verification phase sort", ordered_verification[0]["phase"], "VERIFICATION"))
    return len(tests) + 2, failures


def sample_benchmark():
    manifest = engine.build_manifest()
    engine.load_policy_rules()
    identities = engine.read_csv(engine.DATA / "user_identities.csv")
    audits = engine.read_csv(engine.DATA / "user_audit_log.csv")
    activities = engine.read_csv(engine.DATA / "system_activity.csv")
    observations = engine.extract_all_evidence(manifest)
    requests = engine.read_csv(engine.DATA / "sample_deletion_requests.csv")
    gold = {r["case_id"]: r for r in engine.read_csv(engine.DATA / "sample_output.csv")}
    status_correct = 0
    exact_rows = 0
    gold_step_count = 0
    field_matches = 0
    field_total = 0
    sequence_correct = 0
    gold_tuples = []
    pred_tuples = []
    for req in requests:
        cid, status, steps_json = engine.make_case_output(req, identities, audits, observations, activities)
        predicted_steps = json.loads(steps_json)
        expected = gold[cid]
        expected_steps = json.loads(expected["steps_json"])
        status_correct += status == expected["deletion_status"]
        exact_rows += status == expected["deletion_status"] and predicted_steps == expected_steps
        gold_step_count += len(expected_steps)
        gold_seq = [(s["phase"], s["operation"], s["system_id"], s["resource_id"], s["state"]) for s in expected_steps]
        pred_seq = [(s["phase"], s["operation"], s["system_id"], s["resource_id"], s["state"]) for s in predicted_steps]
        sequence_correct += gold_seq == pred_seq
        for i in range(max(len(expected_steps), len(predicted_steps))):
            gs = expected_steps[i] if i < len(expected_steps) else {}
            ps = predicted_steps[i] if i < len(predicted_steps) else {}
            for key in ("step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"):
                field_total += 1
                field_matches += i < len(expected_steps) and i < len(predicted_steps) and gs.get(key) == ps.get(key)
        gold_tuples.extend(tuple(s.get(k) for k in ("phase", "system_id", "resource_id", "operation", "state", "evidence_id")) for s in expected_steps)
        pred_tuples.extend(tuple(s.get(k) for k in ("phase", "system_id", "resource_id", "operation", "state", "evidence_id")) for s in predicted_steps)
    gc = collections.Counter(gold_tuples); pc = collections.Counter(pred_tuples)
    overlap = sum((gc & pc).values())
    precision = overlap / len(pred_tuples) if pred_tuples else 0.0
    recall = overlap / len(gold_tuples) if gold_tuples else 0.0
    f1 = 2*precision*recall/(precision+recall) if precision+recall else 0.0
    n = len(requests)
    return {
        "cases": n,
        "status_accuracy": status_correct/n if n else 0,
        "exact_row_accuracy": exact_rows/n if n else 0,
        "ordered_sequence_accuracy": sequence_correct/n if n else 0,
        "aligned_field_accuracy": field_matches/field_total if field_total else 0,
        "gold_steps": len(gold_tuples), "predicted_steps": len(pred_tuples),
        "exact_step_precision": precision, "exact_step_recall": recall, "exact_step_f1": f1,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", action="store_true", help="run the slower 32-case public sample benchmark")
    args = parser.parse_args()
    count, failures = custom_tests()
    print(f"Custom tests: {count-len(failures)}/{count} passed")
    for name, got, want in failures:
        print(f"FAIL {name}: got={got!r}, expected={want!r}")
    output = ROOT / "output.csv"
    if output.exists() and (engine.DATA / "deletion_requests.csv").exists():
        requests = engine.read_csv(engine.DATA / "deletion_requests.csv")
        validation = engine.validate_output(output, requests)
        print(f"Output contract: {'PASS' if not validation else 'FAIL'} ({len(requests)} requests)")
        for error in validation[:20]: print("ERROR", error)
    else:
        print("Output contract: skipped (root output.csv or dataset/deletion_requests.csv missing)")
    if args.sample:
        metrics = sample_benchmark()
        print("Public sample benchmark:")
        for key, value in metrics.items():
            print(f"  {key}: {value:.4f}" if isinstance(value, float) else f"  {key}: {value}")
    raise SystemExit(1 if failures else 0)

if __name__ == "__main__": main()
