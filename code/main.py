#!/usr/bin/env python3
"""Ghost Data Still Exists - deletion audit agent entry point.

Usage (from the repository root):
    python3 code/main.py                       # audit dataset/deletion_requests.csv -> ./output.csv
    python3 code/main.py --samples             # audit the 32 sample requests -> ./sample_predictions.csv
    python3 code/main.py --trace out.jsonl     # also write per-case reasoning traces

Optional LLM assistance (intent fallback + screenshot reading) is enabled when
ANTHROPIC_API_KEY is set; otherwise the agent runs fully deterministically.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from ghostaudit.audit import Auditor  # noqa: E402
from ghostaudit.data import World, read_csv  # noqa: E402
from ghostaudit.evidence import extract_all  # noqa: E402
from ghostaudit import llm as llm_mod  # noqa: E402


def write_output(path, results):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
        w.writerow(["case_id", "deletion_status", "steps_json"])
        # numeric case order (case_33 < case_100), identical to deletion_requests.csv order
        for res in sorted(results, key=lambda r: (len(r["case_id"]), r["case_id"])):
            w.writerow([res["case_id"], res["status"],
                        json.dumps(res["steps"], ensure_ascii=False, separators=(",", ":"))])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default=os.path.join(ROOT, "dataset"))
    ap.add_argument("--output", default=None)
    ap.add_argument("--samples", action="store_true", help="audit sample_deletion_requests.csv instead")
    ap.add_argument("--trace", default=None, help="write per-case JSONL reasoning traces")
    ap.add_argument("--no-llm", action="store_true", help="disable optional LLM calls")
    args = ap.parse_args(argv)

    t0 = time.time()
    llm = None if args.no_llm else llm_mod.from_env(cache_dir=os.path.join(ROOT, ".cache", "llm"))
    world = World(args.dataset)
    obs, metas = extract_all(args.dataset, cache_dir=os.path.join(ROOT, ".cache", "ocr"),
                             vision=llm.read_image if llm else None)
    samples = read_csv(os.path.join(args.dataset, "sample_deletion_requests.csv"))
    evals = read_csv(os.path.join(args.dataset, "deletion_requests.csv"))
    auditor = Auditor(world, obs, metas, llm=llm)
    # Link over every known request: one workflow batch can serve several requests.
    auditor.link_all(samples + evals)
    target = samples if args.samples else evals
    results = [auditor.audit_case(r) for r in target]
    out = args.output or os.path.join(ROOT, "sample_predictions.csv" if args.samples else "output.csv")
    write_output(out, results)
    if args.trace:
        with open(args.trace, "w", encoding="utf-8") as fh:
            for res in results:
                fh.write(json.dumps({k: v for k, v in res.items() if k != "steps"}, default=str) + "\n")
    stats = {}
    for res in results:
        stats[res["status"]] = stats.get(res["status"], 0) + 1
    usage = llm.usage_summary() if llm else {"calls": 0}
    print(f"wrote {out}: {len(results)} rows {stats} | evidence obs={len(obs)} files={len(metas)} "
          f"| llm calls={usage.get('calls', 0)} | {time.time() - t0:.1f}s")
    if llm:
        llm.write_usage(os.path.join(ROOT, ".cache", "llm_usage.json"), len(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
