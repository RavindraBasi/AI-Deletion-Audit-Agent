
import json
import sys
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "code"))

import main as engine


def main():
    manifest = engine.build_manifest()
    engine.load_policy_rules()

    identities = engine.read_csv(
        engine.DATA / "user_identities.csv"
    )
    audits = engine.read_csv(
        engine.DATA / "user_audit_log.csv"
    )
    activities = engine.read_csv(
        engine.DATA / "system_activity.csv"
    )
    observations = engine.extract_all_evidence(manifest)

    requests = engine.read_csv(
        engine.DATA / "sample_deletion_requests.csv"
    )
    gold_rows = engine.read_csv(
        engine.DATA / "sample_output.csv"
    )
    gold = {r["case_id"]: r for r in gold_rows}

    failures = []
    total_gold = 0
    total_predicted = 0
    missing_operations = Counter()
    extra_operations = Counter()

    for req in requests:
        cid, status, steps_json = engine.make_case_output(
            req, identities, audits, observations, activities
        )

        predicted = json.loads(steps_json)
        expected_row = gold[cid]
        expected = json.loads(expected_row["steps_json"])

        total_gold += len(expected)
        total_predicted += len(predicted)

        # Compare complete steps, including evidence IDs.
        expected_counter = Counter(
            json.dumps(s, sort_keys=True) for s in expected
        )
        predicted_counter = Counter(
            json.dumps(s, sort_keys=True) for s in predicted
        )

        missing = expected_counter - predicted_counter
        extra = predicted_counter - expected_counter

        for raw in missing:
            step = json.loads(raw)
            missing_operations[step["operation"]] += missing[raw]

        for raw in extra:
            step = json.loads(raw)
            extra_operations[step["operation"]] += extra[raw]

        if (
            status != expected_row["deletion_status"]
            or expected != predicted
        ):
            failures.append({
                "case_id": cid,
                "expected_status": expected_row["deletion_status"],
                "predicted_status": status,
                "expected_steps": len(expected),
                "predicted_steps": len(predicted),
                "missing": [
                    json.loads(s)
                    for s, count in missing.items()
                    for _ in range(count)
                ],
                "extra": [
                    json.loads(s)
                    for s, count in extra.items()
                    for _ in range(count)
                ],
            })

    print(f"Sample cases: {len(requests)}")
    print(f"Failed cases: {len(failures)}")
    print(f"Expected steps: {total_gold}")
    print(f"Predicted steps: {total_predicted}")
    print("\nMost frequently missing operations:")
    print(missing_operations.most_common(15))
    print("\nMost frequently extra operations:")
    print(extra_operations.most_common(15))

    print("\nFirst 10 failing cases:")
    for failure in failures[:10]:
        print(json.dumps(failure, indent=2))


if __name__ == "__main__":
    main()
