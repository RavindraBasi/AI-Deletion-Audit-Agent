"""Token and cost accounting for every live model call; renders usage_report.md."""
from collections import defaultdict
from datetime import datetime, timezone

from ..config import INTENT_MODEL, PRICES, VISION_MODEL

PURPOSES = [(INTENT_MODEL, "request intent"), (VISION_MODEL, "screenshot extraction")]


class UsageLedger:
    def __init__(self):
        self.calls = defaultdict(lambda: {"calls": 0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0})
        self.cache_hits = defaultdict(int)

    def record(self, purpose, model, usage):
        row = self.calls[(model, purpose)]
        row["calls"] += 1
        row["input"] += usage.input_tokens
        row["output"] += usage.output_tokens
        row["cache_read"] += getattr(usage, "cache_read_input_tokens", 0) or 0
        row["cache_write"] += getattr(usage, "cache_creation_input_tokens", 0) or 0

    def cache_hit(self, purpose):
        self.cache_hits[purpose] += 1

    @staticmethod
    def cost(model, row):
        price_in, price_out = PRICES[model]
        return ((row["input"] + 1.25 * row["cache_write"] + 0.1 * row["cache_read"]) * price_in
                + row["output"] * price_out) / 1e6

    def render(self, requests, mode):
        """mode: one paragraph saying how the run was made (live, cached or offline)."""
        lines = ["# Token Usage and Cost Report", "",
                 f"Run finished: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} — "
                 f"{requests} requests in `dataset/deletion_requests.csv` (plus 32 samples audited in the same run).", "",
                 "Provider: **Anthropic** (Claude API). Prices: Claude Haiku 5.5 $0.10 / $0.50 per MTok in / out; "
                 "Claude Sonnet 5.5 $2.00 / $10.00 per MTok in / out.", "", mode, "",
                 "| Model | Purpose | Calls | Input tokens | Output tokens | Total tokens | Est. cost (USD) |",
                 "|---|---|---:|---:|---:|---:|---:|"]
        per_model = defaultdict(lambda: {"calls": 0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0})
        for key in PURPOSES:   # every configured model is listed, even with zero live calls
            self.calls[key]
        for (model, purpose), row in sorted(self.calls.items()):
            for k in row:
                per_model[model][k] += row[k]
            lines.append(f"| `{model}` | {purpose} | {row['calls']} | {row['input']:,} | {row['output']:,} | "
                         f"{row['input'] + row['output']:,} | {self.cost(model, row):.4f} |")
        total = {"calls": 0, "input": 0, "output": 0, "cost": 0.0}
        lines += ["", "**Per-model totals**", "",
                  "| Model | Calls | Input tokens | Output tokens | Total tokens | Est. cost (USD) |",
                  "|---|---:|---:|---:|---:|---:|"]
        for model, row in sorted(per_model.items()):
            c = self.cost(model, row)
            lines.append(f"| `{model}` | {row['calls']} | {row['input']:,} | {row['output']:,} | "
                         f"{row['input'] + row['output']:,} | {c:.4f} |")
            total["calls"] += row["calls"]
            total["input"] += row["input"]
            total["output"] += row["output"]
            total["cost"] += c
        tokens = total["input"] + total["output"]
        lines += ["", "**Overall**", "",
                  "| Metric | Value |", "|---|---:|",
                  f"| Model calls | {total['calls']} |",
                  f"| Input tokens | {total['input']:,} |",
                  f"| Output tokens | {total['output']:,} |",
                  f"| Total tokens | {tokens:,} |",
                  f"| Average tokens per request | {tokens / requests:,.1f} |",
                  f"| Estimated total cost | ${total['cost']:.4f} |",
                  f"| Estimated cost per request | ${total['cost'] / requests:.6f} |", ""]
        if self.cache_hits:
            lines += ["Cache hits (answers reused without a model call): "
                      + ", ".join(f"{k}: {v}" for k, v in sorted(self.cache_hits.items())), ""]
        return "\n".join(lines)
