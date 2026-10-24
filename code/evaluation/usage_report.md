# Token Usage and Cost Report

Run finished: 2026-10-24T15:53:05Z — 518 requests in `dataset/deletion_requests.csv` (plus 32 samples audited in the same run).

Provider: **Anthropic** (Claude API). Prices: Claude Haiku 5.5 $0.10 / $0.50 per MTok in / out; Claude Sonnet 5.5 $2.00 / $10.00 per MTok in / out.

**Mode: offline (no `ANTHROPIC_API_KEY`).** No model was called. Request intent came from the built-in multilingual keyword classifier, and the 11 screenshots were not read: resources they evidence fall back to the latest participant-visible activity. A live run (`code/main.py --refresh-llm` with a key in `.env`) is budgeted at about 13 calls and roughly 65k tokens (≈ $0.13): 2 batched Haiku 5.5 intent calls and 11 Sonnet 5.5 screenshot calls.

| Model | Purpose | Calls | Input tokens | Output tokens | Total tokens | Est. cost (USD) |
|---|---|---:|---:|---:|---:|---:|
| `claude-haiku-5-5` | request intent | 0 | 0 | 0 | 0 | 0.0000 |
| `claude-sonnet-5-5` | screenshot extraction | 0 | 0 | 0 | 0 | 0.0000 |

**Per-model totals**

| Model | Calls | Input tokens | Output tokens | Total tokens | Est. cost (USD) |
|---|---:|---:|---:|---:|---:|
| `claude-haiku-5-5` | 0 | 0 | 0 | 0 | 0.0000 |
| `claude-sonnet-5-5` | 0 | 0 | 0 | 0 | 0.0000 |

**Overall**

| Metric | Value |
|---|---:|
| Model calls | 0 |
| Input tokens | 0 |
| Output tokens | 0 |
| Total tokens | 0 |
| Average tokens per request | 0.0 |
| Estimated total cost | $0.0000 |
| Estimated cost per request | $0.000000 |
