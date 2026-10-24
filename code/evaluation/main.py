#!/usr/bin/env python3
"""Offline evaluator: compare predictions against a reference answer file.

    python3 code/evaluation/main.py                       # samples: runs the agent, scores vs sample_output.csv
    python3 code/evaluation/main.py --pred P.csv --gold G.csv [--details]

Metrics
  status accuracy      deletion_status exact match per case
  exact case match     steps_json identical (as parsed JSON, order-sensitive)
  step P/R/F1          multiset match of full step tuples (phase, system, resource, op, state, evidence)
  step P/R/F1 (no ev)  same, ignoring evidence_id
  field accuracy       position-aligned per-field agreement over gold steps
  sequence accuracy    normalised LCS of (phase, resource_id, operation) sequences; and share of
                       cases whose sequence is identical
  resource recall      share of gold (system_id, resource_id) pairs discovered
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
FIELDS = ["phase", "system_id", "resource_id", "operation", "state", "evidence_id"]


def load(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return {r["case_id"]: (r["deletion_status"], json.loads(r["steps_json"])) for r in csv.DictReader(fh)}


def lcs(a, b):
    dp = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a)):
        for j in range(len(b)):
            dp[i + 1][j + 1] = dp[i][j] + 1 if a[i] == b[j] else max(dp[i][j + 1], dp[i + 1][j])
    return dp[-1][-1]


def score(pred, gold, details=False):
    n = len(gold)
    status_ok = exact = seq_exact = 0
    tp = fp = fn = tp2 = fp2 = fn2 = 0
    field_hits = Counter()
    field_total = 0
    lcs_sum = 0.0
    res_hit = res_tot = 0
    confusion = Counter()
    failures = []
    for cid, (gs, gsteps) in sorted(gold.items()):
        ps, psteps = pred.get(cid, ("MISSING", []))
        confusion[(gs, ps)] += 1
        status_ok += gs == ps
        strip = lambda s: {k: s[k] for k in ["step_no"] + FIELDS}
        if [strip(s) for s in psteps] == [strip(s) for s in gsteps]:
            exact += 1
        else:
            failures.append(cid)
        gt = Counter(tuple(s[f] for f in FIELDS) for s in gsteps)
        pt = Counter(tuple(s[f] for f in FIELDS) for s in psteps)
        inter = sum((gt & pt).values())
        tp += inter; fp += sum(pt.values()) - inter; fn += sum(gt.values()) - inter
        gt2 = Counter(tuple(s[f] for f in FIELDS[:-1]) for s in gsteps)
        pt2 = Counter(tuple(s[f] for f in FIELDS[:-1]) for s in psteps)
        inter2 = sum((gt2 & pt2).values())
        tp2 += inter2; fp2 += sum(pt2.values()) - inter2; fn2 += sum(gt2.values()) - inter2
        for i, g in enumerate(gsteps):
            field_total += 1
            p = psteps[i] if i < len(psteps) else {}
            for f in FIELDS:
                field_hits[f] += p.get(f) == g[f]
        ga = [(s["phase"], s["resource_id"], s["operation"]) for s in gsteps]
        pa = [(s["phase"], s["resource_id"], s["operation"]) for s in psteps]
        if not ga and not pa:
            lcs_sum += 1
            seq_exact += 1
        else:
            lcs_sum += lcs(ga, pa) / max(len(ga), len(pa))
            seq_exact += ga == pa
        gres = {(s["system_id"], s["resource_id"]) for s in gsteps}
        pres = {(s["system_id"], s["resource_id"]) for s in psteps}
        res_tot += len(gres); res_hit += len(gres & pres)
        if details and cid in failures:
            print(f"--- {cid} gold={gs} pred={ps}")
            for s in gsteps:
                t = tuple(s[f] for f in FIELDS)
                print("   G", "  " if pt[t] else "!!", t)
            for s in psteps:
                t = tuple(s[f] for f in FIELDS)
                if not gt[t]:
                    print("   P !!", t)
    prec = tp / (tp + fp) if tp + fp else 1.0
    rec = tp / (tp + fn) if tp + fn else 1.0
    prec2 = tp2 / (tp2 + fp2) if tp2 + fp2 else 1.0
    rec2 = tp2 / (tp2 + fn2) if tp2 + fn2 else 1.0
    f1 = lambda p, r: 2 * p * r / (p + r) if p + r else 0.0
    return {
        "cases": n,
        "status_accuracy": status_ok / n,
        "exact_case_match": exact / n,
        "step_precision": prec, "step_recall": rec, "step_f1": f1(prec, rec),
        "step_f1_ignoring_evidence": f1(prec2, rec2),
        "field_accuracy": {f: field_hits[f] / field_total for f in FIELDS} if field_total else {},
        "sequence_lcs": lcs_sum / n,
        "sequence_exact": seq_exact / n,
        "resource_recall": res_hit / res_tot if res_tot else 1.0,
        "confusion": {f"{g}->{p}": c for (g, p), c in sorted(confusion.items())},
        "failures": failures,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pred")
    ap.add_argument("--gold", default=os.path.join(ROOT, "dataset", "sample_output.csv"))
    ap.add_argument("--details", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if not a.pred:
        a.pred = os.path.join(ROOT, "sample_predictions.csv")
        subprocess.run([sys.executable, os.path.join(ROOT, "code", "main.py"), "--samples", "--no-llm"], check=True)
    res = score(load(a.pred), load(a.gold), a.details)
    if a.json:
        print(json.dumps(res, indent=2))
    else:
        for k, v in res.items():
            if isinstance(v, float):
                print(f"{k:28s} {v:.3f}")
            else:
                print(f"{k:28s} {v}")


if __name__ == "__main__":
    main()
