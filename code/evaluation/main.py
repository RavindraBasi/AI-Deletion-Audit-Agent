"""Score the agent against the public samples (and the custom scenario cases).

    python3 code/evaluation/main.py            # print metrics
    python3 code/evaluation/main.py --write    # also refresh the metrics block in evaluation_report.md

Metrics
- status accuracy: deletion_status exact match per case
- step precision / recall / F1: multiset match of whole steps (phase, system, resource, operation,
  state, evidence_id); step_no is implied by order and scored by the sequence metrics
- field accuracy: steps aligned by position; share of positions where each field matches
- sequence: exact steps_json match per case, and mean normalised LCS of the ordered step tuples
"""
import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

CODE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE))

from audit import dataset  # noqa: E402
from audit.agent import DeletionAuditor  # noqa: E402
from audit.llm.client import LLM  # noqa: E402
from audit.planner import to_json_steps  # noqa: E402

FIELDS = ["step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"]
REPORT = Path(__file__).resolve().parent / "evaluation_report.md"


def step_key(s):
    return tuple(s[f] for f in FIELDS[1:])


def lcs(a, b):
    table = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i, x in enumerate(a):
        for j, y in enumerate(b):
            table[i + 1][j + 1] = table[i][j] + 1 if x == y else max(table[i][j + 1], table[i + 1][j])
    return table[-1][-1]


def score(pairs):
    """pairs: [(case_id, (status, steps), (status, steps))] → metrics dict."""
    m = Counter()
    field_hits, positions = Counter(), 0
    for _, (ps, psteps), (es, esteps) in pairs:
        m["cases"] += 1
        m["status"] += ps == es
        m["exact"] += psteps == esteps
        pk, ek = Counter(map(step_key, psteps)), Counter(map(step_key, esteps))
        m["tp"] += sum((pk & ek).values())
        m["pred"] += len(psteps)
        m["gold"] += len(esteps)
        a, b = [step_key(s) for s in psteps], [step_key(s) for s in esteps]
        m["lcs"] += lcs(a, b) / max(len(a), len(b)) if (a or b) else 1.0
        for i in range(max(len(psteps), len(esteps))):
            positions += 1
            for f in FIELDS:
                field_hits[f] += i < len(psteps) and i < len(esteps) and psteps[i][f] == esteps[i][f]
    p = m["tp"] / m["pred"] if m["pred"] else 1.0
    r = m["tp"] / m["gold"] if m["gold"] else 1.0
    return {"cases": m["cases"], "status_acc": m["status"] / m["cases"], "precision": p, "recall": r,
            "f1": 2 * p * r / (p + r) if p + r else 0.0, "exact_seq": m["exact"] / m["cases"],
            "lcs": m["lcs"] / m["cases"], **{f"field_{f}": field_hits[f] / max(positions, 1) for f in FIELDS}}


def table(slices):
    head = ["Slice", "Cases", "Status acc.", "Step P", "Step R", "Step F1", "Exact sequence", "Mean LCS"]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for name, m in slices:
        lines.append(f"| {name} | {m['cases']} | {m['status_acc']:.1%} | {m['precision']:.1%} | {m['recall']:.1%} | "
                     f"{m['f1']:.1%} | {m['exact_seq']:.1%} | {m['lcs']:.1%} |")
    lines += ["", "| Field (position-aligned) | " + " | ".join(n for n, _ in slices) + " |",
              "|---|" + "---|" * len(slices)]
    for f in FIELDS:
        lines.append(f"| `{f}` | " + " | ".join(f"{m['field_' + f]:.1%}" for _, m in slices) + " |")
    return "\n".join(lines)


def expected():
    with open(dataset.DATASET / "sample_output.csv", encoding="utf-8") as f:
        return {r["case_id"]: (r["deletion_status"], json.loads(r["steps_json"])) for r in csv.DictReader(f)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true", help="print every mismatching case")
    args = parser.parse_args()
    requests = dataset.load_requests() + dataset.load_requests("sample_deletion_requests.csv")
    results = DeletionAuditor(LLM(offline=args.offline)).run(requests)
    gold = expected()
    pairs = [(cid, (results[cid][0], to_json_steps(results[cid][1])), gold[cid]) for cid in sorted(gold)]
    slices = [("All samples", score(pairs)),
              ("case_01–10 (single-artifact)", score([p for p in pairs if dataset.case_number(p[0]) <= 10])),
              ("case_11–32 (generated)", score([p for p in pairs if dataset.case_number(p[0]) > 10]))]
    from custom_cases import run_custom   # noqa: E402  (evaluation/ is on sys.path via this file)
    custom = run_custom()
    if custom:
        slices.append((f"Custom scenarios ({len(custom)})", score(custom)))
    text = table(slices)
    print(text)
    if args.verbose:
        for cid, (ps, psteps), (es, esteps) in pairs + custom:
            if (ps, psteps) != (es, esteps):
                pk, ek = Counter(map(step_key, psteps)), Counter(map(step_key, esteps))
                print(f"\n{cid}: predicted {ps}, expected {es}")
                for k in sorted((ek - pk).elements()):
                    print("   missing", k)
                for k in sorted((pk - ek).elements()):
                    print("   extra  ", k)
    if args.write:
        body = REPORT.read_text(encoding="utf-8")
        start, end = "<!-- metrics:start -->", "<!-- metrics:end -->"
        head, _, rest = body.partition(start)
        _, _, tail = rest.partition(end)
        REPORT.write_text(f"{head}{start}\n{text}\n{end}{tail}", encoding="utf-8")


if __name__ == "__main__":
    main()
