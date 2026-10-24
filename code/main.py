#!/usr/bin/env python3
"""Ghost Data Still Exists — deletion-audit agent.

Usage:
    python3 code/main.py                       # 518 evaluation requests -> ./output.csv
    python3 code/main.py --cases sample        # 32 public samples -> ./sample_predictions.csv
    python3 code/main.py --dataset-dir DIR --requests FILE --out PATH --trace-dir DIR

LLM backend: GDA_LLM_BACKEND=replay (default, committed cache) | claude_cli | anthropic | openai | offline.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ghostaudit.config import DEFAULT_DATASET_DIR, REPO_ROOT, DatasetPaths  # noqa: E402
from ghostaudit.llm.budget import BudgetExceeded  # noqa: E402
from ghostaudit.pipeline import run  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset-dir", type=Path, default=DEFAULT_DATASET_DIR)
    ap.add_argument("--cases", choices=["eval", "sample"], default="eval")
    ap.add_argument("--requests", type=Path, help="override the request CSV")
    ap.add_argument("--out", type=Path, help="output CSV path")
    ap.add_argument("--trace-dir", type=Path, help="write one <case_id>.json decision trace per case")
    ap.add_argument("--backend", help="override GDA_LLM_BACKEND")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    root = args.dataset_dir.resolve()
    default_req = root / ("sample_deletion_requests.csv" if args.cases == "sample" else "deletion_requests.csv")
    requests = (args.requests or default_req).resolve()
    out = args.out or (REPO_ROOT / ("sample_predictions.csv" if args.cases == "sample" else "output.csv"))
    try:
        res = run(DatasetPaths(root, requests), out, args.trace_dir, backend=args.backend)
    except BudgetExceeded as exc:
        print(f"error: {exc}", file=sys.stderr)
        return BudgetExceeded.exit_code
    counts: dict[str, int] = {}
    for r in res.results:
        counts[r.deletion_status] = counts.get(r.deletion_status, 0) + 1
    print(f"wrote {len(res.results)} rows to {out}: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    if res.llm is not None:
        print(res.llm.spend_line())
    if res.errors:
        print(f"{len(res.errors)} output validation error(s):", file=sys.stderr)
        for e in res.errors[:20]:
            print("  " + e, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
