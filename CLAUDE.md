# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@AGENTS.md

The import above is the authoritative contract (session logging to `log.txt`, output format, evidence rules, submission URL). Everything below only adds what AGENTS.md does not say. In log entries use `tool=Claude Code` and take `model=` from the runtime model ID in the system prompt.

## Current state

`code/main.py` and `code/evaluation/main.py` are empty stubs: there is no solution, test suite, or lint config yet. `generator/` (AGENTS.md §7) is organizer-only and not in this checkout.

## Commands

```bash
python3 code/main.py   # must read dataset/ and write ./output.csv (518 rows + header)
zip -r code.zip README.md code -x "*/__pycache__/*" "*/.venv/*" "*/venv/*" "*/node_modules/*" "*/.git/*" "*/.DS_Store"
```

Local environment: Python 3.14 with stdlib `sqlite3` and Pillow; `pdftotext` on PATH. Not installed: `sqlite3` CLI, `tesseract`, `pypdf`/`fitz`, `anthropic` SDK.

## Contract details that are easy to miss

- **Eval report location conflict:** AGENTS.md and problem_statement.md say `evaluation/…`, but README (newer) says `code/evaluation/evaluation_report.md` and `code/evaluation/usage_report.md` so the zip command bundles them. Use `code/evaluation/`. The code judge ignores `.csv/.tsv/.json/.jsonl/.sql/.log`, so metrics, custom cases, and prompts must live in `.md` or source files.
- **Within-phase ordering:** phase, then operation in the order of the Allowed Operations table, then `system_id`, then `resource_id` (ascending).
- **VERIFICATION covers the whole required end state, not only remediated items.** Sample `case_11` (INCOMPLETE) also verifies an account that was already absent (`VERIFY_RECORD_ABSENT`) and a retained `security_service` audit record (`VERIFY_RECORD_PRESENT`).
- **Serialization** matching `sample_output.csv`: `json.dumps(steps, sort_keys=True, separators=(",", ":"))`, written through the `csv` module.
- **`case_id` sort is ambiguous:** eval IDs are unpadded `case_33`…`case_550` and the input is in numeric order; a lexicographic sort would put `case_100` before `case_33`. Unresolved; choose deliberately.
- Sample cases `case_01`–`case_32` do not overlap the eval range, so they are the local scoring set against `sample_output.csv`. Their cited evidence is always a `file_id` (`evidence_NN`), never an activity or audit ID, though the contract allows those.

## Data layout

- `evidence_files.csv` `file_path` is relative to `dataset/` (e.g. `evidence/logs/…`), not the repo root. Filter evidence by `captured_at <= audit_at`.
- `.sql` snapshots load with `sqlite3.connect(":memory:").executescript(text)`. Header comments carry `Captured at` and `Database topology`; replica files pair tables such as `customer_profiles_primary` with `customer_profiles_failover_replica` for drift comparison. `customer_data_inventory_*.sql` files hold one table per data category (`customer_profiles`, `customer_search_documents`, `customer_analytics_profiles`, `support_records`, `customer_exports`, …).
- The 11 PNGs have no local OCR; reading them needs a vision model or a new dependency.
- `deletion_rules.csv` is keyed by `data_category`; its `condition` column is free text that decides between DELETE, REDACT, ANONYMIZE, and RETAIN (e.g. `SUPPORT_MESSAGE` is REDACT only when shared context must be retained).
