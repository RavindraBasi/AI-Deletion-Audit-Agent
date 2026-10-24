"""Ghost Data Still Exists — deletion-audit agent.

    python3 code/main.py                 # audit dataset/deletion_requests.csv → ./output.csv
    python3 code/main.py --offline       # no API calls (cached answers or keyword fallback)
    python3 code/main.py --refresh-llm   # ignore the LLM cache (used for the reported final run)
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from audit import dataset, output  # noqa: E402
from audit.agent import DeletionAuditor  # noqa: E402
from audit.config import OUTPUT_CSV, REPO_ROOT, USAGE_REPORT  # noqa: E402
from audit.llm.client import LLM  # noqa: E402


def load_dotenv(path=REPO_ROOT / ".env"):
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.strip().partition("=")
            if sep and not key.startswith("#"):
                os.environ.setdefault(key.strip(), value.strip().strip('"'))


def run_mode(llm):
    if llm.usage.calls:
        return ("**Mode: live.** Every call counted below was made against the Claude API in this run; "
                "answers are cached in `code/.llm_cache/`.")
    if llm.usage.cache_hits:
        return ("**Mode: cached.** No live calls were made; every answer came from `code/.llm_cache/`, "
                "written by an earlier live run.")
    return ("**Mode: offline (no `ANTHROPIC_API_KEY`).** No model was called. Request intent came from the "
            "built-in multilingual keyword classifier, and the 11 screenshots were not read: resources they "
            "evidence fall back to the latest participant-visible activity. A live run "
            "(`code/main.py --refresh-llm` with a key in `.env`) is budgeted at about 13 calls and roughly "
            "65k tokens (≈ $0.13): 2 batched Haiku 5.5 intent calls and 11 Sonnet 5.5 screenshot calls.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--refresh-llm", action="store_true")
    parser.add_argument("--out", type=Path, default=OUTPUT_CSV)
    args = parser.parse_args()
    load_dotenv()

    llm = LLM(offline=args.offline, refresh=args.refresh_llm)
    requests = dataset.load_requests()
    samples = dataset.load_requests("sample_deletion_requests.csv")
    # Samples are audited in the same run: deletion-worker events are shared across all requests.
    results = DeletionAuditor(llm).run(requests + samples)
    output.write(results, args.out, [r.case_id for r in requests])
    times = {e["file_id"]: e["captured_at"] for e in dataset.load_manifest()}
    times.update({a.activity_id: a.time for a in dataset.load_activities()})
    rows = output.validate(args.out, requests, times)
    USAGE_REPORT.write_text(llm.usage.render(len(requests), run_mode(llm)), encoding="utf-8")
    counts = {}
    for cid in (r.case_id for r in requests):
        counts[results[cid][0]] = counts.get(results[cid][0], 0) + 1
    print(f"wrote {rows} rows to {args.out}: {counts}")


if __name__ == "__main__":
    main()
