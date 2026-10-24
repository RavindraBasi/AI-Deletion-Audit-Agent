# Token Usage and Cost Report

## Final full-dataset run that produced `output.csv`

The command was `python3 code/main.py` over all 518 requests in `dataset/deletion_requests.csv`.
`ANTHROPIC_API_KEY` was not set, so the optional LLM layer was disabled and the run was fully deterministic.

| Provider | Model | Model calls | Input tokens | Output tokens | Total tokens | Est. cost (USD) |
| --- | --- | --- | --- | --- | --- | --- |
| Anthropic (optional, disabled) | claude-haiku-4-5 | 0 | 0 | 0 | 0 | 0.00 |
| Local OCR | tesseract 5 (no tokens) | 11 images | n/a | n/a | n/a | 0.00 |
| **Overall** | | **0** | **0** | **0** | **0** | **0.00** |

| Per-request figures | Value |
| --- | --- |
| Requests processed | 518 |
| Average tokens per request | 0 |
| Estimated cost per request | $0.00 |
| Wall-clock time (cold run, OCR not cached) | ≈14 s |
| Wall-clock time (OCR cached) | <1 s |

## Why the final run uses no model calls

Every decision in this challenge is grounded in structured evidence. That includes:

- identity resolution, including hashes of historical emails;
- state at `audit_at`;
- policy mapping and step ordering.

Deterministic code handles all of this more reliably and reproducibly than a model would.
The intent rules classify 545 of the 550 sample and evaluation texts confidently.
The other 5 (case_534, case_536, case_538, case_539 and case_542) mix deletion words with an explicit refusal of deletion.
For these the rules fall back to the safe default of non-deletion.

## Expected cost when the LLM layer is enabled

Set `ANTHROPIC_API_KEY`. The model defaults to `claude-haiku-4-5` and can be changed with `GHOST_LLM_MODEL`.

| Use | Calls per full run | ≈ input tok/call | ≈ output tok/call | ≈ tokens per run |
| --- | --- | --- | --- | --- |
| Intent fallback, only for texts the rules cannot classify confidently | 5 | 250 | 2 | 1.3 k |
| Screenshot transcription (11 images, cached after the first run) | 11 | 1,900 | 250 | 23.7 k |
| **Total** | **16** | | | **≈25 k** |

At $1 / $5 per million input and output tokens, that is about **$0.04 per full run**, or about **$0.00007 per request**.
All responses are cached on disk keyed by a hash of the prompt, so later runs cost $0.
When the layer is enabled, the agent writes the actual counts to `.cache/llm_usage.json`, using the same fields as this table.
