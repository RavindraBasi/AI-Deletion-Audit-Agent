# Usage Report — Ghost Data Still Exists

**Final full-dataset run:** 2026-10-24

## Model and provider usage

| Item | Value |
|---|---:|
| Model provider | None — no external model provider used |
| Model name | None — deterministic Python rules engine |
| Model calls | 0 |
| Input tokens | 0 |
| Output tokens | 0 |
| Total tokens | 0 |
| Average tokens per request (518 requests) | 0 |
| Estimated model cost, total | **$0.00** |
| Estimated model cost per request | **$0.00** |

## Run details

- Requests processed: 518
- Evidence files listed in the manifest: 94
- Policy rules loaded: 15
- Resource observations extracted: 2,929
- Output: repository-root `output.csv`
- Output-contract validation errors: 0
Predicted statuses: 368 `INCOMPLETE`, 141 `COMPLETE`, 9 `NOT_APPLICABLE`
Deletion-authorized cases with no safe resource-level evidence: 12 (conservatively `INCOMPLETE`, no speculative steps)
Public 32-case sample: 90.62% status accuracy; 25.00% exact full-row accuracy; 71.59% exact-step F1

The pipeline runs locally and reads the participant-provided CSV, JSON, SQL, log, text, PDF, and image evidence. When installed, `pdftotext` and `tesseract` are used as local extraction utilities. They are not remote LLM/API calls, and no content was sent to a model provider.

The reported $0.00 is model/API cost, not an estimate of electricity, CPU time, or general computer usage. No per-model breakdown is applicable because no model was called.
