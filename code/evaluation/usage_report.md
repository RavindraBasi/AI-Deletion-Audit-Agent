# LLM usage report

Final full-dataset run: `python3 code/main.py` (backend `replay`) over the 518 requests in `dataset/deletion_requests.csv`, producing `output.csv`. That run is served entirely from the committed LLM cache, so its marginal cost is $0; the table attributes every cached result it used to the live call that produced it (Claude subscription through `claude -p`, cost = the CLI's API-equivalent `total_cost_usd`).

- Requests processed: **518**
- Cached LLM results used by the final run: **34** (live calls during the final run: 0)
- Replay output byte-identical to the submitted `output.csv`: **True**
- Spending cap (`GDA_BUDGET_USD`): $3.00; live spend recorded in this checkout's development ledgers (`code/cache/ledger/`, all trial runs included): $1.44

| Provider | Model | Tasks | Calls | Input tokens | Output tokens | Total tokens | Cost (USD) |
|---|---|---|---|---|---|---|---|
| anthropic (claude_cli) | `claude-haiku-5-5` | intent×15 | 15 | 373,261 | 29,593 | 402,854 | $0.0594 |
| anthropic (claude_cli) | `claude-sonnet-5-5` | extract_files×4, extract_visual×15 | 19 | 858,510 | 6,968 | 865,478 | $1.3103 |
| **all** | | | **34** | **1,231,771** | **36,561** | **1,268,332** | **$1.3697** |

Per request (518 requests):

- Average tokens per request: **2,448.5** (2,377.9 in / 70.6 out)
- Estimated cost per request: **$0.00264**; estimated total: **$1.3697**

Notes:

- Intent: unique request texts are classified in batches of 25 by the `fast` alias (Claude Haiku 5.5).
- Visual evidence: each of the 15 PNG/PDF files is read once by the `vision` alias (Claude Sonnet 5.5); screenshots that print no resource id get a second, file-listing pass.
- Input token counts include prompt-cache reads/writes reported by the CLI (the CLI adds its own system prompt), which is why visual calls show large input counts at a small cost.
- Everything else (parsing, linking, state reconstruction, policy, planning) is deterministic code: 0 tokens.
- Re-running with an empty cache costs about the total above; `GDA_BUDGET_USD` caps it (default $3.00).
